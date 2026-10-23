"""Offline tests for XRP / TRON / TRC-20 / HYPE support and for the user's wallet addresses. FAKE responses: they prove the logic,
NOT that TronGrid / the XRPL cluster still answer in this shape (run `python3 scripts/update_data.py --dry-run` for that)."""
import copy, datetime as dt, json, pathlib, sys, unittest
from decimal import Decimal
R = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R / "scripts"))
import update_data as U, ztlib as Z
from verify_addresses import valid

NOW = dt.datetime(2026, 10, 13, 12, 0, tzinfo=dt.timezone.utc); T0 = int(NOW.timestamp()) - 3 * 86400
CFG = json.loads((R / "config.json").read_text()); A = {a["id"]: a for a in CFG["assets"]}
LIM = {"max_pages": 10, "max_lookups": 50, "known": set(), "now": int(NOW.timestamp())}
XA, TA = A["XRP"]["address"], A["TRX"]["address"]; TC = A["USDT-TRC20"]["track"]["contract"]


class Fake:
    def __init__(self): self.gets, self.xr, self.calls = {}, {}, []
    def get(self, url, body=None):
        self.calls.append(url)
        if url not in self.gets: raise AssertionError(f"unexpected GET {url}")
        return copy.deepcopy(self.gets[url])
    def xrpl(self, url, method, params):
        self.calls.append(f"xrpl:{method}"); v = self.xr[method]
        return copy.deepcopy(v(params) if callable(v) else v)


class Addresses(unittest.TestCase):
    def test_every_wallet_address_passes_its_checksum(self):
        for a in CFG["assets"]: self.assertTrue(valid(a["address"], (a.get("track") or {}).get("kind"))[0], a["id"])
        self.assertTrue(valid(TC, "tron")[0]); self.assertTrue(valid(A["USDC-ETH"]["track"]["contract"])[0])

    def test_a_single_wrong_character_is_caught(self):
        self.assertFalse(valid(XA[:-1] + "C", "xrp")[0]); self.assertFalse(valid(TA[:-1] + "F", "tron")[0])
        self.assertFalse(valid(XA, "tron")[0]); self.assertFalse(valid(TA, "xrp")[0])

    def test_hash_formats(self):
        self.assertTrue(Z.tx_ok("xrp", "A" * 64)); self.assertFalse(Z.tx_ok("xrp", "a" * 64)); self.assertTrue(Z.tx_ok("tron", "a" * 64)); self.assertFalse(Z.tx_ok("tron", "0x" + "a" * 64))

    def test_hype_and_bnb_are_manual_but_balance_watched_and_stablecoins_are_pegged(self):
        for i in ("HYPE", "BNB"): self.assertIs(A[i]["track"]["scan"], False); self.assertIn("rpc", A[i]["track"])
        for i in ("USDT-ETH", "USDC-ETH", "USDT-TRC20"): self.assertEqual(A[i]["track"]["peg_usd"], "1")


def xtx(h, led, dest=XA, acct="rOther", res="tesSUCCESS", deliv="25000000", valid_=True, typ="Payment"):
    return {"validated": valid_, "tx": {"TransactionType": typ, "Destination": dest, "Account": acct, "hash": h, "date": T0 - 946684800, "ledger_index": led, "Amount": "999999999999"},
            "meta": {"TransactionResult": res, "delivered_amount": deliv}}


class Xrp(unittest.TestCase):
    def api(self):
        f = Fake(); good = xtx("A" * 64, 1000)
        p1 = {"status": "success", "ledger_index_max": 1005, "marker": {"ledger": 900, "seq": 1}, "transactions": [
            good, xtx("B" * 64, 999, deliv="1000000"),                            # partial payment: only what was DELIVERED counts
            xtx("C" * 64, 998, deliv={"currency": "USD", "value": "5"}),          # token payment
            xtx("D" * 64, 997, res="tecPATH_DRY"), xtx("E" * 64, 996, dest="rSomeoneElse"), xtx("F" * 64, 995, acct=XA),
            xtx("1" * 64, 994, valid_=False), xtx("2" * 64, 993, typ="OfferCreate")]}
        p2 = {"status": "success", "ledger_index_max": 1005, "transactions": [xtx("3" * 64, 800, deliv="2000000")]}
        f.xr["account_tx"] = lambda p: p2 if "marker" in p else p1
        return f

    def test_scan_pages_and_filters(self):
        res = U.scan_xrp(self.api(), A["XRP"], None, LIM)
        self.assertEqual([(r["tx"][0], r["amount"]) for r in res["receipts"]], [("A", Decimal(25)), ("B", Decimal(1)), ("3", Decimal(2))])
        self.assertTrue(res["complete"]); self.assertEqual(res["cursor"], {"ledger": 1005}); self.assertEqual(res["receipts"][0]["ts"], T0)

    def test_stops_below_the_cursor(self):
        f = self.api(); res = U.scan_xrp(f, A["XRP"], {"ledger": 1000}, LIM)  # floor 990 -> page one already reaches 993..: continue; page two is 800 < floor
        self.assertTrue(res["complete"]); self.assertEqual(f.calls.count("xrpl:account_tx"), 2)
        f2 = self.api(); res = U.scan_xrp(f2, A["XRP"], {"ledger": 1000}, {**LIM, "max_pages": 1}); self.assertFalse(res["complete"]); self.assertEqual(res["cursor"], {"ledger": 1000})

    def test_server_error_is_loud(self):
        f = Fake(); f.xr["account_tx"] = {"status": "error", "error": "noNetwork"}
        with self.assertRaises(U.ApiError): U.scan_xrp(f, A["XRP"], None, LIM)

    def test_balance(self):
        f = Fake(); f.xr["account_info"] = {"status": "success", "account_data": {"Balance": "12500000"}}; self.assertEqual(U.bal_xrp(f, A["XRP"]), Decimal("12.5"))
        f.xr["account_info"] = {"status": "error", "error": "actNotFound"}; self.assertEqual(U.bal_xrp(f, A["XRP"]), 0)


