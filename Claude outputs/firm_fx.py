"""Firm Finance > Exchange rates: a register of dated rates (ZWG per 1 USD) that year-ends take their closing rate from.

The financial statements, cash book and tax pages still translate every ZWG item at the single rate stored on the year-end
(FirmYearEnd.zwg_rate). The register fills that rate: a year-end created without a rate takes the latest register rate dated
on or before it, and the year-end page and this page offer "use the register rate" where the two differ."""
from datetime import date, datetime

from flask import render_template, redirect, url_for, request, flash
from flask_login import current_user

from extensions import db
import threading
from datetime import timedelta
from models import FirmExchangeRate, FirmFxConfig, FirmFxMiss, FirmYearEnd, INVOICE_CURRENCIES, user_has_permission
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
                           cfg=get_config(), bases=BASES, auto=auto,
                           misses=FirmFxMiss.query.filter(FirmFxMiss.rate_date >= date.today() - timedelta(days=120)).order_by(FirmFxMiss.rate_date.desc()).limit(40).all(), months=list(enumerate(rbz_rates.MONTHS, 1)))


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


def _who():
    """The signed-in user's id, or None when running in the background."""
    try:
        return current_user.id if current_user and current_user.is_authenticated else None
    except Exception:
        return None


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
                                        created_by_id=_who()))
        return "added"
    if (row.source or "").startswith("RBZ") or overwrite:
        row.rate, row.source, row.note = rate, src, note[:250]
        return "updated"
    return "kept"


CATCH_UP_DAYS = 45             # how far back the automatic catch-up looks for missing working days
SETTLED_AFTER = 3              # a day the RBZ has no file for is asked about again only for this many days
_run_lock = threading.Lock()


def _note_miss(day, why):
    row = FirmFxMiss.query.filter_by(currency="ZWG", rate_date=day).first()
    if row is None:
        db.session.add(FirmFxMiss(currency="ZWG", rate_date=day, reason=(why or "")[:250], checked_at=datetime.utcnow()))
    else:
        row.reason, row.checked_at = (why or "")[:250], datetime.utcnow()


def missing_days(today, window=CATCH_UP_DAYS, force=False):
    """Working days with no rate in the register that are worth asking the RBZ about: the last `window` days, plus the days around
    each year-end that has no rate close to its closing date. Days the RBZ has already said it has no file for are skipped once
    they are more than a few days old."""
    have = {r.rate_date for r in FirmExchangeRate.query.filter_by(currency="ZWG").all()}
    miss = {m.rate_date: m for m in FirmFxMiss.query.filter_by(currency="ZWG").all()}
    start = max(today - timedelta(days=window), rbz_rates.ZWG_START)
    want = set(rbz_rates.working_days(start, today))
    for ye in _year_ends():
        if ye.period_end > today or ye.period_end < rbz_rates.ZWG_START:
            continue
        row = register_rate("ZWG", ye.period_end)
        if row is None or (ye.period_end - row.rate_date).days > 7:
            want |= set(rbz_rates.working_days(max(ye.period_end - timedelta(days=10), rbz_rates.ZWG_START), ye.period_end))
    out = []
    for d in sorted(want - have, reverse=True):
        m = miss.get(d)
        if m is not None and not force:
            settled = (today - d).days > SETTLED_AFTER
            recent = m.checked_at and datetime.utcnow() - m.checked_at < AUTO_EVERY
            if settled or recent:
                continue
        out.append(d)
    return out


def catch_up(today=None, force=False, budget=40.0, max_days=40):
    """Fetch every missing working day from the RBZ (newest first). Stores what it finds, remembers what the RBZ has no file for,
    and gives a year-end that has NO rate at all the closing rate it can now find. Returns a one-line summary (also kept in the
    settings row). Never raises."""
    cfg = get_config()
    today = today or date.today()
    try:
        days = missing_days(today, force=force)[:max_days]
        cfg.last_attempt = datetime.utcnow()
        if not days:
            cfg.last_message = f"Catch-up {today.strftime('%d %b %Y')}: the register is up to date."
            db.session.commit()
            return cfg.last_message
        res = rbz_rates.fetch_days(days, budget=budget)
        added = updated = 0
        for rec in res["found"]:
            what = store_rbz(rec, cfg)
            added += what == "added"
            updated += what == "updated"
        for d, why in res["none"]:
            _note_miss(d, why)
        for d, why in res["bad"]:
            _note_miss(d, why)
        db.session.flush()
        filled = []
        for ye in _year_ends():
            if not ye.zwg_rate:
                row = register_rate("ZWG", ye.period_end)
                if row is not None and (ye.period_end - row.rate_date).days <= 10:
                    ye.zwg_rate = row.rate
                    filled.append(ye.period_end.strftime("%d %b %Y"))
        bits = [f"{added} rate(s) added" + (f", {updated} refreshed" if updated else "")]
        if res["none"] or res["bad"]:
            bits.append(f"{len(res['none']) + len(res['bad'])} day(s) the RBZ has no usable file for")
        if filled:
            bits.append("closing rate set on year-end " + ", ".join(filled))
        if res["error"]:
            bits.append("stopped: " + res["error"][:80])
        elif not res["done"]:
            bits.append("time limit reached; the rest follows next time")
        cfg.last_message = f"Catch-up {today.strftime('%d %b %Y')}: " + "; ".join(bits) + "."
        db.session.commit()
        return cfg.last_message
    except Exception as e:                      # a failure here must never reach the user's page or login
        db.session.rollback()
        try:
            cfg = get_config()
            cfg.last_message = f"Catch-up failed: {type(e).__name__}: {str(e)[:120]}"
            db.session.commit()
        except Exception:
            db.session.rollback()
        return f"Catch-up failed: {type(e).__name__}"


