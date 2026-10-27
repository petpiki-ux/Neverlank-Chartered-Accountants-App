"""Import an asset register (Excel or CSV) into an engagement's Asset Register, picking the information out of it by itself.

The file does not have to be laid out any particular way. The importer:
* finds the header row (the row among the first 40 whose cells best match the names a register uses),
* matches each column to a field by its heading (code, description, class, date acquired, cost, opening cost, additions, accumulated
  depreciation, depreciation charge, net book value, tax value, date sold, proceeds, ...) - the preview lets you change any match,
* uses a heading row of text on its own ("MOTOR VEHICLES") as the class of the rows under it when there is no class column,
* leaves out blank rows, totals and sub-totals,
* reads figures written as 1,234.50, (500), $1 200, ZWG 5 000 or - ; and dates as real dates, Excel serial numbers, 2023-03-15, 15/03/2023,
  15 Mar 2023,
* sorts each asset into OPENING BALANCE (acquired before the period, or no date), ADDITION (acquired in the period) or DISPOSAL (sold in the
  period - it is entered as an opening balance or addition and then disposed of), working out accumulated depreciation from cost less net
  book value when the file gives only that,
* skips assets already in the register (same code, or same description and cost) so importing twice does not double up.

The preview shows exactly what will happen to every row before anything is saved.
"""
import csv
import io
import os
import re
import uuid
from datetime import date, datetime, timedelta

from flask import Blueprint, redirect, url_for, request, flash, render_template, abort, current_app
from flask_login import login_required

from extensions import db
from models import PPEAsset, PPEAssetClass
import asset_schedules as asched

assetimp_bp = Blueprint("assetimp", __name__, url_prefix="/engagements")

MAX_ROWS = 2000
MAX_BYTES = 8 * 1024 * 1024

# field -> (label, header phrases). Phrases are matched on the heading with spaces and punctuation removed; an exact match scores 3, a
# heading that contains a phrase 2. The more specific fields come first so "accumulated depreciation b/f" is not read as "depreciation".
FIELDS = [
    ("code", "Asset code", ["assetno", "assetnumber", "assetcode", "assetid", "assetref", "fano", "fanumber", "tagno", "tagnumber", "tag", "code", "reference", "ref", "serialno", "number", "no"]),
    ("description", "Description", ["assetdescription", "description", "assetname", "nameofasset", "particulars", "details", "item", "asset", "name"]),
    ("class", "Class", ["assetclass", "assetcategory", "assetgroup", "assettype", "category", "class", "classification", "group", "type"]),
    ("date", "Date acquired", ["dateacquired", "acquisitiondate", "dateofacquisition", "purchasedate", "dateofpurchase", "datepurchased", "datebought", "dateinservice", "acquired", "purchased", "dateadded", "date"]),
    ("cost_bf", "Cost b/f", ["costbf", "costbroughtforward", "openingcost", "costopening", "costatstart", "costbalancebf", "balancebf", "costbfwd", "costatbeginning", "costbeginning"]),
    ("additions", "Additions", ["additions", "addition", "purchasesduringtheyear", "costadditions", "additionsatcost", "acquisitions"]),
    ("disp_cost", "Cost of disposals", ["disposalscost", "costofdisposals", "costdisposed", "disposalcost", "costdisposals", "disposals", "disposal"]),
    ("cost", "Cost", ["costprice", "originalcost", "purchaseprice", "cost", "acquisitioncost", "purchasecost", "grosscost", "grosscarryingamount", "gross", "amountpaid", "value", "price"]),
    ("acc_dep", "Accumulated depreciation b/f", ["accumulateddepreciationbf", "accumulateddepreciationbroughtforward", "openingaccumulateddepreciation", "accdepbf", "accdepopening", "accumulateddepreciation", "accumulateddepn", "accdep", "accumdep", "accumulateddep", "provisionfordepreciation", "depreciationtodate", "totaldepreciation", "depreciationbf", "depnbf"]),
    ("charge", "Depreciation charge", ["depreciationchargefortheyear", "depreciationfortheyear", "currentyeardepreciation", "depreciationcharge", "chargefortheyear", "depreciationcurrentyear", "annualdepreciation", "depreciationexpense", "yeardepreciation", "depnfortheyear", "depnthisyear", "charge", "depreciation", "depn"]),
    ("nbv", "Net book value", ["netbookvalue", "nbv", "carryingamount", "carryingvalue", "writtendownvalue", "wdv", "bookvalue", "netbook"]),
    ("tax_value", "Tax value b/f", ["taxvaluebf", "taxwrittendownvalue", "taxwdv", "taxvalue", "taxbase", "taxvaluebroughtforward", "taxvalueopening", "tvbf", "taxnbv", "wearandtearvalue", "ztaxvalue"]),
    ("disp_date", "Date sold", ["datesold", "disposaldate", "dateofdisposal", "datedisposed", "soldon", "datesoldscrapped", "dateofsale"]),
    ("proceeds", "Sale proceeds", ["saleproceeds", "disposalproceeds", "proceedsofsale", "proceeds", "salesprice", "sellingprice", "soldfor", "saleprice", "proceedsondisposal"]),
]
FIELD_LABEL = {k: lbl for k, lbl, _ in FIELDS}
FIELD_KEYS = [k for k, _, _ in FIELDS]
TOTAL_WORDS = re.compile(r"^(grand\s+)?(sub\s*-?\s*)?total|^total\b|\btotals?\b\s*$|^balance\b|^carried\b|^brought\b", re.I)
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%y", "%b %d, %Y", "%Y/%m/%d", "%d %b %y", "%m/%d/%Y")


