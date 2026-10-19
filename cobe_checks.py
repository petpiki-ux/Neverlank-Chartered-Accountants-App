"""The Companies and Other Business Entities Act [Chapter 24:31] (COBE Act) set against the Secretarial module.

Three things live here:

  calendar_suggestions(...) - suggested dates for the Secretarial Engagement Plan's statutory calendar, worked out from
                              the Act (annual return = 21 days after the anniversary of incorporation / registration /
                              re-registration, section 165(2); AGM once in every twelve months, section 167(2); notice
                              periods, sections 169 and 175). Hints only: the preparer always types the real date.
  DEADLINES                 - the Act's time limits in one table, each with its section, shown beside the plan.
  SECTION_CORRECTIONS       - section numbers the module used to quote that point at a different topic in the Act.
  FINDINGS                  - the side-by-side of what the Act says and what the module did / does, shown on the
                              COBE Act page. Nothing in it is marked verified; the Partner does that.

Pure Python. The Act's own words are not stored here: the pages fetch them from the filed Act (act_import), so they can
never drift from it. Section numbers below were checked against the Veritas consolidation (Acts 4/2019 and 8/2020).
"""
import calendar
from datetime import date, timedelta


def add_months(d, months):
    """The same day `months` later, or the month's last day if it has none (31 Aug + 6 months = 28/29 Feb)."""
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def anniversary_on_or_after(base, on):
    """The first anniversary of `base` (a date of incorporation / registration / re-registration) falling on or after `on`."""
    try:
        cand = date(on.year, base.month, base.day)
    except ValueError:                       # 29 February in a non-leap year
        cand = date(on.year, base.month, 28)
    if cand < on:
        try:
            cand = date(on.year + 1, base.month, base.day)
        except ValueError:
            cand = date(on.year + 1, base.month, 28)
    return cand


def annual_return_due(registration_date, on):
    """Section 165(2): the annual return is due within twenty-one days of the anniversary of the company's incorporation,
    registration or re-registration. Returns (anniversary, last_day) for the first anniversary on or after `on`."""
    ann = anniversary_on_or_after(registration_date, on)
    return ann, ann + timedelta(days=21)


def calendar_suggestions(period_end, registration_date=None, last_agm_date=None, today=None):
    """Suggested dates for the Annual Statutory Compliance Calendar. Returns None if there is no year-end to work from.

    Keys: draft_financials_target, agm_notice_target, agm_target, annual_return_target (the last is None until a
    registration date is entered), plus `basis` (one line per key saying where the date comes from) and `warnings`.
    The AGM is suggested six months after the year-end - a firm target, because the Act fixes no month after the year-end;
    it only requires an AGM once in every twelve months (section 167(2)) - and is pulled back to twelve months after the
    last AGM if that is earlier."""
    if not period_end:
        return None
    today = today or date.today()
    warnings = []
    basis = {}

    agm = add_months(period_end, 6)
    basis["agm_target"] = "Six months after the year-end is the firm's target. The Act requires an AGM once in every twelve months (s167(2))."
    if last_agm_date:
        latest = add_months(last_agm_date, 12)
        basis["agm_latest"] = "Twelve months after the last AGM (%s) is %s (s167(2))." % (last_agm_date.strftime("%d %b %Y"), latest.strftime("%d %b %Y"))
        if agm > latest:
            warnings.append("Six months after the year-end (%s) is later than twelve months after the last AGM (%s), so the AGM date suggested is the earlier one (s167(2))."
                            % (agm.strftime("%d %b %Y"), latest.strftime("%d %b %Y")))
            agm = latest
        if latest < today:
            warnings.append("More than twelve months have passed since the last AGM recorded (%s): check whether an AGM has been missed (s167(2))." % last_agm_date.strftime("%d %b %Y"))
    else:
        basis["agm_latest"] = "Enter the date of the last AGM to see the latest date the next one may be held."

    notice = agm - timedelta(days=21)
    basis["agm_notice_target"] = "Twenty-one days' written notice of an annual general meeting (s169(1)); the same 21 days apply to a meeting called to pass a special resolution (s175(1))."
    draft = add_months(period_end, 4)
    basis["draft_financials_target"] = ("A firm target. The financial statements must be laid before the AGM (s183(1)); where a copy must be sent to members it goes "
                                        "at least 14 days before the meeting (s190(1)).")
    if draft > agm - timedelta(days=14):
        draft = agm - timedelta(days=14)

    annual = None
    if registration_date:
        anchor = period_end if period_end >= registration_date else registration_date
        ann, annual = annual_return_due(registration_date, anchor)
        basis["annual_return_target"] = ("Twenty-one days after the anniversary of incorporation, registration or re-registration (%s) - the anniversary is "
                                         "%s (s165(2)). It does not depend on the AGM or the year-end." % (registration_date.strftime("%d %b %Y"), ann.strftime("%d %b %Y")))
    else:
        basis["annual_return_target"] = ("Enter the date of incorporation, registration or re-registration above: the annual return is due within 21 days of "
                                         "its anniversary, not a set time after the AGM (s165(2)).")
    return {
        "draft_financials_target": draft,
        "agm_notice_target": notice,
        "agm_target": agm,
        "annual_return_target": annual,
        "basis": basis,
        "warnings": warnings,
    }


