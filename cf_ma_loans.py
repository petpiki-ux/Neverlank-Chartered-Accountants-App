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

A facility can be drawn in several tranches (`draws`: a date and an amount each). Each tranche bears interest from its own date (the part-month rule
above applies to every tranche) and carries its own establishment fee % when drawn (the fixed fee goes with the first tranche). Repayments can be
given as a FIRST REPAYMENT DATE and a NUMBER OF INSTALMENTS: interest only until the month of that date, then one instalment at the end of each month.
A level instalment is worked out on the balance when repayments start and is worked out again whenever a further tranche is drawn after that.
Repayments can also be set date by date (`repays`: a month and a % of the facility, or an amount, for each): capital is paid at the end of
each of those months, interest runs on the balance as usual, and the last month listed clears whatever is left.
Facilities entered earlier with one amount, one date, a term and a grace period behave exactly as before.
"""
import calendar
import copy
import datetime
import hashlib
import json

import cf_ma as M

f = M.f

REPAY_TYPES = [
    ("level", "Equal monthly instalments (capital + interest the same every month)"),
    ("straight", "Equal capital instalments (interest falls as the balance falls)"),
    ("bullet", "Interest monthly, capital in one amount at the end"),
    ("custom", "Repayments on set dates: a % of the facility (or an amount) for each month"),
]
REPAY_SHORT = {"level": "equal monthly instalments", "straight": "equal capital instalments", "bullet": "bullet at the end",
               "custom": "repayments on the dates and percentages entered"}
MAX_FACILITIES = 10
MAX_TERM = 240
MAX_DRAWS = 12
MAX_REPAYS = 36
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


def date_frac(d):
    """Share of the drawdown month the money is outstanding: (days from the drawdown date to month end, both inclusive) / days in the month.
    1.0 when only a month was given (treated as drawn on the 1st), so facilities entered before the date existed are unchanged."""
    if not d:
        return 1.0
    y, m, day = int(d[:4]), int(d[5:7]), int(d[8:])
    dim = calendar.monthrange(y, m)[1]
    return (dim - day + 1) / dim


def first_frac(fac):
    """The part-month share of the FIRST drawdown (see date_frac)."""
    return date_frac(fac.get("draw_date") or "")


def _months_between(a, b):
    """Whole months from month a to month b (negative when b is earlier)."""
    return (int(b[:4]) - int(a[:4])) * 12 + (int(b[5:]) - int(a[5:]))


def facility_id(i):
    return f"fac{i + 1}"


def blank_facility(draw_month, i=0):
    return {"id": facility_id(i), "lender": "New facility", "amount": 0.0, "rate": 0.0, "fee_pct": 0.0, "fee_fixed": 0.0, "draw_month": draw_month,
            "draw_date": "", "draws": [], "first_repay": "", "n_inst": 0, "term_months": DEFAULT_TERM, "grace_months": 0, "repay": "level", "include": True,
            "security": "", "notes": ""}


def _draw_lines(raw, default_month):
    """The drawdown lines as entered: [{date, month, amount}], oldest first. A facility saved with one amount and one date/month has one line."""
    lines = []
    src = raw.get("draws")
    if isinstance(src, list) and len(src) == 1 and isinstance(src[0], dict):
        # one line only: the facility's own amount / date / month are the truth, so changing them on a stored facility takes effect
        x = src[0]
        xd = _date(x.get("date"))
        if (f(raw.get("amount")) != f(x.get("amount")) and raw.get("amount") not in (None, "")) \
                or (_date(raw.get("draw_date")) and _date(raw.get("draw_date")) != xd) \
                or (_month(raw.get("draw_month"), "") and _month(raw.get("draw_month"), "") != (xd[:7] if xd else _month(x.get("month"), ""))):
            src = None
    if isinstance(src, list) and src:
        for x in src[:MAX_DRAWS]:
            if not isinstance(x, dict):
                continue
            dt = _date(x.get("date"))
            lines.append({"date": dt, "month": dt[:7] if dt else _month(x.get("month"), ""), "amount": max(0.0, f(x.get("amount")))})
    else:
        dt = _date(raw.get("draw_date"))
        lines.append({"date": dt, "month": dt[:7] if dt else _month(raw.get("draw_month"), default_month), "amount": max(0.0, f(raw.get("amount")))})
    lines.sort(key=lambda x: (x["date"] or (x["month"] + "-01" if x["month"] else "9999-99-99")))
    return lines


def _repay_lines(raw):
    """The repayment lines as entered: [{date, month, pct, amount}], oldest first. A line has a % of the facility or an amount (an amount wins)."""
    out = []
    src = raw.get("repays")
    if isinstance(src, list):
        for x in src[:MAX_REPAYS]:
            if not isinstance(x, dict):
                continue
            dt = _date(x.get("date"))
            out.append({"date": dt, "month": dt[:7] if dt else _month(x.get("month"), ""),
                        "pct": max(0.0, min(1000.0, f(x.get("pct")))), "amount": max(0.0, f(x.get("amount")))})
    out.sort(key=lambda x: (x["month"] or "9999-99", x["date"]))
    return out


def repayments(fac):
    """The repayments that count for a facility on set dates: [{month, amount}] by month, oldest first (a % is of the total drawn)."""
    by = {}
    for x in fac.get("repays") or []:
        if not x["month"]:
            continue
        amt = x["amount"] if x["amount"] > 0 else fac["amount"] * x["pct"] / 100.0
        if amt > 0:
            by[x["month"]] = by.get(x["month"], 0.0) + amt
    return [{"month": m, "amount": by[m]} for m in sorted(by)]


_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def parse_repay_text(text):
    """Repayment lines pasted as text, one per line, e.g. '5%  May 2027', 'Apr-27  16%', '2026-11  5%', '25,000  Nov 2026' (a number without % is an amount).
    Returns ([{date, pct, amount}], [lines that could not be read]). Each repayment is dated the last day of its month (capital is paid at month end)."""
    import re
    lines, bad = [], []
    for raw_line in str(text or "").splitlines():
        t = raw_line.strip()
        if not t:
            continue
        month = ""
        m = re.search(r"(?<![A-Za-z])(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s\-/,']*((?:19|20)?\d{2})(?!\d)", t, re.I)
        if m:
            yy = int(m.group(2))
            yy = yy + 2000 if yy < 100 else yy
            month = f"{yy:04d}-{_MONTHS[m.group(1).lower()[:3]]:02d}"
            t2 = t[:m.start()] + " " + t[m.end():]
        else:
            m = re.search(r"((?:19|20)\d{2})-(\d{1,2})(?:-\d{1,2})?", t)
            if m:
                month = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
                t2 = t[:m.start()] + " " + t[m.end():]
            else:
                m = re.search(r"(?:\d{1,2}/)?(\d{1,2})/((?:19|20)\d{2})", t)
                if m:
                    month = f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
                    t2 = t[:m.start()] + " " + t[m.end():]
                else:
                    t2 = t
        if not _month(month, ""):
            bad.append(raw_line.strip())
            continue
        pm = re.search(r"(\d+(?:[.,]\d+)?)\s*%", t2)
        pct, amount = 0.0, 0.0
        if pm:
            pct = float(pm.group(1).replace(",", "."))
        else:
            nm = re.search(r"\d[\d,\s]*(?:\.\d+)?", t2)
            if nm and f(nm.group(0).replace(",", "").replace(" ", "")) > 0:
                amount = f(nm.group(0).replace(",", "").replace(" ", ""))
        if pct <= 0 and amount <= 0:
            bad.append(raw_line.strip())
            continue
        y, mo = int(month[:4]), int(month[5:])
        lines.append({"date": datetime.date(y, mo, calendar.monthrange(y, mo)[1]).isoformat(), "pct": pct, "amount": amount})
    return lines[:MAX_REPAYS], bad


def tranches(fac):
    """The drawdowns that count (amount above nil): [{date, month, amount}], oldest first; a line with no date takes the previous line's month."""
    out, prev = [], fac.get("draw_month") or ""
    lines = fac.get("draws") or []
    if len(lines) <= 1:
        # a facility with one drawdown: its own amount, date and month are the truth
        return [{"date": fac.get("draw_date") or "", "month": prev, "amount": fac["amount"]}] if fac.get("amount", 0) > 0 and prev else []
    for x in lines:
        if x["amount"] <= 0:
            continue
        mo = x["month"] or prev
        if mo:
            prev = mo
        out.append({"date": x["date"], "month": mo, "amount": x["amount"]})
    return out


