"""Section 16(1) Prohibited Deductions engine.

Tests every expense charged to profit or loss against the prohibited-deduction rules of the Income Tax Act
[Chapter 23:06], records a tax_deductibility_status and clause for each, and produces (a) an audit schedule of the
add-backs grouped by clause with voucher references and (b) the add-back lines of the Income Tax Computation
working paper, which already carries the profit, the deductions and allowances, the assessed loss and the rates.

How it differs from a naive implementation (and why):

* The clause letters and treatments are DATA (models.ProhibitedDeductionRule), seeded from RULE_DEFAULTS. The Act is
  amended by almost every Finance Act and published summaries disagree on lettering, so the app does not pretend to
  be the authority: a Partner or Admin corrects a clause label or treatment and marks each rule *verified* against the
  current Act; until then every schedule line that relies on it is flagged.
* An add-back is made only for an amount that was actually CHARGED to profit or loss. A capitalised cost or a
  transfer to reserve that never went through the income statement has nothing to add back; adding it back would
  double-count it.
* The thin-capitalisation add-back is the interest on the part of related-party debt above the ratio, i.e.
  interest x (1 - limit / actual ratio), which equals interest x excess debt / total debt. With no or negative equity
  all the interest on that debt is disallowed (no division by zero).
* Mixed-purpose items can be partly disallowed (PARTIALLY_PROHIBITED) by an entered percentage.
* The tax computation itself (profit, less exempt income and allowances, assessed loss, rate) stays in the Income Tax
  Computation working paper; this module posts its add-backs there, replacing only the lines it posted before.
No rate, cap or ratio is assumed: the passenger-motor-vehicle cap and the thin-capitalisation ratio are entered per
engagement, so a Finance Act change never needs a code change.
"""
import csv
import io
import json
from collections import OrderedDict
from datetime import date, datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, send_file
from flask_login import login_required, current_user

from extensions import db
from models import (
    Engagement, GLAccount, JournalEntry, JournalEntryLine, IncomeTaxComputation, IncomeTaxAdjustmentLine, TrialBalance,
    ProhibitedDeductionRule, ProhibitedDeductionSetup, ProhibitedDeductionItem, TAX_DEDUCTIBILITY_STATUSES,
)

prohibited_bp = Blueprint("prohibited", __name__)

ENGINE_SOURCE = "s16_engine"
THIN_CAP_CLAUSE = "16(1)(q)"
TREATMENTS = OrderedDict([
    ("full", "Disallow in full"),
    ("apportion", "Disallow the entered percentage (100% if none entered)"),
    ("insured", "Disallow the amount recovered under insurance"),
    ("travel_conditional", "Disallow unless there is a business necessity"),
    ("pmv_lease", "Disallow lease cost above the 16(1)(k) cap (cumulative for the vehicle)"),
    ("assoc_fee", "Disallow fees to an associated company above the 16(1)(r) limit"),
    ("pmv_cap", "Disallow the part above the passenger-motor-vehicle cap (vehicle cost basis - older reading)"),
    ("mining_fee", "Disallow only for a local mining operation of a foreign parent (older reading)"),
    ("review", "Add back and flag for a Partner decision"),
    ("none", "Allowable - no add-back"),
])

