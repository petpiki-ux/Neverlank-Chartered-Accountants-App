"""Live messaging/calls (Socket.IO) must be able to connect with the pinned Flask / Flask-SocketIO versions.
Regression: Flask 3.1 + Flask-SocketIO 5.3.6 raised "property 'session' of 'RequestContext' object has no setter"
on every connect, which broke Messages > Call on the live site."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nl_")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flask import Flask
from extensions import socketio
app = Flask(__name__); app.secret_key = "test"
socketio.init_app(app)
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

got = []
@socketio.on("ping_test")
def _ping(data): socketio.emit("pong_test", data)

try:
    cl = socketio.test_client(app)
    check(cl.is_connected(), "a client can open a Socket.IO connection")
    cl.emit("ping_test", {"n": 1})
    got = cl.get_received()
    check(any(m["name"] == "pong_test" for m in got), "a message sent to the server is handled and answered")
    cl.disconnect()
    check(not cl.is_connected(), "the client can disconnect cleanly")
except Exception as e:
    check(False, f"Socket.IO connect raised {type(e).__name__}: {e}")
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
