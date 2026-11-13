"""Loan facilities for a Management Accounts pack (pure Python, no Flask / DB).

A client's pack can list the bank facilities it carries (lender, amount, interest rate, establishment fee, term, how it is repaid).
From that list this module produces

* the month-by-month schedule of every facility (opening balance, drawdown, interest, establishment fee, capital repaid, closing balance),
  split into the months that are ACTUAL (the pack already holds them) and the months still to come (the FORECAST);
* the loans the forecast model must carry, so that interest, fees, repayments, cash and borrowings in the forecast follow the facilities
  instead of one generic "existing borrowings" line;
* a few checks against the actual books (does the schedule's interest and borrowings agree with the trial balance?).

Conventions (the same as the forecast engine, so the two always agree):
* a facility is drawn at the START of its drawdown month and bears interest from that month;
* interest for a month = balance (after any drawdown) x rate / 12;
* the establishment fee is a cost of the month in which the facility is drawn (expensed, not spread), = amount x fee % + any fixed fee;
* capital instalments are paid at the END of the month. The first one falls in the drawdown month plus the capital grace months;
  the last one in the month before the term ends (term = months from drawdown to the final repayment).
"""
import calendar
import datetime
import hashlib
import json

import cf_ma as M

f = M.f

REPAY_TYPES = [
    ("level", "Equal monthly instalments (capital + interest the same every month)"),
    ("straight", "Equal capital instalments (interest falls as the balance falls)"),
    ("bullet", "Interest monthly, capital in one amount at the end"),
]
REPAY_SHORT = {"level": "equal monthly instalments", "straight": "equal capital instalments", "bullet": "bullet at the end"}
MAX_FACILITIES = 10
MAX_TERM = 240
DEFAULT_TERM = 12


def _month(value, default=""):
    s = str(value or "").strip()[:7]
    if len(s) == 7 and s[4] == "-" and s[:4].isdigit() and s[5:].isdigit() and 1 <= int(s[5:]) <= 12 and 1900 <= int(s[:4]) <= 2200:
        return s
    return default


def _date(value):
    """A real calendar date as YYYY-MM-DD, or '' when the value is not one."""
    s = str(value or "").strip()[:10]
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.date(int(s[:4]), int(s[5:7]), int(s[8:])).isoformat() if 1900 <= int(s[:4]) <= 2200 else ""
        except ValueError:
            return ""
    return ""


def first_frac(fac):
    """Share of the drawdown month the money is outstanding: (days from the drawdown date to month end, both inclusive) / days in the month.
    1.0 when only a month was given (treated as drawn on the 1st), so facilities entered before the date existed are unchanged."""
    d = fac.get("draw_date") or ""
    if not d:
        return 1.0
    y, m, day = int(d[:4]), int(d[5:7]), int(d[8:])
    dim = calendar.monthrange(y, m)[1]
    return (dim - day + 1) / dim


def _months_between(a, b):
    """Whole months from month a to month b (negative when b is earlier)."""
    return (int(b[:4]) - int(a[:4])) * 12 + (int(b[5:]) - int(a[5:]))


def facility_id(i):
    return f"fac{i + 1}"


def blank_facility(draw_month, i=0):
    return {"id": facility_id(i), "lender": "New facility", "amount": 0.0, "rate": 0.0, "fee_pct": 0.0, "fee_fixed": 0.0, "draw_month": draw_month,
            "draw_date": "", "term_months": DEFAULT_TERM, "grace_months": 0, "repay": "level", "include": True, "security": "", "notes": ""}


