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

# Persistent data directory (Railway volume at /data, local fallback to cwd)
_DATA_DIR = "/data" if os.path.isdir("/data") else "."

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

# Facebook / Instagram Messenger
# Set PAGE_ACCESS_TOKEN in Railway env vars.
# Also subscribe the webhook to the page in Meta App Dashboard under
# Messenger and Instagram settings (subscribed_fields: messages, messaging_postbacks).
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
