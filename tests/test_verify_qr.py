"""Negative tests: the QR/address checks must FAIL on a wrong, swapped, damaged or booby-trapped QR. Skipped when OpenCV is missing."""
import json, pathlib, shutil, subprocess, sys, tempfile, unittest
R = pathlib.Path(__file__).resolve().parent.parent
try: import cv2  # noqa
except Exception: cv2 = None

def sandbox():
    td = tempfile.mkdtemp(); d = pathlib.Path(td) / "repo"
    shutil.copytree(R, d, ignore=shutil.ignore_patterns(".git")); return d
def run(d, *a): return subprocess.run([sys.executable, "-I", *a], cwd=d, capture_output=True, text=True, timeout=120)

@unittest.skipUnless(cv2, "OpenCV not installed")
class Qr(unittest.TestCase):
    def test_clean_repo_passes(self):
        d = sandbox(); self.assertEqual(run(d, "scripts/verify_qr.py", "--generated", "--require").returncode, 0)
    def test_swapped_image_is_caught(self):
        d = sandbox(); shutil.copy(d / "assets/qr/BTC.png", d / "assets/qr/ETH.png")
        r = run(d, "scripts/verify_qr.py", "--require"); self.assertEqual(r.returncode, 1); self.assertIn("ETH", r.stdout); self.assertIn("QR contains", r.stdout)
    def test_garbage_and_missing_images_are_caught(self):
        d = sandbox(); (d / "assets/qr/SOL.png").write_bytes(b"not a png"); (d / "assets/qr/DOGE.png").unlink()
        r = run(d, "scripts/verify_qr.py", "--require"); self.assertEqual(r.returncode, 1); self.assertIn("not a PNG", r.stdout); self.assertIn("missing", r.stdout)
    def test_qr_with_extra_payload_in_config_is_caught(self):
        d = sandbox(); c = json.loads((d / "config.json").read_text()); c["assets"][0]["qr_uri"] += "?amount=1"
        (d / "config.json").write_text(json.dumps(c))
        self.assertEqual(run(d, "scripts/verify_addresses.py").returncode, 1); self.assertEqual(run(d, "scripts/verify_qr.py", "--require").returncode, 1)
    def test_changed_address_with_old_qr_is_caught(self):
        d = sandbox(); c = json.loads((d / "config.json").read_text()); c["assets"][0]["address"] = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"; c["assets"][0]["qr_uri"] = c["assets"][0]["address"]
        (d / "config.json").write_text(json.dumps(c)); r = run(d, "scripts/verify_qr.py", "--require")
        self.assertEqual(r.returncode, 1); self.assertIn("QR contains", r.stdout)
    def test_tampered_vendored_library_is_caught(self):
        d = sandbox(); p = d / "assets/vendor/qrcode.js"; p.write_text(p.read_text() + "\n/* x */")
        r = run(d, "scripts/verify_data.py"); self.assertEqual(r.returncode, 1); self.assertIn("qrcode.js changed", r.stdout)
    def test_external_script_in_html_is_caught(self):
        d = sandbox(); p = d / "index.html"; p.write_text(p.read_text().replace('</head>', '<script src="https://cdn.example/x.js"></script></head>', 1))
        r = run(d, "scripts/verify_data.py"); self.assertEqual(r.returncode, 1); self.assertIn("another website", r.stdout)
    def test_rewritten_ledger_history_is_caught(self):
        d = sandbox(); e = {"asset_id": "BTC", "asset": "BTC", "network": "Bitcoin", "tx_hash": "a" * 64, "amount_native": "1", "usd_value": 10.0, "timestamp": "x", "ts": 1_700_000_000, "status": "confirmed", "source": "manual", "explorer_url": ""}
        sub = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=d, capture_output=True, text=True)
        sub("init", "-q"); (d / "data/ledger.json").write_text(json.dumps({"entries": [e]})); sub("add", "-A"); sub("commit", "-qm", "a")
        (d / "data/ledger.json").write_text(json.dumps({"entries": [{**e, "usd_value": 99999.0}]}))
        r = run(d, "scripts/verify_data.py", "--base", "HEAD"); self.assertEqual(r.returncode, 1); self.assertIn("append-only", r.stdout)

if __name__ == "__main__": unittest.main()
