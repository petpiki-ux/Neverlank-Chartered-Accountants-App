"""Load existing financial statements (PDF / Word / Excel / CSV / pasted text) and pick out the key figures that feed the
Corporate Finance models: the prior-year accounts of a management accounts pack, and the historical analysis of a forecast
model (which in turn drives opening balances and starting assumptions).

Two readers, same output:
  * AI reader   - Claude, when ANTHROPIC_API_KEY is set (handles any layout, scanned pages, notes).
  * Built-in    - a keyword reader for typical statements (a label followed by one or two amount columns). Used when there is
                  no API key or the AI call fails, so the feature never disappears.
Either way the figures are shown for the preparer to CHECK and edit before anything is saved.

Output shape (an "extraction"):
  {"method": "ai"|"builtin", "entity", "currency", "scale": 1|1000|1000000,
   "periods": [{"label", "is": {STD_IS...}, "bs": {STD_BS...}}], "notes": [str], "checks": [...] }
Amounts are in the units shown in the statements; `scale` converts them to whole units when applied.
"""
import os
import re

import cf_ma as M

STD_IS = [
    ("revenue", "Revenue / turnover"), ("cogs", "Cost of sales"), ("other_income", "Other income"),
    ("opex", "Operating expenses (excl. depreciation, finance costs, tax)"), ("dep", "Depreciation and amortisation"),
    ("finance_costs", "Finance costs / interest expense"), ("interest_income", "Finance income / interest received"),
    ("tax", "Income tax expense"), ("pbt", "Profit before tax (as reported)"), ("pat", "Profit after tax (as reported)"),
    ("dividends", "Dividends declared"),
]
STD_BS = [
    ("ppe", "Property, plant and equipment (net)"), ("intangibles", "Intangibles, investments and other non-current assets"),
    ("inventory", "Inventory"), ("receivables", "Trade receivables"), ("other_ca", "Other current assets"),
    ("cash", "Cash and bank"), ("overdraft", "Bank overdraft"), ("payables", "Trade payables"),
    ("other_cl", "Other liabilities (tax, provisions, other payables)"), ("borrowings", "Borrowings (loans, leases)"),
    ("share_capital", "Share capital and premium"), ("retained", "Retained earnings and reserves"),
    ("total_assets", "Total assets (as reported)"), ("total_equity", "Total equity (as reported)"),
    ("total_liabilities", "Total liabilities (as reported)"),
]
IS_KEYS = [k for k, _ in STD_IS]
BS_KEYS = [k for k, _ in STD_BS]
SIGNED = {"pbt", "pat", "retained"}          # may legitimately be negative; every other figure is held as a positive amount
SUMMED = {"ppe", "intangibles", "other_ca", "other_cl", "borrowings", "share_capital", "retained", "opex_part"}

SCALES = {"units": 1, "thousands": 1000, "millions": 1000000}

