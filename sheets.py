"""
Load property listings from Google Sheets.
Falls back to sample data if no credentials are configured.
Caches results for SHEET_CACHE_TTL seconds.
"""
import json
import time
import logging
from config import GOOGLE_SHEET_ID, GOOGLE_CREDENTIALS_JSON, SHEET_CACHE_TTL

logger = logging.getLogger(__name__)

_cache: dict = {"data": None, "ts": 0}

SAMPLE_LISTINGS = [
    {
        "id": "P001",
        "tipo": "Departamento",
        "operacion": "Venta",
        "precio": "USD 95,000",
        "direccion": "Av. Corrientes 1500, CABA",
        "ambientes": 2,
        "m2": 55,
        "descripcion": "Luminoso departamento a estrenar, piso alto, balcon, cochera opcional.",
    },
    {
        "id": "P002",
        "tipo": "Departamento",
        "operacion": "Alquiler",
        "precio": "ARS 280,000/mes",
        "direccion": "Palermo Soho, Thames 1200, CABA",
        "ambientes": 3,
        "m2": 80,
        "descripcion": "Amplio departamento amoblado con terraza. Expensas bajas.",
    },
    {
        "id": "P003",
        "tipo": "Casa",
        "operacion": "Venta",
        "precio": "USD 220,000",
        "direccion": "Olivos, Vicente Lopez, GBA Norte",
        "ambientes": 4,
        "m2": 180,
        "descripcion": "Casa con jardin y pileta, garage doble, barrio tranquilo.",
    },
    {
        "id": "P004",
        "tipo": "Local comercial",
        "operacion": "Alquiler",
        "precio": "USD 1,800/mes",
        "direccion": "Florida 850, Microcentro, CABA",
        "ambientes": None,
        "m2": 120,
        "descripcion": "Local sobre peatonal Florida, gran vidriera, ideal gastronomia o retail.",
    },
    {
        "id": "P005",
        "tipo": "PH",
        "operacion": "Venta",
        "precio": "USD 145,000",
        "direccion": "Villa Crespo, Av. Corrientes 5600, CABA",
        "ambientes": 3,
        "m2": 100,
        "descripcion": "PH con patio propio, muy luminoso, reciclado a nuevo.",
    },
]


def _fetch_from_sheets() -> list:
    """Fetch rows from Google Sheets using service account credentials."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(GOOGLE_SHEET_ID).sheet1
        rows = sheet.get_all_records()
        logger.info("Loaded %d listings from Google Sheets", len(rows))
        return rows
    except Exception as e:
        logger.warning("Could not load from Google Sheets: %s. Using sample data.", e)
        return SAMPLE_LISTINGS


def get_listings() -> list:
    """Return listings, using cache when fresh."""
    now = time.time()
    if _cache["data"] is not None and (now - _cache["ts"]) < SHEET_CACHE_TTL:
        return _cache["data"]

    if GOOGLE_SHEET_ID and GOOGLE_CREDENTIALS_JSON:
        data = _fetch_from_sheets()
    else:
        logger.info("No Google Sheets config — using sample listings.")
        data = SAMPLE_LISTINGS

    _cache["data"] = data
    _cache["ts"] = now
    return data


def format_listings_for_prompt(listings: list) -> str:
    lines = ["PROPIEDADES DISPONIBLES:"]
    for p in listings:
        parts = [f"[{p.get('id', '?')}] {p.get('tipo', '')} en {p.get('operacion', '')}"]
        parts.append(f"  Precio: {p.get('precio', 'Consultar')}")
        parts.append(f"  Ubicacion: {p.get('direccion', '')}")
        if p.get("ambientes"):
            parts.append(f"  Ambientes: {p.get('ambientes')} | m2: {p.get('m2', '?')}")
        else:
            parts.append(f"  m2: {p.get('m2', '?')}")
        parts.append(f"  Detalle: {p.get('descripcion', '')}")
        lines.append("\n".join(parts))
    return "\n\n".join(lines)