# What the Act sets as a time limit. (topic, [section references], what the Act says, penalty note or "")
DEADLINES = [
    ("Annual return", ["165(2)", "165(7)"], "Within 21 days of the anniversary of incorporation, registration or re-registration.", "Category 3 civil penalty for default."),
    ("Annual general meeting", ["167(2)"], "Once in every period of twelve months.", ""),
    ("Notice of an AGM", ["169(1)"], "21 days' written notice. Shorter notice is valid only if every member entitled to attend and vote agrees (s169(2)(a)).", ""),
    ("Notice of other general meetings", ["169(1)", "169(2)(b)"], "14 days' written notice, or 7 days for a private company. Shorter notice is valid if a majority of members holding at least 95% of the shares with votes agree.", ""),
    ("Special resolution", ["175(1)"], "21 days' notice naming the resolution as special; at least 75% of the members present and voting; at least 25% of all votes present.", ""),
    ("Filing a special resolution", ["178(1)", "178(5)"], "A copy to the Registrar within one month of its being passed. It has no force until registered, and then runs from the date it was passed.", "Category 4 civil penalty for default."),
    ("Written resolution of a private company", ["176(1)", "176(2)"], "Signed by all the members entitled to vote. It cannot be used to remove an auditor or a director.", ""),
    ("Financial statements to members", ["190(1)", "183(1)"], "A copy at least 14 days before the AGM. A private company need not send it unless a member is a public company or a subsidiary of one.", "Category 1 civil penalty for default."),
    ("Minutes of members' meetings", ["180(1)", "180(4)"], "Prepared not later than 20 days after the meeting, signed by the chairperson and the secretary of the meeting.", "Category 2 civil penalty."),
    ("Minutes of board and committee meetings", ["205(1)", "205(3)"], "Prepared promptly and submitted to the next meeting; deemed approved once signed by the chairperson.", "Category 2 civil penalty."),
    ("Directors and secretary: return and changes", ["217(4)", "217(5)"], "To the Registrar within one month of incorporation, or of the change being notified to the company.", ""),
    ("Return of allotments", ["121(2)"], "Lodged with the Registrar within one month of the allotment (with the contract, for shares not paid in cash).", "Category 3 civil penalty."),
    ("Increase of share capital", ["141(1)"], "Notice to the Registrar within one month of the special resolution authorising the increase.", "Category 3 civil penalty."),
    ("Consolidation, sub-division, conversion into stock, redemption, cancellation of shares", ["140(1)"], "Notice to the Registrar within one month of doing it.", "Category 3 civil penalty."),
    ("Beneficial ownership", ["72(2)", "2"], "File accurate information in the prescribed form and file updates within seven days of a material change. A beneficial owner holds MORE than 20% of the shares or voting rights, or controls the board, or otherwise has significant control.", ""),
    ("Nominee shareholders", ["73(2)(a)"], "Shares may be held in a nominee's name only where the beneficial owner (with associates) holds less than 20%.", ""),
    ("Register of mortgages and debentures", ["155(1)(a)"], "Each mortgage, registered security interest or debenture entered within 14 days of the hypothecation.", "Category 4 civil penalty."),
    ("Register of members", ["159(1)", "159(3)", "159(4)"], "Kept at the registered office; notice to the Registrar within one month if kept elsewhere; an index within 14 days of any alteration if there are more than fifty members.", ""),
    ("Refusing a share transfer", ["151(3)"], "Notice of refusal to transferor and transferee within two months of the transfer being lodged.", "Category 3 civil penalty."),
    ("Distributions", ["138(1)", "138(3)"], "Board resolution authorising it and acknowledging the solvency and liquidity test (s102); if not completed within 120 business days the test must be re-applied.", ""),
    ("Buying its own shares", ["128(1)", "129(1)"], "Only if the articles allow it and a general meeting has authorised it in advance, stating price, maximum number and an expiry date.", ""),
    ("Directors", ["195(1)", "195(2)", "206(2)"], "Private company with more than one and fewer than ten shareholders: two or more directors; ten or more shareholders: at least three; public company: seven to fifteen. At least one director ordinarily resident in Zimbabwe. A public company needs at least three non-executive or independent directors.", ""),
    ("Company secretary", ["198(1)", "198(2)", "198(4)"], "At least one secretary ordinarily resident in Zimbabwe. A public company's secretary must be qualified (e.g. chartered accountant, chartered secretary, legal practitioner, public accountant or auditor) and may not hold another office.", ""),
    ("Registered office and postal address", ["31(1)", "31(2)", "31(5)"], "A registered office at a physical address in Zimbabwe and a postal address, recorded in the constitutive documents and amended when they change. The Act sets no number of days.", "Category 3 civil penalty; the cumulative part is suspended if the default is put right within seven days of service."),
    ("Striking off", ["52(1)", "52(3)", "52(5)"], "The Registrar may write to an entity that appears not to be operating: 14 days to show cause, then a Gazette notice, then striking off one month later.", ""),
    ("Re-registration of companies from the repealed Act", ["303(9)", "303(12)", "303(11)"], "Within twelve months from the effective date, on the Tenth Schedule form; a company that does not is struck off from the end of that period. Re-registration cannot be used to change name, address, directors or share structure.", ""),
    ("First auditor", ["191(1)"], "Appointed by the directors within one month of the certificate of incorporation (a private company that is not required to appoint an auditor is covered by s166(7)).", ""),
]