# code, name, group, clause, treatment, note
RULE_DEFAULTS = [
    ("ordinary_business_expense", "Ordinary business expense", "General", "", "none", "Allowable if it passes the general deduction test; nothing is added back."),
    # A. personal and domestic
    ("personal_maintenance", "Maintenance of the taxpayer", "Personal and domestic", "16(1)(a)", "full", ""),
    ("family_support", "Maintenance of the taxpayer's family", "Personal and domestic", "16(1)(a)", "full", ""),
    ("personal_establishment", "Maintenance of the taxpayer's establishment", "Personal and domestic", "16(1)(a)", "full", ""),
    ("domestic_private_expense", "Domestic or private expense", "Personal and domestic", "16(1)(b)", "full", ""),
    ("home_to_work_travel", "Travel between home and place of work", "Personal and domestic", "16(1)(b)", "full", ""),
    ("inter_trade_travel", "Travel between two places of trade", "Personal and domestic", "16(1)(b)", "full",
     "Named in section 16(1)(b): travel between the places where a taxpayer carries on two or more distinct trades is not deductible. The paragraph has no business-necessity exception."),
    # B. losses, taxes and reserves
    ("loss_or_damage", "Loss or damage (insurable)", "Losses, taxes and reserves", "16(1)(c)", "insured",
     "Only the part recovered or recoverable under insurance or an indemnity is added back."),
    ("corporate_income_tax", "Income tax", "Losses, taxes and reserves", "16(1)(d)", "full", ""),
    ("capital_gains_tax", "Capital gains tax", "Losses, taxes and reserves", "16(1)(d)", "full", ""),
    ("carbon_tax", "Carbon tax", "Losses, taxes and reserves", "16(1)(d)", "review",
     "Section 16(1)(d) refuses tax 'upon the income of the taxpayer'. Carbon tax (section 36E) is a charge on emissions and is not named. Added back pending a Partner decision."),
    ("presumptive_tax", "Presumptive tax", "Losses, taxes and reserves", "16(1)(d)", "full", ""),
    ("withholding_tax", "Withholding tax borne by the taxpayer", "Losses, taxes and reserves", "16(1)(d)", "full", ""),
    ("vat_expense", "Value added tax as a stand-alone tax charge", "Losses, taxes and reserves", "16(1)(d)", "review",
     "Section 16(1)(d) refuses tax 'upon the income of the taxpayer'; VAT is not named. Irrecoverable VAT that forms part of the cost of an expense belongs to that expense, not here. A stand-alone VAT charge is added back pending a Partner decision."),
    ("paye_expense", "PAYE as a stand-alone tax charge", "Losses, taxes and reserves", "16(1)(d)", "review",
     "PAYE withheld from staff is not the employer's expense (the gross salary is). Tax the employer bears for staff is part of their pay and is not tax upon the taxpayer's own income. Added back pending a Partner decision."),
    ("imtt_expense", "Intermediated money transfer tax", "Losses, taxes and reserves", "16(1)(d1)", "full",
     "Section 16(1)(d1): any amount of Intermediated Money Transfer Tax charged under section 36G."),
    ("tax_penalties", "Tax penalties", "Losses, taxes and reserves", "16(1)(d)", "review",
     "Section 16(1)(d) covers tax on income and interest payable on it. It does not name penalties, so a penalty is tested under section 15(2)(a). Added back pending a Partner decision."),
    ("tax_interest", "Interest on tax", "Losses, taxes and reserves", "16(1)(d)", "full", ""),
    ("transfer_to_reserve", "Transfer to a reserve", "Losses, taxes and reserves", "16(1)(e)", "full",
     "Added back only if it was charged to profit or loss."),
    ("capitalized_expense", "Capitalised expense", "Losses, taxes and reserves", "16(1)(e)", "full",
     "Added back only if it was charged to profit or loss; capital allowances are claimed separately."),
    # C. exempt income, funds, capital costs
    ("exempt_income_cost", "Cost of earning exempt or non-Zimbabwe income", "Exempt income, funds and capital costs", "16(1)(f)", "apportion",
     "Section 16(1)(f): expenditure or loss incurred in producing income that is exempt under Part III or not derived from sources within Zimbabwe, and in operations that would have produced such income. It includes an assessed loss of an earlier year to that extent. Where an expense is shared, enter the percentage that relates to that income."),
    ("contribution_unapproved_fund", "Contribution to a pension or benefit fund (not otherwise allowed)", "Exempt income, funds and capital costs", "16(1)(g)", "full",
     "Section 16(1)(g) refuses employer contributions to a fund providing pensions, annuities or sickness, accident or unemployment benefits, 'save as otherwise expressly provided'. Contributions to approved funds are allowed by section 15(2)(h) and the Sixth Schedule: use the ordinary expense type for those."),
    ("notional_capital_interest", "Notional interest on capital employed", "Exempt income, funds and capital costs", "16(1)(h)", "full", ""),
    ("non_trade_premises_rent", "Rent of premises not used for trade", "Exempt income, funds and capital costs", "16(1)(i)", "apportion", "The part used for trade is allowable: enter the disallowed percentage."),
    ("non_trade_repairs", "Repairs of premises not used for trade", "Exempt income, funds and capital costs", "16(1)(i)", "apportion", "The part used for trade is allowable: enter the disallowed percentage."),
    ("dwelling_house_expense", "Dwelling house or domestic premises expense", "Exempt income, funds and capital costs", "16(1)(i)", "apportion", "The part used for trade is allowable: enter the disallowed percentage."),
    # D. specialised business restrictions
    ("sole_selling_rights", "Cost of securing sole selling rights", "Specialised business and corporate", "16(1)(j)", "full", ""),
    ("restraint_of_trade", "Restraint of trade payment", "Specialised business and corporate", "", "review",
     "Section 16(1)(j) is about obligations that restrain another person from selling goods other than the taxpayer's; it is not a restraint of trade rule. A restraint payment is usually capital and fails section 15(2)(a). Added back pending a Partner decision."),
    ("pmv_lease", "Passenger motor vehicle lease cost", "Specialised business and corporate", "16(1)(k)", "pmv_lease",
     "Section 16(1)(k)(vi): lease cost of a passenger motor vehicle is refused to the extent that it, added to the lease cost of the same vehicle in earlier years, exceeds the cap (US$50,000, or the Zimbabwe dollar equivalent at the prevailing rate, for a lease first made from 1 January 2009). Enter the cap in the setup and, on the line, the vehicle's lease cost to date including this amount."),
    ("pmv_operating_cost", "Passenger motor vehicle running cost", "Specialised business and corporate", "16(1)(k)", "none",
     "Section 16(1)(k) limits only the cost of LEASING a passenger motor vehicle. Running costs are not capped by it. (The cost of buying one is limited for capital allowances by paragraph 14 of the Fourth Schedule.)"),
    ("share_award_expense", "Shares awarded to an employee or director", "Specialised business and corporate", "16(1)(l)", "full", "Check the timing and any other treatment of the award."),
    ("client_entertainment", "Entertainment", "Specialised business and corporate", "16(1)(m)", "full", ""),
    ("business_lunch", "Business lunch (hospitality)", "Specialised business and corporate", "16(1)(m)", "full", "Hospitality is within entertainment as published."),
    ("hospitality_allowance", "Hospitality allowance", "Specialised business and corporate", "16(1)(m)", "full", ""),
    ("expense_earning_stocks_shares_income", "Cost of earning foreign interest or dividends on company shares or stock", "Specialised business and corporate", "16(1)(n)", "full",
     "Section 16(1)(n) refuses the cost of producing the income described in the first proviso to section 12(2): amounts from a source outside Zimbabwe by way of interest or dividends on securities that are stocks or shares issued by a company."),
    ("expense_earning_local_fi_interest", "Cost of earning interest from a local financial institution", "Specialised business and corporate", "16(1)(o)", "full",
     "This is the cost of EARNING such interest, not interest paid."),
    # E. foreign and related party
    ("foreign_management_fee", "Fees, administration and management paid to an associated company", "Foreign and related party", "16(1)(r)", "assoc_fee",
     "Section 16(1)(r): fees, administration and management in favour of a company of which the taxpayer is an associated enterprise (or, for a foreign company, its local branch) are allowed only up to 1% (0.75% before trade or income starts, or in a period of non-production) of A - (B + C), where A is total expenditure qualifying under section 15, B is such fees paid outside Zimbabwe and C is expenditure under section 15(2)(f)(i). Enter A, B and C in the setup; the limit applies to all such fees together."),
    ("foreign_loan_interest_excess", "Foreign loan interest above the ordinary rate", "Foreign and related party", "16(1)(s)", "full",
     "Section 16(1)(s): interest on foreign loans above what would have been payable at the exchange rate the lender ordinarily offers its other clients on the date of the transaction. Enter only the EXCESS."),
    ("thin_capitalisation", "Thin capitalisation (interest on related-party debt above the debt : equity limit)", "Foreign and related party", "16(1)(q)", "full",
     "Driven by the engagement setup, not by individual expenses. Section 16(1)(q) covers a local branch or subsidiary of a foreign company, and a local company or its subsidiary, for debt contracted in connection with producing income beyond a debt to equity ratio of three to one. 'Equity' is issued and paid-up capital, unappropriated profits, reserves, realised reserves and interest-free loans from shareholders. It does not apply to debt contracted with a locally domiciled financial institution or other person ordinarily resident in Zimbabwe who is not an associate (and has not colluded), nor to debt through a Government credit facility by a public entity."),
]
NON_ITEM_CODES = {"thin_capitalisation"}   # rules that are applied from the setup, not chosen for an expense

ALIASES = {
    "interest_local_financial_institution_loan_deposit": "expense_earning_local_fi_interest",
    "pmv_lease_or_operating_cost": "pmv_lease",
    "restraint_of_trade_payment": "restraint_of_trade",
    "directly_linked_to_exempt_income": "exempt_income_cost",
    "tax_expense": "corporate_income_tax",
}


# ----------------------------------------------------------------- rules

