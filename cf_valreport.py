"""Valuation Model - the written valuation REPORT (approach, methodology, bridge, risks, limiting conditions), drafted section by section.

Two drafters share one fact base, so the numbers in the words always come from the model:
  * the built-in drafter (draft_template) writes every section from the model's own figures in the measured, hedged register of a formal
    valuation report - it needs no network and no key, and is the skeleton the AI works from;
  * the AI drafter (ai_draft, only when ANTHROPIC_API_KEY is set) rewrites a section's skeleton as a senior corporate-finance professional would -
    sharper structure, reasoning, and judgement - under strict rules: use only the supplied facts, keep every figure exactly as given, keep every
    [●] placeholder, never invent facts about the company, its market or its people.
After an AI draft the text is checked (unverified_numbers): any figure in it that is not in the model or the skeleton is flagged for the reviewer.
Every section is editable; an edited section is marked and is not overwritten by "draft all" unless asked.

Text format of a section: paragraphs separated by a blank line; a line starting "### " is a sub-heading; lines starting "- " are bullets.
"""
import json
import os
import re
from datetime import date

import cf_valmodel as V

PLACEHOLDER = "[●]"

# (id, title, included by default)
SECTIONS = [
    ("exec", "Executive summary", True),
    ("instructions", "Instructions, purpose and scope", True),
    ("basis", "Basis of value, premise of value and standard", True),
    ("background", "Background to the subject and its assets", True),
    ("info", "Information relied upon and procedures performed", True),
    ("approach", "Valuation approach and methodology", True),
    ("components", "Component valuations", True),
    ("consolidation", "Consolidation: from the components to the group value", True),
    ("goingconcern", "Going-concern and development value", True),
    ("adjustments", "Discounts, premiums and the interest valued", True),
    ("sensitivity", "Valuation range and sensitivity", True),
    ("reasonableness", "Reasonableness and cross-checks", True),
    ("assumptions", "Key assumptions", True),
    ("risks", "Key risks and uncertainties", True),
    ("conclusion", "Conclusion of value", True),
    ("limiting", "Limiting conditions, restrictions on use and reliance", True),
]
TITLES = {k: t for k, t, _ in SECTIONS}
IDS = [k for k, _, _ in SECTIONS]

BASIS_DEFS = {
    "Fair value": "the price that would be received to sell an asset or paid to transfer a liability in an orderly transaction between market participants at the measurement date (IFRS 13)",
    "Fair market value": "the price, expressed in terms of cash equivalents, at which property would change hands between a hypothetical willing and able buyer and a hypothetical willing and able seller, acting at arm's length in an open and unrestricted market, when neither is under compulsion to buy or sell and when both have reasonable knowledge of the relevant facts",
    "Market value": "the estimated amount for which an asset or liability should exchange on the valuation date between a willing buyer and a willing seller in an arm's length transaction, after proper marketing and where the parties had each acted knowledgeably, prudently and without compulsion",
    "Investment value": "the value of an asset to a particular owner or prospective owner for individual investment or operational objectives",
    "Liquidation value": "the amount that would be realised when an asset or group of assets is sold on a piecemeal basis, with the seller under no obligation to sell the assets as a going concern",
}
METHOD_DEFS = {
    "Discounted cash flow": "an income approach that discounts forecast cash flows to a present value at a rate reflecting the risk of those cash flows",
    "Market multiples": "a market approach that applies multiples observed for comparable listed companies or transactions to the subject's earnings, revenue or assets",
    "Net asset value": "an asset-based approach that restates the assets and liabilities to current values and takes the difference",
    "Cost approach": "an approach that reflects the cost to replace or reproduce the asset, less allowances for obsolescence",
    "Dividend / income approach": "an income approach that capitalises sustainable dividends or income at a required rate of return",
    "Forecast model valuation": "an income approach drawn from a forecast model prepared in the firm's corporate-finance module",
    "Independent valuation report": "a conclusion taken from an independent valuation report",
    "Valuation certificate": "a conclusion taken from a signed valuation certificate",
}


# --------------------------------------------------------------------------- formatting

def mny(cur, v):
    if v is None:
        return f"{cur} -"
    n = f"{abs(v):,.0f}"
    return f"negative {cur} {n}" if v < -0.5 else f"{cur} {n}"


def num(v, dp=0):
    return f"{v:,.{dp}f}" if v is not None else "-"


def pc(v, dp=1):
    s = f"{v:.{dp}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return f"{s}%"


def dfmt(v):
    d = V.parse_date(v)
    return f"{d.day} {d.strftime('%B %Y')}" if d else str(v or "")


GC_SHORT = {"dcf": "production-plan DCF", "synergy": "synergies", "development": "development option value", "other_add": "other addition", "capex": "capex still to be spent",
            "rampup": "ramp-up losses and working capital", "overlap": "removal of overlap with the component values", "other_deduct": "other deduction"}


def pts(v):
    return f"{v:g} percentage point" + ("" if abs(v - 1) < 1e-9 else "s")


def _date(d):
    return d.strftime("%d %B %Y") if d else PLACEHOLDER


def _list(items, joiner="and"):
    items = [x for x in items if x]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {joiner} " + items[-1]


# --------------------------------------------------------------------------- fact base