# Late-filing fees: the Ninth Schedule (headed "Section 18(4) and (5)" as printed) multiplies the prescribed fee by the time the
# document is late. The sections supplied do not say which filings it applies to, so the module shows the table and does not calculate.
LATE_FILING_TABLE = [
    ("Within three months", "Twice the prescribed fee"),
    ("Within six months", "Three times the prescribed fee"),
    ("Within twelve months", "Four times the prescribed fee"),
    ("More than twelve months", "Five times the prescribed fee"),
]

# Civil penalty categories (s294). The levels are fixed by the Standard Scale in force, which is not in the library.
CIVIL_PENALTY_CATEGORIES = [
    ("Category 1", "A fixed penalty (level ten) plus a daily penalty (level three) for up to 90 days: a completed default that cannot be put right.", "e.g. s190(3) financial statements not sent to members"),
    ("Category 2", "A daily penalty (level three), suspended if the Registrar's remedial action is taken in time: a completed default that can be put right.", "e.g. s180(4) and s205(5) minutes not kept; s188(2) unsigned or incomplete statements"),
    ("Category 3", "A fixed penalty (level five) plus daily penalties, partly suspended on remedial action: a default that can be partly put right.", "e.g. s165(7) annual return; s121(4) allotments; s141(2) capital; s217(6) a director or secretary not supplying particulars; s31(5) registered office"),
    ("Category 4", "A daily penalty (level one) for up to 90 days, suspended if the default stops at once: a continuing default.", "e.g. s155(4) registers of mortgages; s178(5) special resolutions"),
    ("Category 5", "A fixed penalty (level ten) plus a daily penalty (level three), suspended on remedial action: a continuing default where time is of the essence.", ""),
]

