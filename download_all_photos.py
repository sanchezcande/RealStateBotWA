"""Download ALL photos from esquelprop.com organized by city > property."""
import os, re, requests, time
from bs4 import BeautifulSoup

BASE = "/Users/msg1234/Desktop/Esquel Propiedades"

# Property data: (id, titulo, ciudad, page_url)
PROPERTIES = [
    ("EP001", "Casa 3 ambientes Yrigoyen y Don Bosco", "Esquel", "https://esquelprop.com/propiedad/casa-3-ambientes-yrigoyen-y-don-bosco/"),
    ("EP002", "Casa 2 dormitorios - Ruta 259", "Esquel", "https://esquelprop.com/propiedad/casa-2-dormitorios-ruta-259/"),
    ("EP003", "Casa en venta en Bo Baden - Esquel", "Esquel", "https://esquelprop.com/propiedad/casa-en-venta-en-bo-baden-esquel/"),
    ("EP004", "Casa 6 ambientes - Esquel", "Esquel", "https://esquelprop.com/propiedad/casa-6-ambientes-esquel/"),
    ("EP005", "Casa 3 dormitorios - Esquel", "Esquel", "https://esquelprop.com/propiedad/casa-3-domitorios-esquel/"),
    ("EP006", "Gran casa en Villa Ayelen - Esquel", "Esquel", "https://esquelprop.com/propiedad/gran-casa-en-villa-ayelen-esquel/"),
    ("EP007", "4 casas a construir en Laderas del Sol", "Esquel", "https://esquelprop.com/propiedad/4-casas-a-construir-en-laderas-del-sol/"),
    ("EP008", "3 departamentos en un mismo lote", "Esquel", "https://esquelprop.com/propiedad/3-departamentos-en-un-mismo-lote/"),
    ("EP009", "Complejo Habitacional", "Esquel", "https://esquelprop.com/propiedad/complejo-habitacional/"),
    ("EP010", "Alquiler - Juntas o separadas", "Esquel", "https://esquelprop.com/propiedad/alquiler-juntas-o-separadas/"),
    ("EP011", "Galpon multiproposito en Esquel", "Esquel", "https://esquelprop.com/propiedad/galpon-multiproposito-en-venta-en-esquel-ubicacion-estrategica/"),
    ("EP012", "Lote de 1500 mts2 - Villa Ayelen", "Esquel", "https://esquelprop.com/propiedad/lote-de-1-500-mts2-villa-ayelen/"),
    ("EP013", "Terreno de 800 mts2 - Villa Ayelen", "Esquel", "https://esquelprop.com/propiedad/terreno-de-800-mts2-villa-ayelen/"),
    ("EP014", "Lote de 2000 mts2 - Esquel", "Esquel", "https://esquelprop.com/propiedad/lote-de-2000-mts2-esquel/"),
    ("EP015", "Lote 375 mts2 - Avellaneda y Pioneros", "Esquel", "https://esquelprop.com/propiedad/lote-300-mts2-avellaneda-y-pioneros/"),
    ("EP016", "5 Lotes - Esquel", "Esquel", "https://esquelprop.com/propiedad/5-lotes-esquel/"),
    ("EP017", "Lote 800 mts2 - Loteo Los Coihues", "Esquel", "https://esquelprop.com/propiedad/lote-800-mts2-en-loteo-los-coihues-esquel/"),
    ("EP018", "Fraccion de 3900 mts2 - Esquel", "Esquel", "https://esquelprop.com/propiedad/fraccion-4-900-mts2-esquel/"),
    ("EP019", "Lote 870 mts2 - En esquina", "Trevelin", "https://esquelprop.com/propiedad/lote-870-mts2-en-esquina/"),
    ("EP020", "Lotes en Venta en Loteo Nueva Gales - Trevelin", "Trevelin", "https://esquelprop.com/propiedad/lotes-en-venta-en-loteo-nueva-gales-trevelin/"),
    ("EP021", "Fraccion de 5000 m2 en la entrada a Trevelin", "Trevelin", "https://esquelprop.com/propiedad/fraccion-de-5-000-m%c2%b2-en-la-entrada-a-trevelin/"),
    ("EP022", "1 Hectarea - Los Cipreses", "Trevelin", "https://esquelprop.com/propiedad/1-hectarea-los-cipreses/"),
    ("EP023", "Fraccion 1,5 has en Los Cipreses", "Trevelin", "https://esquelprop.com/propiedad/15-ha-con-agua-frente-a-psicultura/"),
    ("EP024", "Fraccion 2,40 ha Esquel", "Esquel", "https://esquelprop.com/propiedad/fraccion-2-40-hectarea-esquel/"),
    ("EP025", "Fracciones de 3 ha Nido de Condores", "Esquel", "https://esquelprop.com/propiedad/fracciones-de-3-htas-nido-de-condores-ruta-259-esquel-trevelin/"),
    ("EP026", "3 hta en Cholila", "Cholila", "https://esquelprop.com/propiedad/3-hta-en-cholila/"),
    ("EP027", "3 hta en venta - Cholila, Chubut", "Cholila", "https://esquelprop.com/propiedad/3-hta-en-venta-cholila-chubut/"),
]

