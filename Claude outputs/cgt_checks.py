"""The Capital Gains Tax Act [Chapter 23:01] set beside what the app does for CGT.

The app has no CGT register of its own. What it holds for CGT is: the "Tax / CGT Advisory" engagement checklist (seed.TAX_CGT_PROGRAM),
the TX-03 CGT Computation Workpaper and TX-02 information request (Document Templates), the CGT tax head in the Tax module (registrations,
calendar, return records, penalty and interest rates, disputes) and the CGT section of the Tax Health Check. This module holds:

  DEADLINES, EXEMPTIONS_NOTED  - the Act's time limits and the exemptions, each with its section, for the comparison page
  FINDINGS                     - the Act against the app, line by line. Nothing in it is marked verified; the Partner does that.
  PROGRAM_PAIRS, NEW_PROGRAM_ITEMS - the wording of the "Tax / CGT Advisory" checklist brought into line with the Act, and the lines added
  payment_due(), withholding_due(), reinvestment_deadline(), objection_due() - the dates the Act sets, worked out from an event date

The Act's own words are never copied here: the pages fetch them from the filed Act (act_import), so they cannot drift from it.
The Act sets no rate of tax (section 7 sends the rate to the Finance Act [Chapter 23:04], which was not supplied), so no rate is stated.
Pure Python.
"""
from datetime import date, timedelta

RESULT_LABELS = {
    "agrees": "Agrees with the Act",
    "updated": "Brought into line in this update",
    "differs": "Differs from the Act",
    "missing": "Not in the app",
}


# ------------------------------------------------------------------------------------------------------ dates the Act sets

def payment_due(title_transfer_date):
    """Section 26(1)(b): tax is due no later than 30 days from the date title to the specified asset is formally transferred to the taxpayer.
    (For an instalment or suspensive sale, section 26(1)(a): 30 days from the date the agreement was entered into, section 18(1) and 19(1).)"""
    return title_transfer_date + timedelta(days=30) if title_transfer_date else None


def withholding_due(payment_date, working_days=3):
    """Sections 22C(1), 22D(1), 22E(1): capital gains withholding tax is paid to the Commissioner no later than the 3rd working day from the date
    the amount was paid or received. Saturdays and Sundays are skipped; public holidays are NOT (no list of them is held), so the real date may be
    later. The Commissioner may allow more time for good cause."""
    if not payment_date:
        return None
    d, n = payment_date, 0
    while n < working_days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return d


def reinvestment_deadline(sale_date):
    """Sections 21(2) and 22(1): the money must be spent on the new residence, stand or business property before the end of the year of assessment
    next following the sale. A year of assessment is the twelve months from 1 January (Income Tax Act section 2(1), applied by CGT Act section 2(2))."""
    return date(sale_date.year + 1, 12, 31) if sale_date else None


def damage_replacement_deadline(damage_date):
    """Section 13(3): the receipt must be spent on a replacement or repair within 2 years of the damage or destruction."""
    if not damage_date:
        return None
    try:
        return damage_date.replace(year=damage_date.year + 2)
    except ValueError:  # 29 February
        return damage_date.replace(year=damage_date.year + 2, day=28)


def objection_due(notice_date):
    """Section 25(1): an objection to an assessment, or to the decisions listed there, is made within 30 days after the date of the notice."""
    return notice_date + timedelta(days=30) if notice_date else None


# ------------------------------------------------------------------------------------------------------ the Act's time limits