# Section numbers the module used to quote that point at a different topic in the Act. (what was quoted, what it was used for,
# what that section actually is, where the topic is)
SECTION_CORRECTIONS = [
    ("Sec 196", "borrowing limits in the Articles", "Directors acting other than in person (written consent, electronic meetings)", "No borrowing-limit section as such: read the Articles. (s158 restricts a public company's borrowing before it may commence business.)"),
    ("Sec 170", "notice of an AGM; minute books", "General provisions as to meetings and votes (quorum)", "s169(1) notice; s175 special resolutions; s180 minutes of members' meetings"),
    ("Sec 176", "minutes", "Written resolutions of a private company", "s180 (members), s205 (board and committees)"),
    ("Sec 215", "register of directors and secretaries; Form CR6 in 14 days", "Particulars in accounts of directors' salaries and pensions", "s217 - one month, not 14 days"),
    ("Sec 241", "director and secretary changes", "Requirements as to foreign companies", "s217"),
    ("Sec 211", "register of directors' interests and conflicts", "Duty of a director to disclose payments for loss of office", "s213 (register of directors' share holdings, companies other than private companies); conflicts are dealt with in ss56-58"),
    ("Sec 216", "approval of loans to directors", "Particulars in accounts of loans to officers", "s208 prohibits loans to directors, with exceptions; s216 is the disclosure in the accounts"),
    ("Sec 160", "change of registered office, 14 days", "Inspection of the register of members", "s31 - the Act sets no number of days"),
    ("Sec 240", "registered office changes", "Definitions for the foreign-company Chapter", "s31"),
    ("Sec 157", "register of members", "Power to re-issue redeemed debentures", "s159"),
    ("Sec 149", "share transfers", "Penalty for concealing the name of a creditor", "s151 (transfer of title to shares)"),
    ("Sec 93 and 95", "return of allotments", "Members' shares in a co-operative company: charge and set-off; legal nature of shares", "s121 (register and return as to allotments); s96 (authorisation for shares)"),
    ("Sec 104", "share buy-backs", "Matters to be stated in a prospectus", "ss128-134 (purchase of own shares); s138 (distributions)"),
    ("Sec 162", "register of mortgages and charges", "Power of the court to rectify the register of members", "s155"),
    ("Sec 163 and 164", "charge registration within 30 days", "Trusts in respect of shares; branch registers", "s154 (how security is registered: Movable Property Security Interest Act or the Deeds Registry) and s155 (the company's register, 14 days)"),
    ("Sec 16 and 25", "constitutional amendments and name changes", "Replacement of lost documents; undesirable names", "s79 (memorandum), s81 (articles), s26 (change of name), then ss175 and 178"),
    ("Sec 143 or 228", "minority appraisal remedies", "Variation of share rights; procedure for a merger", "s233 (dissenting shareholders' appraisal rights - it applies to ss143 and 228)"),
    ("Sec 310", "striking off / deregistration", "The Act ends at s304", "s52"),
    ("Sec 165/166", "annual return and declaration of AGM", "s166 is the statutory meeting (not for private companies)", "s165 (annual return); s167 (AGM)"),
    ("Sec 195/206", "quorum", "Number of directors; independent directors for public companies", "s170 (quorum of members), s204 (quorum of the board)"),
    ("Sec 72", "nominee holdings capped at 20%", "Beneficial ownership register", "s73(2)(a) - a nominee is allowed only for a beneficial owner holding less than 20%"),
]

RESULT_LABELS = {
    "agrees": "Agrees with the Act",
    "updated": "Brought into line in this update",
    "differs": "Differs from the Act",
    "missing": "Not in the app",
}