def normalise_facility(raw, i, default_month):
    raw = raw or {}
    repay = raw.get("repay") if raw.get("repay") in REPAY_SHORT else "level"
    term = int(max(1, min(MAX_TERM, f(raw.get("term_months"), DEFAULT_TERM))))
    grace = int(max(0, min(term - 1, f(raw.get("grace_months"), 0))))
    inc = raw.get("include")
    # the drawdown DATE is the input; the month is derived from it (a facility saved with only a month keeps that month, drawn on the 1st)
    draw_date = _date(raw.get("draw_date"))
    draw_month = draw_date[:7] if draw_date else _month(raw.get("draw_month"), default_month)
    return {
        "id": str(raw.get("id") or facility_id(i))[:24], "lender": str(raw.get("lender") or "").strip()[:60],
        "amount": max(0.0, f(raw.get("amount"))), "rate": max(0.0, min(200.0, f(raw.get("rate")))),
        "fee_pct": max(0.0, min(100.0, f(raw.get("fee_pct")))), "fee_fixed": max(0.0, f(raw.get("fee_fixed"))),
        "draw_month": draw_month, "draw_date": draw_date, "term_months": term, "grace_months": grace, "repay": repay,
        "include": True if inc is None else bool(inc),
        "security": str(raw.get("security") or "").strip()[:200], "notes": str(raw.get("notes") or "").strip()[:400],
    }


def facilities_of(d):
    """The pack's facilities, normalised. Facilities with no lender name and no amount are dropped."""
    try:
        default_month = M.fy_months(d)[0]
    except Exception:
        default_month = ""
    out = []
    for i, raw in enumerate((d.get("facilities") or [])[:MAX_FACILITIES]):
        if not isinstance(raw, dict):
            continue
        fac = normalise_facility(raw, i, default_month)
        if fac["lender"] or fac["amount"] > 0:
            out.append(fac)
    # ids must be unique
    seen = set()
    for k, fac in enumerate(out):
        while fac["id"] in seen:
            fac["id"] = facility_id(k + len(seen))
        seen.add(fac["id"])
    return out


def fee_of(fac):
    return fac["amount"] * fac["fee_pct"] / 100.0 + fac["fee_fixed"]


def label_of(fac):
    return fac["lender"] or "Facility"


# --------------------------------------------------------------------------- one facility's schedule

def schedule(fac):
    """Every month of the facility from the drawdown month to its final repayment.
    Rows: {month, n, open, draw, interest, fee, repay, close, payment}. Capital outstanding after the last month is nil."""
    amount, rate, term, grace = fac["amount"], fac["rate"], int(fac["term_months"]), int(fac["grace_months"])
    r = rate / 100.0 / 12.0
    k_pay = max(1, term - grace)                       # number of capital instalments
    rows, bal = [], 0.0
    pmt = 0.0
    if fac["repay"] == "level":
        pmt = amount / k_pay if r == 0 else amount * r / (1.0 - (1.0 + r) ** (-k_pay))
    for i in range(term):
        month = M.shift_month(fac["draw_month"], i)
        op = bal
        draw = amount if i == 0 else 0.0
        base = op + draw
        interest = base * r * (first_frac(fac) if i == 0 else 1.0)     # drawn part-way through the first month: only the days it is outstanding
        fee = fee_of(fac) if i == 0 else 0.0
        if i < grace:
            repay = 0.0
        elif i == term - 1:
            repay = base                                # the last instalment clears whatever is left
        elif fac["repay"] == "bullet":
            repay = 0.0
        elif fac["repay"] == "straight":
            repay = min(base, amount / k_pay)
        else:
            repay = min(base, max(0.0, pmt - interest))
        close = base - repay
        rows.append({"month": month, "n": i + 1, "open": op, "draw": draw, "interest": interest, "fee": fee, "repay": repay,
                     "close": close, "payment": interest + repay})
        bal = close
    return rows


def level_instalment(fac):
    """The equal monthly payment (capital + interest) of a 'level' facility; None for the other repayment types."""
    if fac["repay"] != "level":
        return None
    rows = schedule(fac)
    k = max(1, fac["term_months"] - fac["grace_months"])
    r = fac["rate"] / 100.0 / 12.0
    if r == 0:
        return fac["amount"] / k
    return fac["amount"] * r / (1.0 - (1.0 + r) ** (-k)) if rows else None


def totals(rows):
    return {"interest": sum(x["interest"] for x in rows), "fee": sum(x["fee"] for x in rows), "repay": sum(x["repay"] for x in rows),
            "draw": sum(x["draw"] for x in rows)}


def balance_at(rows, month):
    """Capital outstanding at the END of `month`: 0 before the facility is drawn and after it is repaid."""
    for x in rows:
        if x["month"] == month:
            return x["close"]
    if rows and month > rows[-1]["month"]:
        return 0.0
    return 0.0


