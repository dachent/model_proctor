"""Fixture: big exploration — trace refund flow across ~50 modules in 8 packages.

Generates a synthetic order-processing system with ~50 Python modules across
8 packages (api, orders, payments, refunds, inventory, notifications,
persistence, fraud). One true end-to-end refund flow crosses 8 modules in a
defined order. Decoy chains, dead-end modules, and realistic cross-imports
provide noise that makes exhaustive reading expensive.
"""
import os
import sys
import random

TRUE_CHAIN = [
    ("api/routes.py", "post_refund"),
    ("orders/service.py", "initiate_refund"),
    ("refunds/manager.py", "process_refund"),
    ("refunds/validator.py", "check_eligibility"),
    ("payments/gateway.py", "reverse_charge"),
    ("inventory/restock.py", "restock_items"),
    ("notifications/emailer.py", "send_refund_confirmation"),
    ("persistence/ledger.py", "record_refund"),
]
CHAIN_STEP_COUNT = 8
DECOY_FUNC = "flag_suspicious_refund"

PACKAGES = {
    "api": ("HTTP API layer - REST endpoints and request handling",
            ["routes", "auth", "middleware", "serializers", "errors",
             "rate_limiter", "versioning"]),
    "orders": ("Order management - creation, updates, lifecycle",
               ["service", "models", "repository", "events", "pricing",
                "lifecycle"]),
    "payments": ("Payment processing - gateway, authorization, settlement",
                 ["gateway", "processor", "models", "exceptions", "retries",
                  "webhook"]),
    "refunds": ("Refund processing - eligibility, policies, execution",
                ["manager", "validator", "policies", "calculator", "history"]),
    "inventory": ("Inventory management - stock levels, restocking, allocation",
                  ["restock", "tracker", "models", "warehouse", "allocation"]),
    "notifications": ("Notification delivery - email, SMS, templating",
                      ["emailer", "sms", "templates", "queue", "dispatcher"]),
    "persistence": ("Data persistence - ledger, database, sessions",
                    ["ledger", "database", "migrations", "models", "session"]),
    "fraud": ("Fraud detection - risk scoring, pattern analysis",
              ["checker", "rules", "scorer", "models"]),
}

EXPLICIT_MODULES = {
    "api/routes", "orders/service", "orders/repository", "orders/models",
    "refunds/manager", "refunds/validator", "payments/gateway",
    "payments/processor", "payments/models", "inventory/restock",
    "notifications/emailer", "persistence/ledger", "fraud/checker",
}

# Decoy imports create plausible-looking but wrong paths through the codebase.
DECOY_IMPORTS = {
    "orders/events": "from fraud.checker import flag_suspicious_refund",
    "payments/retries": "from fraud.checker import flag_suspicious_refund",
    "refunds/policies": "from fraud.checker import flag_suspicious_refund",
    "refunds/calculator": "from payments.gateway import reverse_charge",
    "inventory/allocation": "from persistence.ledger import record_refund",
    "notifications/dispatcher": "from persistence.ledger import record_refund",
    "api/middleware": "from fraud.checker import flag_suspicious_refund",
    "orders/lifecycle": "from refunds.manager import process_refund",
}

FILLER_NAMES = ["order", "customer", "payment", "refund", "invoice", "product",
                "item", "transaction", "account", "session", "warehouse",
                "ledger", "cart", "shipping", "tax", "discount", "coupon",
                "address"]
FILLER_VERBS = ["validate", "serialize", "deserialize", "compute", "format",
                "parse", "normalize", "transform", "extract", "sanitize",
                "encode", "decode", "render", "compile", "resolve"]
FILLER_TYPES = ["str", "int", "float", "bool"]


def gen_filler(rng, content, target_min=6000):
    """Pad content with realistic filler functions to reach target size."""
    counter = 0
    while len(content) < target_min:
        counter += 1
        name = rng.choice(FILLER_NAMES)
        verb = rng.choice(FILLER_VERBS)
        typ = rng.choice(FILLER_TYPES)
        fn = f"_{verb}_{name}_{counter}"
        content += '\n\ndef ' + fn + f'(value: {typ}) -> bool:\n'
        content += f'    """{verb.capitalize()} the {name} value.\n\n'
        content += f'    Internal helper that {verb}s the provided {name} value,\n'
        content += f'    applying format and business rule checks specific to\n'
        content += f'    this module.\n\n'
        content += f'    Args:\n        value: The {name} value to {verb}.\n\n'
        content += f'    Returns:\n        True if the value passes {verb}ion,\n'
        content += f'        False otherwise.\n    """\n'
        content += f'    if value is None:\n        return False\n'
        content += f'    if not isinstance(value, {typ}):\n        return False\n'
        content += f'    if isinstance(value, str) and len(value) == 0:\n'
        content += f'        return False\n'
        content += f'    if isinstance(value, (int, float)) and value < 0:\n'
        content += f'        return False\n'
        content += f'    return True\n'
    return content


