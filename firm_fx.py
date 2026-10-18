"""Firm Finance > Exchange rates: the screens and the RBZ fetching for the firm's exchange-rate engine (see fx_engine.py).

The engine answers every "what was the ZWG per USD rate on this date" question for the whole app from three readings: rates typed
here, the RBZ interbank rates fetched from rbz.co.zw, and the Tax rates (ZIMRA) kept under Tax > FX rates. A year-end takes its
closing rate from the engine; the financial statements, cash book and ledger then translate ZWG items at the rate stored on the
year-end (FirmYearEnd.zwg_rate)."""
import threading
from datetime import date, datetime, timedelta

from flask import render_template, redirect, url_for, request, flash
from flask_login import current_user, login_required

from extensions import db
from models import FirmExchangeRate, FirmFxConfig, FirmFxMiss, FirmRbzReading, FirmYearEnd, FxRate, INVOICE_CURRENCIES, user_has_permission
import fx_engine
import rbz_rates
from finance import finance_required, _parse_date, _parse_float
from firm_finance import firmfs_bp, _year_ends

FOREIGN = [c for c in INVOICE_CURRENCIES if c != "USD"]
STALE_DAYS = 31


def register_rate(currency, on, purpose="reporting"):
    """The engine's rate for the date (a fx_engine.Rate with .rate, .rate_date, .source, .note) or None."""
    return fx_engine.rate_on(on, purpose, currency)


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
    start = _parse_date(request.args.get("from")) or (date.today() - timedelta(days=60))
    end = _parse_date(request.args.get("to")) or date.today()
    if start > end:
        start, end = end, start
    days = fx_engine.days_between(start, end)[:400]
    yes = _year_ends()
    convert = None
    amt = _parse_float(request.args.get("amount"))
    if amt is not None:
        when = _parse_date(request.args.get("on")) or date.today()
        frm, to = request.args.get("frm", "USD"), request.args.get("to_cur", "ZWG")
        purpose = "tax" if request.args.get("purpose") == "tax" else "reporting"
        value, used = fx_engine.convert(amt, frm, to, when, purpose)
        convert = {"amount": amt, "from": frm, "to": to, "on": when, "value": value, "rate": used, "purpose": purpose}
    return render_template("firmfs/exchange_rates.html", active="fx", ye=None, days=days, start=start, end=end, today=date.today(), currencies=FOREIGN,
                           year_ends=[(y, year_end_info(y)) for y in reversed(yes)], stale_days=STALE_DAYS,
                           cfg=get_config(), bases=BASES, auto=auto, convert=convert, kinds=fx_engine.KIND_LABELS,
                           misses=FirmFxMiss.query.filter(FirmFxMiss.rate_date >= date.today() - timedelta(days=120)).order_by(FirmFxMiss.rate_date.desc()).limit(40).all(), months=list(enumerate(rbz_rates.MONTHS, 1)))


@firmfs_bp.route("/exchange-rates/lookup")
@login_required
def fx_lookup():
    """JSON for any part of the app (or a script) that needs a rate: /firm-finance/exchange-rates/lookup?on=2026-09-30&purpose=tax
    (optionally amount, frm, to to convert). Rates are not confidential, so any signed-in user may ask."""
    from flask import jsonify
    on = _parse_date(request.args.get("on")) or date.today()
    purpose = "tax" if request.args.get("purpose") == "tax" else "reporting"
    r = fx_engine.rate_on(on, purpose)
    if r is None:
        return jsonify({"on": on.isoformat(), "found": False, "message": "No rate on or before that date."}), 404
    out = {"on": on.isoformat(), "found": True, "currency_pair": "USD/ZWG", "rate": r.rate, "rate_date": r.rate_date.isoformat(), "exact": r.exact,
           "age_days": r.age_days, "source": r.source, "kind": r.kind, "purpose": purpose}
    amt = _parse_float(request.args.get("amount"))
    if amt is not None:
        value, _ = fx_engine.convert(amt, request.args.get("frm", "USD"), request.args.get("to", "ZWG"), on, purpose)
        out.update({"amount": amt, "from": request.args.get("frm", "USD"), "to": request.args.get("to", "ZWG"), "converted": value})
    return jsonify(out)


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
    return fx_engine.config()


def _who():
    """The signed-in user's id, or None when running in the background."""
    try:
        return current_user.id if current_user and current_user.is_authenticated else None
    except Exception:
        return None


def store_rbz(rec, cfg, overwrite=False):
    """Keep what the RBZ published for a day (bid, ask, mid). The engine turns it into a rate on demand using the chosen figure, so
    changing the figure later (mid, bid, ask) applies to every date. Returns 'added' or 'updated'. A rate typed by hand for the same
    date is untouched and still wins (see fx_engine)."""
    rd = FirmRbzReading.query.filter_by(rate_date=rec["date"]).first()
    if rd is None:
        db.session.add(FirmRbzReading(rate_date=rec["date"], bid=rec["bid"], ask=rec["ask"], mid=rec["mid"]))
        return "added"
    changed = (rd.bid, rd.ask, rd.mid) != (rec["bid"], rec["ask"], rec["mid"])
    rd.bid, rd.ask, rd.mid, rd.fetched_at = rec["bid"], rec["ask"], rec["mid"], datetime.utcnow()
    return "updated" if changed else "same"


