#!/usr/bin/env python3
"""Offline integrity check of the repo's data. Stdlib only, no network, writes nothing.

  python3 scripts/verify_data.py                         check everything
  python3 scripts/verify_data.py --base <git ref>        also prove data/ledger.json only GREW compared with that commit
  python3 scripts/verify_data.py --bot-changes-only      fail if anything except the three data files the bot may write has changed
Exit 1 on any FAIL (a failing run turns the workflow red and GitHub emails you)."""
import argparse, hashlib, json, pathlib, re, subprocess, sys, urllib.parse
from decimal import Decimal

R = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ztlib as Z
import update_data as U
from verify_addresses import valid

EXPLORER_HOSTS = {"mempool.space", "blockstream.info", "etherscan.io", "bscscan.com", "solscan.io", "explorer.solana.com", "dogechain.info", "blockchair.com", "eth.blockscout.com", "xrpscan.com", "tronscan.org", "hyperevmscan.io"}
# Canonical stablecoin contracts. A config that points these ids anywhere else is refused (fake-token protection).
KNOWN_TOKENS = {"USDT-ETH": ("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6), "USDT-BSC": ("0x55d398326f99059fF775485246999027B3197955", 18),
                "USDC-ETH": ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6), "USDT-TRC20": ("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", 6)}
BOT_FILES = {"data/ledger.json", "data/progress.json", "data/state.json"}
bad, warn = [], []


def fail(m): bad.append(m)
def load(p):
    try: return json.loads((R / p).read_text(encoding="utf-8"))
    except Exception as e: fail(f"{p}: not valid JSON ({e})"); return None
def https_host(u, allowed):
    try: s = urllib.parse.urlsplit(u)
    except Exception: return False
    return s.scheme == "https" and not s.username and s.hostname in allowed


def check_config(cfg):
    ids = [a.get("id") for a in cfg["assets"]]
    if len(ids) != len(set(ids)): fail("config.json: duplicate asset ids")
    for a in cfg["assets"]:
        i = a.get("id", "?"); on = a.get("enabled") is not False
        if not re.fullmatch(r"[A-Za-z0-9-]+", str(i)): fail(f"{i}: unsafe asset id")
        for k in ("explorer", "tx_explorer"):
            if a.get(k) and not https_host(a[k], EXPLORER_HOSTS): fail(f"{i}: {k} must be https on a known explorer ({sorted(EXPLORER_HOSTS)})")
        if a.get("qr_image") and not re.fullmatch(r"assets/qr/[A-Za-z0-9-]+\.png", a["qr_image"]): fail(f"{i}: qr_image must be assets/qr/<ID>.png")
        t = a.get("track")
        if not t:
            if on: warn.append(f"{i}: enabled but has no 'track' block: contributions must be added by hand")
            continue
        k = t.get("kind")
        if k not in Z.KINDS: fail(f"{i}: track.kind {k!r} is not one of {Z.KINDS}"); continue
        for h in ("api", "base", "rpc"):
            if t.get(h) and not https_host(t[h], U.ALLOWED_HOSTS): fail(f"{i}: track.{h} host is not in the tracker's allow-list")
        need = {"btc": ("api",), "doge": ("api",), "sol": ("rpc",), "evm_native": ("base", "rpc"), "evm_token": ("base", "rpc", "contract", "decimals"), "evm_token_logs": ("rpc", "contract", "decimals"), "xrp": ("api",), "tron": ("api",), "tron_trc20": ("api", "contract", "decimals")}[k]
        if t.get("scan") is False: need = tuple(x for x in need if x != "base")
        for n in need:
            if t.get(n) in (None, ""): fail(f"{i}: track.{n} is required for kind {k}")
        if not (t.get("peg_usd") or t.get("coingecko")): fail(f"{i}: track needs 'coingecko' (or 'peg_usd' for a stablecoin)")
        if "peg_usd" in t and not Z.DEC.match(str(t["peg_usd"])): fail(f"{i}: peg_usd must be a plain number")
        if t.get("min_confirmations") is not None and not (isinstance(t["min_confirmations"], int) and 1 <= t["min_confirmations"] <= 1000): fail(f"{i}: min_confirmations must be 1..1000")
        if t.get("min_native") is not None and not Z.DEC.match(str(t["min_native"])): fail(f"{i}: min_native must be a plain number")
        if k.startswith("evm_token") or k == "tron_trc20":
            tron = k == "tron_trc20"; ok, why = valid(t.get("contract", ""), "tron" if tron else None)
            if not ok or (not tron and not str(t.get("contract", "")).startswith("0x")): fail(f"{i}: token contract is not a valid {'TRON' if tron else 'EVM'} address ({why})")
            if i in KNOWN_TOKENS and (t.get("contract"), t.get("decimals")) != KNOWN_TOKENS[i]:
                fail(f"{i}: contract/decimals differ from the canonical token {KNOWN_TOKENS[i][0]} ({KNOWN_TOKENS[i][1]} decimals)")
            elif i not in KNOWN_TOKENS: warn.append(f"{i}: token contract is not in the built-in canonical list: check it on the explorer yourself")
            if on and a.get("symbol") in ("USDT", "USDC") and i not in KNOWN_TOKENS: fail(f"{i}: stablecoins must use one of the ids {sorted(KNOWN_TOKENS)}")
        if k == "evm_token_logs" and on and t.get("start_block") is None: fail(f"{i}: enabled, but track.start_block is empty (set the block height when the wallet was created)")
        if on and t.get("scan") is False: warn.append(f"{i}: scan is off: contributions must be added by hand (the balance check still watches the wallet)")
    tr = cfg.get("tracking", {})
    for k in ("min_usd", "tolerance_usd", "max_pages"):
        if not isinstance(tr.get(k), (int, float)) or isinstance(tr.get(k), bool) or tr[k] < 0: fail(f"tracking.{k} must be a non-negative number")
    if not isinstance(tr.get("exclude_tx", []), list): fail("tracking.exclude_tx must be a list")
    print("OK   config.json structure")