# --------------------------------------------------------------------------- the pack's loan schedule by month

def pack_schedule(d, facs=None):
    """The facilities month by month over the pack's financial year, each month marked actual or forecast.
    Returns {months, actual_n, facilities:[{fac, rows:{month: row}, ...}], total:{month: {...}}, year:{...}}."""
    facs = facilities_of(d) if facs is None else facs
    months = M.fy_months(d)
    ridx = M.report_index(d)
    actual = set(months[:ridx + 1])
    out_f, tot = [], {m: {"open": 0.0, "draw": 0.0, "interest": 0.0, "fee": 0.0, "repay": 0.0, "close": 0.0} for m in months}
    for fac in facs:
        if not fac["include"] or fac["amount"] <= 0:
            continue
        rows = schedule(fac)
        by = {x["month"]: x for x in rows}
        view = {}
        for m in months:
            if m in by:
                view[m] = by[m]
            else:
                bal = balance_at(rows, M.shift_month(m, -1)) if m > fac["draw_month"] else 0.0
                view[m] = {"month": m, "n": None, "open": bal, "draw": 0.0, "interest": 0.0, "fee": 0.0, "repay": 0.0, "close": bal, "payment": 0.0}
            for k in ("open", "draw", "interest", "fee", "repay", "close"):
                tot[m][k] += view[m][k]
        yr = {k: sum(view[m][k] for m in months) for k in ("draw", "interest", "fee", "repay")}
        yr["open"] = view[months[0]]["open"]
        yr["close"] = view[months[-1]]["close"]
        out_f.append({"fac": fac, "rows": view, "year": yr, "life": totals(rows), "last_month": rows[-1]["month"] if rows else "",
                      "instalment": level_instalment(fac)})
    year = {k: sum(tot[m][k] for m in months) for k in ("draw", "interest", "fee", "repay")}
    year["open"] = tot[months[0]]["open"]
    year["close"] = tot[months[-1]]["close"]
    return {"months": months, "actual": actual, "actual_n": ridx + 1, "facilities": out_f, "total": tot, "year": year}


def checks(d, facs=None):
    """Plain-English findings the preparer should look at. Each is a string."""
    facs = facilities_of(d) if facs is None else facs
    msgs = []
    if not facs:
        return msgs
    months = M.fy_months(d)
    ridx = M.report_index(d)
    cur = months[ridx]
    on = [x for x in facs if x["include"] and x["amount"] > 0]
    for x in on:
        if not x["lender"]:
            msgs.append("A facility has no lender name. Name it so that it can be recognised in the forecast and reports.")
        if x["rate"] <= 0:
            msgs.append(f"{label_of(x)}: the interest rate is nil, so the schedule shows no interest.")
        if x["repay"] == "bullet" and x["grace_months"]:
            msgs.append(f"{label_of(x)}: a capital grace period has no effect on a bullet facility.")
    names = [label_of(x).strip().lower() for x in on]
    if len(set(names)) < len(names):
        msgs.append("Two facilities have the same lender name. Give them different names (for example the lender and the purpose).")
    ps = pack_schedule(d, facs)
    # fee in an actual month: the books should already hold it
    for fx in ps["facilities"]:
        fac = fx["fac"]
        if fac["draw_month"] <= cur and fee_of(fac) > 0 and fac["draw_month"] >= months[0]:
            msgs.append(f"{label_of(fac)} was drawn in {M.month_label(fac['draw_month'], False)}, a month that is already actual. Its establishment fee of "
                        f"{fee_of(fac):,.2f} should already be in the actual finance costs for that month; the forecast does not charge it again.")
    # schedule against the books
    try:
        bs_months = [m for m in months[:ridx + 1] if m in M.bs_series(d)]
    except Exception:
        bs_months = []
    if bs_months:
        bm = bs_months[-1]
        books = f(M.bs_series(d)[bm].get("borrowings"))
        sched = ps["total"][bm]["close"]
        if on and abs(books - sched) > max(1.0, 0.005 * max(books, sched)):
            msgs.append(f"At {M.month_label(bm, False)} the balance sheet shows borrowings of {books:,.2f} but the facilities schedule gives {sched:,.2f}. "
                        "Check the amounts, drawdown months and repayments, or whether other borrowings are in the books.")
    return msgs


