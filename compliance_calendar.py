"""The Neverlank Compliance Calendar: the dates, and the PDF and Excel files built from them.

One list of dates (items(year)) feeds both files, so they can never disagree.

What is in it
* Monthly dates: returns on the 5th; PAYE, withholding taxes, NSSA, ZIMDEF and NEC payments on the 10th; VAT on the 15th; excise duty on the 20th.
* Quarterly payment dates for income tax: returns 20 March, 20 June, 20 September, 15 December; payments 25 March, 25 June, 25 September, 20 December.
* Yearly dates: employers' annual PAYE return (end of January, announced each year), the annual income tax return (30 April, announced each year).
* Public holidays, worked out from the rules (Sunday holidays move to the Monday), with the last working day before every due date that falls
  on a weekend or holiday.
* A page of time limits that start when something happens (a sale, a share allotment, a change of director), taken from the Capital Gains Tax Act
  and the Companies and Other Business Entities Act as already held in cobe_checks.py and cgt_checks.py, and ZIMRA's payment-dates page.
* A page for the client's own dates.

Where the dates come from: ZIMRA "Tax payment dates" page, PwC Zimbabwe tax summaries (tax administration), the Companies and Other Business
Entities Act and the Capital Gains Tax Act as held in this app, and published public holiday lists. Dates marked "announced each year" are set by
ZIMRA in a public notice and can move. The wording tells the reader to confirm critical dates; a person at the firm should review the file each
November (set LEADS_CALENDAR_REVIEWED on the server to print who reviewed it and when).
"""
import calendar as _cal
import io
import os
from datetime import date, timedelta
from xml.sax.saxutils import escape

PREPARED = "24 November 2026"          # when the dates below were last put together from the sources
FIRM = {
    "name": "Neverlank Chartered Accountants",
    "strapline": "World Class Financial Services",
    "address": "2nd Floor, Michael House, 62 Nelson Mandela Avenue, Harare, Zimbabwe",
    "web": "www.neverlank.co.zw",
    "email": "info@neverlank.co.zw",
    "phone": "+263 71 811 0832",
    "paab": "PAAB Firm No. Z10110",
}
NAVY, GOLD, INK, MUTED, LINE, TINT = "0F2942", "B8934A", "1F2933", "6B7684", "E3E8EE", "F6F8FB"
LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "img", "neverlank-logo.png")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# How the date is set (shown in the "basis" column)
FIXED = "Fixed by law"
RETURNS_BASIS = "Fixed: returns on the 5th, payment on the 10th (Statutory Instrument 81 of 2025)"
USUAL = "The usual date: confirm with the fund or ZIMRA"
ANNOUNCED = "ZIMRA announces the date each year"
ADVICE = "Our suggestion, not a legal date"

NOTICE = ("Dates are taken from ZIMRA's published payment dates, the Companies and Other Business Entities Act, the Capital Gains Tax Act and "
          "published public holiday lists, and were put together on " + PREPARED + ". Laws, rates and notices change, and ZIMRA sets some dates by public "
          "notice each year. Please confirm any date that matters with us or with ZIMRA before you rely on it. This calendar is general information, not "
          "advice for your own business.")
HOLIDAY_ADVICE = ("If a date falls on a weekend or a public holiday, our advice is to pay or file by the last working day before it. "
                  "The calendar shows that day for you.")


# ------------------------------------------------------------------ public holidays