def facts(vm, res=None):
    """Everything a section needs, with the figures already formatted. Built only from the model."""
    d = V.normalise(vm.data, vm.currency)
    res = res or V.consolidate(d, vm.currency, vm.valuation_date)
    cur = vm.currency
    g = d["group"]
    whole = g["stake_pct"] == 100
    target = res["group_equity"] if whole else res["stake"]
    rp = d["report"]
    comps = []
    for r in res["rows"]:
        c = r["c"]
        row = {"name": c["name"], "type": c["type"], "method": c["method"] or "not stated", "currency": c["currency"], "basis": c["basis"], "date": c["date"],
               "ownership_pct": c["ownership_pct"], "net_debt": c["net_debt"], "control_pct": c["control_pct"], "minority_pct": c["minority_pct"],
               "dlom_pct": c["dlom_pct"], "source": c["source"]["label"] or "typed in by the preparer", "notes": c["notes"], "included": r["adjusted"] is not None,
               "problem": r["problem"], "rate": r["rate"]}
        if r["adjusted"] is not None:
            row.update(local=r["local"], equity_local=r["equity_local"], equity=r["equity"], attributable=r["attributable"], adjusted=r["adjusted"], share=r["share"])
        comps.append(row)
    live = [c for c in comps if c["included"]]
    largest = max(live, key=lambda c: c["adjusted"][1]) if live else None
    spreads = sorted(live, key=lambda c: c["adjusted"][2] - c["adjusted"][0], reverse=True)
    tot_spread = sum(c["adjusted"][2] - c["adjusted"][0] for c in live)
    gc = d["goingconcern"]
    gc_rows = []
    for r in res["gc_rows"]:
        it = r["g"]
        gc_rows.append({"label": it["label"], "kind": it["kind"], "kind_label": dict((k, l) for k, l, _ in V.GC_KINDS)[it["kind"]], "sign": r["sign"], "currency": it["currency"] or cur,
                        "probability": it["probability_pct"], "vals": r["vals"], "unrisked": r.get("unrisked"), "effect": r["effect"], "problem": r["problem"],
                        "counted": r["counted"], "notes": it["notes"], "years": len(it["cash_flows"]), "rate_pct": it["rate_pct"], "rate_range": it["rate_range"],
                        "growth_pct": it["growth_pct"], "mid_year": it["mid_year"], "tv_share": r["tv_share"]})
    hold = []
    for hr in res["holding_rows"]:
        h = hr["h"]
        hold.append({"label": h["label"], "kind": h["kind"], "currency": h["currency"] or cur, "effect": hr["effect"], "notes": h["notes"]})
    return {"vm": vm, "d": d, "res": res, "cur": cur, "client": vm.client.name, "engagement": vm.engagement.title if vm.engagement else "",
            "number": vm.vm_number, "vdate": vm.valuation_date, "vdate_s": _date(vm.valuation_date), "whole": whole, "g": g, "target": target,
            "comps": comps, "live": live, "largest": largest, "spreads": spreads, "tot_spread": tot_spread, "gc": gc, "gc_rows": gc_rows, "hold": hold,
            "rp": rp, "basis": d["basis_of_value"], "premise": d["premise"], "standard": d["standard"], "purpose": d["purpose"], "nar": d["narrative"],
            "fx": {k: v for k, v in d["fx"].items() if k != cur}}


def facts_json(F):
    """A compact JSON of the fact base for the AI (formatted strings alongside the raw values, no object references)."""
    cur = F["cur"]
    res = F["res"]

    def tri(t):
        return None if t is None else {"low": mny(cur, t[0]), "adopted": mny(cur, t[1]), "high": mny(cur, t[2])}
    out = {
        "firm": "Neverlank Chartered Accountants", "client": F["client"], "engagement": F["engagement"], "reference": F["number"], "valuation_date": F["vdate_s"],
        "reporting_currency": cur, "basis_of_value": F["basis"], "premise_of_value": F["premise"], "standard": F["standard"] or None, "purpose": F["purpose"] or None,
        "interest_valued": "100% of the group's equity" if F["whole"] else f"{pc(F['g']['stake_pct'])} of the group's equity",
        "conclusion": tri(F["target"]), "group_equity": tri(res["group_equity"]), "stake_value": tri(res["stake"]) if not F["whole"] else None,
        "per_share": None if not res["per_share"] else {"shares": F["g"]["shares"], "low": f"{res['per_share'][0]:,.4f}", "adopted": f"{res['per_share'][1]:,.4f}", "high": f"{res['per_share'][2]:,.4f}"},
        "sum_of_parts": tri(res["parts"]), "holding_additions": tri(res["additions"]), "holding_deductions": tri(res["deductions"]),
        "eliminations": tri(res["eliminations"]), "capitalised_central_costs": tri(res["capcost"]),
        "going_concern": None if not F["gc"]["enabled"] else {"stage": F["gc"]["stage"] or None, "net_risk_adjusted": tri(res["gc"]), "net_unrisked": tri(res["gc_unrisked"]),
                                                              "basis_note": F["gc"]["basis"] or None},
        "group_equity_before_group_adjustments": tri(res["pre"]),
        "group_adjustments": {"control_premium_pct": F["g"]["control_pct"], "minority_discount_pct": F["g"]["minority_pct"], "dlom_pct": F["g"]["dlom_pct"], "factor": round(res["group_factor"], 4)},
        "components": [{"name": c["name"], "type": c["type"], "method": c["method"], "valuation_date": c["date"] or None, "basis": c["basis"], "currency": c["currency"],
                        "ownership_pct": c["ownership_pct"], "control_pct": c["control_pct"], "minority_pct": c["minority_pct"], "dlom_pct": c["dlom_pct"],
                        "source": c["source"], "in_total": c["included"],
                        "adopted_value_own_currency": None if not c["included"] else f"{c['currency']} {c['local'][1]:,.0f}",
                        "contribution": None if not c["included"] else tri(c["adjusted"]), "share_of_sum_of_parts_pct": None if not c["included"] else round(c["share"] or 0, 1)}
                       for c in F["comps"]],
        "holding_items": [{"label": h["label"], "treatment": h["kind"], "effect_adopted": mny(cur, h["effect"][1]) if h["effect"] else None} for h in F["hold"]],
        "going_concern_items": [{"label": x["label"], "kind": x["kind_label"], "probability_pct": x["probability"], "counted": x["counted"],
                                 "unrisked_adopted": None if not x["unrisked"] else mny(cur, x["unrisked"][1]), "effect_adopted": None if not x["effect"] else mny(cur, x["effect"][1]),
                                 "dcf": None if x["kind"] != "dcf" else {"years": x["years"], "discount_rate_pct": x["rate_pct"], "rate_range_pts": x["rate_range"],
                                                                        "terminal_growth_pct": x["growth_pct"], "terminal_value_share_pct": None if x["tv_share"] is None else round(x["tv_share"], 0)}}
                                for x in F["gc_rows"]] if F["gc"]["enabled"] else [],
        "exchange_rates": F["fx"], "warnings": res["warnings"], "complete": res["complete"],
        "preparer_notes": {"scope": F["nar"]["scope"] or None, "assumptions": F["nar"]["assumptions"] or None, "limiting_conditions": F["nar"]["limiting"] or None,
                           "background_to_the_company": F["rp"]["background"] or None, "intended_users": F["rp"]["audience"] or None,
                           "extra_instructions": F["rp"]["instructions"] or None},
    }
    return json.dumps(out, indent=1, default=str)


