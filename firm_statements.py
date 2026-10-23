"""The firm's own financial statements (Finance > Firm Finance).

Builds an Income Statement, Statement of Financial Position (balance sheet),
Statement of Changes in Equity, Cash Flow Statement and Notes for Neverlank
itself, from:

* the app's own records, on an ACCRUAL basis -
    revenue          = invoices issued (Sent/Paid) by invoice date, excl. VAT,
                       less credit notes dated in the period;
    expenses         = the Expense register by bill date (paid or not), by category;
    receivables      = what clients still owe at the year-end (incl. VAT, after
                       receipts and credit notes dated on or before it);
    payables         = Expense-register bills not yet paid at the year-end;
    customer advances= money received before invoicing and still unapplied
                       (a liability, never revenue);
* the firm's Asset Register (FirmAsset / FirmAssetClass): cost, depreciation
  calculated month by month, disposals and the profit or loss on them;
* the figures entered for each year-end (FirmYearEnd / FirmBalanceLine): bank
  and cash, loans, share capital, VAT payable, tax, dividends, opening retained
  earnings and anything else the app cannot know.

Everything is in USD. A ZWG item (an invoice, a bill or an entered figure) is
translated at the ZWG-per-USD rate entered on that year-end, and the rate is
disclosed in the notes. If ZWG items exist but no rate was entered they are
LEFT OUT and a warning says so - they are never silently added at 1:1.

The statements reuse the audit engine in financials.py (income statement,
equity roll-forward, statement of financial position) so the firm's own
accounts are laid out exactly like a client's. The cash flow is built here
instead because the firm has a register of its own assets: depreciation is
added back and purchases/disposals of assets are shown gross.

Nothing here hides a problem: an unbalanced balance sheet, a cash flow that
does not agree to cash, an opening retained earnings that differs from the
previous closing, and excluded ZWG items are each reported as a check.
"""
import calendar
from collections import defaultdict
from datetime import date, timedelta

import analytics
import cashbook
import firm_tax
import financials
from financials import (
    _row, _is_nil, _filter_nil_rows, _infer_period_start, _attach_note_numbers,
    build_income_statement, build_financial_position, category_label,
)
from models import (
    FirmAsset, FirmAssetClass, FirmYearEnd, INVOICE_CURRENCIES, FIRM_FRAMEWORKS,
)

FIRM_NAME = "Neverlank Chartered Accountants"
ALL_CODES = [c[0] for c in financials.FS_CATEGORIES if c[0] != "excluded"]
UNCLASSIFIED = "Unclassified"


def r2(v):
    return round(float(v or 0.0), 2)


# ====================================================================== depreciation

def _midx(d):
    return d.year * 12 + d.month - 1


def annual_rate(cls):
    """Annual depreciation rate as a fraction (0.3333 for 33.33%). The rate wins over the useful life; no rate and no
    life, or the 'Not depreciated' method (land, work in progress), means no depreciation."""
    if not cls or cls.get("depreciation_method") == "Not depreciated":
        return 0.0
    if cls.get("rate_percent") is not None:
        return max(cls["rate_percent"], 0.0) / 100.0
    life = cls.get("useful_life_years")
    return 1.0 / life if life and life > 0 else 0.0


def _blank_movement():
    return {
        "opening_cost": 0.0, "additions": 0.0, "disposals_cost": 0.0, "closing_cost": 0.0,
        "opening_acc_dep": 0.0, "charge": 0.0, "disposals_acc_dep": 0.0, "closing_acc_dep": 0.0,
        "opening_nbv": 0.0, "closing_nbv": 0.0, "proceeds": 0.0, "gain_loss": 0.0,
    }


