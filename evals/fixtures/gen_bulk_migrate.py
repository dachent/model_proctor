"""Fixture: bulk migration - replace deprecated logging and utils across ~30 modules.

Generates a ~30-module Python project (inventory app with models, services,
repos, cli) where every module uses two deprecated patterns:
  (a) logger = get_logger(__name__)  from deprecated_logging.py
  (b) from old_utils import slugify, clamp

The task is to migrate every module to Logger.get(__name__) from logging_ext.py
and to the utils/ package (utils.text.slugify, utils.num.clamp).
"""
import os
import sys
import random

# ── App module definitions: (rel_path, base_content) ─────────────────
# Each module uses both deprecated patterns and is padded with filler.

INFRA = {
    "deprecated_logging.py": '''"""Deprecated logging shim.

This module provides the legacy get_logger function. It will be removed
in a future version. Migrate to logging_ext.Logger.get(name).
"""
import logging


def get_logger(name: str) -> logging.Logger:
    """Get a logger by name.

    Deprecated: use Logger.get(name) from logging_ext instead.

    Args:
        name: The logger name, typically __name__.

    Returns:
        A logging.Logger instance.
    """
    return logging.getLogger(name)
''',

    "logging_ext.py": '''"""Enhanced logging utilities.

Replacement for the deprecated_logging module. Provides a Logger factory
class with caching for efficient logger retrieval.
"""
import logging


class Logger:
    """Logger factory with instance caching.

    Use Logger.get(name) to obtain a logger instance.
    """

    _loggers: dict = {}

    @classmethod
    def get(cls, name: str) -> logging.Logger:
        """Get or create a logger by name.

        Args:
            name: The logger name, typically __name__.

        Returns:
            A logging.Logger instance.
        """
        if name not in cls._loggers:
            cls._loggers[name] = logging.getLogger(name)
        return cls._loggers[name]
''',

    "old_utils.py": '''"""Deprecated utility functions.

This module provides slugify and clamp. It will be removed in a future
version. Migrate to utils.text.slugify and utils.num.clamp.
"""
import re


def slugify(text: str) -> str:
    """Convert text to a URL-friendly slug.

    Args:
        text: The text to slugify.

    Returns:
        A lowercase slug with words separated by hyphens.
    """
    text = text.lower().strip()
    text = re.sub(r'[^\\w\\s-]', '', text)
    text = re.sub(r'[\\s_-]+', '-', text)
    text = text.strip('-')
    return text


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value between min and max.

    Args:
        value: The value to clamp.
        min_val: The minimum allowed value.
        max_val: The maximum allowed value.

    Returns:
        The clamped value.
    """
    if value < min_val:
        return min_val
    if value > max_val:
        return max_val
    return value
''',

    "utils/__init__.py": '"""Utils package - text and number utilities."""\n',

    "utils/text.py": '''"""Text utilities for the inventory application."""
import re


def slugify(text: str) -> str:
    """Convert text to a URL-friendly slug.

    Args:
        text: The text to slugify.

    Returns:
        A lowercase slug with words separated by hyphens.
    """
    text = text.lower().strip()
    text = re.sub(r'[^\\w\\s-]', '', text)
    text = re.sub(r'[\\s_-]+', '-', text)
    text = text.strip('-')
    return text
''',

    "utils/num.py": '''"""Number utilities for the inventory application."""


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value between min and max.

    Args:
        value: The value to clamp.
        min_val: The minimum allowed value.
        max_val: The maximum allowed value.

    Returns:
        The clamped value.
    """
    if value < min_val:
        return min_val
    if value > max_val:
        return max_val
    return value
''',
}

# Standard header every app module starts with
DEP_HEADER = (
    'from deprecated_logging import get_logger\n'
    'from old_utils import slugify, clamp\n'
    'from typing import Any, Dict, List, Optional\n\n'
    'logger = get_logger(__name__)\n'
)