_IS_RULES = [
    ("pat", ["profit for the year", "profit/(loss) for the year", "(loss)/profit for the year", "profit after tax", "profit after taxation",
             "net profit", "loss for the year", "profit for the period", "profit/(loss) after tax", "(loss)/profit after tax", "loss for the period"]),
    ("pbt", ["profit before tax", "profit before taxation", "profit/(loss) before tax", "(loss)/profit before tax", "loss before tax"]),
    (None, ["gross profit", "gross loss", "operating profit", "profit from operations", "operating loss", "ebitda", "total comprehensive"]),
    ("cogs", ["cost of sales", "cost of goods sold", "cost of revenue", "direct costs", "cost of services"]),
    ("tax", ["income tax", "taxation", "tax expense", "tax charge"]),
    ("other_income", ["other income", "other operating income", "sundry income", "other gains"]),
    ("interest_income", ["finance income", "interest income", "interest received", "investment income"]),
    ("finance_costs", ["finance costs", "finance cost", "interest expense", "interest paid", "finance expense", "interest on"]),
    ("dep", ["depreciation", "amortisation", "amortization"]),
    ("opex", ["total operating expenses", "total expenses", "total administrative expenses", "total operating costs"]),
    ("opex_part", ["administrative expenses", "admin expenses", "distribution costs", "selling and distribution", "selling expenses",
                   "marketing expenses", "other operating expenses", "other expenses", "operating expenses", "staff costs"]),
    ("dividends", ["dividend"]),
    ("revenue", ["revenue", "turnover", "sales", "income from contracts"]),
]
_BS_RULES = [
    (None, ["total current", "total non-current", "total non current", "net current", "net assets", "total equity and liabilities",
            "total liabilities and equity", "total equity & liabilities"]),
    ("total_assets", ["total assets"]),
    ("total_equity", ["total equity", "shareholders' equity", "shareholders equity", "capital and reserves", "equity attributable", "total shareholders"]),
    ("total_liabilities", ["total liabilities"]),
    ("overdraft", ["bank overdraft", "overdraft"]),
    ("borrowings", ["borrowings", "bank loan", "term loan", "debenture", "lease liabilit", "mortgage", "loans"]),
    ("payables", ["trade and other payables", "trade payables", "accounts payable", "creditors", "accruals"]),
    ("other_cl", ["provision", "current tax payable", "tax payable", "vat payable", "deferred income", "other payables", "contract liabilit",
                  "deferred tax liabilit", "dividends payable", "other current liabilities", "other liabilities", "employee benefit", "amounts due to"]),
    ("inventory", ["inventor", "stock"]),
    ("receivables", ["trade and other receivables", "trade receivables", "accounts receivable", "debtors"]),
    ("cash", ["cash and cash equivalents", "cash and bank", "cash at bank", "bank and cash", "cash on hand", "cash"]),
    ("other_ca", ["prepayment", "other receivable", "other current assets", "tax receivable", "current tax asset", "vat receivable", "deposits",
                  "amounts due from", "loans to"]),
    ("ppe", ["property, plant", "property plant", "plant and equipment", "fixed assets", "investment property", "right-of-use", "right of use",
             "biological assets", "land and buildings", "motor vehicles"]),
    ("intangibles", ["intangible", "goodwill", "investment in", "investments", "deferred tax asset", "other non-current assets"]),
    ("share_capital", ["share capital", "stated capital", "share premium", "ordinary share"]),
    ("retained", ["retained", "accumulated", "reserve", "revaluation", "earnings"]),
]

_NUM = re.compile(r"^\(?-?[0-9][0-9,]*(?:\.[0-9]+)?\)?$")
_DASH = {"-", "–", "—", "nil"}


def _num(tok):
    t = tok.strip()
    if t.lower() in _DASH:
        return 0.0
    if not _NUM.match(t):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _split_line(line):
    """-> (label, [numbers]) for a statement line. Cells joined with ' | ' (our Excel/Word extraction) or plain spaced text."""
    line = line.strip()
    if not line:
        return None, []
    if " | " in line:
        cells = [c.strip() for c in line.split(" | ")]
        label = next((c for c in cells if c and _num(c) is None), "")
        idx = cells.index(label) if label else 0
        nums = [_num(c) for c in cells[idx + 1:] if c != ""]
        nums = [x for x in nums if x is not None]
    else:
        toks = line.split()
        nums = []
        while toks and (_num(toks[-1]) is not None):
            nums.append(_num(toks.pop()))
        nums.reverse()
        label = " ".join(toks)
    label = re.sub(r"\s+", " ", label).strip(" :.-")
    # drop a "Notes" reference column (a small whole number ahead of the amounts)
    if len(nums) >= 2 and nums[0] is not None and float(nums[0]).is_integer() and 0 < nums[0] <= 40 and (len(nums) >= 3 or abs(nums[1]) >= 1000):
        nums = nums[1:]
    return label, nums