def _background(app, force):
    with _run_lock:
        with app.app_context():
            catch_up(force=force)


def start_catch_up(app, force=False):
    """Run the catch-up unless one ran within the last six hours (or automatic fetching is switched off). Runs in a background
    thread so signing in or opening a page never waits for the RBZ website; with app.config['RBZ_SYNC'] it runs inline (tests).
    Returns True when a run was started."""
    try:
        cfg = get_config()
        if not force and (not cfg.auto_fetch or (cfg.last_attempt and datetime.utcnow() - cfg.last_attempt < AUTO_EVERY)):
            return False
        if _run_lock.locked():
            return False
        cfg.last_attempt = datetime.utcnow()                # claimed now, so a second visit does not start a second run
        db.session.commit()
        if app.config.get("RBZ_SYNC"):
            catch_up(force=force)
        else:
            threading.Thread(target=_background, args=(app._get_current_object() if hasattr(app, "_get_current_object") else app, force), daemon=True).start()
        return True
    except Exception:
        db.session.rollback()
        return False


def on_login(app, user):
    """Called after a successful sign-in: anyone who can see Firm Finance (Managing Partner, Admin) triggers the catch-up."""
    try:
        if user_has_permission(user, "view_firm_finances"):
            return start_catch_up(app)
    except Exception:
        pass
    return False


def maybe_auto_fetch(today=None):
    """Opening the rates page also starts the catch-up (at most every six hours) and shows how the last one went."""
    from flask import current_app
    start_catch_up(current_app)
    return None


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
    if not year or not month or not (1 <= month <= 12) or date(year, month, 1) > date.today():
        flash("Choose a month and year that is not in the future.", "danger")
        return redirect(url_for("firmfs.exchange_rates"))
    if date(year + (month == 12), month % 12 + 1, 1) <= rbz_rates.ZWG_START:
        flash("The ZWG began in April 2024. The RBZ's rates before then are in Zimbabwe dollars, so they are not loaded.", "danger")
        return redirect(url_for("firmfs.exchange_rates"))
    overwrite = bool(request.form.get("overwrite"))
    first = date(year, month, 1)
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    have = {r.rate_date for r in FirmExchangeRate.query.filter(FirmExchangeRate.currency == "ZWG", FirmExchangeRate.rate_date >= first,
                                                               FirmExchangeRate.rate_date <= last).all()}
    res = rbz_rates.fetch_days(list(d for d in rbz_rates.working_days(first, min(last, date.today())) if overwrite or d not in have), budget=45.0)
    counts = {"added": 0, "updated": 0, "kept": 0}
    for rec in res["found"]:
        counts[store_rbz(rec, cfg, overwrite=overwrite)] += 1
    for d, why in res["none"] + res["bad"]:
        _note_miss(d, why)
    cfg.last_attempt = datetime.utcnow()
    cfg.last_message = f"Month fetch {MONTH_NAMES[month - 1]} {year}: {counts['added']} added, {counts['updated']} updated"
    db.session.commit()
    flash(f"{MONTH_NAMES[month - 1]} {year}: {counts['added']} rate(s) added, {counts['updated']} updated"
          + (f", {len(have)} day(s) already in the register were skipped" if have and not overwrite else "") + ".", "success" if res["found"] else "info")
    gaps = sorted(res["none"] + res["bad"])
    if gaps:
        flash("No usable RBZ file for: " + "; ".join(f"{d.strftime('%a %d %b')} ({why})" for d, why in gaps[:8]) + (f"; and {len(gaps) - 8} more" if len(gaps) > 8 else "")
              + ". Public holidays have no file. Enter any other day by hand if you need it.", "info")
    if res["error"]:
        flash("The RBZ website stopped answering: " + res["error"], "danger")
    elif not res["done"]:
        flash(f"Stopped at {res['stopped_at'].strftime('%d %b %Y')} (time limit). Press the button again to carry on from there.", "info")
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


@firmfs_bp.route("/exchange-rates/rbz/catch-up", methods=["POST"])
@finance_required
def rbz_catch_up():
    """Fetch every missing working day now (including days the RBZ had no file for earlier)."""
    msg = catch_up(force=True)
    flash(msg, "danger" if "failed" in msg else "success")
    return redirect(url_for("firmfs.exchange_rates"))


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