APP_MODULES = {
    "models/product.py": '''"""Product model - represents a product in the inventory."""


class Product:
    """A product in the inventory system."""

    def __init__(self, name: str, price: float, stock: int):
        self.name = name
        self.slug = slugify(name)
        self.price = clamp(price, 0.0, 999999.0)
        self.stock = clamp(stock, 0, 1000000)
        logger.debug("Created product: %s", self.slug)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "slug": self.slug,
                "price": self.price, "stock": self.stock}
''',

    "models/order.py": '''"""Order model - represents a customer order."""


class Order:
    """A customer order."""

    def __init__(self, order_id: str, customer_name: str,
                 total: float):
        self.order_id = order_id
        self.order_ref = slugify(order_id)
        self.customer_name = customer_name
        self.total = clamp(total, 0.0, 999999.0)
        logger.debug("Created order: %s", self.order_ref)

    def to_dict(self) -> Dict[str, Any]:
        return {"order_id": self.order_id, "order_ref": self.order_ref,
                "customer_name": self.customer_name, "total": self.total}
''',

    "models/customer.py": '''"""Customer model - represents a customer."""


class Customer:
    """A customer in the system."""

    def __init__(self, name: str, email: str, credit_limit: float):
        self.name = name
        self.code = slugify(name)
        self.email = email
        self.credit_limit = clamp(credit_limit, 0.0, 1000000.0)
        logger.debug("Created customer: %s", self.code)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "code": self.code,
                "email": self.email, "credit_limit": self.credit_limit}
''',

    "models/inventory_item.py": '''"""Inventory item model."""


class InventoryItem:
    """An item in the inventory."""

    def __init__(self, sku: str, name: str, quantity: int):
        self.sku = slugify(sku)
        self.name = name
        self.quantity = clamp(quantity, 0, 100000)
        logger.debug("Created inventory item: %s", self.sku)

    def to_dict(self) -> Dict[str, Any]:
        return {"sku": self.sku, "name": self.name,
                "quantity": self.quantity}
''',

    "models/invoice.py": '''"""Invoice model."""


class Invoice:
    """An invoice for a customer."""

    def __init__(self, invoice_number: str, amount: float):
        self.invoice_number = slugify(invoice_number)
        self.amount = clamp(amount, 0.0, 999999.0)
        logger.debug("Created invoice: %s", self.invoice_number)

    def to_dict(self) -> Dict[str, Any]:
        return {"invoice_number": self.invoice_number,
                "amount": self.amount}
''',

    "models/refund.py": '''"""Refund model."""


class Refund:
    """A refund request."""

    def __init__(self, refund_id: str, amount: float, reason: str):
        self.refund_id = slugify(refund_id)
        self.amount = clamp(amount, 0.0, 999999.0)
        self.reason = reason
        logger.debug("Created refund: %s", self.refund_id)

    def to_dict(self) -> Dict[str, Any]:
        return {"refund_id": self.refund_id, "amount": self.amount,
                "reason": self.reason}
''',

    "services/order_service.py": '''"""Order service - business logic for orders."""


def create_order(customer_name: str, total: float) -> Dict[str, Any]:
    """Create a new order."""
    ref = slugify(customer_name)
    safe_total = clamp(total, 0.0, 999999.0)
    logger.debug("Creating order for: %s", ref)
    return {"order_ref": ref, "total": safe_total, "status": "created"}


def get_order(order_id: str) -> Dict[str, Any]:
    """Get an order by ID."""
    safe_id = slugify(order_id)
    logger.debug("Looking up order: %s", safe_id)
    return {"order_id": safe_id, "status": "found"}
''',

    "services/inventory_service.py": '''"""Inventory service - business logic for inventory."""


def update_stock(sku: str, delta: int) -> Dict[str, Any]:
    """Update stock for a SKU."""
    safe_sku = slugify(sku)
    safe_delta = clamp(delta, -10000, 10000)
    logger.debug("Updating stock for: %s", safe_sku)
    return {"sku": safe_sku, "delta": safe_delta, "status": "updated"}


def check_stock(sku: str) -> Dict[str, Any]:
    """Check stock for a SKU."""
    safe_sku = slugify(sku)
    logger.debug("Checking stock for: %s", safe_sku)
    return {"sku": safe_sku, "quantity": 0}
''',

    "services/refund_service.py": '''"""Refund service - business logic for refunds."""


def request_refund(order_id: str, amount: float,
                   reason: str) -> Dict[str, Any]:
    """Request a refund for an order."""
    safe_id = slugify(order_id)
    safe_amount = clamp(amount, 0.0, 999999.0)
    logger.debug("Refund request for: %s", safe_id)
    return {"refund_id": safe_id, "amount": safe_amount,
            "reason": reason, "status": "pending"}


def get_refund(refund_id: str) -> Dict[str, Any]:
    """Get a refund by ID."""
    safe_id = slugify(refund_id)
    logger.debug("Getting refund: %s", safe_id)
    return {"refund_id": safe_id, "status": "found"}
''',

    "services/customer_service.py": '''"""Customer service - business logic for customers."""


def register_customer(name: str, email: str,
                      credit_limit: float) -> Dict[str, Any]:
    """Register a new customer."""
    code = slugify(name)
    safe_credit = clamp(credit_limit, 0.0, 1000000.0)
    logger.debug("Registering customer: %s", code)
    return {"code": code, "email": email,
            "credit_limit": safe_credit, "status": "registered"}


def get_customer(code: str) -> Dict[str, Any]:
    """Get a customer by code."""
    safe_code = slugify(code)
    logger.debug("Getting customer: %s", safe_code)
    return {"code": safe_code, "status": "found"}
''',

    "services/invoice_service.py": '''"""Invoice service - business logic for invoices."""


def create_invoice(customer_code: str,
                   amount: float) -> Dict[str, Any]:
    """Create a new invoice."""
    safe_code = slugify(customer_code)
    safe_amount = clamp(amount, 0.0, 999999.0)
    logger.debug("Creating invoice for: %s", safe_code)
    return {"invoice_number": f"INV_{safe_code}",
            "amount": safe_amount, "status": "created"}


def send_invoice(invoice_number: str) -> Dict[str, Any]:
    """Send an invoice."""
    safe_num = slugify(invoice_number)
    logger.debug("Sending invoice: %s", safe_num)
    return {"invoice_number": safe_num, "status": "sent"}
''',

    "services/notification_service.py": '''"""Notification service - send alerts and updates."""


def send_alert(recipient: str, subject: str) -> Dict[str, Any]:
    """Send an alert notification."""
    safe_recipient = slugify(recipient)
    safe_subject = slugify(subject)
    priority = clamp(1, 1, 5)
    logger.debug("Sending alert to: %s", safe_recipient)
    return {"recipient": safe_recipient, "subject": safe_subject,
            "priority": priority, "status": "sent"}


def send_update(recipient: str, message: str) -> Dict[str, Any]:
    """Send an update notification."""
    safe_recipient = slugify(recipient)
    logger.debug("Sending update to: %s", safe_recipient)
    return {"recipient": safe_recipient, "message": message,
            "status": "sent"}
''',

    "repos/order_repo.py": '''"""Order repository - data access for orders."""


class OrderRepo:
    """Repository for orders."""

    def __init__(self):
        self._store: Dict[str, Any] = {}

    def save(self, order: Dict[str, Any]) -> None:
        key = slugify(order.get("order_ref", "unknown"))
        self._store[key] = order
        logger.debug("Saved order: %s", key)

    def get(self, order_id: str) -> Optional[Dict[str, Any]]:
        key = slugify(order_id)
        logger.debug("Getting order: %s", key)
        return self._store.get(key)
''',

    "repos/inventory_repo.py": '''"""Inventory repository - data access for inventory."""


class InventoryRepo:
    """Repository for inventory items."""

    def __init__(self):
        self._store: Dict[str, Any] = {}

    def update(self, sku: str, qty: int) -> None:
        key = slugify(sku)
        safe_qty = clamp(qty, 0, 100000)
        self._store[key] = safe_qty
        logger.debug("Updated inventory: %s = %s", key, safe_qty)

    def get(self, sku: str) -> Optional[int]:
        key = slugify(sku)
        logger.debug("Getting inventory: %s", key)
        return self._store.get(key)
''',

    "repos/customer_repo.py": '''"""Customer repository - data access for customers."""


class CustomerRepo:
    """Repository for customers."""

    def __init__(self):
        self._store: Dict[str, Any] = {}

    def save(self, customer: Dict[str, Any]) -> None:
        key = slugify(customer.get("code", "unknown"))
        self._store[key] = customer
        logger.debug("Saved customer: %s", key)

    def get(self, code: str) -> Optional[Dict[str, Any]]:
        key = slugify(code)
        logger.debug("Getting customer: %s", key)
        return self._store.get(key)
''',

    "repos/invoice_repo.py": '''"""Invoice repository - data access for invoices."""


class InvoiceRepo:
    """Repository for invoices."""

    def __init__(self):
        self._store: Dict[str, Any] = {}

    def save(self, invoice: Dict[str, Any]) -> None:
        key = slugify(invoice.get("invoice_number", "unknown"))
        safe_amount = clamp(invoice.get("amount", 0.0), 0.0, 999999.0)
        invoice["amount"] = safe_amount
        self._store[key] = invoice
        logger.debug("Saved invoice: %s", key)

    def get(self, invoice_number: str) -> Optional[Dict[str, Any]]:
        key = slugify(invoice_number)
        logger.debug("Getting invoice: %s", key)
        return self._store.get(key)
''',

    "cli/commands.py": '''"""CLI command handlers."""


def handle_command(cmd: str, args: List[str]) -> Dict[str, Any]:
    """Handle a CLI command."""
    safe_cmd = slugify(cmd)
    logger.debug("Handling command: %s", safe_cmd)
    return {"command": safe_cmd, "status": "ok"}
''',

    "cli/parser.py": '''"""CLI argument parser."""


def parse_args(argv: List[str]) -> Dict[str, Any]:
    """Parse command-line arguments."""
    logger.debug("Parsing args: %s", argv)
    result: Dict[str, Any] = {}
    for arg in argv:
        key = slugify(arg)
        result[key] = True
    return result
''',

    "app.py": '''"""Main application module."""


def run() -> Dict[str, Any]:
    """Run the application."""
    logger.debug("Starting application")
    name = slugify("Inventory App")
    timeout = clamp(30, 1, 300)
    return {"app": name, "timeout": timeout, "status": "running"}
''',

    "config.py": '''"""Application configuration."""


class Config:
    """Application configuration."""

    def __init__(self):
        self.app_name = slugify("Inventory App")
        self.max_retries = clamp(5, 1, 20)
        self.timeout = clamp(30, 1, 300)
        logger.debug("Config initialized")


def get_config() -> Config:
    """Get the application configuration."""
    return Config()
''',

    "helpers.py": '''"""Utility helper functions."""


def format_name(name: str) -> str:
    """Format a name as a slug."""
    logger.debug("Formatting name: %s", name)
    return slugify(name)


def clamp_value(value: float, min_val: float,
                max_val: float) -> float:
    """Clamp a value between min and max."""
    logger.debug("Clamping value: %s", value)
    return clamp(value, min_val, max_val)
''',

    "validators.py": '''"""Validation functions."""


def validate_price(price: float) -> bool:
    """Validate a price value."""
    safe_price = clamp(price, 0.0, 999999.0)
    logger.debug("Validating price: %s", safe_price)
    return safe_price == price and price >= 0


def validate_quantity(qty: int) -> bool:
    """Validate a quantity value."""
    safe_qty = clamp(qty, 0, 100000)
    logger.debug("Validating quantity: %s", safe_qty)
    return safe_qty == qty and qty >= 0
''',
}

