"""The Registrar's CR forms (Companies and Other Business Entities Act [Chapter 24:31]) for the Secretarial module.

CR_FORMS is the list of forms the firm uses, as supplied by the Managing Partner (CR1 to CR35), each tied to the section of the
Act that calls for it where one could be found in the Act text, and to the Secretarial activities it normally belongs to. Two
entries that are not on the CR list are added because the Act prescribes the form itself: the annual return (Fourth Schedule,
code AR) and the re-registration form (Tenth Schedule, code RR).

due_date_for() works out the last day for lodging from the date of the triggering event where the Act sets a time limit
(one month, 28 days, 7 days, 21 days). It is a hint: the preparer can type another date. Nothing here is marked verified;
the form numbers and titles are the firm's list and the time limits are from the consolidation supplied to the firm.

Pure Python.
"""
from datetime import date, timedelta

from cobe_checks import add_months

FORM_STATUSES = ["Not started", "Prepared", "Lodged", "Registered", "Queried by Registrar", "Withdrawn"]
FORM_OPEN_STATUSES = ("Not started", "Prepared", "Queried by Registrar")

GROUPS = [
    "Annual and periodic filings",
    "Directors, registered office and registers",
    "Meetings and resolutions",
    "Share capital",
    "Names, searches and copies",
    "Starting up, re-registering and ending",
    "Foreign companies",
    "Private business corporations and partnerships",
]