def check_ledger(cfg, led):
    by = {a["id"]: a for a in cfg["assets"]}; keys = set(); n = 0
    if not isinstance(led, dict) or not isinstance(led.get("entries"), list): fail("data/ledger.json: needs an 'entries' list"); return
    for e in led["entries"]:
        n += 1; tag = f"ledger entry #{n}"
        miss = [f for f in Z.FIELDS_REQUIRED if f not in e]
        if miss: fail(f"{tag}: missing {miss}"); continue
        a = by.get(e["asset_id"])
        if not a: fail(f"{tag}: unknown asset_id {e['asset_id']!r}"); continue
        k = Z.entry_key(e); tag = f"ledger entry {k[:40]}"
        if k in keys: fail(f"{tag}: duplicate key")
        keys.add(k)
        kind = (a.get("track") or {}).get("kind")
        if not Z.tx_ok(kind, e["tx_hash"]): fail(f"{tag}: tx_hash has the wrong format for {e['asset_id']}")
        if e.get("sub") is not None and not re.fullmatch(r"[il]\d{1,9}", str(e["sub"])): fail(f"{tag}: bad sub index")
        if e["status"] not in Z.STATUSES: fail(f"{tag}: status must be one of {Z.STATUSES}")
        if e["status"] in ("excluded", "disputed") and not e.get("note"): fail(f"{tag}: status {e['status']} needs a 'note'")
        if e["source"] not in ("auto", "manual"): fail(f"{tag}: source must be auto or manual")
        if not (isinstance(e["amount_native"], str) and Z.DEC.match(e["amount_native"])): fail(f"{tag}: amount_native must be a plain decimal string")
        if not (isinstance(e["usd_value"], (int, float)) and not isinstance(e["usd_value"], bool) and e["usd_value"] >= 0): fail(f"{tag}: usd_value must be a number >= 0")
        if not (isinstance(e["ts"], int) and e["ts"] > Z.MIN_TS): fail(f"{tag}: ts is not a plausible unix time")
        if e.get("confirmations") is not None and not isinstance(e["confirmations"], int): fail(f"{tag}: confirmations must be a number or null")
        if e.get("explorer_url") and not https_host(e["explorer_url"], EXPLORER_HOSTS): fail(f"{tag}: explorer_url must be https on a known explorer")
        if e["source"] == "auto":
            for f in ("price_usd", "price_source", "price_at"):
                if not e.get(f): fail(f"{tag}: automatic entries must record {f}")
    print(f"OK   ledger: {n} entr{'y' if n == 1 else 'ies'}, keys unique, formats valid")


def check_progress(cfg, led, prog, state):
    if not isinstance(prog, dict): fail("data/progress.json: not an object"); return
    want = Z.raised(led["entries"]) if isinstance(led, dict) and isinstance(led.get("entries"), list) else None
    v = prog.get("verified_raised_usd")
    if not isinstance(v, (int, float)) or isinstance(v, bool): fail("progress.verified_raised_usd must be a number")
    elif want is not None and Decimal(str(v)) != want: fail(f"progress.verified_raised_usd ({v}) is not the sum of the 'confirmed' ledger entries ({want})")
    else: print(f"OK   progress equals the sum of confirmed entries (${want})")
    c = prog.get("coverage"); enabled = {a["id"] for a in cfg["assets"] if a.get("enabled") is not False}
    if not isinstance(c, dict): fail("progress.coverage is missing"); return
    for k in ("auto_tracked", "manual_only", "incomplete"):
        if not isinstance(c.get(k), list) or not set(c[k]) <= enabled | set(): fail(f"progress.coverage.{k} must list enabled asset ids")
    bc = c.get("balance_check") or {}
    if not isinstance(bc.get("checked"), list) or not isinstance(bc.get("discrepancies"), list): fail("progress.coverage.balance_check is malformed")
    if not isinstance((c.get("recheck") or {}).get("unconfirmed"), list): fail("progress.coverage.recheck is malformed")
    if isinstance(state, dict):
        if not all(isinstance(state.get(k), dict) for k in ("cursors", "failures", "flags")): fail("data/state.json needs cursors/failures/flags objects")
        elif not all(isinstance(x, int) and 0 <= x <= 3 for k in ("failures", "flags") for x in state[k].values()): fail("data/state.json counters must be 0..3")
    print("OK   progress coverage and state shape")