def rbz_dates(start=None, end=None):
    """Dates with an RBZ reading (the readings table, plus older RBZ rows kept in the register)."""
    q = FirmRbzReading.query
    q2 = FirmExchangeRate.query.filter(FirmExchangeRate.currency == "ZWG", FirmExchangeRate.source.like("RBZ%"))
    if start:
        q, q2 = q.filter(FirmRbzReading.rate_date >= start), q2.filter(FirmExchangeRate.rate_date >= start)
    if end:
        q, q2 = q.filter(FirmRbzReading.rate_date <= end), q2.filter(FirmExchangeRate.rate_date <= end)
    return {r.rate_date for r in q.all()} | {r.rate_date for r in q2.all()}


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
    have = rbz_dates()                  # the RBZ is asked for every working day it has not been asked for; a typed or Tax rate does not stop that
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
            bits.append(f"{len(res['none']) + len(res['bad'])} day(s) the RBZ has no usable file for (the Tax rate or the last earlier rate applies)")
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


# ---------------------------------------------------------------- the check between the Tax rates (ZIMRA) and the RBZ

def _window():
    end = _parse_date(request.values.get("to")) or date.today()
    start = _parse_date(request.values.get("from")) or (end - timedelta(days=90))
    if start > end:
        start, end = end, start
    return start, end


@firmfs_bp.route("/exchange-rates/compare")
@finance_required
def fx_compare():
    start, end = _window()
    rows = fx_engine.days_between(start, end, only_with={"zimra"})
    return render_template("firmfs/exchange_compare.html", active="fx", ye=None, rows=rows, counts=fx_engine.counts_of(rows), start=start, end=end,
                           total=FxRate.query.count(), first=db.session.query(db.func.min(FxRate.rate_date)).scalar(),
                           last=db.session.query(db.func.max(FxRate.rate_date)).scalar())


@firmfs_bp.route("/exchange-rates/compare/load-rbz", methods=["POST"])
@finance_required
def fx_compare_load():
    """Fetch the RBZ's figures for the Tax-rate dates in the window that have no RBZ reading yet."""
    start, end = _window()
    cfg = get_config()
    have = rbz_dates()
    days = sorted({fx.rate_date for fx in FxRate.query.filter(FxRate.rate_date >= start, FxRate.rate_date <= end).all()
                   if fx.rate_date not in have and fx.rate_date.weekday() < 5 and fx.rate_date >= rbz_rates.ZWG_START and fx.rate_date <= date.today()}, reverse=True)
    back = url_for("firmfs.fx_compare", **{"from": start.isoformat(), "to": end.isoformat()})
    if not days:
        flash("Every Tax-rate date in that period already has its RBZ figures (or is a weekend or before the ZWG began).", "info")
        return redirect(back)
    res = rbz_rates.fetch_days(days[:60], budget=45.0)
    for rec in res["found"]:
        store_rbz(rec, cfg)
    for d, why in res["none"] + res["bad"]:
        _note_miss(d, why)
    db.session.commit()
    flash(f"RBZ figures loaded for {len(res['found'])} of {min(len(days), 60)} date(s)."
          + (f" {len(days) - 60} more remain: press again." if len(days) > 60 else "")
          + (f" No usable RBZ file for {len(res['none']) + len(res['bad'])} date(s)." if res["none"] or res["bad"] else ""), "success" if res["found"] else "info")
    if res["error"]:
        flash("The RBZ website stopped answering: " + res["error"], "danger")
    elif not res["done"]:
        flash("Time limit reached; press again to carry on.", "info")
    return redirect(back)


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
    what = store_rbz(rec, cfg)
    cfg.last_message = f"Manual fetch: {rec['date'].strftime('%d %b %Y')} ({what})"
    db.session.commit()
    val = rbz_rates.pick(rec, cfg.rbz_basis)
    flash(f"RBZ interbank {cfg.rbz_basis} rate for {rec['date'].strftime('%d %b %Y')}: {val:g} ZWG per USD ({what}).", "success")
    typed = fx_engine.rate_on(rec["date"], "reporting")
    if typed is not None and typed.kind == "manual" and typed.rate_date == rec["date"]:
        flash(f"You have typed {typed.rate:g} for that date, so that stays in use. Delete the typed rate to use the RBZ's.", "info")
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
    overwrite = bool(request.form.get("overwrite"))            # ask the RBZ again even for days already read
    first = date(year, month, 1)
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    have = rbz_dates(first, last)
    res = rbz_rates.fetch_days(list(d for d in rbz_rates.working_days(first, min(last, date.today())) if overwrite or d not in have), budget=45.0)
    counts = {"added": 0, "updated": 0, "same": 0}
    for rec in res["found"]:
        counts[store_rbz(rec, cfg)] += 1
    for d, why in res["none"] + res["bad"]:
        _note_miss(d, why)
    cfg.last_attempt = datetime.utcnow()
    cfg.last_message = f"Month fetch {MONTH_NAMES[month - 1]} {year}: {counts['added']} added, {counts['updated']} updated"
    db.session.commit()
    flash(f"{MONTH_NAMES[month - 1]} {year}: {counts['added']} rate(s) added, {counts['updated']} updated"
          + (f", {len(have)} day(s) already read were skipped" if have and not overwrite else "") + ".", "success" if res["found"] else "info")
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
    if request.form.get("prefer") in ("rbz", "zimra"):
        cfg.prefer = request.form.get("prefer")
    db.session.commit()
    flash("Exchange-rate settings saved.", "success")
    return redirect(url_for("firmfs.exchange_rates"))


MONTH_NAMES = rbz_rates.MONTHS