# code, title, used when, group, entity, [sections], (rule kind, n) or None, rule text, [activity keys]
_ROWS = [
    ("AR", "Annual return of a company (Fourth Schedule form)",
     "Every company, every year. Not on the CR list: the Act prescribes the Fourth Schedule form. The date to enter is the anniversary of incorporation, registration or re-registration.",
     "Annual and periodic filings", "Company", ["165(1)", "165(2)", "165(3)", "165(4)", "Sch4"], ("days", 21), "Within 21 days of the anniversary of incorporation, registration or re-registration (s165(2)).", ["statutory_records", "meetings"]),
    ("CR17", "Declaration that annual general meeting was held",
     "After each annual general meeting of a company, if the Registrar still asks for it.",
     "Annual and periodic filings", "Company", ["167(2)"], None, "The Act sets no period for the declaration; the AGM itself is due once in every twelve months (s167(2)).", ["meetings"]),
    ("CR33", "Declaration that an entity is still in existence",
     "In answer to a Registrar's notice that an entity appears not to be operating, or to show the entity is still carrying on business.",
     "Annual and periodic filings", "All", ["52(1)", "52(3)"], ("days", 14), "14 days from the date of the Registrar's notice to show cause (s52(1), (3)).", ["statutory_records"]),

    ("CR5", "Notice of situation of postal address of a company's registered office, registered principal place of business and any change",
     "On incorporation and whenever the registered office, postal address or principal place of business changes.",
     "Directors, registered office and registers", "All", ["31(1)", "31(2)", "31(5)"], None, "The Act sets no number of days; amend the constitutive documents when the particulars change (s31(2)). Default is a category 3 civil penalty.", ["statutory_records", "incorporation"]),
    ("CR6", "Notice of change of list of directors and secretaries",
     "Any appointment, resignation, removal or change of particulars of a director or the secretary.",
     "Directors, registered office and registers", "Company", ["217(4)", "217(5)", "198(1)"], ("months", 1), "Within one month of the change being notified to the company (s217(5)).", ["directorship"]),
    ("CR20", "Consent to act as Director of a Public Company",
     "Before a person is named as a director or proposed director of a public company.",
     "Directors, registered office and registers", "Public company", ["199(1)", "199(2)"], None, "Signed consent and share-qualification undertaking before the person is named (s199).", ["directorship", "incorporation"]),
    ("CR21", "List of persons to act as directors",
     "When a company is being registered: the list of the first directors lodged with the registration documents.",
     "Directors, registered office and registers", "Company", ["199(4)", "195(7)"], None, "Lodged with the memorandum and articles (s199(4)).", ["directorship", "incorporation"]),
    ("CR12", "Notice of place where register of mortgages and debentures is kept or any change thereto",
     "When the register of mortgages and debentures is kept anywhere other than the registered office, and whenever that place changes.",
     "Directors, registered office and registers", "Company", ["155(2)", "155(3)"], ("months", 1), "Within one month of the register first being kept there or of the change (s155(3)).", ["statutory_records"]),
    ("CR13", "Notice of situation of the office where branch register of members or debenture holders is kept or discontinuance thereof",
     "When a branch register is opened abroad, moved or discontinued.",
     "Directors, registered office and registers", "Company", ["164(2)", "156(2)"], ("months", 1), "Within one month of the opening, change or discontinuance (s164(2); s156(2) for debenture holders).", ["statutory_records"]),
    ("CR16", "Beneficiary ownership declaration form",
     "Filing and updating the beneficial ownership information of a company (more than 20% of shares or voting rights, or control).",
     "Directors, registered office and registers", "Company", ["72(1)", "72(2)", "72(4)", "2"], ("days", 7), "Updates within seven days of a material change (s72(2)).", ["statutory_records", "share_capital"]),
    ("CR1", "Application for authority to use an electronic mail address, website, portal or other interactive electronic link",
     "To use an electronic email address, website, portal or other link for notices, filings or other transactions with the Companies Office.",
     "Directors, registered office and registers", "All", ["31(3)", "31(4)"], None, "Application in the prescribed form (s31(4)).", ["statutory_records"]),

    ("CR8", "Special resolution",
     "Every special resolution (amendment of the memorandum, a merger, a major asset transaction, dissolution, or where the memorandum requires one).",
     "Meetings and resolutions", "Company", ["175(1)", "178(1)", "170(6)"], ("months", 1), "A copy to the Registrar within one month of its being passed; no force until registered (s178(1)).", ["constitutional", "meetings"]),
    ("CR34", "Alteration in company's Memorandum or Articles",
     "When the memorandum or articles are altered.",
     "Meetings and resolutions", "Company", ["79(2)", "81", "178(1)"], ("months", 1), "Special resolution lodged within one month (s178(1)); the memorandum alteration can be challenged in court within one month of the resolution (s79(5)).", ["constitutional"]),
    ("CR23", "Statutory report",
     "A company other than a private company, after its statutory meeting. Not the annual return.",
     "Meetings and resolutions", "Public company", ["166(1)", "166(5)"], ("months", 1), "Filed within one month of the report being certified (s166(5)). The statutory meeting is held one to three months after the company may commence business (s166(1)).", ["meetings", "incorporation"]),

    ("CR11", "Return of allotments",
     "Every allotment of shares.",
     "Share capital", "Company", ["121(2)", "121(3)"], ("months", 1), "Within one month of the allotment (s121(2)); with the contract if the shares are not paid in cash.", ["share_capital"]),
    ("CR10", "Registration of any increase in shares",
     "An increase of share capital beyond the registered share capital.",
     "Share capital", "Company", ["141(1)"], ("months", 1), "Within one month of the special resolution authorising the increase (s141(1)).", ["share_capital"]),
    ("CR9", "Notice of conversion, buy back of shares",
     "Consolidation, sub-division, conversion into stock, redemption or cancellation of shares; and the return on a share buy-back.",
     "Share capital", "Company", ["140(1)", "132(1)", "128(1)", "129(1)"], ("days", 28), "Return of shares bought back within 28 days of delivery (s132(1)); notice of conversion, consolidation, redemption or cancellation within one month (s140(1)). The 28 days is shown, being the shorter.", ["share_capital"]),
    ("CR22", "Statement by company of the amount or rate agreed to be paid by way of commission in respect of shares",
     "When a commission is to be paid on a share issue that is not offered to the public.",
     "Share capital", "Company", ["122(1)"], None, "Delivered to the Registrar before the commission is paid (s122(1)(c)(ii)).", ["share_capital"]),

    ("CR2", "Application for search as to availability of name",
     "To find out whether a name is available for registration. The Registrar can reserve a name for up to one month (s25(2)). This is a name search, not a search of the company file (see CR35).",
     "Names, searches and copies", "All", ["25(1)", "25(2)"], None, "A reserved name is held for not more than one month (s25(2)).", ["incorporation", "constitutional"]),
    ("CR7", "Notice of change of entity name",
     "A change of name: written approval of the Registrar first, then advertisement, then the certificate of change of name.",
     "Names, searches and copies", "All", ["26(1)", "26(2)"], None, "Registrar's written approval first (s26(1)); special resolution filed with it.", ["constitutional"]),
    ("CR35", "Application to inspect entity documents and registers",
     "To inspect a company's file at the Companies Office or obtain a certified copy; used for registry searches.",
     "Names, searches and copies", "All", ["14(1)"], None, "On payment of the prescribed fee (s14(1)).", ["statutory_records", "incorporation"]),
    ("CR3", "Application for replacement of lost, defaced and destroyed documents",
     "A filed document has been lost, defaced or destroyed, or the Registrar cannot find it. CR3 and CR4 carry the same title on the list supplied; confirm the difference with the Registrar.",
     "Names, searches and copies", "All", ["16(1)", "16(4)"], None, "No fee if the Registrar lost it (s16(2)).", ["statutory_records"]),
    ("CR4", "Application for replacement of lost, defaced and destroyed documents",
     "As CR3 (same title on the list supplied; confirm the difference with the Registrar).",
     "Names, searches and copies", "All", ["16(1)", "16(4)"], None, "No fee if the Registrar lost it (s16(2)).", ["statutory_records"]),

    ("CR18", "Declaration of shelf and shell companies per company",
     "A shelf or shell company, as the Act defines them.",
     "Starting up, re-registering and ending", "Company", ["293"], None, "See the section for what must be declared.", ["incorporation"]),
    ("CR19", "Affidavit in terms of section 158 of the Act",
     "A company's affidavit, signed by the secretary or a director, that it has complied with the conditions for commencing business, before the Registrar certifies that it may commence business.",
     "Starting up, re-registering and ending", "Public company", ["158"], None, "Delivered to the Registrar before the company commences business or exercises borrowing powers (s158).", ["incorporation"]),
    ("CR24", "Declaration of continuance of incorporation of company limited by guarantee",
     "A company limited by guarantee confirming the continuance of its incorporation.",
     "Starting up, re-registering and ending", "Company limited by guarantee", ["303(9)"], None, "Look up the exact requirement with the Registrar: the Act supplied does not name this declaration.", ["incorporation", "re_registration"]),
    ("RR", "Form for re-registration of companies and PBCs (Tenth Schedule)",
     "A company or PBC from the repealed Acts re-registering under the Act. Not on the CR list: the Act prescribes the Tenth Schedule form.",
     "Starting up, re-registering and ending", "All", ["303(9)", "303(11)", "303(12)", "Sch10"], None, "Within twelve months of the effective date (13 February 2020) on the Tenth Schedule form; the Registrar has extended it since, so confirm the current deadline (s303(9), (12)).", ["re_registration"]),
    ("CR14", "Statement by directors for strike off or voluntary wind up",
     "Every director signing that the company has ceased business and has no assets or liabilities, asking the Registrar to strike it off.",
     "Starting up, re-registering and ending", "Company", ["52(6)", "52(4)"], None, "Signed by every director; the Registrar then acts under s52.", ["statutory_records"]),

    ("CR25", "List of documents delivered for registration by a foreign company",
     "A foreign company registering a place of business in Zimbabwe.",
     "Foreign companies", "Foreign company", ["241(1)"], None, "See s241 for the documents.", ["incorporation"]),
    ("CR26", "Annual return of a foreign company",
     "Each year, for a foreign company registered in Zimbabwe.",
     "Foreign companies", "Foreign company", ["241", "242"], None, "Confirm the filing date with the Registrar; the sections supplied do not give a number of days.", ["incorporation", "statutory_records"]),
    ("CR27", "Return of alteration in the charter, constitution etc. of a foreign company",
     "A change in a foreign company's charter, name, directors or address of its principal place of business.",
     "Foreign companies", "Foreign company", ["241(4)"], ("months", 1), "Within one month of the alteration (s241(4)).", ["incorporation"]),

    ("CR15", "Application for conversion of private company into PBC",
     "A private company converting to a private business corporation: notice of the application, then the application to the Registrar.",
     "Private business corporations and partnerships", "Company", ["251"], None, "See s251 for the notice and objection procedure.", ["constitutional"]),
    ("CR28", "Incorporation statement of a private business corporation",
     "Registering a new private business corporation.",
     "Private business corporations and partnerships", "PBC", ["248"], None, "In the prescribed form with the particulars s248 lists.", ["incorporation"]),
    ("CR29", "Declaration that annual general meeting was held (PBC)",
     "After the annual meeting of a private business corporation with more than one member.",
     "Private business corporations and partnerships", "PBC", ["268(1)"], None, "The annual meeting is due not later than six months after the end of the financial year (s268(1)).", ["meetings"]),
    ("CR30", "Amendment of incorporation statement",
     "Changing a private business corporation's incorporation statement.",
     "Private business corporations and partnerships", "PBC", ["249"], None, "See s249.", ["constitutional"]),
    ("CR31", "Application for conversion of a private business corporation to a private company",
     "A PBC converting to a company.",
     "Private business corporations and partnerships", "PBC", ["250"], None, "Application signed by all members, with the documents for forming a company (s250(1)).", ["constitutional"]),
    ("CR32", "Voluntary registration of partnerships and agreements",
     "A partnership, syndicate or joint venture registering voluntarily.",
     "Private business corporations and partnerships", "Partnership", ["278", "5(1)"], None, "See s278.", ["incorporation"]),
]

