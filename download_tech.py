"""Descarga fotos tech/software de Unsplash para la landing."""
import os
import requests

ACCESS_KEY = "pLg3EcvJzsYxC6vqbK-EylXBdOz7FODEUcVjRP4NB4I"
OUT_DIR = "/Users/msg1234/Desktop/PropBot/RealStateBotWA/static/img/editorial"
os.makedirs(OUT_DIR, exist_ok=True)

# nombre -> (query, orientation)
QUERIES = {
    "tech-abstract-1": ("dark abstract technology gradient neon", "landscape"),
    "tech-abstract-2": ("abstract data visualization dark blue", "landscape"),
    "tech-abstract-3": ("dark bokeh lights minimal technology", "landscape"),
    "workspace-1":     ("minimal desk laptop dark mode", "landscape"),
    "workspace-2":     ("laptop analytics dashboard charts screen", "landscape"),
    "screen-code":     ("code editor screen dark", "landscape"),
    "screen-phone":    ("phone chat messaging app dark", "portrait"),
    "data-chart":      ("financial chart stock market screen", "landscape"),
    "texture-grain":   ("paper grain texture minimal beige", "landscape"),
    "night-city":      ("night city lights abstract bokeh", "landscape"),
    "team-tech":       ("tech team startup laptop working", "landscape"),
    "server-room":     ("server room blue light minimal", "landscape"),
}

headers = {"Authorization": f"Client-ID {ACCESS_KEY}"}

for name, (query, orientation) in QUERIES.items():
    path = os.path.join(OUT_DIR, f"{name}.jpg")
    if os.path.exists(path):
        print(f"skip {name}")
        continue
    r = requests.get(
        "https://api.unsplash.com/photos/random",
        params={"query": query, "orientation": orientation},
        headers=headers,
        timeout=30,
    )
    if r.status_code != 200:
        print(f"FAIL {name}: {r.status_code} {r.text[:200]}")
        continue
    data = r.json()
    img_url = data["urls"]["regular"]
    credit = f"{data['user']['name']} (@{data['user']['username']})"
    print(f"{name}: {credit}")
    img = requests.get(img_url, timeout=60)
    with open(path, "wb") as f:
        f.write(img.content)
    print(f"  saved {path}")
print("done")