def ensure_rules():
    """Seed any default rule that is missing (never overwrites an edited one)."""
    have = {r.code for r in ProhibitedDeductionRule.query.all()}
    added = False
    for code, name, group, clause, treatment, note in RULE_DEFAULTS:
        if code not in have:
            db.session.add(ProhibitedDeductionRule(code=code, name=name, group=group, clause_label=clause, treatment=treatment, note=note))
            added = True
    if added:
        db.session.commit()


# What the Act's own text (as filed in the Legislative Updates library) changes in the starting rules. Each entry lists the value the
# rule was first seeded with and the Act-based value. A rule is brought into line ONLY while it still holds the seeded value, so
# anything a Partner has edited is left alone; and a rule that changes is no longer "verified" (it has to be ticked again).
ACT_ALIGN = {
    "inter_trade_travel": {"treatment": ("travel_conditional", "full")},
    "carbon_tax": {"treatment": ("full", "review")},
    "vat_expense": {"treatment": ("full", "review")},
    "paye_expense": {"treatment": ("full", "review")},
    "tax_penalties": {"treatment": ("full", "review")},
    "imtt_expense": {"clause_label": ("16(1)(d)", "16(1)(d1)")},
    "pmv_lease": {"treatment": ("pmv_cap", "pmv_lease")},
    "foreign_management_fee": {"treatment": ("mining_fee", "assoc_fee"),
                               "name": ("Management or administration fee to a foreign parent", "Fees, administration and management paid to an associated company")},
    "exempt_income_cost": {"name": ("Cost of earning exempt income", "Cost of earning exempt or non-Zimbabwe income")},
    "contribution_unapproved_fund": {"name": ("Contribution to a fund not approved by the Commissioner", "Contribution to a pension or benefit fund (not otherwise allowed)")},
    "expense_earning_stocks_shares_income": {"name": ("Cost of earning income from shares or stock", "Cost of earning foreign interest or dividends on company shares or stock")},
}


def align_with_act():
    """Bring unedited starting rules into line with the Act's wording. Returns how many rules changed."""
    ensure_rules()
    defaults = {d[0]: d for d in RULE_DEFAULTS}
    changed = 0
    for code, fields in ACT_ALIGN.items():
        r = ProhibitedDeductionRule.query.filter_by(code=code).first()
        if r is None:
            continue
        touched = False
        for field, (old, new) in fields.items():
            if (getattr(r, field) or "") == old:
                setattr(r, field, new)
                touched = True
                if field in ("treatment", "clause_label"):
                    r.verified_by_id = r.verified_at = r.verified_note = None
        d = defaults.get(code)
        # the explanatory note follows the Act whenever the rule's treatment or clause moved, or it still reads as the seeded text
        if d is not None and (touched or not (r.note or "").strip()):
            r.note = d[5]
        if touched:
            changed += 1
    if changed:
        reevaluate_all()
    db.session.commit()
    return changed


def rules_by_code():
    ensure_rules()
    return {r.code: r for r in ProhibitedDeductionRule.query.all()}


def normalise_type(value):
    v = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(v, v)


def get_setup(engagement_id, create=False):
    s = ProhibitedDeductionSetup.query.filter_by(engagement_id=engagement_id).first()
    if s is None and create:
        s = ProhibitedDeductionSetup(engagement_id=engagement_id)
        db.session.add(s)
        db.session.flush()
    return s


# ------------------------------------------------------------ evaluation

def _f(v):
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _status(amount, disallowed):
    if abs(disallowed) < 0.005:
        return "ALLOWABLE"
    if abs(amount) - abs(disallowed) < 0.005 or abs(disallowed) >= abs(amount):
        return "PROHIBITED_SECTION_16"
    return "PARTIALLY_PROHIBITED"


def evaluate(item, rule, setup=None):
    """Pure rule evaluation for one expense. `item` and `rule` are objects or simple namespaces with the model's
    attribute names; `setup` supplies the PMV cap and the mining flag. Returns a dict:
    status, clause, disallowed (the part section 16(1) refuses), addback (the part that must be added to profit, which is
    nil when the amount was never charged to profit or loss), note, verified."""
    amount = _f(item.amount)
    notes = []
    clause = (rule.clause_label or "").strip() if rule is not None else ""
    treatment = rule.treatment if rule is not None and getattr(rule, "is_active", True) else "none"
    if rule is None:
        notes.append("No rule for this expense type - treated as allowable")
    elif not getattr(rule, "is_active", True):
        notes.append("Rule switched off - treated as allowable")

    disallowed = 0.0
    if treatment == "full":
        disallowed = amount
    elif treatment == "apportion":
        pct = item.prohibited_percent
        pct = 100.0 if pct is None else max(0.0, min(_f(pct), 100.0))
        disallowed = amount * pct / 100.0
        if pct < 100.0:
            notes.append(f"{pct:g}% disallowed")
    elif treatment == "insured":
        if item.is_insured and _f(item.reimbursed_amount) > 0:
            disallowed = min(abs(_f(item.reimbursed_amount)), abs(amount)) * (1 if amount >= 0 else -1)
            notes.append("Amount recovered under insurance")
        else:
            notes.append("No insurance recovery recorded")
    elif treatment == "travel_conditional":
        if item.business_necessity:
            notes.append("Business necessity recorded - allowable")
        else:
            disallowed = amount
    elif treatment == "pmv_cap":
        cap = _f(getattr(setup, "pmv_cap", None)) if setup is not None else 0.0
        cost = _f(item.vehicle_cost)
        if cap <= 0:
            notes.append("Enter the passenger-motor-vehicle cap in the setup to test this line")
        elif cost <= 0:
            notes.append("Enter the vehicle's cost to test this line")
        elif cost > cap:
            ratio = cap / cost
            disallowed = amount * (1 - ratio)
            notes.append(f"Vehicle cost {cost:,.2f} exceeds the cap {cap:,.2f}: {ratio * 100:.2f}% allowable")
        else:
            notes.append("Vehicle cost is within the cap")
    elif treatment == "pmv_lease":
        cap = _f(getattr(setup, "pmv_cap", None)) if setup is not None else 0.0
        to_date = _f(item.vehicle_cost) or amount      # lease cost of this vehicle to date, this amount included
        if cap <= 0:
            notes.append("Enter the passenger-motor-vehicle lease cap in the setup to test this line (section 16(1)(k))")
        elif to_date > cap:
            disallowed = max(0.0, min(amount, to_date - cap))
            notes.append(f"Lease cost of this vehicle to date {to_date:,.2f} exceeds the cap {cap:,.2f} by {to_date - cap:,.2f}")
        else:
            notes.append("Lease cost to date is within the cap")
    elif treatment == "assoc_fee":
        a, b, c = (_f(getattr(setup, k, None)) for k in ("assoc_fee_a", "assoc_fee_b", "assoc_fee_c")) if setup is not None else (0.0, 0.0, 0.0)
        base = a - (b + c)
        if base <= 0:
            notes.append("Enter A (total expenditure qualifying under section 15), B and C in the setup to test this fee (section 16(1)(r))")
        else:
            rate = 0.0075 if getattr(setup, "assoc_fee_pretrade", False) else 0.01
            limit = base * rate
            total = _f(getattr(setup, "assoc_fee_total", None)) or amount
            if total > limit + 0.005 and total > 0:
                disallowed = amount * (total - limit) / total
                notes.append(f"Fees to associated companies {total:,.2f} exceed the limit {limit:,.2f} ({rate * 100:g}% of {base:,.2f})")
            else:
                notes.append(f"Fees to associated companies are within the limit {limit:,.2f} ({rate * 100:g}% of {base:,.2f})")
    elif treatment == "mining_fee":
        if setup is not None and getattr(setup, "mining_foreign_parent", False):
            disallowed = amount
        else:
            notes.append("Entity is not recorded as a local mining operation of a foreign parent - allowable here")
    elif treatment == "review":
        disallowed = amount
        notes.append("Added back pending a Partner decision")

    # an expense directly linked to exempt income is refused whatever its type (16(1)(f))
    if item.linked_to_exempt:
        pct = item.prohibited_percent
        pct = 100.0 if pct is None else max(0.0, min(_f(pct), 100.0))
        exempt = amount * pct / 100.0
        if abs(exempt) > abs(disallowed):
            disallowed = exempt
            ef = _exempt_clause()
            clause = ef or clause
            notes.append("Linked to exempt income")
    if abs(disallowed) > abs(amount):
        disallowed = amount

    status = _status(amount, disallowed)
    if treatment == "review" and abs(disallowed) >= 0.005:
        status = "REVIEW_REQUIRED"
    charged = bool(item.charged_to_pl)
    addback = disallowed if charged else 0.0
    if abs(disallowed) >= 0.005 and not charged:
        notes.append("Not charged to profit or loss - nothing to add back")
    return {"status": status, "clause": clause if abs(disallowed) >= 0.005 else "", "disallowed": round(disallowed, 2),
            "addback": round(addback, 2), "note": "; ".join(notes)[:400], "verified": bool(getattr(rule, "is_verified", False)) if rule is not None else False}