CR_FORMS = [
    {"code": r[0], "title": r[1], "used_when": r[2], "group": r[3], "entity": r[4], "refs": r[5],
     "rule": r[6], "rule_text": r[7], "activities": r[8]}
    for r in _ROWS
]
CR_FORM_BY_CODE = {f["code"]: f for f in CR_FORMS}


def sort_key(code):
    m = code.upper()
    if m.startswith("CR") and m[2:].isdigit():
        return (1, int(m[2:]))
    return (0, 0 if m == "AR" else 1)


def forms_for_activities(activities):
    """The forms normally used with the given Secretarial activity keys (every form if none were chosen)."""
    acts = set(activities or [])
    out = [f for f in CR_FORMS if not acts or acts & set(f["activities"])]
    return sorted(out, key=lambda f: sort_key(f["code"]))


def due_date_for(code, event_date):
    """The last day for lodging, from the date of the event that triggers it, where the Act sets a limit. None otherwise."""
    f = CR_FORM_BY_CODE.get(code)
    if not f or not f["rule"] or not event_date:
        return None
    kind, n = f["rule"]
    if kind == "months":
        return add_months(event_date, n)
    return event_date + timedelta(days=n)


def is_overdue(status, due, today=None):
    today = today or date.today()
    return bool(due) and status in FORM_OPEN_STATUSES and due < today