# --------------------------------------------------------------------------- the built-in drafter

def _subject(F):
    return f"{F['client']} (together with the assets and interests described in this report, “the Group”)" if F["whole"] else \
        f"a {pc(F['g']['stake_pct'])} shareholding in {F['client']} (together with its assets and interests, “the Group”)"


def _rng(F, t=None):
    t = t or F["target"]
    return f"{mny(F['cur'], t[1])}, within a range of {mny(F['cur'], t[0])} to {mny(F['cur'], t[2])}"


def _t_exec(F):
    cur, res = F["cur"], F["res"]
    n = len(F["live"])
    p = [f"Neverlank Chartered Accountants (“Neverlank”, “we”) was engaged by {F['client']} (“the Company”) to estimate the {F['basis'].lower()} of {_subject(F)} as at "
         f"{F['vdate_s']} (“the Valuation Date”)" + (f", for the purpose of {F['purpose'].rstrip('.')}" if F["purpose"] else "") + ".",
         f"### Conclusion\nOn the basis and subject to the assumptions and limiting conditions set out in this report, we estimate the {F['basis'].lower()} of "
         f"{'the Group' if F['whole'] else 'the shareholding'} at the Valuation Date at {_rng(F)}."]
    if res["per_share"]:
        p[-1] += f" This equates to {cur} {res['per_share'][1]:,.4f} per share on {F['g']['shares']:,.0f} shares in issue (range {cur} {res['per_share'][0]:,.4f} to {cur} {res['per_share'][2]:,.4f})."
    how = [f"- The value is built up on a sum-of-the-parts basis from {n} component valuation{'s' if n != 1 else ''}, each converted to {cur}, adjusted for the Group's ownership and "
           f"for any control, minority and marketability adjustments." ]
    if F["live"]:
        L = F["largest"]
        how.append(f"- {L['name']} is the largest component, contributing {mny(cur, L['adjusted'][1])} ({pc(L['share'])} of the sum of the parts).")
    net_h = tuple(res["additions"][i] + res["deductions"][i] + res["eliminations"][i] + res["capcost"][i] for i in range(3))
    if F["hold"]:
        how.append(f"- Holding-company items, eliminations and capitalised central costs have a net effect of {mny(cur, net_h[1])} on the adopted value.")
    if F["gc"]["enabled"] and F["gc_rows"]:
        how.append(f"- Because the business is moving into production, a risk-adjusted going-concern and development value of {mny(cur, res['gc'][1])} has been added "
                   f"(before probability weighting: {mny(cur, res['gc_unrisked'][1])}).")
    if res["group_factor"] != 1:
        how.append(f"- Group-level adjustments (control premium {pc(F['g']['control_pct'])}, minority discount {pc(F['g']['minority_pct'])}, marketability discount {pc(F['g']['dlom_pct'])}) "
                   f"change the group equity value by a factor of {res['group_factor']:.4f}.")
    p.append("### How the value is built up\n" + "\n".join(how))
    if not res["complete"]:
        p.append("### Matters outstanding\nThe model is not yet complete: " + "; ".join(res["warnings"][:3]) + " The conclusion above must not be relied upon until these are resolved.")
    return "\n\n".join(p)


def _t_instructions(F):
    aud = F["rp"]["audience"].strip() or PLACEHOLDER
    p = [f"We were engaged by {F['client']} to estimate the {F['basis'].lower()} of {_subject(F)} as at {F['vdate_s']}"
         + (f". The purpose of the valuation is {F['purpose'].rstrip('.')}." if F["purpose"] else f". The purpose of the valuation is {PLACEHOLDER}."),
         f"This report is addressed to, and is intended solely for the use of, {aud}. It has been prepared for the purpose stated above and for no other purpose. "
         f"All amounts are expressed in {F['cur']} unless stated otherwise.",
         "### Scope of work\n- Review the component valuations supplied for each business, asset or interest, and the date, basis and method of each.\n"
         "- Convert each component to the reporting currency at the exchange rates at the Valuation Date and apply the Group's ownership of each.\n"
         "- Consolidate the components, together with holding-company assets, liabilities, eliminations and central costs, into a value for the Group.\n"
         + ("- Assess the further value attributable to the move into production, weighted for the risk that the plan is not achieved.\n" if F["gc"]["enabled"] else "")
         + "- Apply any control, minority and marketability adjustments appropriate to the interest valued, and conclude on a range and an adopted value."]
    if F["nar"]["scope"].strip():
        p.append(F["nar"]["scope"].strip())
    p.append(f"### Independence\n{PLACEHOLDER} [Engagement partner to confirm the independence and objectivity statement, and any relationship between Neverlank and the Company or its shareholders.]")
    return "\n\n".join(p)


def _t_basis(F):
    b = F["basis"]
    p = [f"The valuation has been prepared on the basis of {b}.",
         (f"{b} is defined as {BASIS_DEFS[b]}." if b in BASIS_DEFS else f"{b}: {PLACEHOLDER} [state the definition of this basis of value and its source].")]
    prem = F["premise"] or "Going concern"
    if prem.lower().startswith("going"):
        p.append("The premise of value is a going concern: we have assumed that the Group's businesses and assets will continue to be operated, or brought into operation, in the manner described to us, "
                 "and that the Group will have access to the funding and the licences and approvals needed to do so.")
    else:
        p.append(f"The premise of value is {prem.lower()}.")
    p.append(f"Standard: " + (F["standard"].strip() + "." if F["standard"].strip() else f"{PLACEHOLDER} [state the valuation standard followed, for example the International Valuation Standards, and the professional standards applied].")
             + " The valuation date is the date at which the estimate of value is made; events after that date have not been taken into account except where stated.")
    return "\n\n".join(p)


