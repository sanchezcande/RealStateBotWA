"""Read Drive folder structure and update fotos_url in Google Sheet."""
import json, os, time
from dotenv import load_dotenv
load_dotenv("/Users/msg1234/Desktop/PropBot/RealStateBotWA/.env")

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# --- Auth ---
creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
sheet_id = os.environ["GOOGLE_SHEET_ID"]
scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(creds_json)
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)

PARENT_ID = "1OxKeUb-g54rLjAbAgoZF_W-v7cVu9kQY"

# --- List all folders recursively (parent > maybe intermediate > city > property) ---
print("Leyendo carpetas de Drive...")

def list_subfolders(parent_id):
    q = f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    return drive.files().list(q=q, spaces="drive", fields="files(id,name)", pageSize=200).execute().get("files", [])

drive_props = {}  # name -> folder_url

def scan_folder(folder_id, depth=0):
    """Recursively find property folders (leaf folders or folders inside city folders)."""
    subs = list_subfolders(folder_id)
    time.sleep(0.2)
    for f in subs:
        name = f["name"]
        # City-level folders contain " - Propiedades"
        if "Propiedades" in name or depth < 2:
            print(f"{'  ' * depth}  📁 {name}")
            scan_folder(f["id"], depth + 1)
        else:
            # This is a property folder
            url = f"https://drive.google.com/drive/folders/{f['id']}"
            drive_props[name] = url
            print(f"{'  ' * depth}  🏠 {name} -> {f['id']}")

scan_folder(PARENT_ID)

print(f"\nTotal carpetas de propiedades en Drive: {len(drive_props)}")

# --- Read sheet ---
ws = gc.open_by_key(sheet_id).get_worksheet_by_id(567871247)
headers = ws.row_values(1)
all_rows = ws.get_all_values()
id_col = headers.index("id")
titulo_col = headers.index("titulo")
fotos_col = headers.index("fotos_url")

# --- Match and build name variants for fuzzy matching ---
def normalize(s):
    """Normalize for comparison: lowercase, strip accents, remove special chars."""
    import unicodedata
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")  # strip accents
    s = s.lower().strip()
    for ch in ".,;:-_'\"()[]{}!?":
        s = s.replace(ch, "")
    return " ".join(s.split())

def find_drive_match(titulo, prop_id):
    """Find matching Drive folder for a property."""
    # Exact match
    if titulo in drive_props:
        return drive_props[titulo]
    # Normalized match
    norm_titulo = normalize(titulo)
    for name, url in drive_props.items():
        if normalize(name) == norm_titulo:
            return url
    # Partial match (title contains or is contained)
    for name, url in drive_props.items():
        n = normalize(name)
        if n in norm_titulo or norm_titulo in n:
            return url
    # Match by key words
    words = set(norm_titulo.split()) - {"en", "de", "la", "el", "los", "las", "y", "a", "del"}
    best_score = 0
    best_url = None
    for name, url in drive_props.items():
        n_words = set(normalize(name).split()) - {"en", "de", "la", "el", "los", "las", "y", "a", "del"}
        overlap = len(words & n_words)
        if overlap > best_score and overlap >= 2:
            best_score = overlap
            best_url = url
    return best_url

# --- Match each property ---
updates = []
unmatched = []

for row_idx, row in enumerate(all_rows[1:], start=2):
    prop_id = row[id_col]
    titulo = row[titulo_col]
    if not prop_id:
        continue

    match = find_drive_match(titulo, prop_id)
    if match:
        updates.append((row_idx, match, titulo))
    else:
        unmatched.append((prop_id, titulo))

print(f"\nMatched: {len(updates)}")
if unmatched:
    print(f"Sin match: {len(unmatched)}")
    for pid, t in unmatched:
        print(f"  {pid}: {t}")

# --- Update sheet ---
if updates:
    cells = []
    for row_num, url, titulo in updates:
        cells.append(gspread.Cell(row_num, fotos_col + 1, url))
        print(f"  Row {row_num}: {titulo[:40]} -> {url}")
    ws.update_cells(cells, value_input_option="USER_ENTERED")
    print(f"\nSheet actualizada: {len(updates)} fotos_url con links de Drive")
else:
    print("\nNo updates needed")

print("\nDONE!")
