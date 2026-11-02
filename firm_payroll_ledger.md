# Payroll in Firm Financials (d56)

Neverlank's own staff payroll now feeds the firm's ledger and financial statements. Nothing is keyed twice and nothing is stored: like invoices and bills, the postings are derived each time the ledger or the statements are built (`firm_payroll.py`).

## What posts

A **finalized internal** pay period (client payrolls never touch the firm's books), dated its pay date (else period end):

| | Dr | Cr |
|---|---|---|
| Gross pay of all payslips | 6300 Salaries and wages | |
| Employer NSSA + APWCS | 6310 Employer NSSA and APWCS contributions | |
| PAYE + AIDS levy | | 2041 PAYE and AIDS levy payable (payroll) |
| Employees' NSSA + employer's NSSA + APWCS | | 2042 NSSA contributions payable (payroll) |
| Deductions taken off pay | | 2044 Other payroll deductions payable |
| Net pay | | 2043 Net salaries payable (payroll) |

Net pay is taken as gross less every deduction, so the posting always balances; if the payslips' stored net pay was typed by hand and differs, a warning says so. Only totals are posted (no names or individual pay), because the ledger is visible to people who cannot see payslips (payroll stays Admin-only). Reopening a period (back to Draft) takes it out again.

New system accounts: PAY_SAL, PAY_ER, PAY_ZIMRA, PAY_NSSA, PAY_NET, PAY_DED. They are control accounts (a manual journal to them shows a warning). On existing installs they are created automatically.

## Paying what is owed (Cash Book)

Money-out headings that clear the payables (cash-only, no expense):

- **Salaries paid** -> Net salaries payable
- **PAYE and other taxes paid** -> PAYE and AIDS levy payable
- **NSSA contributions paid** (new heading) -> NSSA contributions payable

"Salaries paid" and "NSSA contributions paid" are offered again on the new-entry form (under "Other payment"). The expense itself comes from the payroll, so salaries must not also be filed under a Salaries expense category.

## Medical aid bills (payroll_medical.py)

A bill posted from a posted period no longer debits an expense account: it debits **Other payroll deductions payable**. The employer's share is already in gross pay (a payslip allowance) and the employees' share is money taken off pay, so a second expense would double count. The statements remove those bills from expenses the same way. The bill is still owed to the society in Trade payables and paid from the supplier's page.

## Start date, so history is not changed

The link starts with the earliest finalized period. **Payroll Tax Settings > "Firm Financials: start posting payroll from"** (`PayrollTaxSettings.ledger_from`) moves that later. Periods before the start, and cash book entries under the three headings dated before the first posted period, behave as before (clearing account / allocation).

**Salary payments already filed in the Expense register** (a bill under a category with "salar", "wage" or "payroll" in its name, dated from the start) are not deleted and not a second expense: they are treated as payments of net salaries. The bill debits *Net salaries payable* (instead of an expense account) and credits Trade payables, so paying it from the cash book/supplier page clears the payable. The ledger and statements show an information note listing how many bills and the total. If Net salaries payable is left in debit, a bill was more than net pay: file that one under another category. (Journals made earlier for the same salaries by the clearing-account allocation are not detected; reverse them.)

## Statements

`firm_payroll.statement_items(start, end, zwg_rate)` is called from `firm_statements.build_column`: expenses are the periods dated inside the year; the payables are everything posted to the year-end less what the Cash Book paid to it (the statement and the ledger agree line by line; `reconcile` shows 0 differences). Negative balances (overpaid) stay inside the liability line. A ZWG period with no rate for the year-end is left out with a warning.

## Where to see it

- Payroll > a period: card **In Firm Financials** (the six postings, or why it is not posted).
- Firm Finance > Ledger / Trial balance / Reconcile: the six accounts and the "Payroll" source.
- Firm Finance > Statements: Salaries and wages and the employer contributions under administrative expenses; the payables as their own lines under other current liabilities.

## Tests

`smoke_test_payroll_ledger.py`: draft/client/out-of-range periods post nothing; postings and balances; no names in the ledger; cash book clearing; medical aid bill clears deductions; statements agree with the ledger; double-count warning; start date; reopening; net pay typed by hand; ZWG without a rate; partner can see the accounts but not the payslips.

## Not done / for later

- Per-employee or per-department cost reporting (the ledger carries totals only, deliberately).
- Posting the payment from the period page (payments are entered in the Cash Book).
- Leave pay, bonuses and gratuity provisions.

## Cash book (also in d56)

- The new-entry form has a **Payroll** group: *Salaries paid - clears Net salaries payable*, *PAYE and other taxes paid - clears PAYE and AIDS levy payable*, *NSSA contributions paid - clears NSSA contributions payable* (not a supplier, not an expense).
- Manual entries have an **Edit** button; **transfers can now be edited** too (both legs change together; a transfer cannot be turned into a payment). Lines the app records itself (receipts, advances, paid bills) show **Edit at source**, because they are corrected where they were recorded.

## "Paid via payroll" in the Expense register and analytics

A bill the payroll already carries (a salary payment filed under a salary/wage/payroll category from the start date, or a medical aid bill posted from payroll) is **not an expense; it is a payment of a liability** (Net salaries payable, or Other payroll deductions payable). So:

- the Expense register tags it **Paid via payroll** with "Not an expense: a payment of Net salaries payable", and leaves it out of the register total (the header says how many);
- the Cash Book shows it as a payroll payment (see above);
- the Analytics expense totals, monthly series and category split leave it out and use the payroll's own cost instead (Salaries and wages (payroll), Employer NSSA and APWCS (payroll)); creditors still show anything owed on the bill;
- employees are **not** suppliers: salaries are a payroll liability, not Trade payables.
