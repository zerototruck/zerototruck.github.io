#!/usr/bin/env python3
"""Adds (or refreshes) a strict Content-Security-Policy <meta> tag in index.html. Stdlib only.

Run it AFTER `python3 scripts/setup.py --localize`, and again whenever you change an inline <script>/<style> in index.html.
  python3 scripts/add_csp.py            write the policy
  python3 scripts/add_csp.py --check    only report; changes nothing (exit 1 if the policy is missing or stale)
  python3 scripts/add_csp.py --force    write even if external resources remain (the browser will BLOCK them)

Resulting policy: everything must come from this site itself; the only inline code allowed is the exact inline
<script>/<style> blocks currently in index.html (matched by SHA-256 hash). Nothing from other websites can run.
Note: GitHub Pages cannot send HTTP headers, so a <meta> tag is the only way; `frame-ancestors` is not supported in <meta>."""
import argparse, base64, hashlib, json, pathlib, re, sys

R = pathlib.Path(__file__).resolve().parent.parent
INLINE_JS = re.compile(r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.S | re.I)
INLINE_CSS = re.compile(r"<style[^>]*>(.*?)</style>", re.S | re.I)
META = re.compile(r"""<meta\s+http-equiv=["']Content-Security-Policy["'][^>]*>\s*""", re.I)
TAG = re.compile(r"<(script|img|link|iframe|video|audio|source)\b[^>]*>", re.I)
ATTR = re.compile(r"""\b(src|href)\s*=\s*["']([^"']+)["']""", re.I)
CSS_URL = re.compile(r"""(?:url\(\s*|@import\s+)["']?((?:https?:)?//[^)"'\s;]+)""", re.I)


def sha(s):
    return "'sha256-" + base64.b64encode(hashlib.sha256(s.encode("utf-8")).digest()).decode() + "'"


def build(html):
    js = sorted({sha(m.group(1)) for m in INLINE_JS.finditer(html)})
    css = sorted({sha(m.group(1)) for m in INLINE_CSS.finditer(html)})
    parts = ["default-src 'none'", "script-src " + " ".join(["'self'"] + js), "style-src " + " ".join(["'self'"] + css),
             "img-src 'self' data:", "font-src 'self'", "connect-src 'self'", "base-uri 'none'", "form-action 'none'", "object-src 'none'"]
    return "; ".join(parts)


def external(html, css_texts):
    found = []
    for m in TAG.finditer(html):
        tag = m.group(0); a = ATTR.search(tag)
        if not a: continue
        url = a.group(2)
        if not re.match(r"(https?:)?//", url): continue
        if m.group(1).lower() == "link" and re.search(r"""rel=["'][^"']*(preconnect|dns-prefetch)""", tag, re.I): continue
        found.append(url)
    for name, text in css_texts.items():
        found += [f"{name}: {u}" for u in CSS_URL.findall(text)]
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true"); ap.add_argument("--force", action="store_true"); a = ap.parse_args()
    p = R / "index.html"; html = p.read_text(encoding="utf-8")
    css = {f.name: f.read_text(encoding="utf-8", errors="ignore") for f in [R / "styles.css", R / "assets/fonts.css"] if f.is_file()}
    ext = external(html, css)
    if ext:
        print("External resources still referenced (a strict policy would block them):")
        for u in ext: print("  -", u)
        if not a.force and not a.check:
            print("\nRun `python3 scripts/setup.py --localize` first (or use --force)."); sys.exit(1)
    try:
        g = json.loads((R / "config.json").read_text()).get("analytics", {}).get("goatcounter")
    except Exception: g = ""
    if g: print("NOTE: analytics.goatcounter is set; its script comes from another site and WILL be blocked. Self-host count.js or leave analytics off.")
    policy = build(html)
    tag = f'<meta http-equiv="Content-Security-Policy" content="{policy}">'
    cur = META.search(html)
    if a.check:
        ok = bool(cur) and cur.group(0).strip() == tag
        print("CSP is present and matches the page." if ok else "CSP is missing or out of date: run `python3 scripts/add_csp.py`.")
        sys.exit(0 if ok else 1)
    new = META.sub("", html)
    m = re.search(r"<meta\s+charset=[^>]*>", new, re.I) or re.search(r"<head[^>]*>", new, re.I)
    if not m: print("Could not find <head> in index.html"); sys.exit(1)
    new = new[:m.end()] + tag + new[m.end():]
    if new == html: print("CSP already up to date.")
    else: p.write_text(new, encoding="utf-8"); print("CSP written to index.html.")
    print("\nPolicy:\n  " + policy.replace("; ", ";\n  "))


if __name__ == "__main__": main()