def easter_sunday(year):
    """Gregorian Easter (the usual anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year, month, weekday, n):
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def holidays(year):
    """[(date, name, note)] in date order. A holiday on a Sunday is observed on the next day that is not already a holiday; one on a Saturday stays."""
    easter = easter_sunday(year)
    heroes = _nth_weekday(year, 8, 0, 2)
    base = [
        (date(year, 1, 1), "New Year's Day"),
        (date(year, 2, 21), "Robert Gabriel Mugabe National Youth Day"),
        (easter - timedelta(days=2), "Good Friday"),
        (easter - timedelta(days=1), "Easter Saturday"),
        (easter + timedelta(days=1), "Easter Monday"),
        (date(year, 4, 18), "Independence Day"),
        (date(year, 5, 1), "Workers' Day"),
        (date(year, 5, 25), "Africa Day"),
        (heroes, "Heroes' Day"),
        (heroes + timedelta(days=1), "Defence Forces Day"),
    ]
    if year >= 2026:
        base.append((date(year, 9, 15), "Munhumutapa Day"))
    base += [(date(year, 12, 22), "National Unity Day"), (date(year, 12, 25), "Christmas Day"), (date(year, 12, 26), "Boxing Day")]
    base.sort()
    taken, out = set(), []
    for d, name in base:
        taken.add(d)
    used = set()
    for d, name in base:
        note = ""
        if d.weekday() == 6:                       # Sunday
            shifted = d + timedelta(days=1)
            while shifted in taken or shifted in used:
                shifted += timedelta(days=1)
            used.add(shifted)
            out.append((shifted, name + " (observed)", f"The holiday is on Sunday {d.day} {MONTHS[d.month - 1]}, so it is taken on the Monday or next free day"))
            continue
        if d.weekday() == 5:
            note = "Falls on a Saturday"
        used.add(d)
        out.append((d, name, note))
    out.sort()
    return out


def holiday_map(year):
    return {d: name for d, name, _ in holidays(year)}


def is_working_day(d, hmap):
    return d.weekday() < 5 and d not in hmap


def last_working_day_on_or_before(d, hmap):
    while not is_working_day(d, hmap):
        d -= timedelta(days=1)
    return d


# ------------------------------------------------------------------ the dated items

VAT_CATEGORIES = [
    ("A", "Every 2 months", "Jan, Mar, May, Jul, Sep, Nov", "Feb, Apr, Jun, Aug, Oct, Dec"),
    ("B", "Every 2 months", "Feb, Apr, Jun, Aug, Oct, Dec", "Jan, Mar, May, Jul, Sep, Nov"),
    ("C", "Monthly", "Every month (Jan to Dec)", "Every month (Feb to Jan)"),
    ("D", "Special", "As approved by ZIMRA", "The month after the approved period ends"),
]


def vat_cat(month):
    """The two-monthly VAT category that files in this month: A in even months (Feb, Apr ...), B in odd months."""
    return "A" if month % 2 == 0 else "B"


def vat_text(month, year):
    def label(back):
        mm = (month - 1 - back - 1) % 12
        yy = year + ((month - 1 - back - 1) // 12)
        return f"{MONTHS[mm]} {yy}"
    return (f"VAT return and payment. Category C (monthly): {label(0)}. Category {vat_cat(month)} (every two months): {label(1)} and {label(0)}. "
            "Category D: the period ZIMRA approved")


def _monthly(year, month):
    """Dated items for one month. Each is a dict: date, what, who, basis, group."""
    m = MONTHS[month - 1]
    prev = MONTHS[(month - 2) % 12]
    rows = [
        (5, f"Returns for {prev}: PAYE (with the AIDS levy), withholding taxes, presumptive tax and the other monthly returns", "Employers and anyone who withholds tax", RETURNS_BASIS, "monthly"),
        (10, f"Payment of PAYE (with the AIDS levy), withholding taxes and the other monthly taxes for {prev}", "Employers and anyone who withholds tax", RETURNS_BASIS, "monthly"),
        (10, f"NSSA contributions for {prev}", "Employers", USUAL, "monthly"),
        (10, f"ZIMDEF levy for {prev}", "Employers", USUAL, "monthly"),
        (10, f"NEC levies for {prev}", "Employers in industries with a National Employment Council", USUAL, "monthly"),
        (15, vat_text(month, year), "VAT-registered businesses (your category is on your VAT certificate)", FIXED, "monthly"),
        (20, f"Excise duty for {prev}", "Manufacturers of excisable goods", USUAL, "monthly"),
    ]
    out = [dict(date=date(year, month, d), what=w, who=who, basis=b, group=g) for d, w, who, b, g in rows]
    first_monday = _nth_weekday(year, month, 0, 1)
    out.append(dict(date=first_monday, what=f"{m}: your 15-minute compliance review (name a person for every date this month)",
                    who="Everyone", basis=ADVICE, group="advice"))
    return out


def items(year):
    """Every dated item for the year, in date order, each with the last working day to act by."""
    hmap = holiday_map(year)
    out = []
    for month in range(1, 13):
        out += _monthly(year, month)
    quarterly = [(3, 25, 20, "10%", "First"), (6, 25, 20, "25%", "Second"), (9, 25, 20, "30%", "Third"), (12, 20, 15, "35%", "Fourth")]
    for month, day, rday, pct, nth in quarterly:
        out.append(dict(date=date(year, month, rday), what=f"{nth} quarterly payment date (QPD) return",
                        who="Companies, and individuals with income other than employment", basis=FIXED, group="quarterly"))
        out.append(dict(date=date(year, month, day), what=f"{nth} quarterly payment date (QPD) payment: {pct} of the year's estimated income tax",
                        who="Companies, and individuals with income other than employment", basis=FIXED, group="quarterly"))
    out += [
        dict(date=date(year, 1, 31), what=f"Employers' annual PAYE return (ITF16) for {year - 1}",
             who="Employers", basis=ANNOUNCED + " (the end of January in recent years)", group="yearly"),
        dict(date=date(year, 4, 30), what=f"Annual income tax return for the year ended 31 December {year - 1}, with the financial statements; "
                                       "transfer pricing return (ITF12C2) with it where it applies. Pay any balance of tax.",
             who="Companies on the standard tax year", basis=ANNOUNCED + " (30 April in recent years)", group="yearly"),
        dict(date=date(year, 1, 15), what="Add your own dates to this calendar: your year-end, lender and customer deadlines, licence and insurance renewals",
             who="Everyone", basis=ADVICE, group="advice"),
        dict(date=date(year, 2, 26), what=f"Target: books for {year - 1} closed and handed to the auditor",
             who="Companies that are audited", basis=ADVICE, group="advice"),
        dict(date=date(year, 3, 31), what=f"Target: audit of {year - 1} finished, so the return can be filed by 30 April",
             who="Companies that are audited", basis=ADVICE, group="advice"),
        dict(date=date(year, 11, 30), what="The national Budget is usually presented around now: ask what changes from 1 January",
             who="Everyone", basis=ADVICE, group="advice"),
        dict(date=date(year, 12, 31), what="Year-end for companies on the standard tax year: stock count, debtor and creditor confirmations, bank confirmations",
             who="Companies on the standard tax year", basis=FIXED, group="yearly"),
    ]
    for it in out:
        d = it["date"]
        it["day"] = DAYS[d.weekday()]
        if it["group"] == "advice":
            it["act_by"] = d
            it["flag"] = ""
            continue
        act = last_working_day_on_or_before(d, hmap)
        it["act_by"] = act
        if act != d:
            why = hmap.get(d) or DAYS[d.weekday()]
            it["flag"] = f"{d.day} {MONTHS[d.month - 1][:3]} is {'a public holiday (' + why + ')' if d in hmap else 'a ' + why}: act by {DAYS[act.weekday()][:3]} {act.day} {MONTHS[act.month - 1][:3]}"
        else:
            it["flag"] = ""
    out.sort(key=lambda r: (r["date"], r["group"] == "advice"))
    return out



def glance(year):
    """The year in one table (the layout the firm uses): per filing month, the period covered and the 5th / 10th / 15th dates, then the quarterly and annual dates.
    Each date is (date shown, original date or None): the first is the last working day to act by, the second the legal date when it differs."""
    hmap = holiday_map(year)

    def pair(d):
        act = last_working_day_on_or_before(d, hmap)
        return act, (d if act != d else None)

    quarterly = {3: (20, 25, "10%", "1"), 6: (20, 25, "25%", "2"), 9: (20, 25, "30%", "3"), 12: (15, 20, "35%", "4")}
    rows = []
    for month in range(1, 13):
        prev_month = 12 if month == 1 else month - 1
        prev_year = year - 1 if month == 1 else year
        extra = []
        if month in quarterly:
            r, p_, pct, n = quarterly[month]
            extra.append((f"QPD {n} return", pair(date(year, month, r))))
            extra.append((f"QPD {n} payment ({pct})", pair(date(year, month, p_))))
        if month == 4:
            extra.append(("Annual income tax return (ITF12C)", pair(date(year, 4, 30))))
        if month == 1:
            extra.append(("Employers' annual PAYE return (ITF16): date announced each year", None))
        rows.append(dict(month=MONTHS[month - 1], period=f"{MONTHS[prev_month - 1][:3]} {prev_year}",
                         returns=pair(date(year, month, 5)), payments=pair(date(year, month, 10)), vat=pair(date(year, month, 15)),
                         vat_cats=f"Cat. C and {vat_cat(month)}", extra=extra))
    return rows


def glance_text(pr):
    """('Fri 08 Jan', '(10 Jan)') for a date pair from glance(), or ('Fri 05 Feb', '')."""
    act, orig = pr
    shown = f"{DAYS[act.weekday()][:3]} {act.day:02d} {MONTHS[act.month - 1][:3]}"
    return shown, (f"({orig.day} {MONTHS[orig.month - 1][:3]})" if orig else "")


# When something happens: (event, who, time limit, where it comes from)
EVENTS = [
    ("You sell or transfer a specified asset (property or shares)", "Seller", "Capital Gains Tax due within 30 days of title being formally transferred to the buyer", "Capital Gains Tax Act s26(1)(b)"),
    ("A bank, agent or conveyancer pays the seller of a specified asset", "Whoever pays", "Withhold the capital gains tax and pay it to ZIMRA by the 3rd working day after payment", "Capital Gains Tax Act s22C-22E; ZIMRA"),
    ("You pay a dividend to a non-resident shareholder", "Company", "Non-resident shareholders' tax within 30 days of payment or crediting", "ZIMRA tax payment dates"),
    ("You pay a dividend to a resident, or pay fees, royalties or remittances abroad, or pay interest to a resident", "Payer", "The withholding tax is due within 10 days of payment or crediting", "ZIMRA tax payment dates"),
    ("You pay a non-executive director's fees", "Company", "Tax within 10 days of payment or crediting", "ZIMRA tax payment dates"),
    ("You pay an agent's commission on a property or insurance sale", "Payer", "By the 10th of the month after the month of payment", "ZIMRA tax payment dates"),
    ("You buy services from a supplier outside Zimbabwe", "VAT-registered businesses", "VAT on imported services within 30 days of the invoice or of payment, whichever is earlier", "ZIMRA tax payment dates"),
    ("ZIMRA sends you an assessment", "Taxpayer", "Object in writing within 30 days of the notice", "Capital Gains Tax Act s25(1) and the Income Tax Act"),
    ("A company allots shares", "Company", "Return of allotments to the Registrar within one month", "COBE Act s121(2)"),
    ("A company passes a special resolution", "Company", "A copy to the Registrar within one month, or it has no force", "COBE Act s178(1)"),
    ("A director or the company secretary changes", "Company", "Tell the Registrar within one month", "COBE Act s217(4)"),
    ("The people who own or control a company change in a material way", "Company", "Update the beneficial ownership information within 7 days", "COBE Act s72(2)"),
    ("A company gives a mortgage or debenture over its assets", "Company", "Enter it in the register within 14 days", "COBE Act s155(1)(a)"),
    ("A company holds a meeting of members", "Company", "Minutes written up within 20 days of the meeting", "COBE Act s180(1)"),
    ("A company's incorporation anniversary comes round", "Company", "File the annual return with the Registrar within 21 days of the anniversary", "COBE Act s165(2)"),
    ("A company holds its annual general meeting", "Company", "One every 12 months; 21 days' notice; send the financial statements at least 14 days before", "COBE Act s167, s169, s190"),
]

NOT_DATED = [
    ("Your company's own annual return date", f"21 days after the anniversary of incorporation. Write your anniversary here: ______________"),
    ("Your year-end and your audit date", "Fill in the dates agreed with your auditor."),
    ("Loan covenant reports and lender dates", "Take them from your loan agreements."),
    ("Licences, vehicle licences and insurance renewals", "Take them from the certificates."),
    ("Tax clearance certificate expiry", "Renew before it expires if you tender or are paid by government."),
]


def sources():
    return [
        "ZIMRA: Public Notice 61 of 2026 (returns by the 5th, payments by the 10th, Statutory Instrument 81 of 2025) and the Tax payment dates page",
        "PwC Worldwide Tax Summaries, Zimbabwe: tax administration",
        "Companies and Other Business Entities Act [Chapter 24:31]",
        "Capital Gains Tax Act [Chapter 23:01]",
        "Published Zimbabwe public holiday lists; the Presidential declaration of Munhumutapa Day, 15 September, from 2026",
    ]


def filename(year, ext):
    return f"neverlank-compliance-calendar-{year}.{ext}"


def onepager_filename(year):
    return f"neverlank-compliance-calendar-{year}-one-page.pdf"


def _fmt(d):
    return f"{DAYS[d.weekday()][:3]} {d.day} {MONTHS[d.month - 1][:3]}"


def _long(d):
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year}"


# ------------------------------------------------------------------ PDF

def build_pdf(year, reviewed=""):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)

    c = lambda h: colors.HexColor("#" + h)
    navy, gold, ink, muted, line, tint = c(NAVY), c(GOLD), c(INK), c(MUTED), c(LINE), c(TINT)
    base = ParagraphStyle("b", fontName="Helvetica", fontSize=9, leading=12, textColor=ink)
    small = ParagraphStyle("s", parent=base, fontSize=7.8, leading=10, textColor=muted)
    bold = ParagraphStyle("bd", parent=base, fontName="Helvetica-Bold")
    head = ParagraphStyle("h", parent=base, fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=navy, spaceBefore=2, spaceAfter=6)
    sub = ParagraphStyle("sub", parent=base, fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=navy, spaceBefore=6, spaceAfter=3)
    white = ParagraphStyle("w", parent=base, textColor=colors.white, fontName="Helvetica-Bold", fontSize=9.5)
    hol = ParagraphStyle("hol", parent=base, textColor=muted, fontName="Helvetica-Oblique")
    cover_t = ParagraphStyle("ct", parent=base, fontName="Helvetica-Bold", fontSize=30, leading=34, textColor=colors.white)
    cover_s = ParagraphStyle("cs", parent=base, fontSize=12, leading=16, textColor=c("D4AF6A"))
    P = lambda t, st=base: Paragraph(escape(str(t)).replace("\n", "<br/>"), st)
    PB = lambda t, st=base: Paragraph(t, st)       # already-escaped markup

    W, H = A4
    left = right = 15 * mm
    width = W - left - right
    hmap = holiday_map(year)
    all_items = items(year)
    review_line = (reviewed or "").strip()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(navy)
        canvas.rect(0, 0, W, 11 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(left, 4.2 * mm, f"{FIRM['name']}  |  {FIRM['web']}  |  {FIRM['email']}  |  {FIRM['phone']}")
        canvas.drawRightString(W - right, 4.2 * mm, f"{year} Compliance Calendar  |  page {doc.page}")
        canvas.restoreState()

    def band(canvas, doc):
        footer(canvas, doc)
        canvas.saveState()
        canvas.setFillColor(navy)
        canvas.rect(0, H - 14 * mm, W, 14 * mm, stroke=0, fill=1)
        canvas.setFillColor(gold)
        canvas.rect(0, H - 15 * mm, W, 1 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(left, H - 9 * mm, FIRM["name"])
        canvas.setFont("Helvetica", 8.5)
        canvas.drawRightString(W - right, H - 9 * mm, f"{year} Compliance Calendar")
        canvas.restoreState()

    def cover(canvas, doc):
        footer(canvas, doc)
        canvas.saveState()
        canvas.setFillColor(navy)
        canvas.rect(0, H - 118 * mm, W, 118 * mm, stroke=0, fill=1)
        canvas.setFillColor(gold)
        canvas.rect(0, H - 120 * mm, W, 2 * mm, stroke=0, fill=1)
        if os.path.exists(LOGO):
            try:
                canvas.drawImage(LOGO, left, H - 48 * mm, width=30 * mm, height=30 * mm, mask="auto", preserveAspectRatio=True)
            except Exception:
                pass
        canvas.restoreState()

    doc = BaseDocTemplate(io.BytesIO(), pagesize=A4, title=f"Neverlank Compliance Calendar {year}", author=FIRM["name"],
                          subject="Zimbabwe tax and company-law dates", leftMargin=left, rightMargin=right)
    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, title=f"Neverlank Compliance Calendar {year}", author=FIRM["name"],
                          subject="Zimbabwe tax and company-law dates", leftMargin=left, rightMargin=right)
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[Frame(left, 16 * mm, width, H - 16 * mm - 124 * mm, id="c", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=cover),
        PageTemplate(id="body", frames=[Frame(left, 15 * mm, width, H - 15 * mm - 22 * mm, id="b", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=band),
    ])
    from reportlab.platypus import NextPageTemplate
    story = []

    # ---- cover: the title sits on the navy block, so it is drawn with a table into the top frame area via negative spacer is avoided:
    # the cover frame starts below the block; the title is a Paragraph block placed first on a separate canvas pass.
    def cover_title(canvas, doc):
        pass

    # Title text is drawn on the navy block by a custom onPage for the cover
    def cover_page(canvas, doc):
        cover(canvas, doc)
        canvas.saveState()
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawString(left + 36 * mm, H - 28 * mm, FIRM["name"].upper())
        canvas.setFillColor(c("D4AF6A"))
        canvas.setFont("Helvetica", 9.5)
        canvas.drawString(left + 36 * mm, H - 34 * mm, FIRM["strapline"])
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 34)
        canvas.drawString(left, H - 74 * mm, "Compliance Calendar")
        canvas.setFillColor(c("D4AF6A"))
        canvas.setFont("Helvetica-Bold", 44)
        canvas.drawString(left, H - 92 * mm, str(year))
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica", 11)
        canvas.drawString(left, H - 106 * mm, "The tax, payroll and company-law dates for the year, month by month.")
        canvas.setFont("Helvetica", 9)
        canvas.drawString(left, H - 112 * mm, f"Put a name next to every date. Look at it once a month.")
        canvas.restoreState()

    doc.pageTemplates[0].onPage = cover_page

    # ---- page 1 body: how to use + the dates that matter most
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Make it work in ten minutes", head))
    steps = [
        ("1", "Put a name next to every date.", "Every date in this calendar needs one person who is responsible, and one person who checks."),
        ("2", "Add your own dates.", "Your year-end, your lenders' and customers' deadlines, your licence and insurance renewals. The last page has room."),
        ("3", "Look at it for 15 minutes in the first week of every month.", "Ask: what is due this month, who has it, is it on track? Pay or file by the day shown under \"act by\"."),
    ]
    rows = [[P(n, ParagraphStyle("n", parent=bold, fontSize=16, textColor=gold, alignment=TA_CENTER)), PB(f"<b>{escape(t)}</b><br/>{escape(d)}")] for n, t, d in steps]
    t = Table(rows, colWidths=[12 * mm, width - 12 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, -2), 0.4, line),
                           ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    inside = [("The year at a glance", "every return and payment date in one table, with the VAT categories"),
              ("Month by month", "each date with who it applies to, and the last working day to act by"),
              ("Public holidays", f"the {year} holidays, so you know which due dates move"),
              ("When something happens", "time limits that start the day you sell an asset, allot shares or change a director"),
              ("Your own dates", "room for your year-end, lenders, licences and insurance")]
    irows = [[P(a_, bold), P(b_, small)] for a_, b_ in inside]
    it_ = Table(irows, colWidths=[44 * mm, width - 44 * mm])
    it_.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, -2), 0.4, line),
                             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [t, Spacer(1, 6 * mm), Paragraph("What is inside", head), it_, Spacer(1, 7 * mm),
              Table([[PB("<font color='#FFFFFF'><b>Free 20-minute compliance check</b><br/>We go through your dates with you and tell you plainly where the real risks are. "
                         "You keep the notes whether or not we ever work together. WhatsApp " + escape(FIRM["phone"]) + " or write to " + escape(FIRM["email"]) + ".</font>",
                         ParagraphStyle("cv", parent=base, leading=13))]], colWidths=[width], style=TableStyle([
                  ("BACKGROUND", (0, 0), (-1, -1), navy), ("LINEBEFORE", (0, 0), (0, -1), 3, gold), ("LEFTPADDING", (0, 0), (-1, -1), 10),
                  ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)])),
              NextPageTemplate("body"), PageBreak(),
              Paragraph(f"{year} month-by-month tax calendar", head)]
    grows = [[P("Filing month", white), P("Period covered", white), P("Returns (5th)", white), P("Payments (10th)", white), P("VAT (15th)", white), P("Quarterly and annual", white)]]
    def cellp(pr, note=""):
        shown, orig = glance_text(pr)
        sub_ = (f"<i>{orig}</i> " if orig else "") + note
        if orig:
            return PB(f"<b>{shown}</b><br/><font color='#6B7684' size='7.5'>{sub_}</font>")
        return PB(shown + (f"<br/><font color='#6B7684' size='7.5'>{note}</font>" if note else ""))
    for g in glance(year):
        extra = []
        for label, pr in g["extra"]:
            if pr is None:
                extra.append(f"<font size='7.5' color='#6B7684'>{escape(label)}</font>")
                continue
            shown, orig = glance_text(pr)
            extra.append(f"<b>{escape(label)}</b>: {shown}" + (f" <font color='#6B7684' size='7.5'><i>{orig}</i></font>" if orig else ""))
        grows.append([P(g["month"], bold), P(g["period"]), cellp(g["returns"]), cellp(g["payments"]), cellp(g["vat"], g["vat_cats"]), PB("<br/>".join(extra)) if extra else P("-")])
    kt = Table(grows, colWidths=[21 * mm, 19 * mm, 22 * mm, 22 * mm, 33 * mm, width - 117 * mm], repeatRows=1)
    kt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), navy), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, tint]),
                            ("LINEBELOW", (0, 0), (-1, -1), 0.3, line), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("TOPPADDING", (0, 0), (-1, -1), 2.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6)]))
    story += [kt, Spacer(1, 2 * mm), P("Bold dates are the day to act by. A date in brackets is the legal date when it falls on a weekend or public holiday. "
                                       "NSSA, ZIMDEF and NEC payments are usually due on the 10th as well; excise duty on the 20th. The pages that follow list every date.", small),
              Spacer(1, 4 * mm), Paragraph("Which VAT category are you?", sub)]
    vrows = [[P("Category", white), P("How often", white), P("Tax period ends in", white), P("File and pay in", white)]]
    for cat, freq, ends, files in VAT_CATEGORIES:
        vrows.append([P("Category " + cat, bold), P(freq), P(ends), P(files)])
    vt = Table(vrows, colWidths=[24 * mm, 28 * mm, (width - 52 * mm) / 2, (width - 52 * mm) / 2])
    vt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), navy), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, tint]),
                            ("LINEBELOW", (0, 0), (-1, -1), 0.3, line), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story += [vt, Spacer(1, 1.5 * mm), P("Your category is on your VAT registration certificate. If you are not sure, ask us.", small)]
    story.append(PageBreak())

    # ---- month pages
    story.append(Paragraph(f"Month by month, {year}", head))
    story.append(P(HOLIDAY_ADVICE, small))
    story.append(Spacer(1, 2 * mm))
    for month in range(1, 13):
        month_items = [it for it in all_items if it["date"].month == month]
        holiday_rows = [(d, n) for d, n in sorted(hmap.items()) if d.month == month]
        by_date = {}
        for it in month_items:
            by_date.setdefault(it["date"], []).append(it)
        for d, n in holiday_rows:
            by_date.setdefault(d, [])
        rows = [[PB(f"<b>{MONTHS[month - 1].upper()} {year}</b>", white), "", ""]]
        styles = [("BACKGROUND", (0, 0), (-1, 0), navy), ("SPAN", (0, 0), (-1, 0)), ("LINEBELOW", (0, 1), (-1, -1), 0.3, line),
                  ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        for d in sorted(by_date):
            group = by_date[d]
            real = [g for g in group if g["group"] != "advice"]
            adv = [g for g in group if g["group"] == "advice"]
            lines = []
            for g in real:
                lines.append(f"<b>{escape(g['what'])}</b>" + (f" <font color='#6B7684'>({escape(g['who'])})</font>" if g["who"] not in ("Everyone",) else ""))
            for g in adv:
                lines.append(f"<font color='#B8934A'><b>Suggestion:</b></font> {escape(g['what'])}")
            if d in hmap:
                lines.insert(0, f"<i>Public holiday: {escape(hmap[d])}</i>")
            flags = [g["flag"] for g in real if g["flag"]]
            right_cell = PB("<br/>".join(escape(f) for f in dict.fromkeys(flags)), small) if flags else ""
            row_i = len(rows)
            rows.append([P(_fmt(d), bold), PB("<br/>".join(lines)), right_cell])
            if d in hmap and not real and not adv:
                styles.append(("BACKGROUND", (0, row_i), (-1, row_i), tint))
        if len(rows) == 1:
            rows.append([P(""), P("No dates this month."), P("")])
        mt = Table(rows, colWidths=[23 * mm, width - 23 * mm - 52 * mm, 52 * mm])
        mt.setStyle(TableStyle(styles))
        story.append(KeepTogether([mt, Spacer(1, 5 * mm)]))

    # ---- holidays
    story.append(PageBreak())
    story.append(Paragraph(f"Public holidays {year}", head))
    hrows = [[P("Date", white), P("Holiday", white), P("Note", white)]]
    for d, name, note in holidays(year):
        hrows.append([P(_long(d), bold), P(name), P(note, small)])
    ht = Table(hrows, colWidths=[52 * mm, 62 * mm, width - 114 * mm], repeatRows=1)
    ht.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), navy), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, tint]),
                            ("LINEBELOW", (0, 0), (-1, -1), 0.3, line), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]))
    story += [ht, Spacer(1, 2 * mm),
              P("A holiday on a Sunday is taken on the next day. The Government can declare other holidays; watch for notices in the Government Gazette.", small)]

    # ---- events
    story += [Spacer(1, 8 * mm), Paragraph("When something happens", head),
              P("These time limits do not sit on a calendar date. They start the day something happens in your business, so the clock starts with a person who has to notice it.", base),
              Spacer(1, 2 * mm)]
    erows = [[P("What happens", white), P("What you must do, and by when", white), P("Where it comes from", white)]]
    for ev, who, rule, src in EVENTS:
        erows.append([P(ev, bold), P(rule), P(src, small)])
    et = Table(erows, colWidths=[56 * mm, width - 56 * mm - 40 * mm, 40 * mm], repeatRows=1)
    et.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), navy), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, tint]),
                            ("LINEBELOW", (0, 0), (-1, -1), 0.3, line), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]))
    story.append(et)

    # ---- your own dates
    story += [PageBreak(), Paragraph("Your own dates", head),
              P("Write in the dates only your business has, and a name next to each.", base), Spacer(1, 2 * mm)]
    own = [[P("What", white), P("Date", white), P("Who is responsible", white), P("Who checks", white)]]
    for what, hint in NOT_DATED:
        own.append([PB(f"<b>{escape(what)}</b><br/><font size=7.5 color='#6B7684'>{escape(hint)}</font>"), "", "", ""])
    for _ in range(6):
        own.append(["", "", "", ""])
    ot = Table(own, colWidths=[width * 0.40, width * 0.16, width * 0.24, width * 0.20], repeatRows=1, rowHeights=[None] * (1 + len(NOT_DATED)) + [9 * mm] * 6)
    ot.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), navy), ("GRID", (0, 0), (-1, -1), 0.4, line), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]))
    story.append(ot)

    # ---- closing
    cta = Table([[PB(f"<font color='#FFFFFF'><b>Want a second pair of eyes?</b><br/>We offer a free 20-minute compliance check: we go through your dates with you and tell "
                     f"you plainly where the real risks are. You keep the notes whether or not we ever work together.<br/><br/>"
                     f"<b>WhatsApp {escape(FIRM['phone'])}</b>  |  {escape(FIRM['email'])}  |  {escape(FIRM['web'])}</font>", ParagraphStyle("cta", parent=base, leading=13))]],
                colWidths=[width])
    cta.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), navy), ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                             ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10), ("LINEBEFORE", (0, 0), (0, -1), 3, gold)]))
    notes = [P("Please read this", sub), P(NOTICE, small)]
    if review_line:
        notes.append(P(review_line, small))
    notes += [Spacer(1, 2 * mm), P("Where the dates come from", sub)] + [P("- " + s, small) for s in sources()]
    story += [Spacer(1, 6 * mm), KeepTogether([cta, Spacer(1, 4 * mm)] + notes)]

    doc.build(story)
    return buf.getvalue()


# ------------------------------------------------------------------ one page

def build_onepager(year, reviewed=""):
    """The year on one A4 page, branded: the monthly rhythm, the quarterly and yearly dates, the VAT categories and the holidays that move dates."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle

    c = lambda h: colors.HexColor("#" + h)
    navy, gold, ink, muted, line, tint = c(NAVY), c(GOLD), c(INK), c(MUTED), c(LINE), c(TINT)
    base = ParagraphStyle("b", fontName="Helvetica", fontSize=8.2, leading=10.4, textColor=ink)
    small = ParagraphStyle("s", parent=base, fontSize=6.9, leading=8.6, textColor=muted)
    bold = ParagraphStyle("bd", parent=base, fontName="Helvetica-Bold")
    white = ParagraphStyle("w", parent=base, textColor=colors.white, fontName="Helvetica-Bold", fontSize=8.2)
    sec = ParagraphStyle("sec", parent=base, fontName="Helvetica-Bold", fontSize=9.6, leading=12, textColor=navy, spaceBefore=3, spaceAfter=2)
    P = lambda t, st=base: Paragraph(escape(str(t)).replace("\n", "<br/>"), st)
    PB = lambda t, st=base: Paragraph(t, st)

    W, H = A4
    left = right = 13 * mm
    width = W - left - right
    hmap = holiday_map(year)

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(navy)
        canvas.rect(0, H - 34 * mm, W, 34 * mm, stroke=0, fill=1)
        canvas.setFillColor(gold)
        canvas.rect(0, H - 35.2 * mm, W, 1.2 * mm, stroke=0, fill=1)
        if os.path.exists(LOGO):
            try:
                canvas.drawImage(LOGO, left, H - 30 * mm, width=24 * mm, height=24 * mm, mask="auto", preserveAspectRatio=True)
            except Exception:
                pass
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(left + 28 * mm, H - 11 * mm, FIRM["name"].upper())
        canvas.setFont("Helvetica-Bold", 22)
        canvas.drawString(left + 28 * mm, H - 21 * mm, f"Compliance Calendar {year}")
        canvas.setFillColor(c("D4AF6A"))
        canvas.setFont("Helvetica", 9)
        canvas.drawString(left + 28 * mm, H - 28 * mm, "The year on one page. The full calendar has every date, month by month.")
        canvas.setFillColor(navy)
        canvas.rect(0, 0, W, 10 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica", 7.2)
        canvas.drawString(left, 3.8 * mm, f"{FIRM['name']}  |  {FIRM['web']}  |  {FIRM['email']}  |  WhatsApp {FIRM['phone']}  |  {FIRM['paab']}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, title=f"Neverlank Compliance Calendar {year}: one page", author=FIRM["name"], leftMargin=left, rightMargin=right)
    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(left, 12 * mm, width, H - 12 * mm - 39 * mm, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=page)])

    def tbl(rows, widths, head=True, zebra=True):
        t = Table(rows, colWidths=widths)
        st = [("LINEBELOW", (0, 0), (-1, -1), 0.3, line), ("VALIGN", (0, 0), (-1, -1), "TOP"),
              ("TOPPADDING", (0, 0), (-1, -1), 2.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
        if head:
            st.append(("BACKGROUND", (0, 0), (-1, 0), navy))
        if zebra:
            st.append(("ROWBACKGROUNDS", (0, 1 if head else 0), (-1, -1), [colors.white, tint]))
        t.setStyle(TableStyle(st))
        return t

    story = []
    # 1 monthly rhythm
    story.append(Paragraph("Every month", sec))
    rhythm = [[P("Day", white), P("What is due", white), P("Who", white)],
              [P("5th", bold), P("Returns for last month: PAYE (with the AIDS levy), withholding taxes, presumptive tax and the other monthly returns"), P("Employers and anyone who withholds tax")],
              [P("10th", bold), P("Payment of those taxes. Also NSSA, ZIMDEF and NEC levies (the usual date: confirm)"), P("Employers")],
              [P("15th", bold), P("VAT return and payment"), P("VAT-registered businesses")],
              [P("20th", bold), P("Excise duty (the usual date: confirm)"), P("Manufacturers of excisable goods")]]
    story.append(tbl(rhythm, [14 * mm, width - 14 * mm - 52 * mm, 52 * mm]))
    story.append(Spacer(1, 1.5 * mm))
    story.append(P("If the date is a weekend or public holiday, act by the last working day before it.", small))

    # 2 quarterly and yearly
    story.append(Paragraph("Income tax: quarterly and yearly", sec))
    g = {x["month"]: x for x in glance(year)}
    def f(pr):
        a, o = glance_text(pr)
        return f"<b>{a}</b>" + (f" <font color='#6B7684' size='6.9'>{o}</font>" if o else "")
    q = [[P("Date", white), P("What", white), P("Date", white), P("What", white)]]
    qd = {}
    for mth, label in (("March", "1"), ("June", "2"), ("September", "3"), ("December", "4")):
        ex = g[mth]["extra"]
        qd[label] = (ex[0][1], ex[1][1], ex[1][0].split("(")[1].rstrip(")"))
    q.append([PB(f(qd["1"][0])), P("QPD 1 return"), PB(f(qd["1"][1])), P("QPD 1 payment: 10%")])
    q.append([PB(f(qd["2"][0])), P("QPD 2 return"), PB(f(qd["2"][1])), P("QPD 2 payment: 25%")])
    q.append([PB(f(qd["3"][0])), P("QPD 3 return"), PB(f(qd["3"][1])), P("QPD 3 payment: 30%")])
    q.append([PB(f(qd["4"][0])), P("QPD 4 return"), PB(f(qd["4"][1])), P("QPD 4 payment: 35%")])
    ann = glance(year)[3]["extra"][0][1]
    q.append([PB(f(ann)), P("Annual income tax return (ITF12C) for " + str(year - 1) + ", with the financial statements; pay any balance"), PB("<b>End Jan</b>"), P("Employers' annual PAYE return (ITF16): ZIMRA announces the date")])
    story.append(tbl(q, [30 * mm, (width - 60 * mm) / 2, 30 * mm, (width - 60 * mm) / 2]))

    # 3 months at a glance: dates that move
    story.append(Paragraph(f"Your {year} dates that move", sec))
    mv = []
    for gm in glance(year):
        moved = [(k, gm[k]) for k in ("returns", "payments", "vat") if gm[k][1]]
        if moved:
            names = {"returns": "5th", "payments": "10th", "vat": "15th"}
            mv.append(f"<b>{gm['month'][:3]}</b>: " + ", ".join(f"{names[k]} to {glance_text(pr)[0]}" for k, pr in moved))
    half = (len(mv) + 1) // 2
    mrows = [[PB("<br/>".join(mv[:half]), small), PB("<br/>".join(mv[half:]), small)]]
    mt = Table(mrows, colWidths=[width / 2, width / 2])
    mt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, -1), tint), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [mt, Spacer(1, 1 * mm), P("These monthly dates fall on a weekend or public holiday, so the last working day before them is shown.", small)]

    # 4 VAT categories + holidays side by side
    vat = [[P("VAT category", white), P("Files and pays in", white)]]
    for cat, freq, ends, files in VAT_CATEGORIES:
        vat.append([PB(f"<b>{cat}</b> <font color='#6B7684' size='6.9'>{escape(freq.lower())}</font>"), P(files if cat != "D" else "Month after the approved period")])
    vt = tbl(vat, [24 * mm, width * 0.40 - 8 * mm - 24 * mm])
    hrows = [[P("Holiday", white), P("Date", white)]]
    for d, name, note in holidays(year):
        short = name.replace("Robert Gabriel Mugabe National Youth Day", "Youth Day").replace(" (observed)", "*")
        hrows.append([P(short), P(f"{DAYS[d.weekday()][:3]} {d.day} {MONTHS[d.month - 1][:3]}", bold)])
    half_h = (len(hrows) - 1 + 1) // 2
    ht_l = tbl(hrows[:1] + hrows[1:1 + half_h], [(width / 2 - 6 * mm) * 0.28, (width / 2 - 6 * mm) * 0.14]) if False else None
    two = [[hrows[0][0], hrows[0][1], hrows[0][0], hrows[0][1]]]
    body = hrows[1:]
    h2 = (len(body) + 1) // 2
    for i in range(h2):
        l = body[i]
        r_ = body[i + h2] if i + h2 < len(body) else [P(""), P("")]
        two.append([l[0], l[1], r_[0], r_[1]])
    hw = width * 0.60 - 3 * mm
    ht = Table(two, colWidths=[hw * 0.31, hw * 0.19, hw * 0.31, hw * 0.19])
    ht.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), navy), ("LINEBELOW", (0, 0), (-1, -1), 0.3, line), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, tint]),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                            ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    side = Table([[Paragraph("VAT: which category are you?", sec), Paragraph(f"Public holidays {year}", sec)], [vt, ht]], colWidths=[width * 0.40, width * 0.60])
    side.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (0, -1), 8), ("RIGHTPADDING", (1, 0), (1, -1), 0)]))
    story += [side, Spacer(1, 0.8 * mm), P("* observed on the next day because the holiday falls on a Sunday. Your VAT category is on your VAT registration certificate.", small)]

    # 5 event rules + call to action
    story.append(Paragraph("When something happens, the clock starts", sec))
    rules = [("Sell property or shares", "Capital Gains Tax within 30 days of transfer; withholding by the 3rd working day"),
             ("Allot shares or change a director", "Tell the Registrar within one month"),
             ("Company's incorporation anniversary", "Annual return within 21 days"),
             ("Pay fees, royalties or dividends abroad", "Withholding tax within 10 days (30 days for non-resident shareholders)"),
             ("Receive a ZIMRA assessment", "Object in writing within 30 days")]
    rr = [[PB(f"<b>{escape(a)}</b>"), P(b)] for a, b in rules]
    story.append(tbl(rr, [58 * mm, width - 58 * mm], head=False))
    story.append(Spacer(1, 3 * mm))
    cta = Table([[PB("<font color='#FFFFFF'><b>Ten minutes makes it work.</b> Put a name next to every date, add your own year-end and lender dates, and look at the calendar for "
                     "15 minutes in the first week of each month. <b>Want a second pair of eyes?</b> We do a free 20-minute compliance check: WhatsApp "
                     + escape(FIRM["phone"]) + " or write to " + escape(FIRM["email"]) + ".</font>", ParagraphStyle("cta", parent=base, leading=11.2, fontSize=8.4))]], colWidths=[width])
    cta.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), navy), ("LINEBEFORE", (0, 0), (0, -1), 3, gold), ("LEFTPADDING", (0, 0), (-1, -1), 9),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.append(cta)
    foot = f"Put together on {PREPARED} from ZIMRA's published dates (Public Notice 61 of 2026), the COBE Act, the Capital Gains Tax Act and published holiday lists. " \
           "Dates can change: confirm any that matter with us or ZIMRA. General information, not advice for your own business." + (" " + reviewed.strip() if (reviewed or "").strip() else "")
    story += [Spacer(1, 2 * mm), P(foot, small)]
    doc.build(story)
    return buf.getvalue()


