"""The Payroll tax calculation engine.

Deliberately data-driven: every rate (PAYE bands, AIDS levy %, NSSA
employee/employer % and insurable ceiling) is read from the firm's own
editable PayrollTaxSettings/PayrollTaxBand records (see models.py) rather
than hard-coded here, because current Zimbabwean rates could not be
reliably confirmed from public sources when this module was built - see
PAYROLL_TAX_CAVEAT in models.py. This module only implements the
arithmetic; it never invents a rate the firm hasn't entered.

Kept separate from payroll.py (the routes) the same way financials.py is
kept separate from engagements.py elsewhere in this app - so the same
numbers used to populate a payslip on screen are exactly what gets written
into the downloaded Word/PDF document.

ZIMRA's own published PAYE method (zimra.co.zw) is followed step for step:
  1. Gross income = basic salary + allowances (+ Exempt Income items - see
     below).
  2. Less exempt income (PayslipItem category "Exempt Income", e.g. a bonus
     exemption) -> Income.
  3. Less allowable deductions, e.g. pension/NSSA contributions -> Taxable
     Income.
  4. Apply the tax tables (calculate_paye) -> PAYE before credits.
  5. Less tax credits (PayslipItem category "Tax Credit", e.g. an elderly/
     blind/disabled person's or medical expenses credit) -> PAYE payable.
  6. Plus 3% AIDS Levy on the PAYE payable (after credits).

The employee's own NSSA contribution is one of step 3's "allowable
deductions" - it reduces taxable income BEFORE the tax tables are applied,
not just a deduction taken from net pay after tax. This module deducts it
in full (already capped via PayrollTaxSettings.nssa_insurable_ceiling, the
same statutory ceiling that caps the contribution itself - no separate
limit is applied on top of that).

Exempt Income items (step 2) count toward gross/net pay exactly like an
Allowance, but are NEVER part of taxable income, regardless of their own
`taxable` flag. Tax Credit items (step 5) are the opposite: they never
touch gross pay or taxable income, only PAYE payable itself (floored at
zero - a credit can't turn into a refund here). Neither category's exact
CURRENT statutory amount could be reliably confirmed when this was built -
see PAYROLL_TAX_CAVEAT in models.py - so nothing is pre-filled; the firm
enters them per employee/payslip once confirmed against ZIMRA.
"""


def calculate_paye(taxable_income, bands):
    """Progressive PAYE across the given PayrollTaxBand rows. Each band
    taxes the slice of income between its own lower and upper bound (its
    upper bound open-ended when `upper` is None) at that band's rate - the
    standard marginal-rate approach, so bands should be entered exactly as
    published (e.g. "0-upper1 @ 0%, upper1-upper2 @ 20%, ..."), not as
    cumulative/already-blended figures."""
    taxable_income = taxable_income or 0.0
    if taxable_income <= 0 or not bands:
        return 0.0
    ordered = sorted(bands, key=lambda b: b.lower or 0.0)
    tax = 0.0
    for band in ordered:
        lower = band.lower or 0.0
        if taxable_income <= lower:
            break
        upper = band.upper
        top = min(taxable_income, upper) if upper is not None else taxable_income
        portion = max(0.0, top - lower)
        tax += portion * (band.rate_pct or 0.0) / 100.0
    return round(tax, 2)


def calculate_payslip(basic_salary, items, settings, bands):
    """Returns a dict of every computed payslip figure. `items` is a flat
    list of PayslipItem rows (or anything shaped like one: .category,
    .amount, .taxable) for this one payslip, covering all four
    PAYSLIP_ITEM_CATEGORIES - filtered internally by category below, so
    callers just pass `payslip.items` (or `[]` for a brand-new payslip).
    Never touches the database - callers decide whether/how to save the
    result, so a "preview before saving" recalculation is just as cheap as
    a real one."""
    basic_salary = basic_salary or 0.0
    allowance_items = [i for i in items if i.category == "Allowance"]
    deduction_items = [i for i in items if i.category == "Deduction"]
    exempt_items = [i for i in items if i.category == "Exempt Income"]
    credit_items = [i for i in items if i.category == "Tax Credit"]

    taxable_allowances = sum((i.amount or 0.0) for i in allowance_items if getattr(i, "taxable", True))
    non_taxable_allowances = sum((i.amount or 0.0) for i in allowance_items if not getattr(i, "taxable", True))
    allowances_total = taxable_allowances + non_taxable_allowances
    # Exempt Income counts toward gross pay (it's cash the employee actually
    # receives) but is carved out of taxable income below regardless of its
    # own `taxable` flag - see module docstring, step 2.
    exempt_income_total = round(sum((i.amount or 0.0) for i in exempt_items), 2)
    gross_pay = basic_salary + allowances_total + exempt_income_total

    # NSSA employee contribution is computed FIRST - it's needed both for
    # net pay (as always) AND, per ZIMRA's own PAYE method (see module
    # docstring, step 3), as an allowable deduction that reduces taxable
    # income BEFORE the tax tables are applied - not just a deduction taken
    # from net pay after tax.
    ceiling = settings.nssa_insurable_ceiling
    nssa_base = min(gross_pay, ceiling) if ceiling else gross_pay
    nssa_employee = round(nssa_base * (settings.nssa_employee_pct or 0.0) / 100.0, 2)
    nssa_employer = round(nssa_base * (settings.nssa_employer_pct or 0.0) / 100.0, 2)

    taxable_income = max(0.0, basic_salary + taxable_allowances - nssa_employee - exempt_income_total)

    paye_before_credits = calculate_paye(taxable_income, bands)
    tax_credits_total = round(sum((i.amount or 0.0) for i in credit_items), 2)
    paye_tax = round(max(0.0, paye_before_credits - tax_credits_total), 2)
    aids_levy = round(paye_tax * (settings.aids_levy_pct or 0.0) / 100.0, 2)

    other_deductions_total = round(sum((i.amount or 0.0) for i in deduction_items), 2)

    net_pay = round(gross_pay - paye_tax - aids_levy - nssa_employee - other_deductions_total, 2)

    return {
        "basic_salary": round(basic_salary, 2),
        "allowances_total": round(allowances_total, 2),
        "exempt_income_total": exempt_income_total,
        "taxable_income": round(taxable_income, 2),
        "gross_pay": round(gross_pay, 2),
        "paye_before_credits": paye_before_credits,
        "tax_credits_total": tax_credits_total,
        "paye_tax": paye_tax,
        "aids_levy": aids_levy,
        "nssa_employee": nssa_employee,
        "nssa_employer": nssa_employer,
        "other_deductions_total": other_deductions_total,
        "net_pay": net_pay,
    }