def asset_movement(asset, cls, period_start, period_end):
    """What happened to one asset between period_start and period_end (inclusive), as a dict shaped like a row of
    financials.build_ppe_movement_schedule plus proceeds and gain_loss.

    Depreciation is monthly: a month is charged if the asset was owned for it - the month of acquisition is charged,
    the month of disposal is not. Straight-line charges (cost - residual) x rate / 12 a month; reducing balance charges
    (net book value at the start of the financial year, or cost for an asset bought in the year) x rate / 12 a month.
    The charge never takes the net book value below the residual value. If an accumulated depreciation figure from the
    old books is entered with its date, it replaces the calculated figure from the end of that month onward."""
    out = dict(_blank_movement(), in_scope=False, disposed_in_period=False)
    cost = float(asset.get("cost") or 0.0)
    residual = min(float(asset.get("residual_value") or 0.0), cost)
    acq, disp = asset["date_acquired"], asset.get("disposed_on")
    S, E, A = _midx(period_start), _midx(period_end), _midx(acq)
    D = _midx(disp) if disp else None
    if A > E or (D is not None and D < S):
        return out
    out["in_scope"] = True

    r = annual_rate(cls)
    fy_start_month = period_end.month % 12 + 1
    bf, bf_date = asset.get("acc_dep_bf"), asset.get("acc_dep_bf_date")
    B = _midx(bf_date) if (bf is not None and bf_date is not None and _midx(bf_date) >= A) else None
    method_rb = (cls or {}).get("depreciation_method") == "Reducing balance"
    depreciable = max(cost - residual, 0.0)

    acc_after = {}
    cur, base = 0.0, cost
    last = E if D is None else min(E, D - 1)
    for m in range(A, last + 1):
        if m != A and (m % 12 + 1) == fy_start_month:
            base = cost - cur
        monthly = (base * r / 12.0) if method_rb else (depreciable * r / 12.0)
        cur += max(min(monthly, depreciable - cur), 0.0)
        if B is not None and m == B:
            cur = min(max(float(bf), 0.0), cost)
            base = cost - cur
        acc_after[m] = cur
    frozen = cur

    def acc_at(m):
        if m < A:
            return 0.0
        return acc_after.get(m, frozen)

    open_acc = acc_at(S - 1)
    close_acc = acc_at(E)
    is_addition = S <= A <= E
    disposed = D is not None and S <= D <= E
    out["disposed_in_period"] = disposed
    out["opening_cost"] = 0.0 if is_addition else cost
    out["additions"] = cost if is_addition else 0.0
    out["opening_acc_dep"] = 0.0 if is_addition else open_acc
    out["charge"] = close_acc - (0.0 if is_addition else open_acc)
    if disposed:
        out["disposals_cost"] = cost
        out["disposals_acc_dep"] = close_acc
        proceeds = float(asset.get("disposal_proceeds") or 0.0)
        out["proceeds"] = proceeds
        out["gain_loss"] = proceeds - (cost - close_acc)
    out["closing_cost"] = cost - out["disposals_cost"]
    out["closing_acc_dep"] = out["opening_acc_dep"] + out["charge"] - out["disposals_acc_dep"]
    out["opening_nbv"] = out["opening_cost"] - out["opening_acc_dep"]
    out["closing_nbv"] = out["closing_cost"] - out["closing_acc_dep"]
    return out


def register_schedule(assets, classes, period_start, period_end):
    """The movement schedule for the whole register: per class and in total, plus one row per asset.
    assets / classes are plain dicts (see load_register)."""
    class_by_id = {c["id"]: c for c in classes}
    order = {c["id"]: i for i, c in enumerate(classes)}
    by_class, total, rows = {}, _blank_movement(), []
    for a in assets:
        cls = class_by_id.get(a.get("asset_class_id"))
        mv = asset_movement(a, cls, period_start, period_end)
        if not mv["in_scope"]:
            continue
        cid = a.get("asset_class_id")
        row = by_class.setdefault(cid, _blank_movement())
        for k in _blank_movement():
            row[k] += mv[k]
            total[k] += mv[k]
        rows.append(dict(a, **{k: mv[k] for k in _blank_movement()}, disposed_in_period=mv["disposed_in_period"],
                         class_name=(cls or {}).get("name", UNCLASSIFIED)))
    classes_out = []
    for cid, row in by_class.items():
        name = (class_by_id.get(cid) or {}).get("name", UNCLASSIFIED)
        classes_out.append(dict(row, class_id=cid, class_name=name))
    classes_out.sort(key=lambda r: (order.get(r["class_id"], len(order)), r["class_name"]))
    return {"by_class": classes_out, "total": total, "assets": rows}


def load_register():
    """(classes, assets) as plain dicts, classes in display order."""
    classes = [{
        "id": c.id, "name": c.name, "depreciation_method": c.depreciation_method,
        "rate_percent": c.rate_percent, "useful_life_years": c.useful_life_years, "is_active": c.is_active,
    } for c in FirmAssetClass.query.order_by(FirmAssetClass.order, FirmAssetClass.name).all()]
    assets = [{
        "id": a.id, "asset_code": a.asset_code, "description": a.description, "asset_class_id": a.asset_class_id,
        "date_acquired": a.date_acquired, "cost": a.cost or 0.0, "residual_value": a.residual_value or 0.0,
        "supplier": a.supplier, "location": a.location, "serial_number": a.serial_number,
        "acc_dep_bf": a.acc_dep_bf, "acc_dep_bf_date": a.acc_dep_bf_date,
        "disposed_on": a.disposed_on, "disposal_proceeds": a.disposal_proceeds or 0.0,
    } for a in FirmAsset.query.order_by(FirmAsset.date_acquired, FirmAsset.id).all()]
    return classes, assets


# ====================================================================== what the app already knows

