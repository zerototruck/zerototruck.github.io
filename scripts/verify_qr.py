#!/usr/bin/env python3
"""Reads every wallet QR image, DECODES it and compares the result with the address in config.json.
A QR that points anywhere else (stale, swapped, damaged) makes this fail. Writes nothing, sends nothing.

  python3 scripts/verify_qr.py                 decode assets/qr/*.png
  python3 scripts/verify_qr.py --generated     also build fresh QR images with the website's own library (needs Node) and decode them
  python3 scripts/verify_qr.py --require       fail (instead of skipping) when no QR decoder is installed
Decoders: OpenCV (pip install opencv-python-headless) and/or the `zbarimg` program. Exit: 0 ok, 1 mismatch/problem, 3 no decoder."""
import argparse, json, pathlib, shutil, subprocess, sys, tempfile

R = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ztlib as Z

MAX_PNG = 300_000


def cv_decode(path):
    import cv2
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None: return []
    padded = cv2.copyMakeBorder(img, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
    variants = [img, padded] + ([cv2.resize(padded, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)] if max(img.shape) < 600 else [])
    dets = [cv2.QRCodeDetector()] + ([cv2.QRCodeDetectorAruco()] if hasattr(cv2, "QRCodeDetectorAruco") else [])
    for v in variants:
        found = []
        for d in dets:
            try: s = d.detectAndDecode(v)[0]
            except Exception: continue
            if s: found.append(s)
        if found: return found
    return []


def zbar_decode(path):
    r = subprocess.run(["zbarimg", "--quiet", "--raw", str(path)], capture_output=True, text=True, timeout=30)
    return [l for l in r.stdout.splitlines() if l]


def decoders():
    out = []
    try: import cv2; out.append(("opencv", cv_decode))  # noqa
    except Exception: pass
    if shutil.which("zbarimg"): out.append(("zbarimg", zbar_decode))
    return out


def png_problem(path):
    b = path.read_bytes()
    if len(b) > MAX_PNG: return "file is unexpectedly large"
    if b[:8] != b"\x89PNG\r\n\x1a\n" or b[12:16] != b"IHDR": return "not a PNG file"
    w, h = int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")
    if w != h or w < 200: return f"unexpected size {w}x{h} (a QR must be square and at least 200 px)"
    return None


def check_image(path, expected, decs):
    """-> list of problems for one image."""
    p = png_problem(path)
    if p: return [p]
    seen = set()
    for _, fn in decs:
        try: seen.update(fn(path))
        except Exception as e: return [f"decoder crashed: {type(e).__name__}: {e}"]
    if not seen: return ["no QR code could be decoded from this image"]
    wrong = sorted(s for s in seen if s != expected)
    return [f"QR contains {wrong[0]!r} but the wallet payload is {expected!r}"] if wrong else []


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--generated", action="store_true"); ap.add_argument("--require", action="store_true"); a = ap.parse_args()
    cfg = json.loads((R / "config.json").read_text(encoding="utf-8")); bad, warn = [], []
    decs = decoders()
    if not decs:
        msg = "no QR decoder installed (pip install opencv-python-headless, or install zbar-tools): QR images were NOT checked"
        print(("FAIL " if a.require else "SKIP ") + msg); sys.exit(1 if a.require else 3)
    print("decoders:", ", ".join(n for n, _ in decs))
    assets = [x for x in cfg["assets"] if x.get("enabled") is not False]
    for x in assets:
        expected = x.get("qr_uri") or x["address"]
        pb = Z.qr_payload_problem(x)
        if pb: bad.append(f"{x['id']}: {pb}")
        if not x.get("qr_image"): warn.append(f"{x['id']}: no qr_image (the site draws the QR itself, so this is fine)"); continue
        path = R / x["qr_image"]
        if not path.is_file(): bad.append(f"{x['id']}: qr_image missing: {x['qr_image']}"); continue
        errs = check_image(path, expected, decs)
        for e in errs: bad.append(f"{x['id']}: {x['qr_image']}: {e}")
        if not errs: print(f"OK   {x['id']}: {x['qr_image']} decodes to the wallet payload")
    extra = sorted(p.name for p in (R / "assets/qr").glob("*") if p.is_file() and f"assets/qr/{p.name}" not in {x.get("qr_image") for x in cfg["assets"]})
    for n in extra: warn.append(f"assets/qr/{n} is not used by any wallet in config.json")
    if a.generated:
        node = shutil.which("node")
        if not node: bad.append("--generated needs Node.js (not found)")
        else:
            with tempfile.TemporaryDirectory() as td:
                r = subprocess.run([node, str(R / "scripts/gen_qr_png.js"), td], capture_output=True, text=True, timeout=120)
                if r.returncode: bad.append("gen_qr_png.js failed: " + (r.stderr or r.stdout).strip()[:300])
                else:
                    for x in assets:
                        errs = check_image(pathlib.Path(td) / f"{x['id']}.png", x.get("qr_uri") or x["address"], decs)
                        for e in errs: bad.append(f"{x['id']}: website-generated QR: {e}")
                        if not errs: print(f"OK   {x['id']}: QR made by the website's own library decodes to the wallet payload")
    for w in warn: print("WARN", w)
    for b in bad: print("FAIL", b)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
