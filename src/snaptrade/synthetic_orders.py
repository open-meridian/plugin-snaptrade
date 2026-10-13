"""Synthetic SnapTrade's trading: a simulated brokerage behind SnapTrade's
trading API, for synthetic mode, tests and `make e2e` (contract v22).

SIMULATED ONLY. Nothing here reaches SnapTrade, a brokerage, a paper account
or any trading API: an order "placed" here is a line in this module's state,
and every answer is shaped like SnapTrade's own (its order record, its
error body, its activity), invented, every value of it. The live paper run
against SnapTrade is the product owner's alone, manual, and never this.

What it holds survives the plugin's restart, as a brokerage's orders do: the
state is a JSON file in the plugin's storage (`state`), written whole on
every change; with none given (a test) it lives in the process.

A connection is read-only until trading is enabled on it (step 5's Q10):
`enable_trading` stands in for the person reconnecting it through the
Connection Portal with trading access, after which SnapTrade lists the
connection's `type` as `trade`.

How the simulated brokerage works an order, each read of the account's
orders taking it one step on, so a test and the e2e see every state:

- placed: `ACCEPTED`, its brokerage order ID `SYN-ORD-<n>`;
- a quantity of at most 100: on the next read `PARTIAL`, 40% filled (to a
  whole unit), then `EXECUTED`, every unit filled, at the limit price or
  else the instrument's synthetic price; more than 100 stays `ACCEPTED`,
  working, until cancelled;
- cancelled: `CANCELED`, what was filled kept.

And by the ticker, so each of SnapTrade's answers has an order to show it:

- `SYNREJ`: refused when placed, HTTP 400, SnapTrade's code and words;
- `SYNLOST`: placed, but the answer never arrives (the call fails with no
  status); the order is there when the account's orders are read;
- `SYNODD`: placed with a status SnapTrade's mapping does not know
  (`HELD_FOR_REVIEW`), then `ACCEPTED` on the next read;
- `SYNBUST`: executed in full, then on a later read its filled quantity
  back to 0, the brokerage having busted the fill (`CANCELED`);
- `SYNFEE`: cancelled with nothing filled, the brokerage charges a cancel fee,
  a `FEE` activity whose `external_reference_id` is the order's brokerage ID.

Each fill is also an activity of the account, as a brokerage's activity feed
states it: a `BUY` or `SELL` of the units filled, at the order's price, its
`external_reference_id` the order's brokerage ID, and the settlement date
the simulated brokerage states, the next weekday after the trade date (as
meridian-core's simulated venue states one).

One order the brokerage took outside the platform is on the Alpaca margin
account from the start (`SYN-OUT-0001`, AAPL, bought and executed), for a
venue-originated order.
"""

from __future__ import annotations

import json
import os
import tempfile
import zlib
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

from .venue import Json, OrderAsked, VenueError, parse_exact

#: The ticker each scenario is keyed by (above).
REJECTED = "SYNREJ"
LOST = "SYNLOST"
ODD = "SYNODD"
BUSTED = "SYNBUST"
FEE_ON_CANCEL = "SYNFEE"

#: A status SnapTrade's mapping (trading.py) does not know.
ODD_STATUS = "HELD_FOR_REVIEW"

#: The largest order the simulated brokerage fills at once; one larger works.
FILLS_AT_ONCE = Decimal(100)

#: The synthetic price of each ticker, where an order states none.
PRICES = {"AAPL": Decimal("227.41"), "ZZTOP": Decimal("12.00")}
PRICE = Decimal("100.00")

#: Each synthetic brokerage's order types and times in force, as its
#: catalogue states them (trading.py): Alpaca's takes no good-till-date
#: order, so one is rejected `unsupported` before anything is sent.
OFFERED: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "ALPACA": (("Market", "Limit", "Stop", "StopLimit"), ("Day", "GTC", "IOC", "FOK")),
}
OFFERED_ELSEWHERE = (
    ("Market", "Limit", "Stop", "StopLimit"),
    ("Day", "GTC", "IOC", "FOK", "GTD"),
)

#: The order the brokerage took outside the platform (above).
OUTSIDE_ID = "SYN-OUT-0001"


def _outside(account_id: str, placed: str) -> Json:
    return {
        "brokerage_order_id": OUTSIDE_ID,
        "status": "EXECUTED",
        "universal_symbol": _symbol("AAPL"),
        "action": "BUY",
        "total_quantity": "3",
        "open_quantity": "0",
        "canceled_quantity": "0",
        "filled_quantity": "3",
        "execution_price": "226.10",
        "limit_price": None,
        "stop_price": None,
        "order_type": "Market",
        "time_in_force": "Day",
        "time_placed": placed,
        "time_updated": placed,
        "time_executed": placed,
        "account": account_id,
    }


