"""
inventory_service.py - small inventory and order-reservation service (FICTIONAL fixture for the dev benchmark).

The module is intentionally self-contained (standard library only) so that the benchmark can import it and
run unit tests against a function rewritten by a model. It contains ONE deliberate bug (see the questions file).

Components:
  * Product / StockLevel / OrderLine dataclasses
  * Inventory: in-memory stock with reservations, safety stock and an audit trail
  * pricing helpers: bulk discount tiers, VAT, line and order totals
  * SupplierClient: restock requests with retries and exponential backoff (network is injected)
  * CSV import / export
  * a tiny request router that maps service errors to HTTP status codes
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger("inventory")

# --------------------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------------------

WAREHOUSE_ID = os.environ.get("INVENTORY_WAREHOUSE", "LIS-01")
VAT_RATE = Decimal(os.environ.get("INVENTORY_VAT", "0.23"))
DEFAULT_SAFETY_STOCK = 5
RESERVATION_TTL_SECONDS = 15 * 60

# (minimum quantity, discount percentage). Kept in ascending order for readability.
BULK_TIERS: List[Tuple[int, Decimal]] = [
    (10, Decimal("5")),
    (50, Decimal("8")),
    (100, Decimal("12")),
]

CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    """Rounds to cents using commercial rounding (half up)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------------

class InventoryError(Exception):
    """Base class for service errors."""


class UnknownSku(InventoryError):
    def __init__(self, sku: str):
        super().__init__(f"unknown SKU: {sku}")
        self.sku = sku


class InsufficientStock(InventoryError):
    def __init__(self, sku: str, requested: int, available: int):
        super().__init__(f"insufficient stock for {sku}: requested {requested}, available {available}")
        self.sku, self.requested, self.available = sku, requested, available


class ValidationError(InventoryError):
    pass


class SupplierUnavailable(InventoryError):
    pass


# --------------------------------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------------------------------

@dataclass
class Product:
    sku: str
    name: str
    unit_price: Decimal
    safety_stock: int = DEFAULT_SAFETY_STOCK
    supplier: str = "default"

    def validate(self) -> None:
        if not self.sku or not self.sku.isalnum():
            raise ValidationError(f"invalid SKU {self.sku!r}: must be alphanumeric")
        if self.unit_price <= 0:
            raise ValidationError(f"price must be positive for {self.sku}")
        if self.safety_stock < 0:
            raise ValidationError(f"safety stock cannot be negative for {self.sku}")


@dataclass
class StockLevel:
    on_hand: int = 0
    reserved: int = 0

    @property
    def free(self) -> int:
        return self.on_hand - self.reserved


@dataclass
class Reservation:
    reservation_id: str
    sku: str
    quantity: int
    created_at: float
    order_ref: str = ""

    def expired(self, now: float) -> bool:
        return now - self.created_at > RESERVATION_TTL_SECONDS


@dataclass
class OrderLine:
    sku: str
    quantity: int
    unit_price: Decimal


@dataclass
class AuditEntry:
    ts: str
    action: str
    sku: str
    quantity: int
    detail: str = ""


# --------------------------------------------------------------------------------------------------
# Pricing
# --------------------------------------------------------------------------------------------------

def bulk_discount_pct(quantity: int) -> Decimal:
    """Returns the discount percentage for a line with `quantity` units.

    The HIGHEST tier whose minimum quantity is reached applies:
    10-49 units -> 5 %, 50-99 units -> 8 %, 100 or more -> 12 %, fewer than 10 -> 0 %.
    """
    if quantity < 0:
        raise ValidationError("quantity cannot be negative")
    for threshold, pct in BULK_TIERS:
        if quantity >= threshold:
            return pct
    return Decimal("0")


def line_total(line: OrderLine, include_vat: bool = True) -> Decimal:
    """Total for one order line, after the bulk discount and (optionally) VAT."""
    gross = line.unit_price * line.quantity
    discounted = gross * (Decimal("100") - bulk_discount_pct(line.quantity)) / Decimal("100")
    if include_vat:
        discounted = discounted * (Decimal("1") + VAT_RATE)
    return money(discounted)