def derive_period(period_start, period_end, zwg_rate):
    """Revenue, expenses, receivables, payables and advances for one period, in USD."""
    out = {
        "revenue": 0.0, "revenue_by_service": defaultdict(float), "expenses": defaultdict(float),
        "receivables": 0.0, "payables": 0.0, "advances": 0.0, "supplier_advances": 0.0, "vat_in_receivables": 0.0, "vat_charged": 0.0,
        "ageing": [0.0] * len(analytics.AGEING_BUCKETS), "warnings": [], "zwg_rate": zwg_rate,
    }
    for cur in INVOICE_CURRENCIES:
        inv = analytics._invoice_rows(cur)
        exp = analytics._expense_rows(cur)
        revenue = analytics._sum(inv, "net", period_start, period_end, "issue")
        owed_rows = analytics.outstanding_at(inv, period_end)
        unpaid = [dict(e, amount=analytics.exp_owed_on(e, period_end)) for e in exp if analytics.exp_owed_on(e, period_end) > 0]
        spend = [e for e in exp if period_start <= e["date"] <= period_end]
        advances = analytics.advances_held(cur, period_end)
        supp_adv = analytics.supplier_advances_held(cur, period_end)
        activity = any(abs(x) > 0.004 for x in (
            revenue, advances, supp_adv, sum(r["owed"] for r in owed_rows), sum(e["amount"] for e in unpaid),
            sum(e["amount"] for e in spend)))
        if cur == "USD":
            f = 1.0
        elif zwg_rate and zwg_rate > 0:
            f = 1.0 / zwg_rate
        else:
            f = None
            if activity:
                out["warnings"].append(
                    f"There are {cur} invoices, bills or advances in this period but no {cur} per USD rate has been "
                    f"entered for this year-end, so every {cur} amount was left out. Enter the rate on the year-end page.")
            continue
        out["revenue"] += revenue * f
        for r in inv:
            if period_start <= r["issue"] <= period_end:
                out["revenue_by_service"][r["service"]] += r["net"] * f
            for c in r["credits"]:
                if period_start <= c[0] <= period_end:
                    out["revenue_by_service"][r["service"]] -= c[1] * f
        out["vat_charged"] += (analytics._sum(inv, "gross", period_start, period_end, "issue") - revenue) * f
        out["receivables"] += sum(r["owed"] for r in owed_rows) * f
        out["vat_in_receivables"] += sum(r["owed"] * (1 - (r["net"] / r["gross"] if r["gross"] else 1)) for r in owed_rows) * f
        for e in spend:
            out["expenses"][e["category"]] += e["amount"] * f
        out["payables"] += sum(e["amount"] for e in unpaid) * f
        out["advances"] += advances * f
        out["supplier_advances"] += supp_adv * f
        for i, b in enumerate(analytics.ageing(inv, period_end)):
            out["ageing"][i] += b["amount"] * f
    return out


# ====================================================================== one year-end column

def period_start_for(period_end, prev_end):
    return prev_end + timedelta(days=1) if prev_end else _infer_period_start(period_end)


def period_label(period_start, period_end):
    days = (period_end - period_start).days + 1
    if 360 <= days <= 371:
        return f"Year ended {period_end.strftime('%d %B %Y')}"
    return f"Period {period_start.strftime('%d %B %Y')} to {period_end.strftime('%d %B %Y')}"


def column_profit(t):
    """(profit before tax, profit for the year) from a {code: amount} dict (natural, positive balances)."""
    pbt = (t["revenue"] - t["cost_of_sales"] + t["other_income"] - t["distribution_costs"] - t["admin_expenses"]
           - t["depreciation_amortisation"] - t["other_expenses"] - t["finance_costs"])
    return pbt, pbt - t["income_tax_expense"]


def entered_lines(ye, from_book, tax_auto):
    """The figures entered on a year-end that reach the statements: [(fs_category, description, usd)], warnings.
    Cash lines are skipped when cash comes from the Cash Book, tax lines when tax comes from the Tax pages, and ZWG lines
    when the year-end has no rate. The ledger (firm_ledger) uses this too, so both always agree."""
    items, warnings = [], []
    rate = ye.zwg_rate
    for ln in ye.lines:
        if from_book and ln.fs_category == "cash":
            continue
        if tax_auto and ln.fs_category in firm_tax.TAX_CODES:
            continue
        if ln.currency == "USD" or not ln.currency:
            usd = ln.amount or 0.0
        elif rate and rate > 0:
            usd = (ln.amount or 0.0) / rate
        else:
            warnings.append(f"'{ln.description}' is in {ln.currency} but no rate is entered, so it was left out.")
            continue
        name = ln.description if ln.currency in (None, "USD") else f"{ln.description} ({ln.currency} {ln.amount:,.2f} at {rate:g})"
        items.append((ln.fs_category, name, usd))
    return items, warnings


