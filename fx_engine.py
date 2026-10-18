"""The firm's one exchange-rate engine (USD <-> ZWG).

Every part of the app that needs a rate asks here, so the same date gives the same answer everywhere: Firm Finance (year-end closing
rates, statements, cash book, ledger), Tax (QPD instalments) and any engagement work that converts between USD and ZWG.

Three kinds of reading feed it. None is copied into another table, so a reading is entered once and used everywhere:
    manual  a rate typed in Firm Finance > Exchange rates                          (table FirmExchangeRate; source not "RBZ...")
    rbz     the Reserve Bank's interbank bid / ask / mid read from rbz.co.zw       (table FirmRbzReading; older rows kept in
                                                                                    FirmExchangeRate with source "RBZ..." count too)
    zimra   the rates kept under Tax > FX rates                                    (table FxRate, as uploaded)

`rate_on(day, purpose)` returns the most recent reading dated on or before `day`; when readings share that date the purpose decides:
    reporting  (financial statements, year-ends, cash book, ledger)   typed > RBZ > ZIMRA   (RBZ and ZIMRA swap if the Partner prefers ZIMRA)
    tax        (QPD and other tax computations)                       ZIMRA > typed > RBZ
A date with no reading in the preferred source falls back to the next one, so there is a rate whenever any source has one.

Pure database logic: no Flask routes here (those are in firm_fx.py)."""
import re
from datetime import date, timedelta

from extensions import db
from models import FirmExchangeRate, FirmFxConfig, FirmRbzReading, FxRate

ZWG, USD = "ZWG", "USD"
KIND_LABELS = {"manual": "Typed by hand", "rbz": "RBZ interbank", "zimra": "Tax rates (ZIMRA)"}
SAME_TOL = 0.00015            # rates are published to four decimals
NEAR_PCT = 0.5                # "within 0.5% of the RBZ mid"
_NOTE_RX = re.compile(r"bid ([\d.]+) / ask ([\d.]+) / mid ([\d.]+)")


def config():
    cfg = FirmFxConfig.query.first()
    if cfg is None:
        cfg = FirmFxConfig(rbz_basis="mid", auto_fetch=True)
        db.session.add(cfg)
        db.session.commit()
    return cfg


def order_for(purpose, cfg=None):
    """Source preference for a purpose, best first."""
    if purpose == "tax":
        return ("zimra", "manual", "rbz")
    cfg = cfg or config()
    return ("manual", "zimra", "rbz") if getattr(cfg, "prefer", None) == "zimra" else ("manual", "rbz", "zimra")


class Rate:
    """A rate the engine settled on. `rate`, `rate_date`, `source` and `note` are what the rest of the app reads."""
    def __init__(self, rate, rate_date, kind, source, note=None, asked=None):
        self.rate, self.rate_date, self.kind, self.source, self.note, self.asked = rate, rate_date, kind, source, note, asked

    @property
    def exact(self):
        return self.asked is not None and self.rate_date == self.asked

    @property
    def age_days(self):
        return (self.asked - self.rate_date).days if self.asked else 0

    def __repr__(self):
        return f"<Rate {self.rate:g} {self.rate_date} {self.kind}>"


def rbz_value(bid, ask, mid, basis):
    return {"bid": bid, "ask": ask}.get(basis, mid)


def _legacy_note_values(row):
    m = _NOTE_RX.search(row.note or "")
    return tuple(float(x) for x in m.groups()) if m else None


def _is_rbz_row(row):
    return (row.source or "").startswith("RBZ")


def _is_copy_row(row):
    return (row.source or "").startswith("ZIMRA (Tax rates)")


# ---------------------------------------------------------------- readings of one source

def _manual_latest(day):
    for row in (FirmExchangeRate.query.filter(FirmExchangeRate.currency == ZWG, FirmExchangeRate.rate_date <= day)
                .order_by(FirmExchangeRate.rate_date.desc(), FirmExchangeRate.id.desc()).all()):
        if not _is_rbz_row(row) and not _is_copy_row(row):
            return row
    return None


def _rbz_latest(day, basis):
    """(date, rate, note) of the newest RBZ reading on or before `day`: from the readings table, else an older RBZ row of the register."""
    best = None
    rd = FirmRbzReading.query.filter(FirmRbzReading.rate_date <= day).order_by(FirmRbzReading.rate_date.desc()).first()
    if rd is not None:
        best = (rd.rate_date, rbz_value(rd.bid, rd.ask, rd.mid, basis), f"bid {rd.bid:g} / ask {rd.ask:g} / mid {rd.mid:g}")
    for row in (FirmExchangeRate.query.filter(FirmExchangeRate.currency == ZWG, FirmExchangeRate.rate_date <= day, FirmExchangeRate.source.like("RBZ%"))
                .order_by(FirmExchangeRate.rate_date.desc()).limit(3).all()):
        if best is not None and row.rate_date <= best[0]:
            break
        vals = _legacy_note_values(row)
        best = (row.rate_date, rbz_value(*vals, basis) if vals else row.rate, row.note)
        break
    return best


def _zimra_latest(day):
    fx = FxRate.query.filter(FxRate.rate_date <= day).order_by(FxRate.rate_date.desc()).first()
    return None if fx is None else (fx.rate_date, fx.rate, fx.source, fx.original_rate_zwl)


