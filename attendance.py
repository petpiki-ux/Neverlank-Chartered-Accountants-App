"""Attendance - the management view of the Check In / Check Out button.

Every check-in and check-out is already recorded (models.CheckInRecord, via the
top-bar button - see hr.check_in / hr.check_out). This page lets management see
it: who is in right now, each person's arrival and departure for a chosen day,
people who have not checked in, people still checked in from an earlier day
(they forgot to check out), a summary per person over a period, and a CSV of the
full log. Stored times are UTC; everything shown is Harare time (CAT).
"""
import csv
import io
from collections import OrderedDict
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request, abort, Response
from flask_login import login_required, current_user

from extensions import db
from models import CheckInRecord, User, user_has_permission
from timeutil import to_cat, today_cat, now_cat

attendance_bp = Blueprint("attendance", __name__, url_prefix="/hr/attendance")


def _require_access():
    if not user_has_permission(current_user, "view_attendance"):
        abort(403, description="Attendance is for management. Ask an Admin if you need access (Team > Permissions).")


def _parse_date(value, default):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return default


def _hours(record, now=None):
    """Hours for a record; for one still open, hours so far (using `now`)."""
    end = record.check_out_at or (now or datetime.utcnow())
    return round(max((end - record.check_in_at).total_seconds(), 0) / 3600, 2)


def _fmt_hours(hours):
    minutes = int(round(hours * 60))
    return f"{minutes // 60}h {minutes % 60:02d}m"


def _hhmm(minutes):
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


def _minutes_of_day(dt):
    c = to_cat(dt)
    return c.hour * 60 + c.minute


def _people():
    return User.query.filter_by(is_active_flag=True).order_by(User.name).all()


def day_board(day, people):
    """One row per active person for `day`: their sessions, status and hours."""
    records = CheckInRecord.query.filter(CheckInRecord.work_date == day).order_by(CheckInRecord.check_in_at).all()
    by_user = {}
    for r in records:
        by_user.setdefault(r.user_id, []).append(r)
    # open records from earlier days (never checked out)
    stale = {r.user_id: r for r in CheckInRecord.query.filter(
        CheckInRecord.check_out_at.is_(None), CheckInRecord.work_date < today_cat()).all()}
    now = datetime.utcnow()
    rows = []
    for p in people:
        sessions = by_user.get(p.id, [])
        total = sum(_hours(s, now) for s in sessions)
        if any(s.is_open for s in sessions):
            status = "in"
        elif sessions:
            status = "out"
        else:
            status = "absent"
        rows.append({
            "user": p, "sessions": sessions, "status": status, "hours": round(total, 2),
            "first_in": sessions[0].check_in_at if sessions else None,
            "last_out": max((s.check_out_at for s in sessions if s.check_out_at), default=None),
            "stale": stale.get(p.id) if stale.get(p.id) and stale[p.id].work_date != day else None,
        })
    order = {"in": 0, "out": 1, "absent": 2}
    rows.sort(key=lambda r: (order[r["status"]], r["first_in"] or datetime.max, r["user"].name.lower()))
    return rows


def summary(date_from, date_to, people, user_id=None):
    """Per-person totals for the period (hours count closed sessions only)."""
    q = CheckInRecord.query.filter(CheckInRecord.work_date >= date_from, CheckInRecord.work_date <= date_to)
    if user_id:
        q = q.filter(CheckInRecord.user_id == user_id)
    records = q.order_by(CheckInRecord.check_in_at).all()
    out = OrderedDict()
    for p in people:
        if user_id and p.id != user_id:
            continue
        out[p.id] = {"user": p, "days": set(), "hours": 0.0, "first_ins": [], "last_outs": {}, "open": 0}
    for r in records:
        row = out.get(r.user_id)
        if row is None:
            continue
        row["days"].add(r.work_date)
        if r.check_out_at:
            row["hours"] += _hours(r)
            prev = row["last_outs"].get(r.work_date)
            if prev is None or r.check_out_at > prev:
                row["last_outs"][r.work_date] = r.check_out_at
        else:
            row["open"] += 1
        firsts = row.setdefault("first_by_day", {})
        if r.work_date not in firsts:
            firsts[r.work_date] = r.check_in_at
    result = []
    for row in out.values():
        days = len(row["days"])
        firsts = list(row.get("first_by_day", {}).values())
        outs = list(row["last_outs"].values())
        result.append({
            "user": row["user"], "days": days, "hours": round(row["hours"], 2),
            "avg_hours": round(row["hours"] / days, 2) if days else 0.0,
            "avg_in": _hhmm(sum(_minutes_of_day(f) for f in firsts) / len(firsts)) if firsts else None,
            "avg_out": _hhmm(sum(_minutes_of_day(o) for o in outs) / len(outs)) if outs else None,
            "open": row["open"],
        })
    result.sort(key=lambda r: (-r["days"], r["user"].name.lower()))
    return result


def log(date_from, date_to, user_id=None):
    q = CheckInRecord.query.filter(CheckInRecord.work_date >= date_from, CheckInRecord.work_date <= date_to)
    if user_id:
        q = q.filter(CheckInRecord.user_id == user_id)
    return q.order_by(CheckInRecord.work_date.desc(), CheckInRecord.check_in_at.desc()).all()


def _period_args():
    today = today_cat()
    day = _parse_date(request.args.get("day"), today)
    default_from = today.replace(day=1)
    date_from = _parse_date(request.args.get("date_from"), default_from)
    date_to = _parse_date(request.args.get("date_to"), today)
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    user_id = request.args.get("user_id", type=int)
    return day, date_from, date_to, user_id


@attendance_bp.route("/")
@login_required
def index():
    _require_access()
    day, date_from, date_to, user_id = _period_args()
    people = _people()
    board = day_board(day, people)
    now = datetime.utcnow()
    counts = {"in": sum(1 for r in board if r["status"] == "in"), "out": sum(1 for r in board if r["status"] == "out"),
              "absent": sum(1 for r in board if r["status"] == "absent"), "stale": sum(1 for r in board if r["stale"])}
    return render_template(
        "attendance/index.html", day=day, today=today_cat(), board=board, counts=counts,
        date_from=date_from, date_to=date_to, user_id=user_id, people=people,
        summary=summary(date_from, date_to, people, user_id), records=log(date_from, date_to, user_id),
        timedelta_one=timedelta(days=1), fmt_hours=_fmt_hours, hours_of=lambda r: _hours(r, now), weekday=day.strftime("%A"),
    )


@attendance_bp.route("/export.csv")
@login_required
def export_csv():
    _require_access()
    _day, date_from, date_to, user_id = _period_args()
    now = datetime.utcnow()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Name", "Role", "Check in (CAT)", "Check out (CAT)", "Hours", "Status"])
    for r in reversed(log(date_from, date_to, user_id)):
        w.writerow([
            r.work_date.isoformat(), r.user.name if r.user else "", r.user.role if r.user else "",
            to_cat(r.check_in_at).strftime("%Y-%m-%d %H:%M"),
            to_cat(r.check_out_at).strftime("%Y-%m-%d %H:%M") if r.check_out_at else "",
            f"{_hours(r, now):.2f}", "Checked in" if r.is_open else "Checked out",
        ])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=attendance_{date_from}_{date_to}.csv"})