def normalise_facility(raw, i, default_month):
    raw = raw or {}
    repay = raw.get("repay") if raw.get("repay") in REPAY_SHORT else "level"
    term = int(max(1, min(MAX_TERM, f(raw.get("term_months"), DEFAULT_TERM))))
    grace = int(max(0, min(term - 1, f(raw.get("grace_months"), 0))))
    inc = raw.get("include")
    # the drawdowns are the input (a date and an amount each); the facility's amount and first drawdown month are derived from them
    # (a facility saved with only a month keeps that month, drawn on the 1st)
    lines = _draw_lines(raw, default_month)
    act = [x for x in lines if x["amount"] > 0]
    first = act[0] if act else lines[0]
    amount = sum(x["amount"] for x in act)
    draw_month = first["month"] or default_month
    draw_date = first["date"]
    # repayments: either the term and capital grace (as before) or a FIRST REPAYMENT DATE and a number of instalments
    first_repay = _date(raw.get("first_repay"))
    n_inst = 0
    if first_repay and draw_month:
        gap = max(0, _months_between(draw_month, first_repay[:7]))
        if repay == "bullet":
            grace, term, n_inst = 0, int(max(1, min(MAX_TERM, gap + 1))), 1
        else:
            n_inst = int(max(0, min(MAX_TERM, f(raw.get("n_inst")))))
            if n_inst <= 0:
                n_inst = max(1, term - grace)
            grace = min(gap, MAX_TERM - 1)
            n_inst = max(1, min(n_inst, MAX_TERM - grace))
            term = grace + n_inst
    repays = _repay_lines(raw)
    if repay == "custom":
        # repayments on set dates: the first and last month listed fix the interest-only period and the term
        # (a % is of the total drawn, so the amounts are worked out below once the amount is known)
        act = [x for x in repays if x["month"] and (x["pct"] > 0 or x["amount"] > 0)]
        if act and draw_month:
            gap = max(0, _months_between(draw_month, act[0]["month"]))
            last = max(gap, _months_between(draw_month, act[-1]["month"]))
            grace = min(gap, MAX_TERM - 1)
            term = int(max(grace + 1, min(MAX_TERM, last + 1)))
            n_inst = len({x["month"] for x in act})
            first_repay = act[0]["date"] or (act[0]["month"] + "-01")
    return {
        "id": str(raw.get("id") or facility_id(i))[:24], "lender": str(raw.get("lender") or "").strip()[:60],
        "amount": amount, "repays": repays, "rate": max(0.0, min(200.0, f(raw.get("rate")))),
        "fee_pct": max(0.0, min(100.0, f(raw.get("fee_pct")))), "fee_fixed": max(0.0, f(raw.get("fee_fixed"))),
        "draw_month": draw_month, "draw_date": draw_date, "draws": lines, "first_repay": first_repay if n_inst else "", "n_inst": n_inst,
        "term_months": term, "grace_months": grace, "repay": repay,
        "include": True if inc is None else bool(inc),
        "security": str(raw.get("security") or "").strip()[:200], "notes": str(raw.get("notes") or "").strip()[:400],
    }


