"""Notes that need the client's own particulars: the Share capital note (authorised / issued shares and nominal value, entered on the
client page) and the Shareholder loans note (opening balance, funding / repayments, closing balance for both years, with the loan terms).

`attach_client_notes(statements, engagement)` adds `share_layout` / `loan_layout` to the matching notes of a built statements dict; the
Word pack and the Finalisation tab draw those layouts instead of the plain account list. Idempotent."""
from financials import whole, _is_owner_loan, _signed_category_value, CATEGORY_BY_CODE

DEFAULT_LOAN_TERMS = "The loan is unsecured, interest free and has no fixed repayment terms."
CURRENCY_SYMBOL = {"USD": "$", "ZWG": "ZWG ", "ZWL": "ZWL ", "ZAR": "R"}


def currency_of(client):
    return ((getattr(client, "reporting_currency", None) or "USD").strip().upper()) or "USD"


def symbol_of(code):
    return CURRENCY_SYMBOL.get(code, code + " ")


def _par_text(par):
    return f"{par:,.2f}" if abs(par * 100 - round(par * 100)) < 1e-9 else f"{par:,.4f}".rstrip("0")


def year_headers(engagement, client):
    cur = currency_of(client)
    y = engagement.period_end.year if engagement.period_end else None
    return [f"{y} {cur}" if y else f"Current year {cur}", f"{y - 1} {cur}" if y else f"Prior year {cur}"]


def prior_owner_loan_openings(engagement):
    """Each shareholder loan account's balance at the start of the prior year = its closing balance in the client's previous engagement's
    trial balance (current-year column): {account name (case-folded): amount}. None when there is no previous engagement / trial balance."""
    from models import Engagement, TrialBalance
    if not engagement.period_end or not engagement.client_id:
        return None
    prev = (Engagement.query.filter(Engagement.client_id == engagement.client_id, Engagement.id != engagement.id,
                                    Engagement.period_end < engagement.period_end)
            .order_by(Engagement.period_end.desc()).first())
    if prev is None:
        return None
    tb = TrialBalance.query.filter_by(engagement_id=prev.id).first()
    if not tb or not tb.lines:
        return None
    out = {}
    for l in tb.lines:
        if l.fs_category in CATEGORY_BY_CODE and _is_owner_loan(l.account_name):
            key = (l.account_name or "").strip().casefold()
            out[key] = out.get(key, 0.0) + _signed_category_value(l.fs_category, l.current_debit, l.current_credit)
    return {k: whole(v) for k, v in out.items()}


def prior_owner_loan_opening(engagement):
    """Total of prior_owner_loan_openings(), or None."""
    d = prior_owner_loan_openings(engagement)
    return whole(sum(d.values())) if d else None


def _loan_lines(loans, openings):
    """Rows of the Shareholder Loans note. One loan account: Opening Balance, Shareholder Funding, closing. Several (one account per
    shareholder): a block per shareholder, then the total of all of them. A line is {"label", "cur", "pri", "style"}, style one of
    head (bold label), row, sub (single rule above the figures), total (single rule above, double below), gap."""
    def block(name, cur_close, pri_close, pri_open, multi):
        cur_open = pri_close
        out = []
        if multi:
            out.append({"label": name, "cur": None, "pri": None, "style": "head"})
        out.append({"label": "Opening Balance", "cur": cur_open, "pri": pri_open, "style": "row"})
        out.append({"label": "Shareholder Funding", "cur": cur_close - cur_open,
                    "pri": (pri_close - pri_open) if pri_open is not None else None, "style": "row"})
        return out, cur_open
    multi = len(loans) > 1
    lines = [{"label": "Shareholder Loans", "cur": None, "pri": None, "style": "head"}]
    tot = {"open_c": 0.0, "open_p": 0.0, "fund_c": 0.0, "fund_p": 0.0, "close_c": 0.0, "close_p": 0.0, "p_known": True}
    for a in loans:
        po = None if openings is None else openings.get((a["account_name"] or "").strip().casefold())
        rows, cur_open = block(a["account_name"], a["current"], a["prior"], po, multi)
        lines += rows
        lines.append({"label": "", "cur": a["current"], "pri": a["prior"], "style": "total" if not multi else "sub"})
        if multi:
            lines.append({"label": "", "cur": None, "pri": None, "style": "gap"})
        tot["open_c"] += cur_open; tot["fund_c"] += a["current"] - cur_open; tot["close_c"] += a["current"]; tot["close_p"] += a["prior"]
        if po is None:
            tot["p_known"] = False
        else:
            tot["open_p"] += po; tot["fund_p"] += a["prior"] - po
    if multi:
        lines.append({"label": "Total shareholder loans", "cur": None, "pri": None, "style": "head"})
        lines.append({"label": "Opening Balance", "cur": tot["open_c"], "pri": tot["open_p"] if tot["p_known"] else None, "style": "row"})
        lines.append({"label": "Shareholder Funding", "cur": tot["fund_c"], "pri": tot["fund_p"] if tot["p_known"] else None, "style": "row"})
        lines.append({"label": "", "cur": tot["close_c"], "pri": tot["close_p"], "style": "total"})
    return lines


def attach_client_notes(statements, engagement, prior_opening="lookup"):
    if not statements or not statements.get("notes") or statements.get("_client_notes_done"):
        return statements
    client = engagement.client
    statements["_client_notes_done"] = True
    headers = year_headers(engagement, client)
    statements["year_labels"] = headers
    for note in statements["notes"]:
        loans = [a for a in note.get("accounts") or [] if _is_owner_loan(a["account_name"])]
        if loans and not note.get("movement"):
            if prior_opening == "lookup":
                try:
                    prior_opening = prior_owner_loan_openings(engagement)
                except Exception:
                    prior_opening = None
            terms = (getattr(client, "shareholder_loan_terms", None) or "").strip() or DEFAULT_LOAN_TERMS
            note["loan_layout"] = {
                "title": "Shareholder Loans", "headers": headers,
                "lines": _loan_lines(loans, prior_opening), "after": terms,
                "replaces_breakdown": len(loans) == len(note["accounts"]),
            }
        if note["title"] == "Share capital":
            auth = getattr(client, "authorised_shares", None)
            issued = getattr(client, "issued_shares", None)
            par = getattr(client, "share_par_value", None)
            if auth is None or issued is None or par is None:
                continue
            cls = (getattr(client, "share_class", None) or "Ordinary").strip()
            sym = symbol_of(currency_of(client))
            per = f"{sym}{_par_text(par)}"
            cur, pri = note["total"]["current"], note["total"]["prior"]
            if auth == issued:
                a_cur, a_pri = cur, pri
            else:
                a_cur = a_pri = whole(auth * par)
            note["share_layout"] = {
                "headers": headers,
                "lines": [
                    {"label": "Authorised:", "cur": None, "pri": None, "style": "head"},
                    {"label": f"{auth:,} {cls} Shares of {per}", "cur": a_cur, "pri": a_pri, "style": "double"},
                    {"label": "", "cur": None, "pri": None, "style": "gap"},
                    {"label": "Issued and fully paid:", "cur": None, "pri": None, "style": "head"},
                    {"label": f"{issued:,} {cls} Shares of {per} issued", "cur": cur, "pri": pri, "style": "double"},
                ],
                "check": abs(issued * par - cur) > 1.0,
            }
    return statements
