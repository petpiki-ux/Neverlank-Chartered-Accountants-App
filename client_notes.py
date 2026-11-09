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


def prior_owner_loan_opening(engagement):
    """The shareholder loan balance at the start of the prior year = the closing balance in the client's previous engagement's trial
    balance (current-year column), when there is one; None when there is not."""
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
    total, found = 0.0, False
    for l in tb.lines:
        if l.fs_category in CATEGORY_BY_CODE and _is_owner_loan(l.account_name):
            total += _signed_category_value(l.fs_category, l.current_debit, l.current_credit)
            found = True
    return whole(total) if found else None


def attach_client_notes(statements, engagement, prior_opening="lookup"):
    if not statements or not statements.get("notes") or statements.get("_client_notes_done"):
        return statements
    client = engagement.client
    statements["_client_notes_done"] = True
    headers = year_headers(engagement, client)
    for note in statements["notes"]:
        loans = [a for a in note.get("accounts") or [] if _is_owner_loan(a["account_name"])]
        if loans and not note.get("movement"):
            if prior_opening == "lookup":
                try:
                    prior_opening = prior_owner_loan_opening(engagement)
                except Exception:
                    prior_opening = None
            cur_close = sum(a["current"] for a in loans)
            pri_close = sum(a["prior"] for a in loans)
            cur_open = pri_close
            pri_open = prior_opening
            terms = (getattr(client, "shareholder_loan_terms", None) or "").strip() or DEFAULT_LOAN_TERMS
            note["loan_layout"] = {
                "title": "Shareholder Loans", "headers": headers,
                "rows": [["Opening Balance", cur_open, pri_open],
                         ["Shareholder Funding", cur_close - cur_open, (pri_close - pri_open) if pri_open is not None else None]],
                "closing": [cur_close, pri_close], "after": terms,
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
                "blocks": [
                    {"heading": "Authorised:", "label": f"{auth:,} {cls} Shares of {per}", "cur": a_cur, "pri": a_pri},
                    {"heading": "Issued and fully paid:", "label": f"{issued:,} {cls} Shares of {per} issued", "cur": cur, "pri": pri},
                ],
                "check": abs(issued * par - cur) > 1.0,
            }
    return statements