def repay_start(fac):
    """The first repayment date as shown on the form: the date entered, or (facilities entered with a term and grace) the 1st of the first instalment month."""
    if fac.get("first_repay"):
        return fac["first_repay"]
    if not fac.get("draw_month"):
        return ""
    return M.shift_month(fac["draw_month"], int(fac.get("grace_months") or 0)) + "-01"


def instalments_of(fac):
    """Number of capital instalments (a bullet has one)."""
    if fac["repay"] == "bullet":
        return 1
    if fac["repay"] == "custom" and repayments(fac):
        return len(repayments(fac))
    return max(1, int(fac["term_months"]) - int(fac["grace_months"]))


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

def _annuity(balance, r, k):
    """Equal payment that clears `balance` in k payments at monthly rate r."""
    if k <= 0:
        return balance
    return balance / k if r == 0 else balance * r / (1.0 - (1.0 + r) ** (-k))


def schedule(fac):
    """Every month of the facility from the first drawdown month to its final repayment.
    Rows: {month, n, open, draw, draw_frac, interest, fee, repay, close, payment}. Capital outstanding after the last month is nil.
    Interest = opening balance x rate/12 + each tranche drawn in the month x rate/12 x its share of the month outstanding.
    Repayments on set dates: the amount entered for each month (capital paid at month end).
    Capital: nothing until the capital grace months have passed; then the instalment (level: re-worked whenever a further tranche is drawn;
    equal capital: balance / instalments left, re-worked the same way); the last month repays whatever is left."""
    rate, term, grace = fac["rate"], int(fac["term_months"]), int(fac["grace_months"])
    r = rate / 100.0 / 12.0
    k_pay = max(1, term - grace)                       # number of capital instalments
    by_m = {}
    for t in tranches(fac):
        e = by_m.setdefault(t["month"], {"amt": 0.0, "w": 0.0, "fee": 0.0})
        e["amt"] += t["amount"]
        e["w"] += t["amount"] * date_frac(t["date"])
        e["fee"] += t["amount"] * fac["fee_pct"] / 100.0
    if not by_m:                                       # nothing drawn: a nil facility
        by_m = {fac["draw_month"]: {"amt": 0.0, "w": 0.0, "fee": 0.0}}
    last_draw = max(_months_between(fac["draw_month"], mo) for mo in by_m)
    n_rows = max(term, last_draw + 1)                  # a tranche drawn after the term ends extends the facility to that month
    rep_by = {x["month"]: x["amount"] for x in repayments(fac)} if fac["repay"] == "custom" else {}
    rows, bal, step = [], 0.0, 0.0
    for i in range(n_rows):
        month = M.shift_month(fac["draw_month"], i)
        e = by_m.get(month)
        op = bal
        draw = e["amt"] if e else 0.0
        base = op + draw
        interest = r * (op + (e["w"] if e else 0.0))   # a tranche drawn part-way through its month: only the days it is outstanding
        fee = (e["fee"] if e else 0.0) + (fac["fee_fixed"] if i == 0 else 0.0)
        left = k_pay - (i - grace)                      # instalments still to pay, this month included
        if fac["repay"] != "custom" and i >= grace and (i == grace or draw > 0) and left > 0:
            # the instalment is (re)set when repayments start and whenever more is drawn after that
            step = _annuity(base, r, left) if fac["repay"] == "level" else base / left
        if i < grace:
            repay = 0.0
        elif i == term - 1 or i == n_rows - 1:
            repay = base                                # the last instalment clears whatever is left
        elif fac["repay"] == "bullet":
            repay = 0.0
        elif fac["repay"] == "custom":
            repay = min(base, rep_by.get(month, 0.0))      # the amount set for this month (the last month listed clears the rest)
        elif fac["repay"] == "straight":
            repay = min(base, step)
        else:
            repay = min(base, max(0.0, step - interest))
        close = base - repay
        rows.append({"month": month, "n": i + 1, "open": op, "draw": draw, "draw_frac": (e["w"] / e["amt"]) if e and e["amt"] > 0 else 1.0,
                     "interest": interest, "fee": fee, "repay": repay, "close": close, "payment": interest + repay})
        bal = close
    return rows