FINDINGS = [
    {
        "topic": "Annual return - when it is due", "refs": ["165(2)", "165(7)"], "result": "updated",
        "app": "The plan suggested the annual return 42 days after the AGM (and called it Form CR11 / CR23); the Execution Plan, the programme and the risk questions repeated '42 days of the AGM'.",
        "note": "The Act fixes it by the company's registration date, not the AGM or the year-end: within 21 days of the anniversary of incorporation, registration or re-registration. The plan now has a 'registration date' box and suggests the date from it. A company that has not entered its date gets no suggestion rather than a wrong one. Default is a category 3 civil penalty.",
    },
    {
        "topic": "Annual return - contents and certificates", "refs": ["165(1)", "165(3)", "165(4)", "85(1)"], "result": "updated",
        "app": "No task covered what goes with the return.",
        "note": "A private company must send a certificate signed by a director and the secretary that it has made no invitation to the public and stating its number of members (and, over fifty, that the excess are employees or ex-employees); every return is certified by a director and the secretary and a signed duplicate kept at the registered office. Added to the Execution Plan.",
    },
    {
        "topic": "Annual general meeting", "refs": ["167(2)", "167(5)", "183(1)"], "result": "updated",
        "app": "AGM suggested six months after the year-end; the analytical review point called that 'the statutory requirement (within 6 months of financial year-end)'.",
        "note": "The sections supplied do not set a month after the year-end. They require an AGM 'once in every period of twelve months', with the financial statements laid before it. Six months stays as the firm's target; the plan now takes the date of the last AGM and holds the suggestion to twelve months after it. Wording that called six months a statutory requirement was corrected.",
    },
    {
        "topic": "Notice of meetings", "refs": ["169(1)", "169(2)", "175(1)", "170(1)"], "result": "updated",
        "app": "The Execution Plan, programme and risk questions cited s170 for the 21-day AGM notice.",
        "note": "Notice is s169(1): 21 days for an AGM; 14 days for other meetings (7 days for a private company); a special resolution needs 21 days and 75%. s170 is the quorum and voting section (a majority of votes entitled to vote, not less than one-third if the memorandum lowers it). All references corrected.",
    },
    {
        "topic": "Special and written resolutions", "refs": ["175(1)", "176(1)", "178(1)", "178(5)"], "result": "updated",
        "app": "The finalisation checklist cited 'Sec 176 & 178' for special resolutions and the corporate-actions task cited 'Secs 16 and 25'.",
        "note": "A special resolution is s175; written resolutions of a private company are s176 (unanimous; not for removing a director or auditor); a copy goes to the Registrar within one month and has no force until registered (s178). Corrected. Amending the memorandum is s79 (five-per-cent holders may apply to court within one month), the articles s81 and a change of name s26 (the Registrar's written approval first).",
    },
    {
        "topic": "Minutes", "refs": ["180(1)", "205(1)", "180(4)", "205(5)"], "result": "updated",
        "app": "Drafts within 5 working days (Sec 176).",
        "note": "Five working days stays as the firm's practice. The Act allows up to 20 days for minutes of members' meetings (s180) and says 'promptly' for the board (s205). Failing to keep minutes is a category 2 civil penalty. Section reference corrected.",
    },
    {
        "topic": "Register of directors and secretaries", "refs": ["217(1)", "217(4)", "217(5)", "217(6)"], "result": "updated",
        "app": "'File Form CR6 ... within 14 days (Sec 215)' and 'Sec 241'.",
        "note": "The register is s217 and the period is one month after incorporation or after the company is told of the change, not 14 days. A director or secretary who does not give the company the particulars within seven days of demand is in default (category 3). A resignation does not release the person unless the Registrar and the company were told and the person had reason to believe the company would file (s217(7)). The form number (CR6) comes from the firm's own list and is not in the Act, which says 'prescribed form'.",
    },
    {
        "topic": "Registered office and postal address", "refs": ["31(1)", "31(2)", "31(5)"], "result": "updated",
        "app": "'Lodge Form CR5 ... within 14 days (Sec 160)' and 'Sec 240'.",
        "note": "The rule is s31: a registered office (physical address in Zimbabwe) and a postal address, recorded in the constitutive documents and amended when they change. No number of days is stated in the section; the Registrar may serve a category 3 order, suspended if the default is cured within seven days. The 14 days was removed.",
    },
    {
        "topic": "Beneficial ownership", "refs": ["2", "72(1)", "72(2)", "72(4)", "73(2)"], "result": "updated",
        "app": "Owners with '20% or more'; updates within 14 days; nominees capped at 20% (cited to s72); client acceptance asked for owners 'more than 20-25%'.",
        "note": "A beneficial owner holds MORE than 20% of the shares or voting rights (or controls the board or otherwise has significant control) - 20% exactly is not enough. Updates are due within SEVEN days of a material change. The company must appoint a custodian resident in Zimbabwe, named in the filed form (s72(4)-(5)), and keep the register where the register of members is kept. The nominee rule is s73(2)(a): a nominee is allowed only for a beneficial owner who, with associates, holds less than 20%. All corrected. For the Partner: the printed s72(1)(b) speaks of recording the details 'with respect to each director' - an oddity of the consolidation worth confirming against the Gazette.",
    },
    {
        "topic": "Register of members and transfers", "refs": ["159(1)", "159(3)", "159(4)", "151(1)", "151(3)"], "result": "updated",
        "app": "Register of Members (Sec 157); transfers (Sec 149).",
        "note": "The register is s159 (kept at the registered office; notice to the Registrar within one month if kept elsewhere; an index within 14 days of any change if there are over fifty members). Transfers are s151: a proper instrument of transfer, and notice of refusal to both parties within two months. Corrected.",
    },
    {
        "topic": "Mortgages, charges and debentures", "refs": ["154(2)", "154(3)", "155(1)", "155(4)"], "result": "updated",
        "app": "'File mortgage/debenture registration forms with the Registrar within 30 days of creation (Sec 163)'; register of charges 'Sec 162'.",
        "note": "Nothing in the Act supplied requires charge forms to be lodged with the Registrar within 30 days. The company keeps a register of mortgages, registered security interests and debentures and enters each within 14 days of the hypothecation (s155); security is registered under the Movable Property Security Interest Act or by mortgage bond at the Deeds Registry (s154). Those two Acts are not in the library, so any time limit in them must be confirmed separately. Wording and risk questions corrected.",
    },
    {
        "topic": "Allotments and capital changes", "refs": ["121(2)", "141(1)", "140(1)", "96", "99", "139"], "result": "updated",
        "app": "'Returns of allotment ... (Secs 93 and 95)'; capital forms CR9 / CR10 without time limits.",
        "note": "A return of allotments goes to the Registrar within one month of the allotment (s121, with the contract if shares are paid otherwise than in cash); notice of an increase within one month of the special resolution (s141); notice of consolidation, sub-division, redemption or cancellation within one month (s140). Pre-emption rights are ss99 and 139. All three are category 3 matters.",
    },
    {
        "topic": "Distributions and buy-backs", "refs": ["102", "138(1)", "128(1)", "129(1)"], "result": "updated",
        "app": "'Solvency and Liquidity Test (Secs 102 and 104)'.",
        "note": "s104 is about prospectuses. A distribution needs a board resolution authorising it and a second board resolution acknowledging that the solvency and liquidity test was applied (s138), re-applied if not completed in 120 business days. A buy-back also needs the articles to allow it and advance authority from a general meeting stating price, maximum shares and expiry (ss128-129). Corrected.",
    },
    {
        "topic": "Directors, conflicts and loans", "refs": ["195(1)", "195(2)", "206(2)", "208(1)", "213", "56", "57"], "result": "updated",
        "app": "Minimum directors by 'members' (under or over 10); a public company 'at least 3 independent non-executive directors'; loans 'approved under Section 216'; a 'Register of Directors' Interests and Conflicts (Sec 211)'.",
        "note": "s195: more than one and fewer than ten shareholders - two or more directors; ten or more - at least three; public company seven to fifteen; and at least one director ordinarily resident in Zimbabwe (not previously asked). s206: at least three non-executive OR independent directors. Loans and guarantees to directors are prohibited by s208 with listed exceptions (including a private company with the consent of holders of nine-tenths of the shares); s216 is disclosure in the accounts. A register of directors' share holdings is required of companies other than private companies (s213); conflicts are ss56-58. The Act supplied has no register called 'directors' interests and conflicts'. Checklist wording and references corrected; the residency question was added.",
    },
    {
        "topic": "Written consent and proxies at board level", "refs": ["196(1)", "196(2)", "204"], "result": "updated",
        "app": "'Section 196, which prohibits directors from acting or voting by proxy at board meetings.'",
        "note": "s196 as supplied does not mention proxies. It allows a board decision by written consent signed by ALL directors entitled to vote, and meetings by electronic or conference means unless the memorandum or articles prohibit it. The board quorum is a majority of the number of directors fixed in the articles (s204). The question was reworded; whether the articles bar director proxies is for the articles.",
    },
    {
        "topic": "Company secretary", "refs": ["198(1)", "198(2)", "198(3)", "198(4)"], "result": "updated",
        "app": "'Confirm the company secretary is appointed and the appointment is lodged with the Registrar.'",
        "note": "At least one secretary must be ordinarily resident in Zimbabwe. A public company's secretary must be qualified as the section lists and may not hold another office. The secretary certifies in the annual financial statements whether the returns and notices required by the Act, including the annual return, have been filed (s198(3)(f)). Residency and the certification were added.",
    },
    {
        "topic": "Financial statements and members' copies", "refs": ["182", "183(1)", "188(3)", "190(1)", "190(3)"], "result": "agrees",
        "app": "Notice of AGM 'with proxy forms and the financial statements'.",
        "note": "Consistent with s190(1) for public companies; a private company is required to send the statements only if one of its members is a public company or a subsidiary of one. Signed by two directors (s188(3)). No change.",
    },
    {
        "topic": "Striking off", "refs": ["52(1)", "52(3)", "52(5)", "298"], "result": "updated",
        "app": "Risk questions and impact scales cited 'Sec 310'.",
        "note": "The Act ends at section 304. Striking off of defunct entities is s52: a written notice giving 14 days to show cause, then a Gazette notice, then striking off one month after it. References corrected.",
    },
    {
        "topic": "Re-registration", "refs": ["303(8)", "303(9)", "303(11)", "303(12)", "303(13)"], "result": "updated",
        "app": "Re-registration tasks and questions, noting the Registrar has extended the deadline before.",
        "note": "The Act itself gave twelve months from the effective date (13 February 2020), on the Tenth Schedule form, with striking off from the end of that period; re-registration keeps the same name and number and does not create a new entity. It may not be used to change name, address, directors or share structure - those changes follow the ordinary formalities 'before, after or together with' it. The current deadline is outside the Act (the Registrar extended it), so the tasks still say to confirm it. Section references added.",
    },
    {
        "topic": "'Company Summary' return", "refs": ["165(1)"], "result": "differs",
        "app": "A task and a finalisation line require a separate 'Company Summary return introduced under the new COBE Act'.",
        "note": "Neither 'Company Summary' nor any such return appears in the Act as supplied, and no form of that name is on the Registrar's CR1 to CR35 list. What the Act calls the annual return is itself 'a summary' in the Fourth Schedule form (s165(1)). The wording now says so and still tells staff to confirm with the Registrar whether the Company Summary is the same document or a separate one. Nothing is marked as confirmed.",
    },
    {
        "topic": "Form numbers (CR1 to CR35)", "refs": ["10", "165(1)"], "result": "differs",
        "app": "Forms were named throughout the checklists without a list to check them against. CR2 was called the registry search, CR12 the satisfaction of mortgages, CR23 the annual return, and CR11 the annual return in one place and the return of allotments in another.",
        "note": "The Act refers only to 'the prescribed form' (the annual return follows the Fourth Schedule and re-registration the Tenth). The Registrar's list of forms CR1 to CR35 has now been supplied and is on the Registry Forms tab of every Secretarial engagement, tied to the section that calls for each form. Against that list: CR2 is the application for search as to availability of a name (the registry search is CR35, application to inspect entity documents and registers); CR12 is the notice of the place where the register of mortgages and debentures is kept (s155(3)), not a satisfaction form; CR23 is the statutory report (s166), not the annual return; CR17 is the declaration that the AGM was held; CR11 is the return of allotments; CR10 registers an increase in shares and CR9 is the notice of conversion or buy-back of shares. The annual return is not on the list. The checklist wording is corrected. The list and the section links are for a Partner to confirm against the Registrar's current forms; nothing is marked verified.",
    },
    {
        "topic": "Penalties for late filing", "refs": ["294", "Sch9"], "result": "missing",
        "app": "The risk questions talk about 'fines' and 'Registrar default notices'; no penalty is worked out anywhere.",
        "note": "The Registrar serves civil penalty orders in five categories (s294), each tied to the section breached - the table below the findings lists the sections used in the module. The levels are set by the Standard Scale in force, which is not in the library. The Ninth Schedule multiplies the prescribed fee by two to five times for documents lodged three to over twelve months late; the sections supplied do not say which filings it covers, so the module shows the table and does not calculate a penalty.",
    },
    {
        "topic": "Statutory meeting and first auditor", "refs": ["166(1)", "166(5)", "191(1)"], "result": "missing",
        "app": "Not covered.",
        "note": "A company other than a private company holds a statutory meeting one to three months after it may commence business and files the statutory report within a month (s166). The first auditor is appointed within a month of the certificate of incorporation (s191(1)); a private company may be excused an auditor under s166(7). Relevant to incorporation work; not yet in the checklists.",
    },
    {
        "topic": "Foreign companies, private business corporations, electronic registry", "refs": ["241", "247", "280"], "result": "missing",
        "app": "Secretarial activities cover companies only; the entity question lists the types but nothing follows from them.",
        "note": "The Act also covers foreign companies (ss241-246), private business corporations (ss247-277: accounting officers, members' interests, by-laws) and the electronic registry and digital signatures (ss280-290). None has its own checklist yet. They can be added once the firm confirms it wants them.",
    },
]
