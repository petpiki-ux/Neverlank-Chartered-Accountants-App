from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_socketio import SocketIO

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "info"

# Used for real-time features: voice/video call signaling (see calls.py) and
# presence (who's currently online, for the "Call" button). async_mode is
# pinned to "threading" deliberately - it needs no extra dependency
# (eventlet/gevent) and no monkey-patching, so it works the same way in the
# Render deployment (gunicorn) and the standalone Windows .exe build. Its
# one trade-off: under gunicorn it falls back to Socket.IO long-polling
# instead of a real WebSocket upgrade - completely fine here, since the
# actual call audio/video never goes through this at all (that's
# peer-to-peer WebRTC) - this channel only carries small, infrequent
# signaling messages. See README_APPLY_THIS_UPDATE.txt for the one
# required change this needs in production: a single web worker process.
socketio = SocketIO(cors_allowed_origins="*", async_mode="threading")
