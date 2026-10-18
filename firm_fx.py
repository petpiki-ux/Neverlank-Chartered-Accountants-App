"""Firm Finance > Exchange rates: a register of dated rates (ZWG per 1 USD) that year-ends take their closing rate from.

The financial statements, cash book and tax pages still translate every ZWG item at the single rate stored on the year-end
(FirmYearEnd.zwg_rate). The register fills that rate: a year-end created without a rate takes the latest register rate dated
on or before it, and the year-end page and this page offer "use the register rate" where the two differ."""
from datetime import date, datetime

from flask import render_template, redirect, url_for, request, flash
from flask_login import current_user

from extensions import db
from models import FirmExchangeRate, FirmYearEnd, INVOICE_CURRENCIES
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
    rows = FirmExchangeRate.query.order_by(FirmExchangeRate.rate_date.desc(), FirmExchangeRate.id.desc()).limit(400).all()
    yes = _year_ends()
    return render_template("firmfs/exchange_rates.html", active="fx", ye=None, rates=rows, today=date.today(), currencies=FOREIGN,
                           year_ends=[(y, year_end_info(y)) for y in reversed(yes)], stale_days=STALE_DAYS)


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