# ---------------------------------------------------------------------------------------------------- reading cells

def norm(text):
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def to_num(v):
    """float, or None when the cell is not a figure. Blank, '-' and '0' are 0.0 only through to_num_or0."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s in ("", "-", "--", "—", "n/a", "N/A", "nil", "Nil"):
        return None
    neg = (s.startswith("(") and s.endswith(")")) or s.startswith("-") or s.endswith("-")
    s = re.sub(r"(?i)(us\$|zwg|zwl|zig|usd|rtgs|bond|\$|z\$|r|£|€)", "", s)
    s = s.replace(",", "").replace(" ", "").replace(" ", "").strip("()-+")
    try:
        x = float(s)
    except ValueError:
        return None
    return -abs(x) if neg else x


def to_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 20000 < float(v) < 80000:                       # an Excel serial day number (1954 - 2118)
            return (datetime(1899, 12, 30) + timedelta(days=int(v))).date()
        return None
    s = str(v).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{5}(\.\d+)?", s) and 20000 < float(s) < 80000:
        return (datetime(1899, 12, 30) + timedelta(days=int(float(s)))).date()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def read_rows(data, filename):
    """The sheet as a list of rows (lists of cell values). Excel .xlsx: the sheet that looks most like a register; CSV/TXT: comma, semicolon
    or tab separated."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        best, best_score = None, -1
        for ws in wb.worksheets:
            rows = [list(r) for r in ws.iter_rows(values_only=True)][:MAX_ROWS + 60]
            score = header_score(rows)[1]
            if score > best_score or best is None:
                best, best_score = rows, score
        return best or []
    if ext in (".csv", ".txt", ".tsv"):
        text = data.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        return [row for row in csv.reader(io.StringIO(text), dialect)]
    raise ValueError("Please upload an Excel (.xlsx) or CSV file. An old .xls file can be opened in Excel and saved as .xlsx.")


# ---------------------------------------------------------------------------------------------------- finding the columns

def _score_heading(h):
    """(field, score) best match for one heading, or (None, 0)."""
    n = norm(h)
    if not n or len(n) > 60:
        return None, 0
    best, best_score = None, 0
    for key, _, phrases in FIELDS:
        for ph in phrases:
            if n == ph:
                sc = 3 + min(len(ph), 12) / 100.0
            elif len(ph) >= 5 and ph in n:
                sc = 2 + min(len(ph), 12) / 100.0
            else:
                continue
            if sc > best_score:
                best, best_score = key, sc
    return best, best_score


def header_score(rows):
    """(row index, how many distinct fields its cells name) for the best-looking header row among the first 40."""
    best_i, best = 0, 0
    for i, row in enumerate(rows[:40]):
        fields = set()
        for c in row:
            if isinstance(c, str):
                f, sc = _score_heading(c)
                if f:
                    fields.add(f)
        if len(fields) > best:
            best_i, best = i, len(fields)
    return best_i, best


