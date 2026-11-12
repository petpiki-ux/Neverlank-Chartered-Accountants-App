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


def _months_between(a, b):
    """Whole months from month a to month b (negative when b is earlier)."""
    return (int(b[:4]) - int(a[:4])) * 12 + (int(b[5:]) - int(a[5:]))


def facility_id(i):
    return f"fac{i + 1}"


def blank_facility(draw_month, i=0):
    return {"id": facility_id(i), "lender": "New facility", "amount": 0.0, "rate": 0.0, "fee_pct": 0.0, "fee_fixed": 0.0, "draw_month": draw_month,
            "term_months": DEFAULT_TERM, "grace_months": 0, "repay": "level", "include": True, "security": "", "notes": ""}


def normalise_facility(raw, i, default_month):
    raw = raw or {}
    repay = raw.get("repay") if raw.get("repay") in REPAY_SHORT else "level"
    term = int(max(1, min(MAX_TERM, f(raw.get("term_months"), DEFAULT_TERM))))
    grace = int(max(0, min(term - 1, f(raw.get("grace_months"), 0))))
    inc = raw.get("include")
    return {
        "id": str(raw.get("id") or facility_id(i))[:24], "lender": str(raw.get("lender") or "").strip()[:60],
        "amount": max(0.0, f(raw.get("amount"))), "rate": max(0.0, min(200.0, f(raw.get("rate")))),
        "fee_pct": max(0.0, min(100.0, f(raw.get("fee_pct")))), "fee_fixed": max(0.0, f(raw.get("fee_fixed"))),
        "draw_month": _month(raw.get("draw_month"), default_month), "term_months": term, "grace_months": grace, "repay": repay,
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
        interest = base * r
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
                              start_date=fac["draw_month"] + "-01", fee_pct=0.0, fee_amount=fee))
            notes.append(f"{name}: drawn in {M.month_label(fac['draw_month'], False)} (a forecast month), so the establishment fee of {fee:,.2f} is charged then.")
        else:
            bal = balance_at(rows, M.shift_month(start_month, -1))
            loans.append(dict(base, opening_balance=bal, drawdown_amount=0.0, drawdown_year=1, start_date="", fee_pct=0.0, fee_amount=0.0))
            fee = fee_of(fac)
            notes.append(f"{name}: already drawn ({M.month_label(fac['draw_month'], False)}); {bal:,.2f} is outstanding when the forecast starts"
                         + (f" and its establishment fee of {fee:,.2f} was charged when it was drawn, so the forecast does not charge it again." if fee > 0 else "."))
    return loans, notes


def fingerprint(d):
    """Changes whenever the facilities (or whether they are in use) change; stored on the forecast link to flag a forecast that is out of date."""
    facs = facilities_of(d)
    if not facs:
        return ""
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
               "draw_month": form.get(f"fac_draw_{i}"), "term_months": form.get(f"fac_term_{i}"), "grace_months": form.get(f"fac_grace_{i}"),
               "repay": form.get(f"fac_repay_{i}"), "include": bool(form.get(f"fac_inc_{i}")), "security": form.get(f"fac_sec_{i}"),
               "notes": form.get(f"fac_notes_{i}")}
        if not (str(raw["lender"] or "").strip() or f(raw["amount"]) > 0):
            continue
        out.append(normalise_facility(raw, len(out), default_month))
    return out[:MAX_FACILITIES]
