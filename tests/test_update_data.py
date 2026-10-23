"""Offline tests for the ledger tracker. They use FAKE chain/price responses: they prove the logic (paging, cursors,
de-duplication, spoof rejection, balance check, append-only guard), NOT that the live APIs still answer in this shape.
Run:  python3 -I -m unittest discover -s tests -t .      (or: python3 scripts/check_all.py)"""
import copy, datetime as dt, pathlib, sys, unittest, urllib.error
from decimal import Decimal

R = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R / "scripts"))
import update_data as U, ztlib as Z

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
T0 = int(NOW.timestamp()) - 3 * 86400
BTC = "bc1qmehruudj9ug80hun7n2rnggm9s7y4n09lh96pz"
EVM = "0x87C03A643d47e748aC50B022E35cb7E5a6c4fa62"
SOLA = "3WiDYV4Ddv3Mm5wovAciohjvRbpMEQLrsyeG9UGhp8DK"
DOGE = "DRE2ucCnDLmwDEjoebYUxf2DYFe487F9ui"
USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
FAKE_TOKEN = "0x1111111111111111111111111111111111111111"
H = lambda c, n=64: c * n                      # a 64-hex-char hash
EH = lambda c: "0x" + c * 64
SIG = lambda c: c * 88                          # a plausible base58 signature (88 chars, no 0/O/I/l)


class FakeApi:
    def __init__(self): self.gets, self.rpcs, self.calls = {}, {}, []
    def get(self, url, body=None):
        self.calls.append(url)
        v = self.gets[url] if url in self.gets else (_ for _ in ()).throw(AssertionError(f"unexpected GET {url}"))
        if isinstance(v, Exception): raise v
        return v(url) if callable(v) else copy.deepcopy(v)
    def rpc(self, url, method, params):
        self.calls.append(f"rpc:{method}")
        v = self.rpcs[method] if method in self.rpcs else (_ for _ in ()).throw(AssertionError(f"unexpected rpc {method}"))
        if isinstance(v, Exception): raise v
        return v(params) if callable(v) else copy.deepcopy(v)


def cfg(*assets, **tracking):
    return {"assets": list(assets), "tracking": {"min_usd": 1, "tolerance_usd": 5, **tracking}}

def btc_asset(**tr): return {"id": "BTC", "symbol": "BTC", "network": "Bitcoin", "address": BTC, "enabled": True, "tx_explorer": "https://mempool.space/tx/",
                             "track": {"kind": "btc", "api": "https://mempool.space/api", "coingecko": "bitcoin", "min_confirmations": 3, **tr}}
def eth_asset(**tr): return {"id": "ETH", "symbol": "ETH", "network": "Ethereum", "address": EVM, "enabled": True, "tx_explorer": "https://etherscan.io/tx/",
                             "track": {"kind": "evm_native", "base": "https://eth.blockscout.com", "rpc": "https://ethereum-rpc.publicnode.com", "decimals": 18, "coingecko": "ethereum", "min_confirmations": 12, **tr}}
def usdt_asset(**tr): return {"id": "USDT-ETH", "symbol": "USDT", "network": "Ethereum / ERC-20", "address": EVM, "enabled": True, "tx_explorer": "https://etherscan.io/tx/",
                              "track": {"kind": "evm_token", "base": "https://eth.blockscout.com", "rpc": "https://ethereum-rpc.publicnode.com", "contract": USDT, "decimals": 6, "peg_usd": "1", "min_confirmations": 12, **tr}}

def btc_tx(txid, height, ts, value, to=BTC, spend_from=None):
    vin = [{"prevout": {"scriptpubkey_address": spend_from or "bc1qsomeoneelse"}}]
    return {"txid": txid, "status": {"confirmed": True, "block_height": height, "block_time": ts}, "vin": vin,
            "vout": [{"scriptpubkey_address": to, "value": value}, {"scriptpubkey_address": "bc1qchange", "value": 5}]}

PRICE = lambda a: (Decimal("100000"), "test", "2026-10-13T11:59:00Z")


def go(config, ledger=None, state=None, api=None, price=PRICE, prog=None, **kw):
    kw.setdefault("balancers", {}); kw.setdefault("rechecks", {})
    out = []
    led, st, pr, code = U.run(config, ledger or {"entries": []}, state or {}, api, NOW, prog, price_fn=price, log=out.append, **kw)
    return led, st, pr, code, out