def detect_columns(header):
    """{field: column index}: each column goes to its best-matching field, each field to its best column (a field with two candidates
    keeps the better one; the other column stays unused)."""
    cands = []
    for i, h in enumerate(header):
        f, sc = _score_heading(h) if isinstance(h, str) else (None, 0)
        if f:
            cands.append((sc, -i, f, i))
    cands.sort(reverse=True)
    mapping, used = {}, set()
    for sc, _, f, i in cands:
        if f in mapping or i in used:
            continue
        mapping[f] = i
        used.add(i)
    return mapping


# ---------------------------------------------------------------------------------------------------- reading the rows

def _abs(x):
    """A register shows depreciation and deductions in brackets or with a minus sign; every figure here is a positive amount."""
    return None if x is None else abs(x)


def _cell(row, mapping, key):
    i = mapping.get(key)
    return row[i] if i is not None and i < len(row) else None


def _text(v):
    return " ".join(str(v).split()) if v is not None and str(v).strip() else ""


def parse_register(rows, period_start, period_end, mapping=None, header_row=None, default_date=None):
    """Read a register. Returns {"header_row", "header", "mapping", "items": [...], "notes": [...]}. Each item is a dict:
    kind ('opening' | 'addition' | 'skip'), code, description, class_name, date, cost, acc_dep, charge, tax_value, nbv, disposal (None or a
    dict with date, proceeds, cost, acc_dep), notes [...], row (the 1-based row number in the sheet)."""
    if header_row is None:
        header_row, _ = header_score(rows)
    header = list(rows[header_row]) if rows and header_row < len(rows) else []
    auto = detect_columns(header)
    mapping = dict(auto if mapping is None else mapping)
    notes, items = [], []
    if "description" not in mapping:
        body = rows[header_row + 1: header_row + 200]
        used = set(mapping.values())
        if "class" in mapping:
            mapping["description"] = mapping["class"]            # a register kept by class: the class is the line
            notes.append("The register is by class, so each class is one line.")
        else:
            for i in range(len(header)):
                if i in used:
                    continue
                vals = [r[i] for r in body if i < len(r) and r[i] not in (None, "")]
                if vals and sum(1 for v in vals if to_num(v) is None and to_date(v) is None) >= 0.6 * len(vals):
                    mapping["description"] = i
                    notes.append("No column is headed as a description, so the first column of text was used.")
                    break
    has_movement = "cost_bf" in mapping or "additions" in mapping
    current_class = ""
    for ri in range(header_row + 1, min(len(rows), header_row + 1 + MAX_ROWS)):
        row = rows[ri]
        if not any(c not in (None, "") and str(c).strip() for c in row):
            continue
        desc = _text(_cell(row, mapping, "description"))
        code = _text(_cell(row, mapping, "code"))
        nums = {k: _abs(to_num(_cell(row, mapping, k))) for k in ("cost", "cost_bf", "additions", "disp_cost", "acc_dep", "charge", "nbv", "tax_value", "proceeds")}
        if nums["tax_value"] is None and str(_cell(row, mapping, "tax_value") or "").strip().lower() in ("-", "nil", "\u2013", "\u2014"):
            nums["tax_value"] = 0.0                    # a dash or 'nil' in the tax value column is a nil tax value, not a blank
        d_acq = to_date(_cell(row, mapping, "date"))
        d_sold = to_date(_cell(row, mapping, "disp_date"))
        has_money = any(v not in (None, 0.0) for v in nums.values())
        item = {"row": ri + 1, "code": code, "description": desc, "class_name": _text(_cell(row, mapping, "class")) or current_class,
                "date": d_acq, "notes": [], "kind": "opening", "disposal": None}
        label = desc or code
        if label and TOTAL_WORDS.search(label):
            item.update(kind="skip", notes=["A total row."])
            items.append(item)
            continue
        if label and not has_money and not d_acq and not d_sold and "class" not in mapping:
            current_class = label                                # a heading line: the class of the rows under it
            continue
        if not label:
            if has_money:
                item.update(kind="skip", notes=["No description (probably a total)."])
                items.append(item)
            continue
        if not has_money:
            item.update(kind="skip", notes=["No figures on this row."])
            items.append(item)
            continue
        # --- what the row is: with a cost column it is one asset; with cost b/f and additions columns (a movement register) the cost b/f
        # is an opening balance and the additions are bought this year, so one row can give both.
        movement_only = has_movement and "cost" not in mapping
        add_cost = nums["additions"] or 0.0
        parts = []                                     # (kind, cost, date)
        if movement_only:
            if (nums["cost_bf"] or 0.0) != 0:
                parts.append(("opening", nums["cost_bf"], d_acq if (d_acq and (not period_start or d_acq < period_start)) else None))
            if add_cost != 0:
                dd = d_acq if (d_acq and period_start and period_end and period_start <= d_acq <= period_end) else default_date
                parts.append(("addition", add_cost, dd))
        else:
            cost = nums["cost"]
            if cost is None and nums["nbv"] is not None and nums["acc_dep"] is not None:
                cost = nums["nbv"] + nums["acc_dep"]
                item["notes"].append("Cost worked out as net book value plus accumulated depreciation.")
            if cost is None:
                item.update(kind="skip", notes=["No cost on this row."])
                items.append(item)
                continue
            if d_acq and period_end and d_acq > period_end:
                item.update(kind="skip", notes=["Acquired after the period end."])
                items.append(item)
                continue
            in_year = bool(d_acq and period_start and period_end and period_start <= d_acq <= period_end)
            parts.append(("addition" if in_year else "opening", cost, d_acq))
        if not parts:
            item.update(kind="skip", notes=["No cost on this row."])
            items.append(item)
            continue
        # --- accumulated depreciation of an opening asset
        acc = nums["acc_dep"]
        derived = False
        if acc is None and nums["nbv"] is not None and parts[0][0] == "opening":
            acc, derived = parts[0][1] - nums["nbv"], True
        sold = None
        if d_sold is not None or nums["proceeds"] is not None or (nums["disp_cost"] or 0.0) != 0:
            if d_sold is None:
                d_sold = default_date or period_end
                item["notes"].append("Sold, but no date sold - dated %s." % (d_sold.strftime("%d %b %Y") if d_sold else "blank"))
            if period_start and d_sold and d_sold < period_start:
                item.update(kind="skip", notes=["Sold before the period started - it is not in the opening balances."])
                items.append(item)
                continue
            if period_end and d_sold and d_sold > period_end:
                item["notes"].append("Date sold is after the period end - left as still held.")
            else:
                sold = {"date": d_sold, "proceeds": nums["proceeds"] if nums["proceeds"] is not None else 0.0, "cost": nums["disp_cost"] or None}
        sold_at = next((n for n, p_ in enumerate(parts) if p_[0] == "opening"), len(parts) - 1)
        for n, (kind, cost, dt) in enumerate(parts):
            it = dict(item, notes=list(item["notes"]), kind=kind, cost=cost, date=dt, nbv=nums["nbv"], tax_value=nums["tax_value"] if kind == "opening" else None,
                      acc_dep=(acc or 0.0) if kind == "opening" else 0.0, charge=(nums["charge"] or 0.0) if n == 0 else 0.0,
                      disposal=sold if n == sold_at else None)
            d_cost = (sold or {}).get("cost") if n == sold_at else None
            if it["disposal"] and d_cost and 0 < d_cost < cost:
                # only part of the line was sold (a register kept by class): the line is split into what is still held and what was sold,
                # the depreciation and tax value shared out in proportion to cost, so the tax schedule treats each correctly
                share = d_cost / cost
                held = dict(it, cost=cost - d_cost, acc_dep=it["acc_dep"] * (1 - share), charge=it["charge"] * (1 - share), disposal=None,
                            tax_value=None if it["tax_value"] is None else it["tax_value"] * (1 - share), notes=list(it["notes"]))
                gone = dict(it, cost=d_cost, acc_dep=it["acc_dep"] * share, charge=it["charge"] * share,
                            tax_value=None if it["tax_value"] is None else it["tax_value"] * share, description=f"{item['description']} - sold"[:200],
                            code="", notes=list(it["notes"]) + ["Part of the line was sold: split off in proportion to cost."])
                held["notes"].append("Part of the line was sold: the rest is shown here.")
                if kind == "opening" and dt is None:
                    held["notes"].append("No date acquired: taken as an opening balance.")
                items.append(held)
                items.append(gone)
                continue
            if kind == "opening" and derived:
                it["notes"].append("Accumulated depreciation worked out as cost less net book value.")
            if kind == "opening" and dt is None:
                it["notes"].append("No date acquired: taken as an opening balance.")
            if kind == "addition" and acc:
                it["notes"].append("Accumulated depreciation ignored: an asset bought this year has none brought forward.")
            if kind == "addition" and dt is default_date and movement_only:
                it["notes"].append("Addition with no date - dated %s (change it afterwards if needed)." % (default_date.strftime("%d %b %Y") if default_date else "blank"))
            if len(parts) > 1 and kind == "addition":
                it["description"] = f"{item['description']} - additions"[:200]
            items.append(it)
    if not any(i["kind"] != "skip" for i in items):
        notes.append("No asset rows could be read. Check the header row and the columns below.")
    if "cost" not in mapping and "cost_bf" not in mapping and "additions" not in mapping:
        notes.append("No cost column was found - choose one below.")
    return {"header_row": header_row, "header": header, "mapping": mapping, "auto_mapping": auto, "items": items, "notes": notes}


