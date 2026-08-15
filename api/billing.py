"""
Axiom billing API — HTTP adapter over api/billing_core.py.

Endpoints (same-origin, e.g. https://<deploy>/api/billing/...):

  POST /api/billing/verify
      Body: {"txid": "...", "asset": "btc"|"usdt", "plan": "pro"|"team"|"life",
             "billing": "monthly"|"annual"|"once"}
      Server-side on-chain verification with EXACT-amount plan binding.
      - Payment confirmed  -> 201 + signed license. Licenses are
        DETERMINISTIC: the same payment always yields the identical token on
        any instance, after any restart/redeploy, under any concurrency.
      - Payment detected but not yet confirmed (BTC below
        MIN_BTC_CONFIRMATIONS, or USDT not yet mined) -> 202 {"state":
        "pending"}. No license is issued; re-submit the same request once the
        confirmations are met and the identical deterministic license is
        returned.
      - txid already used for the SAME plan -> 200 idempotent with the
        original license.
      - txid already used for a DIFFERENT plan -> 409 payment_already_used.

  POST /api/billing/usage
      Body: {"license": "<signed token>", "amount": <tokens used>}
      Meters usage against the license allowance. Only server-signed licenses
      are accepted, so a browser cannot forge metering.

  GET  /api/billing/status?license=<signed token>
      License status: plan, allowance, usage, expiry, txid, paid, confirmed.

Security posture:
  - BILLING_SIGNING_SECRET lives only in the server environment; absent => 503
    (no guessable-key fallback).
  - The server never trusts client-supplied price, allowance, expiry,
    confirmation or payment state — everything is re-derived on-chain or read
    from the signed license.
  - Request body size capped (16 KiB), Content-Type must be application/json,
    per-client-IP rate limit (per instance — not distributed).
  - USDT verification is pinned to Ethereum mainnet (Ethplorer) + the
    canonical USDT contract + the Axiom recipient (see billing_core).
  - BTC licenses require >= MIN_BTC_CONFIRMATIONS (default 1); zero-conf
    payments stay "pending" and never receive a full-strength license.

Persistence:
  - The anti-replay guarantee is STATELESS: licenses are deterministic
    functions of on-chain facts (txid, exact plan price, on-chain block time),
    so no ledger state is required to prevent double-minting — it is
    structurally impossible to mint two licenses for one payment, even if
    storage is lost, the instance recycles, or the fleet scales out.
  - SQLite (data/axiom_billing.db) additionally provides a crash-consistent
    AUDIT ledger (UNIQUE(txid) + INSERT OR IGNORE) and usage metering behind
    the LedgerStore / UsageStore interfaces. On a read-only filesystem the
    stores degrade to in-memory SQLite without weakening anti-replay.
  - Paths overridable via BILLING_LEDGER_PATH / BILLING_USAGE_PATH.

Local smoke test:
    BILLING_SIGNING_SECRET=dev-secret python3 api/billing.py   # listens :8787
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlparse

from billing_core import (
    ASSETS,
    BillingError,
    MIN_BTC_CONFIRMATIONS,
    PLANS,
    SqliteLedger,
    SqliteUsageStore,
    VerificationError,
    issue_license,
    usage_status,
    verify_btc,
    verify_license,
    verify_usdt,
)

SECRET = os.environ.get("BILLING_SIGNING_SECRET", "")
CORS_ORIGIN = os.environ.get("AXIOM_BILLING_ORIGIN", "*")

MAX_BODY_BYTES = 16 * 1024
_RATE_WINDOW = 60.0
_RATE_MAX = int(os.environ.get("BILLING_RATE_MAX", "60"))
_events: dict[str, list[float]] = {}
_events_lock = threading.Lock()

_REASONS = {
    200: "OK", 201: "Created", 202: "Accepted", 204: "No Content",
    400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed",
    409: "Conflict", 413: "Payload Too Large", 415: "Unsupported Media Type",
    429: "Too Many Requests", 500: "Internal Server Error",
    503: "Service Unavailable",
}


def _build_stores():
    """Prefer persistent SQLite; degrade to in-memory on read-only hosts.

    Degradation is safe: determinism (not the ledger) is the anti-replay
    authority, so losing persistence can never mint a second license.
    """
    try:
        return SqliteLedger(), SqliteUsageStore()
    except (OSError, sqlite3.OperationalError) as e:
        print(f"[billing] persistent store unavailable ({e}); using in-memory stores")
        return SqliteLedger(":memory:"), SqliteUsageStore(":memory:")


LEDGER, USAGE = _build_stores()


class RequestError(Exception):
    """Client-facing request error carrying an HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# JSON/HTTP plumbing
