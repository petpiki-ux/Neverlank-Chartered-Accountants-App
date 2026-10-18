"""The firm's general ledger: double entry for Neverlank itself (Finance > Firm Finance > Ledger).

Two kinds of entry make the ledger
----------------------------------
* DERIVED postings - never stored, never keyed twice. Every time a ledger page is opened the app turns what it already
  records into balanced debit/credit postings:

    invoice issued            Dr Trade receivables        Cr Fees (by service) and VAT control
    credit note               Dr Fees and VAT control     Cr Trade receivables
    receipt / bank payment    Dr Bank (cash book account) Cr Trade receivables
    advance received          Dr Bank                     Cr Customer advances
    advance applied           Dr Customer advances        Cr Trade receivables
    expense bill              Dr Expense (by category)    Cr Trade payables
    expense paid              Dr Trade payables           Cr Bank
    cash book entries         Dr/Cr Bank against tax payable, VAT control, a transfer clearing account or "to be allocated"
    depreciation, disposals   Dr Depreciation / Cr Accumulated depreciation; disposals clear cost and depreciation
    income tax and deferred   Dr Tax expense Cr Tax payable / Deferred tax (from the Tax pages)
    figures entered on a      posted to the account mapped to their statement line, against the clearing account
    year-end                  "Opening balances and entered figures - to be journalised"

  Depreciation, disposals and tax are posted on each year-end date. A transaction before the Cash Book started, or one in
  no Cash Book account, is posted against a clearing account instead of a bank account, so the ledger always balances and
  the clearing accounts show exactly what still needs a proper journal.

* MANUAL JOURNALS (FirmJournal) - stored, saved as a draft, posted once they balance, never edited after posting (reverse
  instead), refused in a closed period. Posted journals flow into the financial statements through the statement line
  each account is mapped to (journal_statement_items, called from firm_statements.build_column).

The trial balance, the ledger per account and the reconciliation of the ledger to the statements are computed from these
postings. ZWG items are translated at the ZWG-per-USD rate of the year-end that covers their date; an item with no rate is
left out and a warning says so, as in the statements.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta

from extensions import db
from models import (
    CashAccount, ClientAdvance, Expense, FirmAccount, FirmJournal, FirmJournalLine, FirmPeriodLock, FirmYearEnd, Invoice,
    Receipt, INVOICE_CURRENCIES, FIRM_ACCOUNT_TYPES,
)
import analytics
import cashbook
import financials

CAT = {c[0]: c for c in financials.FS_CATEGORIES if c[0] != "excluded"}
EXCLUDED = "excluded"
TAX_CODES = ("income_tax_expense", "current_tax_payable", "deferred_tax_asset", "deferred_tax_liability")
CENT = 0.005


def r2(v):
    return round(float(v or 0.0), 2)


# ====================================================================== the starter chart

# (code, name, type, statement line, system key, note). A system key marks an account the posting engine writes to: it can
# be renamed and switched off (if unused) but its statement line is fixed, because the statements derive that figure.
STARTER_CHART = [
    ("1100", "Trade receivables - clients", "Asset", "trade_receivables", "AR", "Fees invoiced and not yet collected (including VAT). Posted by the app from invoices, credit notes and receipts."),
    ("1110", "Other receivables", "Asset", "trade_receivables", None, None),
    ("1120", "Prepayments", "Asset", "other_current_assets", None, None),
    ("1130", "Staff loans and advances", "Asset", "other_current_assets", None, None),
    ("1140", "VAT input recoverable", "Asset", "other_current_assets", None, None),
    ("1150", "Deposits paid", "Asset", "other_current_assets", None, None),
    ("1200", "Inventory - stationery and consumables", "Asset", "inventories", None, None),
    ("1500", "Property, plant and equipment - cost", "Asset", "ppe", "PPE_COST", "Posted by the app from the Asset register."),
    ("1510", "Property, plant and equipment - accumulated depreciation", "Asset", "ppe", "PPE_ACCDEP", "Posted by the app from the Asset register."),
    ("1600", "Intangible assets - cost", "Asset", "intangible_assets", None, None),
    ("1610", "Intangible assets - accumulated amortisation", "Asset", "intangible_assets", None, None),
    ("1700", "Long-term investments", "Asset", "long_term_investments", None, None),
    ("1800", "Deferred tax asset", "Asset", "deferred_tax_asset", "DTA", "Posted by the app from the Tax pages."),
    ("1900", "Other non-current assets", "Asset", "other_noncurrent_assets", None, None),
    ("2000", "Trade payables - supplier bills", "Liability", "trade_payables", "AP", "Expense-register bills not yet paid. Posted by the app."),
    ("2010", "Accruals", "Liability", "trade_payables", None, None),
    ("2020", "Customer advances", "Liability", "other_current_liabilities", "ADV", "Money received before invoicing. Posted by the app."),
    ("2030", "VAT payable (per VAT return)", "Liability", "other_current_liabilities", None, None),
    ("2040", "PAYE and other statutory deductions payable", "Liability", "other_current_liabilities", None, None),
    ("2050", "Other payables", "Liability", "other_current_liabilities", None, None),
    ("2100", "Bank overdraft and short-term loans", "Liability", "short_term_borrowings", None, None),
    ("2150", "Short-term provisions", "Liability", "short_term_provisions", None, None),
    ("2200", "Income tax payable", "Liability", "current_tax_payable", "TAX_PAY", "Posted by the app from the Tax pages and the cash book (a debit balance is shown as tax prepaid)."),
    ("2300", "Long-term loans", "Liability", "long_term_borrowings", None, None),
    ("2350", "Long-term provisions", "Liability", "long_term_provisions", None, None),
    ("2400", "Deferred tax liability", "Liability", "deferred_tax_liability", "DTL", "Posted by the app from the Tax pages."),
    ("2500", "Other non-current liabilities", "Liability", "other_noncurrent_liabilities", None, None),
    ("3000", "Share capital", "Equity", "share_capital", None, None),
    ("3010", "Share premium", "Equity", "share_premium", None, None),
    ("3100", "Retained earnings", "Equity", "retained_earnings", "RE", "Opening retained earnings. Each year's profit is added to it automatically in the trial balance."),
    ("3200", "Other reserves", "Equity", "other_reserves", None, None),
    ("3300", "Dividends declared and paid", "Equity", "dividends_paid", None, None),
    ("4000", "Professional fees - other", "Income", "revenue", None, "For journals. Invoiced fees are posted to one account per service, created automatically."),
    ("4500", "Other income", "Income", "other_income", None, None),
    ("4510", "Interest received", "Income", "other_income", None, None),
    ("4800", "Profit / (loss) on disposal of property, plant and equipment", "Income", "other_income", "DISP_GL", "Posted by the app from the Asset register. A loss is a debit balance and is shown under other expenses."),
    ("4900", "Other comprehensive income", "Income", "oci_items", None, None),
    ("5000", "Cost of sales", "Expense", "cost_of_sales", None, None),
    ("5900", "Expenses - category not in the chart", "Expense", "admin_expenses", "EXP_OTHER", "Where an expense whose category has no account of its own is posted."),
    ("6100", "Distribution costs", "Expense", "distribution_costs", None, None),
    ("6200", "Provision for doubtful debts", "Expense", "admin_expenses", None, None),
    ("6900", "Depreciation", "Expense", "depreciation_amortisation", "DEPR", "Posted by the app from the Asset register."),
    ("6910", "Amortisation of intangible assets", "Expense", "depreciation_amortisation", None, None),
    ("6960", "Other expenses", "Expense", "other_expenses", None, None),
    ("7000", "Bank charges", "Expense", "finance_costs", None, None),
    ("7010", "Interest on loans and overdrafts", "Expense", "finance_costs", None, None),
    ("8000", "Income tax - current", "Expense", "income_tax_expense", "TAX_EXP_CUR", "Posted by the app from the Tax pages."),
    ("8010", "Income tax - deferred", "Expense", "income_tax_expense", "TAX_EXP_DEF", "Posted by the app from the Tax pages."),
    ("9100", "Opening balances and entered figures - to be journalised", "Equity", EXCLUDED, "SUSP_OPEN",
     "Clearing account. Opening cash balances, figures typed on the year-end pages and cash before the Cash Book started are posted here. Journal them to their real accounts, then remove the entered figure."),
    ("9110", "Cash book entries - to be allocated", "Liability", EXCLUDED, "SUSP_CASH",
     "Clearing account. Loans, owner contributions, dividends, salaries and other cash book entries the app has no other record of are posted here until a journal allocates them."),
    ("9120", "Asset purchases and disposals - funding to be matched", "Liability", EXCLUDED, "SUSP_ASSET",
     "Clearing account. The cost of an asset is posted here; clear it with a journal against the payment (the cash book entry) or the supplier."),
    ("9130", "Transfers between cash accounts - clearing", "Liability", EXCLUDED, "CLR_TRANSFER",
     "Clearing account. A transfer between two cash book accounts passes through here and nets to nil."),
    ("9140", "VAT charged on invoices - control (memo)", "Liability", EXCLUDED, "VAT_CTRL",
     "Memo account: VAT on invoices issued less VAT paid to ZIMRA, per the app's records. The statements use the VAT payable you enter, so this account is outside them."),
]
SYSTEM_KEYS = {row[4] for row in STARTER_CHART if row[4]}
# Accounts the statements derive themselves: a journal to one of these is a deliberate adjustment, so it carries a warning.
CONTROL_KEYS = {"AR", "AP", "ADV", "PPE_COST", "PPE_ACCDEP", "DEPR", "VAT_CTRL"}
# Accounts only the app may post to (their statement figure is calculated, or one account feeds two statement lines).
JOURNAL_REFUSED_KEYS = {"TAX_PAY", "TAX_EXP_CUR", "TAX_EXP_DEF", "DTL", "DTA", "DISP_GL"}
SUSPENSE_KEYS = ("SUSP_OPEN", "SUSP_CASH", "SUSP_ASSET", "CLR_TRANSFER", "VAT_CTRL")

CASH_RANGE, REV_RANGE, EXP_RANGE = (1010, 1099), (4100, 4499), (5100, 5899)


def _next_code(start, stop):
    used = {a.code for a in FirmAccount.query.all()}
    c = start
    while c <= stop:
        if str(c) not in used:
            return str(c)
        c += 10
    c = start
    while c <= stop:
        if str(c) not in used:
            return str(c)
        c += 1
    n = stop + 1
    while str(n) in used:
        n += 1
    return str(n)


def services_in_use():
    """Service names of issued invoices, as the analytics label them."""
    from models import Engagement
    names = set()
    q = (db.session.query(Engagement.type).join(Invoice, Invoice.engagement_id == Engagement.id)
         .filter(Invoice.status.in_(["Sent", "Paid"])).distinct())
    names.update(t for (t,) in q.all() if t)
    if Invoice.query.filter(Invoice.status.in_(["Sent", "Paid"]), Invoice.engagement_id.is_(None)).first():
        names.add(analytics.NO_ENGAGEMENT_LABEL)
    return names


def ensure_chart(services=None, categories=None):
    """Create the starter chart on first use, and the accounts the engine needs: one per cash book account, per expense
    category and per service invoiced. Idempotent; commits only when something was added."""
    from finance import category_names
    changed = False
    if FirmAccount.query.count() == 0:
        for i, (code, name, typ, cat, key, note) in enumerate(STARTER_CHART):
            db.session.add(FirmAccount(code=code, name=name, account_type=typ, fs_category=cat, system_key=key, description=note, order=i))
        changed = True
        db.session.flush()
    have = {a.system_key: a for a in FirmAccount.query.filter(FirmAccount.system_key.isnot(None)).all()}
    taken = {a.code for a in FirmAccount.query.all()}
    for i, (code, name, typ, cat, key, note) in enumerate(STARTER_CHART):
        if key and key not in have:
            use = code if code not in taken else _next_code(int(code), int(code) + 9)
            a = FirmAccount(code=use, name=name, account_type=typ, fs_category=cat, system_key=key, description=note, order=i)
            db.session.add(a)
            db.session.flush()
            have[key] = a
            taken.add(use)
            changed = True

    def add_dynamic(key, name, typ, cat, rng, note):
        nonlocal changed
        if key in have:
            return
        a = FirmAccount(code=_next_code(*rng), name=name[:120], account_type=typ, fs_category=cat, system_key=key, description=note,
                        order=int(rng[0]))
        db.session.add(a)
        db.session.flush()
        have[key] = a
        changed = True

    for ca in CashAccount.query.order_by(CashAccount.order, CashAccount.id).all():
        add_dynamic(f"CASH:{ca.id}", ca.name, "Asset", "cash", CASH_RANGE, "A cash book account. Posted by the app; use the Cash Book to record money in or out.")
    cats = set(category_names(active_only=False))
    cats.update(categories or ())
    for cat in sorted(cats):
        add_dynamic(f"EXP:{cat}", cat, "Expense", "admin_expenses", EXP_RANGE, "An Expense-register category. Posted by the app from bills.")
    svcs = set(services if services is not None else services_in_use())
    for svc in sorted(svcs):
        add_dynamic(f"REV:{svc}", f"Professional fees - {svc}", "Income", "revenue", REV_RANGE, "Fees invoiced for this service. Posted by the app from invoices.")
    # a renamed cash book account / category keeps its ledger account name in step unless the user renamed the ledger account
    for ca in CashAccount.query.all():
        a = have.get(f"CASH:{ca.id}")
        if a is not None and a.name != ca.name and a.description and a.description.startswith("A cash book account"):
            a.name = ca.name[:120]
            changed = True
    if changed:
        db.session.commit()
    return changed


# ====================================================================== period lock

def current_lock():
    """The date the books are closed through (journals dated on or before it are refused), or None."""
    row = FirmPeriodLock.query.order_by(FirmPeriodLock.id.desc()).first()
    return row.locked_through if row else None


def lock_history(limit=20):
    return FirmPeriodLock.query.order_by(FirmPeriodLock.id.desc()).limit(limit).all()


def set_lock(locked_through, note, user):
    db.session.add(FirmPeriodLock(locked_through=locked_through, note=(note or "")[:250], set_by_id=getattr(user, "id", None)))


# ====================================================================== journals

def year_end_for(day, year_ends=None):
    """The year-end whose period a date falls in (the first year-end on or after it), or None after the last one."""
    for ye in sorted(year_ends if year_ends is not None else FirmYearEnd.query.all(), key=lambda y: y.period_end):
        if day <= ye.period_end:
            return ye
    return None


def next_journal_number(day=None):
    year = (day or date.today()).year
    prefix = f"JE-{year}-"
    top = 0
    for (num,) in db.session.query(FirmJournal.number).filter(FirmJournal.number.like(prefix + "%")).all():
        try:
            top = max(top, int(num.rsplit("-", 1)[1]))
        except ValueError:
            pass
    n = top + 1
    while FirmJournal.query.filter_by(number=f"{prefix}{n:04d}").first():
        n += 1
    return f"{prefix}{n:04d}"


def line_refusal(account, day, year_ends=None):
    """Why a journal may not use this account on this date (a sentence), or None."""
    if account is None:
        return "Choose an account."
    if account.fs_category == "cash":
        return f"{account.label} is a cash or bank account. Record money in or out in the Cash Book so the cash book and the ledger stay in step."
    if account.system_key in JOURNAL_REFUSED_KEYS:
        return f"{account.label} is posted by the app (from the Tax pages or the Asset register); journal to another account."
    if account.fs_category in TAX_CODES:
        ye = year_end_for(day, year_ends)
        if ye is not None and not getattr(ye, "tax_manual", False):
            return (f"{account.label} feeds a tax line, and the year-end {ye.period_end.strftime('%d %b %Y')} takes its tax from the Tax pages. "
                    "Switch that year-end to manual tax first, or adjust the tax on the Tax pages.")
    return None


def journal_problems(j, for_post=True, year_ends=None):
    """Everything that stops this journal being saved (draft) or posted. Returns a list of sentences."""
    problems = []
    if not j.journal_date:
        problems.append("Enter the journal date.")
    lock = current_lock()
    if j.journal_date and lock and j.journal_date <= lock:
        problems.append(f"The books are closed through {lock.strftime('%d %b %Y')}; a journal cannot be dated on or before that day.")
    if not (j.description or "").strip():
        problems.append("Enter a description.")
    lines = list(j.lines)
    if for_post and len(lines) < 2:
        problems.append("A journal needs at least two lines.")
    seen = set()
    for n, ln in enumerate(lines, 1):
        dr, cr = r2(ln.debit), r2(ln.credit)
        if dr < 0 or cr < 0:
            problems.append(f"Line {n}: amounts cannot be negative.")
        elif dr > 0 and cr > 0:
            problems.append(f"Line {n}: enter a debit or a credit, not both.")
        elif dr == 0 and cr == 0:
            problems.append(f"Line {n}: enter an amount.")
        acct = ln.account if ln.account_id else None
        if acct is None:
            problems.append(f"Line {n}: choose an account.")
            continue
        if for_post and not acct.is_active:
            problems.append(f"Line {n}: {acct.label} is switched off.")
        why = line_refusal(acct, j.journal_date, year_ends) if j.journal_date else None
        if why and (acct.id, why) not in seen:
            seen.add((acct.id, why))
            problems.append(f"Line {n}: {why}")
    if for_post and abs(j.total_debit - j.total_credit) >= CENT:
        problems.append(f"The journal does not balance: debits {j.total_debit:,.2f}, credits {j.total_credit:,.2f} (difference {abs(j.total_debit - j.total_credit):,.2f}).")
    return problems


def journal_warnings(j):
    """Soft cautions shown on the journal (it can still be posted)."""
    out = []
    names = sorted({ln.account.label for ln in j.lines if ln.account and ln.account.system_key in CONTROL_KEYS})
    if names:
        out.append("This journal adjusts account(s) the app fills from its own records (" + "; ".join(names) +
                   "). The adjustment is added to the statements, but the client, supplier or asset records will not show it.")
    return out


def post_journal(j, user):
    j.status = "posted"
    j.posted_by_id = getattr(user, "id", None)
    j.posted_at = datetime.utcnow()


def make_reversal(j, on, user):
    """A new POSTED journal that undoes j on date `on`. Caller checks the problems and commits."""
    rev = FirmJournal(number=next_journal_number(on), journal_date=on, description=f"Reversal of {j.number}: {j.description}"[:250],
                      reference=j.number, status="posted", reverses_id=j.id, created_by_id=getattr(user, "id", None),
                      posted_by_id=getattr(user, "id", None), posted_at=datetime.utcnow())
    for i, ln in enumerate(j.lines):
        rev.lines.append(FirmJournalLine(account_id=ln.account_id, account=ln.account, debit=ln.credit or 0.0, credit=ln.debit or 0.0,
                                         narration=ln.narration, order=i))
    return rev


# ====================================================================== journals -> statements

def _natural(cat, dr, cr):
    return (dr - cr) if CAT[cat][4] == "debit" else (cr - dr)


def journal_statement_items(ye, start, end, first, from_book, tax_auto):
    """What posted journals add to one year-end column of the statements: ([(fs_category, label, amount)], warnings).
    Balance-sheet accounts count cumulatively to the year-end; income and expense accounts and dividends count only for
    journals dated inside the period; retained earnings counts journals inside the period (all earlier ones for the first
    year-end, which has nothing before it)."""
    rows = (db.session.query(FirmJournalLine, FirmJournal, FirmAccount)
            .join(FirmJournal, FirmJournal.id == FirmJournalLine.journal_id)
            .join(FirmAccount, FirmAccount.id == FirmJournalLine.account_id)
            .filter(FirmJournal.status == "posted", FirmJournal.journal_date <= end).all())
    acc = defaultdict(lambda: [0.0, 0.0])
    ignored_cash = ignored_tax = before_first = 0
    for ln, j, a in rows:
        cat = a.fs_category
        if not cat or cat == EXCLUDED or cat not in CAT:
            continue
        stmt = CAT[cat][2]
        if cat == "cash" and from_book:
            ignored_cash += 1
            continue
        if cat in TAX_CODES and tax_auto:
            ignored_tax += 1
            continue
        d = j.journal_date
        if stmt == "SFP" and cat != "retained_earnings":
            ok = True
        elif cat == "retained_earnings":
            ok = True if first else d >= start
        else:
            ok = d >= start
            if not ok and first:
                before_first += 1
        if ok:
            acc[(cat, a.name)][0] += ln.debit or 0.0
            acc[(cat, a.name)][1] += ln.credit or 0.0
    warnings = []
    if ignored_cash:
        warnings.append(f"{ignored_cash} journal line(s) on cash accounts were ignored: cash comes from the Cash Book.")
    if ignored_tax:
        warnings.append(f"{ignored_tax} journal line(s) on tax accounts were ignored: tax comes from the Tax pages for this year-end.")
    if before_first:
        warnings.append(f"{before_first} journal line(s) on income, expense or dividend accounts are dated before this first period began, "
                        "so they are not in these statements. Put them in the opening retained earnings.")
    items = []
    for (cat, name), (dr, cr) in sorted(acc.items()):
        amt = r2(_natural(cat, dr, cr))
        if abs(amt) >= CENT:
            items.append((cat, f"Journals - {name}", amt))
    return items, warnings


# ====================================================================== the ledger (derived postings + journals)

class Ledger:
    """All postings for the firm, derived and manual, as flat debit/credit lines."""

    def __init__(self, year_ends):
        self.year_ends = year_ends
        self.cols = []
        self.lines = []
        self.warnings = []
        self.accounts = {}
        self.by_key = {}
        self._seq = 0
        self._skipped = defaultdict(int)
        self._default = {}
        self.periods = []

    # ---- accounts
    def load_accounts(self):
        rows = FirmAccount.query.order_by(FirmAccount.code).all()
        self.accounts = {a.id: a for a in rows}
        self.by_key = {a.system_key: a for a in rows if a.system_key}
        ranked = sorted(rows, key=lambda a: (a.is_system, a.code))
        for a in ranked:
            if a.is_active and a.fs_category and a.fs_category in CAT and a.fs_category not in self._default:
                self._default[a.fs_category] = a

    def account(self, ref):
        if isinstance(ref, int):
            return self.accounts.get(ref)
        return self.by_key.get(ref)

    def default_for(self, cat):
        return self._default.get(cat)

    # ---- dates and money
    def rate_on(self, on):
        for end, rate in self.periods:
            if on <= end:
                return rate
        return None

    def usd(self, amount, currency, on, what):
        """USD value of an amount, or None (and the item is counted as left out) when a ZWG item has no usable rate."""
        if currency in (None, "USD"):
            return r2(amount)
        rate = self.rate_on(on)
        if rate and rate > 0:
            return r2(amount / rate)
        self._skipped[(what, currency)] += 1
        return None

    def period_start_of(self, on):
        if not self.cols:
            return date(on.year, 1, 1)
        for c in self.cols:
            if on <= c["period_end"]:
                return c["period_start"] if on >= c["period_start"] else date(on.year, 1, 1)
        return self.cols[-1]["period_end"] + timedelta(days=1)

    # ---- posting
    def post(self, on, source, ref, description, legs, link=None, journal_id=None, kind="derived"):
        """legs: [(account key or id, debit, credit, narration)] in USD. Zero legs are dropped; a cent of rounding goes to
        the opening-balances clearing account so the posting always balances."""
        built = []
        for ref_, dr, cr, narr in legs:
            dr, cr = r2(dr), r2(cr)
            if dr == 0 and cr == 0:
                continue
            a = self.account(ref_)
            if a is None:
                a = self.account("SUSP_OPEN")
                narr = f"{narr or ''} [no account for {ref_}]".strip()
            built.append((a.id, dr, cr, narr))
        if not built:
            return
        diff = r2(sum(b[1] for b in built) - sum(b[2] for b in built))
        if abs(diff) >= CENT:
            sa = self.account("SUSP_OPEN")
            built.append((sa.id, 0.0 if diff > 0 else -diff, diff if diff > 0 else 0.0, "balancing figure"))
        self._seq += 1
        for aid, dr, cr, narr in built:
            self.lines.append({"seq": self._seq, "date": on, "source": source, "ref": ref or "", "description": description,
                               "account_id": aid, "dr": dr, "cr": cr, "narr": narr or "", "link": link, "journal_id": journal_id, "kind": kind})

    def finish(self):
        self.lines.sort(key=lambda l: (l["date"], l["seq"]))
        for (what, cur), n in sorted(self._skipped.items()):
            self.warnings.append(f"{n} {what} in {cur} were left out of the ledger because no {cur} per USD rate is entered for the year-end that covers them.")


def _entered_by_category(ye):
    """{fs_category: usd} of the figures entered on a year-end that reach the statements."""
    import firm_statements as fs
    end = ye.period_end
    items, _w = fs.entered_lines(ye, cashbook.uses_cash_book(ye, end), not getattr(ye, "tax_manual", False))
    out = defaultdict(float)
    for code, _name, usd in items:
        if code in CAT:
            out[code] += usd
    return out


RE_CLOSE_CATS = {"retained_earnings", "dividends_paid"} | {c[0] for c in financials.FS_CATEGORIES if c[2] == "PL" and c[0] != "oci_items"}


def is_period_account(a):
    """Income and expense accounts (and dividends and other comprehensive income) run for one financial year and are then
    rolled into retained earnings; everything else carries its balance forward."""
    return a.account_type in ("Income", "Expense") or a.fs_category in ("dividends_paid", "oci_items")


def build_ledger(year_ends=None, include_journals=True):
    """Compute every posting. Heavy-ish (it builds the statements chain), so a page calls it once."""
    import firm_statements as fs
    yes = sorted(year_ends if year_ends is not None else FirmYearEnd.query.all(), key=lambda y: y.period_end)
    classes, assets = fs.load_register()
    cols = fs.build_chain(yes, classes, assets) if yes else []

    inv_rows = {cur: analytics._invoice_rows(cur) for cur in INVOICE_CURRENCIES}
    exp_rows = {cur: analytics._expense_rows(cur) for cur in INVOICE_CURRENCIES}
    services = {r["service"] for rows in inv_rows.values() for r in rows}
    categories = {r["category"] for rows in exp_rows.values() for r in rows}
    ensure_chart(services=services, categories=categories)

    L = Ledger(yes)
    L.cols = cols
    L.periods = [(ye.period_end, ye.zwg_rate) for ye in yes]
    L.load_accounts()
    first_start = cols[0]["period_start"] if cols else None
    last_end = cols[-1]["period_end"] if cols else None

    # ---------------- invoices, credit notes
    for cur in INVOICE_CURRENCIES:
        for r in inv_rows[cur]:
            gross = L.usd(r["gross"], cur, r["issue"], "invoices")
            net = L.usd(r["net"], cur, r["issue"], "invoices")
            if gross is None or net is None:
                continue
            link = ("invoicing.view_invoice", {"invoice_id": r["id"]})
            note = f" ({cur} {r['gross']:,.2f})" if cur != "USD" else ""
            L.post(r["issue"], "Invoice", r["number"], f"{r['client']} - invoice {r['number']}{note}",
                   [("AR", gross, 0, r["client"]), (f"REV:{r['service']}", 0, net, r["service"]), ("VAT_CTRL", 0, gross - net, "VAT on invoice")], link=link)
            for cdate, cnet, cgross in r["credits"]:
                g = L.usd(cgross, cur, cdate, "credit notes")
                n = L.usd(cnet, cur, cdate, "credit notes")
                if g is None or n is None:
                    continue
                L.post(cdate, "Credit note", r["number"], f"{r['client']} - credit note on invoice {r['number']}",
                       [(f"REV:{r['service']}", n, 0, r["service"]), ("VAT_CTRL", g - n, 0, "VAT on credit note"), ("AR", 0, g, r["client"])], link=link)

    # ---------------- advances applied to invoices (not new money)
    for rc in Receipt.query.filter(Receipt.voided_at.is_(None), Receipt.advance_application_id.isnot(None)).all():
        inv = rc.invoice
        if inv is None or inv.status not in ("Sent", "Paid"):
            continue
        amt = L.usd(rc.amount or 0.0, rc.currency or inv.currency, rc.paid_on, "advance applications")
        if amt is None:
            continue
        L.post(rc.paid_on, "Advance applied", rc.receipt_number, f"{inv.client.name if inv.client else 'Client'} - advance applied to invoice {inv.invoice_number}",
               [("ADV", amt, 0, "advance used"), ("AR", 0, amt, "invoice paid")], link=("invoicing.view_invoice", {"invoice_id": inv.id}))

    # ---------------- expense bills
    for cur in INVOICE_CURRENCIES:
        for e in exp_rows[cur]:
            amt = L.usd(e["amount"], cur, e["date"], "expense bills")
            if amt is None:
                continue
            key = f"EXP:{e['category']}"
            L.post(e["date"], "Expense bill", f"EXP-{e['id']}", f"{e['supplier']} - {e['category']}",
                   [(key if L.account(key) else "EXP_OTHER", amt, 0, e["description"][:120]), ("AP", 0, amt, e["supplier"])],
                   link=("expenses.edit_expense", {"expense_id": e["id"]}))

    # ---------------- cash book
    accounts, txns, _clears = cashbook.load_all()
    cash_by_id = {a.id: a for a in accounts}
    tax_windows = [(c["period_start"], c["period_end"]) for c in cols if c.get("tax") is not None]

    def in_tax_window(d):
        return any(s <= d <= e for s, e in tax_windows)

    for a in accounts:
        if abs(a.opening_balance or 0.0) >= CENT:
            amt = L.usd(a.opening_balance, a.currency, a.opening_date, "cash book opening balances")
            if amt is None:
                continue
            L.post(a.opening_date, "Opening balance", "", f"{a.name} - opening balance",
                   [(f"CASH:{a.id}", max(amt, 0), max(-amt, 0), "opening balance"), ("SUSP_OPEN", max(-amt, 0), max(amt, 0), "cash book opening")])
    from models import CASH_TRANSFER_CATEGORY
    for t in txns:
        amt = L.usd(t["amount"], t["currency"], t["date"], "cash book transactions")
        if amt is None:
            continue
        acct = cash_by_id.get(t["account_id"])
        if acct is None:
            cash_key, where = "SUSP_CASH", "not in any cash book account"
        elif t["date"] < acct.opening_date:
            cash_key, where = "SUSP_OPEN", f"before {acct.name} started"
        else:
            cash_key, where = f"CASH:{acct.id}", acct.name
        src = t["source"]
        if src in ("Receipt", "Invoice"):
            contra = "AR"
        elif src in ("Advance", "Refund"):
            contra = "ADV"
        elif src == "Expense":
            contra = "AP"
        else:
            cat = t.get("category") or ""
            if cat == CASH_TRANSFER_CATEGORY:
                contra = "CLR_TRANSFER"
            elif cat in ("QPD (provisional tax) paid", "Income tax paid"):
                contra = "TAX_PAY" if in_tax_window(t["date"]) else "SUSP_CASH"
            elif cat == "VAT paid to ZIMRA":
                contra = "VAT_CTRL"
            else:
                contra = "SUSP_CASH"
        desc = t["description"] if not where.startswith("not in") and not where.startswith("before") else f"{t['description']} ({where})"
        legs = [(cash_key, max(amt, 0), max(-amt, 0), t["ref"]), (contra, max(-amt, 0), max(amt, 0), t.get("category") or src)]
        L.post(t["date"], "Cash book - " + src.lower(), t["ref"], desc, legs, link=t.get("link") and (t["link"][0], t["link"][1]))

    # ---------------- asset register, tax, entered figures (per year-end)
    prev_entered = {}
    prev_def = (0.0, 0.0)
    for idx, col in enumerate(cols):
        ye, start, end = col["ye"], col["period_start"], col["period_end"]
        sched = col["schedule"]
        by_class = defaultdict(float)
        for row in sched["assets"]:
            cost = r2(row["cost"])
            label = f"{row['asset_code']} {row['description']}"
            if row["additions"]:
                L.post(row["date_acquired"], "Asset purchase", row["asset_code"], f"Purchase - {label}",
                       [("PPE_COST", cost, 0, label), ("SUSP_ASSET", 0, cost, "funding to be matched")])
            if idx == 0 and row["opening_cost"]:
                oc, oa = r2(row["opening_cost"]), r2(row["opening_acc_dep"])
                L.post(start - timedelta(days=1), "Asset b/f", row["asset_code"], f"Brought forward - {label}",
                       [("PPE_COST", oc, 0, label), ("PPE_ACCDEP", 0, oa, label), ("SUSP_OPEN", 0, oc - oa, "net book value b/f")])
            if row["charge"]:
                by_class[row["class_name"]] += row["charge"]
            if row["disposed_in_period"]:
                dc, da, pr = r2(row["disposals_cost"]), r2(row["disposals_acc_dep"]), r2(row["proceeds"])
                gl = r2(pr + da - dc)
                L.post(row["disposed_on"], "Asset disposal", row["asset_code"], f"Disposal - {label}",
                       [("SUSP_ASSET", pr, 0, "proceeds to be matched"), ("PPE_ACCDEP", da, 0, label), ("PPE_COST", 0, dc, label),
                        ("DISP_GL", max(-gl, 0), max(gl, 0), "profit / (loss) on disposal")])
        for cname, amt in sorted(by_class.items()):
            a2 = r2(amt)
            L.post(end, "Depreciation", "", f"Depreciation - {cname} ({col['label']})", [("DEPR", a2, 0, cname), ("PPE_ACCDEP", 0, a2, cname)])

        tax = col.get("tax")
        if tax is not None:
            cur_tax = r2(tax["current_tax"])
            L.post(end, "Income tax", "", f"Current tax ({col['label']})", [("TAX_EXP_CUR", max(cur_tax, 0), max(-cur_tax, 0), "current tax"), ("TAX_PAY", max(-cur_tax, 0), max(cur_tax, 0), "tax for the year")])
            c = r2(tax["deferred_closing"])
            new_l, new_a = max(c, 0.0), max(-c, 0.0)
            dl, da_ = r2(new_l - prev_def[0]), r2(new_a - prev_def[1])
            dexp = r2(tax["deferred_expense"])
            L.post(end, "Deferred tax", "", f"Deferred tax ({col['label']})",
                   [("DTL", max(-dl, 0), max(dl, 0), "deferred tax liability"), ("DTA", max(da_, 0), max(-da_, 0), "deferred tax asset"),
                    ("TAX_EXP_DEF", max(dexp, 0), max(-dexp, 0), "deferred tax")])
            prev_def = (new_l, new_a)
        else:
            prev_def = (0.0, 0.0)

        entered = _entered_by_category(ye)
        for code, amt in sorted(entered.items()):
            _c, label, stmt, section, side, _cf = CAT[code]
            if code == "retained_earnings":
                continue                       # handled below, against what the ledger already holds
            if stmt == "SFP":
                delta = r2(amt - prev_entered.get(code, 0.0))
            else:
                delta = r2(amt)
            if abs(delta) < CENT:
                continue
            acct = L.default_for(code)
            if acct is None:
                L.warnings.append(f"No ledger account is mapped to '{label}', so the figure entered on the year-end {end.strftime('%d %b %Y')} is not in the ledger.")
                continue
            d_dr = delta if side == "debit" else -delta
            L.post(end, "Entered figure", "", f"Entered on the year-end {end.strftime('%d %b %Y')}: {label}",
                   [(acct.id, max(d_dr, 0), max(-d_dr, 0), label), ("SUSP_OPEN", max(-d_dr, 0), max(d_dr, 0), "to be journalised")])
        prev_entered = {k: v for k, v in entered.items() if CAT[k][2] == "SFP"}

        if col["re_entered"]:
            L.lines.sort(key=lambda l: (l["date"], l["seq"]))
            entered_re = r2(entered.get("retained_earnings", 0.0))
            day = start - timedelta(days=1)
            held = 0.0
            for ln in L.lines:
                if ln["date"] > day:
                    continue
                a = L.accounts[ln["account_id"]]
                if a.fs_category in RE_CLOSE_CATS:
                    held += ln["cr"] - ln["dr"]
            adj = r2(entered_re - held)
            if abs(adj) >= CENT:
                L.post(day, "Entered figure", "", f"Opening retained earnings entered on the year-end {end.strftime('%d %b %Y')}",
                       [("RE", max(-adj, 0), max(adj, 0), "adjust to the entered opening balance"), ("SUSP_OPEN", max(adj, 0), max(-adj, 0), "to be journalised")])

    # ---------------- manual journals
    if include_journals:
        for j in FirmJournal.query.filter_by(status="posted").order_by(FirmJournal.journal_date, FirmJournal.id).all():
            legs = [(ln.account_id, ln.debit or 0.0, ln.credit or 0.0, ln.narration) for ln in j.lines]
            L.post(j.journal_date, "Journal", j.number, j.description, legs, link=("firmfs.journal_view", {"journal_id": j.id}),
                   journal_id=j.id, kind="journal")
    L.finish()
    return L


# ====================================================================== reports

def trial_balance(L, as_at, period_start=None):
    """Trial balance at the end of `as_at`. Balance-sheet accounts carry everything to the date; income and expense accounts
    (and dividends) show only the current financial year, and the results of earlier years are rolled into one retained
    earnings line, so the debit and credit columns agree."""
    ps = period_start or L.period_start_of(as_at)
    net = defaultdict(float)
    rolled = 0.0
    for ln in L.lines:
        if ln["date"] > as_at:
            break
        a = L.accounts[ln["account_id"]]
        d = ln["dr"] - ln["cr"]
        if is_period_account(a) and ln["date"] < ps:
            rolled += d
        else:
            net[a.id] += d
    rows = []
    for aid, v in net.items():
        a = L.accounts[aid]
        v = r2(v)
        if abs(v) < CENT:
            continue
        rows.append({"account": a, "debit": v if v > 0 else 0.0, "credit": -v if v < 0 else 0.0})
    rows.sort(key=lambda r: r["account"].code)
    rolled = r2(rolled)
    if abs(rolled) >= CENT:
        rows.append({"account": None, "label": "Results of earlier years rolled into retained earnings", "debit": rolled if rolled > 0 else 0.0,
                     "credit": -rolled if rolled < 0 else 0.0})
    td, tc = r2(sum(r["debit"] for r in rows)), r2(sum(r["credit"] for r in rows))
    return {"rows": rows, "debit": td, "credit": tc, "balanced": abs(td - tc) < 0.01, "as_at": as_at, "period_start": ps,
            "suspense": [r for r in rows if r["account"] is not None and r["account"].system_key in SUSPENSE_KEYS]}


def general_ledger(L, account, start, end):
    """Postings to one account between two dates with a running balance. Income and expense accounts open at nil on the
    first day of their financial year; balance-sheet accounts open with everything before `start`."""
    period_acct = is_period_account(account)
    floor = L.period_start_of(start) if period_acct else None
    opening = 0.0
    lines = []
    for ln in L.lines:
        if ln["account_id"] != account.id:
            continue
        if ln["date"] < start:
            if floor is None or ln["date"] >= floor:
                opening += ln["dr"] - ln["cr"]
            continue
        if ln["date"] > end:
            break
        lines.append(ln)
    run = r2(opening)
    out = []
    for ln in lines:
        run = r2(run + ln["dr"] - ln["cr"])
        out.append(dict(ln, balance=run))
    return {"opening": r2(opening), "lines": out, "closing": run,
            "debit": r2(sum(l["dr"] for l in out)), "credit": r2(sum(l["cr"] for l in out))}


def account_balances(L, as_at, start=None):
    """{account id: dr - cr}: to the date for balance-sheet accounts; from `start` for income/expense accounts."""
    ps = start or L.period_start_of(as_at)
    out = defaultdict(float)
    for ln in L.lines:
        if ln["date"] > as_at:
            break
        a = L.accounts[ln["account_id"]]
        if is_period_account(a) and ln["date"] < ps:
            continue
        out[a.id] += ln["dr"] - ln["cr"]
    return out


def reconcile(L, col):
    """Ledger against the statements for one year-end column: per statement line, what the ledger holds, what the statement
    shows, and the difference. The ledger has no figure the statements lack except through the causes listed on the page."""
    import firm_statements as fs
    start, end = col["period_start"], col["period_end"]
    cum, per = defaultdict(float), defaultdict(float)
    for ln in L.lines:
        if ln["date"] > end:
            break
        d = ln["dr"] - ln["cr"]
        cum[ln["account_id"]] += d
        if ln["date"] >= start:
            per[ln["account_id"]] += d
    led = defaultdict(float)
    re_close = 0.0
    for aid in cum:
        a = L.accounts[aid]
        cat = a.fs_category
        if not cat or cat == EXCLUDED or cat not in CAT:
            continue
        stmt, side = CAT[cat][2], CAT[cat][4]
        if cat in RE_CLOSE_CATS:
            re_close += -cum[aid]
        if cat == "retained_earnings":
            continue
        val = cum[aid] if stmt == "SFP" else per.get(aid, 0.0)
        if a.system_key == "TAX_PAY":
            led["current_tax_payable" if val <= 0 else "other_current_assets"] += abs(val)
        elif a.system_key == "DISP_GL":
            led["other_income" if val <= 0 else "other_expenses"] += abs(val)
        else:
            led[cat] += val if side == "debit" else -val
    rows = []
    for code in fs.ALL_CODES:
        if code == "retained_earnings":
            continue
        lv, sv = r2(led.get(code, 0.0)), r2(col["totals"].get(code, 0.0))
        if abs(lv) < CENT and abs(sv) < CENT:
            continue
        rows.append({"code": code, "label": CAT[code][1], "section": CAT[code][3], "statement": CAT[code][2],
                     "ledger": lv, "stmt": sv, "diff": r2(lv - sv)})
    closing_stmt = fs.column_closing_re(col)
    rows.append({"code": "closing_re", "label": "Retained earnings at the year-end (opening + profit - dividends)", "section": "Equity", "statement": "SFP",
                 "ledger": r2(re_close), "stmt": r2(closing_stmt), "diff": r2(re_close - closing_stmt)})
    bad = [r for r in rows if abs(r["diff"]) >= 0.01]
    tb = trial_balance(L, end, start)
    return {"rows": rows, "different": len(bad), "tb": tb, "suspense": tb["suspense"],
            "suspense_net": r2(sum(r["debit"] - r["credit"] for r in tb["suspense"])), "start": start, "end": end, "col": col}