# (matter, sections, what the Act says)
DEADLINES = [
    ("Payment of the tax", ["26(1)(b)"], "No later than 30 days from the date title to the specified asset is formally transferred to the taxpayer. Interest runs on tax not paid by then, at a rate fixed by the Minister by statutory instrument (s26(3))."),
    ("Payment on an instalment or suspensive sale", ["18(1)", "19(1)", "26(1)(a)"], "The whole price is treated as accruing on the date the agreement was entered into; the tax is due within 30 days of that date. An allowance is made for the part not yet receivable at the year end and comes back into the next year's capital amount."),
    ("Withholding by the depositary", ["22C(1)"], "No later than the 3rd working day from the date the depositary pays the seller (or later if the Commissioner allows for good cause). A certificate goes to the payee (s22C(3))."),
    ("Withholding by the agent, or payment by the payee", ["22D(1)", "22E(1)"], "The same 3rd working day, from the date the agent or the payee received the money, where nothing was withheld and no clearance certificate was issued."),
    ("Clearance certificate", ["22C(5)", "22D(7)", "22E(2)"], "Asked for before the seller is paid (or, for a payee, before the 3rd working day ends). The Commissioner must be satisfied that no tax, or less than the withholding tax, is likely to be payable and that payment is arranged."),
    ("Registration of a depositary", ["22FA(1)"], "Within 30 days after starting business as a depositary. Failure is an offence (s22FA(4))."),
    ("Depositary's monthly return", ["22G(1)"], "On or before the last day of every month (or other intervals the Commissioner permits), with the tax withheld."),
    ("Penalty on a depositary or agent who does not withhold or pay", ["22H(1)", "22H(2)"], "Personally liable for the tax and a further 15% of it. The Commissioner may waive all or part of the 15% if there was no intent to evade."),
    ("Refund of tax overpaid", ["22I(1)", "22I(2)"], "A claim within 6 years of the date the tax was paid. Interest, at a rate fixed by statutory instrument, if not refunded within 60 days of the claim or of completing the assessment, whichever is later."),
    ("Elections (spouse, group, business property, residence)", ["15(3)", "16(3)", "17(2)", "21(2a)", "22(1a)"], "Each election is made no later than the date the person submits the return for assessment of the capital gain."),
    ("Reinvestment (residence, stand or business property)", ["21(2)", "22(1)"], "The money is spent on the new property before the end of the year of assessment next following the sale (a year of assessment runs from 1 January)."),
    ("Replacement of a damaged or destroyed asset", ["13(3)"], "Within 2 years from the date of the damage or destruction."),
    ("Objection to an assessment or listed decision", ["25(1)"], "Within 30 days after the date of the notice of assessment or of the written notice of the decision. No further objection to the amount of the previous year's assessed capital loss."),
    ("Special capital gains tax on a mining title", ["30B(5)(b)"], "For transfers before 1 January 2024 still held on that date, no later than 1 April 2024; for later transfers, no later than 30 days after the transaction. The Commissioner-General may extend for up to 6 months or agree instalments."),
    ("Registration of a transfer", ["30A(1)", "30A(2)", "32"], "No transfer of a specified asset on which CGT was not withheld is registered (deeds office or share registry) without ZIMRA's certificate that the tax has been paid. Stands ceded under a land development scheme and condominium interests have their own rule (s30A(2))."),
]

# (item, sections, what the Act says) - amounts are as printed in the consolidation, in zw$ and US$
EXEMPTIONS_NOTED = [
    ("Principal private residence sold by a person of 55 or over", ["10(l)", "21(1)"]),
    ("First amount of marketable securities sold in a year by a person of 55 or over (amount as printed in s10(m))", ["10(m)"]),
    ("Securities sold on the Victoria Falls Stock Exchange (from 1 August 2020)", ["10(r)"]),
    ("Marketable securities already taxed under section 39(c) of the Charging Act (not those in section 38(b)(iii))", ["10(n)"]),
    ("Securities sold to the Sovereign Wealth Fund", ["10(q)"]),
    ("Government, local authority and statutory corporation bonds and stock", ["10(c)"]),
    ("Disposals by an executor of a deceased estate", ["10(b)"]),
    ("A licensed investor selling part of the licensed investment; an industrial park developer", ["10(g)", "10(h)"]),
    ("Shares sold to an approved employee share ownership trust; donations of housing units to a local authority or trust", ["10(k)", "10(p)"]),
    ("Bodies in paragraphs 1, 2 and 3 of the Third Schedule to the Income Tax Act (other than 2(a), (c), (f))", ["10(a)"]),
]


# ------------------------------------------------------------------------------------------------------ the findings

