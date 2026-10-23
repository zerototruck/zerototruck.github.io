#!/usr/bin/env python3
"""Read-only integrity check for the public wallet addresses.
Stdlib only. Writes nothing, runs no shell, uses no eval. Only HTTPS GETs to URLs you list in config.json.
Checks: 1) address format + checksum  2) the QR payload is exactly the address (or scheme:address)  3) no unknown wallet-like strings in site files
        4) the LIVE site's config.json equals this repo's  5) every mirror page (gist, telegram, ...) contains every address
        6) wallets that share an address (ETH / BNB / USDT use one EVM address) are labelled with different networks.
The QR IMAGES are decoded by scripts/verify_qr.py. A valid format is not proof that the wallet is yours or can receive on that network.
Exit 1 on any mismatch (GitHub then emails you). Unreachable URLs only warn."""
import base64, hashlib, json, re, sys, pathlib, urllib.request
R = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ztlib as Z
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"; BECH = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
M64 = (1 << 64) - 1
OFF = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
rol = lambda x, n: ((x << n) | (x >> (64 - n))) & M64 if n else x

def keccak256(m):
    def f(A):
        r = 1
        for _ in range(24):
            C = [A[x] ^ A[x + 5] ^ A[x + 10] ^ A[x + 15] ^ A[x + 20] for x in range(5)]
            D = [C[(x - 1) % 5] ^ rol(C[(x + 1) % 5], 1) for x in range(5)]
            A = [A[i] ^ D[i % 5] for i in range(25)]
            B = [0] * 25
            for x in range(5):
                for y in range(5):
                    B[y + 5 * ((2 * x + 3 * y) % 5)] = rol(A[x + 5 * y], OFF[x][y])
            A = [B[x + 5 * y] ^ (~B[(x + 1) % 5 + 5 * y] & B[(x + 2) % 5 + 5 * y]) for y in range(5) for x in range(5)]
            for j in range(7):
                if r & 1: A[0] ^= 1 << ((1 << j) - 1)
                r = ((r << 1) ^ (0x71 if r & 0x80 else 0)) & 0xFF
        return A
    m = bytearray(m) + b"\x01"
    while len(m) % 136: m.append(0)
    m[-1] |= 0x80
    A = [0] * 25
    for o in range(0, len(m), 136):
        for i in range(17): A[i] ^= int.from_bytes(m[o + 8 * i:o + 8 * i + 8], "little")
        A = f(A)
    return b"".join(A[i].to_bytes(8, "little") for i in range(4))

def b58(s, n):
    v = 0
    for ch in s: v = v * 58 + B58.index(ch)
    return v.to_bytes(n, "big")

def bech32_ok(ad):
    hrp, _, data = ad.rpartition("1"); d = [BECH.index(c) for c in data]
    g = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]; c = 1
    for v in [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp] + d:
        t = c >> 25; c = (c & 0x1ffffff) << 5 ^ v
        for i in range(5):
            if (t >> i) & 1: c ^= g[i]
    return c == 1 and hrp == "bc"

def valid(ad, kind=None):
    import hashlib
    try:
        if kind == "xrp" or (kind is None and ad[0] == "r" and len(ad) <= 35 and Z.xrp_ok(ad)): return Z.xrp_ok(ad), "xrp classic address (checksum)"
        if kind in ("tron", "tron_trc20") or (kind is None and ad[0] == "T" and len(ad) == 34 and Z.tron_ok(ad)): return Z.tron_ok(ad), "tron base58check"
        if ad.startswith("bc1"): return bech32_ok(ad.lower()) and ad == ad.lower(), "bitcoin bech32"
        if ad.startswith("0x"):
            if not re.fullmatch(r"0x[0-9a-fA-F]{40}", ad): return False, "evm format"
            body = ad[2:]
            if body in (body.lower(), body.upper()): return True, "evm (no checksum case)"
            h = keccak256(body.lower().encode()).hex()
            return all(c == (c.upper() if int(h[i], 16) > 7 else c.lower()) for i, c in enumerate(body)), "evm EIP-55"
        if ad[0] == "D" and len(ad) == 34:
            raw = b58(ad, 25); return hashlib.sha256(hashlib.sha256(raw[:-4]).digest()).digest()[:4] == raw[-4:], "dogecoin base58check"
        return len(b58(ad, 32)) == 32, "solana 32-byte key"
    except Exception: return False, "undecodable"

