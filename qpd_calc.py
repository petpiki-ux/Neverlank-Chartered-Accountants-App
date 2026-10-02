"""QPD (Quarterly Payment Date / provisional income tax) calculation
engine, kept separate from qpd.py the same way payroll_calc.py is kept
separate from payroll.py and financials.py from engagements.py - so the
maths can be exercised in isolation without Flask/db.

This module only ever does the VAT-turnover-to-margin-estimate step and
the "split an annual tax charge into instalments" step. Everything else a
QPD estimate needs - annualizing a Trial Balance's profit before tax, then
reconciling it to taxable income and a tax charge via add-backs/deductions
- is NOT reimplemented here: it reuses financials.compute_totals(),
financials.build_income_statement() and financials.build_income_tax_
computation() directly (see models.QPDEstimate's module comment), the same
engine an audited engagement's own Income Tax Computation uses, so the two
never drift apart.
"""


def compute_taxable_income_from_vat(annualized_turnover, net_margin_pct):
    """Turnover is not profit, so this is inherently a rougher estimate
    than the Trial Balance method - a preparer-entered net margin % is the
    only way to get from one to the other without a TB. Never negative."""
    return round(max(0.0, (annualized_turnover or 0.0) * (net_margin_pct or 0.0) / 100.0), 2)


def build_instalments(annual_tax_charge, instalment_rates):
    """Given the firm's current QPDInstalmentRate rows (any order) and an
    annual tax charge, returns a list of dicts - one per instalment, in
    `order` - each instalment's own amount being its cumulative_pct minus
    the previous instalment's cumulative_pct, of the SAME annual_tax_charge.
    The final instalment's amount is the remainder (annual_tax_charge minus
    every earlier instalment already computed) rather than its own
    independently-rounded share, so the instalments always sum to exactly
    annual_tax_charge regardless of rounding - never a cent adrift by the
    last instalment.

    `instalment_rates`: iterable of objects/rows with .label, .due_month,
    .due_day, .cumulative_pct, .order (duck-typed, so a QPDInstalmentRate
    query result works directly)."""
    annual_tax_charge = annual_tax_charge or 0.0
    ordered = sorted(instalment_rates, key=lambda r: r.order)
    instalments = []
    prev_cumulative_pct = 0.0
    running_total = 0.0
    for i, rate in enumerate(ordered):
        is_last = (i == len(ordered) - 1)
        if is_last:
            amount = round(annual_tax_charge - running_total, 2)
        else:
            amount = round(annual_tax_charge * (rate.cumulative_pct - prev_cumulative_pct) / 100.0, 2)
        running_total += amount
        instalments.append({
            "label": rate.label,
            "due_month": rate.due_month,
            "due_day": rate.due_day,
            "cumulative_pct": rate.cumulative_pct,
            "amount": amount,
            "order": rate.order,
        })
        prev_cumulative_pct = rate.cumulative_pct
    return instalments
