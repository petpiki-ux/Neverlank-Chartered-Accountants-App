"""Balance sheet and trial balance reconciliation helper (pure Python, no Flask / DB).

When figures loaded from financial statements, a trial balance or captured by hand do not balance, this module works out
WHY, as far as the numbers allow, and offers fixes the preparer can accept with one click. It never forces a balance and
never posts a plug: every suggestion is a specific, explained change to a figure the preparer can see and reverse, and the
caller records each accepted change in a log.

Balance sheet identity: assets - liabilities - equity = 0. The "gap" is that expression: a positive gap means assets are
bigger than liabilities plus equity.

The patterns looked for, strongest first:
  * the totals printed in the statements disagree with the lines found - the missing or surplus amount localises the problem
    to one side (assets, liabilities or equity) and often to one line;
  * current-year profit has not been added to equity (the gap equals the profit), or has been added twice;
  * one line equals half the gap, so it sits on the wrong side or has the wrong sign;
  * one line equals the gap, so it was probably counted twice (a subtotal read as a line);
  * one line is a digit transposition of what it needs to be (a gap divisible by 9), or out by a power of ten;
  * a catch-all line (other assets, other liabilities, reserves) is short by exactly the gap;
  * for a trial balance: the file itself does not balance (rows that are one-sided, flipped or transposed), or accounts have
    been set to Ignore - with a balanced trial balance the balance sheet is out by exactly minus the ignored accounts.
"""
import hashlib
import json

SIDE_WORD = {"A": "an asset", "L": "a liability", "E": "an equity item"}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _sign(side):
    return 1.0 if side == "A" else -1.0


def gap_of(lines, override=None):
    """assets - liabilities - equity for a list of line dicts ({key, side, value}); `override` is {key: value}."""
    g = 0.0
    for ln in lines:
        v = _f(override[ln["key"]]) if override and ln["key"] in override else _f(ln.get("value"))
        g += _sign(ln["side"]) * v
    return g


def _digits(v):
    return f"{abs(v):.2f}".replace(".", "")


def _adjacent_swap(a, b):
    sa, sb = _digits(a), _digits(b)
    if sa == sb or len(sa) != len(sb):
        return False
    diff = [i for i, (x, y) in enumerate(zip(sa, sb)) if x != y]
    return len(diff) == 2 and diff[1] == diff[0] + 1 and sa[diff[0]] == sb[diff[1]] and sa[diff[1]] == sb[diff[0]]


def _magnitude_slip(old, new):
    if abs(old) < 1e-9 or abs(new) < 1e-9 or (old > 0) != (new > 0):
        return None
    r = new / old
    for p in (10.0, 100.0, 1000.0):
        if abs(r - p) / p < 1e-6:
            return f"x{p:g}"
        if abs(r - 1.0 / p) * p < 1e-6:
            return f"/{p:g}"
    return None