def _exempt_clause():
    r = ProhibitedDeductionRule.query.filter_by(code="exempt_income_cost").first()
    return (r.clause_label or "").strip() if r is not None else "16(1)(f)"


def thin_cap(setup):
    """Thin-capitalisation add-back from the engagement setup. Returns None when nothing was entered."""
    if setup is None:
        return None
    debt, equity, interest = _f(setup.related_debt), _f(setup.qualifying_equity), _f(setup.related_interest)
    limit = _f(setup.thin_cap_ratio)
    if debt <= 0 and interest <= 0:
        return None
    out = {"debt": debt, "equity": equity, "interest": interest, "limit": limit, "ratio": None, "excess_debt": 0.0,
           "disallowed": 0.0, "note": "", "clause": THIN_CAP_CLAUSE}
    if limit <= 0:
        out["note"] = "Enter the debt : equity limit in the setup to test this line"
        return out
    if equity <= 0:
        out["excess_debt"] = debt
        out["disallowed"] = interest if debt > 0 else 0.0
        out["note"] = "No positive qualifying equity: all interest on the related debt is disallowed"
        return out
    ratio = debt / equity
    out["ratio"] = ratio
    if ratio > limit:
        out["excess_debt"] = debt - limit * equity
        out["disallowed"] = round(interest * (1 - limit / ratio), 2)
        out["note"] = f"Debt : equity {ratio:.2f} : 1 exceeds {limit:g} : 1"
    else:
        out["note"] = f"Debt : equity {ratio:.2f} : 1 is within {limit:g} : 1"
    return out


def _prepare_setup(setup, items, rules):
    """The associated-company fee limit (16(1)(r)) applies to all such fees together, so the total is worked out once
    and carried on the setup for each line to share."""
    if setup is None:
        return
    setup.assoc_fee_total = sum(_f(i.amount) for i in items if getattr(rules.get(i.expense_type), "treatment", None) == "assoc_fee"
                                and getattr(rules.get(i.expense_type), "is_active", True))


def reevaluate_engagement(engagement_id, rules=None):
    rules = rules or rules_by_code()
    setup = get_setup(engagement_id)
    all_items = ProhibitedDeductionItem.query.filter_by(engagement_id=engagement_id).all()
    _prepare_setup(setup, all_items, rules)
    for it in all_items:
        res = evaluate(it, rules.get(it.expense_type), setup)
        it.status, it.clause_label = res["status"], res["clause"]
        it.addback_amount, it.disallowed_amount = res["addback"], res["disallowed"]
        it.eval_note, it.rule_verified = res["note"], res["verified"]


def reevaluate_all():
    rules = rules_by_code()
    ids = [i for (i,) in db.session.query(ProhibitedDeductionItem.engagement_id).distinct().all()]
    for eid in ids:
        reevaluate_engagement(eid, rules)


# -------------------------------------------------------------- schedule

def _clause_key(label):
    import re
    nums = re.findall(r"\(([^)]+)\)", label or "")
    return (0 if label else 1, [n for n in nums] or [label or "~"])


