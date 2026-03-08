import os
from dotenv import load_dotenv

load_dotenv()

WHATSAPP_TOKEN = os.environ["WHATSAPP_TOKEN"]
PHONE_NUMBER_ID = os.environ["PHONE_NUMBER_ID"]
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "realstate_webhook_token")
NOTIFY_NUMBER = os.environ["NOTIFY_NUMBER"]

DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "1EgEk6SXEf0LE3eKv7-jYm2L8K9gu5BiK")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")

SHEET_CACHE_TTL = int(os.environ.get("SHEET_CACHE_TTL", "300"))  # seconds