FINDINGS = [
    {
        "topic": "Rate of tax", "refs": ["7"], "result": "agrees",
        "app": "The TX-03 workpaper has an empty 'Applicable CGT rate' cell marked as a key assumption to confirm; the checklist says to 'determine applicable CGT rate'. No rate is built in.",
        "note": "Section 7 says the tax is calculated in accordance with the Finance Act [Chapter 23:04] by reference to the capital gains of the year and the rate fixed in that Act. The CGT Act contains no rate and the Finance Act was not supplied, so the app stays right to leave the rate to be entered. The special tax on a mining title (s30B) is the one rate that is in this Act.",
    },
    {
        "topic": "What is taxed: a 'specified asset'", "refs": ["2", "6", "8(1)"], "result": "missing",
        "app": "The workpaper and the information request take 'asset / transaction description' as free text. Nothing asks whether the asset is one the Act taxes.",
        "note": "Only the sale of a 'specified asset' is taxed: immovable property, any marketable security (bonds, debentures, shares, stock, unit trust rights, shares including a member's interest in a private business corporation), and registered rights under the Mines and Minerals, Patents, Trade Marks, Industrial Designs, Copyright, Brands, Geographical Indications and Integrated Circuit Layout-Designs Acts (s2(1), amended by Finance Act 2 of 2017). Gains before 1 August 1981 are not charged (s6). Gross income under the Income Tax Act is left out of the gross capital amount (s8(1)(a)). The checklist now asks for this to be confirmed.",
    },
    {
        "topic": "Disposals that are not a sale (deemed sales)", "refs": ["8(2)"], "result": "missing",
        "app": "The workpaper has one 'gross disposal proceeds' line.",
        "note": "A disposal other than by sale is treated as a sale at the fair market price in the Commissioner's opinion (8(2)(b), with an exception for donations to an approved employee housing trust); expropriation is a sale at the compensation paid (c); a court sale at the price it fetched (d); maturity or redemption is a sale (e); transferring one's rights under a deed of sale, or in a residential, commercial or industrial stand, or a condominium membership interest, is a sale for the whole amount received (f), (g), (h). Currency variations are dealt with in 8(2)(a). The app does not work any of these out; the checklist now asks whether the disposal is a sale or a deemed sale.",
    },
    {
        "topic": "Cost of the asset", "refs": ["11(2)(a)", "11(2)(b)"], "result": "differs",
        "app": "Workpaper: 'original cost / base cost' as one figure plus 'improvements'. Checklist: proof of original cost and acquisition date.",
        "note": "The cost allowed is the expenditure on acquiring or building the asset and on additions, alterations or improvements, but not expenditure already deductible in working out the seller's taxable income (11(2)(a), (b)). An asset inherited is taken to cost its value in the deceased estate; one acquired other than by purchase or inheritance before 1 August 1981 costs its fair market value then; one acquired otherwise on or after that date costs what was included in the transferor's gross capital amount or gross income. For shares in a company that owns immovable property, the seller's expenditure on the property counts as additions to the shares. Lessors taxed on rent receipts get the matching cost (11(5)). The workpaper does not distinguish these; the checklist now names them.",
    },
    {
        "topic": "Inflation allowance", "refs": ["11(2)(c)"], "result": "differs",
        "app": "Workpaper: allowance = base cost x a rate per year x years held (straight line), with a note to confirm the method.",
        "note": "Section 11(2)(c), as substituted by Finance Act 7 of 2021 with effect from 31 December 2021, gives an allowance by a formula built on the All Items Consumer Price Index at the time of disposal (A), the index in the month of purchase or improvement (B) and the purchase price or revalued amount with improvements (C). Neither the straight-line rate-times-years method nor a single flat rate is in the Act. The formula is printed in the consolidation as '(A - B) / C x C', which reduces to A - B and cannot be what was meant (the usual form divides by the earlier index, B); the Gazette copy of Finance Act 7 of 2021 must be read before any computation is built on it. The workpaper has therefore NOT been changed. A Partner should confirm the formula and the index series to be used.",
    },
    {
        "topic": "Selling costs, bad debts and appeal costs", "refs": ["11(2)(d)", "11(2)(e)", "11(2)(f)", "11(2)(g)"], "result": "agrees",
        "app": "Workpaper: 'selling expenses (agent's commission, legal fees, etc.)' deducted from proceeds.",
        "note": "Expenditure directly incurred for or in connection with the sale is allowed (11(2)(d)). The Act also allows bad debts that were included in the capital amount (e) and taxed costs of a successful appeal to the High Court, Special Court or Supreme Court (f), (g); the workpaper has no lines for those, which will rarely matter.",
    },
    {
        "topic": "Small gains and the assessed capital loss", "refs": ["11(2)(h)", "11(3)", "2"], "result": "missing",
        "app": "No line in the workpaper for either.",
        "note": "A year's total capital gains at or below the amount printed in 11(2)(h) (as printed: zw$25 000 or US$50) are deducted in full, so no tax arises; the same does not apply where instalment or suspensive sales are in the year (18(3), 19(2)). An assessed capital loss (the excess of deductions over the capital amount, with a small-amount reduction in the definition in section 2(1)) is deducted only from the NEXT year's capital amount - 'determined in respect of the previous year of assessment' (11(3)) - unlike a loss carried forward for income tax. Provisos cut it off on a scheme to use the loss, insolvency or assignment; conversion between a company and a private business corporation keeps it. The amounts were redenominated several times (editor's notes in the Act) and must be confirmed before use.",
    },
    {
        "topic": "Exemptions", "refs": ["10", "12"], "result": "updated",
        "app": "Checklist: 'determine ... any exemptions/rollover relief'.",
        "note": "Section 10 lists the exemptions (see the list below). The checklist item now points to section 10 and to the rollover sections. No deduction is allowed for expenditure on an asset whose sale is exempt (s12).",
    },
    {
        "topic": "Reinvestment: residence, stand and business property", "refs": ["21(2)", "22(1)", "21(1)"], "result": "updated",
        "app": "Covered only by the words 'rollover relief' in one checklist line.",
        "note": "An individual selling a principal private residence or residential stand, or a taxpayer selling immovable business property, may elect that no tax is charged if the sale price is spent on a replacement before the end of the year of assessment next following the sale, or that tax is charged on the proportion A x C / B of the gain if only part is reinvested (A = the part not reinvested, B = total sale price, C = the gain). The election is made no later than the return date (21(2a), 22(1a)); the untaxed amount reduces the cost of the new property (21(3), 22(2)). A principal private residence is a dwelling that was the sole or main residence for its whole ownership, or for 4 years or more before the sale (or shorter if reasonable), with up to 2 hectares of adjoining land and garages. The checklist now names the sections and the election date.",
    },
    {
        "topic": "Transfers between spouses, group companies and to a controlled company", "refs": ["15", "16", "17"], "result": "updated",
        "app": "Not covered.",
        "note": "On a transfer between companies in a reconstruction, a merger or a conversion between a company and a private business corporation (s15), between spouses or from a person to a former spouse on divorce (s16), or of immovable business property from an individual to a company he controls (s17), the transferor and transferee may elect that the sale price is the transferor's total allowed costs, so no gain arises then; a later sale outside the group or family is taxed as if the first transferor had kept the asset. The election is made no later than the return date. The checklist now asks about elections.",
    },
    {
        "topic": "Instalment and suspensive sales", "refs": ["18", "19", "26(1)(a)"], "result": "missing",
        "app": "The workpaper has one disposal date and one proceeds figure.",
        "note": "Where ownership passes only after the price (or part) is paid, the whole price accrues on the date of the agreement, less an allowance for the part not yet receivable (formula A x (B - C) / D, s18(1)); where ownership passes on delivery and the price is paid by instalments, the whole price accrues on the agreement date with such further allowance as the Commissioner thinks reasonable (s19). A cancelled agreement is adjusted (18(2)). The tax is due within 30 days of the agreement date. The app does not compute this.",
    },
    {
        "topic": "Damaged or destroyed assets and recoveries of cost", "refs": ["13", "20"], "result": "missing",
        "app": "Not covered.",
        "note": "Insurance and similar receipts on damage or destruction are a deemed sale unless they are within the asset's cost, or spent within 2 years on replacement or repair (s13). A recovery or recoupment of the cost of an asset not yet sold either reduces the cost or, if it exceeds the cost, is a deemed sale (s20).",
    },
    {
        "topic": "Capital gains withholding tax", "refs": ["22A", "22B", "22C", "22D", "22E", "22F", "22J"], "result": "updated",
        "app": "Checklist: 'consider withholding tax on disposal, where applicable'. Workpaper: 'withholding tax already deducted at source' deducted from the tax.",
        "note": "Part IIIA makes the depositary (conveyancer, legal practitioner, estate agent, building society, Sheriff, Master, stockbroker or financial institution holding the price; in a stand cession or condominium interest, the cedent, local authority or developer who holds it) withhold the tax at the rate in the Finance Act and pay it by the 3rd working day. If the depositary does not, the agent and then the payee must. A clearance certificate lets the money go without withholding (22C(5)). Securities sold by a registered unit trust are not withheld on, but redemptions by investors are (22F(b)). Tax withheld is a credit against the CGT, with any excess refunded (22J). The checklist now gives the 3rd working day and the clearance certificate.",
    },
    {
        "topic": "When the tax is due, and interest", "refs": ["26(1)", "26(3)"], "result": "updated",
        "app": "Checklist: 'file the return within the statutory deadline' and 'confirm payment of tax due'. The Tax module's penalty and interest rates are entered by the firm per tax head.",
        "note": "The Act gives no return date of its own: returns and assessments follow the Income Tax Act sections it applies (s23; see section 37 of the Income Tax Act). Payment is due no later than 30 days from formal transfer of title, or earlier on the withholding dates. Interest is at a rate fixed by the Minister by statutory instrument (the editor's notes refer to SI 33/2009, SI 281/2019, SI 54/2021, and SI 211/2022 from 1 December 2022, including interest on tax in foreign currency). The app holds no CGT interest rate unless the firm enters one; the checklist wording now carries the 30 days.",
    },
    {
        "topic": "Transfer of the asset needs ZIMRA's certificate", "refs": ["30A(1)", "30A(2)", "32"], "result": "updated",
        "app": "Not covered.",
        "note": "The Registrar of Deeds, and the person registering a share transfer, may not register the acquisition of a specified asset on which tax was not withheld unless ZIMRA has certified that the tax has been paid (s30A(1)); stand cessions and condominium interests follow s30A(2). The consolidation prints the first rule twice, as section 30A(1) and as a section 32 at the end of the Act (the editor notes it is wrongly numbered); the app files both so that either number can be found. A new checklist line asks for the certificate.",
    },
    {
        "topic": "Special capital gains tax on a mining title", "refs": ["30B(3)", "30B(5)", "30B(6)"], "result": "updated",
        "app": "Not covered.",
        "note": "Inserted by Finance Act 13 of 2023 with effect from 1 January 2024: a tax on the value of any transaction transferring a mining title to an 'entity' as defined (foreign holders, their local subsidiaries, nominees; and individuals and bodies not incorporated under the Companies and Other Business Entities Act), at 20% of the value, or 5% where the transfer needed and received ministerial approval under the mining law. It reaches transfers in the 10 years before 1 January 2024 still held on that date, due by 1 April 2024. The editor notes that the Chamber of Mines was seeking to have it reversed. A mining title is not registered without ZIMRA's certificate (30B(6)). A checklist line now raises it for mining-title transactions; no calculation is built.",
    },
    {
        "topic": "Currency", "refs": ["8(2)(a)", "11(1)"], "result": "missing",
        "app": "The workpaper shows one '$' for every amount.",
        "note": "Where an exchange-rate change makes the amount received differ from the amount that accrued, the amount actually received in Zimbabwean currency is used, with the effect given in the year of accrual; the same for deductions (11(1), proviso). The thresholds in the Act appear in both zw$ and US$ (the zw$ figure is shown in square brackets in the consolidation) and have been changed by several Acts. The app does not convert or choose between them.",
    },
    {
        "topic": "Objection and appeal", "refs": ["25(1)"], "result": "updated",
        "app": "The CGT checklist and workpaper did not mention the objection period.",
        "note": "Objections to an assessment, or to the Commissioner's decisions on deemed fair market price, group transfers, the principal private residence tests and the reinvestment elections, are made within 30 days after the notice (s25(1)); Income Tax Act sections 62 to 70 apply (s25(2)). A checklist line now says so.",
    },
]