def _t_background(F):
    bg = F["rp"]["background"].strip()
    if bg:
        return bg + "\n\n" + (f"The assets and interests included in this valuation are: " + _list([f"{c['name']} ({c['type'].lower()})" for c in F["comps"] if c["included"]]) + ".")
    return (f"{PLACEHOLDER} [Describe {F['client']}: its history and ownership, the businesses and assets it holds, their location and stage of development, key licences and agreements, "
            f"management, funding and current status.]\n\n" + "The assets and interests included in this valuation are: " +
            _list([f"{c['name']} ({c['type'].lower()})" for c in F["comps"] if c["included"]]) + ".")


def _t_info(F):
    srcs = []
    for c in F["live"]:
        srcs.append(f"- {c['name']}: {c['method']}" + (f", valued at {dfmt(c['date'])}" if c["date"] else "") + f"; source: {c['source']}.")
    p = ["In arriving at our conclusion we relied on the component valuations, financial information and representations listed below. We have not audited, reviewed or independently "
         "verified this information and we accept no responsibility for its accuracy or completeness, except where we state that we have performed a procedure on it.",
         "### Component valuations\n" + ("\n".join(srcs) if srcs else f"{PLACEHOLDER}"),
         "### Procedures performed\n- Compared the valuation date, basis (equity or enterprise value) and method of each component, and the age of each valuation against the Valuation Date.\n"
         "- Deducted net debt from every component valued on an enterprise basis, and re-performed the conversion of each component to the reporting currency.\n"
         "- Re-performed the consolidation, including ownership, adjustments, holding-company items and the interest valued.\n"
         "- Considered the reasonableness of the result against the cross-checks described in this report."]
    old = [c for c in F["live"] if c["date"] and F["vdate"] and (F["vdate"] - V.parse_date(c["date"])).days > 366]
    if old:
        p.append("Note: " + _list([c["name"] for c in old]) + " " + ("was" if len(old) == 1 else "were") + " valued more than a year before the Valuation Date; "
                 "we have considered whether events since those dates would have a material effect, and " + PLACEHOLDER + " [state the conclusion reached].")
    return "\n\n".join(p)


def _t_approach(F):
    methods = []
    for c in F["live"]:
        if c["method"] not in methods and c["method"] != "not stated":
            methods.append(c["method"])
    p = ["International valuation practice recognises three broad approaches: the income approach (value is the present value of the economic benefits an asset is expected to generate), "
         "the market approach (value is inferred from the prices of comparable assets or businesses) and the cost or asset-based approach (value reflects the cost to replace or reproduce the asset, or the "
         "net value of its assets and liabilities). The appropriate approach depends on the nature of the asset, its stage of development, and the information available.",
         f"The Group holds {len(F['live'])} separately identifiable business{'es' if len(F['live']) != 1 else ''} and asset{'s' if len(F['live']) != 1 else ''} with different economic characteristics. "
         "We have therefore valued it on a sum-of-the-parts basis: each component is valued on the approach most suited to it, and the component values are then consolidated into a value for the Group. "
         "This allows each asset to be valued on its own evidence while ensuring that ownership, currency, holding-company items and cross-holdings are treated consistently." ]
    if methods:
        p.append("### Methods applied to the components\n" + "\n".join(f"- {m}: " + (METHOD_DEFS[m] if m in METHOD_DEFS else "the method stated in the supporting valuation") + "." for m in methods))
    steps = [f"Take each component's low, adopted and high value, and deduct net debt where the value is an enterprise value, to arrive at an equity value.",
             f"Convert to {F['cur']} at the exchange rate at the Valuation Date.", "Multiply by the Group's ownership percentage.",
             "Apply, where appropriate, a control premium, a minority discount and a discount for lack of marketability (DLOM), in that order.",
             "Add the components, then add or deduct holding-company assets and liabilities, and deduct eliminations of cross-holdings and intercompany balances, and the capitalised cost of central overheads."]
    if F["gc"]["enabled"]:
        steps.append("Add the risk-adjusted going-concern and development value that the component values do not already capture, after removing any overlap with them.")
    steps.append("Apply Group-level adjustments, and take the percentage of the Group that is the subject of the valuation.")
    p.append("### Steps in the consolidation\n" + "\n".join(f"- {s}" for s in steps))
    p.append("Each component is carried at low, adopted and high values, and the same steps are applied to each, so that the result is a range as well as a point estimate.")
    return "\n\n".join(p)


def _t_components(F):
    cur = F["cur"]
    out = []
    for c in F["comps"]:
        if not c["included"]:
            out.append(f"### {c['name']}\nThis component is not included in the total ({'excluded by the preparer' if c['problem'] == 'excluded' else 'it has no ' + ('value' if c['problem'] == 'no value' else 'exchange rate')}).")
            continue
        loc = c["local"]
        t = [f"{c['name']} ({c['type'].lower()}) has been valued using {c['method'].lower() if c['method'] != 'not stated' else 'a method not stated in the supporting document'}"
             + (f" as at {dfmt(c['date'])}" if c["date"] else "") + f", with an adopted {'enterprise' if c['basis'] == 'enterprise' else 'equity'} value of {c['currency']} {loc[1]:,.0f} "
             f"(range {c['currency']} {loc[0]:,.0f} to {c['currency']} {loc[2]:,.0f})."]
        if c["basis"] == "enterprise":
            t.append(f"Net debt of {c['currency']} {c['net_debt']:,.0f} has been deducted to arrive at an equity value of {c['currency']} {c['equity_local'][1]:,.0f}.")
        if c["currency"] != cur:
            t.append(f"The value has been converted at {c['rate']:,.4f} {cur} per {c['currency']}, giving {mny(cur, c['equity'][1])}.")
        t.append(f"The Group owns {pc(c['ownership_pct'])} of the component, an attributable value of {mny(cur, c['attributable'][1])}.")
        adj = []
        if c["control_pct"]:
            adj.append(f"a control premium of {pc(c['control_pct'])}")
        if c["minority_pct"]:
            adj.append(f"a minority discount of {pc(c['minority_pct'])}")
        if c["dlom_pct"]:
            adj.append(f"a discount for lack of marketability of {pc(c['dlom_pct'])}")
        if adj:
            t.append(f"After {_list(adj)}, the component contributes {mny(cur, c['adjusted'][1])} (range {mny(cur, c['adjusted'][0])} to {mny(cur, c['adjusted'][2])}), or {pc(c['share'])} of the sum of the parts.")
        else:
            t.append(f"No further adjustment is applied, so the component contributes {mny(cur, c['adjusted'][1])} (range {mny(cur, c['adjusted'][0])} to {mny(cur, c['adjusted'][2])}), or {pc(c['share'])} of the sum of the parts.")
        t.append(f"Source: {c['source']}." + (f" {c['notes'].strip()}" if c["notes"].strip() else ""))
        out.append(f"### {c['name']}\n" + " ".join(t))
    return "\n\n".join(out) if out else f"{PLACEHOLDER}"


