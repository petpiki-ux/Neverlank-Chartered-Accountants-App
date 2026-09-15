import os
import sys


def _is_frozen():
    return getattr(sys, "frozen", False)


if _is_frozen():
    # Running as a PyInstaller-built .exe.
    # Bundled read-only resources (templates/css) live in the temp extraction dir.
    RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    # Writable data (database, uploads) must live next to the .exe, not in the
    # temp extraction dir, so it survives between runs.
    DATA_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    RESOURCE_DIR = os.path.abspath(os.path.dirname(__file__))
    DATA_DIR = RESOURCE_DIR

# Cloud hosting (e.g. Render) override: the app's code is redeployed fresh from
# git on every deploy, so writable data (database, uploads, editable document
# templates) must live on a separate persistent disk instead of next to the
# code. Set the DATA_DIR environment variable to that disk's mount path (e.g.
# "/var/data") in the hosting platform's dashboard to enable this. Local/.exe
# installs never set this, so their behaviour above is unchanged.
if os.environ.get("DATA_DIR"):
    DATA_DIR = os.environ["DATA_DIR"]

INSTANCE_DIR = os.path.join(DATA_DIR, "instance")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
TEMPLATE_DIR = os.path.join(RESOURCE_DIR, "templates")
STATIC_DIR = os.path.join(RESOURCE_DIR, "static")
# Read-only originals shipped with the app - only ever read from, used once to
# seed DOCUMENT_TEMPLATES_DATA_DIR below. Never written to (it's inside the
# read-only bundle when running as a frozen .exe).
DOCUMENT_TEMPLATES_SEED_DIR = os.path.join(RESOURCE_DIR, "document_templates")
# Writable, editable copy that the app actually serves downloads from and
# that the in-app Edit/New/Delete Document Templates screens modify. Lives
# next to the database/uploads so it survives updates and edits persist.
DOCUMENT_TEMPLATES_DATA_DIR = os.path.join(DATA_DIR, "document_templates_data")
# Backwards-compatible alias (older code referenced this name for the
# read-only bundled copy).
DOCUMENT_TEMPLATES_DIR = DOCUMENT_TEMPLATES_SEED_DIR


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "neverlank-dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(INSTANCE_DIR, 'neverlank.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = UPLOAD_DIR
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024  # 25 MB per upload
    ALLOWED_EXTENSIONS = {
        "pdf", "doc", "docx", "xls", "xlsx", "csv", "png", "jpg", "jpeg", "txt", "zip", "pptx"
    }
    TEMPLATE_FOLDER = TEMPLATE_DIR
    STATIC_FOLDER = STATIC_DIR
    DOCUMENT_TEMPLATES_SEED_DIR = DOCUMENT_TEMPLATES_SEED_DIR
    DOCUMENT_TEMPLATES_DATA_DIR = DOCUMENT_TEMPLATES_DATA_DIR
    DOCUMENT_TEMPLATES_DIR = DOCUMENT_TEMPLATES_DIR