def gen_auto_module(pkg, mod, pkg_desc, rng, extra_imports=""):
    """Generate a non-chain module with realistic content and padding."""
    desc = pkg_desc.split('-')[0].strip()
    content = f'"""{pkg}.{mod} - {desc} module.\n\n'
    content += f'This module provides {mod}-related functionality within\n'
    content += f'the {pkg} package of the order-processing system.\n'
    content += f'"""\nfrom typing import Any, Dict, List, Optional, Tuple\n'
    content += f'import json\nimport re\n'
    if extra_imports:
        content += extra_imports + '\n'
    content += f'\nMAX_{mod.upper()}_RETRIES = 3\n'
    content += f'DEFAULT_{mod.upper()}_TIMEOUT = 30\n'
    content += f'{mod.upper()}_CACHE_TTL = 3600\n'

    # Two base functions
    for _ in range(2):
        name = rng.choice(FILLER_NAMES)
        verb = rng.choice(["create", "update", "get", "list", "process"])
        fn = f"{verb}_{name}"
        content += f'\ndef {fn}(data: Dict[str, Any]) -> Dict[str, Any]:\n'
        content += f'    """{verb.capitalize()} a {name} record."""\n'
        content += f'    return {{"status": "ok", "data": data}}\n'

    # Function that uses the decoy import (if any)
    if extra_imports:
        if "flag_suspicious_refund" in extra_imports:
            content += '\ndef run_security_check(order_id: str) -> bool:\n'
            content += '    """Run a standalone security check."""\n'
            content += '    return flag_suspicious_refund(order_id)\n'
        elif "reverse_charge" in extra_imports:
            content += '\ndef calculate_reversal_fee(order_id: str) -> float:\n'
            content += '    """Calculate reversal fee for accounting."""\n'
            content += '    reversal = reverse_charge(order_id)\n'
            content += '    return float(reversal.get("amount", 0.0))\n'
        elif "record_refund" in extra_imports:
            content += '\ndef log_transaction(order_id: str) -> None:\n'
            content += '    """Log a transaction for audit purposes."""\n'
            content += '    record_refund(order_id, "audit", {})\n'
        elif "process_refund" in extra_imports:
            content += '\ndef handle_lifecycle_event(order_id: str) -> None:\n'
            content += '    """Handle an order lifecycle event."""\n'
            content += '    process_refund(order_id, "lifecycle_event")\n'

    content = gen_filler(rng, content, target_min=6000)
    return content