def _match(label, rules):
    low = label.lower()
    for key, words in rules:
        for w in words:
            if w in low:
                return key, True
    return None, False


def detect_scale(text):
    head = (text or "")[:4000].lower()
    if re.search(r"(in|\$|usd|zwg|zwl|zar|us\$)?\s*'000|\bthousands?\b|\bu?s?d?\s*000s\b", head):
        return 1000
    if re.search(r"\bmillions?\b|\bm\b\s*\)", head):
        return 1000000
    return 1


def detect_currency(text):
    m = re.search(r"\b(USD|ZWG|ZWL|ZAR|GBP|EUR|BWP)\b|US\$", (text or "")[:6000])
    if not m:
        return None
    return "USD" if m.group(0) == "US$" else m.group(0)


def detect_year_labels(lines, ncols):
    """Look near the top of the statements for a header line made only of years, e.g. '2025 2024' or '31 December 2025 31 December 2024'."""
    for line in lines[:400]:
        yrs = re.findall(r"\b(19[89]\d|20[0-4]\d)\b", line)
        stripped = re.sub(r"\b(19[89]\d|20[0-4]\d)\b|31|30|december|june|march|september|january|february|april|may|july|august|october|november|\s|\||usd|us\$|zwg|zwl|note|notes|\$|year|ended|restated|audited|unaudited", "", line.lower())
        if len(yrs) >= 2 and not stripped.strip(" ,.-:()"):
            return [f"FY{y}" for y in yrs[:ncols]]
    return []


def builtin_extract(text):
    """Keyword reader. Returns an extraction (method 'builtin') or raises ValueError when nothing is recognised."""
    lines = [ln for ln in (text or "").splitlines()]
    section = None
    items = {}   # key -> {"label": first label, "labels": {label: values}}
    for raw in lines:
        label, nums = _split_line(raw)
        if not label:
            continue
        low = label.lower()
        heading = (not nums) or bool(re.search(r"statement of|balance sheet|income statement|\bas at\b|for the (year|period) ended", low))
        if heading:
            if "financial position" in low or "balance sheet" in low:
                section = "bs"
            elif "profit or loss" in low or "income statement" in low or "comprehensive income" in low or "statement of profit" in low:
                section = "is"
            elif "cash flow" in low:
                section = "cf"
            elif low.startswith("notes to") or "accounting policies" in low:
                section = "notes"
            if not nums or re.search(r"statement of|balance sheet|income statement", low):
                continue
        if section in ("cf", "notes"):
            continue
        key, hit = (None, False)
        balance_word = any(w in low for w in ("payable", "receivable", "reserve", "accumulated", "liabilit", "asset", "borrowing", "overdraft"))
        if section in (None, "is") and not balance_word:
            key, hit = _match(label, _IS_RULES)
            if hit:
                bucket = "is"
        if not hit and section in (None, "bs"):
            key, hit = _match(label, _BS_RULES)
            if hit:
                bucket = "bs"
        if not hit or key is None:
            continue
        # statement-of-financial-position totals can read like P&L words ("total equity"): the BS rules win on those
        if bucket == "is" and ("total equity" in low or "total assets" in low or "total liabilities" in low):
            continue
        it = items.setdefault((bucket, key), {"labels": {}})
        nl = low
        if nl in it["labels"]:
            continue
        it["labels"][nl] = {"label": label, "values": nums}
    if not items:
        raise ValueError("No financial statement lines were recognised in that document.")
    ncols = max(len(v["values"]) for it in items.values() for v in it["labels"].values())
    ncols = max(1, min(ncols, 4))
    periods = [{"label": "", "is": {}, "bs": {}} for _ in range(ncols)]
    found = {}
    for (bucket, key), it in items.items():
        vals_list = list(it["labels"].values())
        for j in range(ncols):
            get = lambda v: (v["values"][j] if j < len(v["values"]) else None)
            if key in SUMMED or key == "opex_part":
                col = [get(v) for v in vals_list if get(v) is not None]
                if not col:
                    continue
                val = sum(col)
            else:
                val = next((get(v) for v in vals_list if get(v) is not None), None)
                if val is None:
                    continue
            tgt = "opex_part" if key == "opex_part" else key
            if bucket == "is":
                periods[j]["is"][tgt] = val
            else:
                periods[j]["bs"][tgt] = val
        found[(bucket, key)] = vals_list[0]["label"]
    for p in periods:
        part = p["is"].pop("opex_part", None)
        if "opex" not in p["is"] and part is not None:
            p["is"]["opex"] = part
        for k in list(p["is"]):
            if k not in SIGNED:
                p["is"][k] = abs(p["is"][k])
        for k in list(p["bs"]):
            if k not in SIGNED:
                p["bs"][k] = abs(p["bs"][k])
    labels = detect_year_labels(lines, ncols)
    for j, p in enumerate(periods):
        p["label"] = labels[j] if j < len(labels) else ("Current year" if j == 0 else ("Prior year" if j == 1 else f"Year -{j}"))
    # a statement set with only one column of figures still gets a label
    return {"method": "builtin", "entity": "", "currency": detect_currency(text), "scale": detect_scale(text), "periods": periods,
            "notes": ["Read with the built-in reader (keywords and amount columns). Check every figure: the AI reader is more reliable on "
                      "unusual layouts and scanned pages."], "found": {f"{b}:{k}": v for (b, k), v in found.items()}}