# ---------------------------------------------------------------------------------------------------- saving

def _existing_keys(engagement_id):
    codes, desc_cost = set(), set()
    for a in PPEAsset.query.filter_by(engagement_id=engagement_id).all():
        if a.asset_code:
            codes.add(a.asset_code.strip().lower())
        desc_cost.add((" ".join((a.description or "").lower().split()), round(a.cost or 0.0, 2)))
    return codes, desc_cost


def mark_duplicates(items, engagement_id):
    codes, desc_cost = _existing_keys(engagement_id)
    for it in items:
        if it["kind"] == "skip":
            continue
        key = (" ".join(it["description"].lower().split()), round(it["cost"] or 0.0, 2))
        if (it["code"] and it["code"].lower() in codes) or key in desc_cost:
            it["kind"] = "skip"
            it["notes"].append("Already in the Asset Register.")
    return items


def save_items(engagement, items):
    """Create the register rows. Returns (opening, additions, disposals)."""
    n_open = n_add = n_disp = 0
    order = PPEAsset.query.filter_by(engagement_id=engagement.id).count()
    for it in items:
        if it["kind"] == "skip":
            continue
        cls = asched._class_by_name(engagement.id, it["class_name"]) if it["class_name"] else None
        a = PPEAsset(engagement_id=engagement.id, asset_class_id=cls.id if cls else None, asset_code=(it["code"] or None) and it["code"][:40],
                     description=it["description"][:200], date_acquired=it["date"], cost=it["cost"] or 0.0,
                     opening_accumulated_depreciation=it["acc_dep"] or 0.0, current_year_depreciation=it["charge"] or 0.0,
                     opening_tax_value=it.get("tax_value") if it["kind"] == "opening" else None, order=order)
        order += 1
        db.session.add(a)
        db.session.flush()
        if it["kind"] == "addition":
            n_add += 1
        else:
            n_open += 1
        d = it.get("disposal")
        if d:
            asched.apply_disposal(a, d["date"], d["proceeds"], d["cost"], None)
            n_disp += 1
    db.session.commit()
    return n_open, n_add, n_disp