def level_instalment(fac):
    """The equal monthly payment (capital + interest) of a 'level' facility, worked out on what is outstanding when repayments start;
    None for the other repayment types. (It is worked out again when more is drawn after that.)"""
    if fac["repay"] != "level":
        return None
    k = max(1, fac["term_months"] - fac["grace_months"])
    r = fac["rate"] / 100.0 / 12.0
    start = M.shift_month(fac["draw_month"], int(fac["grace_months"]))
    bal = sum(t["amount"] for t in tranches(fac) if t["month"] <= start)
    return _annuity(bal, r, k) if bal > 0 else None


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


def raw(d):
    """The pack as the books hold it (the statements may be shown with the loan facilities added: see `shown`)."""
    return d.get("_raw") or d


def overlay_on(d):
    return raw(d).get("facility_overlay") is not False


def checks(d, facs=None):
    """Plain-English findings the preparer should look at. Each is a string."""
    d = raw(d)
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
    # fees in actual months: the books should already hold them
    for fx in ps["facilities"]:
        fac = fx["fac"]
        sched = schedule(fac)
        in_act = [x for x in sched if months[0] <= x["month"] <= cur and x["fee"] > 0]
        if len(tranches(fac)) <= 1:
            if fac["draw_month"] <= cur and fee_of(fac) > 0 and fac["draw_month"] >= months[0]:
                msgs.append(f"{label_of(fac)} was drawn in {M.month_label(fac['draw_month'], False)}, a month that is already actual. Its establishment fee of "
                            f"{fee_of(fac):,.2f} should already be in the actual finance costs for that month; the forecast does not charge it again.")
        elif in_act:
            msgs.append(f"{label_of(fac)} was drawn in {', '.join(M.month_label(x['month'], False) for x in in_act)}, which are already actual. Its establishment fees of "
                        f"{sum(x['fee'] for x in in_act):,.2f} should already be in the actual finance costs for those months; the forecast does not charge them again.")
        # the dates the preparer entered
        if fac["first_repay"] and fac["first_repay"][:7] < fac["draw_month"]:
            msgs.append(f"{label_of(fac)}: the first repayment date is before the first drawdown, so repayments start in the drawdown month.")
        tr = tranches(fac)
        if len(tr) > 1 and fac["repay"] != "bullet":
            start = M.shift_month(fac["draw_month"], int(fac["grace_months"]))
            if any(t["month"] > start for t in tr):
                msgs.append(f"{label_of(fac)}: a drawdown after repayments have started adds to the balance; the instalment is worked out again over the instalments that are left.")
        if tr and tr[-1]["month"] > M.shift_month(fac["draw_month"], int(fac["term_months"]) - 1):
            msgs.append(f"{label_of(fac)}: the last drawdown ({M.month_label(tr[-1]['month'], False)}) is after the final instalment, so the facility runs on to that month and clears then. Check the dates.")
        if fac["repay"] == "custom":
            rp = repayments(fac)
            if not rp:
                msgs.append(f"{label_of(fac)}: repayments are set by date but none are entered, so the whole facility is repaid at its term end ({M.month_label(sched[-1]['month'], False)}).")
            else:
                tot = sum(x["amount"] for x in rp)
                pct = tot / fac["amount"] * 100 if fac["amount"] else 0
                if abs(tot - fac["amount"]) > 0.5:
                    msgs.append(f"{label_of(fac)}: the repayments entered total {tot:,.2f} ({pct:.1f}% of the {fac['amount']:,.2f} drawn). "
                                + ("The last repayment month clears the rest." if tot < fac["amount"] else "The balance is repaid first and the excess is ignored."))
                early = [x for x in rp if x["month"] < fac["draw_month"]]
                if early:
                    msgs.append(f"{label_of(fac)}: a repayment is dated before the first drawdown ({M.month_label(early[0]['month'], False)}) and is ignored.")
        if any(x["amount"] > 0 and not x["month"] for x in fac["draws"]):
            msgs.append(f"{label_of(fac)}: a drawdown has no date; it is taken as drawn in the same month as the one before it.")
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
    d = raw(d)
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
        drawn = [x for x in rows if x["draw"] > 0]
        multi = len({x["month"] for x in drawn}) > 1
        later = [x for x in drawn if 0 <= _months_between(start_month, x["month"]) < horizon]     # drawdowns inside the model
        if multi:
            # several drawdown months: the model gets the drawdown, its part-month share and its fee for each month
            dr = [{"idx": _months_between(start_month, x["month"]), "amount": x["draw"], "frac": x["draw_frac"], "fee": x["fee"]} for x in later]
            tot_draw = sum(x["draw"] for x in later)
            first_in = idx0 >= 0
            bal0 = 0.0 if first_in else balance_at(rows, M.shift_month(start_month, -1))
            loans.append(dict(base, opening_balance=bal0, drawdown_amount=tot_draw, drawdown_year=(max(idx0, 0) // 12 + 1),
                              start_date=(fac["draw_month"] + "-01") if first_in else "", draw_frac=1.0, fee_pct=0.0, fee_amount=0.0, draws=dr))
            lab = ", ".join(f"{M.month_label(x['month'], False)} {x['draw']:,.0f}" for x in later)
            fee_in = sum(x["fee"] for x in later)
            notes.append(f"{name}: drawn in several tranches. {'Drawn before the forecast starts: ' + format(bal0, ',.2f') + ' is outstanding when it starts. ' if not first_in else ''}"
                         f"Drawdowns in the forecast: {lab or 'none'}"
                         + (f"; establishment fees of {fee_in:,.2f} are charged in the months drawn" if fee_in > 0 else "")
                         + ("; each tranche bears interest from its own date (part-month in the month drawn)." if any(x['draw_frac'] < 1 for x in later) else "."))
        elif idx0 >= 0:
            fee = fee_of(fac)
            fr = drawn[0]["draw_frac"] if drawn else first_frac(fac)
            loans.append(dict(base, opening_balance=0.0, drawdown_amount=fac["amount"], drawdown_year=idx0 // 12 + 1,
                              start_date=fac["draw_month"] + "-01", draw_frac=fr, fee_pct=0.0, fee_amount=fee))
            notes.append(f"{name}: drawn in {M.month_label(fac['draw_month'], False)} (a forecast month), so the establishment fee of {fee:,.2f} is charged then"
                         + (f", and its first month's interest is for the {fr:.0%} of the month it is outstanding (drawn {fac['draw_date']})." if fr < 1 and fac.get("draw_date") else
                          (f", and its first month's interest is for the {fr:.0%} of the month it is outstanding." if fr < 1 else ".")))
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
    # (the same goes for the later fields: a facility with one drawdown and a term/grace hashes as before; with several tranches or a first
    # repayment date, only the drawdowns that count and the dates enter the hash, so an empty extra line does not flag the forecast)
    def _fp(fac):
        out = {k: v for k, v in fac.items() if not ((k == "draw_date" and not v) or (k == "first_repay" and not v) or (k == "n_inst" and not v) or (k == "repays" and not v) or k == "draws")}
        if len(tranches(fac)) > 1:
            out["draws"] = [[t["date"], t["month"], t["amount"]] for t in tranches(fac)]
        if fac.get("repay") == "custom":
            out["repays"] = [[x["month"], x["amount"]] for x in repayments(fac)]       # only the repayments that count
        else:
            out.pop("repays", None)
        return out
    facs = [_fp(fac) for fac in facs]
    adj = adjust_of(d)
    if adj["inventory"] or adj["payables"]:
        facs = facs + [{"_adjust": adj}]       # changing the allocation of the opening gap also makes the forecast out of date
    return hashlib.md5(json.dumps(facs, sort_keys=True).encode()).hexdigest()[:12]


def parse_form(form, d, skip=None, skip_draw=None, skip_repay=None):
    """Facilities from the Loan facilities form: fields fac_<field>_<i>, count in n_fac. `skip` drops facility i; `skip_draw` = (i, j) drops drawdown line j of facility i."""
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
               "first_repay": form.get(f"fac_firstrepay_{i}"), "n_inst": form.get(f"fac_ninst_{i}"),
               "repay": form.get(f"fac_repay_{i}"), "include": bool(form.get(f"fac_inc_{i}")), "security": form.get(f"fac_sec_{i}"),
               "notes": form.get(f"fac_notes_{i}")}
        # drawdown lines (fac_dd_<i>_<j> date, fac_da_<i>_<j> amount, count fac_nd_<i>): lines left completely empty are dropped
        if form.get(f"fac_nd_{i}") is not None:
            try:
                nd = int(form.get(f"fac_nd_{i}") or 0)
            except ValueError:
                nd = 0
            lines = []
            for j in range(min(nd, MAX_DRAWS + 5)):
                if skip_draw is not None and skip_draw == (i, j):
                    continue
                dd, da = form.get(f"fac_dd_{i}_{j}"), form.get(f"fac_da_{i}_{j}")
                if not str(dd or "").strip() and f(da) <= 0:
                    continue
                lines.append({"date": dd, "amount": da})
            raw["draws"] = lines
            raw["amount"] = sum(max(0.0, f(x["amount"])) for x in lines)
        # repayment lines (fac_rn_<i> count, fac_rd_<i>_<j> date, fac_rp_<i>_<j> %, fac_ra_<i>_<j> amount)
        if form.get(f"fac_rn_{i}") is not None:
            try:
                nr = int(form.get(f"fac_rn_{i}") or 0)
            except ValueError:
                nr = 0
            reps = []
            for j in range(min(nr, MAX_REPAYS + 5)):
                if skip_repay is not None and skip_repay == (i, j):
                    continue
                rd, rp, ra = form.get(f"fac_rd_{i}_{j}"), form.get(f"fac_rp_{i}_{j}"), form.get(f"fac_ra_{i}_{j}")
                if not str(rd or "").strip() and f(rp) <= 0 and f(ra) <= 0:
                    continue
                reps.append({"date": rd, "pct": rp, "amount": ra})
            raw["repays"] = reps
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
    d = raw(d)
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