# --------------------------------------------------------------------------- AI reader

_AI_SYSTEM = (
    "You read financial statements (annual financial statements, management accounts, audited or unaudited) for an accounting firm. "
    "Extract the key figures for EVERY period shown (usually the current and the prior year) into the tool. Rules: "
    "report amounts in the units shown in the document and set scale to units, thousands or millions as stated in the headings; "
    "expense lines are positive numbers; profit before tax, profit after tax and retained earnings keep their sign (a loss is negative); "
    "opex means all operating expenses other than cost of sales, depreciation/amortisation (if shown separately in the statement or notes), "
    "finance costs and income tax; if depreciation is not disclosed separately set dep to 0 and leave it inside opex; "
    "ppe is property, plant and equipment net (include right-of-use assets and investment property); intangibles covers goodwill, "
    "intangibles, investments and other non-current assets; receivables is trade and other receivables; other_ca is prepayments and other current assets; "
    "payables is trade and other payables; other_cl is tax, provisions and other current liabilities; borrowings is loans and lease liabilities; "
    "overdraft is a bank overdraft; share_capital includes share premium; retained is retained earnings plus other reserves. "
    "Never invent a figure: omit a field that is not in the document. Copy the figures exactly as printed."
)


def _tool_schema():
    num = {"type": "number"}
    return {
        "name": "record_financial_statements",
        "description": "Record the key figures of the financial statements, one entry per period shown.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string"}, "currency": {"type": "string"},
                "scale": {"type": "string", "enum": ["units", "thousands", "millions"]},
                "periods": {"type": "array", "items": {"type": "object", "properties": {
                    "label": {"type": "string", "description": "e.g. FY2025 or Year ended 31 December 2025"},
                    "is": {"type": "object", "properties": {k: num for k in IS_KEYS}},
                    "bs": {"type": "object", "properties": {k: num for k in BS_KEYS}},
                }, "required": ["label"]}},
                "notes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["periods"],
        },
    }