def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    rng = random.Random(42)

    def w(rel, content):
        full = os.path.join(d, *rel.split('/'))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w', encoding='utf-8') as f:
            f.write(content)

    # ── Chain modules (explicit content, padded) ──────────────────────

    w('api/routes.py', gen_filler(rng, '''"""HTTP API route handlers for the order-processing system.

This module defines all REST API endpoints, including order creation,
payment processing, refund handling, and status queries.
"""
from typing import Any, Dict, List, Optional
from orders.service import initiate_refund, get_order_status
from orders.repository import OrderRepository
from payments.processor import process_payment
from payments.models import PaymentRequest


def post_refund(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle POST /api/v1/refunds - create a refund request.

    Receives a refund request from the HTTP layer, extracts the order
    ID and refund reason, and delegates to the order service to
    initiate the refund workflow.

    Args:
        request_data: Parsed JSON body with order_id and reason.

    Returns:
        A response dict with refund status and details.
    """
    order_id = request_data.get("order_id")
    if not order_id:
        raise ValueError("order_id is required")
    reason = request_data.get("reason", "customer_request")
    result = initiate_refund(order_id, reason)
    return {"status": "ok", "refund": result}
'''))

    w('orders/service.py', gen_filler(rng, '''"""Order service - core business logic for order operations.

Provides functions for creating, updating, querying orders and
initiating refunds and cancellations.
"""
from typing import Any, Dict, List, Optional
from refunds.manager import process_refund
from orders.repository import OrderRepository
from orders.models import Order, OrderStatus


def initiate_refund(order_id: str, reason: str) -> Dict[str, Any]:
    """Initiate a refund for the specified order.

    Validates that the order exists and has not already been refunded,
    then delegates to the refund manager to process the refund.

    Args:
        order_id: The unique identifier of the order to refund.
        reason: The reason for the refund.

    Returns:
        A dict containing the refund result and status.
    """
    repo = OrderRepository()
    order = repo.get_by_id(order_id)
    if order is None:
        return {"status": "error", "message": "Order not found"}
    if order.status == OrderStatus.REFUNDED:
        return {"status": "error", "message": "Order already refunded"}
    result = process_refund(order_id, reason)
    return result


def get_order_status(order_id: str) -> Dict[str, Any]:
    """Get the current status of an order."""
    repo = OrderRepository()
    order = repo.get_by_id(order_id)
    if order is None:
        return {"status": "error", "message": "Order not found"}
    return {"order_id": order_id, "status": order.status.value}
'''))

    w('orders/models.py', gen_filler(rng, '''"""Order models - data structures for orders and order items.

Defines the core domain types used throughout the order-processing
system.
"""
from typing import Any, Dict, List, Optional
from enum import Enum


class OrderStatus(Enum):
    """Enumeration of possible order states."""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class Order:
    """Represents a customer order in the system."""

    def __init__(self, order_id: str, customer_id: str,
                 items: List[Dict[str, Any]], total: float,
                 status: OrderStatus = OrderStatus.PENDING):
        self.order_id = order_id
        self.customer_id = customer_id
        self.items = items
        self.total = total
        self.status = status

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "customer_id": self.customer_id,
            "items": self.items,
            "total": self.total,
            "status": self.status.value,
        }


class OrderItem:
    """Represents a line item in an order."""

    def __init__(self, product_id: str, quantity: int, price: float):
        self.product_id = product_id
        self.quantity = quantity
        self.price = price
'''))

    w('orders/repository.py', gen_filler(rng, '''"""Order repository - data access layer for orders.

Provides persistence and query operations for order records.
"""
from typing import Any, Dict, List, Optional
from orders.models import Order, OrderStatus


class OrderRepository:
    """Repository for persisting and retrieving orders."""

    def __init__(self):
        self._store: Dict[str, Order] = {}

    def get_by_id(self, order_id: str) -> Optional[Order]:
        """Retrieve an order by its ID."""
        return self._store.get(order_id)

    def save(self, order: Order) -> None:
        """Save an order to the repository."""
        self._store[order.order_id] = order

    def list_by_customer(self, customer_id: str) -> List[Order]:
        """List all orders for a given customer."""
        return [o for o in self._store.values()
                if o.customer_id == customer_id]

    def list_by_status(self, status: OrderStatus) -> List[Order]:
        """List all orders with a given status."""
        return [o for o in self._store.values() if o.status == status]
'''))

    w('refunds/manager.py', gen_filler(rng, '''"""Refund manager - orchestrates the end-to-end refund workflow.

Coordinates eligibility checking, payment reversal, inventory
restocking, customer notification, and ledger recording for refund
operations.
"""
from typing import Any, Dict, List, Optional
from refunds.validator import check_eligibility
from payments.gateway import reverse_charge
from inventory.restock import restock_items
from notifications.emailer import send_refund_confirmation
from persistence.ledger import record_refund
from fraud.checker import flag_suspicious_refund


def process_refund(order_id: str, reason: str) -> Dict[str, Any]:
    """Process a refund request through the complete workflow.

    Executes the following steps in order:
    1. Check refund eligibility via the validator.
    2. Reverse the original charge via the payment gateway.
    3. Restock returned inventory items.
    4. Send refund confirmation email to the customer.
    5. Record the refund in the persistence ledger.

    Args:
        order_id: The order to refund.
        reason: The reason for the refund.

    Returns:
        A dict with the refund outcome and details.
    """
    eligibility = check_eligibility(order_id, reason)
    if not eligibility["eligible"]:
        return {"status": "denied", "reason": eligibility["reason"]}

    reversal = reverse_charge(order_id)
    restock_items(order_id)
    send_refund_confirmation(order_id)
    record_refund(order_id, reason, reversal)

    return {"status": "completed", "order_id": order_id}


def run_fraud_scan(order_id: str) -> bool:
    """Run a fraud scan on an order.

    This is a separate analysis path, not part of the standard
    refund processing flow.
    """
    return flag_suspicious_refund(order_id)
'''))

    w('refunds/validator.py', gen_filler(rng, '''"""Refund validator - eligibility checks and refund policy enforcement.

Determines whether a refund request meets the business criteria for
approval, including time limits, status checks, and policy rules.
"""
from typing import Any, Dict, List, Optional
from fraud.checker import flag_suspicious_refund


def check_eligibility(order_id: str, reason: str) -> Dict[str, Any]:
    """Check whether a refund request is eligible for processing.

    Validates the order status, refund reason, and time window to
    determine if the refund can proceed.

    Args:
        order_id: The order to check.
        reason: The stated reason for the refund.

    Returns:
        A dict with 'eligible' (bool) and 'reason' (str or None).
    """
    valid_reasons = [
        "customer_request", "product_defect", "shipping_error",
        "billing_error", "duplicate_order",
    ]
    if reason not in valid_reasons:
        return {"eligible": False, "reason": f"Invalid reason: {reason}"}
    return {"eligible": True, "reason": None}


def deep_fraud_scan(order_id: str) -> Dict[str, Any]:
    """Perform a deep fraud analysis on a refund request.

    This is a separate analysis path, not part of the standard
    refund eligibility check.
    """
    flagged = flag_suspicious_refund(order_id)
    return {"flagged": flagged, "score": 0.0}
'''))

    w('payments/gateway.py', gen_filler(rng, '''"""Payment gateway - interfaces with external payment processors.

Handles charge creation, authorization, capture, and reversal through
the payment provider API.
"""
from typing import Any, Dict, List, Optional


def reverse_charge(order_id: str) -> Dict[str, Any]:
    """Reverse a previously captured charge for the given order.

    Contacts the payment processor to issue a full or partial
    reversal of the original charge.

    Args:
        order_id: The order whose charge should be reversed.

    Returns:
        A dict containing the reversal transaction ID and status.
    """
    reversal_id = f"RV_{order_id}"
    return {"reversal_id": reversal_id, "status": "reversed", "amount": 0.0}
'''))

    w('payments/processor.py', gen_filler(rng, '''"""Payment processor - handles payment authorization and capture.

Coordinates with the payment gateway to process payments for orders.
"""
from typing import Any, Dict, List, Optional


def process_payment(payment_request: Dict[str, Any]) -> Dict[str, Any]:
    """Process a payment request.

    Args:
        payment_request: A dictionary containing payment details.

    Returns:
        A dict with the payment processing result.
    """
    return {"status": "processed", "transaction_id": "TXN_001"}
'''))

    w('payments/models.py', gen_filler(rng, '''"""Payment models - data structures for payments and transactions.

Defines the types used in payment processing throughout the system.
"""
from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class PaymentRequest:
    """Represents a payment request from a customer."""
    order_id: str
    amount: float
    currency: str = "USD"
    method: str = "card"


@dataclass
class Payment:
    """Represents a completed payment transaction."""
    payment_id: str
    order_id: str
    amount: float
    status: str = "completed"
    currency: str = "USD"
'''))

    w('inventory/restock.py', gen_filler(rng, '''"""Inventory restock - handles returning items to stock after refunds.

Updates inventory counts and warehouse records when items are returned
as part of a refund or return process.
"""
from typing import Any, Dict, List, Optional


def restock_items(order_id: str) -> Dict[str, Any]:
    """Restock all items from a refunded order.

    Iterates over the order line items and increments the inventory
    count for each product in the appropriate warehouse.

    Args:
        order_id: The order whose items should be restocked.

    Returns:
        A dict with the count of items restocked and any errors.
    """
    return {"restocked": 0, "errors": []}
'''))

    w('notifications/emailer.py', gen_filler(rng, '''"""Email notification service - sends transactional emails.

Provides functions for sending order confirmations, refund
confirmations, shipping notices, and other customer-facing emails.
"""
from typing import Any, Dict, List, Optional


def send_refund_confirmation(order_id: str) -> Dict[str, Any]:
    """Send a refund confirmation email to the customer.

    Composes and sends an email notification confirming that a refund
    has been processed for the given order.

    Args:
        order_id: The order for which to send the confirmation.

    Returns:
        A dict with the email send status and message ID.
    """
    return {"sent": True, "message_id": f"MSG_{order_id}"}
'''))

    w('persistence/ledger.py', gen_filler(rng, '''"""Persistence ledger - permanent record of all financial transactions.

Provides functions for recording orders, payments, refunds, and
adjustments in the immutable transaction ledger.
"""
from typing import Any, Dict, List, Optional


def record_refund(order_id: str, reason: str,
                  reversal: Dict[str, Any]) -> Dict[str, Any]:
    """Record a completed refund in the persistence ledger.

    Creates a permanent ledger entry documenting the refund, including
    the order ID, reason, reversal transaction details, and timestamp.

    Args:
        order_id: The order that was refunded.
        reason: The reason for the refund.
        reversal: The reversal transaction details from the payment
            gateway.

    Returns:
        A dict with the ledger entry ID and recording status.
    """
    entry_id = f"LED_{order_id}"
    return {"entry_id": entry_id, "status": "recorded"}
'''))

    w('fraud/checker.py', gen_filler(rng, '''"""Fraud checker - detects suspicious orders and refund patterns.

Provides functions for flagging potentially fraudulent transactions
based on velocity, amount, and pattern analysis.
"""
from typing import Any, Dict, List, Optional


def flag_suspicious_refund(order_id: str) -> bool:
    """Flag a refund request as potentially suspicious.

    Analyzes the refund request against fraud indicators including
    repeat refund patterns, high-value reversals, and velocity checks.

    Note:
        This function is called by run_fraud_scan in refunds/manager.py
        and deep_fraud_scan in refunds/validator.py, but is NOT part
        of the standard refund processing flow.

    Args:
        order_id: The order to check for fraud indicators.

    Returns:
        True if the refund is flagged as suspicious, False otherwise.
    """
    return False
'''))

    # ── Auto-generated modules ────────────────────────────────────────

    for pkg, (pkg_desc, modules) in PACKAGES.items():
        for mod in modules:
            rel = f"{pkg}/{mod}"
            if rel in EXPLICIT_MODULES:
                continue
            extra = DECOY_IMPORTS.get(rel, "")
            content = gen_auto_module(pkg, mod, pkg_desc, rng, extra)
            w(f"{rel}.py", content)

    # ── Package __init__.py files ─────────────────────────────────────

    for pkg, (pkg_desc, _) in PACKAGES.items():
        w(f"{pkg}/__init__.py", f'"""{pkg} package - {pkg_desc}"""\n')

    # ── Top-level entry point ─────────────────────────────────────────

    w('main.py', '''"""Order Processing System - application entry point.

This module starts the order-processing system. The primary HTTP
entry point for refund requests is the post_refund handler in
api.routes.
"""
from api.routes import post_refund


def main():
    """Run the application."""
    print("Order Processing System v2.1.0")
    print("Endpoints: POST /api/v1/refunds")


if __name__ == "__main__":
    main()
''')

    # ── check.py ──────────────────────────────────────────────────────

    w('check.py', '''import sys, os

base = os.path.dirname(os.path.abspath(__file__))
p = os.path.join(base, "ANSWER.md")
try:
    with open(p) as f:
        c = f.read()
except FileNotFoundError:
    print("FAIL: ANSWER.md not found")
    sys.exit(1)

chain = [
    ("post_refund", "api/routes"),
    ("initiate_refund", "orders/service"),
    ("process_refund", "refunds/manager"),
    ("check_eligibility", "refunds/validator"),
    ("reverse_charge", "payments/gateway"),
    ("restock_items", "inventory/restock"),
    ("send_refund_confirmation", "notifications/emailer"),
    ("record_refund", "persistence/ledger"),
]

# Check each function name appears in order
pos = 0
for func, mod in chain:
    idx = c.find(func, pos)
    if idx == -1:
        print(f"FAIL: missing or out-of-order: {mod}::{func}")
        sys.exit(1)
    pos = idx + len(func)

# Verify module references exist somewhere in the answer
for func, mod in chain:
    mod_short = mod.split("/")[-1]
    if mod not in c and mod_short not in c:
        print(f"FAIL: missing module reference: {mod}")
        sys.exit(1)

print("PASS")
''')

    # ── hidden_check.py ───────────────────────────────────────────────

    w('hidden_check.py', '''import sys, os

base = os.path.dirname(os.path.abspath(__file__))
p = os.path.join(base, "ANSWER.md")
try:
    with open(p) as f:
        c = f.read()
except FileNotFoundError:
    print("FAIL: ANSWER.md not found")
    sys.exit(1)

# (a) Decoy function must NOT appear in the answer
if "flag_suspicious_refund" in c:
    print("FAIL: decoy 'flag_suspicious_refund' must not be in the refund chain")
    sys.exit(1)

# (b) Final persistence function must appear
if "record_refund" not in c:
    print("FAIL: missing final persistence function 'record_refund'")
    sys.exit(1)

# (c) Exact count of chain steps = 8
step_lines = [l for l in c.strip().split("\\n") if "::" in l]
if len(step_lines) != 8:
    print(f"FAIL: expected exactly 8 chain steps, found {len(step_lines)}")
    sys.exit(1)

print("PASS")
''')


if __name__ == '__main__':
    main()