def build_column(ye, prev_end, classes, assets, prev_col=None):
    """One year-end's accounts. `ye` is a FirmYearEnd. Returns {"accounts": {code: [{name, amount}]}, "totals":
    {code: amount}, ...}. Retained earnings brought forward is only filled here if it was entered; the chain builder
    carries it forward otherwise."""
    start = period_start_for(ye.period_end, prev_end)
    end = ye.period_end
    rate = ye.zwg_rate
    d = derive_period(start, end, rate)
    sched = register_schedule(assets, classes, start, end)
    warnings = list(d["warnings"])
    acc = {code: [] for code in ALL_CODES}

    def add(code, name, amount):
        amount = r2(amount)
        if abs(amount) >= 0.005:
            acc[code].append({"name": name, "amount": amount})

    for service, amt in sorted(d["revenue_by_service"].items(), key=lambda kv: -kv[1]):
        add("revenue", f"Professional fees - {service}", amt)
    for cat, amt in sorted(d["expenses"].items(), key=lambda kv: (-kv[1], kv[0])):
        add("admin_expenses", cat, amt)
    for row in sched["by_class"]:
        add("depreciation_amortisation", f"Depreciation - {row['class_name']}", row["charge"])
        add("ppe", f"{row['class_name']} (net book value)", row["closing_nbv"])
    gain = sched["total"]["gain_loss"]
    if gain > 0:
        add("other_income", "Profit on disposal of property, plant and equipment", gain)
    elif gain < 0:
        add("other_expenses", "Loss on disposal of property, plant and equipment", -gain)
    add("trade_receivables", "Fees receivable from clients (unpaid invoices, including VAT)", d["receivables"])
    add("trade_payables", "Supplier bills not yet paid", d["payables"])
    add("other_current_liabilities", "Customer advances (received before invoicing)", d["advances"])
    add("other_current_assets", "Advances paid to suppliers (before their bills)", d["supplier_advances"])

    # Cash and bank come from the Cash Book automatically once it has an account running at the year-end; only a year-end
    # that predates the Cash Book (or one switched to manual) uses the cash figures entered on the year-end page.
    from_book = cashbook.uses_cash_book(ye, end)
    if from_book:
        cb = cashbook.statement_balances(end, rate)
        for item in cb["items"]:
            add("cash", item["name"], item["usd"])
        warnings.extend(cb["warnings"])
        ignored = [ln for ln in ye.lines if ln.fs_category == "cash"]
        if ignored:
            warnings.append(f"Cash is taken from the Cash Book, so the {len(ignored)} cash figure(s) entered on this year-end were ignored. "
                            "Remove them, or switch this year-end to manual cash in its settings, to avoid confusion.")

    # Income tax, tax payable and deferred tax come from the Tax pages unless this year-end is switched to manual tax
    tax_auto = not getattr(ye, "tax_manual", False)
    if tax_auto:
        ignored_tax = [ln for ln in ye.lines if ln.fs_category in firm_tax.TAX_CODES]
        if ignored_tax:
            warnings.append(f"Tax is taken from the Tax pages, so the {len(ignored_tax)} tax figure(s) entered on this year-end were ignored. "
                            "Remove them, or switch this year-end to manual tax in its settings, to avoid confusion.")

    re_entered = False
    items, w_entered = entered_lines(ye, from_book, tax_auto)
    warnings.extend(w_entered)
    for code, name, usd in items:
        if code == "retained_earnings":
            re_entered = True
        add(code, name, usd)

    # Posted manual journals (Firm Finance > Ledger) add to the statements through the accounts' statement lines
    import firm_ledger
    j_items, w_j = firm_ledger.journal_statement_items(ye, start, end, first=prev_end is None, from_book=from_book, tax_auto=tax_auto)
    warnings.extend(w_j)
    for code, name, amount in j_items:
        add(code, name, amount)

    tax = None
    if tax_auto:
        t_pre = {code: sum(x["amount"] for x in acc[code]) for code in ALL_CODES}
        tax = firm_tax.column_tax(ye, start, end, column_profit(t_pre)[0], d, sched, classes, assets,
                                  (prev_col or {}).get("tax"))
        warnings.extend(tax["warnings"])
        if abs(tax["current_tax"]) >= 0.005:
            add("income_tax_expense", "Current tax (see Tax computation)", tax["current_tax"])
        if abs(tax["deferred_expense"]) >= 0.005:
            add("income_tax_expense", "Deferred tax (see Deferred tax)", tax["deferred_expense"])
        if tax["payable"] >= 0.005:
            add("current_tax_payable", "Income tax payable (tax for the year and earlier, less payments made)", tax["payable"])
        elif tax["payable"] <= -0.005:
            add("other_current_assets", "Income tax prepaid (payments made exceed the tax due)", -tax["payable"])
        if tax["deferred_closing"] >= 0.005:
            add("deferred_tax_liability", "Deferred tax on temporary differences", tax["deferred_closing"])
        elif tax["deferred_closing"] <= -0.005:
            add("deferred_tax_asset", "Deferred tax on temporary differences", -tax["deferred_closing"])

    totals = {code: r2(sum(x["amount"] for x in acc[code])) for code in ALL_CODES}
    return {
        "tax": tax,
        "ye": ye, "ye_id": ye.id, "period_start": start, "period_end": end, "label": period_label(start, end),
        "accounts": acc, "totals": totals, "schedule": sched, "derived": d, "warnings": warnings,
        "re_entered": re_entered, "re_carried": False,
    }


def column_closing_re(col):
    t = col["totals"]
    return r2(t["retained_earnings"] + column_profit(t)[1] - t["dividends_paid"])


