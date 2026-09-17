"""In-app voice/video calling between two logged-in users.

This is WebRTC: the audio/video itself streams directly between the two
browsers (peer-to-peer), not through this server. All this module does is
"signaling" - the brief handshake needed before that direct connection can
be made (who's calling whom, and swapping the connection details each
browser needs) - plus simple presence (who's currently online) so a caller
gets an immediate "not available" instead of ringing into silence.

Important limitation: this uses only a public STUN server (see
templates/calls/room.html) to help each browser discover its own reachable
address. That's enough for most calls, but two people both behind strict
corporate/hotel-style firewalls can fail to connect to each other - fixing
that needs a TURN relay server (a paid service, or self-hosted coturn),
which is NOT set up here. See README_APPLY_THIS_UPDATE.txt.

State here (who's online, who's mid-call) lives in plain in-memory
dictionaries, not the database - calls aren't meant to be durable, and this
keeps things simple. That does mean it only works correctly with a single
web worker process (see the Procfile/render.yaml comments) - with more than
one, different users could land on different processes that can't see each
other's presence.
"""
import uuid

from flask import Blueprint, render_template, redirect, url_for, abort, request
from flask_login import login_required, current_user
from flask_socketio import join_room, leave_room, emit

from extensions import socketio
from models import User

calls_bp = Blueprint("calls", __name__, url_prefix="/calls")

# user_id -> set of Socket.IO session ids currently connected (any page,
# any tab) - just enough to answer "is this person online right now?".
_online_sids = {}
# call_id -> {"caller_id", "callee_id"} for calls that have been invited
# but not yet joined/ended - lets a decline/cancel reach the right person.
_pending_calls = {}
# call_id -> number of participants who have joined the call room, used
# only to decide which side creates the WebRTC offer (whoever joins the
# room *first* waits; whoever joins *second* triggers the first to start).
_call_join_counts = {}


def _user_room(user_id):
    return f"user-{user_id}"


def _call_room(call_id):
    return f"call-{call_id}"


@calls_bp.route("/<int:user_id>")
@login_required
def call_room(user_id):
    if user_id == current_user.id:
        abort(400)
    other = User.query.get_or_404(user_id)
    call_id = request.args.get("call_id") or str(uuid.uuid4())
    # role "caller": this page should immediately invite `other`.
    # role "callee": this page was reached by accepting an incoming call
    # banner, so it should join straight into the existing call_id instead.
    role = "callee" if request.args.get("call_id") else "caller"
    return render_template("calls/room.html", other=other, call_id=call_id, role=role)


# ---------- Presence ----------

@socketio.on("connect")
def handle_connect():
    if not current_user.is_authenticated:
        return False  # reject the connection
    join_room(_user_room(current_user.id))
    _online_sids.setdefault(current_user.id, set()).add(request.sid)


@socketio.on("disconnect")
def handle_disconnect():
    if not current_user.is_authenticated:
        return
    sids = _online_sids.get(current_user.id)
    if sids:
        sids.discard(request.sid)
        if not sids:
            _online_sids.pop(current_user.id, None)


# ---------- Call signaling ----------

@socketio.on("call:invite")
def handle_invite(data):
    if not current_user.is_authenticated:
        return
    call_id = data.get("call_id")
    to_id = data.get("to")
    if not call_id or not to_id:
        return
    to_id = int(to_id)
    if not _online_sids.get(to_id):
        emit("call:unavailable", {"call_id": call_id})
        return
    _pending_calls[call_id] = {"caller_id": current_user.id, "callee_id": to_id}
    emit(
        "call:incoming",
        {"call_id": call_id, "from_id": current_user.id, "from_name": current_user.name},
        room=_user_room(to_id),
    )


@socketio.on("call:decline")
def handle_decline(data):
    call_id = data.get("call_id")
    info = _pending_calls.pop(call_id, None)
    if info:
        emit("call:declined", {"call_id": call_id}, room=_user_room(info["caller_id"]))


@socketio.on("call:cancel")
def handle_cancel(data):
    """Caller hangs up before the callee has answered."""
    call_id = data.get("call_id")
    info = _pending_calls.pop(call_id, None)
    if info:
        emit("call:cancelled", {"call_id": call_id}, room=_user_room(info["callee_id"]))


@socketio.on("call:join")
def handle_join(data):
    call_id = data.get("call_id")
    if not call_id:
        return
    room = _call_room(call_id)
    join_room(room)
    count = _call_join_counts.get(call_id, 0) + 1
    _call_join_counts[call_id] = count
    if count == 1:
        emit("call:waiting", {"call_id": call_id})
    else:
        # Tell whoever is already in the room (the first joiner) to create
        # the WebRTC offer now that both sides are present.
        emit("call:start-offer", {"call_id": call_id}, room=room, include_self=False)


@socketio.on("call:offer")
def handle_offer(data):
    call_id = data.get("call_id")
    if not call_id:
        return
    emit("call:offer", data, room=_call_room(call_id), include_self=False)


@socketio.on("call:answer")
def handle_answer(data):
    call_id = data.get("call_id")
    if not call_id:
        return
    emit("call:answer", data, room=_call_room(call_id), include_self=False)


@socketio.on("call:ice-candidate")
def handle_ice_candidate(data):
    call_id = data.get("call_id")
    if not call_id:
        return
    emit("call:ice-candidate", data, room=_call_room(call_id), include_self=False)


@socketio.on("call:hangup")
def handle_hangup(data):
    call_id = data.get("call_id")
    if not call_id:
        return
    room = _call_room(call_id)
    emit("call:ended", {"call_id": call_id}, room=room, include_self=False)
    _call_join_counts.pop(call_id, None)
    _pending_calls.pop(call_id, None)
    leave_room(room)