class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"): raise ValueError(f"refusing redirect to a non-https address: {newurl[:60]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def fetch(url):
    if not url.startswith("https://"): raise ValueError("https only")
    op = urllib.request.build_opener(_HttpsOnlyRedirect)
    with op.open(urllib.request.Request(url, headers={"User-Agent": "zt-verify"}), timeout=20) as r: return r.read(2_000_000).decode("utf-8", "ignore")

def csp_check(bad, warn):
    """If index.html has a CSP, every inline <script>/<style> must be covered by a hash in it (else the browser blocks it)."""
    html = (R / "index.html").read_text(errors="ignore")
    m = re.search(r"""http-equiv=["']Content-Security-Policy["'][^>]*content="([^"]*)\"""", html, re.I)
    if not m:
        warn.append("index.html has no Content-Security-Policy; run `python3 scripts/setup.py --localize`, then `python3 scripts/add_csp.py`"); return
    pol = m.group(1)
    sha = lambda t: "'sha256-" + base64.b64encode(hashlib.sha256(t.encode("utf-8")).digest()).decode() + "'"
    blocks = re.findall(r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", html, re.S | re.I) + re.findall(r"<style[^>]*>(.*?)</style>", html, re.S | re.I)
    miss = [b for b in blocks if sha(b) not in pol]
    if miss: bad.append(f"CSP does not cover {len(miss)} inline script/style block(s) in index.html: run `python3 scripts/add_csp.py`")
    if re.search(r"unsafe-(inline|eval)|https?://|\*", pol): warn.append("CSP allows unsafe-inline/unsafe-eval or outside sources: it is weaker than intended")
    if not bad: print("OK   CSP present and matches inline code")


def main():
    cfg = json.loads((R / "config.json").read_text()); bad = []; warn = []
    assets = [a for a in cfg["assets"] if a.get("enabled") is not False]
    known = {a["address"].lower() for a in cfg["assets"]}
    for a in assets:
        ok, kind = valid(a["address"], (a.get("track") or {}).get("kind"))
        if not ok: bad.append(f"{a['id']}: invalid address ({kind})")
        pb = Z.qr_payload_problem(a)
        if pb: bad.append(f"{a['id']}: {pb}")
        if a.get("qr_image") and not (R / a["qr_image"]).is_file(): bad.append(f"{a['id']}: qr_image file missing: {a['qr_image']}")
        print(("OK  " if ok else "FAIL"), a["id"], kind)
    by_addr = {}
    for a in assets: by_addr.setdefault(a["address"].lower(), []).append(a)
    for group in by_addr.values():
        if len(group) > 1:
            nets = [(g.get("symbol"), g.get("network")) for g in group]
            if len(set(nets)) != len(nets): bad.append(f"{'/'.join(g['id'] for g in group)} share one address AND the same network label: copy/paste mistake?")
            else: print("NOTE ", "/".join(g["id"] for g in group), "share one address on different networks: the page tells people to check the network")
    pat = re.compile(r"\bbc1[0-9a-z]{20,}\b|\b0x[0-9a-fA-F]{40}\b|\bD[1-9A-HJ-NP-Za-km-z]{33}\b|\bT[1-9A-HJ-NP-Za-km-z]{33}\b|\br[1-9A-HJ-NP-Za-km-z]{24,34}\b")
    sol = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{43,44}\b")
    for p in [R / "index.html", R / "app.js", R / "styles.css", *sorted((R / "data").glob("*.json"))]:
        txt = p.read_text(errors="ignore")
        if p.suffix != ".json": txt_sol = re.sub(r"sha256-[A-Za-z0-9+/=]+", "", txt)
        else: txt_sol = ""
        for s in pat.findall(txt) + (sol.findall(txt_sol) if p.suffix != ".json" else []):
            if s.lower() not in known: bad.append(f"{p.name}: unknown wallet-like string {s}")
    v = cfg.get("verify", {})
    if v.get("live_site"):
        try:
            live = json.loads(fetch(v["live_site"].rstrip("/") + "/config.json"))
            f = lambda c: sorted((a["id"], a["address"], a.get("qr_uri", "")) for a in c["assets"] if a.get("enabled") is not False)
            if f(live) != f(cfg): bad.append("LIVE site config.json differs from this repo")
            else: print("OK   live site matches")
        except Exception as e: warn.append(f"live site unreachable: {e}")
    for url in v.get("mirrors", []):
        try:
            t = fetch(url); miss = [a["id"] for a in assets if a["address"].lower() not in t.lower()]
            if miss: bad.append(f"mirror {url} is missing {miss}")
            else: print("OK   mirror", url)
        except Exception as e: warn.append(f"mirror unreachable {url}: {e}")
    csp_check(bad, warn)
    for w in warn: print("WARN", w)
    for b in bad: print("FAIL", b)
    sys.exit(1 if bad else 0)

if __name__ == "__main__": main()