def safe_name(s):
    """Remove problematic chars for folder names."""
    s = s.replace("/", "-").replace("\\", "-").replace(":", "-")
    s = s.replace('"', "").replace("'", "").replace("?", "").replace("*", "")
    return s.strip()

def scrape_photos(url):
    """Get all photo URLs from a property page."""
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        urls = set()

        # Gallery images (most common patterns on this site)
        for img in soup.find_all("img"):
            src = img.get("src", "") or img.get("data-src", "") or img.get("data-lazy-src", "")
            srcset = img.get("srcset", "") or img.get("data-srcset", "")
            for s in [src] + srcset.split(","):
                s = s.strip().split(" ")[0]
                if s and "esquelprop.com" in s and any(ext in s.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                    # Skip tiny thumbnails
                    if "-150x150" in s or "-100x" in s or "placeholder" in s.lower():
                        continue
                    # Try to get largest version by removing size suffix
                    clean = re.sub(r'-\d+x\d+(?=\.\w+)', '', s)
                    urls.add(clean)

        # Also check <a> links to full-size images
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "esquelprop.com" in href and any(ext in href.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                urls.add(href)

        # Background images in style attributes
        for el in soup.find_all(style=True):
            bg_urls = re.findall(r'url\(["\']?(https?://[^"\')\s]+\.(?:jpg|jpeg|png|webp)[^"\')\s]*)', el["style"], re.I)
            for u in bg_urls:
                if "esquelprop.com" in u:
                    clean = re.sub(r'-\d+x\d+(?=\.\w+)', '', u)
                    urls.add(clean)

        # Filter out logos, icons, etc.
        filtered = []
        for u in urls:
            lower = u.lower()
            if any(skip in lower for skip in ["logo", "icon", "favicon", "avatar", "widget", "banner-ad"]):
                continue
            filtered.append(u)

        return sorted(set(filtered))
    except Exception as e:
        print(f"  Error scraping {url}: {e}")
        return []


def download_photo(url, path):
    """Download a photo to local path."""
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code == 200 and len(resp.content) > 500:
            with open(path, "wb") as f:
                f.write(resp.content)
            return len(resp.content)
    except Exception as e:
        print(f"    Download error: {e}")
    return 0


# --- Clean up old messy folders ---
import shutil
if os.path.exists(BASE):
    print(f"Borrando carpeta vieja: {BASE}")
    shutil.rmtree(BASE)

# --- Create fresh structure and download ---
os.makedirs(BASE, exist_ok=True)
total_photos = 0
total_props = len(PROPERTIES)

for i, (prop_id, titulo, ciudad, page_url) in enumerate(PROPERTIES, 1):
    city_folder = os.path.join(BASE, f"{ciudad} - Propiedades")
    prop_folder = os.path.join(city_folder, safe_name(titulo))
    os.makedirs(prop_folder, exist_ok=True)

    print(f"\n[{i}/{total_props}] {titulo}")
    print(f"  Scraping: {page_url}")

    photos = scrape_photos(page_url)
    print(f"  Encontradas: {len(photos)} fotos")

    for j, photo_url in enumerate(photos, 1):
        ext = photo_url.rsplit(".", 1)[-1].split("?")[0][:4].lower()
        if ext not in ("jpg", "jpeg", "png", "webp"):
            ext = "jpg"
        filename = f"foto_{j}.{ext}" if j > 1 else f"destacada.{ext}"
        filepath = os.path.join(prop_folder, filename)
        size = download_photo(photo_url, filepath)
        if size > 0:
            total_photos += 1
            print(f"    {filename} ({size // 1024} KB)")
        else:
            # Remove empty file
            if os.path.exists(filepath):
                os.remove(filepath)

    time.sleep(0.3)

print(f"\n{'='*50}")
print(f"LISTO! {total_photos} fotos descargadas en {total_props} propiedades")
print(f"Carpeta: {BASE}")
print(f"\nAhora arrastrá cada carpeta de propiedad a la carpeta correspondiente en Drive.")
