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


def calculate_payslip(basic_salary, allowance_items, deduction_items, settings, bands):
    """Returns a dict of every computed payslip figure. `allowance_items`
    and `deduction_items` are lists of objects with .amount (and, for
    allowances, .taxable) - PayslipItem rows, or anything shaped like one.
    Never touches the database - callers decide whether/how to save the
    result, so a "preview before saving" recalculation is just as cheap as
    a real one."""
    basic_salary = basic_salary or 0.0
    taxable_allowances = sum((i.amount or 0.0) for i in allowance_items if getattr(i, "taxable", True))
    non_taxable_allowances = sum((i.amount or 0.0) for i in allowance_items if not getattr(i, "taxable", True))
    allowances_total = taxable_allowances + non_taxable_allowances

    taxable_income = max(0.0, basic_salary + taxable_allowances)
    gross_pay = basic_salary + allowances_total

    paye_tax = calculate_paye(taxable_income, bands)
    aids_levy = round(paye_tax * (settings.aids_levy_pct or 0.0) / 100.0, 2)

    ceiling = settings.nssa_insurable_ceiling
    nssa_base = min(gross_pay, ceiling) if ceiling else gross_pay
    nssa_employee = round(nssa_base * (settings.nssa_employee_pct or 0.0) / 100.0, 2)
    nssa_employer = round(nssa_base * (settings.nssa_employer_pct or 0.0) / 100.0, 2)

    other_deductions_total = round(sum((i.amount or 0.0) for i in deduction_items), 2)

    net_pay = round(gross_pay - paye_tax - aids_levy - nssa_employee - other_deductions_total, 2)

    return {
        "basic_salary": round(basic_salary, 2),
        "allowances_total": round(allowances_total, 2),
        "taxable_income": round(taxable_income, 2),
        "gross_pay": round(gross_pay, 2),
        "paye_tax": paye_tax,
        "aids_levy": aids_levy,
        "nssa_employee": nssa_employee,
        "nssa_employer": nssa_employer,
        "other_deductions_total": other_deductions_total,
        "net_pay": net_pay,
    }
