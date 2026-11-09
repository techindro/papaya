"""Papaya backend (FastAPI).

Flow:  telemetry -> validation -> pricing -> OAuth token -> PayPal Sandbox
       order -> capture -> log.

Run from the project root:
    uvicorn backend.main:app --reload
"""

from __future__ import annotations

import hmac
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import numpy as np
import psutil
import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from backend import config

app = FastAPI(
    title="Papaya",
    description="Automated payment layer for servers and AI nodes (PayPal Sandbox).",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# Data models (Pydantic rejects broken or unexpected input)
# ---------------------------------------------------------------------------


class TelemetryPayload(BaseModel):
    """Workload reported by one node for one billing window."""

    model_config = {"extra": "forbid"}

    node_id: str = Field(..., pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    cpu_percent: float = Field(..., ge=0, le=100)
    tokens: int = Field(0, ge=0, le=10_000_000)
    duration_seconds: float = Field(..., gt=0, le=3600)
    memory_percent: float | None = Field(None, ge=0, le=100)


class PriceBreakdown(BaseModel):
    cpu_cost: str
    token_cost: str
    total: str
    currency: str


# ---------------------------------------------------------------------------
# In-memory history + text logs ("Sovereign Node Logs")
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_logs: deque[str] = deque(maxlen=config.MAX_LOG_LINES)
_transactions: deque[dict[str, Any]] = deque(maxlen=config.MAX_TRANSACTIONS)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def log(level: str, message: str) -> None:
    """Append a line to the in-memory log and (best effort) to a text file.

    Never pass secrets or tokens into this function.
    """
    line = f"[{_now()}] {level.upper():<7} {message}"
    with _lock:
        _logs.append(line)
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        with config.LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass  # logging must never break the request


# ---------------------------------------------------------------------------
# Analytical Modeler (pricing)
# ---------------------------------------------------------------------------


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def price_workload(p: TelemetryPayload) -> tuple[Decimal, PriceBreakdown]:
    """Turn CPU load, time and data tokens into a price."""
    cpu_cost = (
        Decimal(str(p.cpu_percent)) / Decimal(100)
        * Decimal(str(p.duration_seconds))
        * config.CPU_RATE_PER_SECOND
    )
    token_cost = Decimal(p.tokens) / Decimal(1000) * config.TOKEN_RATE_PER_1K
    total = max(_money(cpu_cost + token_cost), config.MIN_CHARGE)
    breakdown = PriceBreakdown(
        cpu_cost=f"{cpu_cost:.6f}",
        token_cost=f"{token_cost:.6f}",
        total=f"{total:.2f}",
        currency=config.CURRENCY,
    )
    return total, breakdown


# ---------------------------------------------------------------------------
# PayPal Sandbox client (OAuth 2.0 + Orders v2)
# ---------------------------------------------------------------------------


class PayPalUnavailable(Exception):
    """Network problem: timeout, DNS failure, connection refused..."""


class PayPalError(Exception):
    """PayPal answered with an error status."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _error_message(resp: requests.Response) -> str:
    try:
        data = resp.json()
        return str(data.get("message") or data.get("error_description") or data)[:300]
    except ValueError:
        return resp.text[:300]


class _TokenCache:
    """Caches the OAuth token and refreshes it shortly before it expires."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    def get(self, force: bool = False) -> str:
        with self._lock:
            stale = time.time() >= self._expires_at - config.TOKEN_REFRESH_MARGIN_SECONDS
            if force or not self._token or stale:
                self._fetch()
            assert self._token is not None
            return self._token

    def _fetch(self) -> None:
        try:
            resp = requests.post(
                f"{config.PAYPAL_BASE_URL}/v1/oauth2/token",
                auth=(config.PAYPAL_CLIENT_ID, config.PAYPAL_CLIENT_SECRET),
                data={"grant_type": "client_credentials"},
                headers={"Accept": "application/json"},
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise PayPalUnavailable(f"token request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise PayPalError(resp.status_code, f"token request rejected: {_error_message(resp)}")
        data = resp.json()
        self._token = data["access_token"]
        self._expires_at = time.time() + int(data.get("expires_in", 300))
        log("info", "PayPal access token refreshed")

    def clear(self) -> None:
        with self._lock:
            self._token = None
            self._expires_at = 0.0


_tokens = _TokenCache()


def _paypal_call(
    method: str, path: str, payload: dict | None = None, request_id: str | None = None
) -> dict:
    """Call PayPal. Retries once with a fresh token if the old one was rejected."""
    resp: requests.Response | None = None
    for attempt in (0, 1):
        token = _tokens.get(force=attempt == 1)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id  # idempotency key
        try:
            resp = requests.request(
                method,
                f"{config.PAYPAL_BASE_URL}{path}",
                json=payload,
                headers=headers,
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise PayPalUnavailable(f"{method} {path} failed: {exc}") from exc
        if resp.status_code == 401 and attempt == 0:
            continue
        break
    assert resp is not None
    if resp.status_code >= 400:
        raise PayPalError(resp.status_code, _error_message(resp))
    return resp.json() if resp.content else {}


def _create_order(node_id: str, amount: Decimal, request_id: str) -> dict:
    body: dict[str, Any] = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": node_id,
                "description": f"Papaya compute usage for node {node_id}",
                "amount": {"currency_code": config.CURRENCY, "value": f"{amount:.2f}"},
            }
        ],
    }
    if config.PAYPAL_VAULT_ID:
        # Stored payment source: lets the merchant charge without a buyer page.
        body["payment_source"] = {
            "paypal": {
                "vault_id": config.PAYPAL_VAULT_ID,
                "stored_credential": {
                    "payment_initiator": "MERCHANT",
                    "payment_type": "UNSCHEDULED",
                    "usage": "SUBSEQUENT",
                },
            }
        }
    return _paypal_call("POST", "/v2/checkout/orders", body, request_id)


def _capture_order(order_id: str, request_id: str) -> dict:
    return _paypal_call("POST", f"/v2/checkout/orders/{order_id}/capture", {}, request_id)


def _capture_id(order: dict) -> str | None:
    try:
        return order["purchase_units"][0]["payments"]["captures"][0]["id"]
    except (KeyError, IndexError, TypeError):
        return None


def _approval_url(order: dict) -> str | None:
    for link in order.get("links", []):
        if link.get("rel") in ("payer-action", "approve"):
            return link.get("href")
    return None


# ---------------------------------------------------------------------------
# Payment orchestration
# ---------------------------------------------------------------------------


def process_payment(payload: TelemetryPayload) -> dict[str, Any]:
    """Price the workload, then try to pay it. Never raises on network errors."""
    amount, breakdown = price_workload(payload)
    record: dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "timestamp": _now(),
        "node_id": payload.node_id,
        "cpu_percent": payload.cpu_percent,
        "tokens": payload.tokens,
        "duration_seconds": payload.duration_seconds,
        "amount": f"{amount:.2f}",
        "currency": config.CURRENCY,
        "breakdown": breakdown.model_dump(),
        "mode": "paypal_sandbox",
        "status": "PENDING",
        "order_id": None,
        "capture_id": None,
        "approval_url": None,
        "detail": "",
    }

    if amount > config.MAX_CHARGE:
        record.update(
            mode="none",
            status="REJECTED",
            detail=f"Charge {amount:.2f} exceeds the per-call cap of {config.MAX_CHARGE:.2f}",
        )
        log("warning", f"node={payload.node_id} rejected: {record['detail']}")
        return _store(record)

    if not config.paypal_configured():
        record.update(
            mode="local_fallback",
            status="LOCAL_ONLY",
            detail="PayPal credentials not set; price calculated locally, no payment made",
        )
        log("warning", f"node={payload.node_id} local fallback amount={record['amount']}")
        return _store(record)

    request_id = uuid.uuid4().hex
    try:
        order = _create_order(payload.node_id, amount, request_id)
        record["order_id"] = order.get("id")
        status = order.get("status", "UNKNOWN")
        log("info", f"node={payload.node_id} order {record['order_id']} status={status}")

        if status == "APPROVED":
            order = _capture_order(order["id"], request_id + "-cap")
            status = order.get("status", "UNKNOWN")

        record["status"] = status
        record["capture_id"] = _capture_id(order)
        if status == "COMPLETED":
            record["detail"] = "Payment captured"
            log("info", f"node={payload.node_id} captured {record['amount']} {config.CURRENCY}")
        else:
            record["approval_url"] = _approval_url(order)
            record["detail"] = (
                "Order created but the payment source has not approved it yet. "
                "Set PAYPAL_VAULT_ID to charge a stored payment source without a buyer page."
            )
            log("warning", f"node={payload.node_id} order not completed (status={status})")
    except PayPalUnavailable as exc:
        record.update(
            mode="local_fallback",
            status="LOCAL_ONLY",
            detail=f"PayPal unreachable; price calculated locally ({exc})",
        )
        log("error", f"node={payload.node_id} PayPal unreachable, using local fallback")
    except PayPalError as exc:
        record.update(status="FAILED", detail=f"PayPal error {exc.status_code}: {exc.message}")
        log("error", f"node={payload.node_id} PayPal error {exc.status_code}")
    return _store(record)


def _store(record: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        _transactions.append(record)
    return record


# ---------------------------------------------------------------------------
# Telemetry Sensor
# ---------------------------------------------------------------------------


def sample_local_telemetry(node_id: str, window_seconds: float) -> dict[str, Any]:
    """Read the real CPU/memory of this machine.

    Data tokens are *simulated* (Poisson noise scaled by CPU load) so the demo
    works on any laptop. Real nodes should send their own token counts.
    """
    cpu = float(psutil.cpu_percent(interval=0.5))
    mem = float(psutil.virtual_memory().percent)
    rng = np.random.default_rng()
    tokens = int(rng.poisson(lam=200 + cpu * 20))
    return {
        "node_id": node_id,
        "cpu_percent": round(cpu, 1),
        "memory_percent": round(mem, 1),
        "tokens": tokens,
        "duration_seconds": window_seconds,
    }


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Protects spending endpoints when PAPAYA_API_KEY is set."""
    if config.PAPAYA_API_KEY and not hmac.compare_digest(
        x_api_key or "", config.PAPAYA_API_KEY
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "Papaya", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "paypal_configured": config.paypal_configured(),
        "vault_configured": bool(config.PAYPAL_VAULT_ID),
        "api_key_required": bool(config.PAPAYA_API_KEY),
        "currency": config.CURRENCY,
    }


@app.get("/telemetry/sample")
def telemetry_sample(
    node_id: str = Query("local-node", pattern=r"^[A-Za-z0-9_.-]{1,64}$"),
    window_seconds: float = Query(5.0, gt=0, le=3600),
) -> dict[str, Any]:
    return sample_local_telemetry(node_id, window_seconds)


@app.post("/price", response_model=PriceBreakdown)
def price(payload: TelemetryPayload) -> PriceBreakdown:
    """Price a workload without paying for it."""
    return price_workload(payload)[1]


@app.post("/pay", dependencies=[Depends(require_api_key)])
def pay(payload: TelemetryPayload) -> dict[str, Any]:
    """Price a workload and pay it through the PayPal Sandbox."""
    return process_payment(payload)


@app.get("/transactions")
def transactions(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    with _lock:
        return list(_transactions)[-limit:][::-1]


@app.get("/logs")
def logs(limit: int = Query(100, ge=1, le=500)) -> dict[str, list[str]]:
    with _lock:
        return {"lines": list(_logs)[-limit:]}