class Tron(unittest.TestCase):
    base = "https://api.trongrid.io/v1/accounts/" + TA
    q = "?only_confirmed=true&only_to=true&limit=200&order_by=block_timestamp,desc"

    def trx(self, h, ms, amount=5_000_000, to=None, owner="41" + "aa" * 20, ret="SUCCESS", typ="TransferContract"):
        return {"txID": h, "block_timestamp": ms, "ret": [{"contractRet": ret}], "raw_data": {"contract": [{"type": typ, "parameter": {"value": {"amount": amount, "to_address": to or Z.tron_hex(TA), "owner_address": owner}}}]}}

    def test_native_paging_filters_and_cursor(self):
        ms = T0 * 1000; f = Fake()
        f.gets[f"{self.base}/transactions{self.q}"] = {"data": [self.trx("a" * 64, ms), self.trx("b" * 64, ms - 1, owner=Z.tron_hex(TA)), self.trx("c" * 64, ms - 2, ret="OUT_OF_ENERGY"),
                                                          self.trx("d" * 64, ms - 3, typ="TriggerSmartContract"), self.trx("e" * 64, ms - 4, to="41" + "bb" * 20), self.trx("f" * 64, ms - 5, amount=0)], "meta": {"fingerprint": "fp1"}}
        f.gets[f"{self.base}/transactions{self.q}&fingerprint=fp1"] = {"data": [self.trx("9" * 64, ms - 9, amount=1_500_000)], "meta": {}}
        res = U.scan_tron(f, A["TRX"], None, LIM)
        self.assertEqual([(r["tx"][0], r["amount"]) for r in res["receipts"]], [("a", Decimal(5)), ("9", Decimal("1.5"))])
        self.assertTrue(res["complete"]); self.assertEqual(res["cursor"], {"ts": ms})
        f.gets[f"{self.base}/transactions{self.q}&min_timestamp={ms - 3_600_000}"] = {"data": [], "data_note": 1, "meta": {}}
        self.assertEqual(U.scan_tron(f, A["TRX"], res["cursor"], LIM)["receipts"], [])

    def test_trc20_only_the_configured_contract_summed_per_transaction(self):
        ms = T0 * 1000; f = Fake(); ti = lambda c: {"address": c, "symbol": "USDT", "decimals": 6}
        t = lambda h, v, c=TC, to=TA, typ="Transfer", at=0: {"transaction_id": h, "block_timestamp": ms - at, "token_info": ti(c), "to": to, "type": typ, "value": v}
        f.gets[f"{self.base}/transactions/trc20?only_confirmed=true&only_to=true&limit=200&contract_address={TC}&order_by=block_timestamp,desc"] = {"data": [
            t("a" * 64, "2500000"), t("a" * 64, "500000"),                              # two transfers in one transaction -> one entry, 3 USDT
            t("b" * 64, "9000000", c="TFakeFakeFakeFakeFakeFakeFakeFakeFa"), t("c" * 64, "9000000", to="TOtherOtherOtherOtherOtherOtherOth"), t("d" * 64, "9", typ="Approval")], "meta": {}}
        res = U.scan_tron_trc20(f, A["USDT-TRC20"], None, LIM)
        self.assertEqual([(r["tx"][0], r["amount"]) for r in res["receipts"]], [("a", Decimal(3))]); self.assertEqual(res["rejected"], 3)

    def test_balances(self):
        f = Fake(); f.gets[self.base] = {"data": [{"balance": 5_250_000, "trc20": [{TC: "2500000"}, {"TOther": "9"}]}], "success": True}
        self.assertEqual(U.bal_tron(f, A["TRX"]), Decimal("5.25")); self.assertEqual(U.bal_tron_trc20(f, A["USDT-TRC20"]), Decimal("2.5"))
        f.gets[self.base] = {"data": [], "success": True}; self.assertEqual(U.bal_tron(f, A["TRX"]), 0)


class SpamGuard(unittest.TestCase):
    def test_receipts_below_min_native_are_not_recorded_but_cursor_moves(self):
        a = copy.deepcopy(A["XRP"]); a["track"]["min_native"] = "0.001"
        rec = [{"tx": "A" * 64, "sub": "", "amount": Decimal("0.000001"), "ts": T0, "conf": None}, {"tx": "B" * 64, "sub": "", "amount": Decimal("5"), "ts": T0, "conf": None}]
        out = []; led, st, pr, code = U.run({"assets": [a], "tracking": {"min_usd": 1}}, {"entries": []}, {}, Fake(), NOW, None, price_fn=lambda x: (Decimal(2), "t", "t"),
                                             scanners={"xrp": lambda *x: U.result(rec, [], {"ledger": 5}, True)}, balancers={}, rechecks={}, log=out.append)
        self.assertEqual([e["tx_hash"][0] for e in led["entries"]], ["B"]); self.assertEqual(st["cursors"]["XRP"], {"ledger": 5}); self.assertEqual(pr["verified_raised_usd"], 10.0)


if __name__ == "__main__": unittest.main()