# ------------------------------------------------------------------ Excel

def build_xlsx(year, reviewed=""):
    from openpyxl import Workbook
    from openpyxl.formatting.rule import CellIsRule, FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    navy = PatternFill("solid", fgColor=NAVY)
    tint = PatternFill("solid", fgColor=TINT)
    gold_fill = PatternFill("solid", fgColor="F3E6C8")
    side = Side(style="thin", color=LINE)
    border = Border(bottom=side)
    hfont = Font(name="Calibri", bold=True, color="FFFFFF", size=10.5)
    wrap = Alignment(wrap_text=True, vertical="top")
    hmap = holiday_map(year)

    def titled(ws, title, subtitle, ncols):
        ws.sheet_view.showGridLines = False
        ws["A1"] = f"{FIRM['name']}  |  {title}"
        ws["A1"].font = Font(name="Georgia", bold=True, size=15, color="FFFFFF")
        ws["A2"] = subtitle
        ws["A2"].font = Font(name="Calibri", size=10, color="D4AF6A")
        for r in (1, 2):
            for col in range(1, ncols + 1):
                ws.cell(r, col).fill = navy
        ws.row_dimensions[1].height = 26
        ws.row_dimensions[2].height = 18

    def header(ws, row, names, widths):
        for i, (n, w) in enumerate(zip(names, widths), start=1):
            cell = ws.cell(row, i, n)
            cell.font, cell.fill, cell.alignment = hfont, navy, Alignment(wrap_text=True, vertical="center")
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.row_dimensions[row].height = 30

    def page(ws, last_col, header_row):
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_title_rows = f"{header_row}:{header_row}"
        ws.oddFooter.left.text = f"{FIRM['name']}  |  {FIRM['web']}"
        ws.oddFooter.right.text = "Page &P of &N"

    # ---- Key dates
    ws = wb.active
    ws.title = "Key dates"
    cols = ["Date", "Day", "Month", "What is due", "Who it applies to", "How the date is set", "Act by (last working day)", "Owner", "Done?", "Notes", "Days to go"]
    titled(ws, f"Compliance Calendar {year}", "Put a name in the Owner column for every date, mark Done when it is done, and filter by month, owner or who it applies to.", len(cols))
    ws["A3"] = HOLIDAY_ADVICE
    ws["A3"].font = Font(name="Calibri", italic=True, size=9, color=MUTED)
    hr = 4
    header(ws, hr, cols, [13, 11, 11, 62, 34, 32, 17, 16, 9, 34, 11])
    row = hr + 1
    for it in items(year):
        ws.cell(row, 1, it["date"]).number_format = "dd mmm yyyy"
        ws.cell(row, 2, it["day"][:3])
        ws.cell(row, 3, MONTHS[it["date"].month - 1])
        ws.cell(row, 4, it["what"])
        ws.cell(row, 5, it["who"])
        ws.cell(row, 6, it["basis"])
        c7 = ws.cell(row, 7, it["act_by"])
        c7.number_format = "ddd dd mmm yyyy"
        ws.cell(row, 10, it["flag"])
        ws.cell(row, 11, f'=IF(I{row}="Yes","",A{row}-TODAY())')
        for col in range(1, 12):
            cell = ws.cell(row, col)
            cell.alignment = wrap
            cell.border = border
            cell.font = Font(name="Calibri", size=10, bold=(col == 4 and it["group"] != "advice"), color=("8A6A2A" if it["group"] == "advice" else INK))
            if it["group"] == "quarterly" or (it["group"] == "yearly"):
                cell.fill = gold_fill
        row += 1
    last = row - 1
    ws.freeze_panes = ws.cell(hr + 1, 5)
    ws.auto_filter.ref = f"A{hr}:K{last}"
    dv = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"I{hr + 1}:I{last}")
    ws.conditional_formatting.add(f"K{hr + 1}:K{last}", CellIsRule(operator="lessThan", formula=["0"], font=Font(color="B3261E", bold=True)))
    ws.conditional_formatting.add(f"K{hr + 1}:K{last}", CellIsRule(operator="between", formula=["0", "7"], fill=PatternFill("solid", bgColor="F3E6C8"), font=Font(bold=True)))
    ws.conditional_formatting.add(f"A{hr + 1}:J{last}", FormulaRule(formula=[f'$I{hr + 1}="Yes"'], font=Font(color="9AA3AF", strike=True)))
    page(ws, 11, hr)

    # ---- Year at a glance
    wg = wb.create_sheet("Year at a glance")
    titled(wg, f"{year} month-by-month tax calendar", "Bold = the day to act by. A date in brackets is the legal date when it falls on a weekend or public holiday.", 6)
    header(wg, 4, ["Filing month", "Period covered", "Returns (5th)", "Payments (10th)", "VAT (15th)", "Quarterly and annual"], [14, 14, 20, 20, 20, 60])
    def shown(pr):
        a, o = glance_text(pr)
        return a + (f" {o}" if o else "")
    for i, g in enumerate(glance(year), start=5):
        extra = [lbl if pr is None else f"{lbl}: {shown(pr)}" for lbl, pr in g["extra"]]
        vals = [g["month"], g["period"], shown(g["returns"]), shown(g["payments"]), shown(g["vat"]) + "\n" + g["vat_cats"], "\n".join(extra) or "-"]
        for col, v in enumerate(vals, start=1):
            cell = wg.cell(i, col, v)
            cell.alignment = wrap
            cell.border = border
            cell.font = Font(name="Calibri", size=10, bold=(col == 1))
            if i % 2 == 0:
                cell.fill = tint
        wg.row_dimensions[i].height = 15 * max(1, len(extra))
    vr = 5 + 12 + 1
    wg.cell(vr, 1, "Which VAT category are you?").font = Font(name="Calibri", bold=True, size=11, color=NAVY)
    for col, n in enumerate(["Category", "How often", "Tax period ends in", "File and pay in"], start=1):
        c_ = wg.cell(vr + 1, col, n)
        c_.font, c_.fill = hfont, navy
    for i, (cat, freq, ends, files) in enumerate(VAT_CATEGORIES, start=vr + 2):
        for col, v in enumerate(("Category " + cat, freq, ends, files), start=1):
            cell = wg.cell(i, col, v)
            cell.alignment = wrap
            cell.border = border
    wg.cell(vr + 6, 1, "Your category is on your VAT registration certificate. If you are not sure, ask us.").font = Font(name="Calibri", italic=True, size=9, color=MUTED)
    page(wg, 6, 4)

    # ---- Public holidays
    wh = wb.create_sheet("Public holidays")
    titled(wh, f"Public holidays {year}", "A holiday on a Sunday is taken on the next day. Watch the Government Gazette for any extra holiday.", 3)
    header(wh, 4, ["Date", "Holiday", "Note"], [24, 44, 70])
    for i, (d, name, note) in enumerate(holidays(year), start=5):
        wh.cell(i, 1, d).number_format = "dddd dd mmm yyyy"
        wh.cell(i, 2, name)
        wh.cell(i, 3, note)
        for col in range(1, 4):
            wh.cell(i, col).border = border
            wh.cell(i, col).font = Font(name="Calibri", size=10)
    page(wh, 3, 4)

    # ---- When something happens
    we = wb.create_sheet("When something happens")
    titled(we, "When something happens", "Time limits that start the day something happens in your business.", 4)
    header(we, 4, ["What happens", "Who", "What you must do, and by when", "Where it comes from"], [52, 22, 70, 34])
    for i, (ev, who, rule, src) in enumerate(EVENTS, start=5):
        for col, v in enumerate((ev, who, rule, src), start=1):
            cell = we.cell(i, col, v)
            cell.alignment = wrap
            cell.border = border
            cell.font = Font(name="Calibri", size=10, bold=(col == 1))
    page(we, 4, 4)

    # ---- My own dates
    wo = wb.create_sheet("My own dates")
    titled(wo, "My own dates", "Your year-end, lenders, customers, licences, insurance. Same columns as Key dates.", 6)
    header(wo, 4, ["Date", "What is due", "Owner", "Who checks", "Done?", "Days to go"], [14, 62, 18, 18, 9, 11])
    for i, (what, hint) in enumerate(NOT_DATED, start=5):
        wo.cell(i, 2, f"{what}: {hint}")
    for i in range(5, 5 + 40):
        wo.cell(i, 1).number_format = "dd mmm yyyy"
        wo.cell(i, 6, f'=IF(OR(A{i}="",E{i}="Yes"),"",A{i}-TODAY())')
        for col in range(1, 7):
            wo.cell(i, col).border = border
            wo.cell(i, col).alignment = wrap
    dv2 = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
    wo.add_data_validation(dv2)
    dv2.add("E5:E44")
    wo.freeze_panes = "A5"
    page(wo, 6, 4)

    # ---- About
    wa = wb.create_sheet("About")
    titled(wa, "About this calendar", f"Prepared {PREPARED}", 2)
    wa.column_dimensions["A"].width = 120
    lines = ["Please read this", NOTICE]
    if (reviewed or "").strip():
        lines.append(reviewed.strip())
    lines += ["", "Where the dates come from"] + ["- " + s for s in sources()]
    lines += ["", "Want a second pair of eyes?",
              "We offer a free 20-minute compliance check: we go through your dates with you and tell you plainly where the real risks are. "
              "You keep the notes whether or not we ever work together.",
              f"WhatsApp {FIRM['phone']}  |  {FIRM['email']}  |  {FIRM['web']}  |  {FIRM['address']}  |  {FIRM['paab']}"]
    for i, text in enumerate(lines, start=4):
        cell = wa.cell(i, 1, text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        cell.font = Font(name="Calibri", size=10.5, bold=text in ("Please read this", "Where the dates come from", "Want a second pair of eyes?"), color=NAVY if text in ("Please read this", "Where the dates come from", "Want a second pair of eyes?") else INK)
    wa.sheet_view.showGridLines = False

    wb.properties.title = f"Neverlank Compliance Calendar {year}"
    wb.properties.creator = FIRM["name"]
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
