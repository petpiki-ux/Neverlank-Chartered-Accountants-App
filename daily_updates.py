"""Projects Daily Update.

Each team member writes a short update for the day: what they are working on, roadblocks and stuck points, assistance required, problems solved,
suggestions for improvement, and any client or advisory fire (urgent client inquiry, cash-flow panic, emergency needing immediate cross-team
support). Next to what they write, the app adds the facts it already holds for that person: open and overdue tasks, deadlines coming up, booked
capacity for the week, hours logged that day.

The Managing Partner (Admin) and Partners see the team roll-up: fires first, then deadline and capacity alerts, then one card per team member, then
the assistance, solved-problem and suggestion lists across the team and who has not yet submitted. Everyone else sees and edits only their own update.
Partners see another person's tasks and engagements only on engagements they are themselves on (the same confidentiality rule as the rest of the app);
what a person writes in their own update is shown as they wrote it.
"""
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import db
from models import (
    DailyUpdate, EngagementTask, PersonalTask, StaffAllocation, TimeEntry, TimeSheet, User, user_can_access_engagement, visible_clients,
)

daily_updates_bp = Blueprint("daily_updates", __name__, url_prefix="/daily-updates")

DUE_SOON_DAYS = 3          # a task due within this many days is a "deadline alert"
OVERBOOKED_PCT = 100       # same thresholds as the Practice Dashboard's staffing panel
UNDERBOOKED_PCT = 50
EDIT_WINDOW_DAYS = 14      # an update can be written or corrected for today and the 14 days before it
OPEN_STATUSES_EXCLUDED = ("Done",)


def can_see_team(user=None):
    user = user or current_user
    return bool(getattr(user, "is_authenticated", False) and user.role in ("admin", "partner"))


def _parse_day(value, default=None):
    default = default or date.today()
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return default


def _week_bounds(day):
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def _engagement_visible(viewer, engagement):
    """May `viewer` see this engagement's name and the work on it? (no engagement = general work, always visible)"""
    return engagement is None or user_can_access_engagement(viewer, engagement)


def member_facts(person, day, viewer):
    """The facts the app already knows about one person on `day`, as seen by `viewer`:
    open_tasks / overdue / due_soon (lists of dicts), hidden_count (tasks on engagements the viewer is not on), capacity, hours, engagements."""
    soon = day + timedelta(days=DUE_SOON_DAYS)
    rows, hidden = [], 0
    for t in EngagementTask.query.filter(EngagementTask.assigned_to_id == person.id, EngagementTask.status.notin_(OPEN_STATUSES_EXCLUDED)).all():
        if not _engagement_visible(viewer, t.engagement):
            hidden += 1
            continue
        rows.append({"title": t.title, "due": t.due_date, "priority": t.priority, "status": t.status,
                     "engagement": t.engagement.title if t.engagement else None,
                     "client": t.engagement.client.name if t.engagement and t.engagement.client else None})
    for t in PersonalTask.query.filter(PersonalTask.assigned_to_id == person.id, PersonalTask.status.notin_(OPEN_STATUSES_EXCLUDED)).all():
        if not _engagement_visible(viewer, t.engagement):
            hidden += 1
            continue
        rows.append({"title": t.title, "due": t.due_date, "priority": t.priority, "status": t.status,
                     "engagement": t.engagement.title if t.engagement else None,
                     "client": t.engagement.client.name if t.engagement and t.engagement.client else None})
    overdue = sorted([r for r in rows if r["due"] and r["due"] < day], key=lambda r: r["due"])
    due_soon = sorted([r for r in rows if r["due"] and day <= r["due"] <= soon], key=lambda r: r["due"])
    wk_start, wk_end = _week_bounds(day)
    allocs = [a for a in StaffAllocation.query.filter(StaffAllocation.user_id == person.id, StaffAllocation.end_date >= wk_start,
                                                       StaffAllocation.start_date <= wk_end).all()]
    capacity = sum(a.allocation_pct or 0 for a in allocs)
    visible_allocs = [a for a in allocs if _engagement_visible(viewer, a.engagement)]
    hours = 0.0
    for sheet in TimeSheet.query.filter(TimeSheet.user_id == person.id, TimeSheet.week_start == wk_start).all():
        hours += sum(e.hours or 0 for e in TimeEntry.query.filter_by(timesheet_id=sheet.id, work_date=day).all())
    engagements = sorted({a.engagement.title for a in visible_allocs} | {r["engagement"] for r in rows if r["engagement"]})
    return {"open_tasks": rows, "open_count": len(rows) + hidden, "overdue": overdue, "due_soon": due_soon, "hidden_count": hidden,
            "capacity": capacity, "over_booked": capacity > OVERBOOKED_PCT, "under_booked": 0 < capacity < UNDERBOOKED_PCT,
            "hours": round(hours, 2), "engagements": engagements}