# ---------------------------------------------------------------------------


def _read_json_body(environ) -> dict:
    ctype = (environ.get("CONTENT_TYPE") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise RequestError("Content-Type must be application/json.", 415)
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        length = 0
    if length <= 0:
        raise RequestError("A JSON request body is required.")
    if length > MAX_BODY_BYTES:
        raise RequestError("Request body too large.", 413)
    raw = environ["wsgi.input"].read(length)
    if len(raw) > MAX_BODY_BYTES:
        raise RequestError("Request body too large.", 413)
    try:
        body = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise RequestError("Invalid JSON body.") from e
    if not isinstance(body, dict):
        raise RequestError("JSON body must be an object.")
    return body


def _cors_headers() -> list[tuple[str, str]]:
    return [
        ("Access-Control-Allow-Origin", CORS_ORIGIN),
        ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type"),
        ("Access-Control-Max-Age", "86400"),
    ]


def _respond(start_response, status: int, payload: dict):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    headers += _cors_headers()
    start_response(f"{status} {_REASONS.get(status, '')}".strip(), headers)
    return [body]


def _rate_allowed(client_ip: str) -> bool:
    now = time.monotonic()
    with _events_lock:
        ev = _events.setdefault(client_ip, [])
        ev[:] = [t for t in ev if now - t < _RATE_WINDOW]
        if len(ev) >= _RATE_MAX:
            return False
        ev.append(now)
        return True


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _handle_verify(body: dict):
    txid = str(body.get("txid") or "").strip().lower()
    asset = str(body.get("asset") or "").strip().lower()
    plan = str(body.get("plan") or "").strip().lower()
    billing = str(body.get("billing") or "").strip().lower()

    if not txid:
        raise RequestError("txid is required.")
    if not re.fullmatch(r"[0-9a-f]{64}", txid):
        raise RequestError("txid must be a 64-character hexadecimal transaction hash.")
    if asset not in ASSETS:
        raise RequestError("asset must be 'btc' or 'usdt'.")
    if plan not in PLANS:
        raise RequestError("Unknown plan.")
    if billing not in PLANS[plan].get("price", {}):
        raise RequestError(f"Billing period '{billing}' not offered for plan '{plan}'.")

    # 1) Audit-ledger check (defense-in-depth — determinism is authoritative).
    existing = LEDGER.find(txid)
    if existing is not None and existing.get("license"):
        try:
            payload = verify_license(SECRET, existing["license"])
        except BillingError:
            payload = None  # e.g. signing-key rotation -> re-verify on-chain
        if payload is not None:
            if payload.get("plan") == plan and payload.get("billing") == billing:
                return 200, {
                    "ok": True, "state": "active", "idempotent": True,
                    "license": existing["license"], "plan": plan, "billing": billing,
                    "note": "This payment already has a license — returning the original.",
                }
            raise RequestError(
                "payment_already_used: this transaction hash was already used for another license.",
                409,
            )

    # 2) Server-side verification — exact price from the server plan table,
    #    never from the client. Exact-amount matching chain-binds the payment
    #    to exactly one plan/period.
    price = PLANS[plan]["price"][billing]
    exact = price["btc"] if asset == "btc" else str(price["usdt"])
    if asset == "btc":
        result = verify_btc(txid, exact)
    else:
        result = verify_usdt(txid, exact)

    # 3) Payment detected but not yet confirmed — no license until confirmed.
    if result["state"] != "active":
        return 202, {
            "ok": True, "state": "pending", "asset": asset, "plan": plan,
            "billing": billing, "paid": result.get("paid"),
            "confirmations": result.get("confirmations", 0),
            "min_confirmations": MIN_BTC_CONFIRMATIONS,
            "note": result["note"],
        }

    # 4) Confirmed — issue the deterministic license and audit it.
    token = issue_license(
        SECRET, plan, billing, txid, asset,
        result["paid"], True, result["anchor"],
    )
    LEDGER.record(txid, plan, billing, asset, token, int(time.time()))
    payload = verify_license(SECRET, token)
    return 201, {
        "ok": True, "state": "active", "idempotent": False,
        "license": token, "plan": plan, "billing": billing,
        "paid": result["paid"], "asset": asset, "confirmed": True,
        "confirmations": result.get("confirmations", 1),
        "note": result["note"],
        "allowance": payload.get("allowance"),
        "issued_at": payload.get("issued_at"),
        "expires_at": payload.get("expires_at"),
    }


def _handle_usage(body: dict):
    token = str(body.get("license") or "")
    try:
        amount = int(body.get("amount") or 0)
    except (TypeError, ValueError):
        raise RequestError("amount must be an integer.") from None
    if not token:
        raise RequestError("license is required.")
    payload = verify_license(SECRET, token)
    used = USAGE.increment(token, amount)
    status = usage_status(payload, used)
    status["ok"] = True
    return 200, status


def _handle_status(params: dict):
    token = (params.get("license") or [""])[0]
    if not token:
        raise RequestError("license query parameter is required.")
    payload = verify_license(SECRET, token)
    used = USAGE.used(token)
    status = usage_status(payload, used)
    status.update({
        "ok": True,
        "issued_at": payload.get("issued_at"),
        "expires_at": payload.get("expires_at"),
        "txid": payload.get("txid"),
        "asset": payload.get("asset"),
        "paid": payload.get("paid"),
        "confirmed": payload.get("confirmed"),
    })
    return 200, status


# ---------------------------------------------------------------------------
# WSGI app
# ---------------------------------------------------------------------------


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = urlparse(environ.get("PATH_INFO", "")).path.rstrip("/")
    client = environ.get("REMOTE_ADDR", "unknown")

    if method == "OPTIONS":
        start_response("204 No Content", _cors_headers())
        return [b""]

    if not _rate_allowed(client):
        return _respond(start_response, 429, {
            "ok": False, "error": "rate_limited",
            "message": "Too many requests — please try again shortly.",
        })

    if SECRET == "":
        return _respond(start_response, 503, {
            "ok": False, "error": "billing_not_configured",
            "note": "BILLING_SIGNING_SECRET is not set on the server.",
        })

    try:
        if method == "POST" and path.endswith("/api/billing/verify"):
            return _respond(start_response, *_handle_verify(_read_json_body(environ)))
        if method == "POST" and path.endswith("/api/billing/usage"):
            return _respond(start_response, *_handle_usage(_read_json_body(environ)))
        if method == "GET" and path.endswith("/api/billing/status"):
            return _respond(start_response, *_handle_status(parse_qs(environ.get("QUERY_STRING", ""))))
        return _respond(start_response, 404, {"ok": False, "error": "not_found"})
    except RequestError as e:
        return _respond(start_response, e.status, {
            "ok": False, "error": "bad_request", "message": str(e),
        })
    except VerificationError as e:
        return _respond(start_response, 400, {
            "ok": False, "error": "verification_failed", "message": str(e),
        })
    except BillingError as e:
        return _respond(start_response, 400, {
            "ok": False, "error": "billing_error", "message": str(e),
        })
    except Exception as e:  # noqa: BLE001 — surface a clean 500
        return _respond(start_response, 500, {
            "ok": False, "error": "internal", "message": str(e),
        })


# Local smoke-test server
if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    port = int(os.environ.get("PORT", "8787"))
    with make_server("0.0.0.0", port, app) as httpd:
        print(f"Axiom billing API listening on http://0.0.0.0:{port} (secret set: {bool(SECRET)})")
        httpd.serve_forever()