class Lib(unittest.TestCase):
    def test_key_includes_asset_so_same_hash_on_two_chains_never_collides(self):
        a = {"asset_id": "ETH", "tx_hash": EH("a")}; b = {"asset_id": "BNB", "tx_hash": EH("a")}
        self.assertNotEqual(Z.entry_key(a), Z.entry_key(b))
        self.assertEqual(Z.entry_key({**a, "sub": "l3"}), f"ETH|{EH('a')}|l3")

    def test_append_only(self):
        e = {"asset_id": "BTC", "tx_hash": H("a"), "usd_value": 10, "status": "confirmed"}
        self.assertEqual(Z.append_only_problems([e], [e, {**e, "tx_hash": H("b")}]), [])
        self.assertTrue(Z.append_only_problems([e], []))                                    # removed
        self.assertTrue(Z.append_only_problems([e], [{**e, "usd_value": 99}]))              # amount edited
        self.assertTrue(Z.append_only_problems([e], [{**e, "status": "disputed"}]))         # status change needs a note
        self.assertEqual(Z.append_only_problems([e], [{**e, "status": "disputed", "note": "reorg"}]), [])
        self.assertTrue(Z.append_only_problems([], [e, e]))                                 # duplicate key

    def test_tx_ok(self):
        self.assertTrue(Z.tx_ok("btc", H("a"))); self.assertFalse(Z.tx_ok("btc", "0x" + H("a")))
        self.assertTrue(Z.tx_ok("evm_native", EH("a"))); self.assertFalse(Z.tx_ok("evm_native", H("a")))
        self.assertFalse(Z.tx_ok("sol", "short")); self.assertTrue(Z.tx_ok("sol", SIG("2")))


class Guard(unittest.TestCase):
    def test_api_refuses_other_hosts_and_http(self):
        api = U.Api()
        for bad in ("http://mempool.space/api", "https://evil.example/api", "https://user:pw@mempool.space/", "file:///etc/passwd"):
            with self.assertRaises(U.ApiError): api._guard(bad)
        self.assertEqual(api._guard("https://mempool.space/api/x"), "mempool.space")

    def test_every_configured_host_is_allowlisted(self):
        import json, urllib.parse
        c = json.loads((R / "config.json").read_text())
        for a in c["assets"]:
            for k in ("api", "base", "rpc"):
                v = a.get("track", {}).get(k)
                if v: self.assertIn(urllib.parse.urlsplit(v).hostname, U.ALLOWED_HOSTS, f"{a['id']}.{k}")


class Btc(unittest.TestCase):
    def api(self):
        f = FakeApi(); base = "https://mempool.space/api"
        f.gets[f"{base}/blocks/tip/height"] = 205
        # newest page: one unconfirmed (ignored), 3 confirmed
        f.gets[f"{base}/address/{BTC}/txs"] = [
            {"txid": H("f"), "status": {"confirmed": False}, "vin": [], "vout": []},
            btc_tx(H("a"), 204, T0 + 3000, 50_000),                 # 2 confirmations -> pending, not recorded
            btc_tx(H("b"), 200, T0 + 2000, 100_000_000),            # 6 confirmations -> recorded
            btc_tx(H("c"), 199, T0 + 1000, 7, spend_from=BTC)]     # we paid -> not a contribution
        f.gets[f"{base}/address/{BTC}/txs/chain/{H('c')}"] = [btc_tx(H("d"), 120, T0, 200_000_000)]
        f.gets[f"{base}/address/{BTC}/txs/chain/{H('d')}"] = []
        return f

    def test_first_scan_pages_to_the_end(self):
        res = U.scan_btc(self.api(), btc_asset(), None, {"max_pages": 40, "now": int(NOW.timestamp())})
        self.assertEqual({r["tx"] for r in res["receipts"]}, {H("b"), H("d")})
        self.assertEqual(res["pending"], [Decimal("0.0005")])
        self.assertTrue(res["complete"]); self.assertEqual(res["cursor"], {"height": 203})  # 205 - 3 + 1

    def test_stops_at_cursor_and_does_not_fetch_older_pages(self):
        f = self.api()                                  # cursor 135 -> floor 125; page two ends at block 120 (< 125) so paging stops there
        res = U.scan_btc(f, btc_asset(), {"height": 135}, {"max_pages": 40, "now": int(NOW.timestamp())})
        self.assertTrue(res["complete"])
        self.assertTrue(any(c.endswith(f"/chain/{H('c')}") for c in f.calls))
        self.assertFalse(any(c.endswith(f"/chain/{H('d')}") for c in f.calls))

    def test_page_limit_reports_incomplete_and_keeps_old_cursor(self):
        res = U.scan_btc(self.api(), btc_asset(), None, {"max_pages": 1, "now": int(NOW.timestamp())})
        self.assertFalse(res["complete"]); self.assertIsNone(res["cursor"])

    def test_bad_txid_is_rejected_loudly(self):
        f = self.api(); f.gets["https://mempool.space/api/address/%s/txs" % BTC][1]["txid"] = "../../etc"
        with self.assertRaises(ValueError): U.scan_btc(f, btc_asset(), None, {"max_pages": 5, "now": int(NOW.timestamp())})