ADJ_LABEL = "Loan facilities not yet in the books (Dr retained earnings)"


def with_facilities(bal, d, facs=None):
    """The balance sheet rows with the loan facilities shown inside the table, under Borrowings: the facilities' balance (from their
    schedule), and how much of it falls due within 12 months / after. Where the books hold less borrowings than the facilities, the Borrowings
    line and the totals include the facilities, with the matching debit to retained earnings (see `_include_facilities_in_totals`), so the
    sheet balances. Returns `bal` unchanged when there are no facilities in use."""
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
    rows = []
    for r in bal["rows"]:
        rows.append(r)
        if r.get("key") == "borrowings":
            rows += extra
    rows = _include_facilities_in_totals(rows, balance, at)
    return dict(bal, rows=rows)


def _include_facilities_in_totals(rows, balance, at):
    """Where the books (the Actual and Last month columns, and the prior year end) hold less borrowings than the facilities' schedule, the
    entry is made in the statement: Dr Retained earnings, Cr Borrowings. Borrowings is raised to the facilities' balance (and so total
    liabilities and net debt), retained earnings and total equity are cut by the same amount, and the sheet still balances. The Forecast and
    Budget columns are left alone: the forecast already follows the facilities and its retained earnings already carry the cut."""
    by = {r["key"]: r for r in rows}
    if "borrowings" not in by:
        return rows
    short = {}
    for col in ("lm", "a", "py"):
        book = by["borrowings"].get(col)
        if book is None:
            continue
        sh = balance(at[col]) - book
        if sh > 0.5:
            short[col] = sh
    if not short:
        return rows
    sign = {"borrowings": 1, "liabilities": 1, "netdebt": 1, "retained": -1, "equity": -1}
    out = []
    for r in rows:
        k = r.get("key")
        if k in sign:
            r = dict(r)
            for col, sh in short.items():
                if r.get(col) is not None:
                    r[col] = r[col] + sign[k] * sh
            r["var"] = M._dv(r.get("a"), r.get("b"))
            r["chg_year"] = M._dv(r.get("a"), r.get("py"))
            r["chg_month"] = M._dv(r.get("a"), r.get("lm"))
        out.append(r)
    return out