# ---------------------------------------------------------------------------------------------------- pages

def _temp_path(engagement_id, token, ext):
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        abort(404)
    return os.path.join(current_app.config["UPLOAD_FOLDER"], f"assetimp_{engagement_id}_{token}{ext}")


def _mapping_from_form(form):
    m = {}
    for k in FIELD_KEYS:
        v = form.get(f"col_{k}")
        if v not in (None, "") and str(v).isdigit():
            m[k] = int(v)
    return m


def _preview(engagement, token, ext, filename, form=None):
    ps, pe, as_at = asched.bounds(engagement)
    path = _temp_path(engagement.id, token, ext)
    if not os.path.exists(path):
        flash("That upload has expired - please choose the file again.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="asset_register") + "#opening")
    with open(path, "rb") as f:
        data = f.read()
    try:
        rows = read_rows(data, filename + ext)
    except Exception as e:                                            # a corrupt or unreadable file
        flash(f"The file could not be read: {e}", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement.id, tab="asset_register") + "#opening")
    mapping = _mapping_from_form(form) if form is not None and form.get("remap") else None
    header_row = max(int(form["header_row_no"]) - 1, 0) if form is not None and (form.get("header_row_no") or "").isdigit() and form.get("remap") else None
    default_date = asched._date(form.get("default_date")) if form is not None and form.get("default_date") else pe
    res = parse_register(rows, ps, pe, mapping=mapping, header_row=header_row, default_date=default_date)
    mark_duplicates(res["items"], engagement.id)
    counts = {k: sum(1 for i in res["items"] if i["kind"] == k) for k in ("opening", "addition", "skip")}
    counts["disposal"] = sum(1 for i in res["items"] if i.get("disposal") and i["kind"] != "skip")
    return render_template(
        "engagements/asset_import_preview.html", engagement=engagement, token=token, ext=ext, filename=filename, res=res, counts=counts,
        fields=[(k, FIELD_LABEL[k]) for k in FIELD_KEYS], period_start=ps, period_end=pe, as_at=as_at, default_date=default_date,
        total_rows=len(rows))