def build_schedule(engagement_id):
    """Everything the page, the export and the posting need."""
    rules = rules_by_code()
    setup = get_setup(engagement_id)
    items = ProhibitedDeductionItem.query.filter_by(engagement_id=engagement_id).order_by(ProhibitedDeductionItem.id).all()
    groups = OrderedDict()
    for it in items:
        if abs(it.addback_amount or 0) >= 0.005 or (abs(it.disallowed_amount or 0) >= 0.005):
            key = it.clause_label or "Clause to be confirmed"
            g = groups.setdefault(key, {"clause": key, "items": [], "amount": 0.0, "addback": 0.0, "names": set()})
            g["items"].append(it)
            g["amount"] += it.amount or 0.0
            g["addback"] += it.addback_amount or 0.0
            r = rules.get(it.expense_type)
            g["names"].add(r.name if r else it.expense_type)
    ordered = sorted(groups.values(), key=lambda g: _clause_key(g["clause"]))
    for g in ordered:
        g["title"] = " / ".join(sorted(g["names"]))
    thin = thin_cap(setup)
    thin_rule = rules.get("thin_capitalisation")
    if thin is not None and thin_rule is not None:
        thin["clause"] = (thin_rule.clause_label or "").strip() or THIN_CAP_CLAUSE
    thin_addback = thin["disallowed"] if thin else 0.0
    total_items_addback = sum(g["addback"] for g in ordered)
    unverified = sorted({rules[it.expense_type].name for it in items if (it.addback_amount or it.disallowed_amount) and it.expense_type in rules and not rules[it.expense_type].is_verified})
    thin_unverified = bool(thin and thin["disallowed"] and not (thin_rule is not None and thin_rule.is_verified))
    return {
        "groups": ordered, "thin": thin, "thin_addback": thin_addback, "items": items,
        "total_addback": round(total_items_addback + thin_addback, 2),
        "counts": {s: sum(1 for i in items if i.status == s) for s in TAX_DEDUCTIBILITY_STATUSES},
        "unverified_rules": unverified, "thin_unverified": thin_unverified,
        "review_items": [i for i in items if i.status == "REVIEW_REQUIRED"],
        "needs_input": [i for i in items if i.eval_note and ("Enter the" in i.eval_note)],
    }


def profit_before_tax(engagement, setup):
    """Profit before tax from the trial balance (the same figure the Income Tax Computation tab uses), else the figure entered."""
    tb = TrialBalance.query.filter_by(engagement_id=engagement.id).first()
    if tb is not None and tb.lines:
        try:
            import financials as fin
            st = fin.build_all_statements(tb.lines, tb.adjustments, reporting_framework=engagement.reporting_framework,
                                          cash_flow_method=engagement.cash_flow_method)
            return st["pl"]["profit_before_tax"]["current"], "trial balance"
        except Exception:
            pass
    if setup is not None and setup.net_profit_override is not None:
        return setup.net_profit_override, "entered"
    return None, None


def computation_statement(engagement, schedule, setup):
    """Net profit + section 16(1) add-backs + other preparer lines - deductions/allowances, using the working paper's own function."""
    import financials as fin
    pbt, basis = profit_before_tax(engagement, setup)
    comp = IncomeTaxComputation.query.filter_by(engagement_id=engagement.id).first()
    other = [{"item_type": l.item_type, "description": l.description, "amount": l.amount}
             for l in (comp.items if comp else []) if l.source != ENGINE_SOURCE]
    lines = list(other)
    for g in schedule["groups"]:
        lines.append({"item_type": "addback", "description": f"Section {g['clause']} - {g['title']}", "amount": g["addback"]})
    if schedule["thin"] and schedule["thin_addback"]:
        lines.append({"item_type": "addback", "description": f"Section {schedule['thin']['clause']} - thin capitalisation", "amount": schedule["thin_addback"]})
    if pbt is None:
        return None, basis
    result = fin.build_income_tax_computation(
        pbt, lines, tax_loss_brought_forward=(comp.tax_loss_brought_forward if comp else 0.0),
        tax_rate_percent=comp.tax_rate_percent if comp else None, aids_levy_percent=comp.aids_levy_percent if comp else None)
    result["basis"] = basis
    return result, basis


def post_to_computation(engagement, user):
    """Replace the lines this module posted before with one add-back line per clause (plus thin capitalisation)."""
    comp = IncomeTaxComputation.query.filter_by(engagement_id=engagement.id).first()
    if comp is None:
        comp = IncomeTaxComputation(engagement_id=engagement.id, tax_loss_brought_forward=0.0)
        db.session.add(comp)
        db.session.flush()
    for l in list(comp.items):
        if l.source == ENGINE_SOURCE:
            db.session.delete(l)
    db.session.flush()
    sched = build_schedule(engagement.id)
    order = IncomeTaxAdjustmentLine.query.filter_by(computation_id=comp.id).count()
    posted = 0
    for g in sched["groups"]:
        if abs(g["addback"]) < 0.005:
            continue
        db.session.add(IncomeTaxAdjustmentLine(computation_id=comp.id, item_type="addback", source=ENGINE_SOURCE, order=order,
                       description=f"Section {g['clause']} - {g['title']} ({len(g['items'])} item{'s' if len(g['items']) != 1 else ''})"[:200],
                       amount=round(g["addback"], 2)))
        order += 1
        posted += 1
    if sched["thin"] and abs(sched["thin_addback"]) >= 0.005:
        db.session.add(IncomeTaxAdjustmentLine(computation_id=comp.id, item_type="addback", source=ENGINE_SOURCE, order=order,
                       description=f"Section {sched['thin']['clause']} - interest disallowed under the thin capitalisation rule", amount=round(sched["thin_addback"], 2)))
        posted += 1
    # a changed computation has to be reviewed and signed again, as for any other edit of it
    comp.completed_by_id, comp.completed_at = user.id, datetime.utcnow()
    comp.reviewed_by_id = comp.reviewed_at = comp.partner_signed_by_id = comp.partner_signed_at = None
    return posted, sched["total_addback"]


# ------------------------------------------------------ ledger and import

def expense_accounts(engagement_id):
    return GLAccount.query.filter_by(engagement_id=engagement_id, account_type="Expense").order_by(GLAccount.order, GLAccount.code).all()


def pull_from_ledger(engagement, user):
    """One item per journal line posted to a mapped expense account (net debit); lines already pulled are skipped."""
    mapped = {a.id: a for a in expense_accounts(engagement.id) if a.tax_expense_type}
    if not mapped:
        return 0, 0
    have = {i for (i,) in db.session.query(ProhibitedDeductionItem.journal_line_id).filter(
        ProhibitedDeductionItem.engagement_id == engagement.id, ProhibitedDeductionItem.journal_line_id.isnot(None)).all()}
    added = skipped = 0
    q = (JournalEntryLine.query.join(JournalEntry, JournalEntryLine.journal_entry_id == JournalEntry.id)
         .filter(JournalEntry.engagement_id == engagement.id, JournalEntryLine.gl_account_id.in_(list(mapped))))
    for line in q.order_by(JournalEntryLine.id).all():
        if line.id in have:
            skipped += 1
            continue
        net = (line.debit or 0.0) - (line.credit or 0.0)
        if abs(net) < 0.005:
            continue
        acc, je = mapped[line.gl_account_id], line.journal_entry
        db.session.add(ProhibitedDeductionItem(
            engagement_id=engagement.id, entry_date=je.entry_date, voucher_ref=je.reference, gl_account_id=acc.id,
            journal_line_id=line.id, expense_type=acc.tax_expense_type, source="Ledger", amount=round(net, 2),
            description=(line.description or je.narration or acc.name or "")[:250] or acc.name, created_by_id=user.id))
        added += 1
    db.session.flush()
    return added, skipped