def _symbol(ticker: str) -> Json:
    return {
        "id": f"00000000-0000-4000-8000-{zlib.crc32(ticker.encode()) % 10**12:012d}",
        "symbol": ticker,
        "raw_symbol": ticker,
        "currency": {"code": "USD"},
        "exchange": {"code": "NASDAQ", "mic_code": "XNAS", "timezone": "America/New_York"},
    }


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class SyntheticTrading:
    """The simulated brokerage's orders, by account, and the connections on
    which trading is enabled."""

    def __init__(self, now: Callable[[], datetime], state: Path | None = None) -> None:
        self._now = now
        self._file = state
        self._held: dict[str, Any] = {}
        self._load()

    # ── State ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        held: Any = None
        if self._file is not None and self._file.exists():
            try:
                held = parse_exact(self._file.read_text())
            except (OSError, ValueError):
                held = None
        self._held = held if isinstance(held, dict) else {}
        self._held.setdefault("trading", [])
        self._held.setdefault("orders", {})
        self._held.setdefault("activities", {})
        self._held.setdefault("next", 1)

    def _save(self) -> None:
        if self._file is None:
            return
        self._file.parent.mkdir(parents=True, exist_ok=True)
        made, written = tempfile.mkstemp(dir=self._file.parent, suffix=".tmp")
        try:
            with os.fdopen(made, "w") as file:
                json.dump(self._held, file, default=str, sort_keys=True)
            os.replace(written, self._file)
        except BaseException:
            Path(written).unlink(missing_ok=True)
            raise

    # ── Connections ─────────────────────────────────────────────────────

    def trading(self, connection_id: str) -> bool:
        return connection_id in self._held["trading"]

    def enable_trading(self, connection_id: str) -> None:
        if connection_id not in self._held["trading"]:
            self._held["trading"].append(connection_id)
            self._save()

    def offered(self, slug: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return OFFERED.get(slug, OFFERED_ELSEWHERE)

    # ── Orders ──────────────────────────────────────────────────────────

    def _orders(self, account_id: str, first: str = "") -> list[Json]:
        orders: dict[str, list[Json]] = self._held["orders"]
        if account_id not in orders and first:
            outside = _outside(account_id, first)
            orders[account_id] = [outside]
            # Its fill, as the account's activity states it.
            placed = date.fromisoformat(first[:10])
            settles = placed + timedelta(days=1)
            while settles.weekday() >= 5:
                settles += timedelta(days=1)
            self._held["activities"].setdefault(account_id, []).append(
                {
                    "id": "00000000-0000-4000-8000-e00000000000",
                    "type": "BUY",
                    "symbol": {"symbol": "AAPL"},
                    "units": "3",
                    "price": "226.10",
                    "amount": "-678.30",
                    "currency": {"code": "USD"},
                    "trade_date": f"{placed.isoformat()}T00:00:00.000Z",
                    "settlement_date": f"{settles.isoformat()}T00:00:00.000Z",
                    "external_reference_id": OUTSIDE_ID,
                }
            )
        return orders.setdefault(account_id, [])

    def place(self, account_id: str, asked: OrderAsked, outside_at: str) -> Json:
        if asked.symbol == REJECTED:
            raise VenueError(
                "placing an order",
                said={
                    "code": "1063",
                    "detail": "The brokerage rejected the order: account restricted",
                },
                status=400,
            )
        now = _stamp(self._now())
        number = int(self._held["next"])
        self._held["next"] = number + 1
        record: Json = {
            "brokerage_order_id": f"SYN-ORD-{number:04d}",
            "client_order_id": asked.client_order_id,
            "status": ODD_STATUS if asked.symbol == ODD else "ACCEPTED",
            "universal_symbol": _symbol(asked.symbol),
            "action": asked.action,
            "total_quantity": str(asked.units),
            "open_quantity": str(asked.units),
            "canceled_quantity": "0",
            "filled_quantity": "0",
            "execution_price": None,
            "limit_price": None if asked.price is None else str(asked.price),
            "stop_price": None if asked.stop is None else str(asked.stop),
            "order_type": asked.order_type,
            "time_in_force": asked.time_in_force,
            "time_placed": now,
            "time_updated": now,
            "time_executed": None,
            "account": account_id,
        }
        self._orders(account_id, outside_at).append(record)
        self._save()
        if asked.symbol == LOST:
            # The order is taken, and the answer is lost on the way back.
            raise VenueError("placing an order")
        answered: Json = parse_exact(json.dumps(record))
        return answered

    def orders(self, account_id: str, outside_at: str) -> list[Json]:
        """Each order, every read taking each one step on (above)."""
        held = self._orders(account_id, outside_at)
        for record in held:
            self._step(account_id, record)
        self._save()
        return [parse_exact(json.dumps(record)) for record in held]

    def cancel(self, account_id: str, brokerage_order_id: str) -> Json:
        for record in self._orders(account_id):
            if record["brokerage_order_id"] != brokerage_order_id:
                continue
            if record["status"] in ("EXECUTED", "CANCELED", "REJECTED"):
                raise VenueError(
                    "cancelling an order",
                    said={"code": "1070", "detail": "The order can no longer be cancelled"},
                    status=400,
                )
            filled = Decimal(record["filled_quantity"])
            record["status"] = "CANCELED"
            record["canceled_quantity"] = str(Decimal(record["total_quantity"]) - filled)
            record["open_quantity"] = "0"
            record["time_updated"] = _stamp(self._now())
            if record["universal_symbol"]["symbol"] == FEE_ON_CANCEL and filled == 0:
                self._fee(account_id, record)
            self._save()
            return {"brokerage_order_id": brokerage_order_id, "status": "CANCELED"}
        raise VenueError(
            "cancelling an order", said={"code": "1069", "detail": "No such order"}, status=404
        )

    def activities(self, account_id: str) -> list[Json]:
        listed: list[Json] = self._held["activities"].get(account_id, [])
        return [parse_exact(json.dumps(activity)) for activity in listed]

    def _fee(self, account_id: str, record: Json) -> None:
        today = self._now().date().isoformat()
        self._held["activities"].setdefault(account_id, []).append(
            {
                "id": f"00000000-0000-4000-8000-f{record['brokerage_order_id'][-4:]:0>11}",
                "type": "FEE",
                "description": "Order cancellation fee",
                "amount": "-0.50",
                "currency": {"code": "USD"},
                "trade_date": f"{today}T00:00:00.000Z",
                "settlement_date": f"{today}T00:00:00.000Z",
                "external_reference_id": record["brokerage_order_id"],
            }
        )

    def _filled(self, account_id: str, record: Json, before: Decimal) -> None:
        """The units a step filled, as the account's activity states them."""
        units = Decimal(record["filled_quantity"]) - before
        if units <= 0:
            return
        today = self._now().date()
        settles = today + timedelta(days=1)
        while settles.weekday() >= 5:
            settles += timedelta(days=1)
        price = Decimal(record["execution_price"])
        sign = -1 if record["action"] == "BUY" else 1
        number = len(self._held["activities"].get(account_id, [])) + 1
        self._held["activities"].setdefault(account_id, []).append(
            {
                "id": f"00000000-0000-4000-8000-e{number:011d}",
                "type": record["action"],
                "symbol": {"symbol": record["universal_symbol"]["symbol"]},
                "units": str(units),
                "price": str(price),
                "amount": str(sign * units * price),
                "currency": {"code": "USD"},
                "trade_date": f"{today.isoformat()}T00:00:00.000Z",
                "settlement_date": f"{settles.isoformat()}T00:00:00.000Z",
                "external_reference_id": record["brokerage_order_id"],
            }
        )

    def _step(self, account_id: str, record: Json) -> None:
        before = Decimal(record["filled_quantity"])
        self._advance(record)
        self._filled(account_id, record, before)

    def _advance(self, record: Json) -> None:
        status = record["status"]
        ticker = record["universal_symbol"]["symbol"]
        total = Decimal(record["total_quantity"])
        price = (
            Decimal(record["limit_price"])
            if record["limit_price"]
            else PRICES.get(ticker, PRICE)
        )
        now = self._now()
        if status == ODD_STATUS:
            record["status"] = "ACCEPTED"
        elif status == "ACCEPTED" and total <= FILLS_AT_ONCE:
            first = (total * Decimal("0.4")).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
            first = first if 0 < first < total else total
            record.update(
                status="PARTIAL" if first < total else "EXECUTED",
                filled_quantity=str(first),
                open_quantity=str(total - first),
                execution_price=str(price),
                time_executed=_stamp(now),
            )
        elif status == "PARTIAL":
            record.update(
                status="EXECUTED",
                filled_quantity=str(total),
                open_quantity="0",
                execution_price=str(price),
                time_executed=_stamp(now),
            )
        elif status == "EXECUTED" and ticker == BUSTED:
            record.setdefault("reads_executed", 0)
            record["reads_executed"] += 1
            if record["reads_executed"] >= 2:
                record.update(
                    status="CANCELED", filled_quantity="0", canceled_quantity=str(total)
                )
        else:
            return
        record["time_updated"] = _stamp(now + timedelta(microseconds=1))
