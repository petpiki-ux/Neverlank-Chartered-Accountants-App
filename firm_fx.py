"""Firm Finance > Exchange rates: a register of dated rates (ZWG per 1 USD) that year-ends take their closing rate from.

The financial statements, cash book and tax pages still translate every ZWG item at the single rate stored on the year-end
(FirmYearEnd.zwg_rate). The register fills that rate: a year-end created without a rate takes the latest register rate dated
on or before it, and the year-end page and this page offer "use the register rate" where the two differ."""
from datetime import date, datetime

from flask import render_template, redirect, url_for, request, flash
from flask_login import current_user

from extensions import db
from datetime import timedelta
from models import FirmExchangeRate, FirmFxConfig, FirmYearEnd, INVOICE_CURRENCIES
import rbz_rates
from finance import finance_required, _parse_date, _parse_float
from firm_finance import firmfs_bp, _year_ends

FOREIGN = [c for c in INVOICE_CURRENCIES if c != "USD"]
STALE_DAYS = 31


def register_rate(currency, on):
    """The latest register row for the currency dated on or before `on`, or None."""
    return (FirmExchangeRate.query.filter(FirmExchangeRate.currency == currency, FirmExchangeRate.rate_date <= on)
            .order_by(FirmExchangeRate.rate_date.desc()).first())


def year_end_info(ye):
    """What the register says about a year-end's closing rate: {row, age_days, stale, in_use, differs, status}."""
    row = register_rate("ZWG", ye.period_end)
    in_use = ye.zwg_rate
    info = {"row": row, "in_use": in_use, "age_days": (ye.period_end - row.rate_date).days if row else None}
    info["stale"] = bool(row and info["age_days"] > STALE_DAYS)
    if row is None:
        info["status"] = "none" if not in_use else "typed"
    elif not in_use:
        info["status"] = "unused"
    elif abs(in_use - row.rate) < 1e-9:
        info["status"] = "match"
    else:
        info["status"] = "differs"
    return info


def _parse_when(raw):
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _upsert(currency, day, rate, source, note):
    """Add or replace the rate for a currency on a date. Returns 'added' or 'updated'."""
    row = FirmExchangeRate.query.filter_by(currency=currency, rate_date=day).first()
    if row is None:
        db.session.add(FirmExchangeRate(currency=currency, rate_date=day, rate=rate, source=source, note=note, created_by_id=current_user.id))
        return "added"
    row.rate, row.source, row.note = rate, source or row.source, note or row.note
    return "updated"


@firmfs_bp.route("/exchange-rates")
@finance_required
def exchange_rates():
    auto = maybe_auto_fetch()
    rows = FirmExchangeRate.query.order_by(FirmExchangeRate.rate_date.desc(), FirmExchangeRate.id.desc()).limit(400).all()
    yes = _year_ends()
    return render_template("firmfs/exchange_rates.html", active="fx", ye=None, rates=rows, today=date.today(), currencies=FOREIGN,
                           year_ends=[(y, year_end_info(y)) for y in reversed(yes)], stale_days=STALE_DAYS,
                           cfg=get_config(), bases=BASES, auto=auto, months=list(enumerate(rbz_rates.MONTHS, 1)))


@firmfs_bp.route("/exchange-rates/add", methods=["POST"])
@finance_required
def exchange_rate_add():
    cur = request.form.get("currency")
    day = _parse_date(request.form.get("rate_date"))
    rate = _parse_float(request.form.get("rate"))
    if cur not in FOREIGN or day is None or rate is None or rate <= 0:
        flash("Choose the currency and date, and enter a rate above zero (units of the currency per 1 USD).", "danger")
    else:
        what = _upsert(cur, day, rate, (request.form.get("source") or "").strip()[:120] or None, (request.form.get("note") or "").strip()[:250] or None)
        db.session.commit()
        flash(f"{cur} rate {rate:g} for {day.strftime('%d %b %Y')} {what}.", "success")
    return redirect(url_for("firmfs.exchange_rates"))