def parse_ai_result(raw):
    """Clean a tool-call payload into an extraction. Raises ValueError when it holds no figures."""
    periods = []
    for p in (raw or {}).get("periods", [])[:6]:
        label = str(p.get("label") or "").strip()[:40] or f"Period {len(periods) + 1}"
        is_ = {k: M.f(v) for k, v in (p.get("is") or {}).items() if k in IS_KEYS and v is not None}
        bs = {k: M.f(v) for k, v in (p.get("bs") or {}).items() if k in BS_KEYS and v is not None}
        for k in list(is_):
            if k not in SIGNED:
                is_[k] = abs(is_[k])
        for k in list(bs):
            if k not in SIGNED:
                bs[k] = abs(bs[k])
        if is_ or bs:
            periods.append({"label": label, "is": is_, "bs": bs})
    if not periods:
        raise ValueError("The reader did not find any figures.")
    return {"method": "ai", "entity": str(raw.get("entity") or "")[:200], "currency": (str(raw.get("currency") or "").upper()[:6] or None),
            "scale": SCALES.get(raw.get("scale"), 1), "periods": periods, "notes": [str(n)[:300] for n in (raw.get("notes") or [])][:8]}


def ai_available():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def ai_extract(text=None, page_images=None):
    """Ask Claude to read the statements. Returns (extraction, error). Never raises."""
    if not ai_available():
        return None, "ANTHROPIC_API_KEY is not set"
    try:
        import base64
        import entity_extraction as EX
        content = []
        if page_images:
            for img in page_images:
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(img).decode("ascii")}})
            content.append({"type": "text", "text": "Extract the key figures from the financial statements shown in the page image(s) above."})
        else:
            content.append({"type": "text", "text": "Extract the key figures from these financial statements:\n\n" + (text or "")[:90000]})
        client = EX._client()
        resp = client.messages.create(model=EX.DEFAULT_MODEL, max_tokens=4096, system=_AI_SYSTEM, tools=[_tool_schema()],
                                      tool_choice={"type": "tool", "name": "record_financial_statements"},
                                      messages=[{"role": "user", "content": content}])
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "record_financial_statements":
                return parse_ai_result(block.input or {}), None
        return None, "The reader returned no structured result."
    except Exception as exc:   # a failed AI call must never take the page down
        return None, str(exc)[:300]


# --------------------------------------------------------------------------- reading files

def file_to_text(path, filename):
    """(text, page_images_or_None, error). Images are only produced for PDFs with no text layer (scans)."""
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    if ext in ("txt", "csv"):
        with open(path, "rb") as fh:
            raw = fh.read().decode("utf-8-sig", errors="replace")
        if ext == "csv":
            import csv, io
            raw = "\n".join(" | ".join(r) for r in csv.reader(io.StringIO(raw)))
        return raw, None, None
    import file_text_extraction as FX
    text, status, _ = FX.extract_text_for_file(path, ext)
    if status == "extracted":
        return text, None, None
    if ext == "pdf" and status in ("no_text_found", "error"):
        try:
            import entity_extraction as EX
            imgs = EX.render_pdf_pages_to_images(path, max_pages=12)
            if imgs:
                return "", imgs, None
        except Exception:
            pass
    return "", None, {"no_text_found": "That file has no readable text (it may be a scan).", "unsupported_format": "That file type cannot be read: use PDF, Word (.docx), Excel (.xlsx) or CSV.",
                      "error": "That file could not be opened."}.get(status, "That file could not be read.")


def extract(text=None, page_images=None):
    """Read statements with AI when available, else the built-in reader. Returns (extraction, message).
    Raises ValueError when nothing could be read at all."""
    msg = None
    if ai_available():
        res, err = ai_extract(text=text, page_images=page_images)
        if res:
            return res, None
        msg = f"The AI reader could not be used ({err}); the built-in reader was used instead."
    if not text:
        raise ValueError("This document has no text layer and the AI reader is not available, so it cannot be read. Upload a text-based PDF, Word or Excel version.")
    res = builtin_extract(text)
    if msg:
        res["notes"].insert(0, msg)
    return res, msg


# --------------------------------------------------------------------------- checks