def _t_consolidation(F):
    cur, res = F["cur"], F["res"]
    p = [f"The adjusted components sum to {mny(cur, res['parts'][1])} (range {mny(cur, res['parts'][0])} to {mny(cur, res['parts'][2])}). The table in the appendix shows the bridge from this "
         "sum to the value of the Group."]
    lines = []
    for key, label in (("additions", "Holding-company assets added"), ("deductions", "Holding-company liabilities deducted"), ("eliminations", "Eliminations of cross-holdings and intercompany balances"),
                       ("capcost", "Capitalised central costs deducted")):
        if any(abs(x) > 0.005 for x in res[key]):
            lines.append(f"- {label}: {mny(cur, res[key][1])}.")
    if F["gc"]["enabled"] and any(abs(x) > 0.005 for x in res["gc"]):
        lines.append(f"- Going-concern and development value (risk-adjusted, net): {mny(cur, res['gc'][1])}.")
    if lines:
        p.append("### Items between the components and the Group\n" + "\n".join(lines))
        hs = [h for h in F["hold"] if h["effect"]]
        if hs:
            p.append("The holding-company items are: " + "; ".join(f"{h['label']} ({mny(cur, h['effect'][1])})" + (f" - {h['notes']}" if h["notes"] else "") for h in hs) + ".")
    else:
        p.append("There are no holding-company items, eliminations or central costs: the Group value is the sum of the components.")
    p.append(f"The Group's equity value before Group-level adjustments is {mny(cur, res['pre'][1])}.")
    if F["fx"]:
        p.append("### Exchange rates\nComponents in other currencies were converted at: " + "; ".join(f"1 {k} = {v:,.4f} {cur}" for k, v in F["fx"].items()) + f", being the rates at the Valuation Date. {PLACEHOLDER} [state the source of the rates].")
    return "\n\n".join(p)


def _t_goingconcern(F):
    cur, res = F["cur"], F["res"]
    if not F["gc"]["enabled"] or not F["gc_rows"]:
        return "No going-concern or development value has been added to the component values in this valuation."
    stage = F["gc"]["stage"] or PLACEHOLDER
    hint = V.GC_STAGE_HINT.get(F["gc"]["stage"])
    p = [f"The Group is at the {stage.lower()} stage. The component valuations value each asset on the evidence supplied for it. Where the Group is moving into production, "
         "there can be value that those valuations do not capture - the value of the operating plan as a whole, synergies between the assets, and options to expand - and there are costs and risks "
         "of reaching production that must be deducted. We have assessed these separately, and added them to the sum of the parts only to the extent that they are not already reflected in it."]
    items = []
    for x in F["gc_rows"]:
        if not x["counted"]:
            items.append(f"- {x['label']}: not included ({'excluded' if not x['problem'] else 'incomplete - ' + x['problem']}).")
            continue
        line = f"- {x['label']} ({GC_SHORT[x['kind']]}): {mny(cur, abs(x['unrisked'][1]))} before risk weighting"
        line += f", weighted at {pc(x['probability'], 0)} probability, giving {mny(cur, x['effect'][1])}" if x["probability"] < 100 else ", at 100% probability"
        if x["kind"] == "dcf":
            line += (f". The production plan covers {x['years']} years, discounted at {pc(x['rate_pct'])} (range {pc(x['rate_pct'] - x['rate_range'])} to {pc(x['rate_pct'] + x['rate_range'])})"
                     + (f" with terminal growth of {pc(x['growth_pct'])}" if x["growth_pct"] is not None else " with no terminal value")
                     + (f"; the terminal value is {x['tv_share']:.0f}% of the value" if x["tv_share"] is not None else "") )
        if x["notes"]:
            line += f". {x['notes'].strip().rstrip('.')}"
        items.append(line + ".")
    p.append("### Items assessed\n" + "\n".join(items))
    p.append(f"In total the going-concern and development value is {mny(cur, res['gc'][1])} on a risk-adjusted basis (range {mny(cur, res['gc'][0])} to {mny(cur, res['gc'][2])}), compared with "
             f"{mny(cur, res['gc_unrisked'][1])} before probability weighting. The difference of {mny(cur, res['gc_unrisked'][1] - res['gc'][1])} is the allowance for the risk that the plan is not achieved.")
    if hint:
        p.append(f"### Probability of reaching steady-state production\nFor a project at the {stage.lower()} stage, we would ordinarily expect the probability of reaching steady-state production to lie in a range of about {hint}. "
                 f"The probabilities applied reflect {PLACEHOLDER} [explain the evidence for the probabilities used: funding, licences and permits, technical studies, offtake, management track record].")
    if F["gc"]["basis"].strip():
        p.append(F["gc"]["basis"].strip())
    return "\n\n".join(p)


