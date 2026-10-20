"""Image test: kya Gemini image bana sakta hai? Wikipedia photo? Background hata sakte hain? Results test_out/ me."""
import base64, io, json, os, re, sys, urllib.parse
from pathlib import Path
import requests

OUT = Path("test_out"); OUT.mkdir(exist_ok=True)
KEY = os.environ.get("GEMINI_API_KEY", "")
UA = {"User-Agent": "video-maker/1.0 (personal educational project)"}
report = []
def say(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); report.append(s)

PROMPTS = {
    "portrait": "Vintage 18th century copperplate engraving portrait of an Ottoman sultan with a large turban, fine black ink hatching on pure plain white background, centered, no text, no frame",
    "plant": "Botanical illustration of a coffee plant branch with red berries and green leaves, detailed vintage style, on pure plain white background, centered, no text",
}

def save_png(data, name):
    p = OUT / name; p.write_bytes(data); return p

# 1) which image models does this key see?
say("== 1. Gemini image models ==")
models = []
try:
    r = requests.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", headers={"x-goog-api-key": KEY}, timeout=60)
    say("list models HTTP", r.status_code)
    for m in r.json().get("models", []):
        n = m["name"].split("/")[-1]
        if "image" in n or "imagen" in n:
            models.append(n)
    say("image-capable names:", models)
except Exception as e:
    say("list error:", e)
for must in ["gemini-2.5-flash-image", "gemini-2.0-flash-preview-image-generation"]:
    if must not in models:
        models.append(must)

got = {}
for model in models:
    if model.startswith("imagen"):
        continue
    for key, prompt in PROMPTS.items():
        if key in got:
            continue
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}
        try:
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", headers={"x-goog-api-key": KEY}, json=body, timeout=180)
        except Exception as e:
            say(f"{model} {key}: error {e}"); continue
        if r.status_code != 200:
            say(f"{model} {key}: HTTP {r.status_code} {r.text[:140].replace(chr(10),' ')}"); continue
        ok = False
        for part in r.json().get("candidates", [{}])[0].get("content", {}).get("parts", []):
            d = part.get("inlineData") or part.get("inline_data")
            if d:
                save_png(base64.b64decode(d["data"]), f"gemini_{key}.png"); got[key] = f"gemini_{key}.png"; ok = True
        say(f"{model} {key}: {'IMAGE OK' if ok else 'no image in reply'}")
say("Gemini images:", "WORKS" if got else "NOT available on this key")

# 2) Pollinations
say("== 2. Pollinations ==")
for key, prompt in PROMPTS.items():
    try:
        r = requests.get("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt) + "?width=768&height=768&nologo=true&model=flux", timeout=120)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            save_png(r.content, f"pollinations_{key}.png"); say(f"pollinations {key}: ok {len(r.content)}B")
        else:
            say(f"pollinations {key}: HTTP {r.status_code}")
    except Exception as e:
        say(f"pollinations {key}: {e}")

# 3) Wikipedia real pictures
say("== 3. Wikipedia photos ==")
for t in ["Murad IV", "Coffea arabica"]:
    try:
        d = requests.get("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(t.replace(" ", "_")), headers=UA, timeout=30).json()
        src = (d.get("thumbnail") or {}).get("source", "")
        src = re.sub(r"/\d+px-", "/1000px-", src)
        if src and not src.endswith(".svg"):
            r = requests.get(src, headers=UA, timeout=60)
            save_png(r.content, "wiki_" + t.replace(" ", "_") + ".png"); say(f"wiki {t}: ok {len(r.content)}B")
        else:
            say(f"wiki {t}: no image")
    except Exception as e:
        say(f"wiki {t}: {e}")

# 4) cut-out + white sticker outline
say("== 4. Background removal (sticker) ==")
from PIL import Image, ImageFilter
def sticker(img, name):
    a = img.split()[-1]
    big = a.filter(ImageFilter.MaxFilter(25))
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste((255, 255, 255, 255), mask=big)
    out.alpha_composite(img)
    bg = Image.new("RGBA", img.size, (222, 200, 160, 255)); bg.alpha_composite(out)
    bg.convert("RGB").save(OUT / name)

def floodcut(img):
    """simple: make near-white edge-connected pixels transparent"""
    img = img.convert("RGBA"); w, h = img.size; px = img.load()
    seen = set(); stack = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    while stack:
        x, y = stack.pop()
        if (x, y) in seen or not (0 <= x < w and 0 <= y < h): continue
        seen.add((x, y)); r, g, b, _ = px[x, y]
        if r > 235 and g > 235 and b > 235:
            px[x, y] = (r, g, b, 0)
            stack += [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
    return img

rembg_ok = False
try:
    from rembg import remove
    rembg_ok = True
    say("rembg import ok")
except Exception as e:
    say("rembg not available:", e)

for f in sorted(OUT.glob("*.png")):
    if f.name.startswith("sticker_") or f.name.startswith("flood_"): continue
    try:
        img = Image.open(f).convert("RGBA")
        sticker(floodcut(img), "flood_" + f.name.replace(".png", ".jpg"))
        say("flood-fill sticker:", f.name)
        if rembg_ok:
            sticker(remove(img).convert("RGBA"), "sticker_" + f.name.replace(".png", ".jpg"))
            say("rembg sticker:", f.name)
    except Exception as e:
        say("cutout error", f.name, e)

(OUT / "report.txt").write_text("\n".join(report), encoding="utf-8")
say("DONE")