class Run(unittest.TestCase):
    def test_records_once_with_price_metadata_and_never_twice(self):
        f = TestBtcFake.make()
        led, st, pr, code, out = go(cfg(btc_asset()), api=f)
        e = [x for x in led["entries"] if x["tx_hash"] == H("b")][0]
        self.assertEqual((e["asset_id"], e["amount_native"], e["usd_value"], e["status"]), ("BTC", "1", 100000.0, "confirmed"))
        self.assertEqual((e["price_usd"], e["price_source"], e["price_at"]), ("100000", "test", "2026-10-13T11:59:00Z"))
        self.assertEqual(pr["verified_raised_usd"], 300000.0); self.assertEqual(st["cursors"]["BTC"], {"height": 203}); self.assertEqual(code, 0)
        n = len(led["entries"])
        led2, st2, pr2, code2, _ = go(cfg(btc_asset()), ledger=led, state=st, api=TestBtcFake.make(), prog=pr)
        self.assertEqual(len(led2["entries"]), n); self.assertEqual(pr2, pr)              # idempotent, "updated" unchanged
        self.assertEqual(Z.append_only_problems(led["entries"], led2["entries"]), [])

    def test_price_failure_records_nothing_and_does_not_move_the_cursor(self):
        def boom(a): raise U.PriceError("no price")
        led, st, pr, code, out = go(cfg(btc_asset()), api=TestBtcFake.make(), price=boom)
        self.assertEqual(led["entries"], []); self.assertNotIn("BTC", st["cursors"]); self.assertEqual(st["failures"]["BTC"], 1)
        self.assertEqual(code, 0)                                                         # one failure is not yet an alarm

    def test_three_failures_in_a_row_become_visible_as_incomplete(self):
        st = {}
        for _ in range(3):
            def boom(a): raise U.PriceError("x")
            _, st, pr, code, _ = go(cfg(btc_asset()), state=st, api=TestBtcFake.make(), price=boom)
        self.assertEqual(pr["coverage"]["incomplete"], ["BTC"]); self.assertEqual(code, 2)

    def test_dust_and_excluded(self):
        px = lambda a: (Decimal("100000"), "test", "t")
        led, *_ = go(cfg(btc_asset(), exclude_tx=[f"BTC|{H('d')}"], min_usd=1), api=TestBtcFake.make(dust=True), price=px)
        by = {e["tx_hash"]: e for e in led["entries"]}
        self.assertEqual(by[H("d")]["status"], "excluded"); self.assertIn("note", by[H("d")])
        self.assertEqual(by[H("b")]["status"], "confirmed")
        self.assertEqual(by[H("e")]["status"], "dust")                                    # 1 sat = $0.001
        self.assertEqual(Z.raised(led["entries"]), Decimal("100000.00"))                  # only "confirmed" counts

    def test_manual_only_assets_are_listed_not_scanned(self):
        bnb = {"id": "BNB", "symbol": "BNB", "network": "BSC", "address": EVM, "enabled": True, "track": {"kind": "evm_native", "scan": False}}
        led, st, pr, code, _ = go(cfg(bnb), api=FakeApi(), balancers={})
        self.assertEqual(pr["coverage"]["manual_only"], ["BNB"]); self.assertEqual(pr["coverage"]["auto_tracked"], [])

    def test_disabled_assets_are_ignored(self):
        a = btc_asset(); a["enabled"] = False
        _, _, pr, _, _ = go(cfg(a), api=FakeApi())
        self.assertEqual(pr["coverage"]["auto_tracked"], [])