def _t_adjustments(F):
    cur, res, g = F["cur"], F["res"], F["g"]
    p = []
    cmp_adj = [c for c in F["live"] if c["control_pct"] or c["minority_pct"] or c["dlom_pct"]]
    if cmp_adj:
        p.append("### Adjustments to components\n" + "\n".join(f"- {c['name']}: " + (_list([x for x in (f"control premium {pc(c['control_pct'])}" if c["control_pct"] else "",
                                                                                                         f"minority discount {pc(c['minority_pct'])}" if c["minority_pct"] else "",
                                                                                                         f"DLOM {pc(c['dlom_pct'])}" if c["dlom_pct"] else "") if x])) + "." for c in cmp_adj))
    if g["control_pct"] or g["minority_pct"] or g["dlom_pct"]:
        p.append("### Adjustments to the Group\n" + f"A control premium of {pc(g['control_pct'])}, a minority discount of {pc(g['minority_pct'])} and a discount for lack of marketability of {pc(g['dlom_pct'])} "
                 f"have been applied to the Group equity value, a net factor of {res['group_factor']:.4f}, moving the value from {mny(cur, res['pre'][1])} to {mny(cur, res['group_equity'][1])}.")
    if not p:
        p.append("No control premium, minority discount or discount for lack of marketability has been applied to the components or to the Group.")
    else:
        p.append(f"{PLACEHOLDER} [Support the percentages applied - for example by reference to empirical studies of control premiums and restricted-stock or pre-IPO discounts, the rights attaching to the interest valued, "
                 "and the liquidity of the shares - and explain why they are not double counted between the component and Group levels.]")
    p.append(f"### Interest valued\n" + (f"The valuation is of 100% of the Group's equity." if F["whole"] else
             f"The interest valued is {pc(g['stake_pct'])} of the Group's equity. Applied to the Group equity value of {mny(cur, res['group_equity'][1])}, the value of the interest is {mny(cur, res['stake'][1])}."))
    return "\n\n".join(p)


def _t_sensitivity(F):
    cur, res = F["cur"], F["res"]
    t = F["target"]
    width = (t[2] - t[0]) / t[1] * 100 if t[1] else 0
    p = [f"The valuation is expressed as a range because the component valuations, and the assumptions beneath them, are themselves estimates. The adopted value is {mny(cur, t[1])}; the low and high values are "
         f"{mny(cur, t[0])} and {mny(cur, t[2])}, so the range spans {width:.0f}% of the adopted value."]
    if F["live"] and F["tot_spread"] > 0 and any(c["adjusted"][2] > c["adjusted"][0] for c in F["live"]):
        top = [c for c in F["spreads"] if c["adjusted"][2] - c["adjusted"][0] > 0][:2]
        p.append("### What drives the range\n" + "\n".join(f"- {c['name']} accounts for {(c['adjusted'][2] - c['adjusted'][0]) / F['tot_spread'] * 100:.0f}% of the width of the range across the components." for c in top))
    if F["largest"]:
        L = F["largest"]
        p.append(f"{L['name']} is {pc(L['share'], 0)} of the sum of the parts, so a 10% change in its value would change the sum of the parts by about {mny(cur, L['adjusted'][1] * 0.1)}.")
    dcf = [x for x in F["gc_rows"] if x["kind"] == "dcf" and x["counted"]]
    if dcf:
        x = dcf[0]
        p.append(f"The going-concern DCF is shown at discount rates {pts(x['rate_range'])} either side of {pc(x['rate_pct'])}; the probability weighting is a further judgement and each 10 percentage points of probability on this item moves the value by about {mny(cur, abs(x['unrisked'][1]) * 0.1)}.")
    p.append("The ranges are not a statistical confidence interval. They show the spread between the low and high conclusions in the supporting valuations, carried through the same adjustments as the adopted values.")
    return "\n\n".join(p)


def _t_reasonableness(F):
    cur, res = F["cur"], F["res"]
    p = ["We considered the reasonableness of the conclusion by:",
         "- Re-performing the arithmetic of the consolidation independently of the model's own totals, and reconciling the components to the supporting valuations.",
         f"- Checking that no component is counted twice, and that cross-holdings and intercompany balances are eliminated (eliminations in this valuation: {mny(cur, res['eliminations'][1])}).",
         f"- Comparing the contribution of each component with its share of the whole: the largest is {F['largest']['name']} at {pc(F['largest']['share'], 0)}." if F["largest"] else "- Comparing the contribution of each component with its share of the whole.",
         f"- {PLACEHOLDER} [Cross-check the conclusion against market evidence: implied EV/EBITDA, price/earnings or price/book multiples, value per tonne, hectare or unit of capacity, and recent comparable transactions - and explain any difference.]"]
    if res["warnings"]:
        p.append("### Points noted in preparing the valuation\n" + "\n".join(f"- {w}" for w in res["warnings"][:8]))
    return "\n\n".join(p)


def _t_assumptions(F):
    items = ["The component valuations supplied to us are the best available estimates of the values of the underlying assets at their stated dates, and we have not been made aware of events since those dates that would materially change them.",
             "The Group's ownership percentage in each component is as stated, and the legal title to each asset is good and free of undisclosed encumbrances.",
             f"Exchange rates are those at the Valuation Date, and the {F['cur']} amounts are stated in nominal terms at that date.",
             "The holding-company assets, liabilities and central costs identified are complete, and there are no contingent liabilities, guarantees or commitments that have not been disclosed to us.",
             "The cross-holdings and intercompany balances eliminated are those that would otherwise be counted twice, and no other overlaps exist between the components."]
    if F["gc"]["enabled"]:
        items += ["The Group will obtain the funding, licences, permits and approvals needed to reach production on the timetable assumed in the production plan, and the plan reflects the capital expenditure still required.",
                  "The probabilities used to weight the going-concern and development value are our best estimate of the likelihood of the Group reaching steady-state production, given what was known at the Valuation Date."]
    p = ["The conclusion depends on the following assumptions. If any of them proves incorrect, the value may be materially different.", "\n".join(f"- {i}" for i in items)]
    if F["nar"]["assumptions"].strip():
        p.append("### Further assumptions specific to this engagement\n" + F["nar"]["assumptions"].strip())
    return "\n\n".join(p)