def build_chain(year_ends, classes, assets):
    """Columns for every year-end in date order. A year-end with no retained earnings entered picks up the previous
    year-end's closing retained earnings (opening_nil year-ends and the very first one start from nil)."""
    cols = []
    prev_end = None
    for ye in sorted(year_ends, key=lambda y: y.period_end):
        col = build_column(ye, prev_end, classes, assets, cols[-1] if cols else None)
        if not col["re_entered"]:
            if cols:
                carry = column_closing_re(cols[-1])
                col["accounts"]["retained_earnings"].append({"name": "Brought forward from the previous year-end", "amount": carry})
                col["totals"]["retained_earnings"] = carry
                col["re_carried"] = True
            elif not ye.opening_nil:
                col["warnings"].append(
                    "No retained earnings brought forward is entered and there is no earlier year-end, so it is treated as nil. "
                    "Enter it on the year-end page (or tick 'first period').")
        cols.append(col)
        prev_end = ye.period_end
    return cols


# ====================================================================== statements

def _totals_pair(cur, pri):
    """{code: {"current", "prior"}} for the audit engine. pri None = no comparative."""
    out = {}
    for code in ALL_CODES:
        out[code] = {"current": cur["totals"][code], "prior": (pri["totals"][code] if pri else 0.0)}
    if pri is None:
        # no comparative year: keep the equity roll-forward honest (no invented movement in capital or reserves)
        for code in ("share_capital", "share_premium", "other_reserves"):
            out[code]["prior"] = out[code]["current"]
    return out


def build_soce(t, opening, re_bf, heading):
    """Statement of changes in equity for one period.
    t = {code: amount} of that period; opening = {code: amount} closing balances of the previous period (share capital,
    premium, reserves) or None when unknown; re_bf = retained earnings brought forward used for the period."""
    pbt, profit = column_profit(t)
    o = opening or {c: t[c] for c in ("share_capital", "share_premium", "other_reserves")}
    cols = ["share_capital", "share_premium", "other_reserves", "retained_earnings"]
    rows = []
    start = {"share_capital": o["share_capital"], "share_premium": o["share_premium"],
             "other_reserves": o["other_reserves"], "retained_earnings": re_bf}
    rows.append(("Balance at the start of the period", dict(start), True))
    rows.append(("Profit for the period", {"retained_earnings": profit}, False))
    if abs(t["oci_items"]) >= 0.005:
        rows.append(("Other comprehensive income", {"other_reserves": t["oci_items"]}, False))
    issued = {c: t[c] - o[c] for c in ("share_capital", "share_premium")}
    if any(abs(v) >= 0.005 for v in issued.values()):
        rows.append(("Shares issued", issued, False))
    res_move = t["other_reserves"] - o["other_reserves"] - t["oci_items"]
    if abs(res_move) >= 0.005:
        rows.append(("Other movements in reserves", {"other_reserves": res_move}, False))
    if abs(t["dividends_paid"]) >= 0.005:
        rows.append(("Dividends", {"retained_earnings": -t["dividends_paid"]}, False))
    closing = {c: 0.0 for c in cols}
    for _l, vals, _b in rows:
        for c, v in vals.items():
            closing[c] += v
    rows.append(("Balance at the end of the period", closing, True))
    out_rows = []
    for label, vals, bold in rows:
        full = {c: vals.get(c, 0.0) for c in cols}
        out_rows.append({"label": label, "values": full, "total": sum(full.values()), "bold": bold})
    return {"heading": heading, "columns": [(c, category_label(c).replace(" (opening balance b/f)", "")) for c in cols],
            "rows": out_rows, "closing": closing}