def interest_check(d, facs=None):
    """Schedule interest against the interest expense in the actuals, month by month, for the actual months that have a P&L.
    Returns [{month, schedule, books, diff}]; books includes any establishment fee posted to interest."""
    ps = pack_schedule(d, facs)
    out = []
    try:
        cum = M.cumulative_actuals(d)
    except Exception:
        return out
    prev = 0.0
    for i, m in enumerate(ps["months"][:ps["actual_n"]]):
        c = cum[i]["cum"] if i < len(cum) else None
        if c is None:
            continue
        ytd = f(c.get("int_exp"))
        books = ytd - prev
        prev = ytd
        sch = ps["total"][m]["interest"] + ps["total"][m]["fee"]
        out.append({"month": m, "schedule": sch, "books": books, "diff": books - sch})
    return out


# --------------------------------------------------------------------------- the forecast model's loans

def engine_loans(facs, start_month, years):
    """Loans for the forecast model (cf_engine format) from the pack's facilities, for a model whose first month is `start_month`.
    A facility already drawn before the model starts opens at the balance outstanding then (its establishment fee is history and is
    not charged again); one drawn in a model month is a new drawdown whose fee is charged in that month.
    Returns (loans, notes)."""
    loans, notes = [], []
    horizon = years * 12
    for fac in facs:
        if not fac["include"] or fac["amount"] <= 0:
            continue
        rows = schedule(fac)
        end_month = rows[-1]["month"]
        name = label_of(fac)
        if end_month < start_month:
            notes.append(f"{name} is fully repaid before the forecast starts ({M.month_label(end_month, False)}), so it is not in the forecast.")
            continue
        idx0 = _months_between(start_month, fac["draw_month"])       # model month in which it is drawn (negative = already drawn)
        if idx0 >= horizon:
            notes.append(f"{name} is drawn in {M.month_label(fac['draw_month'], False)}, after the forecast ends, so it is not in the forecast.")
            continue
        plan = [0.0] * horizon
        for x in rows:
            j = _months_between(start_month, x["month"])
            if 0 <= j < horizon:
                plan[j] = x["repay"]
        last_idx = max(0, _months_between(start_month, end_month))
        base = {"name": name, "rate": fac["rate"], "repay_type": "straight", "grace_years": 0, "fac": fac["id"], "plan": plan,
                "maturity_year": max(1, min(30, last_idx // 12 + 1)), "repay_years": max(1, min(30, -(-(last_idx + 1) // 12))),
                "end_date": end_month + "-01"}
        if idx0 >= 0:
            fee = fee_of(fac)
            loans.append(dict(base, opening_balance=0.0, drawdown_amount=fac["amount"], drawdown_year=idx0 // 12 + 1,
                              start_date=fac["draw_month"] + "-01", draw_frac=first_frac(fac), fee_pct=0.0, fee_amount=fee))
            notes.append(f"{name}: drawn in {M.month_label(fac['draw_month'], False)} (a forecast month), so the establishment fee of {fee:,.2f} is charged then"
                         + (f", and its first month's interest is for the {first_frac(fac):.0%} of the month it is outstanding (drawn {fac['draw_date']})." if first_frac(fac) < 1 else "."))
        else:
            bal = balance_at(rows, M.shift_month(start_month, -1))
            loans.append(dict(base, opening_balance=bal, drawdown_amount=0.0, drawdown_year=1, start_date="", fee_pct=0.0, fee_amount=0.0))
            fee = fee_of(fac)
            notes.append(f"{name}: already drawn ({M.month_label(fac['draw_month'], False)}); {bal:,.2f} is outstanding when the forecast starts"
                         + (f" and its establishment fee of {fee:,.2f} was charged when it was drawn, so the forecast does not charge it again." if fee > 0 else "."))
    return loans, notes


def adjust_of(d):
    """How the facilities' opening gap (facilities the books do not hold) is absorbed: stock up, payables down, the rest reserves."""
    a = d.get("facility_adjust") if isinstance(d.get("facility_adjust"), dict) else {}
    return {"inventory": max(0.0, f(a.get("inventory"))), "payables": max(0.0, f(a.get("payables")))}


def parse_adjust(form):
    return {"inventory": max(0.0, f(form.get("adj_inventory"))), "payables": max(0.0, f(form.get("adj_payables")))}


def fingerprint(d):
    """Changes whenever the facilities (or whether they are in use) change; stored on the forecast link to flag a forecast that is out of date."""
    facs = facilities_of(d)
    if not facs:
        return ""
    # a facility with no drawdown date hashes exactly as before the date existed, so packs saved earlier are not flagged as out of date
    facs = [{k: v for k, v in fac.items() if not (k == "draw_date" and not v)} for fac in facs]
    adj = adjust_of(d)
    if adj["inventory"] or adj["payables"]:
        facs = facs + [{"_adjust": adj}]       # changing the allocation of the opening gap also makes the forecast out of date
    return hashlib.md5(json.dumps(facs, sort_keys=True).encode()).hexdigest()[:12]


def parse_form(form, d, skip=None):
    """Facilities from the Loan facilities form: fields fac_<field>_<i>, count in n_fac."""
    try:
        default_month = M.fy_months(d)[0]
    except Exception:
        default_month = ""
    try:
        n = int(form.get("n_fac") or 0)
    except ValueError:
        n = 0
    out = []
    for i in range(min(n, MAX_FACILITIES + 5)):
        if skip is not None and i == skip:
            continue
        raw = {"id": form.get(f"fac_id_{i}") or "", "lender": form.get(f"fac_lender_{i}"), "amount": form.get(f"fac_amount_{i}"),
               "rate": form.get(f"fac_rate_{i}"), "fee_pct": form.get(f"fac_feepct_{i}"), "fee_fixed": form.get(f"fac_feefix_{i}"),
               "draw_date": form.get(f"fac_drawdate_{i}"), "draw_month": form.get(f"fac_draw_{i}"), "term_months": form.get(f"fac_term_{i}"), "grace_months": form.get(f"fac_grace_{i}"),
               "repay": form.get(f"fac_repay_{i}"), "include": bool(form.get(f"fac_inc_{i}")), "security": form.get(f"fac_sec_{i}"),
               "notes": form.get(f"fac_notes_{i}")}
        if not (str(raw["lender"] or "").strip() or f(raw["amount"]) > 0):
            continue
        out.append(normalise_facility(raw, len(out), default_month))
    return out[:MAX_FACILITIES]


# --------------------------------------------------------------------------- the facilities in the statements

def _due_within_12(rows, month):
    """Capital falling due in the 12 months after the end of `month`."""
    start, end = M.shift_month(month, 1), M.shift_month(month, 12)
    return sum(x["repay"] for x in rows if start <= x["month"] <= end)


def unbooked_gap(d):
    """Amount by which a forecast that follows the loan facilities cuts the reserves at its start, because the books' balance sheet does not
    hold facilities the Loan facilities tab says are outstanding (the engine balances the opening position with the reserves). 0 when the
    forecast is not a continuation of the actuals, or the books already hold the facilities."""
    fc = d.get("forecast") or {}
    link = d.get("forecast_link") or {}
    start = str(link.get("start_month") or "")[:7]
    if fc.get("source") != "continuation" or not start or not (link.get("fac_fp") or ""):
        return 0.0
    try:
        a_bs = M.bs_series(d).get(M.shift_month(start, -1))
        f_bs = M.forecast_bs(d).get(start)
        f_pl = M.forecast_months(d).get(start)
        if not a_bs or not f_bs or not f_pl:
            return 0.0
        v = M.pl_vector(M.normalise_structure(d.get("structure")), f_pl)
        opening_reserves = f(f_bs.get("retained")) - (v["pat"] - v["div"])
        gap = f(a_bs.get("retained")) - opening_reserves
    except Exception:
        return 0.0
    return gap if gap > 1.0 else 0.0


ADJ_LABEL = "Loan facilities not yet in the books (adjustment)"


def with_facilities(bal, d, facs=None):
    """The balance sheet rows with the loan facilities shown inside the table, under Borrowings: the facilities' balance (from their
    schedule), and how much of it falls due within 12 months / after. Presentation only: no total, balance check or other line changes,
    so the statement still ties to the books. The Borrowings line itself is the books (actual) or the forecast, which may not hold the
    facilities - the card under the table shows the difference. Returns `bal` unchanged when there are no facilities in use."""
    facs = [x for x in (facilities_of(d) if facs is None else facs) if x["include"] and x["amount"] > 0]
    if not facs or not bal or not bal.get("rows"):
        return bal
    months = M.fy_months(d)
    cur = bal.get("month") or months[M.report_index(d)]
    at = {"lm": M.shift_month(cur, -1), "a": cur, "f": cur, "ffy": months[-1], "py": M.shift_month(months[0], -1)}
    full = [schedule(x) for x in facs]

    def balance(m):
        return sum(balance_at(r, m) for r in full)

    def due(m):
        return min(balance(m), sum(_due_within_12(r, m) for r in full))

    def mk(key, label, fn):
        r = {"key": key, "label": label, "kind": "indent", "b": None, "bfy": None, "var": None, "chg_month": None}
        for col, m in at.items():
            r[col] = fn(m)
        if not bal.get("has_forecast"):
            r["f"] = r["ffy"] = None
        r["chg_year"] = None if r["a"] is None or r["py"] is None else r["a"] - r["py"]
        return r

    extra = [mk("fac_total", "of which: loan facilities (per their schedule)", balance),
             mk("fac_current", "due within 12 months", due),
             mk("fac_noncurrent", "due after 12 months", lambda m: balance(m) - due(m))]
    gap = unbooked_gap(d)
    rows = []
    for r in bal["rows"]:
        if gap and r.get("key") == "retained":
            # reserves run on from the actuals; the cut the facilities cause is shown on its own line (total equity is unchanged)
            r = dict(r, **{c: (None if r.get(c) is None else r[c] + gap) for c in ("f", "ffy")})
            rows.append(r)
            rows.append({"key": "fac_gap", "label": ADJ_LABEL, "kind": "line", "lm": None, "a": None, "b": None, "bfy": None, "py": None, "var": None,
                         "chg_year": None, "chg_month": None, "f": (None if r["f"] is None else -gap), "ffy": (None if r["ffy"] is None else -gap)})
            continue
        rows.append(r)
        if r.get("key") == "borrowings":
            rows += extra
    return dict(bal, rows=rows)


def cashflow_with_facilities(cfl, d):
    """The cash flow rows with the facilities' opening gap split out of 'Other equity movements' into its own (non-cash) line, in the
    full-year forecast column. Totals do not change."""
    gap = unbooked_gap(d)
    if not gap or not cfl or not cfl.get("rows"):
        return cfl
    rows = []
    for r in cfl["rows"]:
        if r.get("key") == "other_equity" and r.get("ffy") is not None:
            r = dict(r, ffy=r["ffy"] + gap)
            rows.append(r)
            rows.append({"key": "fac_gap", "label": ADJ_LABEL + ", non-cash", "kind": "indent", "lm": None, "a": None, "f": None, "b": None, "ya": None,
                         "yb": None, "ffy": -gap, "bfy": None, "var": None, "yvar": None})
            continue
        rows.append(r)
    return dict(cfl, rows=rows)


def statement_analysis(d, facs=None):
    """Loan facility figures laid out against the income statement, balance sheet, cash flow and KPIs of the pack.
    Returns None when there are no facilities in use, else {income, balance, cashflow, kpis}: each a table
    {title, cols, rows:[{label, vals, kind, fmt}], note}. A value of None means 'not available'.
    The facility figures come from the schedule; the statement figures are the pack's own (actual to the reporting month, forecast after),
    so the balancing line shows what the statement holds beyond the facilities (other interest, other borrowings) - or what it lacks."""
    facs = [x for x in (facilities_of(d) if facs is None else facs) if x["include"] and x["amount"] > 0]
    if not facs:
        return None
    ps = pack_schedule(d, facs)
    months, ridx = ps["months"], ps["actual_n"] - 1
    cur, fy_end = months[ridx], months[-1]
    V = M.vectors(d)
    fy = M.fy_label(d)
    cur_lab = M.month_label(cur, False)
    sets = {"month": [cur], "ytd": months[:ridx + 1], "fy": months}
    ordr = ("month", "ytd", "fy")

    def tot(key, per, fxs=None):
        return sum(fx["rows"][m][key] for fx in (fxs or ps["facilities"]) for m in sets[per])

    def sub(a, b):
        return None if (a is None or b is None) else a - b

    def row(label, vals, kind="line", fmt="money"):
        return {"label": label, "vals": vals, "kind": kind, "fmt": fmt}

    # ---- income statement
    stmt = {"month": (V["actual_month"] or {}).get("int_exp") if V["actual_month"] else None,
            "ytd": (V["actual_ytd"] or {}).get("int_exp") if V["actual_ytd"] else None,
            "fy": (V["outlook"] or {}).get("int_exp") if V["outlook"] else None}
    rows = []
    for fx in ps["facilities"]:
        rows.append(row(f"Interest - {label_of(fx['fac'])}", [tot("interest", p, [fx]) for p in ordr], "indent"))
    rows.append(row("Interest on loan facilities", [tot("interest", p) for p in ordr], "line"))
    rows.append(row("Establishment fees (expensed when drawn)", [tot("fee", p) for p in ordr], "line"))
    cost = [tot("interest", p) + tot("fee", p) for p in ordr]
    rows.append(row("Total loan facility cost", cost, "total"))
    rows.append(row("Interest expense per income statement", [stmt[p] for p in ordr], "line"))
    other = [sub(stmt[p], cost[i]) for i, p in enumerate(ordr)]
    rows.append(row("Other interest and charges (statement less facilities)", other, "memo"))
    note_i = ("Actual months are the books; later months are the forecast. A negative balance means the statement holds less interest than the facilities "
              "schedule: for actual months the books may not yet record the facility interest or fees; for forecast months the forecast may not be "
              "linked to these facilities (use 'Save and update the forecast' on the Loan facilities tab).")
    income = {"title": "Loan facilities in the income statement", "cols": [cur_lab, "Year to date", f"Full year {fy}"], "rows": rows, "note": note_i,
              "warn": any(v is not None and v < -max(1.0, 0.02 * (cost[i] or 0)) for i, v in enumerate(other))}

    # ---- balance sheet
    ser, fser, bser = M.bs_series(d), M.forecast_bs(d), M.budget_bs(d)
    bs_cur = ser.get(cur) or fser.get(cur)
    bs_end = fser.get(fy_end) or bser.get(fy_end) or ser.get(fy_end)
    full = {fx["fac"]["id"]: schedule(fx["fac"]) for fx in ps["facilities"]}
    at = (cur, fy_end)
    rows = []
    for fx in ps["facilities"]:
        rows.append(row(f"{label_of(fx['fac'])} - balance outstanding", [fx["rows"][m]["close"] for m in at], "indent"))
    bal = [sum(fx["rows"][m]["close"] for fx in ps["facilities"]) for m in at]
    rows.append(row("Loan facility borrowings", bal, "total"))
    bs_b = [(f(b.get("borrowings")) if b else None) for b in (bs_cur, bs_end)]
    rows.append(row("Borrowings per balance sheet", bs_b, "line"))
    rows.append(row("Borrowings not in the facilities (balance)", [sub(bs_b[i], bal[i]) for i in range(2)], "memo"))
    due = [sum(_due_within_12(full[fx["fac"]["id"]], m) for fx in ps["facilities"]) for m in at]
    due = [min(due[i], bal[i]) for i in range(2)]
    rows.append(row("Facility capital due within 12 months (current)", due, "line"))
    rows.append(row("Facility capital due after 12 months (non-current)", [bal[i] - due[i] for i in range(2)], "line"))
    balance = {"title": "Loan facilities in the balance sheet", "cols": [f"At {cur_lab}", f"At {M.month_label(fy_end, False)}"], "rows": rows,
               "note": "The balance sheet holds borrowings as one line. This splits the facilities' balance into what falls due within 12 months (a current liability) "
                       "and the rest. At the reporting month the balance sheet is the actual one; at the year end it is the forecast (or budget).",
               "warn": any(bs_b[i] is not None and bs_b[i] < bal[i] - max(1.0, 0.005 * bal[i]) for i in range(2))}

    # ---- cash flow
    cfr = {r["key"]: r for r in M.cashflow_rows(d)["rows"]}
    cfv = {"month": ("a",), "ytd": ("ya",), "fy": ("ffy",)}
    def cfg(key, per):
        r = cfr.get(key)
        return r[cfv[per][0]] if r else None
    draw = [tot("draw", p) for p in ordr]
    rep = [tot("repay", p) for p in ordr]
    net = [draw[i] - rep[i] for i in range(3)]
    rows = [row("Loan drawdowns", draw), row("Capital repaid", [-x for x in rep]),
            row("Net loan drawdown / (repayment) - facilities", net, "total"),
            row("Net loan drawdown / (repayment) - cash flow statement", [cfg("loans", p) for p in ordr]),
            row("Difference (statement less facilities)", [sub(cfg("loans", p), net[i]) for i, p in enumerate(ordr)], "memo"),
            row("Interest and fees paid - facilities", [-(tot("interest", p) + tot("fee", p)) for p in ordr]),
            row("Net interest - cash flow statement", [cfg("interest", p) for p in ordr], "line")]
    cashflow = {"title": "Loan facilities in the cash flow", "cols": [cur_lab, "Year to date", f"Full year {fy}"], "rows": rows,
                "note": "Capital repayments and drawdowns are financing cash flows; interest and fees reduce operating cash. The cash flow statement is derived from the "
                        "movement between balance sheets, so a difference means borrowings moved by something other than the facilities.",
                "warn": False}

    # ---- KPIs
    ebitda = {"ytd": (V["actual_ytd"] or {}).get("ebitda") if V["actual_ytd"] else None, "fy": (V["outlook"] or {}).get("ebitda") if V["outlook"] else None}
    ebit = {"ytd": (V["actual_ytd"] or {}).get("ebit") if V["actual_ytd"] else None, "fy": (V["outlook"] or {}).get("ebit") if V["outlook"] else None}
    ds = {p: tot("interest", p) + tot("fee", p) + tot("repay", p) for p in ("ytd", "fy")}
    cs = {p: tot("interest", p) + tot("fee", p) for p in ("ytd", "fy")}
    div = lambda a, b: (a / b) if (a is not None and b and b > 1e-9) else None
    rows = [row("Debt service (facility interest, fees and capital)", [ds["ytd"], ds["fy"]]),
            row("Debt service cover (EBITDA / debt service)", [div(ebitda["ytd"], ds["ytd"]), div(ebitda["fy"], ds["fy"])], "line", "x"),
            row("Interest cover on the facilities (EBIT / interest and fees)", [div(ebit["ytd"], cs["ytd"]), div(ebit["fy"], cs["fy"])], "line", "x")]
    cur_ratio, cur_adj = None, None
    if bs_cur:
        ca = f(bs_cur["inventory"]) + f(bs_cur["receivables"]) + f(bs_cur["other_assets"]) + f(bs_cur["cash"])
        cl = f(bs_cur["payables"]) + f(bs_cur["other_liabilities"]) + f(bs_cur["overdraft"])
        cur_ratio = div(ca, cl)
        due_cur = min(due[0], bal[0])
        cur_adj = div(ca, cl + due_cur)
    rows.append(row(f"Current ratio as reported, {cur_lab}", [cur_ratio, None], "line", "x"))
    rows.append(row(f"Current ratio including facility capital due within 12 months, {cur_lab}", [cur_adj, None], "line", "x"))
    kpis = {"title": "Loan facilities and the KPIs", "cols": ["Year to date", f"Full year {fy}"], "rows": rows,
            "note": "Debt service cover below about 1.2x, or a current ratio that falls below 1 once the instalments due within 12 months are counted, "
                    "is what a lender will look at first.", "warn": bool(cur_adj is not None and cur_ratio is not None and cur_adj < 1.0 <= cur_ratio)}
    return {"income": income, "balance": balance, "cashflow": cashflow, "kpis": kpis}
