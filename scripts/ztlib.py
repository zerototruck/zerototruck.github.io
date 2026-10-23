"""Shared helpers for the ledger scripts (stdlib only). No network access in this file."""
import hashlib, re
from decimal import Decimal

STATUSES = ("confirmed", "dust", "excluded", "disputed")
COUNTED = ("confirmed",)                      # only these add to "Verified Raised"
ON_CHAIN = ("confirmed", "dust", "excluded")  # still sitting in the wallet, so they explain its balance
MUTABLE = ("status", "note")                  # the only fields a person may change on an existing entry
FIELDS_REQUIRED = ("asset_id", "asset", "network", "tx_hash", "amount_native", "usd_value", "timestamp", "ts", "status", "source")
KINDS = ("btc", "doge", "sol", "evm_native", "evm_token", "evm_token_logs", "xrp", "tron", "tron_trc20")

HEX64 = re.compile(r"^[0-9a-f]{64}$")
EVM_TX = re.compile(r"^0x[0-9a-f]{64}$")
SOL_SIG = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{80,90}$")
GENERIC_TX = re.compile(r"^(0x[0-9a-f]{64}|[0-9A-Za-z]{16,100})$")
DEC = re.compile(r"^\d+(\.\d+)?$")
MIN_TS = 1_230_000_000  # 2009-01-01


def tx_ok(kind, tx):
    if not isinstance(tx, str): return False
    if kind in ("btc", "doge", "tron", "tron_trc20"): return bool(HEX64.match(tx))
    if kind == "xrp": return bool(re.match(r"^[0-9A-F]{64}$", tx))
    if kind in ("evm_native", "evm_token", "evm_token_logs"): return bool(EVM_TX.match(tx))
    if kind == "sol": return bool(SOL_SIG.match(tx))
    return bool(GENERIC_TX.match(tx))


def entry_key(e):
    """Unique id of a ledger entry: <asset id>|<tx hash>[|<sub>]. The asset id already encodes network + asset
    (ETH, BNB, USDT-ETH, USDT-BSC are four different ids), so the same hash can never collide across networks."""
    return "|".join([e["asset_id"], e["tx_hash"]] + ([e["sub"]] if e.get("sub") else []))


def raised(entries):
    """Sum of usd_value over counted entries, as an exact Decimal (cents)."""
    return sum((Decimal(str(e["usd_value"])) for e in entries if e.get("status") in COUNTED), Decimal(0))


def append_only_problems(old, new):
    """Compare two lists of ledger entries. Returns a list of problems (empty = fine).
    Allowed: new entries; `status`/`note` edits (a status change needs a note). Anything else is a problem."""
    out = []
    nk = {}
    for e in new:
        k = entry_key(e)
        if k in nk: out.append(f"duplicate entry key: {k}")
        nk[k] = e
    for e in old:
        k = entry_key(e)
        n = nk.get(k)
        if n is None:
            out.append(f"entry removed: {k}")
            continue
        for f in sorted(set(e) | set(n)):
            if f in MUTABLE: continue
            if e.get(f) != n.get(f): out.append(f"entry {k}: field '{f}' was changed")
        if n.get("status") != e.get("status") and not n.get("note"):
            out.append(f"entry {k}: status changed without a 'note'")
    return out


def qr_payload_problem(a):
    """The QR payload must be the address itself, or '<scheme>:<address>' with nothing after it (no amount/label/query)."""
    p = a.get("qr_uri") or a["address"]; ad = a["address"]
    if p == ad: return None
    if re.fullmatch(r"[a-z][a-z0-9+.-]{1,20}:" + re.escape(ad), p): return None
    return f"qr_uri must be the address itself or '<scheme>:<address>' with nothing after it (got {p!r})"


# ---- base58 helpers (address checksums for XRP and TRON)
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
RIPPLE = "rpshnaf39wBUDNEGHJKLM4PQRST7VWXYZ2bcdeCg65jkm8oFqi1tuvAxyz"


def b58dec(s, n, alphabet=B58):
    v = 0
    for ch in s: v = v * len(alphabet) + alphabet.index(ch)
    return v.to_bytes(n, "big")


def _chk(raw): return hashlib.sha256(hashlib.sha256(raw[:-4]).digest()).digest()[:4] == raw[-4:]


def xrp_ok(ad):
    """XRP Ledger classic address: Ripple base58 alphabet, 25 bytes = 0x00 + 20-byte hash + 4-byte checksum."""
    try: raw = b58dec(ad, 25, RIPPLE); return raw[0] == 0 and _chk(raw)
    except Exception: return False


def tron_ok(ad):
    """TRON address: 34 characters, base58check, first byte 0x41."""
    try: raw = b58dec(ad, 25); return len(ad) == 34 and raw[0] == 0x41 and _chk(raw)
    except Exception: return False


def tron_hex(ad): return b58dec(ad, 25)[:21].hex()  # the 41-prefixed hex form TronGrid uses inside transactions