def rate_on(day, purpose="reporting", currency=ZWG):
    """The rate for `day`: the most recent reading dated on or before it, ties broken by the purpose's source order.
    USD is always 1. Returns a Rate, or None when no source has a rate dated on or before `day`."""
    if day is None:
        return None
    if currency == USD:
        return Rate(1.0, day, "manual", "USD", asked=day)
    if currency != ZWG:
        return None
    cfg = config()
    found = {}
    m = _manual_latest(day)
    if m is not None and m.rate and m.rate > 0:
        found["manual"] = Rate(m.rate, m.rate_date, "manual", m.source or KIND_LABELS["manual"], m.note, day)
    r = _rbz_latest(day, cfg.rbz_basis or "mid")
    if r is not None and r[1] and r[1] > 0:
        found["rbz"] = Rate(r[1], r[0], "rbz", f"RBZ interbank {cfg.rbz_basis or 'mid'}", r[2], day)
    z = _zimra_latest(day)
    if z is not None and z[1] and z[1] > 0:
        note = "from ZWL " + format(z[3], "g") if z[3] else None
        found["zimra"] = Rate(z[1], z[0], "zimra", KIND_LABELS["zimra"] + (f", {z[2]}" if z[2] else ""), note, day)
    if not found:
        return None
    order = order_for(purpose, cfg)
    newest = max(r.rate_date for r in found.values())
    for kind in order:
        if kind in found and found[kind].rate_date == newest:
            return found[kind]
    return None


def closing_rate(period_end, purpose="reporting"):
    return rate_on(period_end, purpose)


def convert(amount, frm, to, day, purpose="reporting"):
    """(converted amount, Rate used) between USD and ZWG at the rate for `day`. Same currency: (amount, None).
    Returns (None, None) when a rate is needed and none is on file."""
    if amount is None:
        return None, None
    if frm == to:
        return amount, None
    r = rate_on(day, purpose)
    if r is None or not r.rate or r.rate <= 0:
        return None, None
    if frm == USD and to == ZWG:
        return amount * r.rate, r
    if frm == ZWG and to == USD:
        return amount / r.rate, r
    return None, None


def working_days(start, end):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def average_rate(start, end, purpose="reporting"):
    """(mean ZWG per USD over the working days start..end that have a reading of their own, number of days). (None, 0) when none."""
    total, n = 0.0, 0
    for d in working_days(start, end):
        r = rate_on(d, purpose)
        if r is not None and r.rate_date == d:
            total += r.rate
            n += 1
    return (total / n, n) if n else (None, 0)


# ---------------------------------------------------------------- one day across all sources, and the check between them

def verdict(z, rbz):
    """How the ZIMRA rate `z` relates to the RBZ's (bid, ask, mid): (code, label)."""
    if rbz is None:
        return "norbz", "No RBZ reading for this date"
    bid, ask, mid = rbz
    if abs(z - mid) <= SAME_TOL:
        return "same", "Same as the RBZ mid"
    if any(abs(round(mid, d) - z) <= 1e-9 for d in (2, 3)):
        return "same", "Same as the RBZ mid (rounded)"
    if abs(z - bid) <= SAME_TOL:
        return "bid", "Equals the RBZ bid"
    if abs(z - ask) <= SAME_TOL:
        return "ask", "Equals the RBZ ask"
    pct = abs(z - mid) / mid * 100
    if pct <= NEAR_PCT:
        return "near", f"Within {NEAR_PCT:g}% of the RBZ mid" + (" (inside bid-ask)" if bid <= z <= ask else "")
    return "differs", "Differs from the RBZ" + (" (inside bid-ask)" if bid <= z <= ask else " (outside bid-ask)")


def days_between(start, end, only_with=None):
    """One row per date in start..end that any source has a reading for (newest first):
    {"date", "manual": row|None, "rbz": (bid, ask, mid)|None, "zimra": FxRate|None, "used": Rate (reporting), "tax": Rate,
     "code", "label"}   -- code/label compare ZIMRA with the RBZ when both exist.
    `only_with`: a set of source kinds the row must have (e.g. {"zimra"})."""
    manual = {}
    for row in FirmExchangeRate.query.filter(FirmExchangeRate.currency == ZWG, FirmExchangeRate.rate_date >= start, FirmExchangeRate.rate_date <= end).all():
        if not _is_rbz_row(row) and not _is_copy_row(row):
            manual[row.rate_date] = row
    rbz = {r.rate_date: (r.bid, r.ask, r.mid) for r in FirmRbzReading.query.filter(FirmRbzReading.rate_date >= start, FirmRbzReading.rate_date <= end).all()}
    for row in FirmExchangeRate.query.filter(FirmExchangeRate.currency == ZWG, FirmExchangeRate.rate_date >= start, FirmExchangeRate.rate_date <= end,
                                             FirmExchangeRate.source.like("RBZ%")).all():
        if row.rate_date not in rbz:
            vals = _legacy_note_values(row)
            if vals:
                rbz[row.rate_date] = vals
    zimra = {f.rate_date: f for f in FxRate.query.filter(FxRate.rate_date >= start, FxRate.rate_date <= end).all()}
    out = []
    for d in sorted(set(manual) | set(rbz) | set(zimra), reverse=True):
        kinds = {k for k, v in (("manual", manual.get(d)), ("rbz", rbz.get(d)), ("zimra", zimra.get(d))) if v}
        if only_with and not (only_with <= kinds):
            continue
        z = zimra.get(d)
        code, label = verdict(z.rate, rbz.get(d)) if z is not None else (("only_rbz", "Only the RBZ has a rate") if d in rbz else ("only_typed", "Only a typed rate"))
        out.append({"date": d, "manual": manual.get(d), "rbz": rbz.get(d), "zimra": z, "used": rate_on(d, "reporting"), "tax": rate_on(d, "tax"),
                    "code": code, "label": label, "kinds": kinds})
    return out


def counts_of(rows):
    c = {}
    for r in rows:
        c[r["code"]] = c.get(r["code"], 0) + 1
    return c
