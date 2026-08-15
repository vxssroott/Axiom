"""
Axiom billing tests (Python stdlib unittest).

Run:  python3 -m unittest discover -s tests -p 'billing_test.py' -v
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest import mock

# License expiry is checked against the real clock; anchor licenses to now.
_NOW = int(time.time())

_TMP_ENV = tempfile.mkdtemp(prefix="axiom-billing-env-")
os.environ["BILLING_LEDGER_PATH"] = os.path.join(_TMP_ENV, "ledger.db")
os.environ["BILLING_USAGE_PATH"] = os.path.join(_TMP_ENV, "usage.db")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import billing_core as core  # noqa: E402
import billing  # noqa: E402


def _btc_tx(value_sats: int, confirmed: bool = True, block_time: int = _NOW,
            block_height: int = 900_000) -> dict:
    status = {"confirmed": confirmed, "block_height": block_height, "block_time": block_time} \
        if confirmed else {}
    return {"vout": [{"scriptpubkey_address": core.PAY["btc"], "value": value_sats}], "status": status}


def _usdt_tx(value_raw: int, success: bool = True, timestamp: int = _NOW) -> dict:
    return {
        "success": success, "confirmations": 42 if success else 0, "timestamp": timestamp,
        "operations": [
            {"type": "transfer", "contract": core.PAY["usdtContract"], "to": core.PAY["usdt"],
             "tokenInfo": {"decimals": 6}, "value": value_raw},
        ],
    }


class PlanTests(unittest.TestCase):
    def test_plans_have_monthly_annual_once(self):
        for pid in ("pro", "team"):
            self.assertIn("monthly", core.PLANS[pid]["price"])
            self.assertIn("annual", core.PLANS[pid]["price"])
        self.assertIn("once", core.PLANS["life"]["price"])

    def test_allowances(self):
        self.assertEqual(core.allowance_for("pro", "monthly"), 500_000)
        self.assertEqual(core.allowance_for("pro", "annual"), 5_000_000)
        self.assertEqual(core.allowance_for("team", "monthly"), 2_000_000)
        self.assertIsNone(core.allowance_for("life", "once"))
        self.assertEqual(core.allowance_for("free", "monthly"), 10_000)

    def test_expiry(self):
        issued = 1_000_000
        self.assertEqual(core.compute_expiry("monthly", issued), issued + 30 * 86400)
        self.assertEqual(core.compute_expiry("annual", issued), issued + 365 * 86400)
        self.assertIsNone(core.compute_expiry("once", issued))

    def test_all_prices_pairwise_distinct(self):
        # One payment must map to exactly one plan/period (chain-enforced binding).
        for asset in ("btc", "usdt"):
            seen = {}
            for pid in ("pro", "team", "life"):
                for billing, price in core.PLANS[pid]["price"].items():
                    amt = str(price[asset])
                    self.assertNotIn(amt, seen, f"duplicate {asset} amount {amt} for {pid}/{billing}")
                    seen[amt] = (pid, billing)

    def test_min_btc_confirmations_default(self):
        self.assertGreaterEqual(core.MIN_BTC_CONFIRMATIONS, 1)


class LicenseTests(unittest.TestCase):
    def setUp(self):
        self.secret = "test-secret-please-ignore"

    def test_roundtrip(self):
        token = core.issue_license(self.secret, "pro", "monthly", "abc123", "btc", "0.00055", True, _NOW)
        payload = core.verify_license(self.secret, token)
        self.assertEqual(payload["plan"], "pro")
        self.assertEqual(payload["billing"], "monthly")
        self.assertEqual(payload["allowance"], 500_000)
        self.assertEqual(payload["txid"], "abc123")
        self.assertEqual(payload["issued_at"], _NOW)
        self.assertEqual(payload["expires_at"], _NOW + 30 * 86400)

    def test_deterministic_identical_for_same_payment(self):
        a = core.issue_license(self.secret, "pro", "monthly", "abc", "btc", "0.00055", True, 1_700_000_000)
        b = core.issue_license(self.secret, "pro", "monthly", "abc", "btc", "0.00055", True, 1_700_000_000)
        self.assertEqual(a, b)  # stateless: any instance, any time -> same token
        c = core.issue_license(self.secret, "pro", "monthly", "abc", "btc", "0.00055", True, 1_700_000_001)
        self.assertNotEqual(a, c)  # different on-chain anchor -> different license

    def test_tamper_detected(self):
        token = core.issue_license(self.secret, "pro", "monthly", "abc", "usdt", "49", True, 1_700_000_000)
        body, sig = token.split(".")
        tampered = body + "." + sig[:-2] + ("A" if sig[-2] != "A" else "B")
        with self.assertRaises(core.BillingError):
            core.verify_license(self.secret, tampered)

    def test_wrong_secret_detected(self):
        token = core.issue_license(self.secret, "pro", "monthly", "abc", "btc", "0.00055", True, 1_700_000_000)
        with self.assertRaises(core.BillingError):
            core.verify_license("other-secret", token)

    def test_expired_rejected(self):
        payload = {
            "v": 1, "plan": "pro", "billing": "monthly", "allowance": 500_000,
            "issued_at": 1, "expires_at": 2, "txid": "x", "asset": "btc",
            "paid": "0.00055", "confirmed": True,
        }
        token = core._sign(self.secret, payload)
        with mock.patch.object(core, "_now", return_value=3):
            with self.assertRaises(core.BillingError):
                core.verify_license(self.secret, token)

    def test_bad_billing_period_rejected(self):
        with self.assertRaises(core.BillingError):
            core.issue_license(self.secret, "pro", "once", "abc", "btc", "0.00055", True, 1_700_000_000)
        with self.assertRaises(core.BillingError):
            core.issue_license(self.secret, "life", "monthly", "abc", "btc", "0.01100", True, 1_700_000_000)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="axiom-ledger-")
        self.path = os.path.join(self.dir, "ledger.db")
        self.ledger = core.SqliteLedger(self.path)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_record_and_find(self):
        entry = self.ledger.record("TX1", "pro", "monthly", "btc", "token-1", 1)
        self.assertEqual(entry["txid"], "tx1")
        found = self.ledger.find("tx1")  # case-insensitive normalization
        self.assertEqual(found["license"], "token-1")
        self.assertIsNone(self.ledger.find("unknown"))

    def test_record_first_write_wins(self):
        # Audit ledger semantics: the first claim of a txid is the record.
        self.ledger.record("TX1", "pro", "monthly", "btc", "token-A", 1)
        self.ledger.record("TX1", "team", "annual", "usdt", "token-B", 2)
        entry = self.ledger.find("tx1")
        self.assertEqual(entry["license"], "token-A")
        self.assertEqual(entry["plan"], "pro")

    def test_replay_detected_in_ledger(self):
        self.ledger.record("TX1", "pro", "monthly", "btc", "token-1", 1)
        self.assertIsNotNone(self.ledger.find("TX1"))


class VerificationTests(unittest.TestCase):
    def test_verify_btc_paid(self):
        with mock.patch.object(core, "_fetch_json", return_value=_btc_tx(55_000)):
            r = core.verify_btc("abc", "0.00055")
        self.assertEqual(r["state"], "active")
        self.assertTrue(r["confirmed"])
        self.assertEqual(r["paid"], "0.00055")
        self.assertEqual(r["anchor"], _NOW)

    def test_verify_btc_pending_zero_confirmations(self):
        with mock.patch.object(core, "_fetch_json", return_value=_btc_tx(55_000, confirmed=False)):
            r = core.verify_btc("abc", "0.00055")
        self.assertEqual(r["state"], "pending")
        self.assertFalse(r["confirmed"])
        self.assertNotIn("anchor", r)

    def test_verify_btc_min_confirmations_gating(self):
        tx = _btc_tx(55_000)
        with mock.patch.object(core, "_fetch_json", return_value=tx), \
             mock.patch.object(core, "_fetch_int", return_value=900_000):  # tip == height -> 1 conf
            r = core.verify_btc("abc", "0.00055", min_confirmations=2)
        self.assertEqual(r["state"], "pending")
        self.assertEqual(r["confirmations"], 1)
        with mock.patch.object(core, "_fetch_json", return_value=tx), \
             mock.patch.object(core, "_fetch_int", return_value=900_001):  # 2 confs -> active
            r = core.verify_btc("abc", "0.00055", min_confirmations=2)
        self.assertEqual(r["state"], "active")
        self.assertEqual(r["confirmations"], 2)

    def test_verify_btc_underpaid(self):
        with mock.patch.object(core, "_fetch_json", return_value=_btc_tx(1_000)):
            with self.assertRaises(core.VerificationError):
                core.verify_btc("abc", "0.00055")

    def test_verify_btc_overpaid(self):
        # Exact-amount binding: an over-payment cannot be claimed either.
        with mock.patch.object(core, "_fetch_json", return_value=_btc_tx(220_000)):
            with self.assertRaises(core.VerificationError):
                core.verify_btc("abc", "0.00055")

    def test_verify_btc_wrong_address(self):
        tx = {"vout": [{"scriptpubkey_address": "bc1qother", "value": 55_000}], "status": {}}
        with mock.patch.object(core, "_fetch_json", return_value=tx):
            with self.assertRaises(core.VerificationError):
                core.verify_btc("abc", "0.00055")

    def test_verify_btc_not_found(self):
        with mock.patch.object(core, "_fetch_json", side_effect=urllib.error.HTTPError(
                "url", 404, "nope", {}, io.BytesIO())):
            with self.assertRaises(core.VerificationError):
                core.verify_btc("abc", "0.00055")

    def test_verify_usdt_paid(self):
        with mock.patch.object(core, "_fetch_json", return_value=_usdt_tx(49_000000)):
            r = core.verify_usdt("0xabc", "49")
        self.assertEqual(r["state"], "active")
        self.assertTrue(r["confirmed"])
        self.assertEqual(r["paid"], "49")

    def test_verify_usdt_pending(self):
        with mock.patch.object(core, "_fetch_json", return_value=_usdt_tx(49_000000, success=False)):
            r = core.verify_usdt("0xabc", "49")
        self.assertEqual(r["state"], "pending")
        self.assertFalse(r["confirmed"])

    def test_verify_usdt_rejects_other_token(self):
        tx = {
            "success": True, "confirmations": 42, "timestamp": 1,
            "operations": [
                {"type": "transfer", "contract": "0x1111111111111111111111111111111111111111",
                 "to": core.PAY["usdt"], "tokenInfo": {"decimals": 6}, "value": 49_000000},
            ],
        }
        with mock.patch.object(core, "_fetch_json", return_value=tx):
            with self.assertRaises(core.VerificationError):
                core.verify_usdt("0xabc", "49")

    def test_verify_usdt_rejects_wrong_recipient(self):
        tx = {
            "success": True, "confirmations": 42, "timestamp": 1,
            "operations": [
                {"type": "transfer", "contract": core.PAY["usdtContract"],
                 "to": "0x9999999999999999999999999999999999999999",
                 "tokenInfo": {"decimals": 6}, "value": 49_000000},
            ],
        }
        with mock.patch.object(core, "_fetch_json", return_value=tx):
            with self.assertRaises(core.VerificationError):
                core.verify_usdt("0xabc", "49")


class AddressRegressionTests(unittest.TestCase):
    """The wallet addresses must stay byte-identical to public/axiom.html."""

    def test_addresses_match_ui(self):
        path = os.path.join(os.path.dirname(__file__), "..", "public", "axiom.html")
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        m = re.search(r"const PAY = \{(.*?)\};", html, re.S)
        self.assertIsNotNone(m, "PAY constant not found in public/axiom.html")
        block = m.group(1)
        ui_btc = re.search(r'btc:\s*"([^"]+)"', block).group(1)
        ui_usdt = re.search(r'usdt:\s*"([^"]+)"', block).group(1)
        ui_contract = re.search(r'usdtContract:\s*"([^"]+)"', block).group(1)
        self.assertEqual(ui_btc, core.PAY["btc"])
        self.assertEqual(ui_usdt, core.PAY["usdt"])
        self.assertEqual(ui_contract, core.PAY["usdtContract"])


class _WSGIBase(unittest.TestCase):
    secret = "test-secret-please-ignore"

    def _call(self, method, path, body=None, qs="", content_type="application/json"):
        captured = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = dict(headers)

        env = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": qs,
            "CONTENT_TYPE": content_type,
            "REMOTE_ADDR": "127.0.0.1",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            env["CONTENT_LENGTH"] = str(len(data))
            env["wsgi.input"] = io.BytesIO(data)
        else:
            env["CONTENT_LENGTH"] = "0"
            env["wsgi.input"] = io.BytesIO(b"")
        result = b"".join(billing.app(env, start_response))
        return captured["status"], captured["headers"], json.loads(result.decode("utf-8"))


class WSGITests(_WSGIBase):
    def setUp(self):
        self._old_secret = billing.SECRET
        billing.SECRET = self.secret
        self.dir = tempfile.mkdtemp(prefix="axiom-billing-")
        billing.LEDGER = core.SqliteLedger(os.path.join(self.dir, "ledger.db"))
        billing.USAGE = core.SqliteUsageStore(os.path.join(self.dir, "usage.db"))
        billing._events.clear()

    def tearDown(self):
        billing.SECRET = self._old_secret
        billing._events.clear()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_verify_issues_signed_license(self):
        with mock.patch.object(core, "_fetch_json", return_value=_btc_tx(55_000)):
            status, _, payload = self._call(
                "POST", "/api/billing/verify",
                {"txid": "e1" * 32, "asset": "btc", "plan": "pro", "billing": "monthly"})
        self.assertEqual(status.split()[0], "201", payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"], "active")
        decoded = core.verify_license(self.secret, payload["license"])
        self.assertEqual(decoded["plan"], "pro")
        self.assertEqual(decoded["allowance"], 500_000)
        self.assertTrue(decoded["confirmed"])

    def test_verify_idempotent_same_plan(self):
        with mock.patch.object(core, "_fetch_json", return_value=_btc_tx(55_000)):
            _, _, first = self._call("POST", "/api/billing/verify",
                                     {"txid": "e2" * 32, "asset": "btc", "plan": "pro", "billing": "monthly"})
            status, _, second = self._call("POST", "/api/billing/verify",
                                           {"txid": "e2" * 32, "asset": "btc", "plan": "pro", "billing": "monthly"})
        self.assertEqual(first["license"], second["license"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(status.split()[0], "200")

    def test_verify_replay_refused_for_other_plan(self):
        with mock.patch.object(core, "_fetch_json", return_value=_btc_tx(220_000)):
            self._call("POST", "/api/billing/verify",
                       {"txid": "e3" * 32, "asset": "btc", "plan": "team", "billing": "monthly"})
            status, _, payload = self._call("POST", "/api/billing/verify",
                                            {"txid": "e3" * 32, "asset": "btc", "plan": "pro", "billing": "monthly"})
        self.assertEqual(status.split()[0], "409")
        self.assertIn("already used", payload["message"])

    def test_zero_conf_btc_pending_issues_no_license(self):
        txid = "ab" * 32
        with mock.patch.object(core, "_fetch_json", return_value=_btc_tx(55_000, confirmed=False)):
            status, _, payload = self._call("POST", "/api/billing/verify",
                                            {"txid": txid, "asset": "btc", "plan": "pro", "billing": "monthly"})
        self.assertEqual(status.split()[0], "202")
        self.assertEqual(payload["state"], "pending")
        self.assertNotIn("license", payload)
        self.assertIsNone(billing.LEDGER.find(txid))

    def test_pending_promotes_to_active_after_confirmation(self):
        txid = "cd" * 32
        with mock.patch.object(core, "_fetch_json", return_value=_btc_tx(55_000, confirmed=False)):
            s1, _, p1 = self._call("POST", "/api/billing/verify",
                                   {"txid": txid, "asset": "btc", "plan": "pro", "billing": "monthly"})
        self.assertEqual(s1.split()[0], "202")
        with mock.patch.object(core, "_fetch_json", return_value=_btc_tx(55_000)):
            s2, _, p2 = self._call("POST", "/api/billing/verify",
                                   {"txid": txid, "asset": "btc", "plan": "pro", "billing": "monthly"})
        self.assertEqual(s2.split()[0], "201")
        self.assertEqual(p2["state"], "active")
        decoded = core.verify_license(self.secret, p2["license"])
        self.assertTrue(decoded["confirmed"])
        # Replay after promotion is idempotent with the same license.
        with mock.patch.object(core, "_fetch_json", return_value=_btc_tx(55_000)):
            s3, _, p3 = self._call("POST", "/api/billing/verify",
                                   {"txid": txid, "asset": "btc", "plan": "pro", "billing": "monthly"})
        self.assertEqual(p3["license"], p2["license"])

    def test_usdt_pending_no_license(self):
        with mock.patch.object(core, "_fetch_json", return_value=_usdt_tx(49_000000, success=False)):
            status, _, payload = self._call("POST", "/api/billing/verify",
                                            {"txid": "ab" * 32, "asset": "usdt", "plan": "pro", "billing": "monthly"})
        self.assertEqual(status.split()[0], "202")
        self.assertEqual(payload["state"], "pending")

    def test_usage_and_status(self):
        token = core.issue_license(self.secret, "pro", "monthly", "e4" * 32, "btc", "0.00055", True, _NOW)
        status, _, usage = self._call("POST", "/api/billing/usage",
                                      {"license": token, "amount": 1234})
        self.assertEqual(status.split()[0], "200")
        self.assertEqual(usage["used"], 1234)
        self.assertEqual(usage["remaining"], 500_000 - 1234)
        status, _, info = self._call("GET", "/api/billing/status", qs="license=" + token)
        self.assertEqual(status.split()[0], "200")
        self.assertEqual(info["plan"], "pro")
        self.assertEqual(info["used"], 1234)
        self.assertEqual(info["txid"], "e4" * 32)

    def test_usage_rejects_forged_license(self):
        fake = core._sign("wrong-secret", {"v": 1, "plan": "pro", "billing": "monthly",
                                           "allowance": 500_000, "issued_at": 1, "expires_at": 9999999999,
                                           "txid": "x", "asset": "btc", "paid": "1", "confirmed": True})
        status, _, payload = self._call("POST", "/api/billing/usage",
                                        {"license": fake, "amount": 1})
        self.assertEqual(status.split()[0], "400")
        self.assertIn("signature", payload["message"])

    def test_usage_rejects_expired_license(self):
        payload = {"v": 1, "plan": "pro", "billing": "monthly", "allowance": 500_000,
                   "issued_at": 1, "expires_at": 2, "txid": "x", "asset": "btc",
                   "paid": "0.00055", "confirmed": True}
        token = core._sign(self.secret, payload)
        with mock.patch.object(core, "_now", return_value=3):
            status, _, payload = self._call("POST", "/api/billing/usage",
                                            {"license": token, "amount": 1})
        self.assertEqual(status.split()[0], "400")
        self.assertIn("expired", payload["message"])

    def test_no_secret_returns_503(self):
        billing.SECRET = ""
        status, _, payload = self._call("POST", "/api/billing/verify",
                                        {"txid": "x", "asset": "btc", "plan": "pro", "billing": "monthly"})
        self.assertEqual(status.split()[0], "503")
        self.assertEqual(payload["error"], "billing_not_configured")

    def test_unknown_route_404(self):
        status, _, _ = self._call("GET", "/api/billing/nope")
        self.assertEqual(status.split()[0], "404")

    def test_cors_preflight(self):
        captured = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = dict(headers)

        billing.app({
            "REQUEST_METHOD": "OPTIONS", "PATH_INFO": "/api/billing/verify", "QUERY_STRING": "",
            "CONTENT_LENGTH": "0", "wsgi.input": io.BytesIO(b""),
        }, start_response)
        self.assertEqual(captured["status"], "204 No Content")
        self.assertIn("Access-Control-Allow-Origin", captured["headers"])

    def test_body_too_large_413(self):
        status, _, payload = self._call("POST", "/api/billing/verify",
                                        {"txid": "a" * 20_000, "asset": "btc", "plan": "pro", "billing": "monthly"})
        self.assertEqual(status.split()[0], "413")

    def test_wrong_content_type_415(self):
        status, _, _ = self._call("POST", "/api/billing/verify",
                                  {"txid": "ab" * 32}, content_type="text/plain")
        self.assertEqual(status.split()[0], "415")

    def test_rate_limit_429(self):
        billing._events.clear()
        try:
            with mock.patch.object(billing, "_RATE_MAX", 5):
                with mock.patch.object(core, "_fetch_json", return_value=_btc_tx(55_000)):
                    codes = []
                    for _ in range(8):
                        s, _, _ = self._call("POST", "/api/billing/verify",
                                             {"txid": "ab" * 32, "asset": "btc", "plan": "pro", "billing": "monthly"})
                        codes.append(s.split()[0])
        finally:
            billing._events.clear()
        self.assertIn("429", codes)
        self.assertEqual(len([c for c in codes if c != "429"]), 5)

    def test_secret_never_in_responses(self):
        with mock.patch.object(core, "_fetch_json", return_value=_btc_tx(55_000)):
            _, _, payload = self._call("POST", "/api/billing/verify",
                                       {"txid": "ab" * 32, "asset": "btc", "plan": "pro", "billing": "monthly"})
        self.assertNotIn(self.secret, json.dumps(payload))


class ServerlessMountTests(_WSGIBase):
    """The billing WSGI app must serve identically whether the platform passes
    the full path ('/api/billing/consume') or a prefix-stripped path
    ('/consume') — both mounting conventions are used by serverless hosts.
    """

    def setUp(self):
        self._old_secret = billing.SECRET
        billing.SECRET = self.secret
        self.dir = tempfile.mkdtemp(prefix="axiom-billing-")
        billing.LEDGER = core.SqliteLedger(os.path.join(self.dir, "ledger.db"))
        billing.USAGE = core.SqliteUsageStore(os.path.join(self.dir, "usage.db"))
        billing._events.clear()

    def tearDown(self):
        billing.SECRET = self._old_secret
        billing._events.clear()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_consume_stripped_path_matches_full_path(self):
        lic = core.issue_license(self.secret, "pro", "monthly", "ab" * 32, "btc", "0.00055", True, _NOW)
        # Stripped-path mount: platform passes only '/consume'.
        status, _, payload = self._call(
            "POST", "/consume",
            {"license": lic, "units": 1000, "operation": "agent.call", "request_id": "mount-test-0001"})
        self.assertEqual(status.split()[0], "200", payload)
        self.assertTrue(payload["allowed"])
        self.assertEqual(payload["used"], 1000)

    def test_verify_stripped_path_works(self):
        with mock.patch.object(core, "_fetch_json", return_value=_btc_tx(55_000)):
            status, _, payload = self._call(
                "POST", "/verify",
                {"txid": "ff" * 32, "asset": "btc", "plan": "pro", "billing": "monthly"})
        self.assertEqual(status.split()[0], "201", payload)
        self.assertEqual(payload["state"], "active")

    def test_status_stripped_path_works(self):
        lic = core.issue_license(self.secret, "life", "once", "cd" * 32, "usdt", "990", True, 1_700_000_000)
        status, _, payload = self._call("GET", "/status", qs="license=" + lic)
        self.assertEqual(status.split()[0], "200", payload)
        self.assertTrue(payload["ok"])

    def test_gateway_prefixed_path_works(self):
        lic = core.issue_license(self.secret, "life", "once", "ef" * 32, "usdt", "990", True, 1_700_000_000)
        status, _, payload = self._call("GET", "/functions/api/billing/status", qs="license=" + lic)
        self.assertEqual(status.split()[0], "200", payload)
        self.assertTrue(payload["ok"])

    def test_unrelated_paths_still_404(self):
        status, _, payload = self._call("POST", "/foo/verify", {"x": 1})
        self.assertEqual(status.split()[0], "404", payload)
        status, _, payload = self._call("GET", "/other/status")
        self.assertEqual(status.split()[0], "404", payload)


class ConcurrencyTests(_WSGIBase):
    def setUp(self):
        self._old_secret = billing.SECRET
        billing.SECRET = self.secret
        self.dir = tempfile.mkdtemp(prefix="axiom-conc-")
        self.ledger_path = os.path.join(self.dir, "ledger.db")
        self.usage_path = os.path.join(self.dir, "usage.db")
        billing.LEDGER = core.SqliteLedger(self.ledger_path)
        billing.USAGE = core.SqliteUsageStore(self.usage_path)
        billing._events.clear()

    def tearDown(self):
        billing.SECRET = self._old_secret
        billing._events.clear()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_100_concurrent_same_txid_same_plan_mints_exactly_one_license(self):
        txid = "ab" * 32
        n = 100
        barrier = threading.Barrier(n)
        results = []
        results_lock = threading.Lock()

        def worker():
            barrier.wait()
            status, _, payload = self._call(
                "POST", "/api/billing/verify",
                {"txid": txid, "asset": "btc", "plan": "pro", "billing": "monthly"})
            with results_lock:
                results.append((status.split()[0], payload))

        # Patch once, outside the threads: all 100 requests share one mock.
        with mock.patch.object(billing, "_RATE_MAX", 10_000), \
             mock.patch.object(core, "_fetch_json", return_value=_btc_tx(55_000)):
            threads = [threading.Thread(target=worker) for _ in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(len(results), n)
        for code, payload in results:
            self.assertIn(code, ("200", "201"), payload)
            self.assertIsNotNone(payload.get("license"), payload)

        # Exactly ONE distinct license across all 100 callers.
        licenses = {p.get("license") for _, p in results}
        self.assertEqual(len(licenses), 1, f"expected exactly one license, got {len(licenses)}")

        # The audit ledger holds exactly one row for this txid.
        conn = sqlite3.connect(self.ledger_path)
        try:
            rows = conn.execute("SELECT COUNT(*) FROM ledger WHERE txid=?", (txid,)).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(rows, 1)

    def test_replay_after_restart_and_storage_loss_never_mints_second_license(self):
        txid = "cd" * 32
        tx = _btc_tx(55_000)
        body = {"txid": txid, "asset": "btc", "plan": "pro", "billing": "monthly"}

        # Claim #1 — instance A.
        with mock.patch.object(core, "_fetch_json", return_value=tx):
            s1, _, p1 = self._call("POST", "/api/billing/verify", body)
        self.assertEqual(s1.split()[0], "201")
        token1 = p1["license"]

        # Instance restart: brand-new store objects over the same db file.
        billing.LEDGER = core.SqliteLedger(self.ledger_path)
        billing.USAGE = core.SqliteUsageStore(self.usage_path)
        with mock.patch.object(core, "_fetch_json", return_value=tx):
            s2, _, p2 = self._call("POST", "/api/billing/verify", body)
        self.assertEqual(p2["license"], token1)
        self.assertTrue(p2.get("idempotent"))

        # Full storage loss (ephemeral-FS simulation): ledger gone, but the
        # deterministic license re-derives byte-identically — no second mint.
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.ledger_path + suffix)
            except FileNotFoundError:
                pass
        billing.LEDGER = core.SqliteLedger(self.ledger_path)
        billing.USAGE = core.SqliteUsageStore(self.usage_path)
        with mock.patch.object(core, "_fetch_json", return_value=tx):
            s3, _, p3 = self._call("POST", "/api/billing/verify", body)
        self.assertEqual(p3["license"], token1)

        # Across all three claims: exactly one distinct license ever minted.
        self.assertEqual(len({token1, p2["license"], p3["license"]}), 1)

    def test_same_txid_different_plan_rejected(self):
        txid = "ef" * 32
        body_team = {"txid": txid, "asset": "btc", "plan": "team", "billing": "monthly"}
        body_pro = {"txid": txid, "asset": "btc", "plan": "pro", "billing": "monthly"}
        with mock.patch.object(core, "_fetch_json", return_value=_btc_tx(220_000)):
            s1, _, p1 = self._call("POST", "/api/billing/verify", body_team)
            self.assertEqual(s1.split()[0], "201")
            s2, _, p2 = self._call("POST", "/api/billing/verify", body_pro)
        self.assertEqual(s2.split()[0], "409")
        self.assertIn("already used", p2["message"])

    def test_amount_binding_rejects_cross_plan_claim_without_ledger(self):
        # Even with no ledger row (fresh instance / lost storage), a team-price
        # payment claimed as pro fails the exact-amount check on-chain.
        with mock.patch.object(core, "_fetch_json", return_value=_btc_tx(220_000)):
            s, _, p = self._call("POST", "/api/billing/verify",
                                 {"txid": "11" * 32, "asset": "btc", "plan": "pro", "billing": "monthly"})
        self.assertEqual(s.split()[0], "400")
        self.assertIn("Amount mismatch", p["message"])


class UsageStoreCoreTests(unittest.TestCase):
    """Direct tests of the atomic consume_if_available primitive."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ax-usage-core-")
        self.store = core.SqliteUsageStore(os.path.join(self.dir, "usage.db"), epoch="e1")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_success_consumes_and_reports_used(self):
        r = self.store.consume_if_available("lic-1", 1234, "agent.call", "rid-0001", 500_000)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["consumed"], 1234)
        self.assertEqual(r["used"], 1234)
        self.assertFalse(r["idempotent"])
        self.assertEqual(self.store.used("lic-1"), 1234)

    def test_exact_allowance_boundary(self):
        r = self.store.consume_if_available("lic-1", 1000, "agent.call", "rid-0001", 1000)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["used"], 1000)
        r = self.store.consume_if_available("lic-1", 1, "agent.call", "rid-0002", 1000)
        self.assertEqual(r["status"], "denied")
        self.assertEqual(r["used"], 1000)  # nothing consumed
        self.assertEqual(self.store.used("lic-1"), 1000)

    def test_denied_does_not_increment(self):
        self.store.consume_if_available("lic-1", 500, "agent.call", "rid-0001", 600)
        r = self.store.consume_if_available("lic-1", 200, "agent.call", "rid-0002", 600)
        self.assertEqual(r["status"], "denied")
        self.assertEqual(self.store.used("lic-1"), 500)

    def test_unlimited_allowance_never_denies(self):
        for i in range(3):
            r = self.store.consume_if_available("lic-life", 1_000_000, "agent.call", f"rid-{i:04d}", None)
            self.assertEqual(r["status"], "ok")

    def test_same_request_id_replays_without_double_consume(self):
        r1 = self.store.consume_if_available("lic-1", 500, "agent.call", "rid-0001", 10_000)
        r2 = self.store.consume_if_available("lic-1", 500, "agent.call", "rid-0001", 10_000)
        self.assertEqual(r1["status"], "ok")
        self.assertEqual(r2["status"], "replay")
        self.assertTrue(r2["idempotent"])
        self.assertEqual(r2["used"], 500)  # NOT 1000
        self.assertEqual(self.store.used("lic-1"), 500)

    def test_request_id_reuse_with_different_units_conflicts(self):
        self.store.consume_if_available("lic-1", 500, "agent.call", "rid-0001", 10_000)
        r = self.store.consume_if_available("lic-1", 600, "agent.call", "rid-0001", 10_000)
        self.assertEqual(r["status"], "conflict")

    def test_request_id_reuse_with_different_operation_conflicts(self):
        self.store.consume_if_available("lic-1", 500, "agent.call", "rid-0001", 10_000)
        r = self.store.consume_if_available("lic-1", 500, "memory.query", "rid-0001", 10_000)
        self.assertEqual(r["status"], "conflict")

    def test_same_request_id_under_different_license_conflicts(self):
        # request_id is a GLOBAL idempotency key: reusing it with a different
        # license is a conflict, never a silent mutation.
        r1 = self.store.consume_if_available("lic-A", 500, "agent.call", "rid-0001", 10_000)
        r2 = self.store.consume_if_available("lic-B", 500, "agent.call", "rid-0001", 10_000)
        self.assertEqual(r1["status"], "ok")
        self.assertEqual(r2["status"], "conflict")
        self.assertEqual(self.store.used("lic-A"), 500)
        self.assertEqual(self.store.used("lic-B"), 0)

    def test_legacy_increment_and_used_compat(self):
        self.store.increment("lic-1", 250)
        self.assertEqual(self.store.used("lic-1"), 250)
        r = self.store.consume_if_available("lic-1", 750, "agent.call", "rid-0001", 1_000)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["used"], 1_000)
        self.assertEqual(self.store.used("lic-1"), 1_000)

    def test_epoch_mismatch_fails_closed(self):
        path = os.path.join(tempfile.mkdtemp(prefix="ax-epoch2-"), "usage.db")
        core.SqliteUsageStore(path, epoch="epoch-a")
        with self.assertRaises(core.UsageIntegrityError):
            core.SqliteUsageStore(path, epoch="epoch-b")

    def test_usage_survives_restart_with_same_epoch(self):
        self.store.consume_if_available("lic-1", 100, "agent.call", "rid-0001", 10_000)
        store2 = core.SqliteUsageStore(self.store.path, epoch="e1")
        self.assertEqual(store2.used("lic-1"), 100)
        r = store2.consume_if_available("lic-1", 100, "agent.call", "rid-0002", 10_000)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["used"], 200)