def _t_risks(F):
    risks = ["Reliance on the component valuations: the Group value is only as reliable as the valuations from which it is built, and we have not re-performed them.",
             "Age of information: values were struck at different dates, and market conditions can change between those dates and the Valuation Date.",
             f"Currency: components valued in other currencies are exposed to movements in exchange rates, and an exchange-rate movement changes the {F['cur']} value of those components directly.",
             "Ownership and title: the value attributable to the Group depends on the ownership percentages and rights stated, and on the enforceability of agreements with co-owners and counterparties.",
             "Liquidity and control: the discounts and premiums applied are judgements; a different view of the rights of the interest valued, or of its marketability, would change the value."]
    if F["gc"]["enabled"]:
        risks += ["Execution: the move into production may take longer, cost more or produce less than planned, including commissioning delays and a slower ramp-up.",
                  "Funding: the plan depends on capital that may not be available on the terms assumed, and funding may dilute the interest valued.",
                  "Market and price: the value of production depends on prices, demand and offtake terms that can move materially.",
                  "Licences and regulation: changes in licences, permits, taxes, royalties or exchange-control rules could change both the cost and the value of production."]
    p = ["The following matters could cause the actual value to differ from our conclusion.", "\n".join(f"- {r}" for r in risks)]
    return "\n\n".join(p)


def _t_conclusion(F):
    cur, res = F["cur"], F["res"]
    p = [f"Based on the work described in this report, and subject to the assumptions, risks and limiting conditions set out in it, we estimate the {F['basis'].lower()} of "
         f"{'the Group' if F['whole'] else 'a ' + pc(F['g']['stake_pct']) + ' shareholding in ' + F['client']} as at {F['vdate_s']} at {_rng(F)}.",
         f"We have adopted {mny(cur, F['target'][1])} as our point estimate."]
    if res["per_share"]:
        p.append(f"On {F['g']['shares']:,.0f} shares in issue, that equates to {cur} {res['per_share'][1]:,.4f} per share.")
    p.append("The conclusion is an estimate of value at a point in time. It is not a guarantee of the price that would be achieved in a transaction.")
    return "\n\n".join(p)


def _t_limiting(F):
    p = ["This report has been prepared solely for the purpose, and for the users, stated in it. It may not be used for any other purpose, or relied upon by any other person, without our written consent, and we accept no liability to any other party.",
         "Our conclusion is based on information provided to us by the Company and others, which we have assumed to be accurate and complete. We have not audited it, and our work is not an audit, review or due-diligence engagement.",
         "A valuation depends on forecasts and judgements about the future that are inherently uncertain. Actual outcomes may differ materially from those assumed, and we give no assurance that any forecast will be achieved.",
         "The conclusion speaks only as at the Valuation Date. We have no obligation to update this report for events or circumstances after that date.",
         "We have not considered the tax consequences to any person of the transaction or event for which the valuation is used, unless stated.",
         "This report is confidential. It may not be published, quoted or referred to, in whole or in part, in any document without our prior written consent."]
    out = "\n\n".join(p)
    if F["nar"]["limiting"].strip():
        out += "\n\n### Further limiting conditions specific to this engagement\n" + F["nar"]["limiting"].strip()
    return out


_TEMPLATES = {"exec": _t_exec, "instructions": _t_instructions, "basis": _t_basis, "background": _t_background, "info": _t_info, "approach": _t_approach,
              "components": _t_components, "consolidation": _t_consolidation, "goingconcern": _t_goingconcern, "adjustments": _t_adjustments,
              "sensitivity": _t_sensitivity, "reasonableness": _t_reasonableness, "assumptions": _t_assumptions, "risks": _t_risks, "conclusion": _t_conclusion,
              "limiting": _t_limiting}


def draft_template(section_id, F):
    """The built-in draft of one section from the model's figures. Never raises."""
    fn = _TEMPLATES.get(section_id)
    if fn is None:
        return ""
    try:
        return fn(F).strip()
    except Exception as exc:  # a drafting bug must not take the page down
        return f"{PLACEHOLDER} [This section could not be drafted automatically ({exc.__class__.__name__}).]"


# --------------------------------------------------------------------------- the AI drafter

AI_STYLE = (
    "You are a senior director in the valuation and corporate-finance practice of a leading international advisory firm, writing a section of a formal valuation report that will be signed "
    "by the Managing Partner of Neverlank Chartered Accountants, Harare, Zimbabwe. Write as the best corporate-finance and valuation houses do: precise, measured, evidence-led, "
    "and free of salesmanship. Use the first person plural (“we”) for the firm, and the passive voice for procedures. Lead with the conclusion or the point, then give the reasoning. "
    "Quantify wherever the facts allow. Where a judgement was made, say what it was and why; where something is uncertain, say so plainly and say what would change the answer. "
    "Reference accepted valuation practice (IVS, IFRS 13, income / market / cost approaches, control and marketability adjustments) where it helps the reader, but do not cite specific "
    "studies, statistics, comparable companies or transactions, laws or regulations unless they appear in the facts. "
    "HARD RULES: (1) Use ONLY the facts supplied. Never invent facts about the company, its people, assets, markets, licences, contracts, funding, history or plans. "
    "(2) Every number, date, percentage, name and currency amount you write must appear in the facts or the skeleton, exactly as written there; do not compute new totals, percentages or ratios. "
    "(3) Keep every [●] placeholder, and the bracketed instruction beside it, exactly as in the skeleton - a person will complete them. Where the report plainly needs information the facts do not contain, "
    "add a [●] placeholder with a short bracketed instruction rather than guessing. (4) Do not add a section heading for the section itself, a signature, or a preamble. "
    "(5) Format: paragraphs separated by a blank line; a line starting with '### ' is a sub-heading; lines starting with '- ' are bullets. No other markdown, no bold, no tables, no emoji. "
    "(6) Keep the length close to the skeleton unless the section needs more explanation; never pad."
)