def period_checks(p):
    """Plain-English reconciliation flags for the review page."""
    out = []
    i, b = p.get("is") or {}, p.get("bs") or {}
    g = lambda d, k: d.get(k)
    if all(k in i for k in ("revenue", "cogs", "pat")):
        calc = i["revenue"] - i["cogs"] + i.get("other_income", 0) - i.get("opex", 0) - i.get("dep", 0) - i.get("finance_costs", 0) + i.get("interest_income", 0) - i.get("tax", 0)
        if abs(calc - i["pat"]) > max(1.0, 0.005 * abs(i["pat"] or 1)):
            out.append(f"Profit after tax recomputed from the lines is {calc:,.0f}, but the statements report {i['pat']:,.0f}: check expenses or sign.")
        else:
            out.append("Income statement lines add up to the reported profit after tax.")
    if "total_assets" in b:
        assets = sum(b.get(k, 0) for k in ("ppe", "intangibles", "inventory", "receivables", "other_ca", "cash"))
        if abs(assets - b["total_assets"]) > max(1.0, 0.005 * b["total_assets"]):
            out.append(f"Assets found add up to {assets:,.0f} against reported total assets of {b['total_assets']:,.0f}: a line is missing or misclassified.")
        else:
            out.append("Asset lines add up to total assets.")
    eq = b.get("total_equity")
    if eq is None and "share_capital" in b and "retained" in b:
        eq = b["share_capital"] + b["retained"]
    if eq is not None and "total_assets" in b:
        liab = sum(b.get(k, 0) for k in ("overdraft", "payables", "other_cl", "borrowings"))
        gap = b["total_assets"] - eq - liab
        if abs(gap) > max(1.0, 0.005 * b["total_assets"]):
            out.append(f"Balance sheet is out by {gap:,.0f} (assets less equity less the liabilities found): check the liability lines.")
        else:
            out.append("Balance sheet balances (assets = equity + liabilities).")
    return out


# --------------------------------------------------------------------------- feeding the models

def scaled(period, mult):
    return {"label": period["label"], "is": {k: v * mult for k, v in (period.get("is") or {}).items()},
            "bs": {k: v * mult for k, v in (period.get("bs") or {}).items()}}


def to_pack_prior(period, structure, mult=1.0):
    """The prior-year accounts of a management pack: ({pl}, {bs}) in the pack's line keys."""
    p = scaled(period, mult)
    i, b = p["is"], p["bs"]
    pl = {k: 0.0 for k in M.pl_keys(structure)}
    pl[structure["revenue"][0]["key"]] = i.get("revenue", 0.0)
    pl["cogs"] = i.get("cogs", 0.0)
    pl["oth_inc"] = i.get("other_income", 0.0)
    pl[structure["opex"][0]["key"]] = i.get("opex", 0.0)
    pl["dep"] = i.get("dep", 0.0)
    pl["int_inc"] = i.get("interest_income", 0.0)
    pl["int_exp"] = i.get("finance_costs", 0.0)
    pl["tax"] = i.get("tax", 0.0)
    pl["div"] = i.get("dividends", 0.0)
    bs = {k: 0.0 for k in M.BS_KEYS}
    bs["ppe"] = b.get("ppe", 0.0) + b.get("intangibles", 0.0)
    bs["inventory"] = b.get("inventory", 0.0)
    bs["receivables"] = b.get("receivables", 0.0)
    bs["other_assets"] = b.get("other_ca", 0.0)
    bs["cash"] = b.get("cash", 0.0)
    bs["overdraft"] = b.get("overdraft", 0.0)
    bs["payables"] = b.get("payables", 0.0)
    bs["other_liabilities"] = b.get("other_cl", 0.0)
    bs["borrowings"] = b.get("borrowings", 0.0)
    bs["share_capital"] = b.get("share_capital", 0.0)
    if "retained" in b:
        bs["retained"] = b["retained"]
    elif "total_equity" in b:
        bs["retained"] = b["total_equity"] - bs["share_capital"]
    else:
        t = M.bs_total(dict(bs, retained=0.0))
        bs["retained"] = t["assets"] - t["liabilities"] - bs["share_capital"]
    return pl, bs