def build_firm_cash_flow(t, pl, sched):
    """Indirect cash flow for the firm. t = {code: {"current", "prior"}}. Depreciation is added back and asset purchases
    and sale proceeds are shown gross, from the Asset Register schedule."""
    def mv(code):
        return t[code]["current"] - t[code]["prior"]

    pbt = pl["profit_before_tax"]["current"]
    dep = t["depreciation_amortisation"]["current"]
    gain = sched["total"]["gain_loss"]
    fin = t["finance_costs"]["current"]
    dt_move = mv("deferred_tax_liability") - mv("deferred_tax_asset")
    prov = mv("long_term_provisions")
    adj = [
        _row("Profit before tax", pbt),
        _row("Depreciation of property, plant and equipment", dep),
        _row("(Profit)/loss on disposal of property, plant and equipment", -gain),
        _row("Finance costs", fin),
        _row("Movement in long-term provisions", prov),
        _row("Movement in deferred tax", dt_move),
    ]
    before_wc = pbt + dep - gain + fin + prov + dt_move
    wc = [
        _row("(Increase)/decrease in inventories", -mv("inventories")),
        _row("(Increase)/decrease in fees receivable and other receivables", -mv("trade_receivables")),
        _row("(Increase)/decrease in other current assets", -mv("other_current_assets")),
        _row("Increase/(decrease) in payables and accruals", mv("trade_payables")),
        _row("Increase/(decrease) in short-term provisions", mv("short_term_provisions")),
        _row("Increase/(decrease) in other current liabilities (VAT, advances)", mv("other_current_liabilities")),
    ]
    generated = before_wc + sum(r["current"] for r in wc)
    fin_paid = -fin
    tax_paid = -(t["current_tax_payable"]["prior"] + t["income_tax_expense"]["current"] - t["current_tax_payable"]["current"])
    operating = (adj + [_row("Operating cash flow before working capital changes", before_wc, bold=True)] + wc
                 + [_row("Cash generated from operations", generated, bold=True),
                    _row("Finance costs paid", fin_paid), _row("Income tax paid", tax_paid)])
    net_operating = generated + fin_paid + tax_paid

    tot = sched["total"]
    inv_rows = [
        _row("Purchase of property, plant and equipment", -tot["additions"], category="ppe"),
        _row("Proceeds from disposal of property, plant and equipment", tot["proceeds"], category="ppe"),
    ]
    for code in ("intangible_assets", "investment_property", "long_term_investments", "other_noncurrent_assets"):
        inv_rows.append(_row(f"Net movement in {category_label(code).lower()}", -mv(code), category=code))
    net_investing = sum(r["current"] for r in inv_rows)

    shares = mv("share_capital") + mv("share_premium")
    borrow = mv("long_term_borrowings") + mv("short_term_borrowings")
    other_nc = mv("other_noncurrent_liabilities")
    reserves = mv("other_reserves") - t["oci_items"]["current"]
    divs = -t["dividends_paid"]["current"]
    fin_rows = [
        _row("Proceeds from shares issued", shares, category=["share_capital", "share_premium"]),
        _row("Proceeds from/(repayment of) borrowings", borrow, category=["long_term_borrowings", "short_term_borrowings"]),
        _row("Movement in other non-current liabilities", other_nc, category="other_noncurrent_liabilities"),
        _row("Movement in other reserves", reserves, category="other_reserves"),
        _row("Dividends paid", divs, category="dividends_paid"),
    ]
    net_financing = sum(r["current"] for r in fin_rows)

    net = net_operating + net_investing + net_financing
    open_cash = t["cash"]["prior"]
    computed = open_cash + net
    actual = t["cash"]["current"]
    return {
        "method": "indirect",
        "operating_rows": operating, "net_operating": net_operating,
        "investing_rows": _filter_nil_rows(inv_rows), "net_investing": net_investing,
        "financing_rows": _filter_nil_rows(fin_rows), "net_financing": net_financing,
        "net_movement": net, "cash_open": open_cash, "cash_close_computed": computed,
        "cash_close_actual": actual, "variance": actual - computed,
    }


# ---------------------------------------------------------------------- notes

NOTE_ORDER = [
    (["ppe"], "Property, plant and equipment"),
    (["intangible_assets"], "Intangible assets"),
    (["investment_property"], "Investment property"),
    (["long_term_investments"], "Long-term investments"),
    (["deferred_tax_asset"], "Deferred tax asset"),
    (["other_noncurrent_assets"], "Other non-current assets"),
    (["inventories"], "Inventories"),
    (["trade_receivables"], "Fees receivable and other receivables"),
    (["other_current_assets"], "Other current assets"),
    (["cash"], "Cash and cash equivalents"),
    (["share_capital", "share_premium"], "Share capital and share premium"),
    (["other_reserves"], "Other reserves"),
    (["long_term_borrowings"], "Long-term borrowings"),
    (["deferred_tax_liability"], "Deferred tax liability"),
    (["long_term_provisions"], "Long-term provisions"),
    (["other_noncurrent_liabilities"], "Other non-current liabilities"),
    (["trade_payables"], "Payables and accruals"),
    (["short_term_borrowings"], "Short-term borrowings"),
    (["current_tax_payable"], "Income tax payable"),
    (["short_term_provisions"], "Short-term provisions"),
    (["other_current_liabilities"], "Other current liabilities"),
    (["revenue"], "Revenue"),
    (["other_income"], "Other income"),
    (["admin_expenses", "depreciation_amortisation"], "Administrative expenses"),
    (["other_expenses"], "Other expenses"),
    (["finance_costs"], "Finance costs"),
    (["income_tax_expense"], "Income tax expense"),
    (["dividends_paid"], "Dividends"),
]


def _merge_accounts(cur, pri, codes):
    merged = {}
    for code in codes:
        for a in cur["accounts"][code]:
            merged.setdefault(a["name"], [0.0, 0.0])[0] += a["amount"]
        if pri:
            for a in pri["accounts"][code]:
                merged.setdefault(a["name"], [0.0, 0.0])[1] += a["amount"]
    rows = [{"name": n, "current": r2(v[0]), "prior": r2(v[1])} for n, v in merged.items()]
    return [r for r in rows if not (_is_nil(r["current"]) and _is_nil(r["prior"]))]


