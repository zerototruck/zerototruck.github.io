#!/usr/bin/env python3
"""python3 scripts/setup.py --url https://YOU.github.io[/REPO]   fills the social-card URLs (run once)
python3 scripts/setup.py --localize                           downloads the fonts and the hero photo into assets/ and rewires the site
After --localize run:  python3 scripts/add_csp.py             (locks the page so it can only load its own files)
Stdlib only (Pillow optional, for WebP). --localize needs internet and edits files only after every download succeeded.
The QR library is NOT downloaded any more: it ships inside the project (assets/vendor/, hash-checked by scripts/verify_data.py)."""
import argparse, hashlib, io, json, pathlib, re, ssl, sys, urllib.error, urllib.parse, urllib.request

R = pathlib.Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
GF = "https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;700&display=swap"
FS = "https://api.fontshare.com/v2/css?f[]=general-sans@400,500,600,700&display=swap"
PHOTO = "https://images.pexels.com/photos/2199293/pexels-photo-2199293.jpeg?auto=compress&cs=tinysrgb&w=1800"
HOSTS = {"fonts.googleapis.com", "fonts.gstatic.com", "api.fontshare.com", "cdn.fontshare.com", "images.pexels.com"}
FONT_EXT = {"woff2", "woff", "ttf", "otf"}
LIMIT = {"css": 500_000, "font": 3_000_000, "photo": 6_000_000}
URL_OK = re.compile(r"^https://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(/[A-Za-z0-9._~-]+)*$")


class _Allowed(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).hostname not in HOSTS or not newurl.startswith("https://"): raise ValueError(f"refusing redirect to {newurl[:60]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def get(u, kind):
    u = "https:" + u if u.startswith("//") else u
    s = urllib.parse.urlsplit(u)
    if s.scheme != "https" or s.hostname not in HOSTS: raise ValueError(f"refusing to download from {s.hostname!r} (allowed: {sorted(HOSTS)})")
    with urllib.request.build_opener(_Allowed).open(urllib.request.Request(u, headers=UA), timeout=60) as r:
        b = r.read(LIMIT[kind] + 1)
    if len(b) > LIMIT[kind]: raise ValueError(f"download too large: {u[:70]}")
    return b


def font_css(u, files):
    def dl(m):
        url = m.group(1).strip("'\"")
        if url.startswith("data:"): return m.group(0)
        ext = urllib.parse.urlsplit("https:" + url if url.startswith("//") else url).path.rsplit(".", 1)[-1].lower()
        if ext not in FONT_EXT: raise ValueError(f"unexpected font file type: {ext!r}")
        fn = hashlib.sha1(url.encode()).hexdigest()[:10] + "." + ext
        files[fn] = get(url, "font"); return f"url(fonts/{fn})"
    return re.sub(r"url\(([^)]+)\)", dl, get(u, "css").decode("utf-8"))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--url"); ap.add_argument("--localize", action="store_true"); a = ap.parse_args()
    if a.url:
        u = a.url.rstrip("/")
        if not URL_OK.match(u): sys.exit("--url must look like https://yourname.github.io or https://yourname.github.io/repo (letters, digits, dots, dashes)")
        p = R / "index.html"; p.write_text(p.read_text(encoding="utf-8").replace("__SITE_URL__", u), encoding="utf-8")
        c = json.loads((R / "config.json").read_text(encoding="utf-8")); c["site"]["url"] = u
        (R / "config.json").write_text(json.dumps(c, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"); print("site url set")
    if a.localize:
        try:
            files = {}; css = font_css(GF, files) + "\n" + font_css(FS, files); img = get(PHOTO, "photo"); name = "hero-truck.jpg"
        except (ssl.SSLError, urllib.error.URLError) as e:
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                sys.exit("Certificate check failed (typical for python.org Python on macOS).\n"
                         "Fix: open Finder -> Applications -> Python 3.x -> double-click 'Install Certificates.command', then run this again.\n"
                         "Do NOT turn certificate checking off.")
            sys.exit(f"Download failed: {e}")
        (R / "assets/fonts").mkdir(parents=True, exist_ok=True)
        for fn, b in files.items(): (R / "assets/fonts" / fn).write_bytes(b)
        (R / "assets/fonts.css").write_text(css, encoding="utf-8"); (R / "assets/hero-truck.jpg").write_bytes(img)
        try:
            from PIL import Image
            Image.open(io.BytesIO(img)).convert("RGB").save(R / "assets/hero-truck.webp", "WEBP", quality=80); name = "hero-truck.webp"
        except Exception as e: print("WebP skipped:", e)
        p = R / "index.html"; h = p.read_text(encoding="utf-8")
        h = re.sub(r'<link rel="preconnect"[^>]*>|<link href="https://(fonts\.googleapis|api\.fontshare)[^>]*>', "", h)
        if "assets/fonts.css" not in h: h = h.replace('<link rel="stylesheet" href="styles.css">', '<link rel="stylesheet" href="assets/fonts.css"><link rel="stylesheet" href="styles.css">')
        p.write_text(h, encoding="utf-8")
        s = R / "styles.css"; s.write_text(re.sub(r'https://images\.pexels\.com/photos/2199293/[^")]+', "assets/" + name, s.read_text(encoding="utf-8")), encoding="utf-8")
        print("localized. Next: python3 scripts/add_csp.py")


if __name__ == "__main__":
    main()