class TestBtcFake:
    @staticmethod
    def make(dust=False):
        f = Btc().api()
        if dust:
            base = "https://mempool.space/api"
            f.gets[f"{base}/address/{BTC}/txs/chain/{H('d')}"] = [btc_tx(H("e"), 118, T0 - 10, 1)]
            f.gets[f"{base}/address/{BTC}/txs/chain/{H('e')}"] = []
        return f


class Balance(unittest.TestCase):
    def setUp(self):
        self.recorded = {"entries": [{"asset_id": "BTC", "asset": "BTC", "network": "Bitcoin", "tx_hash": H("a"), "amount_native": "1", "usd_value": 100000.0,
                                      "timestamp": "x", "ts": T0, "status": "confirmed", "source": "manual"}]}
        self.quiet = {"btc": lambda api, a, cur, lim: U.result([], [], cur, True)}

    def go(self, bal, state=None, pending=None, **kw):
        sc = {"btc": lambda api, a, cur, lim: U.result([], pending or [], cur, True)}
        return go(cfg(btc_asset()), ledger=self.recorded, state=state, api=FakeApi(), scanners=sc, balancers={"btc": lambda api, a: Decimal(bal)}, rechecks={}, **kw)

    def test_wallet_holding_more_is_reported_only_when_it_persists(self):
        _, st, pr, code, _ = self.go("1.01")                                   # 0.01 BTC = $1000 unexplained, first sighting
        self.assertEqual((pr["coverage"]["balance_check"]["discrepancies"], code), ([], 0))
        _, st, pr, code, _ = self.go("1.01", state=st)                         # second run in a row -> reported
        self.assertEqual(pr["coverage"]["balance_check"]["discrepancies"], [{"asset": "BTC"}]); self.assertEqual(code, 2)
        _, st, pr, code, _ = self.go("1", state=st)                            # explained again -> flag clears
        self.assertEqual((pr["coverage"]["balance_check"]["discrepancies"], code), ([], 0))

    def test_spending_money_is_not_a_discrepancy(self):
        for _ in range(2): _, st, pr, code, _ = self.go("0.2")
        self.assertEqual(code, 0)

    def test_receipts_still_waiting_for_confirmations_explain_the_balance(self):
        for _ in range(3): _, st, pr, code, _ = self.go("1.01", pending=[Decimal("0.01")])
        self.assertEqual(code, 0)

    def test_small_excess_under_tolerance_is_ignored(self):
        for _ in range(3): _, st, pr, code, _ = self.go("1.00002")             # $2 < $5
        self.assertEqual(code, 0)

    def test_excluded_test_transfer_explains_balance_but_not_the_total(self):
        self.recorded["entries"].append({"asset_id": "BTC", "asset": "BTC", "network": "Bitcoin", "tx_hash": H("b"), "amount_native": "0.5", "usd_value": 50000.0,
                                         "timestamp": "x", "ts": T0 + 1, "status": "excluded", "source": "auto", "note": "my own test"})
        for _ in range(3): _, st, pr, code, _ = self.go("1.5")
        self.assertEqual(code, 0); self.assertEqual(pr["verified_raised_usd"], 100000.0)

    def test_balance_error_is_not_counted_as_checked(self):
        def bad(api, a): raise U.ApiError("down")
        _, _, pr, code, _ = go(cfg(btc_asset()), ledger=self.recorded, api=FakeApi(), scanners=self.quiet, balancers={"btc": bad}, rechecks={})
        self.assertEqual(pr["coverage"]["balance_check"]["checked"], [])