# ------------------------------------------------------------------------------------------------------ checklist wording

# (old text, new text) for the "Tax / CGT Advisory" checklist (seed.TAX_CGT_PROGRAM), in ChecklistTemplateItem and EngagementChecklistItem.
PROGRAM_PAIRS = [
    ("Obtain details of the asset(s)/transaction(s) in question",
     "Obtain details of the asset(s)/transaction(s) in question; confirm each is a specified asset (immovable property, a marketable security or a registered right - CGT Act s2(1), s8(1)) and whether the disposal is a sale or a deemed sale (s8(2))"),
    ("Obtain proof of original cost/base cost and acquisition date",
     "Obtain proof of original cost/base cost and acquisition date (CGT Act s11(2)(a): inherited assets at the estate valuation; assets acquired before 1 August 1981 at fair market value; leave out cost already deducted for income tax)"),
    ("Obtain details of improvements/capital expenditure incurred",
     "Obtain details of improvements/capital expenditure incurred (CGT Act s11(2)(b))"),
    ("Confirm disposal proceeds and disposal date",
     "Confirm disposal proceeds and disposal date; for an instalment or suspensive sale, the agreement date and the amounts still to come (CGT Act s18, s19)"),
    ("Determine applicable CGT rate and any exemptions/rollover relief",
     "Determine the CGT rate (the Act leaves it to the Finance Act, s7), any exemption (s10) and any rollover or reinvestment election (s15 to s17, s21, s22), each made no later than the date the return is submitted"),
    ("Compute capital gain/loss and inflation allowance where applicable",
     "Compute capital gain/loss: proceeds less cost, improvements, inflation allowance (s11(2)(c) - a Consumer Price Index formula, not a flat rate), selling expenses, the small-gain deduction (s11(2)(h)) and last year's assessed capital loss (s11(3))"),
    ("Consider withholding tax on disposal, where applicable",
     "Consider capital gains withholding tax on the disposal (CGT Act Part IIIA): due to ZIMRA by the 3rd working day from payment unless a clearance certificate is issued first (s22C to s22E); credit it against the tax (s22J)"),
    ("Review any relevant double tax treaty implications",
     "Review any relevant double tax treaty implications and relief from double taxation (CGT Act s28, applying Income Tax Act s91)"),
    ("File the return within the statutory deadline",
     "File the return by the date ZIMRA requires (CGT Act s23 applies the return and assessment sections of the Income Tax Act, from s37)"),
    ("Confirm payment of tax due",
     "Confirm payment of tax due: within 30 days of title being formally transferred (30 days from the agreement date for an instalment or suspensive sale), or on the earlier withholding date; interest runs on late payment (CGT Act s26)"),
]

# New lines for the template only (an existing engagement's checklist is not changed): (section, text)
NEW_PROGRAM_ITEMS = [
    ("Compliance & Filing", "Where the asset is registered (deeds office or share registry), confirm ZIMRA's certificate that the CGT has been paid is on file before the transfer is registered (CGT Act s30A)"),
    ("Compliance & Filing", "Diarise the 30 days to object to an assessment or a listed decision (CGT Act s25)"),
    ("Computation & Technical Analysis", "Where the asset is a mining title, consider the special capital gains tax on its transfer (CGT Act s30B: 20% of the value, or 5% with ministerial approval; payable within 30 days)"),
]