class ConsumeTests(_WSGIBase):
    """WSGI tests for POST /api/billing/consume — server-authoritative."""

    def setUp(self):
        self._old_secret = billing.SECRET
        billing.SECRET = self.secret
        self.dir = tempfile.mkdtemp(prefix="ax-consume-")
        self.ledger_path = os.path.join(self.dir, "ledger.db")
        self.usage_path = os.path.join(self.dir, "usage.db")
        billing.LEDGER = core.SqliteLedger(self.ledger_path)
        billing.USAGE = core.SqliteUsageStore(self.usage_path, epoch="e1")
        billing._events.clear()
        self.tok = core.issue_license(self.secret, "pro", "monthly", "ab" * 32, "btc",
                                      "0.00055", True, _NOW)

    def tearDown(self):
        billing.SECRET = self._old_secret
        billing._events.clear()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _consume(self, body):
        return self._call("POST", "/api/billing/consume", body)

    def test_success_signed_license(self):
        status, _, p = self._consume({"license": self.tok, "units": 1234,
                                      "operation": "context.compile", "request_id": "req-00000001"})
        self.assertEqual(status.split()[0], "200", p)
        self.assertTrue(p["allowed"])
        self.assertEqual(p["consumed"], 1234)
        self.assertEqual(p["used"], 1234)
        self.assertEqual(p["allowance"], 500_000)
        self.assertEqual(p["remaining"], 500_000 - 1234)
        self.assertEqual(p["plan"], "pro")
        self.assertEqual(p["expires"], _NOW + 30 * 86400)
        self.assertFalse(p["idempotent"])

    def test_forged_license_rejected(self):
        fake = core._sign("wrong-secret", {"v": 1, "plan": "pro", "billing": "monthly",
                                           "allowance": 500_000, "issued_at": 1, "expires_at": 9999999999,
                                           "txid": "x", "asset": "btc", "paid": "1", "confirmed": True})
        status, _, p = self._consume({"license": fake, "units": 100,
                                      "operation": "agent.call", "request_id": "req-00000002"})
        self.assertEqual(status.split()[0], "200")
        self.assertFalse(p["allowed"])
        self.assertEqual(p["reason"], "invalid_license")
        self.assertEqual(billing.USAGE.used(fake), 0)

    def test_tampered_license_rejected(self):
        body, sig = self.tok.split(".")
        tampered = body + "." + sig[:-2] + ("A" if sig[-2] != "A" else "B")
        status, _, p = self._consume({"license": tampered, "units": 100,
                                      "operation": "agent.call", "request_id": "req-00000003"})
        self.assertEqual(p["reason"], "invalid_license")

    def test_wrong_secret_license_rejected(self):
        other = core.issue_license("other-secret", "pro", "monthly", "ab" * 32, "btc",
                                   "0.00055", True, _NOW)
        status, _, p = self._consume({"license": other, "units": 100,
                                      "operation": "agent.call", "request_id": "req-00000004"})
        self.assertEqual(p["reason"], "invalid_license")

    def test_expired_license_rejected(self):
        payload = {"v": 1, "plan": "pro", "billing": "monthly", "allowance": 500_000,
                   "issued_at": 1, "expires_at": 2, "txid": "x", "asset": "btc",
                   "paid": "0.00055", "confirmed": True}
        token = core._sign(self.secret, payload)
        with mock.patch.object(core, "_now", return_value=3):
            status, _, p = self._consume({"license": token, "units": 100,
                                          "operation": "agent.call", "request_id": "req-00000005"})
        self.assertEqual(p["reason"], "expired_license")

    def test_invalid_units_rejected(self):
        for bad in (0, -5, 1.5, True, "lots", 1_000_001):
            status, _, p = self._consume({"license": self.tok, "units": bad,
                                          "operation": "agent.call", "request_id": "req-00000006"})
            self.assertEqual(status.split()[0], "400", f"units={bad!r}")
            self.assertEqual(p["reason"], "invalid_units")
        self.assertEqual(billing.USAGE.used(self.tok), 0)

    def test_invalid_operation_rejected(self):
        status, _, p = self._consume({"license": self.tok, "units": 100,
                                      "operation": "hack.anything", "request_id": "req-00000007"})
        self.assertEqual(status.split()[0], "400")
        self.assertEqual(p["reason"], "invalid_operation")

    def test_missing_or_bad_request_id_rejected(self):
        status, _, p = self._consume({"license": self.tok, "units": 100, "operation": "agent.call"})
        self.assertEqual(status.split()[0], "400")
        self.assertEqual(p["reason"], "invalid_request_id")
        status, _, p = self._consume({"license": self.tok, "units": 100,
                                      "operation": "agent.call", "request_id": "short"})
        self.assertEqual(status.split()[0], "400")
        self.assertEqual(p["reason"], "invalid_request_id")

    def test_allowance_exceeded(self):
        self._consume({"license": self.tok, "units": 500_000,
                       "operation": "agent.call", "request_id": "req-00000008"})
        status, _, p = self._consume({"license": self.tok, "units": 1,
                                      "operation": "agent.call", "request_id": "req-00000009"})
        self.assertEqual(status.split()[0], "200")
        self.assertFalse(p["allowed"])
        self.assertEqual(p["reason"], "allowance_exceeded")
        self.assertEqual(p["used"], 500_000)
        self.assertEqual(p["remaining"], 0)
        self.assertEqual(billing.USAGE.used(self.tok), 500_000)

    def test_client_supplied_allowance_is_ignored(self):
        # The browser cannot raise its own allowance: the server uses the
        # signed payload's allowance (500k), never the body field.
        status, _, p = self._consume({"license": self.tok, "units": 500_000,
                                      "operation": "agent.call", "request_id": "req-00000010",
                                      "allowance": 999_999_999})
        self.assertTrue(p["allowed"])
        status, _, p = self._consume({"license": self.tok, "units": 1,
                                      "operation": "agent.call", "request_id": "req-00000011",
                                      "allowance": 999_999_999})
        self.assertFalse(p["allowed"])
        self.assertEqual(p["reason"], "allowance_exceeded")

    def test_request_id_replay_idempotent(self):
        body = {"license": self.tok, "units": 5000, "operation": "agent.call",
                "request_id": "req-00000012"}
        _, _, p1 = self._consume(body)
        status, _, p2 = self._consume(body)
        self.assertEqual(status.split()[0], "200")
        self.assertTrue(p2["idempotent"])
        self.assertEqual(p1["used"], p2["used"])
        self.assertEqual(p2["used"], 5000)  # not 10000
        self.assertEqual(billing.USAGE.used(self.tok), 5000)

    def test_request_id_conflict_different_units(self):
        self._consume({"license": self.tok, "units": 5000, "operation": "agent.call",
                       "request_id": "req-00000013"})
        status, _, p = self._consume({"license": self.tok, "units": 6000, "operation": "agent.call",
                                      "request_id": "req-00000013"})
        self.assertEqual(status.split()[0], "409")
        self.assertEqual(p["reason"], "request_id_conflict")

    def test_request_id_conflict_different_operation(self):
        self._consume({"license": self.tok, "units": 5000, "operation": "agent.call",
                       "request_id": "req-00000014"})
        status, _, p = self._consume({"license": self.tok, "units": 5000, "operation": "memory.query",
                                      "request_id": "req-00000014"})
        self.assertEqual(status.split()[0], "409")
        self.assertEqual(p["reason"], "request_id_conflict")

    def test_request_id_conflict_different_license(self):
        tok2 = core.issue_license(self.secret, "team", "monthly", "cd" * 32, "btc",
                                  "0.00220", True, _NOW)
        self._consume({"license": self.tok, "units": 5000, "operation": "agent.call",
                       "request_id": "req-00000015"})
        status, _, p = self._consume({"license": tok2, "units": 5000, "operation": "agent.call",
                                      "request_id": "req-00000015"})
        self.assertEqual(status.split()[0], "409")
        self.assertEqual(p["reason"], "request_id_conflict")

    def test_free_plan_same_enforcement_path(self):
        body = {"plan": "free", "subject": "client-abc-1234", "units": 100,
                "operation": "agent.call", "request_id": "req-00000016"}
        status, _, p = self._consume(body)
        self.assertEqual(status.split()[0], "200")
        self.assertTrue(p["allowed"])
        self.assertEqual(p["allowance"], 10_000)
        self.assertEqual(p["plan"], "free")
        # Exhaust the free allowance — the server decides, the client cannot.
        self._consume({"plan": "free", "subject": "client-abc-1234", "units": 9_900,
                       "operation": "agent.call", "request_id": "req-00000017"})
        status, _, p = self._consume({"plan": "free", "subject": "client-abc-1234", "units": 100,
                                      "operation": "agent.call", "request_id": "req-00000018"})
        self.assertFalse(p["allowed"])
        self.assertEqual(p["reason"], "allowance_exceeded")
        self.assertEqual(billing.USAGE.used("free:client-abc-1234"), 10_000)

    def test_claiming_paid_plan_without_license_is_invalid(self):
        status, _, p = self._consume({"plan": "pro", "subject": "client-abc-1234", "units": 100,
                                      "operation": "agent.call", "request_id": "req-00000019"})
        self.assertEqual(p["reason"], "invalid_license")
        self.assertEqual(billing.USAGE.used("free:client-abc-1234"), 0)

    def test_usage_store_unavailable_fails_closed(self):
        billing.USAGE = None
        status, _, p = self._consume({"license": self.tok, "units": 100,
                                      "operation": "agent.call", "request_id": "req-00000020"})
        self.assertEqual(status.split()[0], "503")
        self.assertEqual(p["reason"], "billing_unavailable")

    def test_status_unavailable_fails_closed(self):
        billing.USAGE = None
        status, _, p = self._call("GET", "/api/billing/status", qs="license=" + self.tok)
        self.assertEqual(status.split()[0], "503")
        self.assertEqual(p["reason"], "billing_unavailable")

    def test_secret_never_in_consume_responses(self):
        status, _, p = self._consume({"license": self.tok, "units": 1,
                                      "operation": "agent.call", "request_id": "req-00000021"})
        self.assertNotIn(self.secret, json.dumps(p))

    def test_operation_registry_known_operations(self):
        for op in ("agent.call", "context.compile", "memory.query"):
            status, _, p = self._consume({"license": self.tok, "units": 1,
                                          "operation": op, "request_id": "req-op-" + op.replace(".", "-")})
            self.assertEqual(status.split()[0], "200", op)


