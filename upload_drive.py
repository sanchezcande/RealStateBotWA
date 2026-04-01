"""Create Drive folders (city > property), upload photos from esquelprop.com, update sheet."""
import json, os, requests, time
from dotenv import load_dotenv
load_dotenv("/Users/msg1234/Desktop/PropBot/RealStateBotWA/.env")

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

# --- Auth ---
creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
sheet_id = os.environ["GOOGLE_SHEET_ID"]
scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(creds_json)
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)

PARENT_ID = "1OxKeUb-g54rLjAbAgoZF_W-v7cVu9kQY"

# --- Clean up ALL old folders inside parent ---
print("Limpiando carpetas anteriores...")
old_q = f"'{PARENT_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
old_folders = drive.files().list(q=old_q, spaces="drive", fields="files(id,name)", pageSize=200).execute()
for f in old_folders.get("files", []):
    print(f"  Borrando: {f['name']}")
    drive.files().delete(fileId=f["id"]).execute()
    time.sleep(0.2)

# --- Read sheet ---
ws = gc.open_by_key(sheet_id).get_worksheet_by_id(567871247)
headers = ws.row_values(1)
all_rows = ws.get_all_values()
id_col = headers.index("id")
titulo_col = headers.index("titulo")
fotos_col = headers.index("fotos_url")

# Try to use ciudad column if it exists, otherwise use barrio
try:
    city_col = headers.index("ciudad")
except ValueError:
    city_col = headers.index("barrio")

print(f"Sheet: {len(all_rows)-1} propiedades")

# --- Get unique cities from data ---
cities = set()
for row in all_rows[1:]:
    city = row[city_col].strip()
    if not city:
        # Fallback: extract city from barrio
        barrio_col = headers.index("barrio")
        barrio = row[barrio_col].strip()
        # Extract city from "Barrio, Ciudad" format
        if "," in barrio:
            city = barrio.split(",")[-1].strip()
        elif "esquel" in barrio.lower():
            city = "Esquel"
        elif "trevelin" in barrio.lower():
            city = "Trevelin"
        elif "cholila" in barrio.lower():
            city = "Cholila"
        else:
            city = "Otras"
    cities.add(city)
print(f"Ciudades: {sorted(cities)}")

# --- Create city folders in Drive ---
city_folder_ids = {}
for city in sorted(cities):
    folder_name = f"{city} - Propiedades"
    f = drive.files().create(
        body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder", "parents": [PARENT_ID]},
        fields="id",
    ).execute()
    # Make folder publicly readable
    drive.permissions().create(fileId=f["id"], body={"type": "anyone", "role": "reader"}).execute()
    city_folder_ids[city] = f["id"]
    print(f"  Carpeta Drive: {folder_name}")
    time.sleep(0.3)

# --- Determine city for each property ---
def _get_city(row):
    city = row[city_col].strip()
    if city:
        return city
    barrio_col = headers.index("barrio")
    barrio = row[barrio_col].strip()
    if "," in barrio:
        return barrio.split(",")[-1].strip()
    bl = barrio.lower()
    if "esquel" in bl:
        return "Esquel"
    if "trevelin" in bl:
        return "Trevelin"
    if "cholila" in bl:
        return "Cholila"
    return "Otras"

# --- Create property folders, upload photos, update sheet ---
updates = []
errors = []
total = len(all_rows) - 1

for row_idx, row in enumerate(all_rows[1:], start=2):
    prop_id = row[id_col]
    titulo = row[titulo_col]
    current_fotos = row[fotos_col].strip()
    city = _get_city(row)

    if not prop_id:
        continue

    safe_title = titulo.replace("'", "").replace('"', "").replace("/", "-").strip()
    if not safe_title:
        safe_title = f"Propiedad {prop_id}"

    # Create property folder inside city folder
    city_parent = city_folder_ids.get(city, PARENT_ID)
    prop_folder = drive.files().create(
        body={"name": safe_title, "mimeType": "application/vnd.google-apps.folder", "parents": [city_parent]},
        fields="id",
    ).execute()
    prop_folder_id = prop_folder["id"]
    drive.permissions().create(fileId=prop_folder_id, body={"type": "anyone", "role": "reader"}).execute()
    folder_url = f"https://drive.google.com/drive/folders/{prop_folder_id}"

    # Upload current photo (from esquelprop.com URL) to Drive folder
    if current_fotos and current_fotos.startswith("http") and "drive.google.com" not in current_fotos:
        try:
            resp = requests.get(current_fotos, timeout=15)
            if resp.status_code == 200 and len(resp.content) > 1000:
                content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
                ext = current_fotos.rsplit(".", 1)[-1].split("?")[0][:4]
                media = MediaInMemoryUpload(resp.content, mimetype=content_type)
                drive.files().create(
                    body={"name": f"destacada.{ext}", "parents": [prop_folder_id]},
                    media_body=media,
                    fields="id",
                ).execute()
                print(f"  [{row_idx-1}/{total}] {safe_title[:45]} -> subida OK ({len(resp.content)//1024}KB)")
            else:
                print(f"  [{row_idx-1}/{total}] {safe_title[:45]} -> HTTP {resp.status_code}")
                errors.append(prop_id)
        except Exception as e:
            print(f"  [{row_idx-1}/{total}] {safe_title[:45]} -> error: {e}")
            errors.append(prop_id)
    else:
        print(f"  [{row_idx-1}/{total}] {safe_title[:45]} -> sin imagen externa")

    updates.append((row_idx, folder_url))
    time.sleep(0.4)

# --- Update sheet: fotos_url = Drive folder link ---
if updates:
    cells = []
    for row_num, url in updates:
        cells.append(gspread.Cell(row_num, fotos_col + 1, url))
    ws.update_cells(cells, value_input_option="USER_ENTERED")
    print(f"\nSheet actualizada: {len(updates)} fotos_url con links de Drive")

if errors:
    print(f"\nErrores: {errors}")

print(f"\nCarpeta Drive: https://drive.google.com/drive/folders/{PARENT_ID}")
print("Tu papá puede subir más fotos arrastrándolas a cada carpeta de propiedad.")
print("DONE!")
