"""The Value Added Tax Act [Chapter 23:12] set against the VAT register.

Two things live here:

  month_checks(...)  - points a preparer should look at in one VAT month, each
                       tied to the section of the Act it comes from. They are
                       prompts for review ("check this"), never decisions: a
                       document is never changed or left out because of one.
  FINDINGS           - the side-by-side of what the Act says and what the VAT
                       register does, shown on the VAT Act page. Nothing in it is
                       marked verified; the Partner does that.

Pure Python. The Act's own words are not stored here: the pages fetch them from
the filed Act (act_import) so they can never drift from it.
"""
import calendar
import re
from collections import namedtuple
from datetime import date

from models import VAT_DEFAULT_CURRENCY, VAT_DENIAL_LABELS

Flag = namedtuple("Flag", "key level ref text count")
# level: "review" (look at this) or "note" (how the Act treats it)


def return_due_date(year, month):
    """Section 28(1): the return and the tax are due by the 25th day of the month after the tax period.
    Shown for a one-month period; a two-month period (Category A or B, section 27) is due the 25th after its second month."""
    y, m = (year + 1, 1) if month == 12 else (year, month + 1)
    return date(y, m, 25)


def _months_before(d, n):
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


# Words that suggest the input tax may be one that section 16(2) does not allow. A hint to look, nothing more.
_HINTS = [
    ("entertainment", re.compile(r"\b(entertain\w*|hospitality|client\s+lunch|staff\s+party|year[- ]end\s+function|catering|gifts?)\b", re.I), "16(2)(a)"),
    ("club", re.compile(r"\b(golf|sports?\s+club|social\s+club|country\s+club|club\s+(?:fees?|subscription|membership))\b", re.I), "16(2)(b)"),
    ("vehicle", re.compile(r"\b(motor\s*vehicle|passenger\s+(?:car|vehicle)|sedan|hatchback|suv|vehicle\s+purchase)\b", re.I), "16(2)(d)"),
]


