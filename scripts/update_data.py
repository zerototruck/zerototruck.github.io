#!/usr/bin/env python3
"""Read-only ledger updater. Stdlib only. Reads PUBLIC chain data (no keys, no secrets, nothing is ever signed or sent).

  python3 scripts/update_data.py --dry-run     show what it would record, change nothing  (run this first on your computer)
  python3 scripts/update_data.py               update data/ledger.json, data/progress.json, data/state.json

What it does, per enabled asset that has a "track" block in config.json:
  1. scans incoming transfers (paginated, resumable through a cursor in data/state.json),
  2. records only transfers with enough confirmations, once, under the key <asset id>|<tx hash>[|<sub>],
  3. stores the price, its source and its time next to every entry,
  4. compares the on-chain balance with what the ledger explains, and re-checks recent entries still exist on-chain.
Exit code: 0 = fine, 2 = something needs a human (shown in progress.json -> coverage and on the website), 1 = crash.
It never deletes or rewrites old entries (only new ones are added); see scripts/ztlib.py."""
import argparse, copy, datetime as dt, json, pathlib, re, sys, time, urllib.error, urllib.parse, urllib.request
from decimal import Decimal, ROUND_HALF_UP, getcontext

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ztlib as Z

R = pathlib.Path(__file__).resolve().parent.parent
UTC = dt.timezone.utc
getcontext().prec = 60
CENT = Decimal("0.01")
MAX_BYTES = 8_000_000
OVERLAP = 10  # blocks re-read behind the cursor (harmless: entries are de-duplicated by key)
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"  # keccak256("Transfer(address,address,uint256)")
BALANCE_OF = "0x70a08231"
# Only these hosts may be contacted. A config.json edit cannot point the tracker at some other server.
ALLOWED_HOSTS = {
    "mempool.space", "api.blockchair.com", "api.mainnet-beta.solana.com", "eth.blockscout.com",
    "ethereum-rpc.publicnode.com", "bsc-rpc.publicnode.com", "api.coingecko.com", "api.coinbase.com",
    "xrplcluster.com", "api.trongrid.io", "rpc.hyperliquid.xyz",
}


class ApiError(Exception): pass
class PriceError(Exception): pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None