def build_rollup(day, viewer):
    """Everything the team page shows for `day`."""
    people = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
    updates = {u.user_id: u for u in DailyUpdate.query.filter_by(update_date=day).all()}
    cards = []
    for person in people:
        cards.append({"user": person, "entry": updates.get(person.id), "facts": member_facts(person, day, viewer)})
    fires = [c for c in cards if c["entry"] and c["entry"].has_fire]
    fires.sort(key=lambda c: (not c["entry"].fire_urgent, c["user"].name))
    alerts = []
    for c in cards:
        f, name = c["facts"], c["user"].name
        if f["overdue"]:
            alerts.append({"kind": "Overdue", "level": "danger", "who": name,
                           "text": f"{len(f['overdue'])} overdue task(s), oldest: {f['overdue'][0]['title']} (due {f['overdue'][0]['due'].strftime('%d %b')})"})
        if f["due_soon"]:
            alerts.append({"kind": "Deadline", "level": "warning", "who": name,
                           "text": f"{len(f['due_soon'])} task(s) due in the next {DUE_SOON_DAYS} days, first: {f['due_soon'][0]['title']} ({f['due_soon'][0]['due'].strftime('%d %b')})"})
        if f["over_booked"]:
            alerts.append({"kind": "Capacity", "level": "danger", "who": name, "text": f"Booked {f['capacity']}% this week (over 100%)"})
        elif f["under_booked"]:
            alerts.append({"kind": "Capacity", "level": "info", "who": name, "text": f"Booked only {f['capacity']}% this week - has room for more work"})
        u = c["entry"]
        if u and u.has_roadblock and u.needs_help:
            alerts.append({"kind": "Stuck", "level": "warning", "who": name, "text": "Has a roadblock and has asked for help"})
    order = {"danger": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: (order[a["level"]], a["who"]))
    lists = {key: [(c["user"], getattr(c["entry"], key)) for c in cards if c["entry"] and (getattr(c["entry"], key) or "").strip()]
             for key in ("assistance_needed", "problems_solved", "suggestions")}
    missing = [c["user"] for c in cards if not c["entry"]]
    return {"cards": cards, "fires": fires, "alerts": alerts, "lists": lists, "missing": missing,
            "submitted": len(cards) - len(missing), "total": len(cards)}


def _clients_for_select(user):
    try:
        return visible_clients(user)
    except Exception:
        return []


@daily_updates_bp.route("/", methods=["GET"])
@login_required
def my_update():
    day = _parse_day(request.args.get("date"))
    if day > date.today():
        day = date.today()
    update = DailyUpdate.query.filter_by(user_id=current_user.id, update_date=day).first()
    facts = member_facts(current_user, day, current_user)
    recent = DailyUpdate.query.filter_by(user_id=current_user.id).order_by(DailyUpdate.update_date.desc()).limit(8).all()
    return render_template("daily_updates/form.html", day=day, today=date.today(), update=update, facts=facts, recent=recent,
                           clients=_clients_for_select(current_user), can_team=can_see_team(),
                           editable=(date.today() - day).days <= EDIT_WINDOW_DAYS, prev_day=day - timedelta(days=1),
                           next_day=(day + timedelta(days=1)) if day < date.today() else None)


@daily_updates_bp.route("/save", methods=["POST"])
@login_required
def save_update():
    day = _parse_day(request.form.get("update_date"))
    if day > date.today() or (date.today() - day).days > EDIT_WINDOW_DAYS:
        flash(f"An update can be written for today or the last {EDIT_WINDOW_DAYS} days.", "danger")
        return redirect(url_for("daily_updates.my_update"))
    update = DailyUpdate.query.filter_by(user_id=current_user.id, update_date=day).first()
    if update is None:
        update = DailyUpdate(user_id=current_user.id, update_date=day)
        db.session.add(update)
    for field in DailyUpdate.TEXT_FIELDS:
        setattr(update, field, (request.form.get(field) or "").strip() or None)
    update.fire_urgent = bool(request.form.get("fire_urgent"))
    client_id = request.form.get("fire_client_id", type=int)
    allowed = {c.id for c in _clients_for_select(current_user)}
    update.fire_client_id = client_id if client_id in allowed else None
    db.session.commit()
    flash("Your daily update is saved." + (" It is flagged as needing immediate support." if update.fire_urgent else ""), "success")
    return redirect(url_for("daily_updates.my_update", date=day.isoformat()))


@daily_updates_bp.route("/team", methods=["GET"])
@login_required
def team_rollup():
    if not can_see_team():
        abort(403, description="The team roll-up is for the Managing Partner and Partners.")
    day = _parse_day(request.args.get("date"))
    if day > date.today():
        day = date.today()
    return render_template("daily_updates/team.html", day=day, today=date.today(), roll=build_rollup(day, current_user),
                           prev_day=day - timedelta(days=1), next_day=(day + timedelta(days=1)) if day < date.today() else None,
                           due_soon_days=DUE_SOON_DAYS)