@assetimp_bp.route("/<int:engagement_id>/assets/import/upload", methods=["POST"])
@login_required
def upload(engagement_id):
    engagement = asched._engagement(engagement_id)
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose the asset register file first.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="asset_register") + "#opening")
    data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        flash("That file is too large (8 MB limit).", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="asset_register") + "#opening")
    ext = os.path.splitext(f.filename)[1].lower()
    try:
        read_rows(data, f.filename)
    except Exception as e:
        flash(str(e) if isinstance(e, ValueError) else f"The file could not be read: {e}", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="asset_register") + "#opening")
    token = uuid.uuid4().hex
    with open(_temp_path(engagement.id, token, ext), "wb") as out:
        out.write(data)
    return _preview(engagement, token, ext, os.path.splitext(f.filename)[0])


@assetimp_bp.route("/<int:engagement_id>/assets/import/<token>/preview", methods=["POST"])
@login_required
def preview(engagement_id, token):
    engagement = asched._engagement(engagement_id)
    return _preview(engagement, token, request.form.get("ext", ""), request.form.get("filename", ""), request.form)


@assetimp_bp.route("/<int:engagement_id>/assets/import/<token>/confirm", methods=["POST"])
@login_required
def confirm(engagement_id, token):
    engagement = asched._engagement(engagement_id)
    ext, filename = request.form.get("ext", ""), request.form.get("filename", "")
    path = _temp_path(engagement.id, token, ext)
    if not os.path.exists(path):
        flash("That upload has expired - please choose the file again.", "danger")
        return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="asset_register") + "#opening")
    ps, pe, _ = asched.bounds(engagement)
    with open(path, "rb") as fh:
        rows = read_rows(fh.read(), filename + ext)
    mapping = _mapping_from_form(request.form)
    header_row = int(request.form["header_row"]) if (request.form.get("header_row") or "").isdigit() else None
    default_date = asched._date(request.form.get("default_date")) or pe
    res = parse_register(rows, ps, pe, mapping=mapping or None, header_row=header_row, default_date=default_date)
    mark_duplicates(res["items"], engagement.id)
    skip_rows = {int(x) for x in request.form.getlist("skip_row") if x.isdigit()}
    for it in res["items"]:
        if it["row"] in skip_rows:
            it["kind"] = "skip"
    n_open, n_add, n_disp = save_items(engagement, res["items"])
    try:
        os.remove(path)
    except OSError:
        pass
    if n_open + n_add == 0:
        flash("Nothing was imported - every row was skipped.", "danger")
    else:
        flash(f"Asset register imported: {n_open} opening balance(s), {n_add} addition(s), {n_disp} disposal(s). Check the tax value brought forward on each opening asset.", "success")
    return redirect(url_for("engagements.view_engagement", engagement_id=engagement_id, tab="asset_register") + "#opening")