TEST_MODULES = {
    "tests/test_models.py": '''"""Tests for model classes."""
import unittest
from models.product import Product
from models.order import Order
from models.customer import Customer


class TestProduct(unittest.TestCase):
    def test_creation(self):
        p = Product("Widget Pro", 19.99, 100)
        self.assertEqual(p.name, "Widget Pro")
        self.assertEqual(p.slug, "widget-pro")
        self.assertEqual(p.price, 19.99)
        self.assertEqual(p.stock, 100)

    def test_clamp_negative_price(self):
        p = Product("Test", -5.0, 10)
        self.assertEqual(p.price, 0.0)

    def test_clamp_large_stock(self):
        p = Product("Test", 10.0, 2000000)
        self.assertEqual(p.stock, 1000000)

    def test_to_dict(self):
        p = Product("Test Item", 5.0, 3)
        d = p.to_dict()
        self.assertEqual(d["name"], "Test Item")
        self.assertEqual(d["slug"], "test-item")


class TestOrder(unittest.TestCase):
    def test_creation(self):
        o = Order("ORD-001", "Alice", 99.99)
        self.assertEqual(o.order_id, "ORD-001")
        self.assertEqual(o.order_ref, "ord-001")
        self.assertEqual(o.total, 99.99)

    def test_clamp_total(self):
        o = Order("ORD-002", "Bob", -10.0)
        self.assertEqual(o.total, 0.0)


class TestCustomer(unittest.TestCase):
    def test_creation(self):
        c = Customer("Alice Smith", "alice@example.com", 5000.0)
        self.assertEqual(c.name, "Alice Smith")
        self.assertEqual(c.code, "alice-smith")
        self.assertEqual(c.email, "alice@example.com")
        self.assertEqual(c.credit_limit, 5000.0)

    def test_clamp_credit(self):
        c = Customer("Bob", "bob@example.com", -100.0)
        self.assertEqual(c.credit_limit, 0.0)


if __name__ == "__main__":
    unittest.main()
''',

    "tests/test_services.py": '''"""Tests for service functions."""
import unittest
from services.order_service import create_order, get_order
from services.refund_service import request_refund, get_refund
from services.customer_service import register_customer, get_customer


class TestOrderService(unittest.TestCase):
    def test_create_order(self):
        result = create_order("Alice", 99.99)
        self.assertEqual(result["order_ref"], "alice")
        self.assertEqual(result["total"], 99.99)
        self.assertEqual(result["status"], "created")

    def test_create_order_clamp(self):
        result = create_order("Bob", -10.0)
        self.assertEqual(result["total"], 0.0)

    def test_get_order(self):
        result = get_order("ORD-001")
        self.assertEqual(result["order_id"], "ord-001")


class TestRefundService(unittest.TestCase):
    def test_request_refund(self):
        result = request_refund("ORD-001", 50.0, "defect")
        self.assertEqual(result["refund_id"], "ord-001")
        self.assertEqual(result["amount"], 50.0)
        self.assertEqual(result["status"], "pending")


class TestCustomerService(unittest.TestCase):
    def test_register_customer(self):
        result = register_customer("Alice", "alice@example.com", 5000.0)
        self.assertEqual(result["code"], "alice")
        self.assertEqual(result["credit_limit"], 5000.0)

    def test_get_customer(self):
        result = get_customer("Alice")
        self.assertEqual(result["code"], "alice")


if __name__ == "__main__":
    unittest.main()
''',

    "tests/test_repos.py": '''"""Tests for repository classes."""
import unittest
from repos.order_repo import OrderRepo
from repos.customer_repo import CustomerRepo


class TestOrderRepo(unittest.TestCase):
    def test_save_and_get(self):
        repo = OrderRepo()
        order = {"order_ref": "test-order", "total": 99.99}
        repo.save(order)
        result = repo.get("test-order")
        self.assertIsNotNone(result)
        self.assertEqual(result["total"], 99.99)

    def test_get_missing(self):
        repo = OrderRepo()
        result = repo.get("nonexistent")
        self.assertIsNone(result)


class TestCustomerRepo(unittest.TestCase):
    def test_save_and_get(self):
        repo = CustomerRepo()
        customer = {"code": "alice", "email": "alice@example.com"}
        repo.save(customer)
        result = repo.get("alice")
        self.assertIsNotNone(result)
        self.assertEqual(result["email"], "alice@example.com")


if __name__ == "__main__":
    unittest.main()
''',

    "tests/test_helpers.py": '''"""Tests for helper and validator functions."""
import unittest
from helpers import format_name, clamp_value
from validators import validate_price, validate_quantity


class TestHelpers(unittest.TestCase):
    def test_format_name(self):
        self.assertEqual(format_name("Hello World"), "hello-world")

    def test_clamp_value(self):
        self.assertEqual(clamp_value(5, 0, 10), 5)
        self.assertEqual(clamp_value(-5, 0, 10), 0)
        self.assertEqual(clamp_value(15, 0, 10), 10)


class TestValidators(unittest.TestCase):
    def test_validate_price_valid(self):
        self.assertTrue(validate_price(99.99))

    def test_validate_price_negative(self):
        self.assertFalse(validate_price(-10.0))

    def test_validate_quantity_valid(self):
        self.assertTrue(validate_quantity(50))

    def test_validate_quantity_negative(self):
        self.assertFalse(validate_quantity(-5))


if __name__ == "__main__":
    unittest.main()
''',
}