def build_notes(ye, cur, pri, totals, pl, classes, framework_label):
    """Numbered notes. Returns (notes, note_number_by_category)."""
    rate = ye.zwg_rate
    zwg_text = (f"Amounts in Zimbabwe Gold (ZWG) are translated into US dollars at ZWG {rate:g} to USD 1, the rate entered for this period, "
                "for revenue, expenses and year-end balances alike." if rate else
                "No ZWG rate has been entered for this period; any ZWG amount is excluded from these statements.")
    class_dicts = [c for c in classes if c.get("is_active", True) or any(r["class_name"] == c["name"] for r in cur["schedule"]["by_class"])]
    ppe_policy = financials.ppe_accounting_policy_text(class_dicts) or (
        "Property, plant and equipment are stated at cost less accumulated depreciation.")
    policies = [
        ("Revenue", "Revenue is the fees invoiced to clients for professional services, excluding value added tax, after deducting credit "
                    "notes, and is recognised in the period in which the invoice is issued. Money received from a client before an invoice is "
                    "raised is a customer advance and is a liability until it is applied to an invoice."),
        ("Expenses", "Expenses are recognised in the period of the supplier's bill, whether or not it has been paid (accrual basis). "
                     "Value added tax recoverable from the tax authority is not treated as an expense."),
        ("Property, plant and equipment", ppe_policy + " Depreciation is calculated monthly from the month of acquisition to, but not "
                                                       "including, the month of disposal. A profit or loss on disposal is the difference between the "
                                                       "proceeds and the net book value, and is taken to profit or loss."),
        ("Fees receivable", "Fees receivable are the amounts clients still owe at the reporting date, including value added tax, after "
                            "receipts and credit notes dated on or before that date."),
        ("Income tax", "Income tax expense is the amount entered for the period. Current tax payable is the amount entered at the reporting date."),
        ("Foreign currency", "The financial statements are presented in United States dollars (USD), the functional currency. " + zwg_text),
    ]
    notes = [
        {"number": 1, "title": "General information", "policy": None, "paragraphs": [
            f"{FIRM_NAME} is a private company providing audit, assurance, accounting, tax and advisory services. "
            f"These financial statements are for the {cur['label'].lower()}."], "accounts": [], "total": None},
        {"number": 2, "title": "Basis of preparation", "policy": None, "paragraphs": [
            f"The financial statements are prepared on the accrual basis in accordance with {framework_label}, from the firm's own "
            "records (invoicing, expense register and asset register) and the balances entered for the reporting date. "
            "They are management accounts and have not been audited.", zwg_text], "accounts": [], "total": None},
        {"number": 3, "title": "Significant accounting policies", "policy": None, "paragraphs": [], "policies": policies,
         "accounts": [], "total": None},
    ]
    number = 4
    by_cat = {}
    for codes, title in NOTE_ORDER:
        cur_total = sum(totals[c]["current"] for c in codes)
        pri_total = sum(totals[c]["prior"] for c in codes) if pri else 0.0
        has_register = codes == ["ppe"] and bool(cur["schedule"]["by_class"])
        if _is_nil(cur_total) and _is_nil(pri_total) and not has_register:
            continue
        note = {"number": number, "title": title, "policy": None, "paragraphs": [],
                "accounts": _merge_accounts(cur, pri, codes), "total": {"current": r2(cur_total), "prior": r2(pri_total)}}
        for c in codes:
            by_cat[c] = number
        if has_register:
            note["accounts"] = []
            note["movement"] = cur["schedule"]["by_class"]
            note["movement_total"] = cur["schedule"]["total"]
            note["total"] = {"current": cur["schedule"]["total"]["closing_nbv"], "prior": cur["schedule"]["total"]["opening_nbv"]}
            note["prior_label"] = "Opening"
        if codes == ["trade_receivables"]:
            d = cur["derived"]
            labels = analytics.AGEING_BUCKETS
            note["extra_table"] = {
                "title": "Age of fees receivable from clients at the reporting date (by days past the due date)",
                "rows": [(labels[i], r2(d["ageing"][i])) for i in range(len(labels)) if abs(d["ageing"][i]) >= 0.005],
            }
            if abs(d["vat_in_receivables"]) >= 0.005:
                note["paragraphs"].append(
                    f"Fees receivable are stated including value added tax. The VAT included in them is USD {d['vat_in_receivables']:,.2f}; "
                    "the VAT owed to the tax authority is entered separately under other current liabilities.")
        if codes == ["income_tax_expense"]:
            pbt = pl["profit_before_tax"]["current"]
            if abs(pbt) >= 0.005:
                note["paragraphs"].append(
                    f"The income tax expense is {cur_total / pbt * 100:.2f}% of profit before tax of USD {pbt:,.2f}.")
        if codes == ["other_current_liabilities"]:
            note["paragraphs"].append("Customer advances are amounts received from clients before an invoice was raised and not yet applied or refunded.")
        notes.append(note)
        number += 1
    if (ye.extra_note or "").strip():
        notes.append({"number": number, "title": "Additional information", "policy": None,
                      "paragraphs": [p.strip() for p in ye.extra_note.strip().splitlines() if p.strip()], "accounts": [], "total": None})
    return notes, by_cat


# ---------------------------------------------------------------------- the whole set