def check_vendor():
    d = R / "assets/vendor"; m = json.loads((d / "VENDORED.json").read_text(encoding="utf-8"))
    h = hashlib.sha256((d / "qrcode.js").read_bytes()).hexdigest()
    if h != m["sha256"]: fail(f"assets/vendor/qrcode.js changed (sha256 {h[:12]}… != recorded {m['sha256'][:12]}…). If you updated it on purpose, update VENDORED.json after reviewing the diff")
    else: print("OK   vendored QR library matches its recorded sha256")
    if not (d / "qrcode-generator.LICENSE").is_file(): fail("assets/vendor/qrcode-generator.LICENSE is missing")


def check_html():
    html = (R / "index.html").read_text(encoding="utf-8", errors="ignore")
    ext_js = re.findall(r"""<script\b[^>]*\bsrc\s*=\s*["'](?:https?:)?//[^"']+""", html, re.I)
    if ext_js: fail(f"index.html loads JavaScript from another website: {ext_js[0][-60:]}")
    elif "assets/vendor/qrcode.js" not in html: fail("index.html does not load the vendored QR library")
    else: print("OK   index.html loads no JavaScript from other websites")
    if re.search(r"""<(link|img)\b[^>]*(href|src)\s*=\s*["'](?:https?:)?//""", html, re.I) or "pexels.com" in (R / "styles.css").read_text(encoding="utf-8", errors="ignore"):
        warn.append("fonts/photo still come from other websites: run `python3 scripts/setup.py --localize`, then `python3 scripts/add_csp.py`")


def check_misc():
    for n, root in (("proof", "purchase_proof"), ("changes", "entries"), ("faq", None)):
        d = load(f"data/{n}.json")
        if d is None: continue
        for it in (d.get(root, []) if root else (d.get("items") or d.get("entries") or [])):
            for k, v in (it.items() if isinstance(it, dict) else []):
                if k in ("url", "link") and v and not str(v).startswith("https://"): fail(f"data/{n}.json: {k} must be https: {v}")
    print("OK   proof / changes / faq links are https")


def git(*a):
    return subprocess.run(["git", *a], cwd=R, capture_output=True, text=True, timeout=60)


def check_base(ref, led):
    if not re.fullmatch(r"[0-9A-Za-z._/~^-]{1,100}", ref): fail("--base: unsafe ref"); return
    if set(ref) == {"0"}: print("SKIP append-only check (no earlier commit)"); return
    r = git("show", f"{ref}:data/ledger.json")
    if r.returncode: print(f"SKIP append-only check ({ref} has no data/ledger.json)"); return
    probs = Z.append_only_problems(json.loads(r.stdout).get("entries", []), led.get("entries", []))
    for p in probs: fail("ledger is append-only: " + p)
    if not probs: print(f"OK   ledger only grew compared with {ref[:12]}")


def check_bot_only():
    r = git("status", "--porcelain", "--untracked-files=all")
    if r.returncode: fail("git status failed"); return
    changed = {l[3:].strip().strip('"') for l in r.stdout.splitlines() if l.strip()}
    extra = sorted(changed - BOT_FILES)
    if extra: fail(f"the update changed files it must never touch: {extra}")
    else: print(f"OK   only the bot's data files changed ({sorted(changed) or 'nothing'})")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--base"); ap.add_argument("--bot-changes-only", action="store_true"); a = ap.parse_args()
    cfg = load("config.json"); led = load("data/ledger.json"); prog = load("data/progress.json"); state = load("data/state.json")
    if cfg and isinstance(cfg.get("assets"), list):
        check_config(cfg)
        if led is not None: check_ledger(cfg, led)
        if prog is not None: check_progress(cfg, led, prog, state)
    else: fail("config.json: needs an 'assets' list")
    try: check_vendor()
    except Exception as e: fail(f"vendored library check failed: {e}")
    check_html(); check_misc()
    if a.base and led: check_base(a.base, led)
    if a.bot_changes_only: check_bot_only()
    for w in warn: print("WARN", w)
    for b in bad: print("FAIL", b)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
