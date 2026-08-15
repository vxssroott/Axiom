"""
Axiom billing core — Python stdlib only.

Crypto-native subscription billing for Axiom, production-grade integrity:

1. STATELESS, DETERMINISTIC LICENSES (primary anti-replay mechanism).
   A license token is a pure function of (secret, plan, billing, txid, asset,
   paid, on-chain anchor timestamp, confirmed). The same payment always
   produces the IDENTICAL token — on any instance, after any restart or
   redeploy, under any concurrency. There is no authoritative server state to
   lose, so replay, double-mint, cold-start and ledger-loss attacks are
   structurally impossible.

2. CHAIN-ENFORCED PLAN BINDING. Every plan/period has an exact price (all
   prices pairwise distinct — regression-tested). The backend accepts a
   payment ONLY if the received amount equals that price (1-sat / 1e-6-token
   tolerance). One transaction therefore maps to exactly one plan/period, so
   the same txid cannot be replayed against a different plan, and the same
   txid always yields the same (single) license.

3. CONFIRMATION REQUIREMENT. BTC licenses are issued only once the tx reaches
   MIN_BTC_CONFIRMATIONS (default 1). Earlier states are reported as
   "pending" (payment detected — awaiting confirmation) and issue no license.

4. SQLite (stdlib sqlite3) provides a crash-consistent, atomic AUDIT ledger
   (UNIQUE(txid) + INSERT OR IGNORE) and usage metering behind the
   LedgerStore / UsageStore interfaces. The audit ledger is defense-in-depth
   (not authoritative — determinism is), so its durability never gates
   security; losing it can never mint a second license.

5. HMAC-SHA256 signatures; the signing secret lives only in the server
   environment and is never exposed to clients.

Nothing here touches user funds or introduces custody: it only reads public
blockchain data and issues signed access licenses.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

# ---------------------------------------------------------------------------
# Wallet addresses — MUST stay byte-identical to public/axiom.html's `PAY`.
# A regression test (tests/billing_test.py) asserts this.
# ---------------------------------------------------------------------------
PAY = {
    "btc": "bc1q0jrmywdtx45q30ek9zh9hysqwvtvau8gsl3kwu",
    "usdt": "0x6355a8F4a5066A3aA6658F1b8a83cc83b0963FA9",
    "usdtContract": "0xdac17f958d2ee523a2206206994597c13d831ec7",  # canonical Ethereum-mainnet USDT
}

# ---------------------------------------------------------------------------
# Plans — server-authoritative. The UI mirrors this for display; the server
# enforces exact prices and allowances when issuing licenses.
# All prices are pairwise distinct per asset (regression-tested) so that one
# payment can be claimed for exactly one plan/period.
# ---------------------------------------------------------------------------
PLANS = {
    "free": {
        "name": "Community", "rank": 0, "unlimited": False,
        "allowance": 10_000,
        "blurb": "Understand any codebase locally. Nothing sent anywhere.",
    },
    "pro": {
        "name": "Pro", "rank": 1, "unlimited": False,
        "price": {
            "monthly": {"usd": 49, "btc": "0.00055", "usdt": 49},
            "annual": {"usd": 490, "btc": "0.00550", "usdt": 490},
        },
        "allowance": {"monthly": 500_000, "annual": 5_000_000},
        "blurb": "For engineers shipping changes daily.",
    },
    "team": {
        "name": "Team", "rank": 2, "unlimited": False,
        "price": {
            "monthly": {"usd": 199, "btc": "0.00220", "usdt": 199},
            "annual": {"usd": 1990, "btc": "0.02200", "usdt": 1990},
        },
        "allowance": {"monthly": 2_000_000, "annual": 20_000_000},
        "blurb": "For teams that need institutional memory.",
    },
    "life": {
        "name": "Lifetime", "rank": 3, "unlimited": True,
        "allowance": None,
        "price": {
            "once": {"usd": 990, "btc": "0.01100", "usdt": 990},
        },
        "blurb": "One payment. Every capability, forever.",
    },
}

PERIOD_DAYS = {"monthly": 30, "annual": 365}
ASSETS = ("btc", "usdt")
ALLOWED_BILLING = ("monthly", "annual", "once")
EXPLORER_TIMEOUT = 15
ETHPLORER_API_KEY = os.environ.get("ETHPLORER_API_KEY", "freekey")
MIN_BTC_CONFIRMATIONS = max(1, int(os.environ.get("MIN_BTC_CONFIRMATIONS", "1")))
USDT_CHAIN = "ethereum-mainnet"  # Ethplorer is mainnet-scoped; contract pin below enforces it

# Exact-amount tolerance: BTC amounts are integer sats; USDT amounts use 6 decimals.
BTC_TOLERANCE = Decimal("1e-8")   # 1 sat
USDT_TOLERANCE = Decimal("1e-6")  # 0.000001 token

# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _now() -> int:
    return int(time.time())


def _normalize_txid(txid: str) -> str:
    """BTC and Ethereum txids are 64-char hex; normalize case for ledger keys."""
    return txid.strip().lower()


# ---------------------------------------------------------------------------
# On-chain verification (server-side)
# ---------------------------------------------------------------------------


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "axiom-billing/1.0"})
    with urllib.request.urlopen(req, timeout=EXPLORER_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_int(url: str) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": "axiom-billing/1.0"})
    with urllib.request.urlopen(req, timeout=EXPLORER_TIMEOUT) as resp:
        return int(resp.read().decode("utf-8").strip())


def _exact_price(plan_id: str, billing: str, asset: str) -> str:
    price = PLANS[plan_id]["price"][billing]
    return price["btc"] if asset == "btc" else str(price["usdt"])


def verify_btc(txid: str, exact_btc: str, min_confirmations: int = MIN_BTC_CONFIRMATIONS) -> dict:
    """Verify a BTC payment to PAY['btc'].

    Accepts ONLY the exact plan amount (chain-enforced plan binding).
    Returns state: "active" (license may be issued) or "pending" (payment
    detected, confirmations not yet met — no license).
    """
    url = "https://blockstream.info/api/tx/" + urllib.parse.quote(txid.strip())
    try:
        tx = _fetch_json(url)
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as e:
        raise VerificationError("Transaction not found on the Bitcoin network.") from e

    sats = sum(
        o.get("value", 0)
        for o in tx.get("vout", [])
        if o.get("scriptpubkey_address") == PAY["btc"]
    )
    paid = Decimal(sats) / Decimal(10**8)
    if paid <= 0:
        raise VerificationError("This transaction does not pay the Axiom BTC address.")
    if abs(paid - Decimal(exact_btc)) > BTC_TOLERANCE:
        raise VerificationError(
            f"Amount mismatch: expected exactly {Decimal(exact_btc)} BTC, received {paid} BTC. "
            "Over- and under-payments cannot be claimed — send the exact plan amount."
        )

    confirmed = bool(tx.get("status", {}).get("confirmed"))
    if not confirmed:
        return {
            "state": "pending", "confirmed": False, "confirmations": 0,
            "paid": str(paid), "note": "Payment detected on the Bitcoin network — awaiting confirmation.",
        }

    block_time = tx.get("status", {}).get("block_time")
    if not block_time:
        raise VerificationError("Confirmed transaction is missing its block time.")
    height = tx.get("status", {}).get("block_height")

    if min_confirmations > 1:
        tip = _fetch_int("https://blockstream.info/api/blocks/tip/height")
        confirmations = max(1, tip - int(height) + 1) if height else 1
        if confirmations < min_confirmations:
            return {
                "state": "pending", "confirmed": True, "confirmations": confirmations,
                "paid": str(paid),
                "note": f"Confirmed with {confirmations}/{min_confirmations} confirmations — awaiting more.",
            }
    else:
        confirmations = 1

    return {
        "state": "active", "confirmed": True, "confirmations": confirmations,
        "paid": str(paid), "anchor": int(block_time),
        "note": f"Confirmed in block {height}",
    }


def verify_usdt(txid: str, exact_usdt: str) -> dict:
    """Verify an ERC-20 USDT payment to PAY['usdt'] on Ethereum mainnet.

    Pins the canonical USDT contract, the Axiom recipient, the token, and the
    exact amount. Returns state "active" or "pending".
    """
    url = (
        "https://api.ethplorer.io/getTxInfo/"
        + urllib.parse.quote(txid.strip())
        + "?apiKey="
        + urllib.parse.quote(ETHPLORER_API_KEY)
    )
    try:
        tx = _fetch_json(url)
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as e:
        raise VerificationError("Transaction not found on Ethereum.") from e
    if tx.get("error"):
        raise VerificationError(tx["error"].get("message") or "Transaction not found on Ethereum.")

    ops = [
        o
        for o in tx.get("operations", [])
        if o.get("type") == "transfer"
        and (o.get("contract") or (o.get("tokenInfo") or {}).get("address") or "").lower()
        == PAY["usdtContract"].lower()
        and (o.get("to") or "").lower() == PAY["usdt"].lower()
    ]
    if not ops:
        raise VerificationError("No USDT transfer to the Axiom address in this transaction.")

    dec = int((ops[0].get("tokenInfo") or {}).get("decimals") or 6)
    paid = sum(Decimal(str(o.get("value", 0))) / Decimal(10**dec) for o in ops)
    if abs(paid - Decimal(exact_usdt)) > USDT_TOLERANCE:
        raise VerificationError(
            f"Amount mismatch: expected exactly {Decimal(exact_usdt)} USDT, received {paid:.2f} USDT. "
            "Send the exact plan amount."
        )

    confirmations = int(tx.get("confirmations") or 0)
    if not tx.get("success"):
        return {
            "state": "pending", "confirmed": False, "confirmations": confirmations,
            "paid": str(paid), "note": "Payment detected on Ethereum — awaiting confirmation.",
        }
    ts = tx.get("timestamp")
    if not ts:
        raise VerificationError("Confirmed transaction is missing its timestamp.")
    return {
        "state": "active", "confirmed": True, "confirmations": confirmations,
        "paid": str(paid), "anchor": int(ts),
        "note": f"Confirmed · {confirmations} confirmations",
    }


class VerificationError(Exception):
    """Raised when an on-chain payment cannot be accepted."""


# ---------------------------------------------------------------------------
# Licenses — deterministic HMAC-signed tokens
# ---------------------------------------------------------------------------

LICENSE_VERSION = 1


def allowance_for(plan_id: str, billing: str):
    plan = PLANS[plan_id]
    if plan.get("unlimited"):
        return None
    a = plan["allowance"]
    return a if isinstance(a, (int, type(None))) else a.get(billing)


def compute_expiry(billing: str, anchor_ts: int) -> int | None:
    if billing == "once":
        return None
    return anchor_ts + PERIOD_DAYS[billing] * 86400


def issue_license(secret: str, plan_id: str, billing: str, txid: str, asset: str,
                  paid: str, confirmed: bool, anchor_ts: int) -> str:
    """Sign and return a license token.

    DETERMINISTIC: the payload depends only on the arguments (anchor_ts is the
    on-chain transaction time, never the wall clock), so the same payment
    always yields the identical token.
    """
    plan = PLANS.get(plan_id)
    if plan is None:
        raise BillingError("Unknown plan.")
    if billing not in plan.get("price", {}):
        raise BillingError(f"Billing period '{billing}' not offered for plan '{plan_id}'.")
    payload = {
        "v": LICENSE_VERSION,
        "plan": plan_id,
        "billing": billing,
        "allowance": allowance_for(plan_id, billing),
        "issued_at": int(anchor_ts),
        "expires_at": compute_expiry(billing, int(anchor_ts)),
        "txid": txid.strip(),
        "asset": asset,
        "paid": paid,
        "confirmed": bool(confirmed),
    }
    return _sign(secret, payload)


def _sign(secret: str, payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return _b64url(body) + "." + _b64url(sig)


def verify_license(secret: str, token: str) -> dict:
    """Verify signature + expiry against the server clock. Returns payload."""
    try:
        body_b64, sig_b64 = token.split(".")
        body = _unb64url(body_b64)
        sig = _unb64url(sig_b64)
    except Exception:
        raise BillingError("Malformed license.")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise BillingError("Invalid license signature.")
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError:
        raise BillingError("Malformed license payload.")
    exp = payload.get("expires_at")
    if exp and _now() > exp:
        raise BillingError("License expired.")
    return payload


class BillingError(Exception):
    """User-facing billing error (malformed input, unknown plan, ...)."""


# ---------------------------------------------------------------------------
# Ledger — crash-consistent AUDIT store (defense-in-depth, not authoritative)
# ---------------------------------------------------------------------------


class LedgerStore:
    """Interface: txid -> license audit records. Atomic and durable per the
    backing store. Never authoritative for anti-replay — determinism is."""

    def record(self, txid: str, plan: str, billing: str, asset: str, license_token: str, at: int) -> dict | None:
        raise NotImplementedError

    def find(self, txid: str) -> dict | None:
        raise NotImplementedError


class SqliteLedger(LedgerStore):
    """SQLite audit ledger. UNIQUE(txid) + INSERT OR IGNORE makes concurrent
    claims atomic at the database level (no TOCTOU, safe across threads and
    processes on the same file). WAL + synchronous=FULL give crash consistency.
    Path: BILLING_LEDGER_PATH (default data/axiom_billing.db)."""

    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("BILLING_LEDGER_PATH") or "data/axiom_billing.db"
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _schema(self):
        conn = self._connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ledger("
                " txid TEXT PRIMARY KEY, plan TEXT NOT NULL, billing TEXT NOT NULL,"
                " asset TEXT NOT NULL, license TEXT NOT NULL, at INTEGER NOT NULL)"
            )
            conn.commit()
        finally:
            conn.close()

    def record(self, txid: str, plan: str, billing: str, asset: str, license_token: str, at: int) -> dict | None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO ledger(txid,plan,billing,asset,license,at) VALUES(?,?,?,?,?,?)",
                (_normalize_txid(txid), plan, billing, asset, license_token, at),
            )
            conn.commit()
            return self.find(txid)
        finally:
            conn.close()

    def find(self, txid: str) -> dict | None:
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT txid,plan,billing,asset,license,at FROM ledger WHERE txid=?",
                (_normalize_txid(txid),),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {"txid": row[0], "plan": row[1], "billing": row[2], "asset": row[3],
                    "license": row[4], "at": row[5]}
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Usage metering (per license) — advisory, crash-consistent
# ---------------------------------------------------------------------------


class UsageStore:
    def increment(self, license_token: str, amount: int) -> int:
        raise NotImplementedError

    def used(self, license_token: str) -> int:
        raise NotImplementedError


class SqliteUsageStore(UsageStore):
    """SQLite usage store. Atomic increments. Path: BILLING_USAGE_PATH."""

    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("BILLING_USAGE_PATH") or "data/axiom_billing.db"
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _schema(self):
        conn = self._connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS usage("
                " license TEXT NOT NULL, amount INTEGER NOT NULL, at INTEGER NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_license ON usage(license)")
            conn.commit()
        finally:
            conn.close()

    def increment(self, license_token: str, amount: int) -> int:
        amount = max(0, int(amount))
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO usage(license,amount,at) VALUES(?,?,?)",
                (license_token, amount, _now()),
            )
            conn.commit()
        finally:
            conn.close()
        return self.used(license_token)

    def used(self, license_token: str) -> int:
        conn = self._connect()
        try:
            cur = conn.execute("SELECT COALESCE(SUM(amount),0) FROM usage WHERE license=?", (license_token,))
            return int(cur.fetchone()[0])
        finally:
            conn.close()


def usage_status(payload: dict, used: int) -> dict:
    allowance = payload.get("allowance")
    remaining = None if allowance is None else max(0, allowance - used)
    return {
        "plan": payload.get("plan"),
        "billing": payload.get("billing"),
        "allowance": allowance,
        "used": used,
        "remaining": remaining,
        "exceeded": allowance is not None and used >= allowance,
        "unlimited": allowance is None,
    }