class Api:
    """The only place that touches the network. https only, allow-listed hosts, no redirects, size cap, retries, throttle."""
    def __init__(self, delay=0.35, retries=3, timeout=25, sleep=time.sleep):
        self.delay, self.retries, self.timeout, self.sleep = delay, retries, timeout, sleep
        self.opener = urllib.request.build_opener(_NoRedirect)
        self.last = {}

    def _guard(self, url):
        u = urllib.parse.urlsplit(url)
        if u.scheme != "https" or u.username or u.password or u.hostname not in ALLOWED_HOSTS:
            raise ApiError(f"blocked URL (https + allow-listed host only): {u.scheme}://{u.hostname}")
        return u.hostname

    def get(self, url, body=None):
        host = self._guard(url)
        data = json.dumps(body).encode() if body is not None else None
        for i in range(self.retries):
            wait = self.last.get(host, 0) + self.delay - time.monotonic()
            if wait > 0: self.sleep(wait)
            self.last[host] = time.monotonic()
            try:
                req = urllib.request.Request(url, data=data, headers={"User-Agent": "zero-to-truck-ledger", "Accept": "application/json", "Content-Type": "application/json"})
                with self.opener.open(req, timeout=self.timeout) as r:
                    raw = r.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES: raise ApiError("response too large")
                return json.loads(raw)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and i < self.retries - 1: self.sleep(2 ** i * 2); continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if i < self.retries - 1: self.sleep(2 ** i * 2); continue
                raise
        raise ApiError("unreachable")

    def xrpl(self, url, method, params):
        """rippled JSON-RPC (different envelope from Ethereum/Solana JSON-RPC). Returns the `result` object; callers check `status`."""
        r = need(self.get(url, {"method": method, "params": [params]}), "result")
        if not isinstance(r, dict): raise ApiError(f"xrpl {method}: unexpected response")
        return r

    def rpc(self, url, method, params):
        d = self.get(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if not isinstance(d, dict) or d.get("error"): raise ApiError(f"rpc {method}: {d.get('error') if isinstance(d, dict) else d}")
        return d.get("result")


# ---------------------------------------------------------------- small helpers
def iso(t): return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
def fmt(d): return format(d.normalize(), "f") if d != 0 else "0"
def hexint(s):
    if not isinstance(s, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", s): raise ValueError(f"not hex: {s!r}")
    return int(s, 16)
def ts_ok(ts, now):
    if not isinstance(ts, int) or not (Z.MIN_TS < ts < now + 3600): raise ValueError(f"implausible time: {ts!r}")
    return ts
def pad(addr): return "0x" + addr[2:].lower().rjust(64, "0")
def iso_ts(s):
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return int((d if d.tzinfo else d.replace(tzinfo=UTC)).timestamp())
def need(d, key):
    if not isinstance(d, dict) or key not in d: raise ValueError(f"unexpected response: missing '{key}'")
    return d[key]
def digits(v): return isinstance(v, str) and v.isdigit() and int(v) > 0


def result(receipts, pending, cursor, complete, rejected=0):
    return {"receipts": receipts, "pending": pending, "cursor": cursor, "complete": complete, "rejected": rejected}


# ---------------------------------------------------------------- scanners (incoming transfers only)
def scan_btc(api, a, cur, lim):
    t = a["track"]; base = t["api"].rstrip("/"); addr = a["address"]; minc = t.get("min_confirmations", 3)
    tip = int(api.get(f"{base}/blocks/tip/height"))
    floor = cur["height"] - OVERLAP if cur else None
    rec, pend, url, complete = [], [], f"{base}/address/{addr}/txs", False
    for _ in range(lim["max_pages"]):
        page = api.get(url)
        conf = [x for x in page if (x.get("status") or {}).get("confirmed")]
        if not conf: complete = True; break
        for x in conf:
            txid = x["txid"]; h = int(x["status"]["block_height"])
            if not Z.tx_ok("btc", txid): raise ValueError(f"bad txid {txid!r}")
            if any((i.get("prevout") or {}).get("scriptpubkey_address") == addr for i in x.get("vin", [])): continue  # we paid: not a contribution
            sats = sum(int(o["value"]) for o in x.get("vout", []) if o.get("scriptpubkey_address") == addr)
            if sats <= 0: continue
            r = {"tx": txid, "amount": Decimal(sats) / 10 ** 8, "ts": ts_ok(int(x["status"]["block_time"]), lim["now"]), "conf": tip - h + 1}
            (rec if r["conf"] >= minc else pend).append(r)
        if floor is not None and min(int(x["status"]["block_height"]) for x in conf) < floor: complete = True; break
        url = f"{base}/address/{addr}/txs/chain/{conf[-1]['txid']}"
    return result(rec, [r["amount"] for r in pend], {"height": tip - minc + 1} if complete else cur, complete)


def scan_doge(api, a, cur, lim):
    t = a["track"]; base = t["api"].rstrip("/"); addr = a["address"]; minc = t.get("min_confirmations", 20); PAGE = 100
    floor = cur["height"] - OVERLAP if cur else None
    rec, pend, offset, complete, tip = [], [], 0, False, None
    for _ in range(lim["max_pages"]):
        d = api.get(f"{base}/dashboards/address/{addr}?transaction_details=true&limit={PAGE}&offset={offset}")
        tip = int(need(need(d, "context"), "state")); txs = need(need(need(d, "data"), addr), "transactions")
        heights = []
        for x in txs:
            b = int(x.get("block_id") or -1)
            if b <= 0: continue  # still in the mempool
            heights.append(b)
            h = x["hash"]
            if not Z.tx_ok("doge", h): raise ValueError(f"bad txid {h!r}")
            ch = int(x["balance_change"])
            if ch <= 0: continue
            ts = int(dt.datetime.strptime(x["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp())
            r = {"tx": h, "amount": Decimal(ch) / 10 ** 8, "ts": ts_ok(ts, lim["now"]), "conf": tip - b + 1}
            (rec if r["conf"] >= minc else pend).append(r)
        if len(txs) < PAGE: complete = True; break
        if floor is not None and heights and min(heights) < floor: complete = True; break
        offset += PAGE
    return result(rec, [r["amount"] for r in pend], {"height": tip - minc + 1} if complete and tip else cur, complete)


def _sol_delta(addr, r):
    if (r.get("meta") or {}).get("err") is not None: return 0
    msg = r["transaction"]["message"]; loaded = r["meta"].get("loadedAddresses") or {}
    keys = list(msg["accountKeys"]) + list(loaded.get("writable", [])) + list(loaded.get("readonly", []))
    if addr not in keys: return 0
    i = keys.index(addr)
    if i == 0: return 0  # fee payer = the sender
    return int(r["meta"]["postBalances"][i]) - int(r["meta"]["preBalances"][i])


def scan_sol(api, a, cur, lim):
    t = a["track"]; rpc = t["rpc"]; addr = a["address"]; PAGE = 100
    until = (cur or {}).get("sig"); before = None; newest = None; looked = 0; rec = []; complete = False
    for _ in range(lim["max_pages"]):
        opts = {"limit": PAGE, "commitment": "finalized"}
        if before: opts["before"] = before
        if until: opts["until"] = until
        sigs = api.rpc(rpc, "getSignaturesForAddress", [addr, opts])
        if not isinstance(sigs, list): raise ValueError("unexpected response")
        if not sigs: complete = True; break
        newest = newest or sigs[0]["signature"]
        for s in sigs:
            sig = s["signature"]
            if s.get("err") or f"{a['id']}|{sig}" in lim["known"]: continue
            if not Z.tx_ok("sol", sig): raise ValueError(f"bad signature {sig!r}")
            if looked >= lim["max_lookups"]: return result(rec, [], cur, False)
            looked += 1
            r = api.rpc(rpc, "getTransaction", [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": "finalized"}])
            if not r or not r.get("blockTime"): continue
            d = _sol_delta(addr, r)
            if d > 0: rec.append({"tx": sig, "amount": Decimal(d) / 10 ** 9, "ts": ts_ok(int(r["blockTime"]), lim["now"]), "conf": None})
        if len(sigs) < PAGE: complete = True; break
        before = sigs[-1]["signature"]
    return result(rec, [], {"sig": newest} if complete and newest else cur, complete)


def _blk(x):
    b = x.get("block_number", x.get("block"))
    return int(b) if b is not None else None


def bs_collect(api, url, max_pages, floor):
    """Blockscout v2 paging, newest first. Stops after a page that reaches below `floor`. Returns (items, complete)."""
    items, params = [], None
    for _ in range(max_pages):
        u = url if not params else url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        d = api.get(u); batch = need(d, "items")
        if not isinstance(batch, list): raise ValueError("unexpected response shape")
        items += batch
        params = d.get("next_page_params")
        if not params: return items, True
        blocks = [b for b in map(_blk, batch) if b is not None]
        if floor is not None and blocks and min(blocks) < floor: return items, True
    return items, False


def _split(rec, pend, r, minc):
    (rec if r["conf"] >= minc else pend).append(r)


def scan_evm_native(api, a, cur, lim):
    """Plain transfers AND transfers made by smart contracts (internal transactions), through a Blockscout explorer."""
    t = a["track"]; addr = a["address"]; low = addr.lower(); minc = t.get("min_confirmations", 12); unit = 10 ** t.get("decimals", 18)
    tip = hexint(api.rpc(t["rpc"], "eth_blockNumber", [])); floor = cur["block"] - OVERLAP if cur else None
    base = t["base"].rstrip("/"); rec, pend = [], []
    txs, c1 = bs_collect(api, f"{base}/api/v2/addresses/{addr}/transactions?filter=to", lim["max_pages"], floor)
    ints, c2 = bs_collect(api, f"{base}/api/v2/addresses/{addr}/internal-transactions?filter=to", lim["max_pages"], floor)
    for x in txs:
        if x.get("status") != "ok" or ((x.get("to") or {}).get("hash") or "").lower() != low: continue
        wei = x.get("value"); b = _blk(x)
        if b is None or not digits(wei): continue
        h = str(x.get("hash", "")).lower()
        if not Z.tx_ok("evm_native", h): raise ValueError(f"bad tx hash {h!r}")
        _split(rec, pend, {"tx": h, "sub": "", "amount": Decimal(int(wei)) / unit, "ts": ts_ok(iso_ts(x["timestamp"]), lim["now"]), "conf": tip - b + 1}, minc)
    for x in ints:
        if x.get("success") is not True or x.get("type") in ("delegatecall", "staticcall"): continue
        if ((x.get("to") or {}).get("hash") or "").lower() != low: continue
        wei = x.get("value"); b = _blk(x)
        if b is None or not digits(wei): continue
        h = str(x.get("transaction_hash", "")).lower()
        if not Z.tx_ok("evm_native", h): raise ValueError(f"bad tx hash {h!r}")
        _split(rec, pend, {"tx": h, "sub": f"i{int(x['index'])}", "amount": Decimal(int(wei)) / unit, "ts": ts_ok(iso_ts(x["timestamp"]), lim["now"]), "conf": tip - b + 1}, minc)
    complete = c1 and c2
    return result(rec, [r["amount"] for r in pend], {"block": max(0, tip - minc + 1)} if complete else cur, complete)


def scan_evm_token(api, a, cur, lim):
    """ERC-20 / BEP-20 transfers of ONE configured contract, through a Blockscout explorer. Look-alike tokens are ignored."""
    t = a["track"]; addr = a["address"]; low = addr.lower(); contract = t["contract"].lower(); minc = t.get("min_confirmations", 12)
    tip = hexint(api.rpc(t["rpc"], "eth_blockNumber", [])); floor = cur["block"] - OVERLAP if cur else None
    items, complete = bs_collect(api, f"{t['base'].rstrip('/')}/api/v2/addresses/{addr}/token-transfers?type=ERC-20&filter=to&token={t['contract']}", lim["max_pages"], floor)
    rec, pend, rejected = [], [], 0
    for x in items:
        tok = x.get("token") or {}
        taddr = str(tok.get("address_hash") or tok.get("address") or "").lower()
        if taddr != contract or ((x.get("to") or {}).get("hash") or "").lower() != low: rejected += 1; continue
        val = (x.get("total") or {}).get("value"); b = _blk(x)
        if b is None or not digits(val): continue
        h = str(x.get("transaction_hash") or x.get("tx_hash") or "").lower()
        if not Z.tx_ok("evm_token", h): raise ValueError(f"bad tx hash {h!r}")
        _split(rec, pend, {"tx": h, "sub": f"l{int(x['log_index'])}", "amount": Decimal(int(val)) / 10 ** int(t["decimals"]), "ts": ts_ok(iso_ts(x["timestamp"]), lim["now"]), "conf": tip - b + 1}, minc)
    return result(rec, [r["amount"] for r in pend], {"block": max(0, tip - minc + 1)} if complete else cur, complete, rejected)


def scan_evm_token_logs(api, a, cur, lim):
    """ERC-20 / BEP-20 Transfer events of ONE contract read straight from a public RPC (eth_getLogs), in block ranges.
    Needs track.start_block the first time (the block height when the wallet was created or first published)."""
    t = a["track"]; addr = a["address"]; rpc = t["rpc"]; contract = t["contract"].lower(); minc = t.get("min_confirmations", 15)
    chunk = int(t.get("log_chunk", 2000)); tip = hexint(api.rpc(rpc, "eth_blockNumber", []))
    if cur: start = max(0, cur["block"] - OVERLAP + 1)
    elif t.get("start_block") is not None: start = int(t["start_block"])
    else: raise ValueError("track.start_block is required for the first scan (block height when this wallet was created)")
    rec, pend, blocks, last, lo, n = [], [], {}, None, start, 0
    while lo <= tip and n < lim["max_pages"]:
        hi = min(lo + chunk - 1, tip); n += 1
        logs = api.rpc(rpc, "eth_getLogs", [{"fromBlock": hex(lo), "toBlock": hex(hi), "address": t["contract"], "topics": [TRANSFER_TOPIC, None, pad(addr)]}])
        if not isinstance(logs, list): raise ValueError("unexpected response")
        for lg in logs:
            tp = lg.get("topics") or []
            if lg.get("removed") or str(lg.get("address", "")).lower() != contract or len(tp) != 3 or tp[0].lower() != TRANSFER_TOPIC or tp[2].lower() != pad(addr): continue
            h = str(lg.get("transactionHash", "")).lower()
            if not Z.tx_ok("evm_token_logs", h): raise ValueError(f"bad tx hash {h!r}")
            amt = hexint(lg["data"]); b = hexint(lg["blockNumber"])
            if amt <= 0: continue
            if b not in blocks: blocks[b] = ts_ok(hexint(need(api.rpc(rpc, "eth_getBlockByNumber", [hex(b), False]), "timestamp")), lim["now"])
            _split(rec, pend, {"tx": h, "sub": f"l{hexint(lg['logIndex'])}", "amount": Decimal(amt) / 10 ** int(t["decimals"]), "ts": blocks[b], "conf": tip - b + 1}, minc)
        last = hi; lo = hi + 1
    complete = lo > tip
    return result(rec, [r["amount"] for r in pend], {"block": min(last, tip - minc + 1)} if last is not None else cur, complete)


def scan_xrp(api, a, cur, lim):
    """XRP Ledger: validated Payments to this account, paid in XRP. Uses meta.delivered_amount (never tx.Amount: partial payments)."""
    t = a["track"]; addr = a["address"]; floor = cur["ledger"] - OVERLAP if cur else None
    rec, marker, complete, head = [], None, False, None
    for _ in range(lim["max_pages"]):
        p = {"account": addr, "ledger_index_min": -1, "ledger_index_max": -1, "limit": 200, "forward": False, "api_version": 1}
        if marker: p["marker"] = marker
        r = api.xrpl(t["api"], "account_tx", p)
        if r.get("status") != "success": raise ApiError(f"xrpl account_tx: {r.get('error')}")
        head = head or int(need(r, "ledger_index_max")); lows = []
        for it in need(r, "transactions"):
            tx, meta = it.get("tx") or {}, it.get("meta") or {}
            if tx.get("ledger_index") is not None: lows.append(int(tx["ledger_index"]))
            if not it.get("validated") or tx.get("TransactionType") != "Payment" or tx.get("Destination") != addr or tx.get("Account") == addr: continue
            if meta.get("TransactionResult") != "tesSUCCESS" or not digits(meta.get("delivered_amount")): continue  # failed, or a token payment
            h = str(tx.get("hash", ""))
            if not Z.tx_ok("xrp", h): raise ValueError(f"bad tx hash {h!r}")
            rec.append({"tx": h, "sub": "", "amount": Decimal(int(meta["delivered_amount"])) / 10 ** 6, "ts": ts_ok(int(tx["date"]) + 946684800, lim["now"]), "conf": None})
        marker = r.get("marker")
        if not marker: complete = True; break
        if floor is not None and lows and min(lows) < floor: complete = True; break
    return result(rec, [], {"ledger": head} if complete and head else cur, complete)


def _tron_pages(api, url, max_pages, min_ts):
    items, fp = [], None
    for _ in range(max_pages):
        u = url + (f"&min_timestamp={min_ts}" if min_ts else "") + (f"&fingerprint={urllib.parse.quote(fp)}" if fp else "")
        d = api.get(u); batch = need(d, "data")
        if not isinstance(batch, list): raise ValueError("unexpected response shape")
        items += batch; fp = (d.get("meta") or {}).get("fingerprint")
        if not fp: return items, True
    return items, False


def scan_tron(api, a, cur, lim):
    """TRON: confirmed (solidified) incoming TRX transfers through TronGrid."""
    t = a["track"]; addr = a["address"]; me = Z.tron_hex(addr); base = t["api"].rstrip("/")
    items, complete = _tron_pages(api, f"{base}/v1/accounts/{addr}/transactions?only_confirmed=true&only_to=true&limit=200&order_by=block_timestamp,desc", lim["max_pages"], cur["ts"] - 3_600_000 if cur else None)
    rec, newest = [], (cur or {}).get("ts", 0)
    for x in items:
        ms = int(x["block_timestamp"]); newest = max(newest, ms)
        c = ((x.get("raw_data") or {}).get("contract") or [{}])[0]; v = (c.get("parameter") or {}).get("value") or {}
        if c.get("type") != "TransferContract" or str(v.get("to_address", "")).lower() != me or str(v.get("owner_address", "")).lower() == me: continue
        if ((x.get("ret") or [{}])[0].get("contractRet")) != "SUCCESS" or not isinstance(v.get("amount"), int) or v["amount"] <= 0: continue
        h = str(x.get("txID", ""))
        if not Z.tx_ok("tron", h): raise ValueError(f"bad tx id {h!r}")
        rec.append({"tx": h, "sub": "", "amount": Decimal(v["amount"]) / 10 ** 6, "ts": ts_ok(ms // 1000, lim["now"]), "conf": None})
    return result(rec, [], {"ts": newest} if complete and newest else cur, complete)


def scan_tron_trc20(api, a, cur, lim):
    """TRON: confirmed incoming TRC-20 transfers of ONE configured contract. Transfers inside one transaction are summed."""
    t = a["track"]; addr = a["address"]; contract = t["contract"]; base = t["api"].rstrip("/")
    items, complete = _tron_pages(api, f"{base}/v1/accounts/{addr}/transactions/trc20?only_confirmed=true&only_to=true&limit=200&contract_address={contract}&order_by=block_timestamp,desc", lim["max_pages"], cur["ts"] - 3_600_000 if cur else None)
    agg, newest, rejected = {}, (cur or {}).get("ts", 0), 0
    for x in items:
        ms = int(x["block_timestamp"]); newest = max(newest, ms)
        if (x.get("token_info") or {}).get("address") != contract or x.get("to") != addr or x.get("type") != "Transfer": rejected += 1; continue
        if not digits(x.get("value")): continue
        h = str(x.get("transaction_id", ""))
        if not Z.tx_ok("tron_trc20", h): raise ValueError(f"bad tx id {h!r}")
        e = agg.setdefault(h, [0, ms]); e[0] += int(x["value"])
    rec = [{"tx": h, "sub": "", "amount": Decimal(v) / 10 ** int(t["decimals"]), "ts": ts_ok(ms // 1000, lim["now"]), "conf": None} for h, (v, ms) in agg.items()]
    return result(rec, [], {"ts": newest} if complete and newest else cur, complete, rejected)


SCANNERS = {"xrp": scan_xrp, "tron": scan_tron, "tron_trc20": scan_tron_trc20, "btc": scan_btc, "doge": scan_doge, "sol": scan_sol, "evm_native": scan_evm_native, "evm_token": scan_evm_token, "evm_token_logs": scan_evm_token_logs}


# ---------------------------------------------------------------- balances and re-checks
def bal_btc(api, a):
    d = need(api.get(f"{a['track']['api'].rstrip('/')}/address/{a['address']}"), "chain_stats")
    return Decimal(int(d["funded_txo_sum"]) - int(d["spent_txo_sum"])) / 10 ** 8

def bal_doge(api, a):
    d = api.get(f"{a['track']['api'].rstrip('/')}/dashboards/address/{a['address']}?limit=1")
    return Decimal(int(need(need(need(d, "data"), a["address"]), "address")["balance"])) / 10 ** 8

def bal_sol(api, a):
    return Decimal(int(need(api.rpc(a["track"]["rpc"], "getBalance", [a["address"], {"commitment": "finalized"}]), "value"))) / 10 ** 9

def bal_evm_native(api, a):
    return Decimal(hexint(api.rpc(a["track"]["rpc"], "eth_getBalance", [a["address"], "latest"]))) / 10 ** a["track"].get("decimals", 18)

def bal_evm_token(api, a):
    t = a["track"]
    return Decimal(hexint(api.rpc(t["rpc"], "eth_call", [{"to": t["contract"], "data": BALANCE_OF + pad(a["address"])[2:]}, "latest"]))) / 10 ** int(t["decimals"])

def _tron_acct(api, a):
    data = need(api.get(f"{a['track']['api'].rstrip('/')}/v1/accounts/{a['address']}"), "data")
    return data[0] if data else {}  # an account that was never activated has no data (balance 0)

def bal_tron(api, a): return Decimal(int(_tron_acct(api, a).get("balance", 0))) / 10 ** 6

def bal_tron_trc20(api, a):
    c = a["track"]["contract"]
    return Decimal(sum(int(m[c]) for m in _tron_acct(api, a).get("trc20", []) if c in m)) / 10 ** int(a["track"]["decimals"])

def bal_xrp(api, a):
    r = api.xrpl(a["track"]["api"], "account_info", {"account": a["address"], "ledger_index": "validated", "api_version": 1})
    if r.get("error") == "actNotFound": return Decimal(0)  # never activated
    return Decimal(int(need(need(r, "account_data"), "Balance"))) / 10 ** 6

BALANCERS = {"xrp": bal_xrp, "tron": bal_tron, "tron_trc20": bal_tron_trc20, "btc": bal_btc, "doge": bal_doge, "sol": bal_sol, "evm_native": bal_evm_native, "evm_token": bal_evm_token, "evm_token_logs": bal_evm_token}


def chk_btc(api, a, e):
    try: return bool(api.get(f"{a['track']['api'].rstrip('/')}/tx/{e['tx_hash']}/status").get("confirmed"))
    except urllib.error.HTTPError as x:
        if x.code == 404: return False
        raise

def chk_doge(api, a, e):
    d = api.get(f"{a['track']['api'].rstrip('/')}/dashboards/transaction/{e['tx_hash']}").get("data")
    tr = (d.get(e["tx_hash"]) or {}).get("transaction") if isinstance(d, dict) else None
    return bool(tr) and int(tr.get("block_id") or -1) > 0

def chk_evm(api, a, e):
    r = api.rpc(a["track"]["rpc"], "eth_getTransactionReceipt", [e["tx_hash"]])
    return False if r is None else r.get("status") == "0x1"

def chk_sol(api, a, e):
    v = (api.rpc(a["track"]["rpc"], "getSignatureStatuses", [[e["tx_hash"]], {"searchTransactionHistory": True}]) or {}).get("value") or [None]
    return False if v[0] is None else (v[0].get("err") is None and v[0].get("confirmationStatus") in ("confirmed", "finalized"))

RECHECKS = {"btc": chk_btc, "doge": chk_doge, "sol": chk_sol, "evm_native": chk_evm, "evm_token": chk_evm, "evm_token_logs": chk_evm}


# ---------------------------------------------------------------- prices
def price_for(api, a, now, cache):
    """-> (Decimal usd price, source label, ISO time of the price). Two independent sources must agree within 5%."""
    tr = a["track"]
    if "peg_usd" in tr: return Decimal(str(tr["peg_usd"])), f"peg:{tr['peg_usd']}", iso(now)
    cg = tr["coingecko"]
    if cg in cache: return cache[cg]
    got = {}
    try:
        d = api.get(f"https://api.coingecko.com/api/v3/simple/price?ids={urllib.parse.quote(cg)}&vs_currencies=usd&include_last_updated_at=true")
        p = Decimal(str(d[cg]["usd"])); at = d[cg].get("last_updated_at")
        if p > 0 and isinstance(at, int) and now.timestamp() - at < 6 * 3600: got["coingecko"] = (p, dt.datetime.fromtimestamp(at, UTC))
    except Exception as e: print(f"  note: coingecko price for {cg}: {type(e).__name__}: {e}")
    if tr.get("coinbase"):
        try:
            p = Decimal(str(need(need(api.get(f"https://api.coinbase.com/v2/prices/{urllib.parse.quote(tr['coinbase'])}/spot"), "data"), "amount")))
            if p > 0: got["coinbase"] = (p, now)
        except Exception as e: print(f"  note: coinbase price for {tr['coinbase']}: {type(e).__name__}: {e}")
    if not got: raise PriceError(f"no price for {a['id']}")
    if len(got) == 2:
        x, y = got["coingecko"][0], got["coinbase"][0]
        if abs(x - y) / min(x, y) > Decimal("0.05"): raise PriceError(f"price sources disagree for {a['id']}: {x} vs {y}")
        out = (x, "coingecko+coinbase", iso(got["coingecko"][1]))
    else:
        (name, (p, at)), = got.items(); out = (p, name, iso(at))
    cache[cg] = out
    return out


# ---------------------------------------------------------------- the run
def flag(state, name, on):
    """Debounce: a condition must hold on two runs in a row before it is reported (avoids false alarms from timing)."""
    f = state.setdefault("flags", {})
    if on: f[name] = min(f.get(name, 0) + 1, 3)
    else: f.pop(name, None)
    return on and f[name] >= 2


def make_entry(a, r, price, now, excl, floor):
    px, src, at = price; amt = r["amount"]; usd = (amt * px).quantize(CENT, ROUND_HALF_UP)
    e = {"asset_id": a["id"], "asset": a["symbol"], "network": a["network"], "tx_hash": r["tx"]}
    if r.get("sub"): e["sub"] = r["sub"]
    key = Z.entry_key(e)
    status = "excluded" if (key in excl or r["tx"] in excl) else ("dust" if usd < floor else "confirmed")
    e.update({"amount_native": fmt(amt), "usd_value": float(usd), "price_usd": fmt(px), "price_source": src, "price_at": at,
              "timestamp": dt.datetime.fromtimestamp(r["ts"], UTC).strftime("%Y-%m-%d %H:%M UTC"), "ts": r["ts"],
              "confirmations": r.get("conf"), "status": status, "explorer_url": a.get("tx_explorer", "") + r["tx"],
              "source": "auto", "recorded_at": iso(now)})
    if status == "excluded": e["note"] = "excluded through tracking.exclude_tx (not counted in the total)"
    return e


def run(cfg, led, state, api, now, old_prog=None, price_fn=None, scanners=None, balancers=None, rechecks=None, log=print):
    """Core. All network access goes through `api`. Returns (ledger, state, progress, exit_code)."""
    scanners = SCANNERS if scanners is None else scanners; balancers = BALANCERS if balancers is None else balancers
    rechecks = RECHECKS if rechecks is None else rechecks
    T = cfg.get("tracking", {}); floor = Decimal(str(T.get("min_usd", 1))); tol = Decimal(str(T.get("tolerance_usd", 5)))
    excl = set(T.get("exclude_tx", []))
    cache = {}; price_fn = price_fn or (lambda a: price_for(api, a, now, cache))
    led = copy.deepcopy(led); state = copy.deepcopy(state) if state else {}
    entries = led.setdefault("entries", []); before = copy.deepcopy(entries)
    known = {Z.entry_key(e) for e in entries}
    cursors = state.setdefault("cursors", {}); fails = state.setdefault("failures", {}); state.setdefault("flags", {})
    enabled = [a for a in cfg["assets"] if a.get("enabled") is not False]; by_id = {a["id"]: a for a in enabled}
    lim = {"max_pages": int(T.get("max_pages", 40)), "max_lookups": int(T.get("max_sol_lookups", 200)), "known": known, "now": int(now.timestamp())}
    cov = {"auto": [], "manual": [], "incomplete": [], "checked": [], "disc": [], "rechecked": 0, "gone": []}
    pending = {}
    log(f"Scanning {len(enabled)} enabled asset(s)…")
    for a in enabled:
        aid = a["id"]; tr = a.get("track") or {}; kind = tr.get("kind")
        if not kind or tr.get("scan") is False:
            cov["manual"].append(aid); log(f"  {aid}: not scanned (add entries by hand)"); continue
        cov["auto"].append(aid)
        try:
            if kind not in scanners: raise ValueError(f"unknown tracker kind {kind!r}")
            res = scanners[kind](api, a, cursors.get(aid), lim)
            new, seen = [], set(); tiny = Decimal(str(tr.get("min_native", 0)))  # spam guard: below this amount nothing is recorded
            for r in res["receipts"]:
                if r["amount"] < tiny: continue
                k = Z.entry_key({"asset_id": aid, "tx_hash": r["tx"], "sub": r.get("sub", "")})
                if k not in known and k not in seen: seen.add(k); new.append(r)
            price = price_fn(a) if new else None
        except Exception as e:
            fails[aid] = min(fails.get(aid, 0) + 1, 3)
            log(f"  WARN {aid}: scan failed ({type(e).__name__}: {e}) [{fails[aid]}/3]")
            if fails[aid] >= 3: cov["incomplete"].append(aid)
            continue
        for r in sorted(new, key=lambda r: r["ts"]):
            e = make_entry(a, r, price, now, excl, floor); entries.append(e); known.add(Z.entry_key(e))
            log(f"  + {aid}: {e['amount_native']} {a['symbol']} = ${e['usd_value']:.2f} [{e['status']}] {r['tx'][:14]}…")
        cursors[aid] = res["cursor"]; fails.pop(aid, None); pending[aid] = sum(res["pending"], Decimal(0))
        if res.get("rejected"): log(f"  note {aid}: ignored {res['rejected']} transfer(s) of other tokens / other recipients")
        if not res["complete"]: cov["incomplete"].append(aid); log(f"  WARN {aid}: history not fully scanned (page limit reached); it continues next run")
        elif not new: log(f"  {aid}: nothing new")
    entries.sort(key=lambda e: (-e.get("ts", 0), Z.entry_key(e)))

    for a in enabled:                                    # does the wallet hold more than the ledger explains?
        aid = a["id"]; kind = (a.get("track") or {}).get("kind")
        if kind not in balancers: continue
        try: bal = balancers[kind](api, a)
        except Exception as e: log(f"  WARN {aid}: balance check failed ({type(e).__name__}: {e})"); continue
        cov["checked"].append(aid)
        rec = sum((Decimal(e["amount_native"]) for e in entries if e.get("asset_id") == aid and e.get("status") in Z.ON_CHAIN), Decimal(0))
        excess = bal - rec - pending.get(aid, Decimal(0)); active = False
        if excess > 0:
            try: usd = excess * price_fn(a)[0]; active = usd > tol; log(f"  balance {aid}: wallet holds {fmt(excess)} more than the ledger explains (about ${usd:.2f})")
            except Exception as e: log(f"  WARN {aid}: wallet holds more than the ledger explains, price unavailable ({e})")
        if flag(state, f"mismatch:{aid}", active): cov["disc"].append({"asset": aid})

    window = now.timestamp() - 14 * 86400                 # do recent entries still exist on-chain?
    auto = [e for e in entries if e.get("status") == "confirmed" and e.get("source") == "auto"]
    todo = {Z.entry_key(e): e for e in [e for e in auto if e.get("ts", 0) >= window][:25]}
    todo.update({Z.entry_key(e): e for e in auto if f"gone:{Z.entry_key(e)}" in state["flags"]})
    for k, e in todo.items():
        a = by_id.get(e["asset_id"]); fn = rechecks.get((a.get("track") or {}).get("kind")) if a else None
        if not fn: continue
        try: ok = fn(api, a, e)
        except Exception as x: log(f"  WARN recheck {k[:30]}: {type(x).__name__}: {x}"); continue
        cov["rechecked"] += 1
        if flag(state, f"gone:{k}", ok is False): cov["gone"].append(k)

    problems = Z.append_only_problems(before, entries)
    if problems: raise RuntimeError("refusing to write, the ledger is append-only: " + "; ".join(problems))
    prog = {"verified_raised_usd": float(Z.raised(entries)), "updated": None,
            "coverage": {"auto_tracked": sorted(cov["auto"]), "manual_only": sorted(cov["manual"]), "incomplete": sorted(set(cov["incomplete"])),
                         "balance_check": {"checked": sorted(cov["checked"]), "discrepancies": sorted(cov["disc"], key=lambda d: d["asset"])},
                         "recheck": {"checked": cov["rechecked"], "unconfirmed": sorted(cov["gone"])}}}
    today = now.strftime("%Y-%m-%d"); old = old_prog or {}
    same = {k: v for k, v in old.items() if k != "updated"} == {k: v for k, v in prog.items() if k != "updated"}
    prog["updated"] = (old.get("updated") or today) if same else today
    attention = bool(prog["coverage"]["incomplete"] or cov["disc"] or cov["gone"])
    return led, state, prog, (2 if attention else 0)


def _load(p, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else default


def _write(p, data):
    s = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if not p.exists() or p.read_text(encoding="utf-8") != s: p.write_text(s, encoding="utf-8"); return True
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0]); ap.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    args = ap.parse_args(argv)
    cfg = _load(R / "config.json", None); led = _load(R / "data/ledger.json", {"entries": []}); old = _load(R / "data/progress.json", {})
    state = _load(R / "data/state.json", {"cursors": {}, "failures": {}, "flags": {}})
    led2, st2, prog, code = run(cfg, led, state, Api(), dt.datetime.now(UTC), old)
    c = prog["coverage"]
    print(f"\nVerified raised: ${prog['verified_raised_usd']:,.2f}   automatic: {', '.join(c['auto_tracked']) or '-'}   by hand: {', '.join(c['manual_only']) or '-'}")
    if c["incomplete"]: print("ATTENTION not fully scanned:", ", ".join(c["incomplete"]))
    if c["balance_check"]["discrepancies"]: print("ATTENTION wallet holds more than the ledger explains:", ", ".join(d["asset"] for d in c["balance_check"]["discrepancies"]))
    if c["recheck"]["unconfirmed"]: print("ATTENTION recorded entries not found on-chain any more:", ", ".join(c["recheck"]["unconfirmed"]))
    if args.dry_run: print("\n--dry-run: nothing was written.")
    else:
        ch = [_write(R / "data/ledger.json", led2), _write(R / "data/state.json", st2), _write(R / "data/progress.json", prog)]
        print("\nfiles updated." if any(ch) else "\nno changes.")
    sys.exit(code)


if __name__ == "__main__":
    main()