def month_checks(docs, year, month, is_done=False, today=None):
    """Flags for the documents in one VAT month (all currencies). `docs` are VATInvoice rows dated in that month."""
    today = today or date.today()
    flags = []
    due = return_due_date(year, month)

    def add(key, level, ref, text, count=0):
        flags.append(Flag(key, level, ref, text, count))

    if not is_done and today > due:
        add("overdue", "review", "28(1)",
            "The return and payment for this period were due by %s. If the tax is paid late the Act charges a penalty equal to the tax and interest for each month or part of a month (section 39)." % due.strftime("%d %B %Y"))

    exports = [d for d in docs if d.doc_type == "Export Sale"]
    n = len([d for d in exports if not (d.related_reference or "").strip()])
    if n:
        add("export_proof", "review", "10(3)",
            "%d export sale%s with no customs or export entry reference. A zero rate needs documentary proof that the Commissioner accepts, and the registered operator must keep it." % (n, "" if n == 1 else "s"), n)

    # a rate that does not match the VAT charged
    odd = []
    for d in docs:
        if d.doc_type in ("Export Sale",):
            continue
        r = d.vat_rate
        if r is None:
            continue
        expected = (d.taxable_amount or 0.0) * (r.rate_percent or 0.0) / 100.0
        if abs((d.vat_amount or 0.0) - expected) > max(0.05, 0.005 * abs(expected)):
            odd.append(d)
    if odd:
        add("rate_mismatch", "review", "9(2)",
            "%d document%s where the VAT stated is not the chosen rate applied to the value. Check the rate used, or that the value excludes tax (section 9(2): value is the consideration less the tax in it)." % (len(odd), "" if len(odd) == 1 else "s"), len(odd))

    std_zero = [d for d in docs if d.direction == "Output" and d.doc_type == "Invoice" and d.vat_rate is not None
                and d.vat_rate.rate_type == "Standard" and (d.vat_amount or 0.0) == 0.0 and (d.taxable_amount or 0.0) > 0.0]
    if std_zero:
        add("standard_nil", "review", "6(1)",
            "%d sales invoice%s on the Standard rate with no VAT charged. If the supply is zero-rated (section 10) or exempt (section 11), pick that rate so the working paper shows it correctly." % (len(std_zero), "" if len(std_zero) == 1 else "s"), len(std_zero))

    zero_vat = [d for d in docs if d.vat_rate is not None and d.vat_rate.rate_type in ("Zero-rated", "Exempt") and (d.vat_amount or 0.0) > 0.0]
    if zero_vat:
        add("zero_with_vat", "review", "10",
            "%d document%s on a Zero-rated or Exempt rate that nevertheless carries VAT. One of the two is wrong." % (len(zero_vat), "" if len(zero_vat) == 1 else "s"), len(zero_vat))

    # exempt supplies and input tax
    cur_has_exempt = {}
    cur_has_input = {}
    for d in docs:
        cur = d.currency or VAT_DEFAULT_CURRENCY
        if d.direction == "Output" and d.vat_rate is not None and d.vat_rate.rate_type == "Exempt":
            cur_has_exempt[cur] = True
        if d.direction == "Input" and (d.vat_amount or 0.0) > 0.0 and not d.input_tax_denied:
            cur_has_input[cur] = True
    both = [c for c in cur_has_exempt if cur_has_input.get(c)]
    if both:
        add("apportion", "review", "16(1)",
            "Exempt sales and input tax in the same month (%s). Input tax on goods and services used partly for exempt supplies is deductible only in the proportion used for taxable supplies, and in full where taxable use is 90%% or more. The register does not apportion; work it out and enter the allowable amount." % ", ".join(sorted(both)))

    # input documents
    inputs = [d for d in docs if d.direction == "Input"]
    limit = _months_before(today, 12)
    old = [] if is_done else [d for d in inputs if d.doc_type in ("Invoice", "Bill of Entry") and d.invoice_date and d.invoice_date < limit
                              and (d.vat_amount or 0.0) > 0.0 and not d.input_tax_denied]
    if old:
        add("old_input", "review", "15(2)",
            "%d input document%s dated more than 12 months ago and still being claimed. Input tax needs a tax invoice (or Bill of Entry) held when the return is furnished, and the Act sets a 12-month limit; check the claim is still in time." % (len(old), "" if len(old) == 1 else "s"), len(old))

    thin = []
    for d in inputs:
        if d.doc_type != "Invoice" or (d.vat_amount or 0.0) <= 0.0:
            continue
        small = (d.currency or VAT_DEFAULT_CURRENCY) == "USD" and (d.taxable_amount or 0.0) + (d.vat_amount or 0.0) <= 10.0
        if small:
            continue   # section 20(5): no tax invoice is required for a supply of US$10 or less
        if not (d.invoice_number or "").strip() or not (d.counterparty_name or "").strip():
            thin.append(d)
    if thin:
        add("invoice_particulars", "review", "20(4)",
            "%d input invoice%s with no invoice number or no supplier name. A tax invoice must carry a serial number, the date and the supplier's name, address and registration number." % (len(thin), "" if len(thin) == 1 else "s"), len(thin))

    hints = {}
    for d in inputs:
        if d.doc_type == "Credit Note" or d.input_tax_denied or (d.vat_amount or 0.0) <= 0.0:
            continue
        text = "%s %s" % (d.description or "", d.counterparty_name or "")
        for key, rx, ref in _HINTS:
            if rx.search(text):
                hints.setdefault(key, [ref, 0])[1] += 1
                break
    for key, (ref, cnt) in hints.items():
        add("deny_" + key, "review", ref,
            "%d input document%s look like %s. Input tax on %s is not deductible. If it is, use 'Not deductible' on the document so it is left out of the claim." % (
                cnt, "" if cnt == 1 else "s", {"entertainment": "entertainment", "club": "a club or society subscription", "vehicle": "a motor vehicle"}[key],
                {"entertainment": "entertainment", "club": "club subscriptions", "vehicle": "most motor vehicles"}[key]), cnt)

    cn = [d for d in docs if d.doc_type == "Credit Note" and not (d.related_reference or "").strip()]
    if cn:
        add("credit_note_ref", "review", "21(3)",
            "%d credit note%s with no original invoice reference. A credit note must give information sufficient to identify the transaction it corrects." % (len(cn), "" if len(cn) == 1 else "s"), len(cn))

    add("time_of_supply", "note", "8(1)",
        "Documents are filed in the month of their date. Under section 8(1) a supply is made at the earliest of: the invoice, payment received, removal of the goods, possession of fixed property, or performance of the service. If payment, delivery or the service came first, the supply belongs in that earlier month.")
    return flags