_TRUE = {"1", "true", "yes", "y", "x", "t"}


def _truthy(v):
    return str(v or "").strip().lower() in _TRUE


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "").replace(" ", "").strip())
    except ValueError:
        return None


def _date(v):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            continue
    return None


def import_csv(engagement, text, user):
    """CSV with a header row. Columns (names or the listed synonyms): date, voucher_ref|voucher|reference, description,
    expense_type|type, amount, charged_to_pl, is_insured, reimbursed_amount, business_necessity, linked_to_exempt,
    prohibited_percent, vehicle_cost. Returns (added, errors[list of text])."""
    rules = rules_by_code()
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return 0, ["The file has no header row."]
    cols = {(c or "").strip().lower().replace(" ", "_"): c for c in reader.fieldnames}
    def get(row, *names):
        for n in names:
            if n in cols:
                return row.get(cols[n])
        return None
    added, errors = 0, []
    for n, row in enumerate(reader, start=2):
        desc = (get(row, "description", "narration") or "").strip()
        amount = _num(get(row, "amount"))
        etype = normalise_type(get(row, "expense_type", "type") or "ordinary_business_expense")
        if not desc and amount is None:
            continue
        if not desc or amount is None:
            errors.append(f"Row {n}: needs a description and a numeric amount.")
            continue
        if etype not in rules or etype in NON_ITEM_CODES:
            errors.append(f"Row {n}: unknown expense type '{etype}'.")
            continue
        charged = get(row, "charged_to_pl")
        db.session.add(ProhibitedDeductionItem(
            engagement_id=engagement.id, entry_date=_date(get(row, "date") or ""), voucher_ref=(get(row, "voucher_ref", "voucher", "reference") or "").strip() or None,
            description=desc[:250], expense_type=etype, amount=amount, source="Import", created_by_id=user.id,
            charged_to_pl=True if charged in (None, "") else _truthy(charged), is_insured=_truthy(get(row, "is_insured")),
            reimbursed_amount=_num(get(row, "reimbursed_amount")) or 0.0, business_necessity=_truthy(get(row, "business_necessity")),
            linked_to_exempt=_truthy(get(row, "linked_to_exempt")), prohibited_percent=_num(get(row, "prohibited_percent")),
            vehicle_cost=_num(get(row, "vehicle_cost"))))
        added += 1
    db.session.flush()
    return added, errors


# ----------------------------------------------------------------- export

def build_xlsx(engagement, schedule, statement):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    wb = Workbook()
    bold = Font(bold=True)
    head = PatternFill("solid", fgColor="E8E2D0")
    num = '#,##0.00;(#,##0.00)'

    ws = wb.active
    ws.title = "Tax computation"
    ws.append([f"Tax computation statement - {engagement.client.name}"]); ws["A1"].font = Font(bold=True, size=13)
    ws.append([f"Period ended {engagement.period_end.strftime('%d %B %Y') if getattr(engagement, 'period_end', None) else '-'}"])
    ws.append([])
    if statement is None:
        ws.append(["Profit before tax is not available (no trial balance and none entered)."])
    else:
        ws.append(["Profit before tax", statement["profit_before_tax"]])
        ws.append(["Add: disallowable items"])
        for l in statement["addbacks"]:
            ws.append(["   " + l["description"], l["amount"]])
        ws.append(["Total add-backs", statement["total_addbacks"]]); ws.cell(ws.max_row, 1).font = bold
        ws.append(["Less: allowable deductions and capital allowances"])
        for l in statement["deductions"]:
            ws.append(["   " + l["description"], -l["amount"]])
        ws.append(["Total deductions", -statement["total_deductions"]]); ws.cell(ws.max_row, 1).font = bold
        ws.append(["Taxable income before assessed loss", statement["taxable_income_before_losses"]]); ws.cell(ws.max_row, 1).font = bold
        ws.append(["Less: assessed loss utilised", -statement["loss_utilised"]])
        ws.append(["Taxable income", statement["taxable_income"]]); ws.cell(ws.max_row, 1).font = bold
        if statement["tax_rate_percent"] is not None:
            ws.append([f"Tax at {statement['tax_rate_percent']:g}%", statement["base_tax"]])
            ws.append([f"AIDS levy at {statement['aids_levy_percent'] or 0:g}%", statement["aids_levy_amount"]])
            ws.append(["Total tax charge", statement["total_tax_charge"]]); ws.cell(ws.max_row, 1).font = bold
        for row in ws.iter_rows(min_row=4, min_col=2, max_col=2):
            for c in row:
                c.number_format = num
    ws.column_dimensions["A"].width = 78; ws.column_dimensions["B"].width = 18

    ws2 = wb.create_sheet("Schedule by clause")
    ws2.append(["Clause", "Voucher ref", "Date", "Description", "Expense type", "Status", "Original amount", "Add-back", "Note"])
    for c in ws2[1]: c.font = bold; c.fill = head
    rules = rules_by_code()
    for g in schedule["groups"]:
        ws2.append([f"Section {g['clause']} - {g['title']}"]); ws2.cell(ws2.max_row, 1).font = bold
        for it in g["items"]:
            ws2.append([it.clause_label, it.voucher_ref or "", it.entry_date.strftime("%Y-%m-%d") if it.entry_date else "", it.description,
                        rules[it.expense_type].name if it.expense_type in rules else it.expense_type, it.status, it.amount, it.addback_amount, it.eval_note or ""])
        ws2.append(["", "", "", "", "", f"Total {g['clause']}", g["amount"], g["addback"]]); ws2.cell(ws2.max_row, 6).font = bold
    if schedule["thin"] and schedule["thin_addback"]:
        t = schedule["thin"]
        ws2.append([f"Section {t['clause']} - thin capitalisation"]); ws2.cell(ws2.max_row, 1).font = bold
        ws2.append([t["clause"], "", "", "Interest on related-party debt above the limit", "", "PROHIBITED_SECTION_16", t["interest"], t["disallowed"], t["note"]])
    ws2.append([]); ws2.append(["", "", "", "", "", "TOTAL ADD-BACKS", "", schedule["total_addback"]]); ws2.cell(ws2.max_row, 6).font = bold
    for row in ws2.iter_rows(min_row=2, min_col=7, max_col=8):
        for c in row: c.number_format = num
    for col, w in zip("ABCDEFGHI", (24, 16, 12, 44, 34, 24, 16, 16, 60)): ws2.column_dimensions[col].width = w

    ws3 = wb.create_sheet("All expenses tested")
    ws3.append(["Voucher ref", "Date", "Description", "Expense type", "Charged to P&L", "Status", "Clause", "Amount", "Disallowed", "Add-back", "Rule verified", "Note"])
    for c in ws3[1]: c.font = bold; c.fill = head
    for it in schedule["items"]:
        ws3.append([it.voucher_ref or "", it.entry_date.strftime("%Y-%m-%d") if it.entry_date else "", it.description,
                    rules[it.expense_type].name if it.expense_type in rules else it.expense_type, "Yes" if it.charged_to_pl else "No",
                    it.status, it.clause_label or "", it.amount, it.disallowed_amount, it.addback_amount, "Yes" if it.rule_verified else "No", it.eval_note or ""])
    for row in ws3.iter_rows(min_row=2, min_col=8, max_col=10):
        for c in row: c.number_format = num
    for col, w in zip("ABCDEFGHIJKL", (16, 12, 44, 34, 14, 24, 12, 16, 16, 16, 13, 60)): ws3.column_dimensions[col].width = w

    ws4 = wb.create_sheet("Rules relied on")
    ws4.append(["Expense type", "Clause", "Treatment", "Verified against the Act", "By", "Note"])
    for c in ws4[1]: c.font = bold; c.fill = head
    used = {it.expense_type for it in schedule["items"]}
    for code in sorted(used):
        r = rules.get(code)
        if r:
            ws4.append([r.name, r.clause_label or "", TREATMENTS.get(r.treatment, r.treatment), r.verified_at.strftime("%Y-%m-%d") if r.verified_at else "NOT VERIFIED",
                        r.verified_by.name if r.verified_by else "", r.note or ""])
    for col, w in zip("ABCDEF", (50, 14, 52, 24, 20, 70)): ws4.column_dimensions[col].width = w
    ws4.append([]); ws4.append(["Clause references are held as data and must be confirmed against the current Income Tax Act [Chapter 23:06]. Items under unverified rules are marked above."])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return buf