INIT_FILES = {
    "models/__init__.py": '"""Models package."""\n',
    "services/__init__.py": '"""Services package."""\n',
    "repos/__init__.py": '"""Repos package."""\n',
    "cli/__init__.py": '"""CLI package."""\n',
    "tests/__init__.py": '"""Tests package."""\n',
}

FILLER_NAMES = ["text", "value", "price", "quantity", "amount", "rate",
                "score", "level", "tag", "label"]


def gen_bulk_filler(rng, content, target_min=3500):
    """Pad content with filler functions that use all three deprecated patterns."""
    counter = 0
    while len(content) < target_min:
        counter += 1
        name = rng.choice(FILLER_NAMES)
        fn = f"_process_{name}_{counter}"
        content += f'\ndef {fn}(text: str, value: float) -> str:\n'
        content += f'    """Process {name} data for internal use."""\n'
        content += f'    logger.debug("Processing {name}")\n'
        content += f'    safe_text = slugify(text)\n'
        content += f'    safe_value = clamp(value, 0.0, 100.0)\n'
        content += f'    return f"{{safe_text}}_{{safe_value}}"\n'
    return content


def main():
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    rng = random.Random(99)

    def w(rel, content):
        full = os.path.join(d, *rel.split('/'))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w', encoding='utf-8') as f:
            f.write(content)

    # ── Infrastructure files ──────────────────────────────────────────
    for rel, content in INFRA.items():
        w(rel, content)

    # ── Package __init__.py files ─────────────────────────────────────
    for rel, content in INIT_FILES.items():
        w(rel, content)

    # ── App modules (with deprecated patterns + filler) ───────────────
    for rel, base_content in APP_MODULES.items():
        full_content = DEP_HEADER + "\n" + base_content
        full_content = gen_bulk_filler(rng, full_content, target_min=3500)
        w(rel, full_content)

    # ── Test files ────────────────────────────────────────────────────
    for rel, content in TEST_MODULES.items():
        w(rel, content)

    # ── check.py ──────────────────────────────────────────────────────
    w('check.py', '''import sys, os, glob, subprocess

base = os.path.dirname(os.path.abspath(__file__))

# 1. Run the test suite
r = subprocess.run(
    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
    cwd=base, capture_output=True, text=True)
if r.returncode != 0:
    print("FAIL: test suite failed")
    print(r.stderr[-500:])
    sys.exit(1)

# 2. Grep all .py files for deprecated patterns
for py in glob.glob(os.path.join(base, "**", "*.py"), recursive=True):
    name = os.path.relpath(py, base).replace(os.sep, "/")
    if name in ("check.py", "hidden_check.py",
                "deprecated_logging.py", "old_utils.py"):
        continue
    with open(py) as f:
        c = f.read()
    if "get_logger(" in c:
        print(f"FAIL: {name} still uses get_logger()")
        sys.exit(1)
    if "old_utils" in c:
        print(f"FAIL: {name} still imports from old_utils")
        sys.exit(1)

print("PASS")
''')

    # ── hidden_check.py ───────────────────────────────────────────────
    w('hidden_check.py', '''import sys, os

base = os.path.dirname(os.path.abspath(__file__))

# Check that old_utils.py and deprecated_logging.py are deleted or stubbed
for old_file in ["old_utils.py", "deprecated_logging.py"]:
    p = os.path.join(base, old_file)
    if not os.path.exists(p):
        continue  # Deleted - OK
    with open(p) as f:
        c = f.read()
    if len(c) > 200:
        print(f"FAIL: {old_file} still has substantial content "
              f"({len(c)} bytes)")
        sys.exit(1)
    if "def slugify" in c or "def clamp" in c or "def get_logger" in c:
        print(f"FAIL: {old_file} still contains function definitions")
        sys.exit(1)

# Check that utils/text.py and utils/num.py have real implementations
for path, func in [("utils/text.py", "def slugify"),
                   ("utils/num.py", "def clamp")]:
    p = os.path.join(base, *path.split("/"))
    if not os.path.exists(p):
        print(f"FAIL: {path} not found")
        sys.exit(1)
    with open(p) as f:
        c = f.read()
    if func not in c:
        print(f"FAIL: {path} missing {func}")
        sys.exit(1)

print("PASS")
''')


if __name__ == '__main__':
    main()