def _sid(kind, changes):
    raw = json.dumps([kind, sorted((k, round(float(v), 2) if isinstance(v, (int, float)) else v) for k, v in changes.items())], sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()[:10]


def _money(v):
    return f"{abs(v):,.2f}" if abs(v - round(v)) > 0.004 else f"{abs(v):,.0f}"


def diagnose(lines, pat=None, totals=None, tol=1.0, pairs=None, dest=None, retained_key="retained"):
    """Explain why a balance sheet does not balance and propose fixes.

    lines   [{key, label, side 'A'|'L'|'E', value (None when blank), signed (may be negative), catchall}]
    pat     current-year profit after tax (to spot profit not rolled into equity); None to skip that test
    totals  {'assets','liabilities','equity'} as printed in the source document (any may be missing)
    pairs   {key: key} lines that are two sides of one thing, e.g. cash <-> overdraft
    dest    {'A': key, 'L': key, 'E': key} where a line moved to another side should land
    Returns {gap, balanced, headline, notes[], suggestions[]}; each suggestion is
    {id, kind, confidence high|medium|low, title, detail, changes[{key,label,old,new}], after_gap, advisory}."""
    pairs = pairs or {}
    dest = dest or {}
    by_key = {ln["key"]: ln for ln in lines}
    vals = {ln["key"]: _f(ln.get("value")) for ln in lines}
    gap = gap_of(lines)
    assets = sum(vals[k] for k, ln in by_key.items() if ln["side"] == "A")
    out = {"gap": gap, "balanced": abs(gap) <= tol, "headline": "", "notes": [], "suggestions": []}
    if out["balanced"]:
        out["headline"] = "The balance sheet balances."
        return out
    word = "Assets are more than liabilities plus equity by" if gap > 0 else "Liabilities plus equity are more than assets by"
    out["headline"] = f"{word} {_money(gap)}."
    if abs(assets) > 0 and abs(gap) <= 0.001 * abs(assets):
        out["notes"].append(f"The difference is only {abs(gap) / abs(assets) * 100:.3f}% of total assets, which usually means rounding in the source document. "
                            "Check it against the printed statements rather than chasing it.")
    cands = []

    def add(kind, conf, title, detail, changes, advisory=False, localised=False):
        cands.append({"kind": kind, "confidence": conf, "title": title, "detail": detail, "changes": dict(changes), "advisory": advisory,
                      "localised": localised})

    def lab(k):
        return by_key[k]["label"] if k in by_key else k

    # ---- 1. the statements' own totals point at one side
    t = totals or {}
    side_sum = {s: sum(vals[k] for k, ln in by_key.items() if ln["side"] == s) for s in ("A", "L", "E")}
    for s, tk, name in (("A", "assets", "assets"), ("L", "liabilities", "liabilities"), ("E", "equity", "equity")):
        if t.get(tk) is None:
            continue
        diff = _f(t[tk]) - side_sum[s]
        if abs(diff) <= tol:
            continue
        if diff > 0:
            caps = [k for k, ln in by_key.items() if ln["side"] == s and ln.get("catchall")]
            for k in caps:
                add("short_side", "high", f"Add {_money(diff)} to {lab(k)}",
                    f"The statements print total {name} of {_money(_f(t[tk]))}, but the {name} lines found add up to {_money(side_sum[s])}: "
                    f"{_money(diff)} is missing, probably a line that was not read or sits in {lab(k)}.", {k: vals[k] + diff}, localised=True)
        else:
            surplus = abs(diff)
            hit = [k for k, ln in by_key.items() if ln["side"] == s and abs(abs(vals[k]) - surplus) <= tol and vals[k] != 0]
            for k in hit:
                add("surplus_line", "high", f"Remove {lab(k)} ({_money(vals[k])}): it looks like a subtotal or a duplicate",
                    f"The {name} lines found add up to {_money(side_sum[s])}, which is {_money(surplus)} more than the printed total of {_money(_f(t[tk]))}; "
                    f"{lab(k)} is exactly that amount, so it was probably counted twice (for example a subtotal read as a line).", {k: 0.0}, localised=True)
            if not hit:
                out["notes"].append(f"The {name} lines found add up to {_money(side_sum[s])}, {_money(surplus)} more than the printed total of {_money(_f(t[tk]))}: "
                                    "a line was probably read twice or from the wrong column.")
    if t.get("assets") is not None and (t.get("liabilities") is not None or t.get("equity") is not None):
        other = (_f(t.get("liabilities")) if t.get("liabilities") is not None else side_sum["L"]) + (_f(t.get("equity")) if t.get("equity") is not None else side_sum["E"])
        if abs(_f(t["assets"]) - other) > max(tol, 1.0):
            out["notes"].append(f"The printed totals do not agree with each other either: total assets {_money(_f(t['assets']))} against liabilities plus equity "
                                f"{_money(other)}. Check that the totals were read from the same year's column.")

    # ---- 2. profit not rolled into equity
    rk = retained_key if retained_key in by_key else next((k for k, ln in by_key.items() if ln["side"] == "E" and ln.get("signed")), None)
    if pat is not None and rk and abs(pat) > tol:
        if abs(gap - pat) <= tol:
            add("profit_to_equity", "high", f"Add the year's profit ({_money(pat)}) to {lab(rk)}",
                f"The gap equals the profit for the year. Retained earnings here look like the balance brought forward: the current-year result has not been added to equity.",
                {rk: vals[rk] + pat})
        elif abs(gap + pat) <= tol:
            add("profit_twice", "high", f"Take the year's profit ({_money(pat)}) out of {lab(rk)}",
                "The gap equals minus the profit for the year, so the result looks to have been added to retained earnings twice (once in the reserves, once again as profit).",
                {rk: vals[rk] - pat})

    # ---- 3. line-level patterns
    for k, ln in by_key.items():
        v = vals[k]
        if abs(v) <= tol:
            continue
        s = _sign(ln["side"])
        # wrong sign / wrong side
        if abs(gap - 2 * s * v) <= tol:
            if v < 0 or ln.get("signed"):
                add("flip_sign", "high", f"Change the sign of {lab(k)} ({_money(v)} to {_money(-v)})",
                    f"Flipping the sign of {lab(k)} changes the balance sheet by exactly twice that line, which is the gap.", {k: -v})
            elif k in pairs and pairs[k] in by_key:
                pk = pairs[k]
                add("move_pair", "high", f"Move {_money(v)} from {lab(k)} to {lab(pk)}",
                    f"{lab(k)} is counted as {SIDE_WORD[ln['side']]} but {lab(pk)} is on the other side. Twice its amount equals the gap, so it is probably in the wrong place.",
                    {k: 0.0, pk: vals[pk] + v})
            else:
                # an asset moved to the other side lowers the gap by twice its amount, the reverse raises it: only the closing direction is offered
                dests = [dest.get("L"), dest.get("E")] if ln["side"] == "A" else [dest.get("A")]
                for dk in dests:
                    if dk in by_key and dk != k and by_key[dk]["side"] != ln["side"]:
                        add("move_side", "medium", f"Move {lab(k)} ({_money(v)}) to {lab(dk)}",
                            f"Twice the amount of {lab(k)} equals the gap. It is counted as {SIDE_WORD[ln['side']]}; if it belongs with {lab(dk)} the sheet balances.",
                            {k: 0.0, dk: vals[dk] + v})
        # counted twice / dropped
        if (ln["side"] == "A" and abs(gap - v) <= tol) or (ln["side"] in ("L", "E") and abs(gap + v) <= tol):
            add("duplicate_line", "medium", f"{lab(k)} ({_money(v)}) equals the gap: remove it if it is a duplicate",
                f"{lab(k)} is exactly the size of the gap. If it duplicates another line or is a subtotal that was read as a line, taking it out balances the sheet.",
                {k: 0.0})
        # transposed digits / power of ten
        need = v - gap * s
        if abs(need) > tol:
            if (v > 0) == (need > 0) and _adjacent_swap(v, need):
                add("transposition", "medium", f"{lab(k)}: {_money(v)} may have been typed as {_money(need)} (digits swapped)",
                    "A gap that is a multiple of nine is the signature of two digits swapped. This line needs to be exactly that value to balance, and it differs from the current one only by a swap.",
                    {k: need})
            else:
                mag = _magnitude_slip(v, need)
                if mag:
                    add("magnitude", "medium", f"{lab(k)}: {_money(v)} may be out by a power of ten ({_money(need)})",
                        "A decimal point or thousands scale may have slipped on this line: the value that balances the sheet is exactly ten, a hundred or a thousand times (or a tenth, etc.) of what is there.",
                        {k: need})

    # ---- 4. catch-all lines short by the gap, and blank lines on the needed side
    need_side = ("L", "E") if gap > 0 else ("A",)
    amount = abs(gap)
    for k, ln in by_key.items():
        if ln["side"] not in need_side:
            continue
        if ln.get("catchall") or ln.get("blank"):
            new = vals[k] + amount
            word_ = "Add" if not ln.get("blank") else "Enter"
            add("fill_line", "low", f"{word_} {_money(amount)} {'to' if word_ == 'Add' else 'on'} {lab(k)}",
                f"{'Liabilities or equity' if gap > 0 else 'Assets'} are short by exactly this amount. If a {SIDE_WORD[ln['side']].split(' ', 1)[1]} was left out of the source or read as nil, {lab(k)} is where it would go. "
                "Only accept this once you have found the missing item in the document.", {k: new})

    # ---- simulate, filter, rank
    seen, res = set(), []
    rank = {"high": 0, "medium": 1, "low": 2}
    for c in cands:
        sid = _sid(c["kind"], c["changes"])
        if sid in seen:
            continue
        seen.add(sid)
        after = gap_of(lines, c["changes"])
        if abs(after) > tol and not (c["localised"] and abs(after) < abs(gap) - tol):
            continue
        res.append({"id": sid, "kind": c["kind"], "confidence": c["confidence"], "title": c["title"], "detail": c["detail"], "advisory": c["advisory"],
                    "after_gap": after, "cleared": abs(after) <= tol,
                    "changes": [{"key": k, "label": lab(k), "old": vals.get(k, 0.0), "new": nv} for k, nv in c["changes"].items()]})
    res.sort(key=lambda r: (not r["cleared"], rank[r["confidence"]], len(r["changes"]), sum(abs(x["new"] - x["old"]) for x in r["changes"])))
    if any(r["cleared"] and r["confidence"] in ("high", "medium") for r in res):
        res = [r for r in res if r["confidence"] != "low"]   # "put the gap on a catch-all line" is only worth offering when nothing better explains it
    out["suggestions"] = res[:6]
    if not res:
        out["notes"].append("No single correction explains the difference. Compare each line with the source document; the difference to find is "
                            f"{_money(gap)} ({'assets higher' if gap > 0 else 'liabilities and equity higher'}).")
    return out


# --------------------------------------------------------------------------- adapters

STD_SIDES = {"ppe": "A", "intangibles": "A", "inventory": "A", "receivables": "A", "other_ca": "A", "cash": "A",
             "overdraft": "L", "payables": "L", "other_cl": "L", "borrowings": "L", "share_capital": "E", "retained": "E"}
STD_CATCH = {"intangibles", "other_ca", "other_cl", "retained"}


def statement_lines(bs, labels):
    """Lines of an extracted statement period (cf_fsload.STD_BS keys). `labels` is {key: label}."""
    out = []
    for k, side in STD_SIDES.items():
        v = bs.get(k)
        out.append({"key": k, "label": labels.get(k, k), "side": side, "value": v, "signed": k == "retained", "catchall": k in STD_CATCH,
                    "blank": v in (None, "")})
    return out


def statement_totals(bs):
    return {"assets": bs.get("total_assets"), "liabilities": bs.get("total_liabilities"), "equity": bs.get("total_equity")}


def statement_pat(period):
    i = period.get("is") or {}
    if i.get("pat") is not None:
        return _f(i["pat"])
    if all(k in i for k in ("revenue", "cogs")):
        return (_f(i.get("revenue")) - _f(i.get("cogs")) + _f(i.get("other_income")) - _f(i.get("opex")) - _f(i.get("dep")) - _f(i.get("finance_costs"))
                + _f(i.get("interest_income")) - _f(i.get("tax")))
    return None


STD_DIAG = {"pairs": {"cash": "overdraft", "overdraft": "cash"}, "dest": {"A": "other_ca", "L": "other_cl", "E": "retained"}}


def diagnose_statement_period(period, labels):
    bs = period.get("bs") or {}
    lines = statement_lines(bs, labels)
    ta = _f(bs.get("total_assets")) if bs.get("total_assets") is not None else sum(_f(bs.get(k)) for k in ("ppe", "intangibles", "inventory", "receivables", "other_ca", "cash"))
    tol = max(1.0, 0.0001 * abs(ta))
    return diagnose(lines, pat=statement_pat(period), totals=statement_totals(bs), tol=tol, **STD_DIAG)


def pack_lines(bs, labels, sides):
    """Lines of a management-accounts balance sheet (cf_ma.BS_LINES): sides is {key: 'A'|'L'|'E'}."""
    out = []
    for k, side in sides.items():
        v = (bs or {}).get(k)
        out.append({"key": k, "label": labels.get(k, k), "side": side, "value": v, "signed": k == "retained",
                    "catchall": k in ("other_assets", "other_liabilities", "retained"), "blank": v in (None, "")})
    return out


PACK_DIAG = {"pairs": {"cash": "overdraft", "overdraft": "cash"}, "dest": {"A": "other_assets", "L": "other_liabilities", "E": "retained"}}


def diagnose_pack_bs(bs, labels, sides, pat=None, tol=0.5):
    return diagnose(pack_lines(bs, labels, sides), pat=pat, tol=tol, **PACK_DIAG)


# --------------------------------------------------------------------------- trial balances

def diagnose_tb(rows, chosen, tol=0.5, fallback_target=None):
    """Explain a trial balance that does not balance, or a balance sheet out because accounts were ignored.

    rows    [{code, name, net_dr}]       chosen  {str(row index): target or 'ignore'}
    Returns {total, ignored_total, gap, headline, notes[], suggestions[]}. A suggestion carries `row_changes` {index: new net_dr}
    and/or `map_changes` {index: target}; `advisory` suggestions only point at rows to look at."""
    total = sum(_f(r["net_dr"]) for r in rows)
    ignored = [(i, r) for i, r in enumerate(rows) if chosen.get(str(i)) == "ignore" and abs(_f(r["net_dr"])) > tol]
    ign_total = sum(_f(r["net_dr"]) for _, r in ignored)
    out = {"total": total, "ignored_total": ign_total, "gap": 0.0, "headline": "", "notes": [], "suggestions": []}
    sugg = []

    def nm(i):
        r = rows[i]
        return f"{r.get('code') + ' ' if r.get('code') else ''}{r['name']}"

    if abs(total) > tol:
        out["gap"] = total
        out["headline"] = (f"The trial balance itself does not balance: debits exceed credits by {_money(total)}." if total > 0
                           else f"The trial balance itself does not balance: credits exceed debits by {_money(total)}.")
        for i, r in enumerate(rows):
            v = _f(r["net_dr"])
            if abs(v) <= tol:
                continue
            if abs(2 * v - total) <= tol:
                sugg.append({"kind": "flip_row", "confidence": "high", "title": f"Reverse the sign of {nm(i)} ({_money(v)} {'debit' if v > 0 else 'credit'})",
                             "detail": "Twice this balance equals the difference, so it was probably posted to the wrong side (a credit captured as a debit or the other way round).",
                             "row_changes": {i: -v}, "after": total - 2 * v})
            elif abs(v - total) <= tol:
                sugg.append({"kind": "row_equals_gap", "confidence": "medium", "advisory": True, "title": f"{nm(i)} equals the difference ({_money(v)})",
                             "detail": "Either its offsetting entry is missing from the file, or the account is a duplicate. Compare it with the ledger.", "after": None})
            need = v - total
            if abs(need) > tol:
                if _adjacent_swap(v, need) and (v > 0) == (need > 0):
                    sugg.append({"kind": "transposition", "confidence": "medium", "title": f"{nm(i)}: {_money(v)} may have been typed as {_money(need)}",
                                 "detail": "The difference is a multiple of nine and this balance differs from the value that would balance the file only by two swapped digits.",
                                 "row_changes": {i: need}, "after": 0.0})
                else:
                    mag = _magnitude_slip(v, need)
                    if mag:
                        sugg.append({"kind": "magnitude", "confidence": "medium", "title": f"{nm(i)}: {_money(v)} may be out by a power of ten ({_money(need)})",
                                     "detail": "The value that balances the file is exactly ten, a hundred or a thousand times (or a fraction) of this one: a decimal slip.",
                                     "row_changes": {i: need}, "after": 0.0})
        names = {}
        for i, r in enumerate(rows):
            names.setdefault((r["name"].strip().lower(), round(_f(r["net_dr"]), 2)), []).append(i)
        for (n, v), idx in names.items():
            if len(idx) > 1 and abs(v) > tol:
                sugg.append({"kind": "duplicate_row", "confidence": "low", "advisory": True,
                             "title": f"'{rows[idx[0]]['name']}' appears {len(idx)} times with the same balance ({_money(v)})",
                             "detail": "A repeated account is a common cause of an unbalanced file. Remove the duplicate if it is one.", "after": None})
        if not sugg:
            out["notes"].append("No single row explains the difference. Check the file's totals against the ledger, and that no accounts were left off.")
    elif abs(ign_total) > tol:
        out["gap"] = -ign_total
        out["headline"] = (f"The trial balance balances, but the accounts set to Ignore total {_money(ign_total)} {'debit' if ign_total > 0 else 'credit'}, "
                           f"so the balance sheet built from the rest is out by {_money(ign_total)}.")
        for i, r in ignored:
            v = _f(r["net_dr"])
            tgt = (fallback_target(i) if fallback_target else None) or ("other_assets" if v > 0 else "other_liabilities")
            sugg.append({"kind": "unignore", "confidence": "high" if abs(abs(v) - abs(ign_total)) <= tol else "medium",
                         "title": f"Stop ignoring {nm(i)} ({_money(v)} {'debit' if v > 0 else 'credit'}): map it to {tgt.replace('_', ' ')}",
                         "detail": "Ignoring an account removes its balance from the statements, and the trial balance only balances with every account included."
                                   + (" This account alone accounts for the whole difference." if abs(abs(v) - abs(ign_total)) <= tol else ""),
                         "map_changes": {i: tgt}, "after": ign_total - v})
        if len(ignored) > 1:
            sugg.append({"kind": "unignore_all", "confidence": "medium", "title": f"Stop ignoring all {len(ignored)} ignored accounts",
                         "detail": "Together they account for the whole difference. Each is mapped to the nearest catch-all line; change any to a better line afterwards.",
                         "map_changes": {i: ((fallback_target(i) if fallback_target else None) or ("other_assets" if _f(r['net_dr']) > 0 else "other_liabilities")) for i, r in ignored},
                         "after": 0.0})
    else:
        out["headline"] = "The trial balance balances and no accounts are ignored."
    for s in sugg:
        s["id"] = _sid(s["kind"], {**{f"r{k}": v for k, v in (s.get("row_changes") or {}).items()}, **{f"m{k}": v for k, v in (s.get("map_changes") or {}).items()}} or {"t": s["title"]})
        s.setdefault("advisory", False)
        s["cleared"] = s.get("after") is not None and abs(s["after"]) <= tol
    rank = {"high": 0, "medium": 1, "low": 2}
    sugg.sort(key=lambda s: (s["advisory"], not s["cleared"], rank[s["confidence"]]))
    out["suggestions"] = sugg[:7]
    return out