# ----------------------------------------------------------------- routes

def _access(engagement_id):
    from engagements import _ensure_engagement_access
    eng = Engagement.query.get_or_404(engagement_id)
    _ensure_engagement_access(eng)
    return eng


def _senior_only(what):
    if getattr(current_user, "role", None) not in ("partner", "admin"):
        abort(403, description=f"Only a Partner or Admin can {what}.")


def _back(eid):
    return redirect(url_for("prohibited.page", engagement_id=eid))


@prohibited_bp.route("/engagements/<int:engagement_id>/prohibited-deductions")
@login_required
def page(engagement_id):
    eng = _access(engagement_id)
    rules = rules_by_code()
    setup = get_setup(engagement_id)
    sched = build_schedule(engagement_id)
    statement, basis = computation_statement(eng, sched, setup)
    comp = IncomeTaxComputation.query.filter_by(engagement_id=engagement_id).first()
    posted = [l for l in (comp.items if comp else []) if l.source == ENGINE_SOURCE]
    accounts = expense_accounts(engagement_id)
    grouped_rules = OrderedDict()
    for r in sorted(rules.values(), key=lambda r: (r.group or "", r.code)):
        if r.is_active and r.code not in NON_ITEM_CODES:
            grouped_rules.setdefault(r.group or "Other", []).append(r)
    return render_template("prohibited/page.html", engagement=eng, rules=rules, grouped_rules=grouped_rules, setup=setup, sched=sched,
                           statement=statement, basis=basis, accounts=accounts, statuses=TAX_DEDUCTIBILITY_STATUSES,
                           posted=posted, posted_total=sum(l.amount or 0 for l in posted), stale=abs(sum(l.amount or 0 for l in posted) - sched["total_addback"]) > 0.005,
                           is_senior=getattr(current_user, "role", None) in ("partner", "admin"), treatments=TREATMENTS)


@prohibited_bp.route("/engagements/<int:engagement_id>/prohibited-deductions/setup", methods=["POST"])
@login_required
def save_setup(engagement_id):
    _access(engagement_id)
    s = get_setup(engagement_id, create=True)
    f = request.form
    s.pmv_cap = _num(f.get("pmv_cap"))
    s.assoc_fee_a, s.assoc_fee_b, s.assoc_fee_c = _num(f.get("assoc_fee_a")), _num(f.get("assoc_fee_b")), _num(f.get("assoc_fee_c"))
    s.assoc_fee_pretrade = bool(f.get("assoc_fee_pretrade"))
    s.thin_cap_ratio = _num(f.get("thin_cap_ratio"))
    s.related_debt = _num(f.get("related_debt")) or 0.0
    s.qualifying_equity = _num(f.get("qualifying_equity")) or 0.0
    s.related_interest = _num(f.get("related_interest")) or 0.0
    s.net_profit_override = _num(f.get("net_profit_override"))
    s.notes = f.get("notes", "").strip() or None
    reevaluate_engagement(engagement_id)
    db.session.commit()
    flash("Setup saved and every expense re-tested.", "success")
    return _back(engagement_id)


def _fill_item(it, f, rules):
    etype = normalise_type(f.get("expense_type"))
    if etype not in rules or etype in NON_ITEM_CODES:
        return "Choose an expense type."
    desc = f.get("description", "").strip()
    amount = _num(f.get("amount"))
    if not desc or amount is None:
        return "Give the expense a description and an amount."
    it.expense_type, it.description, it.amount = etype, desc[:250], amount
    it.voucher_ref = f.get("voucher_ref", "").strip() or None
    it.entry_date = _date(f.get("entry_date") or "")
    it.charged_to_pl = bool(f.get("charged_to_pl"))
    it.is_insured = bool(f.get("is_insured"))
    it.reimbursed_amount = _num(f.get("reimbursed_amount")) or 0.0
    it.business_necessity = bool(f.get("business_necessity"))
    it.linked_to_exempt = bool(f.get("linked_to_exempt"))
    it.prohibited_percent = _num(f.get("prohibited_percent"))
    it.vehicle_cost = _num(f.get("vehicle_cost"))
    return None


@prohibited_bp.route("/engagements/<int:engagement_id>/prohibited-deductions/items/add", methods=["POST"])
@login_required
def add_item(engagement_id):
    _access(engagement_id)
    rules = rules_by_code()
    it = ProhibitedDeductionItem(engagement_id=engagement_id, source="Manual", created_by_id=current_user.id)
    f = request.form.copy()
    err = _fill_item(it, f, rules)
    if err:
        flash(err, "danger")
        return _back(engagement_id)
    db.session.add(it)
    db.session.flush()
    reevaluate_engagement(engagement_id, rules)    # re-tests every line: the section 16(1)(r) limit is shared by all the fees
    db.session.commit()
    flash(f"Added - {it.status.replace('_', ' ').lower()}" + (f", add back {it.addback_amount:,.2f}" if it.addback_amount else "") + ".", "success")
    return _back(engagement_id)