def order_total(lines: Iterable[OrderLine], include_vat: bool = True) -> Decimal:
    return money(sum((line_total(l, include_vat) for l in lines), Decimal("0")))


# --------------------------------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------------------------------

class Inventory:
    def __init__(self, clock: Callable[[], float] = time.time):
        self.products: Dict[str, Product] = {}
        self.stock: Dict[str, StockLevel] = {}
        self.reservations: Dict[str, Reservation] = {}
        self.audit: List[AuditEntry] = []
        self._clock = clock
        self._next_id = 1

    # ---- helpers -------------------------------------------------------------------------------
    def _audit(self, action: str, sku: str, quantity: int, detail: str = "") -> None:
        ts = datetime.fromtimestamp(self._clock(), tz=timezone.utc).isoformat(timespec="seconds")
        self.audit.append(AuditEntry(ts, action, sku, quantity, detail))
        log.debug("%s %s %s %s", action, sku, quantity, detail)

    def _product(self, sku: str) -> Product:
        try:
            return self.products[sku]
        except KeyError:
            raise UnknownSku(sku) from None

    # ---- catalogue -----------------------------------------------------------------------------
    def add_product(self, product: Product, on_hand: int = 0) -> None:
        product.validate()
        if product.sku in self.products:
            raise ValidationError(f"duplicate SKU {product.sku}")
        self.products[product.sku] = product
        self.stock[product.sku] = StockLevel(on_hand=on_hand)
        self._audit("add_product", product.sku, on_hand)

    def receive(self, sku: str, quantity: int, note: str = "") -> int:
        """Goods received from a supplier. Returns the new on-hand quantity."""
        self._product(sku)
        if quantity <= 0:
            raise ValidationError("received quantity must be positive")
        self.stock[sku].on_hand += quantity
        self._audit("receive", sku, quantity, note)
        return self.stock[sku].on_hand

    def available(self, sku: str) -> int:
        """Units that can still be reserved: free stock minus the product's safety stock."""
        product = self._product(sku)
        self.expire_reservations()
        return max(self.stock[sku].free - product.safety_stock, 0)

    # ---- reservations --------------------------------------------------------------------------
    def reserve(self, sku: str, quantity: int, order_ref: str = "") -> str:
        """Reserves units for an order and returns the reservation id.

        Raises InsufficientStock when the request is larger than available(sku), i.e. when it would
        dip into the safety stock.
        """
        if quantity <= 0:
            raise ValidationError("reservation quantity must be positive")
        avail = self.available(sku)
        if quantity > avail:
            raise InsufficientStock(sku, quantity, avail)
        rid = f"R{self._next_id:06d}"
        self._next_id += 1
        self.reservations[rid] = Reservation(rid, sku, quantity, self._clock(), order_ref)
        self.stock[sku].reserved += quantity
        self._audit("reserve", sku, quantity, rid)
        return rid

    def release(self, reservation_id: str) -> None:
        res = self.reservations.pop(reservation_id, None)
        if res is None:
            raise ValidationError(f"unknown reservation {reservation_id}")
        self.stock[res.sku].reserved -= res.quantity
        self._audit("release", res.sku, res.quantity, reservation_id)

    def commit(self, reservation_id: str) -> None:
        """Ships a reservation: the units leave the warehouse."""
        res = self.reservations.pop(reservation_id, None)
        if res is None:
            raise ValidationError(f"unknown reservation {reservation_id}")
        level = self.stock[res.sku]
        level.reserved -= res.quantity
        level.on_hand -= res.quantity
        self._audit("commit", res.sku, res.quantity, reservation_id)

    def expire_reservations(self) -> int:
        """Releases reservations older than RESERVATION_TTL_SECONDS. Returns how many expired."""
        now = self._clock()
        expired = [rid for rid, r in self.reservations.items() if r.expired(now)]
        for rid in expired:
            self.release(rid)
            self._audit("expire", "-", 0, rid)
        return len(expired)

    # ---- reporting -----------------------------------------------------------------------------
    def low_stock(self) -> List[str]:
        """SKUs whose free stock is at or below the safety stock, sorted alphabetically."""
        return sorted(sku for sku, p in self.products.items() if self.stock[sku].free <= p.safety_stock)

    def valuation(self) -> Decimal:
        """Value of the on-hand stock at unit price, without VAT."""
        return money(sum((p.unit_price * self.stock[s].on_hand for s, p in self.products.items()), Decimal("0")))

    def snapshot(self) -> dict:
        return {
            "warehouse": WAREHOUSE_ID,
            "products": {s: asdict(p) | {"unit_price": str(p.unit_price)} for s, p in self.products.items()},
            "stock": {s: asdict(l) for s, l in self.stock.items()},
            "reservations": len(self.reservations),
        }


