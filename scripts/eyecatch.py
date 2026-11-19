"""noteアイキャッチの案を Cloudflare Workers AI でまとめて生成する。

使い方: python scripts/eyecatch.py [依頼ファイル]
  引数なし → requests/*.json のうち、results/<名前>/done が無いものを全部処理。

依頼ファイル（JSON）の例:
{
  "title": "SAM GRIFFIN",          # 画像に入れる文字（英字推奨。AIは日本語を崩しやすい）
  "colors": ["#c8102e", "#f2e6c9", "#1d1d1b"],   # パッケージから取り出した色
  "styles": ["vintage", "swiss"],  # 省略時は STYLES 全部
  "models": ["klein", "schnell"],  # 省略時は両方
  "seeds": 1,                      # 1画風あたりの枚数
  "notes": ""                      # 追加の指示（英語）
}
出力: results/<名前>/ に 各案の jpg、一覧画像 sheet.jpg、log.json
"""
import base64, glob, io, json, os, random, sys, time, urllib.request, urllib.error, uuid

from PIL import Image, ImageDraw

ACC = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
TOK = os.environ.get("CLOUDFLARE_API_TOKEN", "")
W, H = 1280, 670

MODELS = {
    "klein": "@cf/black-forest-labs/flux-2-klein-4b",   # 幅・高さ指定可（multipart）
    "schnell": "@cf/black-forest-labs/flux-1-schnell",  # 正方形 → 横長に切り出し
}

STYLES = {
    "vintage": "1960s American sporting-goods catalog typography, slightly distressed offset print, cream paper",
    "swiss": "bold Swiss modernist poster, strong grid, simple geometric shapes",
    "signpaint": "hand-painted sign lettering, brush strokes, weathered wood board texture",
    "screenprint": "retro two-color screen print with halftone dots and slight misregistration",
    "deco": "art deco poster lettering with fine line ornaments",
    "letterpress": "woodblock letterpress type, ink texture, uneven impression",
    "seventies": "1970s groovy rounded display type, warm stripes",
    "minimal": "minimal flat design, generous negative space, subtle paper grain",
}

BASE = ("Wide blog header logo design. The text \"{title}\" is the main element, very large and bold, "
        "filling most of the canvas, spelled exactly, highly legible even when scaled down. {sub}Style: {style}. "
        "Color palette limited to {colors}. Abstract or typographic only: no fishing lures, no fish, "
        "no product, no brand logos, no photographs, no extra words. {notes}")


def call(model, prompt, seed):
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACC}/ai/run/{MODELS[model]}"
    if model == "klein":
        b = uuid.uuid4().hex
        fields = {"prompt": prompt, "width": "1280", "height": "672", "seed": str(seed)}
        body = b"".join(
            f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode() for k, v in fields.items()
        ) + f"--{b}--\r\n".encode()
        ctype = f"multipart/form-data; boundary={b}"
    else:
        body = json.dumps({"prompt": prompt, "steps": 8, "seed": seed}).encode()
        ctype = "application/json"
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {TOK}", "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read()[:500].decode('utf-8', 'replace')}"
    img = (data.get("result") or {}).get("image")
    if not img:
        return None, json.dumps(data)[:500]
    return Image.open(io.BytesIO(base64.b64decode(img))).convert("RGB"), None


def fit(im):
    """横長 1280x670 に合わせる（足りない比率は中央を切り出す）"""
    r = W / H
    w, h = im.size
    if w / h > r:
        nw = int(h * r); im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = int(w / r); im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    return im.resize((W, H), Image.LANCZOS)


def sheet(items, path):
    tw, th, cols = 640, 335, 2
    rows = (len(items) + cols - 1) // cols
    s = Image.new("RGB", (tw * cols, (th + 24) * rows), "white")
    d = ImageDraw.Draw(s)
    for i, (label, im) in enumerate(items):
        x, y = (i % cols) * tw, (i // cols) * (th + 24)
        s.paste(im.resize((tw, th)), (x, y + 24))
        d.text((x + 6, y + 5), label, fill="black")
    s.save(path, quality=85)


def run(reqfile):
    name = os.path.splitext(os.path.basename(reqfile))[0]
    req = json.load(open(reqfile, encoding="utf-8"))
    out = f"results/{name}"
    os.makedirs(out, exist_ok=True)
    styles = req.get("styles") or list(STYLES)
    models = req.get("models") or list(MODELS)
    log, items = [], []
    for st in styles:
        for m in models:
            for k in range(int(req.get("seeds", 1))):
                seed = random.randint(1, 2**31 - 1)
                sub = f'A smaller second line reads "{req["subtitle"]}". ' if req.get("subtitle") else ""
                prompt = BASE.format(title=req["title"], sub=sub, style=STYLES.get(st, st),
                                     colors=", ".join(req.get("colors", [])) or "a restrained vintage palette",
                                     notes=req.get("notes", ""))
                t = time.time()
                im, err = call(m, prompt, seed)
                label = f"{len(items)+1:02d}_{st}_{m}"
                rec = {"id": label, "model": m, "style": st, "seed": seed, "prompt": prompt,
                       "sec": round(time.time() - t, 1), "error": err}
                if im:
                    rec["raw_size"] = im.size
                    im = fit(im)
                    im.save(f"{out}/{label}.jpg", quality=90)
                    items.append((label, im))
                log.append(rec)
                print(label, "OK" if im else err, flush=True)
    if items:
        sheet(items, f"{out}/sheet.jpg")
    json.dump(log, open(f"{out}/log.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(f"{out}/done", "w").write(time.strftime("%Y-%m-%d %H:%M:%S"))


if __name__ == "__main__":
    files = sys.argv[1:] or [f for f in sorted(glob.glob("requests/*.json"))
                             if not os.path.exists(f"results/{os.path.splitext(os.path.basename(f))[0]}/done")]
    import traceback
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        os.makedirs(f"results/{name}", exist_ok=True)
        open(f"results/{name}/status.txt", "w").write(
            f"started {time.strftime('%H:%M:%S')} token_set={bool(TOK)} account_set={bool(ACC)}\n")
        try:
            run(f)
        except Exception:
            open(f"results/{name}/status.txt", "a").write(traceback.format_exc())