def to_model_history(periods, mult=1.0):
    """Historical analysis input of a forecast model (cf_tools.HIST_IS / HIST_BS), oldest year first."""
    cols = [scaled(p, mult) for p in _oldest_first(periods)]
    out = {"years": [c["label"] for c in cols], "is": {k: [] for k in ("revenue", "cogs", "opex", "dep", "fin_cost", "tax")},
           "bs": {k: [] for k in ("ppe", "inventory", "receivables", "other_ca", "cash", "payables", "other_cl", "borrowings", "equity")}}
    for c in cols:
        i, b = c["is"], c["bs"]
        out["is"]["revenue"].append(i.get("revenue", 0.0))
        out["is"]["cogs"].append(i.get("cogs", 0.0))
        out["is"]["opex"].append(i.get("opex", 0.0))
        out["is"]["dep"].append(i.get("dep", 0.0))
        out["is"]["fin_cost"].append(i.get("finance_costs", 0.0) - i.get("interest_income", 0.0))
        out["is"]["tax"].append(i.get("tax", 0.0))
        out["bs"]["ppe"].append(b.get("ppe", 0.0) + b.get("intangibles", 0.0))
        out["bs"]["inventory"].append(b.get("inventory", 0.0))
        out["bs"]["receivables"].append(b.get("receivables", 0.0))
        out["bs"]["other_ca"].append(b.get("other_ca", 0.0))
        out["bs"]["cash"].append(b.get("cash", 0.0))
        out["bs"]["payables"].append(b.get("payables", 0.0))
        out["bs"]["other_cl"].append(b.get("other_cl", 0.0))
        out["bs"]["borrowings"].append(b.get("borrowings", 0.0) + b.get("overdraft", 0.0))
        eq = b.get("total_equity")
        if eq is None:
            eq = b.get("share_capital", 0.0) + b.get("retained", 0.0)
        out["bs"]["equity"].append(eq)
    return out


def _oldest_first(periods):
    """History is stored oldest year first. Order by the year in each label when they all carry one; otherwise assume the
    statements list the current year first (as they normally do) and reverse."""
    yrs = [re.search(r"(?:19|20)\d\d", p.get("label") or "") for p in periods]
    if len(periods) > 1 and all(yrs) and len({y.group(0) for y in yrs}) == len(periods):
        return [p for _, p in sorted(zip([int(y.group(0)) for y in yrs], periods), key=lambda t: t[0])]
    return list(reversed(periods))


def merge_history(existing, new):
    """Add `new` years to an existing history (matching year labels are replaced). Oldest first, at most 6 years."""
    ex_years = list((existing or {}).get("years") or [])
    out = {"years": [], "is": {}, "bs": {}}
    keys_is, keys_bs = list(new["is"]), list(new["bs"])
    cols = {}
    for j, y in enumerate(ex_years):
        cols[y] = ({k: ((existing.get("is") or {}).get(k) or [0.0] * len(ex_years))[j] if j < len((existing.get("is") or {}).get(k) or []) else 0.0 for k in keys_is},
                   {k: ((existing.get("bs") or {}).get(k) or [0.0] * len(ex_years))[j] if j < len((existing.get("bs") or {}).get(k) or []) else 0.0 for k in keys_bs})
    order = [y for y in ex_years if y not in new["years"]]
    for j, y in enumerate(new["years"]):
        cols[y] = ({k: new["is"][k][j] for k in keys_is}, {k: new["bs"][k][j] for k in keys_bs})
    years = (order + [y for y in new["years"] if y not in order])[-6:]
    out["years"] = years
    out["is"] = {k: [cols[y][0][k] for y in years] for k in keys_is}
    out["bs"] = {k: [cols[y][1][k] for y in years] for k in keys_bs}
    return out