class Recheck(unittest.TestCase):
    def test_entry_missing_on_chain_twice_is_flagged_not_deleted(self):
        led = {"entries": [{"asset_id": "BTC", "asset": "BTC", "network": "Bitcoin", "tx_hash": H("a"), "amount_native": "1", "usd_value": 1.0,
                            "timestamp": "x", "ts": T0, "status": "confirmed", "source": "auto"}]}
        sc = {"btc": lambda api, a, cur, lim: U.result([], [], cur, True)}; ch = {"btc": lambda api, a, e: False}
        st = {}
        for i in range(2):
            l2, st, pr, code, _ = go(cfg(btc_asset()), ledger=led, state=st, api=FakeApi(), scanners=sc, balancers={}, rechecks=ch)
        self.assertEqual(pr["coverage"]["recheck"]["unconfirmed"], [f"BTC|{H('a')}"]); self.assertEqual(code, 2)
        self.assertEqual(l2["entries"], led["entries"])                                    # nothing was removed or changed
        self.assertEqual(pr["verified_raised_usd"], 1.0)                                    # a human decides (status -> disputed + note)

    def test_unknown_answer_is_not_a_flag(self):
        led = {"entries": [{"asset_id": "BTC", "asset": "BTC", "network": "Bitcoin", "tx_hash": H("a"), "amount_native": "1", "usd_value": 1.0,
                            "timestamp": "x", "ts": T0, "status": "confirmed", "source": "auto"}]}
        def err(api, a, e): raise U.ApiError("down")
        for _ in range(3):
            _, st, pr, code, _ = go(cfg(btc_asset()), ledger=led, api=FakeApi(), scanners={"btc": lambda *x: U.result([], [], None, True)}, balancers={}, rechecks={"btc": err})
        self.assertEqual(code, 0)

    def test_chk_btc_treats_404_as_gone_and_other_errors_as_unknown(self):
        f = FakeApi(); url = f"https://mempool.space/api/tx/{H('a')}/status"; e = {"tx_hash": H("a")}
        f.gets[url] = urllib.error.HTTPError(url, 404, "nf", {}, None)
        self.assertFalse(U.chk_btc(f, btc_asset(), e))
        f.gets[url] = urllib.error.HTTPError(url, 500, "boom", {}, None)
        with self.assertRaises(urllib.error.HTTPError): U.chk_btc(f, btc_asset(), e)
        f.gets[url] = {"confirmed": True}; self.assertTrue(U.chk_btc(f, btc_asset(), e))


class Evm(unittest.TestCase):
    base = "https://eth.blockscout.com/api/v2/addresses/" + EVM

    def native(self):
        f = FakeApi(); f.rpcs["eth_blockNumber"] = hex(1000)
        tx = lambda h, blk, val, status="ok", to=EVM: {"hash": h, "value": val, "status": status, "block_number": blk, "to": {"hash": to}, "timestamp": "2026-10-10T10:00:00.000000Z"}
        f.gets[f"{self.base}/transactions?filter=to"] = {"items": [tx(EH("1"), 900, str(10 ** 18)), tx(EH("2"), 900, "5", "error"), tx(EH("3"), 900, "5", "ok", "0x" + "9" * 40)], "next_page_params": {"block_number": 900, "index": 1}}
        f.gets[f"{self.base}/transactions?filter=to&block_number=900&index=1"] = {"items": [tx(EH("4"), 995, "2000000000000000000")], "next_page_params": None}   # 6 confirmations -> pending
        f.gets[f"{self.base}/internal-transactions?filter=to"] = {"items": [
            {"transaction_hash": EH("5"), "index": 7, "value": str(3 * 10 ** 17), "success": True, "type": "call", "block_number": 800, "to": {"hash": EVM.lower()}, "timestamp": "2026-10-09T10:00:00Z"},
            {"transaction_hash": EH("6"), "index": 1, "value": "9", "success": False, "type": "call", "block_number": 800, "to": {"hash": EVM}, "timestamp": "2026-10-09T10:00:00Z"},
            {"transaction_hash": EH("7"), "index": 2, "value": "9", "success": True, "type": "delegatecall", "block_number": 800, "to": {"hash": EVM}, "timestamp": "2026-10-09T10:00:00Z"}], "next_page_params": None}
        return f

    def test_native_plain_and_contract_sent_transfers(self):
        res = U.scan_evm_native(self.native(), eth_asset(), None, {"max_pages": 10, "now": int(NOW.timestamp())})
        got = {(r["tx"], r["sub"]): r["amount"] for r in res["receipts"]}
        self.assertEqual(got, {(EH("1"), ""): Decimal(1), (EH("5"), "i7"): Decimal("0.3")})
        self.assertEqual(res["pending"], [Decimal(2)]); self.assertTrue(res["complete"]); self.assertEqual(res["cursor"], {"block": 989})

    def test_native_end_to_end_keys_include_sub_index(self):
        led, st, pr, code, _ = go(cfg(eth_asset()), api=self.native(), balancers={}, rechecks={}, price=lambda a: (Decimal("4000"), "t", "t"))
        self.assertEqual(sorted(Z.entry_key(e) for e in led["entries"]), sorted([f"ETH|{EH('1')}", f"ETH|{EH('5')}|i7"]))
        self.assertEqual(pr["verified_raised_usd"], 5200.0)

    def tok(self, token, to=EVM, val="2500000", h=EH("a"), idx=4):
        return {"token": {"address_hash": token, "symbol": "USDT"}, "to": {"hash": to}, "total": {"value": val, "decimals": "6"}, "transaction_hash": h, "log_index": idx, "block_number": 800, "timestamp": "2026-10-09T10:00:00Z"}

    def test_erc20_only_the_configured_contract_counts_fake_usdt_is_rejected(self):
        f = FakeApi(); f.rpcs["eth_blockNumber"] = hex(1000)
        f.gets[f"{self.base}/token-transfers?type=ERC-20&filter=to&token={USDT}"] = {"items": [
            self.tok(USDT), self.tok(FAKE_TOKEN, h=EH("b")), self.tok(USDT.lower(), h=EH("c"), idx=1), self.tok(USDT, to="0x" + "8" * 40, h=EH("d"))], "next_page_params": None}
        res = U.scan_evm_token(f, usdt_asset(), None, {"max_pages": 5, "now": int(NOW.timestamp())})
        self.assertEqual({(r["tx"], r["sub"]) for r in res["receipts"]}, {(EH("a"), "l4"), (EH("c"), "l1")})
        self.assertEqual(res["rejected"], 2); self.assertEqual(res["receipts"][0]["amount"], Decimal("2.5"))

    def test_erc20_priced_at_peg_and_labelled(self):
        f = FakeApi(); f.rpcs["eth_blockNumber"] = hex(1000)
        f.gets[f"{self.base}/token-transfers?type=ERC-20&filter=to&token={USDT}"] = {"items": [self.tok(USDT)], "next_page_params": None}
        cache = {}; led, *_ = go(cfg(usdt_asset()), api=f, balancers={}, rechecks={}, price=lambda a: U.price_for(f, a, NOW, cache))
        e = led["entries"][0]; self.assertEqual((e["usd_value"], e["price_source"], e["sub"], e["asset_id"]), (2.5, "peg:1", "l4", "USDT-ETH"))

    def test_token_balance_uses_balanceof_and_decimals(self):
        f = FakeApi(); seen = []
        f.rpcs["eth_call"] = lambda p: (seen.append(p), hex(12_500_000))[1]
        self.assertEqual(U.bal_evm_token(f, usdt_asset()), Decimal("12.5"))
        self.assertEqual(seen[0][0]["to"], USDT); self.assertTrue(seen[0][0]["data"].startswith("0x70a08231" + "0" * 24 + EVM[2:].lower()))


