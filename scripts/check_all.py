#!/usr/bin/env python3
"""One command, quiet when everything is fine: python3 scripts/check_all.py   (add --require to fail when QR decoding is not possible)
Runs the offline tests, address/data/ledger checks, QR decoding and the CSP check, and prints ONE line per step.
Details are printed only for a step that fails. Needs no internet. (Live-API check: python3 scripts/update_data.py --dry-run)"""
import pathlib, re, subprocess, sys
R = pathlib.Path(__file__).resolve().parent.parent
req = "--require" in sys.argv
steps = [(f"tests/{t.name}", [str(t)]) for t in sorted((R / "tests").glob("test_*.py"))]
steps += [("addresses", ["scripts/verify_addresses.py"]), ("data + ledger", ["scripts/verify_data.py"]), ("QR images (decoded)", ["scripts/verify_qr.py", "--generated"] + (["--require"] if req else []))]
if re.search(r'http-equiv="Content-Security-Policy"', (R / "index.html").read_text(encoding="utf-8")): steps.append(("CSP matches page", ["scripts/add_csp.py", "--check"]))
bad = 0
for name, cmd in steps:
    r = subprocess.run([sys.executable, "-I", *cmd], cwd=R, capture_output=True, text=True, timeout=300)
    tag = "SKIP" if r.returncode == 3 else ("PASS" if r.returncode == 0 else "FAIL")
    warns = [l for l in r.stdout.splitlines() if l.startswith("WARN")]
    print(f"{tag}  {name}" + (f"   ({len(warns)} warning(s): rerun that script to read them)" if warns and tag == "PASS" else ""))
    if tag == "FAIL": bad += 1; print("\n".join("      " + l for l in (r.stdout + r.stderr).splitlines() if not l.startswith("OK") and not l.startswith("  note")))
print("\nALL GOOD" if not bad else f"\n{bad} step(s) FAILED")
sys.exit(1 if bad else 0)