def shown(d):
    """The pack as its statements show it: the loan facilities' interest and establishment fees added to the actual months' interest expense,
    and their balances added to Borrowings (with the matching debit to Retained earnings, as before), so the income statement, balance sheet,
    cash flow, KPIs, PDF and Excel all carry the loans and tie to each other. Cash is left as the books hold it.
    The result is a copy (the pack itself is never changed) that remembers the original as `_raw` and what it added as `_overlay`.
    Returns `d` itself when there is nothing to add: no facilities in use, the switch on the Loan facilities tab is off, or the books already hold them.
    A month whose balance sheet already holds at least the facilities' borrowings is taken to hold their interest too, and gets no extra interest."""
    if d.get("_overlay") is not None or d.get("_raw") is not None or not overlay_on(d):
        return d
    facs = [x for x in facilities_of(d) if x["include"] and x["amount"] > 0]
    if not facs:
        return d
    try:
        months = M.fy_months(d)
        raw_series = M.bs_series(d)
    except Exception:
        return d
    full = [schedule(x) for x in facs]

    def bal(m):
        return sum(balance_at(r, m) for r in full)

    cost_all = {m: {"int": sum(x["interest"] for r in full for x in r if x["month"] == m), "fee": sum(x["fee"] for r in full for x in r if x["month"] == m)}
                for m in months}
    entries = d.get("entries") or {}
    e2 = copy.deepcopy(entries)
    added, S = {}, {}
    run_i = run_f = 0.0
    for m in months:
        e = e2.get(m) or {}
        booked = False
        if e.get("bs") and m in raw_series and bal(m) > 0.5:
            booked = f(raw_series[m].get("borrowings")) >= bal(m) - 0.5
        a_i, a_f = (0.0, 0.0) if booked else (cost_all[m]["int"], cost_all[m]["fee"])
        added[m] = {"int": a_i, "fee": a_f}
        run_i += a_i
        run_f += a_f
        if e.get("pl") is not None and e.get("kind") in ("ytd", "month"):
            pl = e["pl"]
            extra = (run_i + run_f) if e.get("kind") == "ytd" else (a_i + a_f)
            pl["int_exp"] = f(pl.get("int_exp")) + extra
        if e.get("bs") and m in raw_series:
            book_b = f(raw_series[m].get("borrowings"))
            sh = bal(m) - book_b
            sh = sh if sh > 0.5 else 0.0
            S[m] = sh
            e["bs"]["borrowings"] = book_b + sh
            e["bs"]["retained"] = f(raw_series[m].get("retained")) - sh       # explicit, so later months do not roll the cut twice
    p2 = copy.deepcopy(d.get("prior")) if d.get("prior") else d.get("prior")
    s_py = 0.0
    pbs = (p2 or {}).get("bs")
    if pbs:
        sh = bal(M.shift_month(months[0], -1)) - f(pbs.get("borrowings"))
        if sh > 0.5:
            s_py = sh
            pbs["borrowings"] = f(pbs.get("borrowings")) + sh
            pbs["retained"] = f(pbs.get("retained")) - sh
    if not any(a["int"] or a["fee"] for a in added.values()) and not any(S.values()) and not s_py:
        return d
    d2 = dict(d)
    d2["entries"] = e2
    d2["prior"] = p2
    d2["_raw"] = d
    d2["_overlay"] = {"months": months, "cost_all": cost_all, "added": added, "S": S, "S_py": s_py, "gap_raw": unbooked_gap(d)}
    return d2