class Logs(unittest.TestCase):
    def asset(self, **tr): return {**usdt_asset(**tr), "id": "USDT-BSC", "track": {**usdt_asset()["track"], "kind": "evm_token_logs", "rpc": "https://bsc-rpc.publicnode.com", "contract": "0x55d398326f99059fF775485246999027B3197955", "decimals": 18, "min_confirmations": 15, "log_chunk": 100, **tr}}

    def test_needs_start_block_first_time(self):
        f = FakeApi(); f.rpcs["eth_blockNumber"] = hex(1000)
        with self.assertRaises(ValueError): U.scan_evm_token_logs(f, self.asset(), None, {"max_pages": 5, "now": int(NOW.timestamp())})

    def test_chunks_validates_logs_and_resumes(self):
        a = self.asset(start_block=900); c = a["track"]["contract"]; topic_to = U.pad(EVM)
        ranges = []
        def logs(p):
            f0, t0 = int(p[0]["fromBlock"], 16), int(p[0]["toBlock"], 16); ranges.append((f0, t0))
            good = {"address": c.lower(), "topics": [U.TRANSFER_TOPIC, "0x" + "0" * 64, topic_to], "data": hex(5 * 10 ** 18), "blockNumber": hex(950), "transactionHash": EH("a"), "logIndex": hex(2), "removed": False}
            bad = [{**good, "address": FAKE_TOKEN, "transactionHash": EH("b")}, {**good, "removed": True, "transactionHash": EH("c")},
                   {**good, "topics": [U.TRANSFER_TOPIC, "0x0", U.pad("0x" + "7" * 40)], "transactionHash": EH("d")}]
            return ([good] + bad) if f0 <= 950 <= t0 else []
        f = FakeApi(); f.rpcs["eth_blockNumber"] = hex(1000); f.rpcs["eth_getLogs"] = logs; f.rpcs["eth_getBlockByNumber"] = {"timestamp": hex(T0)}
        res = U.scan_evm_token_logs(f, a, None, {"max_pages": 40, "now": int(NOW.timestamp())})
        self.assertEqual([(r["tx"], r["sub"], r["amount"], r["conf"]) for r in res["receipts"]], [(EH("a"), "l2", Decimal(5), 51)])
        self.assertTrue(res["complete"]); self.assertEqual(res["cursor"], {"block": 986}); self.assertEqual(ranges[0], (900, 999)); self.assertEqual(ranges[-1][1], 1000)
        ranges.clear(); f.rpcs["eth_blockNumber"] = hex(1010)
        res2 = U.scan_evm_token_logs(f, a, res["cursor"], {"max_pages": 40, "now": int(NOW.timestamp())})
        self.assertEqual(ranges[0][0], 986 - U.OVERLAP + 1)                                  # resumes behind the cursor, not at start_block

    def test_chunk_limit_makes_partial_progress(self):
        a = self.asset(start_block=0); f = FakeApi(); f.rpcs["eth_blockNumber"] = hex(100000); f.rpcs["eth_getLogs"] = []
        res = U.scan_evm_token_logs(f, a, None, {"max_pages": 3, "now": int(NOW.timestamp())})
        self.assertFalse(res["complete"]); self.assertEqual(res["cursor"], {"block": 299})