@prohibited_bp.route("/prohibited-deductions/items/<int:item_id>/update", methods=["POST"])
@login_required
def update_item(item_id):
    it = ProhibitedDeductionItem.query.get_or_404(item_id)
    _access(it.engagement_id)
    rules = rules_by_code()
    err = _fill_item(it, request.form, rules)
    if err:
        flash(err, "danger")
        return _back(it.engagement_id)
    db.session.flush()
    reevaluate_engagement(it.engagement_id, rules)
    db.session.commit()
    flash("Updated.", "success")
    return _back(it.engagement_id)


@prohibited_bp.route("/prohibited-deductions/items/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_item(item_id):
    it = ProhibitedDeductionItem.query.get_or_404(item_id)
    _access(it.engagement_id)
    eid = it.engagement_id
    db.session.delete(it)
    db.session.flush()
    reevaluate_engagement(eid)
    db.session.commit()
    flash("Removed.", "info")
    return _back(eid)


@prohibited_bp.route("/engagements/<int:engagement_id>/prohibited-deductions/mapping", methods=["POST"])
@login_required
def save_mapping(engagement_id):
    _access(engagement_id)
    rules = rules_by_code()
    n = 0
    for acc in expense_accounts(engagement_id):
        val = normalise_type(request.form.get(f"map_{acc.id}"))
        new = val if val in rules and val not in NON_ITEM_CODES else None
        if new != acc.tax_expense_type:
            acc.tax_expense_type = new
            n += 1
    db.session.commit()
    flash(f"Ledger mapping saved ({n} changed).", "success")
    return _back(engagement_id)


@prohibited_bp.route("/engagements/<int:engagement_id>/prohibited-deductions/pull", methods=["POST"])
@login_required
def pull(engagement_id):
    eng = _access(engagement_id)
    mapped = [a for a in expense_accounts(engagement_id) if a.tax_expense_type]
    if not mapped:
        flash("Map at least one expense account to an expense type first.", "danger")
        return _back(engagement_id)
    added, skipped = pull_from_ledger(eng, current_user)
    reevaluate_engagement(engagement_id)
    db.session.commit()
    flash(f"{added} ledger line(s) pulled in and tested" + (f"; {skipped} already there" if skipped else "") + ".", "success")
    return _back(engagement_id)


@prohibited_bp.route("/engagements/<int:engagement_id>/prohibited-deductions/import", methods=["POST"])
@login_required
def import_items(engagement_id):
    eng = _access(engagement_id)
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose a CSV file.", "danger")
        return _back(engagement_id)
    try:
        text = f.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("The file must be a UTF-8 CSV.", "danger")
        return _back(engagement_id)
    added, errors = import_csv(eng, text, current_user)
    reevaluate_engagement(engagement_id)
    db.session.commit()
    flash(f"{added} line(s) imported and tested." + ("" if not errors else " Not imported: " + " ".join(errors[:5]) + (" ..." if len(errors) > 5 else "")),
          "success" if added and not errors else "danger" if not added else "info")
    return _back(engagement_id)


@prohibited_bp.route("/engagements/<int:engagement_id>/prohibited-deductions/post", methods=["POST"])
@login_required
def post(engagement_id):
    eng = _access(engagement_id)
    reevaluate_engagement(engagement_id)
    posted, total = post_to_computation(eng, current_user)
    db.session.commit()
    flash(f"{posted} add-back line(s) totalling {total:,.2f} posted to the Income Tax Computation. Its review and sign-off have been reset.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="income_tax"))


@prohibited_bp.route("/engagements/<int:engagement_id>/prohibited-deductions/export.xlsx")
@login_required
def export_xlsx(engagement_id):
    eng = _access(engagement_id)
    sched = build_schedule(engagement_id)
    statement, _ = computation_statement(eng, sched, get_setup(engagement_id))
    buf = build_xlsx(eng, sched, statement)
    name = f"Section16_Prohibited_Deductions_{eng.client.name}".replace(" ", "_")[:80] + ".xlsx"
    return send_file(buf, as_attachment=True, download_name=name, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@prohibited_bp.route("/engagements/<int:engagement_id>/prohibited-deductions/print")
@login_required
def print_schedule(engagement_id):
    eng = _access(engagement_id)
    rules = rules_by_code()
    setup = get_setup(engagement_id)
    sched = build_schedule(engagement_id)
    statement, basis = computation_statement(eng, sched, setup)
    return render_template("prohibited/print.html", engagement=eng, sched=sched, statement=statement, rules=rules, today=date.today(), treatments=TREATMENTS)


# ------------------------------------------------------------ rules admin

@prohibited_bp.route("/prohibited-deductions/rules")
@login_required
def rules_page():
    rules = rules_by_code()
    grouped = OrderedDict()
    for r in sorted(rules.values(), key=lambda r: (r.group or "", r.code)):
        grouped.setdefault(r.group or "Other", []).append(r)
    return render_template("prohibited/rules.html", grouped=grouped, treatments=TREATMENTS,
                           is_senior=getattr(current_user, "role", None) in ("partner", "admin"))


@prohibited_bp.route("/prohibited-deductions/rules/<int:rule_id>", methods=["POST"])
@login_required
def save_rule(rule_id):
    _senior_only("change a section 16 rule")
    r = ProhibitedDeductionRule.query.get_or_404(rule_id)
    f = request.form
    treatment = f.get("treatment")
    if treatment not in TREATMENTS:
        flash("Choose a treatment.", "danger")
        return redirect(url_for("prohibited.rules_page"))
    changed = (r.clause_label or "") != f.get("clause_label", "").strip() or r.treatment != treatment or bool(f.get("is_active")) != bool(r.is_active)
    r.clause_label = f.get("clause_label", "").strip()[:60]
    r.treatment = treatment
    r.is_active = bool(f.get("is_active"))
    if f.get("note") is not None:
        r.note = f.get("note", "").strip()
    if f.get("verify"):
        r.verified_by_id, r.verified_at = current_user.id, datetime.utcnow()
        r.verified_note = f.get("verified_note", "").strip()[:300] or None
    elif changed:
        r.verified_by_id = r.verified_at = r.verified_note = None   # a changed rule has to be verified again
    reevaluate_all()
    db.session.commit()
    flash("Rule saved; every engagement's expenses were re-tested." + ("" if f.get("verify") or not changed else " It must be marked verified again."), "success")
    return redirect(url_for("prohibited.rules_page"))