def income_with_facilities(inc, d):
    """The income statement with two lines under Interest expense showing how much of it is the loan facilities' interest and establishment
    fees (the amounts are already in Interest expense, Profit before tax and Profit after tax)."""
    info = d.get("_overlay")
    if not info or not inc or not inc.get("rows"):
        return inc
    V = inc["V"]
    months, idx = info["months"], V["idx"]
    added, cost_all = info["added"], info["cost_all"]

    def pick(key, m):
        return added.get(m, {}).get(key) if m in added else None

    def fy_value(key):
        if V["outlook"] is None:
            return None
        basis = V.get("outlook_basis") or ""
        if basis.startswith("Year-to-date actual plus the latest forecast"):
            return sum(cost_all[m][key] if m in months[V["n"]:] else added[m][key] for m in months)     # actual months as added, the rest as the forecast carries it
        if basis.startswith("Year-to-date actual annualised"):
            return sum(added[m][key] for m in months[:V["n"]]) * 12.0 / V["n"]
        return sum(added[m][key] for m in months[:V["n"]])

    def mk(key, label, k):
        def blk(a=None, f_=None):
            return {"a": a, "f": f_, "b": None, "var": None, "varpct": None, "p": None, "pvar": None, "pvarpct": None, "rag": "na"}
        lm = pick(k, months[idx - 1]) if idx > 0 and V["last_month"] is not None else None
        a = pick(k, months[idx]) if V["actual_month"] is not None else None
        f_ = cost_all[months[idx]][k] if V["forecast_month"] is not None else None
        y = sum(added[m][k] for m in months[:V["n"]]) if V["actual_ytd"] is not None else None
        fy = blk(None, fy_value(k))
        fy["f"] = fy_value(k)
        return {"key": key, "label": label, "kind": "memo", "income": False, "lm": lm, "month": blk(a, f_), "ytd": blk(y), "fy": fy}

    rows = []
    for r in inc["rows"]:
        rows.append(r)
        if r["key"] == "int_exp":
            rows.append(mk("fac_int", "of which: loan facility interest (per the schedules)", "int"))
            rows.append(mk("fac_fee", "of which: loan facility establishment fees", "fee"))
    return dict(inc, rows=rows)