class Sol(unittest.TestCase):
    def tx(self, pre, post, keys=None, err=None, t=T0):
        return {"blockTime": t, "meta": {"err": err, "preBalances": pre, "postBalances": post}, "transaction": {"message": {"accountKeys": keys or ["payer", SOLA]}}}

    def test_incoming_only_failed_and_outgoing_are_skipped(self):
        sig = lambda c: c * 88
        f = FakeApi()
        f.rpcs["getSignaturesForAddress"] = lambda p: [{"signature": sig("2"), "err": None}, {"signature": sig("3"), "err": {"x": 1}}, {"signature": sig("4"), "err": None}, {"signature": sig("5"), "err": None}]
        txs = {sig("2"): self.tx([10 ** 10, 0], [10 ** 10 - 2 * 10 ** 9, 2 * 10 ** 9]),
               sig("4"): self.tx([5 * 10 ** 9, 10 ** 9], [4 * 10 ** 9, 2 * 10 ** 9], keys=[SOLA, "other"]),       # we are index 0 = we paid
               sig("5"): self.tx([10 ** 10, 0], [9 * 10 ** 9, 0], err=None)}
        f.rpcs["getTransaction"] = lambda p: txs[p[0]]
        res = U.scan_sol(f, {"id": "SOL", "address": SOLA, "track": {"rpc": "https://api.mainnet-beta.solana.com"}}, None, {"max_pages": 5, "max_lookups": 50, "known": set(), "now": int(NOW.timestamp())})
        self.assertEqual([(r["tx"], r["amount"]) for r in res["receipts"]], [(sig("2"), Decimal(2))]); self.assertEqual(res["cursor"], {"sig": sig("2")})

    def test_known_signatures_are_not_fetched_again_and_lookup_cap_is_incomplete(self):
        sig = lambda c: c * 88; f = FakeApi()
        f.rpcs["getSignaturesForAddress"] = [{"signature": sig("2"), "err": None}, {"signature": sig("3"), "err": None}]
        f.rpcs["getTransaction"] = lambda p: (_ for _ in ()).throw(AssertionError("should not be fetched"))
        res = U.scan_sol(f, {"id": "SOL", "address": SOLA, "track": {"rpc": "r"}}, {"sig": sig("9")}, {"max_pages": 5, "max_lookups": 50, "known": {f"SOL|{sig('2')}", f"SOL|{sig('3')}"}, "now": 1})
        self.assertTrue(res["complete"])
        f.rpcs["getTransaction"] = self.tx([10 ** 10, 0], [0, 10 ** 9])
        res = U.scan_sol(f, {"id": "SOL", "address": SOLA, "track": {"rpc": "r"}}, {"sig": sig("9")}, {"max_pages": 5, "max_lookups": 1, "known": set(), "now": int(NOW.timestamp())})
        self.assertFalse(res["complete"]); self.assertEqual(res["cursor"], {"sig": sig("9")})