def build_firm_statements(year_end_id=None, year_ends=None, classes=None, assets=None):
    """Everything for one year-end (latest if none given). Returns None when no year-end exists."""
    if year_ends is None:
        year_ends = FirmYearEnd.query.order_by(FirmYearEnd.period_end).all()
    if not year_ends:
        return None
    if classes is None or assets is None:
        classes, assets = load_register()
    chain = build_chain(year_ends, classes, assets)
    idx = len(chain) - 1
    if year_end_id is not None:
        for i, c in enumerate(chain):
            if c["ye_id"] == year_end_id:
                idx = i
                break
    cur = chain[idx]
    pri = chain[idx - 1] if idx > 0 else None
    ye = cur["ye"]
    framework_label = dict(FIRM_FRAMEWORKS).get(ye.reporting_framework or "ifrs_for_smes", "IFRS for SMEs")

    totals = _totals_pair(cur, pri)
    if pri is None and ye.opening_nil:
        pass  # a first period with nil opening: every "prior" balance is genuinely zero except as set above
    pl = build_income_statement(totals)
    equity = financials.build_equity_statement(totals, pl)
    sfp = build_financial_position(totals, equity)

    has_prior = pri is not None
    cf_available = has_prior or ye.opening_nil
    cf = None
    if cf_available:
        t_cf = totals
        if not has_prior:  # opening_nil: opening balances really are nil
            t_cf = {c: dict(v) for c, v in totals.items()}
            for code in ALL_CODES:
                t_cf[code]["prior"] = 0.0
        cf = build_firm_cash_flow(t_cf, pl, cur["schedule"])

    # statement of changes in equity: this period, and the comparative period where its opening is known
    cur_open = None
    if pri is not None:
        cur_open = {c: pri["totals"][c] for c in ("share_capital", "share_premium", "other_reserves")}
    elif ye.opening_nil:
        cur_open = {c: 0.0 for c in ("share_capital", "share_premium", "other_reserves")}
    soce = [build_soce(cur["totals"], cur_open, cur["totals"]["retained_earnings"], cur["label"])]
    if pri is not None:
        pp = chain[idx - 2] if idx > 1 else None
        if pp is not None:
            pri_open = {c: pp["totals"][c] for c in ("share_capital", "share_premium", "other_reserves")}
        elif pri["ye"].opening_nil:
            pri_open = {c: 0.0 for c in ("share_capital", "share_premium", "other_reserves")}
        else:
            pri_open = None
        if pri_open is not None:
            soce.append(build_soce(pri["totals"], pri_open, pri["totals"]["retained_earnings"], pri["label"]))

    notes, by_cat = build_notes(ye, cur, pri, totals, pl, classes, framework_label)
    cf_shell = cf if cf else {"investing_rows": [], "financing_rows": []}
    _attach_note_numbers(pl, sfp, cf_shell, by_cat)

    # ---- checks, every one reported
    checks = []
    bal = sfp["balance_check"]["current"]
    checks.append({"key": "balance", "ok": abs(bal) < 0.01, "title": "Balance sheet balances",
                   "detail": "Total assets equal total equity and liabilities." if abs(bal) < 0.01 else
                   f"Total assets exceed total equity and liabilities by USD {bal:,.2f} (negative = the reverse). "
                   f"An opening retained earnings of USD {cur['totals']['retained_earnings'] + bal:,.2f} would balance it; otherwise a balance is missing or wrong."})
    if cf is not None:
        v = cf["variance"]
        checks.append({"key": "cash", "ok": abs(v) < 0.01, "title": "Cash flow agrees to cash at bank and on hand",
                       "detail": "The movement in cash explains the change in the cash balance." if abs(v) < 0.01 else
                       f"Cash per the balance sheet differs from the cash flow's closing figure by USD {v:,.2f}."})
    else:
        checks.append({"key": "cash", "ok": False, "title": "Cash flow statement not available",
                       "detail": "A cash flow needs the previous year-end's balances as the opening position. Add the previous year-end (even with only its "
                                 "closing balances), or tick 'first period' on this year-end if the firm started in this period."})
    if has_prior:
        var = equity["rows"][-1].get("opening_variance") if equity["rows"] else None
        for r in equity["rows"]:
            if r["label"] == "Retained earnings":
                var = r.get("opening_variance")
        ok = var is None or abs(var) < 0.01
        checks.append({"key": "re", "ok": ok, "title": "Opening retained earnings equals the previous closing",
                       "detail": "Retained earnings brought forward agrees to the previous year-end's closing figure." if ok else
                       f"Retained earnings brought forward differs from the previous year-end's closing by USD {var:,.2f}."})
    warns = list(cur["warnings"])
    for w in warns:
        checks.append({"key": "warn", "ok": False, "title": "Check", "detail": w})
    if abs(sfp["total_assets"]["current"]) < 0.005 and abs(pl["profit_for_year"]["current"]) < 0.005:
        checks.append({"key": "empty", "ok": False, "title": "Nothing to report yet",
                       "detail": "No invoices, expenses, assets or entered balances were found for this period."})

    return {
        "ye": ye, "cur": cur, "pri": pri, "has_prior": has_prior, "cf_available": cf_available,
        "totals": totals, "pl": pl, "equity": equity, "sfp": sfp, "cf": cf, "soce": soce, "notes": notes,
        "checks": checks, "ok": all(c["ok"] for c in checks), "framework_label": framework_label,
        "labels": {"current": cur["label"], "prior": pri["label"] if pri else None},
        "chain": chain, "classes": classes,
    }
