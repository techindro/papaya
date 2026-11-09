"""Papaya configuration.

All PayPal credentials come from environment variables. Nothing secret is
stored in the code base. Papaya talks to the PayPal *Sandbox* only.

Required environment variables
    PAYPAL_CLIENT_ID       Sandbox app client id
    PAYPAL_CLIENT_SECRET   Sandbox app secret

Optional environment variables
    PAYPAL_VAULT_ID        Vaulted PayPal payment token. When set, orders are
                           created against this stored payment source so no
                           buyer approval page is needed.
    PAPAYA_API_KEY         If set, POST /pay requires an `X-API-Key` header.
    PAPAYA_MAX_CHARGE      Hard cap per charge (default 5.00).
    PAPAYA_CURRENCY        ISO currency code (default USD).
"""

import os
from decimal import Decimal
from pathlib import Path

# --- PayPal (Sandbox only) -------------------------------------------------
PAYPAL_BASE_URL = "https://api-m.sandbox.paypal.com"
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_VAULT_ID = os.getenv("PAYPAL_VAULT_ID", "")

# --- Networking ------------------------------------------------------------
REQUEST_TIMEOUT_SECONDS = 10
TOKEN_REFRESH_MARGIN_SECONDS = 60  # refresh the token this long before expiry

# --- Pricing (all values in CURRENCY) --------------------------------------
CURRENCY = os.getenv("PAPAYA_CURRENCY", "USD")
CPU_RATE_PER_SECOND = Decimal("0.002")   # price of 100% CPU for one second
TOKEN_RATE_PER_1K = Decimal("0.01")      # price per 1,000 data tokens
MIN_CHARGE = Decimal("0.01")             # PayPal minimum is 0.01
MAX_CHARGE = Decimal(os.getenv("PAPAYA_MAX_CHARGE", "5.00"))

# --- Access control --------------------------------------------------------
PAPAYA_API_KEY = os.getenv("PAPAYA_API_KEY", "")

# --- Logs and history ------------------------------------------------------
LOG_DIR = Path(os.getenv("PAPAYA_LOG_DIR", "logs"))
LOG_FILE = LOG_DIR / "node_logs.txt"
MAX_LOG_LINES = 500
MAX_TRANSACTIONS = 500


def paypal_configured() -> bool:
    """True when both Sandbox credentials are present."""
    return bool(PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET)