# --------------------------------------------------------------------------------------------------
# Supplier integration
# --------------------------------------------------------------------------------------------------

class SupplierClient:
    """Sends restock requests to a supplier API. The transport is injected so it can be faked in tests.

    `send` must return a dict like {"status": "accepted", "eta_days": 3} or raise ConnectionError.
    Retries use exponential backoff: base_delay * 2 ** attempt seconds (0.5 s, 1 s, 2 s, 4 s, ...).
    """

    def __init__(self, send: Callable[[dict], dict], max_retries: int = 4, base_delay: float = 0.5,
                 sleep: Callable[[float], None] = time.sleep):
        self.send = send
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.sleep = sleep

    def request_restock(self, sku: str, quantity: int) -> dict:
        payload = {"warehouse": WAREHOUSE_ID, "sku": sku, "quantity": quantity}
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                reply = self.send(payload)
                if reply.get("status") != "accepted":
                    raise SupplierUnavailable(f"supplier rejected restock of {sku}: {reply}")
                return reply
            except ConnectionError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    delay = self.base_delay * 2 ** attempt
                    log.warning("supplier unreachable (attempt %d), retrying in %.1fs", attempt + 1, delay)
                    self.sleep(delay)
        raise SupplierUnavailable(f"supplier unreachable after {self.max_retries + 1} attempts") from last_error


def plan_restock(inv: Inventory, target_days: int = 14, daily_demand: Optional[Dict[str, int]] = None) -> Dict[str, int]:
    """Quantity to order per low-stock SKU so that it lasts `target_days` at the given daily demand."""
    daily_demand = daily_demand or {}
    plan = {}
    for sku in inv.low_stock():
        demand = daily_demand.get(sku, 1)
        needed = demand * target_days + inv.products[sku].safety_stock - inv.stock[sku].free
        if needed > 0:
            plan[sku] = needed
    return plan


# --------------------------------------------------------------------------------------------------
# CSV import / export
# --------------------------------------------------------------------------------------------------

CSV_FIELDS = ["sku", "name", "unit_price", "safety_stock", "on_hand", "supplier"]


def import_csv(inv: Inventory, text: str) -> Tuple[int, List[str]]:
    """Imports products from CSV text. Returns (rows imported, list of error messages).

    Bad rows are skipped and reported; the import never stops half-way.
    """
    reader = csv.DictReader(io.StringIO(text))
    missing = [f for f in ("sku", "name", "unit_price") if f not in (reader.fieldnames or [])]
    if missing:
        raise ValidationError(f"CSV is missing columns: {', '.join(missing)}")
    imported, errors = 0, []
    for lineno, row in enumerate(reader, start=2):
        try:
            product = Product(
                sku=row["sku"].strip(),
                name=row["name"].strip(),
                unit_price=Decimal(row["unit_price"]),
                safety_stock=int(row.get("safety_stock") or DEFAULT_SAFETY_STOCK),
                supplier=(row.get("supplier") or "default").strip(),
            )
            inv.add_product(product, on_hand=int(row.get("on_hand") or 0))
            imported += 1
        except (InventoryError, ArithmeticError, ValueError) as exc:
            errors.append(f"line {lineno}: {exc}")
    return imported, errors