@firmfs_bp.route("/exchange-rates/bulk", methods=["POST"])
@finance_required
def exchange_rate_bulk():
    cur = request.form.get("currency")
    source = (request.form.get("source") or "").strip()[:120] or None
    if cur not in FOREIGN:
        flash("Choose the currency.", "danger")
        return redirect(url_for("firmfs.exchange_rates"))
    added = updated = 0
    bad = []
    for n, line in enumerate((request.form.get("lines") or "").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        parts = [p for p in line.replace("\t", ",").replace(";", ",").split(",") if p.strip()]
        if len(parts) == 1 and " " in line:
            parts = line.rsplit(None, 1)
        day = _parse_when(parts[0]) if len(parts) >= 2 else None
        rate = _parse_float(parts[1]) if len(parts) >= 2 else None
        if day is None or rate is None or rate <= 0:
            bad.append(str(n))
            continue
        if _upsert(cur, day, rate, source, None) == "added":
            added += 1
        else:
            updated += 1
    db.session.commit()
    if bad:
        flash(f"Line(s) {', '.join(bad)} were not understood (use: date, rate). The other lines were saved.", "danger")
    if added or updated:
        flash(f"{added} rate(s) added, {updated} updated.", "success")
    return redirect(url_for("firmfs.exchange_rates"))


@firmfs_bp.route("/exchange-rates/<int:rate_id>/delete", methods=["POST"])
@finance_required
def exchange_rate_delete(rate_id):
    row = FirmExchangeRate.query.get_or_404(rate_id)
    db.session.delete(row)
    db.session.commit()
    flash("Rate deleted. Year-ends that already took it keep their stored rate.", "success")
    return redirect(url_for("firmfs.exchange_rates"))


@firmfs_bp.route("/exchange-rates/apply/<int:ye_id>", methods=["POST"])
@finance_required
def exchange_rate_apply(ye_id):
    ye = FirmYearEnd.query.get_or_404(ye_id)
    row = register_rate("ZWG", ye.period_end)
    if row is None:
        flash(f"The register has no ZWG rate dated on or before {ye.period_end.strftime('%d %b %Y')}.", "danger")
    else:
        old = ye.zwg_rate
        ye.zwg_rate = row.rate
        db.session.commit()
        flash(f"Year-end {ye.period_end.strftime('%d %b %Y')} now uses {row.rate:g} ZWG per USD (register rate dated {row.rate_date.strftime('%d %b %Y')})"
              + (f", replacing {old:g}." if old else "."), "success")
    back = request.form.get("back")
    return redirect(url_for("firmfs.year_end", ye_id=ye.id) if back == "year_end" else url_for("firmfs.exchange_rates"))


# ---------------------------------------------------------------- Reserve Bank of Zimbabwe rates

AUTO_EVERY = timedelta(hours=6)
BASES = {"mid": "average (mid)", "bid": "bid", "ask": "ask"}


def get_config():
    cfg = FirmFxConfig.query.first()
    if cfg is None:
        cfg = FirmFxConfig(rbz_basis="mid", auto_fetch=True)
        db.session.add(cfg)
        db.session.commit()
    return cfg


def store_rbz(rec, cfg, overwrite=False):
    """Save one RBZ record in the register. Returns 'added', 'updated' or 'kept' (a rate typed by hand is never replaced
    unless `overwrite`)."""
    basis = cfg.rbz_basis if cfg.rbz_basis in BASES else "mid"
    rate = round(rbz_rates.pick(rec, basis), 6)
    src = f"RBZ interbank {basis}"
    note = f"bid {rec['bid']:g} / ask {rec['ask']:g} / mid {rec['mid']:g}, fetched from rbz.co.zw"
    row = FirmExchangeRate.query.filter_by(currency="ZWG", rate_date=rec["date"]).first()
    if row is None:
        db.session.add(FirmExchangeRate(currency="ZWG", rate_date=rec["date"], rate=rate, source=src, note=note[:250],
                                        created_by_id=getattr(current_user, "id", None) if current_user and current_user.is_authenticated else None))
        return "added"
    if (row.source or "").startswith("RBZ") or overwrite:
        row.rate, row.source, row.note = rate, src, note[:250]
        return "updated"
    return "kept"


def maybe_auto_fetch(today=None):
    """Called when the rates page opens: at most once every six hours, fetch the latest published RBZ rate. Never raises."""
    try:
        cfg = get_config()
        now = datetime.utcnow()
        if not cfg.auto_fetch or (cfg.last_attempt and now - cfg.last_attempt < AUTO_EVERY):
            return None
        today = today or date.today()
        cfg.last_attempt = now
        rec, notes = rbz_rates.fetch_latest(today, max_back=5)
        if rec is None:
            cfg.last_message = ("Automatic fetch: " + "; ".join(notes))[:300]
        else:
            what = store_rbz(rec, cfg)
            cfg.last_message = f"Automatic fetch: {rec['date'].strftime('%d %b %Y')} {rbz_rates.pick(rec, cfg.rbz_basis):g} ({what})"[:300]
        db.session.commit()
        return cfg.last_message
    except Exception as e:                      # the page must open whatever the website does
        db.session.rollback()
        return f"Automatic fetch failed: {type(e).__name__}"


@firmfs_bp.route("/exchange-rates/rbz/latest", methods=["POST"])
@finance_required
def rbz_latest():
    cfg = get_config()
    rec, notes = rbz_rates.fetch_latest(date.today(), max_back=7)
    cfg.last_attempt = datetime.utcnow()
    if rec is None:
        cfg.last_message = ("Manual fetch: " + "; ".join(notes))[:300]
        db.session.commit()
        flash("Could not get a rate from the RBZ website. " + " ".join(notes) + " Enter the rate by hand below if you need it now.", "danger")
        return redirect(url_for("firmfs.exchange_rates"))
    what = store_rbz(rec, cfg, overwrite=bool(request.form.get("overwrite")))
    cfg.last_message = f"Manual fetch: {rec['date'].strftime('%d %b %Y')} ({what})"
    db.session.commit()
    val = rbz_rates.pick(rec, cfg.rbz_basis)
    if what == "kept":
        flash(f"RBZ published {val:g} for {rec['date'].strftime('%d %b %Y')}, but you have already typed a rate for that date, so it was left alone.", "info")
    else:
        flash(f"RBZ interbank {cfg.rbz_basis} rate for {rec['date'].strftime('%d %b %Y')}: {val:g} ZWG per USD ({what}).", "success")
    return redirect(url_for("firmfs.exchange_rates"))


@firmfs_bp.route("/exchange-rates/rbz/month", methods=["POST"])
@finance_required
def rbz_month():
    cfg = get_config()
    year = request.form.get("year", type=int)
    month = request.form.get("month", type=int)
    if not year or not month or not (1 <= month <= 12) or not (2009 <= year <= date.today().year):
        flash("Choose a month and year.", "danger")
        return redirect(url_for("firmfs.exchange_rates"))
    overwrite = bool(request.form.get("overwrite"))
    first = date(year, month, 1)
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    have = {r.rate_date for r in FirmExchangeRate.query.filter(FirmExchangeRate.currency == "ZWG", FirmExchangeRate.rate_date >= first,
                                                               FirmExchangeRate.rate_date <= last).all()}
    res = rbz_rates.fetch_month(year, month, date.today(), skip=set() if overwrite else have, budget=45.0)
    counts = {"added": 0, "updated": 0, "kept": 0}
    for rec in res["found"]:
        counts[store_rbz(rec, cfg, overwrite=overwrite)] += 1
    cfg.last_attempt = datetime.utcnow()
    cfg.last_message = f"Month fetch {MONTH_NAMES[month - 1]} {year}: {counts['added']} added, {counts['updated']} updated"
    db.session.commit()
    flash(f"{MONTH_NAMES[month - 1]} {year}: {counts['added']} rate(s) added, {counts['updated']} updated"
          + (f", {len(have)} day(s) already in the register were skipped" if have and not overwrite else "") + ".", "success" if res["found"] else "info")
    if res["bad"]:
        flash("Not read: " + "; ".join(f"{d.strftime('%d %b')} ({m})" for d, m in res["bad"][:6]) + ". Enter those by hand if needed.", "danger")
    if not res["done"]:
        flash(f"Stopped at {res['stopped_at'].strftime('%d %b %Y')} (time limit or the website stopped answering). Press the button again to carry on from there.", "info")
    return redirect(url_for("firmfs.exchange_rates"))


@firmfs_bp.route("/exchange-rates/rbz/year-end/<int:ye_id>", methods=["POST"])
@finance_required
def rbz_for_year_end(ye_id):
    """Fetch the RBZ rate for a year-end's closing date (or the last working day before it), then put it on the year-end."""
    ye = FirmYearEnd.query.get_or_404(ye_id)
    cfg = get_config()
    if ye.period_end > date.today():
        flash("That year-end is in the future: the RBZ has not published a rate for it yet.", "info")
        return redirect(url_for("firmfs.exchange_rates"))
    rec, notes = rbz_rates.fetch_latest(ye.period_end, max_back=7)
    if rec is None:
        flash("Could not get the closing rate from the RBZ website. " + " ".join(notes), "danger")
        return redirect(url_for("firmfs.exchange_rates"))
    what = store_rbz(rec, cfg)
    db.session.flush()
    row = register_rate("ZWG", ye.period_end)
    ye.zwg_rate = row.rate
    db.session.commit()
    flash(f"Year-end {ye.period_end.strftime('%d %b %Y')} now uses {row.rate:g} ZWG per USD (rate dated {row.rate_date.strftime('%d %b %Y')}, "
          f"{row.source or 'register'}).", "success")
    return redirect(url_for("firmfs.year_end", ye_id=ye.id) if request.form.get("back") == "year_end" else url_for("firmfs.exchange_rates"))


@firmfs_bp.route("/exchange-rates/rbz/settings", methods=["POST"])
@finance_required
def rbz_settings():
    cfg = get_config()
    if request.form.get("basis") in BASES:
        cfg.rbz_basis = request.form.get("basis")
    cfg.auto_fetch = bool(request.form.get("auto_fetch"))
    db.session.commit()
    flash("RBZ fetch settings saved.", "success")
    return redirect(url_for("firmfs.exchange_rates"))


MONTH_NAMES = rbz_rates.MONTHS