class ConsumeConcurrencyTests(_WSGIBase):
    """Deterministic concurrency regression: usage can never exceed allowance,
    and each request_id consumes exactly once."""

    def setUp(self):
        self._old_secret = billing.SECRET
        billing.SECRET = self.secret
        self.dir = tempfile.mkdtemp(prefix="ax-consume-conc-")
        self.usage_path = os.path.join(self.dir, "usage.db")
        billing.LEDGER = core.SqliteLedger(os.path.join(self.dir, "ledger.db"))
        billing.USAGE = core.SqliteUsageStore(self.usage_path, epoch="e1")
        billing._events.clear()
        self.tok = core.issue_license(self.secret, "pro", "monthly", "ab" * 32, "btc",
                                      "0.00055", True, _NOW)

    def tearDown(self):
        billing.SECRET = self._old_secret
        billing._events.clear()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_100_concurrent_consumers_cannot_overspend(self):
        allowance, per = 500_000, 10_000
        n = 100
        barrier = threading.Barrier(n)
        results, lock = [], threading.Lock()

        def worker(i):
            barrier.wait()
            status, _, p = self._call(
                "POST", "/api/billing/consume",
                {"license": self.tok, "units": per, "operation": "agent.call",
                 "request_id": f"conc-{i:04d}"})
            with lock:
                results.append((status.split()[0], p))

        with mock.patch.object(billing, "_RATE_MAX", 10_000):
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        allowed = [p for _, p in results if p.get("allowed")]
        denied = [p for _, p in results if p.get("allowed") is False]
        # Exactly 50 successes (500_000 / 10_000), 50 denials — no races.
        self.assertEqual(len(allowed), 50, f"allowed={len(allowed)} denied={len(denied)}")
        self.assertEqual(len(denied), 50)
        for p in denied:
            self.assertEqual(p["reason"], "allowance_exceeded")
        self.assertEqual(billing.USAGE.used(self.tok), allowance)
        # Every success consumed exactly its units once.
        self.assertEqual(sum(p["consumed"] for p in allowed), allowance)

    def test_100_concurrent_unique_request_ids_each_consumed_once(self):
        allowance, per = 10_000, 100
        n = 100
        barrier = threading.Barrier(n)
        results, lock = [], threading.Lock()

        def worker(i):
            barrier.wait()
            status, _, p = self._call(
                "POST", "/api/billing/consume",
                {"license": self.tok, "units": per, "operation": "agent.call",
                 "request_id": f"uniq-{i:04d}"})
            with lock:
                results.append(p)

        with mock.patch.object(billing, "_RATE_MAX", 10_000):
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(len([p for p in results if p.get("allowed")]), n)
        self.assertEqual(billing.USAGE.used(self.tok), allowance)

    def test_101st_request_at_boundary_is_denied(self):
        # A license whose payload allowance is exactly 1000 (server-derived).
        payload = {"v": 1, "plan": "pro", "billing": "monthly", "allowance": 1000,
                   "issued_at": _NOW, "expires_at": _NOW + 86400, "txid": "x",
                   "asset": "btc", "paid": "0.00055", "confirmed": True}
        tok = core._sign(self.secret, payload)
        allowance, per = 1000, 10
        with mock.patch.object(billing, "_RATE_MAX", 10_000):
            for i in range(100):
                status, _, p = self._call(
                    "POST", "/api/billing/consume",
                    {"license": tok, "units": per, "operation": "agent.call",
                     "request_id": f"bnd-{i:04d}"})
                self.assertTrue(p["allowed"], f"#{i}: {p}")
            self.assertEqual(billing.USAGE.used(tok), allowance)
            status, _, p = self._call(
                "POST", "/api/billing/consume",
                {"license": tok, "units": per, "operation": "agent.call",
                 "request_id": "bnd-9999"})
        self.assertFalse(p["allowed"])
        self.assertEqual(p["reason"], "allowance_exceeded")
        self.assertEqual(billing.USAGE.used(tok), allowance)  # no overspend

    def test_same_request_id_100_way_race_consumes_once(self):
        n = 100
        barrier = threading.Barrier(n)
        results, lock = [], threading.Lock()

        def worker():
            barrier.wait()
            status, _, p = self._call(
                "POST", "/api/billing/consume",
                {"license": self.tok, "units": 500, "operation": "agent.call",
                 "request_id": "race-rid-0001"})
            with lock:
                results.append(p)

        with mock.patch.object(billing, "_RATE_MAX", 10_000):
            threads = [threading.Thread(target=worker) for _ in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(len(results), n)
        for p in results:
            self.assertTrue(p.get("allowed"), p)
            self.assertEqual(p["used"], 500)
        self.assertEqual(billing.USAGE.used(self.tok), 500)  # exactly once


class UsageStorageSafetyTests(unittest.TestCase):
    """Storage-loss behavior: usage must fail closed, never reset to zero."""

    def test_build_stores_fails_closed_when_usage_store_unavailable(self):
        with mock.patch.object(billing, "SqliteUsageStore", side_effect=core.UsageIntegrityError("wiped")):
            ledger, usage = billing._build_stores()
        self.assertIsNotNone(ledger)   # ledger is audit-only; degradation is safe
        self.assertIsNone(usage)       # usage is authoritative; NEVER in-memory fallback

    def test_build_stores_usage_never_degrades_to_memory(self):
        with mock.patch.object(billing, "SqliteUsageStore", side_effect=OSError("read-only fs")):
            _, usage = billing._build_stores()
        self.assertIsNone(usage)

    def test_epoch_mismatch_detects_replaced_database(self):
        path = os.path.join(tempfile.mkdtemp(prefix="ax-epoch-"), "usage.db")
        core.SqliteUsageStore(path, epoch="deploy-1").consume_if_available(
            "lic", 100, "agent.call", "rid-0001", 10_000)
        with self.assertRaises(core.UsageIntegrityError):
            core.SqliteUsageStore(path, epoch="deploy-2")

    def test_ledger_loss_does_not_reset_usage(self):
        d = tempfile.mkdtemp(prefix="ax-split-")
        ledger = core.SqliteLedger(os.path.join(d, "ledger.db"))
        usage = core.SqliteUsageStore(os.path.join(d, "usage.db"), epoch="e1")
        ledger.record("tx", "pro", "monthly", "btc", "lic-1", 1)
        usage.consume_if_available("lic-1", 250, "agent.call", "rid-0001", 10_000)
        # Wipe the ledger only — usage must be untouched.
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(os.path.join(d, "ledger.db") + suffix)
            except FileNotFoundError:
                pass
        usage2 = core.SqliteUsageStore(os.path.join(d, "usage.db"), epoch="e1")
        self.assertEqual(usage2.used("lic-1"), 250)


class SecurityReviewTests(_WSGIBase):
    """Evidence-first security review (§21): each client-side trust boundary is
    exercised through the real WSGI app. A failing invariant here is a
    production blocker."""

    def setUp(self):
        self._old_secret = billing.SECRET
        billing.SECRET = self.secret
        self.dir = tempfile.mkdtemp(prefix="ax-secrev-")
        billing.LEDGER = core.SqliteLedger(os.path.join(self.dir, "ledger.db"))
        billing.USAGE = core.SqliteUsageStore(os.path.join(self.dir, "usage.db"), epoch="e1")
        billing._events.clear()
        self.tok = core.issue_license(self.secret, "pro", "monthly", "ab" * 32, "btc",
                                      "0.00055", True, _NOW)

    def tearDown(self):
        billing.SECRET = self._old_secret
        billing._events.clear()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _consume(self, body):
        return self._call("POST", "/api/billing/consume", body)

    def test_browser_cannot_increase_allowance(self):
        # Client claims a 10^12 allowance; the server uses the signed 500k.
        s, _, p = self._consume({"license": self.tok, "units": 500_000,
                                 "operation": "agent.call", "request_id": "sec-0001", "allowance": 10**12})
        self.assertTrue(p["allowed"])
        s, _, p = self._consume({"license": self.tok, "units": 1,
                                 "operation": "agent.call", "request_id": "sec-0002", "allowance": 10**12})
        self.assertFalse(p["allowed"])
        self.assertEqual(p["reason"], "allowance_exceeded")

    def test_browser_cannot_decrease_recorded_usage(self):
        for bad in (-100, 0):
            s, _, p = self._consume({"license": self.tok, "units": bad,
                                     "operation": "agent.call", "request_id": "sec-0003"})
            self.assertEqual(s.split()[0], "400")
            self.assertEqual(p["reason"], "invalid_units")
        self.assertEqual(billing.USAGE.used(self.tok), 0)  # nothing mutated

    def test_browser_cannot_change_expiry(self):
        forged = {"v": 1, "plan": "pro", "billing": "monthly", "allowance": 500_000,
                  "issued_at": 1, "expires_at": 10**12, "txid": "x", "asset": "btc",
                  "paid": "0.00055", "confirmed": True}
        s, _, p = self._consume({"license": core._sign("wrong-key", forged), "units": 10,
                                 "operation": "agent.call", "request_id": "sec-0004"})
        self.assertFalse(p["allowed"])
        self.assertEqual(p["reason"], "invalid_license")

    def test_browser_cannot_change_plan(self):
        s, _, p = self._consume({"plan": "life", "subject": "client-sec-0001", "units": 10,
                                 "operation": "agent.call", "request_id": "sec-0005"})
        self.assertFalse(p["allowed"])
        self.assertEqual(p["reason"], "invalid_license")

    def test_no_local_authorization_bypass(self):
        s, _, p = self._consume({"license": "garbage-token", "units": 10,
                                 "operation": "agent.call", "request_id": "sec-0006"})
        self.assertFalse(p["allowed"])
        self.assertEqual(p["reason"], "invalid_license")
        self.assertEqual(billing.USAGE.used("garbage-token"), 0)

    def test_expired_license_cannot_consume(self):
        exp = {"v": 1, "plan": "pro", "billing": "monthly", "allowance": 500_000,
               "issued_at": 1, "expires_at": 2, "txid": "x", "asset": "btc",
               "paid": "0.00055", "confirmed": True}
        token = core._sign(self.secret, exp)
        with mock.patch.object(core, "_now", return_value=3):
            s, _, p = self._consume({"license": token, "units": 10,
                                     "operation": "agent.call", "request_id": "sec-0007"})
        self.assertFalse(p["allowed"])
        self.assertEqual(p["reason"], "expired_license")

    def test_forged_license_cannot_consume(self):
        forged = {"v": 1, "plan": "pro", "billing": "monthly", "allowance": 500_000,
                  "issued_at": 1, "expires_at": 10**12, "txid": "x", "asset": "btc",
                  "paid": "0.00055", "confirmed": True}
        s, _, p = self._consume({"license": core._sign("attacker-secret", forged), "units": 10,
                                 "operation": "agent.call", "request_id": "sec-0008"})
        self.assertFalse(p["allowed"])
        self.assertEqual(p["reason"], "invalid_license")

    def test_secret_never_in_any_response(self):
        s, _, p = self._consume({"license": self.tok, "units": 1,
                                 "operation": "agent.call", "request_id": "sec-0009"})
        self.assertNotIn(self.secret, json.dumps(p))
        s, _, p = self._call("GET", "/api/billing/status", qs="license=" + self.tok)
        self.assertNotIn(self.secret, json.dumps(p))

    def test_oversize_units_rejected_before_mutation(self):
        s, _, p = self._consume({"license": self.tok, "units": 10**7,
                                 "operation": "agent.call", "request_id": "sec-0010"})
        self.assertEqual(s.split()[0], "400")
        self.assertEqual(p["reason"], "invalid_units")
        self.assertEqual(billing.USAGE.used(self.tok), 0)

if __name__ == "__main__":
    unittest.main()
