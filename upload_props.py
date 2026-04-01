"""One-time script: read Excel, update Google Sheet, optionally create Drive folders."""
import json, os, sys, time
from dotenv import load_dotenv
load_dotenv("/Users/msg1234/Desktop/PropBot/RealStateBotWA/.env")

import openpyxl
import gspread
from google.oauth2.service_account import Credentials

# --- Auth ---
creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
sheet_id = os.environ["GOOGLE_SHEET_ID"]
scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(creds_json)
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(creds)

# --- Read Excel ---
wb = openpyxl.load_workbook("/Users/msg1234/Downloads/Propiedades-Export-2026-March-30-1629.xlsx")
ws_xl = wb.active
# Row 2 = headers: id, Title, Excerpt, Image Featured, Ciudad, Ventas, precio, ubicacion, metros2
properties = []
for row_idx in range(3, ws_xl.max_row + 1):
    row = [c.value for c in ws_xl[row_idx]]
    if row[0] is None:
        continue
    properties.append({
        "id": str(row[0]),
        "titulo": row[1] or "",
        "image_url": (row[3] or "") if len(row) > 3 else "",
        "ciudad": (row[4] or "") if len(row) > 4 else "",
        "tipo_propiedad": (row[5] or "") if len(row) > 5 else "",
        "precio": row[6] if (len(row) > 6 and row[6]) else "",
        "ubicacion": (row[7] or "") if len(row) > 7 else "",
        "metros2": row[8] if (len(row) > 8 and row[8]) else "",
    })
print(f"Parsed {len(properties)} properties from Excel")

# --- Try Drive (optional — skip if API not enabled) ---
drive = None
fotos_urls = {}
try:
    import requests
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaInMemoryUpload
    drive = build("drive", "v3", credentials=creds)

    parent_folder_name = "PropBot - Fotos Propiedades"
    q = f"name='{parent_folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = drive.files().list(q=q, spaces="drive", fields="files(id,name)").execute()
    if results["files"]:
        parent_id = results["files"][0]["id"]
        print(f"Found parent folder: {parent_id}")
    else:
        meta = {"name": parent_folder_name, "mimeType": "application/vnd.google-apps.folder"}
        folder = drive.files().create(body=meta, fields="id").execute()
        parent_id = folder["id"]
        drive.permissions().create(
            fileId=parent_id, body={"type": "anyone", "role": "reader"}
        ).execute()
        print(f"Created parent folder: {parent_id}")

    errors = []
    for i, p in enumerate(properties):
        safe_title = p["titulo"][:60].replace("'", "").replace('"', "")
        prop_folder_name = f"{p['id']} - {safe_title}"
        search_q = (
            f"'{parent_id}' in parents and "
            f"name contains '{p['id']} -' and "
            f"mimeType='application/vnd.google-apps.folder' and trashed=false"
        )
        try:
            existing = drive.files().list(q=search_q, spaces="drive", fields="files(id)").execute()
        except Exception:
            existing = {"files": []}
        if existing["files"]:
            folder_id = existing["files"][0]["id"]
        else:
            meta = {
                "name": prop_folder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            }
            folder = drive.files().create(body=meta, fields="id").execute()
            folder_id = folder["id"]
            drive.permissions().create(
                fileId=folder_id, body={"type": "anyone", "role": "reader"}
            ).execute()
        folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
        fotos_urls[p["id"]] = folder_url
        if p["image_url"]:
            try:
                resp = requests.get(p["image_url"], timeout=15)
                if resp.status_code == 200:
                    ext = p["image_url"].rsplit(".", 1)[-1].split("?")[0][:4]
                    media = MediaInMemoryUpload(resp.content, mimetype=resp.headers.get("content-type", "image/jpeg"))
                    drive.files().create(body={"name": f"destacada.{ext}", "parents": [folder_id]}, media_body=media, fields="id").execute()
                    print(f"  [{i+1}/{len(properties)}] {p['id']} ok -> {folder_url}")
                else:
                    print(f"  [{i+1}/{len(properties)}] {p['id']} HTTP {resp.status_code}")
                    errors.append(p["id"])
            except Exception as e:
                print(f"  [{i+1}/{len(properties)}] {p['id']} error: {e}")
                errors.append(p["id"])
        else:
            print(f"  [{i+1}/{len(properties)}] {p['id']} sin imagen")
        time.sleep(0.3)
    if errors:
        print(f"Drive image errors: {errors}")
except Exception as e:
    print(f"Drive API not available ({e}), skipping folder creation. Using image URLs directly.")
    for p in properties:
        fotos_urls[p["id"]] = p["image_url"]

# --- Write to Google Sheet ---
sheet = gc.open_by_key(sheet_id)
ws = sheet.get_worksheet_by_id(567871247)
sheet_headers = ws.row_values(1)

existing_data = ws.get_all_values()
existing_ids = set()
if len(existing_data) > 1:
    id_col = sheet_headers.index("id")
    for row in existing_data[1:]:
        if row[id_col]:
            existing_ids.add(str(row[id_col]))

print(f"\nExisting IDs in sheet: {len(existing_ids)}")

rows_to_add = []
for p in properties:
    if p["id"] in existing_ids:
        print(f"  Skip {p['id']} (already in sheet)")
        continue

    titulo_lower = p["titulo"].lower()
    tipo_op = "Alquiler" if "alquiler" in titulo_lower else "Venta"

    precio = p["precio"]
    try:
        precio = int(float(str(precio).replace(",", "")))
    except (ValueError, TypeError):
        precio = str(precio) if precio else "Consultar"

    metros = p["metros2"]
    try:
        metros = int(float(str(metros).replace(",", "")))
    except (ValueError, TypeError):
        metros = str(metros) if metros else ""

    row_dict = {
        "id": p["id"],
        "titulo": p["titulo"],
        "precio_usd": precio,
        "tipo_operacion": tipo_op,
        "direccion": p["ubicacion"],
        "tipo_propiedad": p["tipo_propiedad"],
        "mt2_totales": metros,
        "barrio": p["ciudad"],
        "fotos_url": fotos_urls.get(p["id"], p["image_url"]),
    }

    row = [row_dict.get(h, "") for h in sheet_headers]
    rows_to_add.append(row)

if rows_to_add:
    ws.append_rows(rows_to_add, value_input_option="USER_ENTERED")
    print(f"\nAdded {len(rows_to_add)} properties to sheet")
else:
    print("\nNo new properties to add")

print("\nDONE!")
