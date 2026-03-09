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
        "titulo": "Departamento luminoso a estrenar en Palermo",
        "precio_usd": 95000,
        "tipo_operacion": "Venta",
        "tipo_propiedad": "Departamento",
        "ambientes": 2,
        "dormitorios": 1,
        "banos": 1,
        "suite": "No",
        "mt2_cubiertos": 55,
        "mt2_totales": 60,
        "barrio": "Palermo, CABA",
        "cochera": "No",
        "balcon": "Si",
        "patio_delantero": "No",
        "patio_trasero": "No",
        "terraza": "No",
        "jardin": "No",
        "pileta": "No",
        "quincho": "No",
        "calefon": "Si",
        "calefaccion": "Losa radiante",
        "aire_acondicionado": "Si",
        "gas_natural": "Si",
        "ascensor": "Si",
        "seguridad": "Portero eléctrico",
        "antiguedad_anos": 0,
        "estado": "A estrenar",
        "piso": "8",
        "orientacion": "Norte",
        "expensas_usd": 80,
        "apto_credito": "Si",
        "fotos_url": "",
        "descripcion": "Luminoso departamento a estrenar, piso alto con balcón. Edificio con ascensor y portero.",
    },
    {
        "id": "P002",
        "titulo": "Departamento amoblado con terraza en Palermo Soho",
        "precio_usd": 900,
        "tipo_operacion": "Alquiler",
        "tipo_propiedad": "Departamento",
        "ambientes": 3,
        "dormitorios": 2,
        "banos": 1,
        "suite": "No",
        "mt2_cubiertos": 80,
        "mt2_totales": 95,
        "barrio": "Palermo Soho, CABA",
        "cochera": "No",
        "balcon": "No",
        "patio_delantero": "No",
        "patio_trasero": "No",
        "terraza": "Si",
        "jardin": "No",
        "pileta": "No",
        "quincho": "No",
        "calefon": "Si",
        "calefaccion": "Split",
        "aire_acondicionado": "Si",
        "gas_natural": "Si",
        "ascensor": "Si",
        "seguridad": "Portero eléctrico",
        "antiguedad_anos": 5,
        "estado": "Muy bueno",
        "piso": "3",
        "orientacion": "Este",
        "expensas_usd": 60,
        "apto_credito": "No",
        "fotos_url": "",
        "descripcion": "Amplio departamento amoblado con terraza privada. Expensas bajas. Ideal para pareja o familia chica.",
    },
    {
        "id": "P003",
        "titulo": "Casa con pileta y jardín en Olivos",
        "precio_usd": 220000,
        "tipo_operacion": "Venta",
        "tipo_propiedad": "Casa",
        "ambientes": 5,
        "dormitorios": 3,
        "banos": 2,
        "suite": "Si",
        "mt2_cubiertos": 180,
        "mt2_totales": 400,
        "barrio": "Olivos, Vicente López",
        "cochera": "Si",
        "balcon": "No",
        "patio_delantero": "Si",
        "patio_trasero": "Si",
        "terraza": "No",
        "jardin": "Si",
        "pileta": "Si",
        "quincho": "Si",
        "calefon": "Si",
        "calefaccion": "Central a gas",
        "aire_acondicionado": "Si",
        "gas_natural": "Si",
        "ascensor": "No",
        "seguridad": "Alarma",
        "antiguedad_anos": 15,
        "estado": "Muy bueno",
        "piso": "PB",
        "orientacion": "Sur",
        "expensas_usd": 0,
        "apto_credito": "Si",
        "fotos_url": "",
        "descripcion": "Hermosa casa familiar con jardín, pileta y quincho. Garage doble. Barrio tranquilo y arbolado.",
    },
    {
        "id": "P004",
        "titulo": "PH con patio propio en Villa Crespo",
        "precio_usd": 145000,
        "tipo_operacion": "Venta",
        "tipo_propiedad": "PH",
        "ambientes": 3,
        "dormitorios": 2,
        "banos": 1,
        "suite": "No",
        "mt2_cubiertos": 90,
        "mt2_totales": 120,
        "barrio": "Villa Crespo, CABA",
        "cochera": "No",
        "balcon": "No",
        "patio_delantero": "No",
        "patio_trasero": "Si",
        "terraza": "No",
        "jardin": "No",
        "pileta": "No",
        "quincho": "No",
        "calefon": "Si",
        "calefaccion": "Split",
        "aire_acondicionado": "Si",
        "gas_natural": "Si",
        "ascensor": "No",
        "seguridad": "Portero eléctrico",
        "antiguedad_anos": 40,
        "estado": "Reciclado",
        "piso": "PB/1",
        "orientacion": "Norte",
        "expensas_usd": 40,
        "apto_credito": "Si",
        "fotos_url": "",
        "descripcion": "PH reciclado a nuevo con patio privado. Muy luminoso. A pasos del subte B.",
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
        sheet = client.open_by_key(GOOGLE_SHEET_ID).get_worksheet_by_id(567871247)
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


def _bool_field(value) -> str:
    """Normalize sheet boolean-like values to Sí/No/Consultar."""
    if value is None or value == "":
        return "Consultar"
    s = str(value).strip().lower()
    if s in ("si", "sí", "yes", "true", "1"):
        return "Sí"
    if s in ("no", "false", "0"):
        return "No"
    return str(value)


def format_listings_for_prompt(listings: list) -> str:
    lines = ["PROPIEDADES DISPONIBLES:"]
    for p in listings:
        pid = p.get("id", "?")
        titulo = p.get("titulo", "")
        tipo_op = p.get("tipo_operacion", "")
        tipo_prop = p.get("tipo_propiedad", "")
        precio = p.get("precio_usd", "Consultar")
        barrio = p.get("barrio", "")
        ambientes = p.get("ambientes", "")
        dorm = p.get("dormitorios", "")
        banos = p.get("banos", "")
        suite = _bool_field(p.get("suite"))
        mt2_c = p.get("mt2_cubiertos", "")
        mt2_t = p.get("mt2_totales", "")
        cochera = _bool_field(p.get("cochera"))
        balcon = _bool_field(p.get("balcon"))
        patio_del = _bool_field(p.get("patio_delantero"))
        patio_tras = _bool_field(p.get("patio_trasero"))
        terraza = _bool_field(p.get("terraza"))
        jardin = _bool_field(p.get("jardin"))
        pileta = _bool_field(p.get("pileta"))
        quincho = _bool_field(p.get("quincho"))
        calefon = _bool_field(p.get("calefon"))
        calefaccion = p.get("calefaccion", "")
        aa = _bool_field(p.get("aire_acondicionado"))
        gas = _bool_field(p.get("gas_natural"))
        ascensor = _bool_field(p.get("ascensor"))
        seguridad = p.get("seguridad", "")
        antiguedad = p.get("antiguedad_anos", "")
        estado = p.get("estado", "")
        piso = p.get("piso", "")
        orientacion = p.get("orientacion", "")
        expensas = p.get("expensas_usd", "")
        apto_credito = _bool_field(p.get("apto_credito"))
        fotos_url = str(p.get("fotos_url", "") or "").strip()
        direccion = str(p.get("direccion", "") or "").strip()
        descripcion = p.get("descripcion", "")

        precio_str = f"USD {precio:,}" if isinstance(precio, (int, float)) else str(precio)
        expensas_str = (f"USD {expensas}/mes" if isinstance(expensas, (int, float)) and expensas > 0
                        else ("Sin expensas" if expensas == 0 else str(expensas)))
        fotos_str = fotos_url if fotos_url else "Sin fotos cargadas"

        direccion_str = direccion if direccion else "Consultar"

        block = f"""[{pid}] {titulo}
  Operación: {tipo_op} | Tipo: {tipo_prop} | Barrio: {barrio} | Dirección: {direccion_str}
  Precio: {precio_str} | Expensas: {expensas_str} | Apto crédito: {apto_credito}
  Ambientes: {ambientes} | Dormitorios: {dorm} | Baños: {banos} | Suite: {suite}
  m² cubiertos: {mt2_c} | m² totales: {mt2_t} | Piso: {piso} | Orientación: {orientacion}
  Cochera: {cochera} | Balcón: {balcon} | Patio delantero: {patio_del} | Patio trasero: {patio_tras}
  Terraza: {terraza} | Jardín: {jardin} | Pileta: {pileta} | Quincho: {quincho}
  Calefón: {calefon} | Calefacción: {calefaccion} | Aire acond.: {aa} | Gas natural: {gas}
  Ascensor: {ascensor} | Seguridad: {seguridad} | Antigüedad: {antiguedad} años | Estado: {estado}
  Fotos: {fotos_str}
  Descripción: {descripcion}"""
        lines.append(block)
    return "\n\n".join(lines)
