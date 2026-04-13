import os
import sys
from dotenv import load_dotenv
import pytz

load_dotenv()

# Timezone used across the app
AR_TZ = pytz.timezone("America/Argentina/Buenos_Aires")

_REQUIRED_VARS = ["WHATSAPP_TOKEN", "PHONE_NUMBER_ID", "NOTIFY_NUMBER", "DEEPSEEK_API_KEY"]
_missing = [v for v in _REQUIRED_VARS if not os.environ.get(v)]
if _missing:
    print(f"ERROR: Missing required environment variables: {', '.join(_missing)}", file=sys.stderr)
    sys.exit(1)

WHATSAPP_TOKEN = os.environ["WHATSAPP_TOKEN"]
PHONE_NUMBER_ID = os.environ["PHONE_NUMBER_ID"]
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "realstate_webhook_token")
NOTIFY_NUMBER = os.environ["NOTIFY_NUMBER"]

DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = "deepseek-chat"

GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "1mlysMKdK1vQp4zZBlsrl4AY28ZrNKCjCPRSHNY-qmgc")
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")

SHEET_CACHE_TTL = int(os.environ.get("SHEET_CACHE_TTL", "60"))  # seconds

# Follow-up automation for inactive leads
FOLLOWUP_DAYS = int(os.environ.get("FOLLOWUP_DAYS", "3"))
FOLLOWUP_ENABLED = os.environ.get("FOLLOWUP_ENABLED", "true").lower() in ("true", "1", "yes")

# CRM Webhook
CRM_WEBHOOK_URL = os.environ.get("CRM_WEBHOOK_URL", "")
CRM_WEBHOOK_SECRET = os.environ.get("CRM_WEBHOOK_SECRET", "")

# Persistent data directory (Railway volume at /data, local fallback to cwd)
# os.path.ismount is more reliable than os.path.isdir — avoids false positives
# from Dockerfile "mkdir /data" which creates the dir in the ephemeral image layer.
# Override with DATA_DIR=/data in env vars if ismount gives false negative.
_DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if os.path.ismount("/data") else ".")

if _DATA_DIR == "/data":
    print(f"[config] ✔ Railway volume detected at /data — data will persist across deploys")
    os.makedirs("/data/uploads/photos", exist_ok=True)
    os.makedirs("/data/uploads/videos", exist_ok=True)
else:
    print(f"[config] ⚠ NO volume mounted at /data — using '{os.path.abspath(_DATA_DIR)}', DATA WILL BE LOST on redeploy!")
    print(f"[config]   isdir=/data: {os.path.isdir('/data')}, ismount=/data: {os.path.ismount('/data')}")

# Analytics dashboard
ANALYTICS_DB_PATH = os.environ.get("ANALYTICS_DB_PATH", os.path.join(_DATA_DIR, "analytics.db"))
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
DASHBOARD_PLAN = os.environ.get("DASHBOARD_PLAN", "starter")  # starter | pro | premium
BRANCH_NAME = os.environ.get("BRANCH_NAME", "")
DASHBOARD_SECRET_KEY = os.environ.get("DASHBOARD_SECRET_KEY", "change-me-in-production")
DASHBOARD_ADMIN_PASSWORD = os.environ.get("DASHBOARD_ADMIN_PASSWORD", "")

# Google Gemini AI (for video/image generation — see media_studio_gemini.py)
GOOGLE_AI_API_KEY = os.environ.get("GOOGLE_AI_API_KEY", "")

# Media Studio — FFmpeg + Ken Burns (free, no API costs)
MEDIA_MUSIC_PATH = os.environ.get("MEDIA_MUSIC_PATH", "")     # background music mp3
MEDIA_LOGO_PATH = os.environ.get("MEDIA_LOGO_PATH", "")       # logo overlay image
REALESRGAN_PATH = os.environ.get("REALESRGAN_PATH", "")       # path to realesrgan-ncnn-vulkan
MEDIA_VOICEOVER_VOICE = os.environ.get("MEDIA_VOICEOVER_VOICE", "es-AR-TomasNeural")  # edge-tts voice
MEDIA_UPLOAD_DIR = os.environ.get("MEDIA_UPLOAD_DIR", os.path.join(_DATA_DIR, "uploads"))

# MercadoPago
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "")
EXTRA_VIDEO_PRICE_ARS = int(os.environ.get("EXTRA_VIDEO_PRICE_ARS", "25000"))
BASE_URL = os.environ.get("BASE_URL", "")  # e.g. https://tu-app.up.railway.app
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "sanchezgcandelaria@gmail.com")

# Asset cache busting
import subprocess as _sp, time as _time
try:
    ASSET_VERSION = _sp.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=_sp.DEVNULL).decode().strip() + "." + str(int(_time.time()))
except Exception:
    ASSET_VERSION = str(int(_time.time()))

# Tokko Broker CRM integration
TOKKO_API_KEY = os.environ.get("TOKKO_API_KEY", "")
TOKKO_ENABLED = os.environ.get("TOKKO_ENABLED", "false").lower() in ("true", "1", "yes")

# Facebook / Instagram Messenger
# Set PAGE_ACCESS_TOKEN in Railway env vars.
# Also subscribe the webhook to the page in Meta App Dashboard under
# Messenger and Instagram settings (subscribed_fields: messages, messaging_postbacks).
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")

# Visit scheduling mode:
#   "notify" = Vera tells the client the agent will contact them, notifies NOTIFY_NUMBER with summary
#   "self"   = Vera schedules directly via calendar (original behavior)
VISIT_MODE = os.environ.get("VISIT_MODE", "notify")