def export_csv(inv: Inventory) -> str:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=CSV_FIELDS)
    w.writeheader()
    for sku in sorted(inv.products):
        p = inv.products[sku]
        w.writerow({"sku": sku, "name": p.name, "unit_price": str(p.unit_price), "safety_stock": p.safety_stock,
                    "on_hand": inv.stock[sku].on_hand, "supplier": p.supplier})
    return out.getvalue()


# --------------------------------------------------------------------------------------------------
# Request router (framework-agnostic)
# --------------------------------------------------------------------------------------------------

ERROR_STATUS = {
    UnknownSku: 404,
    InsufficientStock: 409,
    ValidationError: 422,
    SupplierUnavailable: 503,
}


def handle_request(inv: Inventory, method: str, path: str, body: Optional[dict] = None) -> Tuple[int, dict]:
    """Maps a minimal REST-like API onto the Inventory. Returns (status code, JSON body).

    GET  /stock/<sku>            -> 200 {"sku", "available", "on_hand", "reserved"}
    POST /reserve                -> 201 {"reservation_id"}   body: {"sku", "quantity", "order_ref"?}
    POST /release/<reservation>  -> 200 {}
    GET  /low-stock              -> 200 {"skus": [...]}
    Errors use ERROR_STATUS; anything else is a 500.
    """
    body = body or {}
    try:
        parts = [p for p in path.split("/") if p]
        if method == "GET" and len(parts) == 2 and parts[0] == "stock":
            sku = parts[1]
            level = inv.stock.get(sku)
            avail = inv.available(sku)
            return 200, {"sku": sku, "available": avail, "on_hand": level.on_hand, "reserved": level.reserved}
        if method == "POST" and parts == ["reserve"]:
            qty = body.get("quantity")
            if not isinstance(qty, int):
                raise ValidationError("quantity must be an integer")
            rid = inv.reserve(str(body.get("sku", "")), qty, str(body.get("order_ref", "")))
            return 201, {"reservation_id": rid}
        if method == "POST" and len(parts) == 2 and parts[0] == "release":
            inv.release(parts[1])
            return 200, {}
        if method == "GET" and parts == ["low-stock"]:
            return 200, {"skus": inv.low_stock()}
        return 404, {"error": f"no route for {method} {path}"}
    except InventoryError as exc:
        for cls, status in ERROR_STATUS.items():
            if isinstance(exc, cls):
                return status, {"error": str(exc)}
        return 500, {"error": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("unexpected error")
        return 500, {"error": f"internal error: {exc.__class__.__name__}"}


# --------------------------------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------------------------------

DEMO_CSV = """sku,name,unit_price,safety_stock,on_hand,supplier
BOLT8,Hex bolt M8 x 40,0.18,200,1500,FixPro
NUT8,Hex nut M8,0.06,300,2600,FixPro
WASH8,Flat washer M8,0.03,300,250,FixPro
HINGE2,Steel hinge 75 mm,2.40,10,48,DoorCo
LOCK5,Mortise lock 5-lever,18.90,4,6,DoorCo
"""


def demo() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    inv = Inventory()
    n, errors = import_csv(inv, DEMO_CSV)
    print(f"imported {n} products into {WAREHOUSE_ID}; errors: {errors}")
    rid = inv.reserve("HINGE2", 20, "ORD-1001")
    print("reserved", rid, "available now", inv.available("HINGE2"))
    print("low stock:", inv.low_stock())
    lines = [OrderLine("BOLT8", 120, Decimal("0.18")), OrderLine("HINGE2", 20, Decimal("2.40"))]
    print("order total (VAT incl.):", order_total(lines))
    print(json.dumps(handle_request(inv, "GET", "/stock/LOCK5")[1]))
    print("restock plan:", plan_restock(inv, daily_demand={"WASH8": 40, "LOCK5": 1}))


if __name__ == "__main__":
    demo()