class Doge(unittest.TestCase):
    def page(self, n, start):
        return {"context": {"state": 5000}, "data": {DOGE: {"transactions": [{"block_id": 4000 - start - i, "hash": f"{(start + i):064x}", "time": "2026-10-10 10:00:00", "balance_change": 10 ** 10} for i in range(n)]}}}

    def test_offset_paging_mempool_and_negative_changes(self):
        base = "https://api.blockchair.com/dogecoin/dashboards/address/" + DOGE + "?transaction_details=true&limit=100&offset="
        f = FakeApi(); p0 = self.page(100, 0); p0["data"][DOGE]["transactions"][0]["balance_change"] = -5; p0["data"][DOGE]["transactions"][1]["block_id"] = -1
        f.gets[base + "0"] = p0; f.gets[base + "100"] = self.page(3, 100)
        res = U.scan_doge(f, {"id": "DOGE", "address": DOGE, "track": {"api": "https://api.blockchair.com/dogecoin"}}, None, {"max_pages": 10, "now": int(NOW.timestamp())})
        self.assertEqual(len(res["receipts"]), 98 + 3 - 0)                                  # 100 - negative - mempool + 3 on page two
        self.assertTrue(res["complete"]); self.assertEqual(res["cursor"], {"height": 5000 - 20 + 1})


class Prices(unittest.TestCase):
    def api(self, cg, cb, at=None):
        f = FakeApi(); at = at or int(NOW.timestamp()) - 60
        f.gets["https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_last_updated_at=true"] = cg if isinstance(cg, Exception) else {"bitcoin": {"usd": cg, "last_updated_at": at}}
        f.gets["https://api.coinbase.com/v2/prices/BTC-USD/spot"] = cb if isinstance(cb, Exception) else {"data": {"amount": str(cb)}}
        return f

    def a(self): return btc_asset(coinbase="BTC-USD")

    def test_two_sources_agree(self):
        px, src, at = U.price_for(self.api(100000, 100500), self.a(), NOW, {})
        self.assertEqual((px, src), (Decimal(100000), "coingecko+coinbase")); self.assertTrue(at.endswith("Z"))

    def test_sources_disagree_is_refused(self):
        with self.assertRaises(U.PriceError): U.price_for(self.api(100000, 80000), self.a(), NOW, {})

    def test_one_source_down_still_works_and_says_which(self):
        self.assertEqual(U.price_for(self.api(RuntimeError("down"), 99000), self.a(), NOW, {})[:2], (Decimal(99000), "coinbase"))
        self.assertEqual(U.price_for(self.api(101000, RuntimeError("down")), self.a(), NOW, {})[:2], (Decimal(101000), "coingecko"))

    def test_both_down_or_stale_is_an_error(self):
        with self.assertRaises(U.PriceError): U.price_for(self.api(RuntimeError("x"), RuntimeError("y")), self.a(), NOW, {})
        with self.assertRaises(U.PriceError): U.price_for(self.api(100000, RuntimeError("y"), at=int(NOW.timestamp()) - 86400), self.a(), NOW, {})


class Output(unittest.TestCase):
    def test_progress_equals_sum_of_confirmed_entries(self):
        led, st, pr, *_ = go(cfg(btc_asset()), api=TestBtcFake.make(dust=True))
        self.assertEqual(Decimal(str(pr["verified_raised_usd"])), Z.raised(led["entries"]))
        self.assertTrue(all(e["status"] in Z.STATUSES for e in led["entries"]))

    def test_refuses_to_write_if_history_would_change(self):
        def evil(api, a, cur, lim): raise AssertionError
        led = {"entries": [{"asset_id": "BTC", "asset": "BTC", "network": "Bitcoin", "tx_hash": H("a"), "amount_native": "1", "usd_value": 1.0, "timestamp": "x", "ts": 1, "status": "confirmed", "source": "auto"}]}
        orig = Z.append_only_problems; Z.append_only_problems = lambda o, n: ["entry removed: x"]
        try:
            with self.assertRaises(RuntimeError): go(cfg(btc_asset()), ledger=led, api=FakeApi(), scanners={"btc": lambda *x: U.result([], [], None, True)}, balancers={}, rechecks={})
        finally: Z.append_only_problems = orig


if __name__ == "__main__":
    unittest.main()