# ----------------------------------------------------------------------------------------------
# the side-by-side
# result: agrees | updated | differs | missing
RESULT_LABELS = {
    "agrees": "Agrees with the Act",
    "updated": "Brought into line in this update",
    "differs": "Differs from the Act",
    "missing": "Not in the app",
}

FINDINGS = [
    {
        "topic": "The rate of tax", "refs": ["6(1)"], "result": "agrees",
        "app": "Every rate comes from the firm-wide VAT Rates table that a Partner keeps. None is built in.",
        "note": "The VAT Act contains no percentage: it charges tax 'at such rate as may be fixed by the Charging Act'. Keep the table in line with the Charging Act and each Finance Act.",
    },
    {
        "topic": "What is taxed", "refs": ["6(1)", "13"], "result": "missing",
        "app": "The register has sales, export sales, credit notes, purchases and Bills of Entry.",
        "note": "Imported services (section 13: tax paid by the recipient, then claimed as input tax under 15(2)(e)) have no document type. Until one is added, capture the client's own accounting for them as a purchase with the VAT the client declared.",
    },
    {
        "topic": "Time of supply", "refs": ["8(1)", "8(3)"], "result": "differs",
        "app": "A document is placed in the month of its date.",
        "note": "The Act puts a supply in the month of the earliest of invoice, payment, removal of the goods, possession of property or performance of the service, and has special timing for rentals, instalments, progressive work and fixed property. A deposit paid in October for an invoice dated November belongs in October. The register now says so on every month; it does not move documents on its own.",
    },
    {
        "topic": "Value of the supply", "refs": ["9(2)"], "result": "missing",
        "app": "A document is captured as a value excluding VAT plus the VAT charged.",
        "note": "Where the tax is not shown separately the Act treats part of the price as tax (the tax fraction), so a price that includes VAT cannot be entered directly. Work the value out first.",
    },
    {
        "topic": "Zero-rated supplies", "refs": ["10(1)(a)", "10(2)", "10(3)"], "result": "updated",
        "app": "An export sale is always taxed at 0%. Other zero-rated supplies (transport, services to non-residents, going-concern sales and the rest of section 10) are captured as sales invoices on the Zero-rated rate.",
        "note": "The Act makes the zero rate conditional on documentary proof the Commissioner accepts. The register now flags an export sale with no customs or export entry reference, and shows zero-rated sales as their own line.",
    },
    {
        "topic": "Exempt supplies", "refs": ["11"], "result": "updated",
        "app": "'Exempt' is a rate type. Before this update exempt sales were added into the taxable value of sales.",
        "note": "An exempt supply is not a taxable supply (it counts neither as output tax nor towards the registration threshold). The working paper now shows exempt sales separately and takes them out of the taxable sales line.",
    },
    {
        "topic": "Conditions for claiming input tax", "refs": ["15(2)(a)", "15(2)(d)"], "result": "updated",
        "app": "Any purchase invoice or Bill of Entry captured is counted as input tax.",
        "note": "The Act allows the claim only with a tax invoice or Bill of Entry in hand when the return is furnished and within 12 months. The register now flags input documents older than that and invoices with no number or supplier. It does not stop the claim.",
    },
    {
        "topic": "Input tax the Act does not allow", "refs": ["16(2)"], "result": "updated",
        "app": "Every input document used to reduce the VAT payable. Now a document can be marked 'Not deductible' with the ground (entertainment, club subscription, motor vehicle, export tax and so on); its VAT stays in the register but is left out of the claim, and the working paper shows the amount disallowed.",
        "note": "The register suggests documents that look like entertainment, clubs or motor vehicles from their description. A person decides; nothing is marked automatically. The motor-vehicle rule has exceptions (vehicles bought for the trade of a type set in the Charging Act or regulations, and motor dealers).",
    },
    {
        "topic": "Input tax used partly for exempt supplies", "refs": ["16(1)"], "result": "missing",
        "app": "No apportionment is computed.",
        "note": "Input tax is deductible only in the proportion of taxable use, and in full when taxable use is 90%% or more. The register flags a month that has both exempt sales and input tax so the apportionment is not forgotten.",
    },
    {
        "topic": "What a tax invoice must show", "refs": ["20(1)", "20(4)", "20(5)"], "result": "differs",
        "app": "Invoice number, date, customer or supplier name, description and amounts are captured.",
        "note": "A tax invoice must say 'Fiscal Tax Invoice', carry the supplier's and (for registered recipients) the recipient's registration numbers, a serial number, quantity and either value, tax and consideration or the rate. The register does not hold registration numbers or test these, apart from the missing-number and missing-supplier flags. No tax invoice is needed for a supply of US$10 or less.",
    },
    {
        "topic": "Credit and debit notes", "refs": ["21(3)", "21(6)", "21(7)"], "result": "differs",
        "app": "Credit notes issued and received are captured and taken off in the month of their date. There is no debit note.",
        "note": "A supplier who undercharged tax issues a debit note, which the recipient may claim in the month it is issued. Capture it as an ordinary invoice for the additional amount until a debit note type is added. A credit note must identify the transaction it corrects; the register flags one with no reference.",
    },
    {
        "topic": "Irrecoverable debts", "refs": ["22(1)"], "result": "missing",
        "app": "No bad-debt relief.",
        "note": "Tax already accounted for on a sale can be recovered once the debt is written off, subject to the conditions in section 22. Capture it as a purchase-side adjustment with a clear description for now.",
    },
    {
        "topic": "Registration", "refs": ["23(1)", "24(1)"], "result": "missing",
        "app": "The register is for clients that are already registered. The registration threshold is not tested.",
        "note": "A trader becomes liable to register when taxable supplies in the last 12 months exceed the amount printed in section 23(1) (or the amount prescribed, which has changed over time; confirm it before relying on it). Exempt supplies are not counted. A registered operator may ask to be cancelled when supplies stay below it (section 24).",
    },
    {
        "topic": "Tax periods", "refs": ["27(1)"], "result": "differs",
        "app": "Each calendar month is computed and locked on its own, which matches Category C (monthly).",
        "note": "Most operators are in Category A or B, with two-month periods; farmers and some others are in Category D. For those clients the two months are combined into one return. The register does not yet group months.",
    },
    {
        "topic": "Return and payment", "refs": ["28(1)", "28(2)"], "result": "updated",
        "app": "A month is marked 'Computation done' and locked.",
        "note": "The return and tax are due by the 25th of the month after the tax period, and a return is due even when no tax is payable or a refund is claimed. The month page now shows the due date and warns when it has passed with the computation not done.",
    },
    {
        "topic": "Late payment", "refs": ["39(2)"], "result": "differs",
        "app": "The Penalty and Interest engine applies a rate you enter: a flat penalty on the principal and simple interest on days late over 365.",
        "note": "For VAT the Act charges a penalty equal to the tax unpaid and interest at the prescribed rate for each month or part of a month, counted from the first day of the month after the one in which payment was due. Enter the VAT rates accordingly and count months, not days, until the engine is changed.",
    },
    {
        "topic": "Refunds", "refs": ["44(1)"], "result": "missing",
        "app": "A negative net position is shown as 'Refundable'.",
        "note": "A refund must be claimed within 6 years of the end of the tax period, and a small refund (below the amount printed in section 44) is carried forward instead of paid. The register does not apply either rule.",
    },
    {
        "topic": "Currency of trade", "refs": ["15(3)"], "result": "agrees",
        "app": "ZWG and USD are kept as separate working papers and never set against each other.",
        "note": "An editor's note printed at section 15(3)(g) says there is no set-off between currencies, which the register already respects. It is the editor's note, not the statute's own wording.",
    },
]
