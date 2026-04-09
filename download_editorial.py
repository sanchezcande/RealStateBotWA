"""Descarga fotos de Unsplash para el rediseño editorial de la landing."""
import os
import requests

ACCESS_KEY = "pLg3EcvJzsYxC6vqbK-EylXBdOz7FODEUcVjRP4NB4I"
OUT_DIR = "/Users/msg1234/Desktop/PropBot/RealStateBotWA/static/img/editorial"
os.makedirs(OUT_DIR, exist_ok=True)

# query -> filename
QUERIES = {
    "hero": "luxury modern architecture exterior",
    "interior-1": "minimalist luxury living room",
    "interior-2": "elegant modern kitchen",
    "skyline": "buenos aires skyline architecture",
    "cityscape": "modern apartment building facade",
    "portrait-1": "professional woman real estate agent",
    "portrait-2": "professional businessman smiling",
    "portrait-3": "businesswoman portrait smiling",
    "detail": "architectural detail minimalist",
    "chair": "designer chair interior",
}

headers = {"Authorization": f"Client-ID {ACCESS_KEY}"}

for name, query in QUERIES.items():
    path = os.path.join(OUT_DIR, f"{name}.jpg")
    if os.path.exists(path):
        print(f"skip {name}")
        continue
    r = requests.get(
        "https://api.unsplash.com/photos/random",
        params={"query": query, "orientation": "landscape" if name != "hero" else "landscape"},
        headers=headers,
        timeout=30,
    )
    if r.status_code != 200:
        print(f"FAIL {name}: {r.status_code} {r.text[:200]}")
        continue
    data = r.json()
    img_url = data["urls"]["regular"]  # ~1080px wide
    credit = f"{data['user']['name']} (@{data['user']['username']})"
    print(f"{name}: {credit} — {data.get('description') or data.get('alt_description','')[:60]}")
    img = requests.get(img_url, timeout=60)
    with open(path, "wb") as f:
        f.write(img.content)
    print(f"  saved {path}")
print("done")