BRIEFS = {
    "exec": "Executive summary for a reader who will read nothing else: the engagement, the conclusion with range, how the value is built up, the one or two judgements that matter most, and any matter outstanding.",
    "instructions": "Why we were engaged, for whom, as at what date, on what scope, and the independence statement placeholder.",
    "basis": "The basis of value with its definition, the premise of value and the standard followed; explain in a sentence why this basis suits the stated purpose.",
    "background": "Background to the subject and its assets, using only what the preparer has supplied; otherwise leave clear placeholders for what a reader would expect to find.",
    "info": "What we were given and what we did with it; be candid about what we did not verify.",
    "approach": "Why a sum-of-the-parts approach, the approaches and methods used for the components, and the logic of the steps from component to Group value.",
    "components": "One short sub-section per component: what it is, how it was valued, the key figures, and what it contributes. Keep the figures exactly.",
    "consolidation": "Walk the reader from the sum of the components to the Group value: holding items, eliminations, central costs, the going-concern item if present, and currency conversion.",
    "goingconcern": "Explain why value beyond the component valuations may exist for a business moving into production, what was assessed, how risk was reflected through probabilities, how overlap with the components was avoided, and what remains uncertain.",
    "adjustments": "Explain the control, minority and marketability adjustments and the interest valued; keep the placeholder where support for percentages is needed.",
    "sensitivity": "Explain the range, what drives it, and how sensitive the result is to the largest component and the going-concern judgements. Do not invent new sensitivities beyond those in the skeleton.",
    "reasonableness": "The cross-checks performed and still to be performed; keep the placeholder for market evidence.",
    "assumptions": "The assumptions on which the conclusion depends, in order of importance.",
    "risks": "The risks and uncertainties that could make the actual value differ, ordered by importance to this valuation.",
    "conclusion": "A clear, quotable statement of the conclusion and range, with the caveat that it is an estimate at a point in time.",
    "limiting": "Limiting conditions and restrictions on use and reliance in the firm's formal register.",
}


def ai_available():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def ai_draft(section_id, F, skeleton, fj=None):
    """(text, error). Rewrites the skeleton as polished report prose. Never raises."""
    if not ai_available():
        return "", "ANTHROPIC_API_KEY is not set"
    try:
        import entity_extraction as EX
        client = EX._client()
        fj = fj or facts_json(F)
        user = (f"SECTION TO WRITE: {TITLES[section_id]}\nBRIEF: {BRIEFS.get(section_id, '')}\n"
                + (f"EXTRA INSTRUCTIONS FROM THE PREPARER: {F['rp']['instructions']}\n" if F["rp"]["instructions"].strip() else "")
                + f"\nFACTS (JSON):\n{fj}\n\nSKELETON (accurate figures and structure - improve the writing and reasoning, keep the figures and placeholders):\n{skeleton}\n\n"
                  "Write the final text of this section now.")
        resp = client.messages.create(model=EX.DEFAULT_MODEL, max_tokens=2600, system=AI_STYLE, messages=[{"role": "user", "content": user}])
        text = "".join(getattr(b, "text", "") for b in resp.content).strip()
        if not text:
            return "", "The writer returned no text."
        return clean_text(text), None
    except Exception as exc:
        return "", str(exc)[:300]


def clean_text(text):
    """Normalise model output to the section format: strip markdown emphasis and numbered-heading noise, collapse blank lines."""
    t = text.replace("\r\n", "\n").strip()
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"(?m)^#{1,2}[ \t]+", "### ", t)
    t = re.sub(r"(?m)^#{4,}[ \t]+", "### ", t)
    t = re.sub(r"(?m)^[ \t]*[*•][ \t]+", "- ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


_NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?")


def _numbers(text):
    out = set()
    for m in _NUM_RE.findall(text or ""):
        tok = m.rstrip("%").replace(",", "")
        try:
            v = float(tok)
        except ValueError:
            continue
        out.add(round(v, 4))
        if m.endswith("%"):
            out.add(("pct", round(v, 4)))
    return out


def unverified_numbers(text, F, skeleton=""):
    """Figures in `text` that appear neither in the model's facts nor in the skeleton (small whole numbers and years are ignored)."""
    allowed = _numbers(facts_json(F)) | _numbers(skeleton)
    bad = []
    for m in _NUM_RE.findall(text or ""):
        tok = m.rstrip("%").replace(",", "")
        try:
            v = round(float(tok), 4)
        except ValueError:
            continue
        if v in allowed or (v == int(v) and (0 <= v <= 31 or 1900 <= v <= 2100)):
            continue
        if m not in bad:
            bad.append(m)
    return bad[:12]


# --------------------------------------------------------------------------- report state on the model

def ordered_sections(d):
    """[(id, title, state)] in report order, with the state from the model's data (or a default)."""
    rp = d["report"]["sections"]
    out = []
    for sid, title, inc in SECTIONS:
        st = rp.get(sid) or {"text": "", "origin": "", "include": inc and (sid != "goingconcern" or d["goingconcern"]["enabled"]), "at": ""}
        out.append((sid, title, st))
    return out


def placeholders(text):
    return len(re.findall(re.escape(PLACEHOLDER), text or ""))


def report_ready(d):
    """(has_text, placeholder_count, included_sections) for the report written so far."""
    secs = [(sid, st) for sid, _, st in ordered_sections(d) if st["include"] and (st["text"] or "").strip()]
    return bool(secs), sum(placeholders(st["text"]) for _, st in secs), len(secs)


def parse_blocks(text):
    """[(kind, text)] with kind in 'p', 'h', 'b' for the exporters."""
    blocks = []
    for raw in (text or "").replace("\r\n", "\n").split("\n\n"):
        raw = raw.strip()
        if not raw:
            continue
        lines = raw.split("\n")
        i = 0
        buf = []
        while i < len(lines):
            ln = lines[i].rstrip()
            if ln.startswith("### "):
                if buf:
                    blocks.append(("p", " ".join(buf)))
                    buf = []
                blocks.append(("h", ln[4:].strip()))
            elif ln.lstrip().startswith("- "):
                if buf:
                    blocks.append(("p", " ".join(buf)))
                    buf = []
                blocks.append(("b", ln.lstrip()[2:].strip()))
            elif ln.strip():
                buf.append(ln.strip())
            i += 1
        if buf:
            blocks.append(("p", " ".join(buf)))
    return blocks