def cashflow_with_facilities(cfl, d):
    """The cash flow rows with the loan facilities' effect that is not in the books' cash shown on its own non-cash line, so that the drawdowns,
    capital repaid and interest and fees paid are in the statement (Net loan drawdown, Net interest) and closing cash still agrees to the books.
    Without the loans-in view (see `shown`), only the forecast's opening gap is split out of 'Other equity movements', as before."""
    if not cfl or not cfl.get("rows"):
        return cfl
    info = d.get("_overlay")
    if not info:
        gap = unbooked_gap(d)
        offs = {"ffy": -gap} if gap else {}
    else:
        months, added, S = info["months"], info["added"], info["S"]
        idx = M.report_index(d)

        def s_of(m):
            return info["S_py"] if m < months[0] else S.get(m)

        def c_of(m):
            return added[m]["int"] + added[m]["fee"] if m in added else 0.0

        def ds(end, start):
            a, b = s_of(end), s_of(start)
            return None if (a is None or b is None) else a - b

        offs = {}
        if idx >= 1:
            e_, s_ = months[idx - 1], (months[idx - 2] if idx >= 2 else M.shift_month(months[0], -1))
            d_ = ds(e_, s_)
            offs["lm"] = None if d_ is None else -d_ + c_of(e_)
        d_ = ds(months[idx], months[idx - 1] if idx >= 1 else M.shift_month(months[0], -1))
        offs["a"] = None if d_ is None else -d_ + c_of(months[idx])
        d_ = ds(months[idx], M.shift_month(months[0], -1))
        offs["ya"] = None if d_ is None else -d_ + sum(c_of(m) for m in months[:idx + 1])
        offs["ffy"] = -info["gap_raw"] + sum(c_of(m) for m in months[:idx + 1])
    if not any(abs(v or 0.0) > 0.5 for v in offs.values()):
        return cfl
    label = "Loan facility movements not in the books (non-cash)" if info else ADJ_LABEL + ", non-cash"
    rows = []
    for r in cfl["rows"]:
        if r.get("key") == "other_equity":
            r = dict(r)
            if not info:
                # without the loans-in view the gap is only split out of the full-year forecast column, as before
                if r.get("ffy") is not None and offs.get("ffy"):
                    r["ffy"] = r["ffy"] - offs["ffy"]
            else:
                for col, v in offs.items():
                    if v is not None and r.get(col) is not None:
                        r[col] = r[col] - v
                r["var"] = M._dv(r.get("a"), r.get("b"))
                r["yvar"] = M._dv(r.get("ya"), r.get("yb"))
            rows.append(r)
            new = {"key": "fac_gap", "label": label, "kind": "indent", "lm": None, "a": None, "f": None, "b": None, "ya": None, "yb": None,
                   "ffy": None, "bfy": None, "var": None, "yvar": None}
            for col, v in offs.items():
                if v is not None and r.get(col) is not None:
                    new[col] = v
            rows.append(new)
            continue
        rows.append(r)
    return dict(cfl, rows=rows)


def statement_note(d, tab):
    """One sentence under a statement saying how the loan facilities are in it (None when the loans-in view is not on)."""
    info = d.get("_overlay")
    if not info:
        return None
    names = ", ".join(label_of(x) for x in facilities_of(d) if x["include"] and x["amount"] > 0)
    ytd = sum(a["int"] + a["fee"] for a in info["added"].values())
    if tab == "income":
        return (f"Loan facilities ({names}): their interest and establishment fees per the Loan facilities schedules ({ytd:,.0f} for the actual months of the year) are included in "
                "Interest expense, Profit before tax and Profit after tax, because the books do not hold them (the lines under Interest expense show the part that is the facilities). "
                "Income tax is as the books have it: no tax effect has been taken on this extra cost.")
    if tab == "balance":
        return (f"Loan facilities ({names}): their balance per the schedules is in Borrowings, with the matching debit in Retained earnings where the books hold less. "
                "Cash is as the books hold it.")
    if tab == "cashflow":
        return (f"Loan facilities ({names}): drawdowns and capital repaid are in Net loan drawdown / (repayment) and the interest and fees paid are in Net interest. "
                "The bank movements are not in the books' cash, so the line 'Loan facility movements not in the books (non-cash)' offsets them and closing cash still agrees to the balance sheet.")
    return None


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
    if d.get("_overlay"):
        note_i = ("The facilities' interest and fees are included in the statement's interest expense (the books did not hold them), so the balance is the interest "
                  "the books hold for other borrowings and charges. A negative balance for a forecast month means the forecast is not linked to these facilities "
                  "(use 'Save and update the forecast' on the Loan facilities tab).")
    else:
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
    rows.append(row("Borrowings per the books / forecast", bs_b, "line"))
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
