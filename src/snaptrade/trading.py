"""SnapTrade as a connectivity plugin (contract v22; meridian-design
plans/an-order-reaches-a-venue-through-the-book, build row 16; W11.8 to
W11.12, W11.20).

**What it does.** It hears each placement naming its own instance, and no
other's (the sidecar delivers a placement to the connectivity plugin it
names alone, matrix/directed.tsv; one naming another is never acted on). For
each queued one it reads the placement's order from the book (ListOrders),
the instrument's record (ResolveInstrument) for the ticker SnapTrade names it
by, and places it at SnapTrade's trading API in the account the placement's
venue account code names -- the code the placement's `ems` mapped, which is
the external account ID this plugin reports for custody -- with the
placement's ID as SnapTrade's client order ID. It reports what SnapTrade did
in the contract's meanings (ReportPlacement), every fill (ReportExecution)
and every charge with no fill (ReportCharge), each with the raw record it was
converted from, kept in the plugin's storage with every request and answer
(raw.py). It reads its account's orders at SnapTrade on each of its own
polls (`poll_seconds`), SnapTrade answering orders, not fills.

**Rejections, never refusals.** What SnapTrade or this plugin does not offer
for the account -- a connection on which trading was never enabled (read-only,
step 5's Q10), an order type or time in force its catalogue does not list, a
short sale, a strategy (SnapTrade has no algorithms; the `ems` works
strategies, Q6), an instrument with no ticker SnapTrade names, a code this
plugin holds no account for -- is the placement rejected `unsupported`,
nothing sent, its raw record what the decision was read from. What SnapTrade
answered with a refusal is `venue rejected`, its code and words as reported;
a status the mapping below does not know is outcome unknown, `venue status
not mapped`, the status as reported. A send SnapTrade never answered is
outcome unknown, `request unanswered`, and is resolved by asking SnapTrade
for the account's orders, never by sending it again (Q17): before every send
the placement is noted in the plugin's storage (`Sent`), so a placement the
book still holds queued after a restart is asked about, never sent twice.

**Fills.** SnapTrade states an order's filled quantity and its average
price, not each fill, so a fill is inferred from the filled quantity
growing between two reads (kind `inferred`): its quantity the difference,
its price worked out from the two averages (exact where it divides;
otherwise rounded half-even to `PRICE_PLACES` places, which the log and the
raw record show), its ID derived from the order's brokerage ID and the
filled quantity it brought the order to, the same however often it is read
(requirement 33). Its trade date is the date its execution time falls on in
the listing exchange's time zone as SnapTrade states it. SnapTrade's order
states no settlement date, so every fill carries the date read's
(ComputeDate, `computed_settlement_date` with its basis). Where the date read
answers none (a calendar or a rule not held, outside coverage), the date
SnapTrade states for the fill is sent instead: its `BUY` or `SELL` activity
naming the order's brokerage ID, as `settlement_date`; with neither, the fill
waits for the next poll, never sent with a guessed date.

**Busts, from a later read.** A filled quantity that falls between two reads
is the brokerage taking fills back: each fill this plugin reported beyond
the quantity SnapTrade now states is busted, a ReportExecution of quantity 0
superseding the book's execution (found by its venue execution ID in
ListOrders), its ID derived as the fill's was.

**Charges, from the activities.** SnapTrade's orders carry no fee. A `FEE`
activity whose `external_reference_id` is the brokerage ID of an order this
plugin placed that ended with nothing filled is the brokerage's charge on
that placement: a ReportCharge, kind cancel where the order was cancelled,
the amount positive as a fee takes cash. A fee on a filled order, or naming
no order of a placement, is not a charge here (custody reports every
activity to the street as it stands).

**The firm's controls.** It reads the trading instructions in force on start
(ListTradingInstructions) and hears each change. Under a pause or a stop on
All accounts, on this instance, or on a placement's account, it sends
nothing new: a queued placement is held, and sent once the instruction is
lifted if the book still holds it queued. A stop cancels at SnapTrade at
once every placement it is working that the stop covers, and reports each
cancel as a fact naming the stop's entry (RequestPlacementChange with
`cause.entry_id`; W11.9, v21's Q17), then SnapTrade's confirmation. A cancel
the `ems` asks for (a change requested on the placement) is sent too.

**Its catalogue.** On start, and when a connection's trading changes, it
publishes per venue account code what SnapTrade offers there
(ReportVenueCatalogue): the order types and times in force SnapTrade's
`POST /trade/place` takes, for an account whose connection has trading
enabled (the synthetic brokerages state their own, synthetic_orders.py), and
none for a read-only one; never a strategy, SnapTrade having none (FIXatdl's
model, Q31, has nothing to describe).

**Venue-originated orders.** An order SnapTrade lists on an account whose
code an `ems` mapped on this plugin (as a placement naming it showed) that
no placement of the book names was placed at the brokerage directly: it
is recorded (RecordOrder, origin venue, its ticker resolved by the order's
time, W3.1, or reported missing), placed on itself (CreatePlacement, a
direct order) and its fills reported as any placement's. The v22 book takes
a placement as a user's act and refuses one sent for no one; where it does,
the order stands recorded and its fills are reported on the order itself.

What it does not do in this version: a replace (SnapTrade's replace
endpoint is not called; the change request stays for the `ems`), a locate
(SnapTrade has no locate API), a place of execution (SnapTrade states none).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import meridian
from meridian.operations import as_decimal
from meridian.plugin.v1 import operations_pb2 as ops

from .declaration import seen_in_order
from .normalise import SOURCE, external_account
from .raw import (
    ACTIVITIES,
    CANCEL,
    OFFERED,
    ORDERS,
    PLACE,
    RawStore,
    trading_call,
    trading_failed,
    trading_taken,
)
from .synthetic_orders import SyntheticTrading
from .venue import Json, OrderAsked, Venue, VenueError

log = logging.getLogger("snaptrade.trading")

#: How often SnapTrade is asked about working orders: real SnapTrade allows
#: an account ten requests a minute; the simulated brokerage none.
POLL_SECONDS = 15
SYNTHETIC_POLL_SECONDS = 1

#: The places a fill's price worked out from two averages is rounded to
#: where the division is not exact.
PRICE_PLACES = 8

#: The scheme a venue's words are kept in as reported.
STATUS_SCHEME = "snaptrade:order-status"
ERROR_SCHEME = "snaptrade:error"

#: What SnapTrade's `POST /trade/place` takes, the catalogue of an account
#: whose connection has trading enabled.
PLACE_ORDER_TYPES = ("Market", "Limit", "Stop", "StopLimit")
PLACE_TIMES_IN_FORCE = ("Day", "GTC", "FOK", "IOC", "GTD")

ORDER_TYPES = {
    ops.ORDER_TYPE_MARKET: "Market",
    ops.ORDER_TYPE_LIMIT: "Limit",
    ops.ORDER_TYPE_STOP: "Stop",
    ops.ORDER_TYPE_STOP_LIMIT: "StopLimit",
}
TIMES_IN_FORCE = {
    ops.TIME_IN_FORCE_DAY: "Day",
    ops.TIME_IN_FORCE_GOOD_TILL_CANCELLED: "GTC",
    ops.TIME_IN_FORCE_IMMEDIATE_OR_CANCEL: "IOC",
    ops.TIME_IN_FORCE_FILL_OR_KILL: "FOK",
    ops.TIME_IN_FORCE_GOOD_TILL_DATE: "GTD",
}
ACTIONS = {ops.SIDE_BUY: "BUY", ops.SIDE_SELL: "SELL"}
FROM_ORDER_TYPE = {said: ours for ours, said in ORDER_TYPES.items()}
FROM_TIME_IN_FORCE = {said: ours for ours, said in TIMES_IN_FORCE.items()}

#: SnapTrade's order statuses, as this plugin converts them. Any other is
#: outcome unknown, `venue status not mapped`, its status as reported.
WORKING = frozenset(
    {
        "ACCEPTED",
        "PENDING",
        "QUEUED",
        "PARTIAL",
        "TRIGGERED",
        "ACTIVATED",
        "PENDING_RISK_REVIEW",
        "CONTINGENT_ORDER",
        "CANCEL_PENDING",
        "REPLACE_PENDING",
    }
)
FILLED = frozenset({"EXECUTED"})
CANCELLED = frozenset({"CANCELED", "PARTIAL_CANCELED"})
REJECTED = frozenset({"REJECTED", "FAILED"})
EXPIRED = frozenset({"EXPIRED"})
SUSPENDED = frozenset({"SUSPENDED"})
MAPPED = WORKING | FILLED | CANCELLED | REJECTED | EXPIRED | SUSPENDED

#: How many polls after a placement ends its order is still read, for a
#: bust after it filled, and its activities for a charge after it ended
#: unfilled; then it is let go.
CHARGE_POLLS = 10

HOLDING = (ops.TRADING_INSTRUCTION_VALUE_PAUSE, ops.TRADING_INSTRUCTION_VALUE_STOP)

#: The reason the book writes on a placement's change requested for a cancel
#: (meridian-core's `change_words`): the only place a connectivity plugin
#: can tell a cancel from a replace, the placement carrying no request kind.
CANCEL_ASKED = "cancel requested"


def _ns(moment: datetime) -> int:
    return int(moment.timestamp()) * 1_000_000_000 + moment.microsecond * 1000


def _number(value: Any) -> Decimal | None:
    """A JSON number or numeral read exactly, or None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value)) if isinstance(value, (int, str, Decimal)) else None
    except InvalidOperation:
        return None


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _dict(value: Any) -> Json:
    return value if isinstance(value, dict) else {}


def ticker_of(record: ops.InstrumentRecord) -> str:
    """The ticker SnapTrade names an instrument by: its symbol under
    SnapTrade's own scheme first, any symbol else; "" for none."""
    symbols = [held for held in record.identifiers if held.scheme == "symbol" and held.value]
    for held in symbols:
        if held.source == SOURCE:
            return str(held.value)
    return str(symbols[0].value) if symbols else ""


def trade_date_of(record: Json, moment: datetime) -> date:
    """The date an execution falls on in its listing exchange's time zone,
    as SnapTrade states it, else in UTC."""
    zone = _dict(_dict(record.get("universal_symbol")).get("exchange")).get("timezone")
    if isinstance(zone, str) and zone:
        with contextlib.suppress(ZoneInfoNotFoundError, ValueError):
            return moment.astimezone(ZoneInfo(zone)).date()
    return moment.astimezone(UTC).date()


def fill_price(
    before: Decimal, average_before: Decimal | None, after: Decimal, average_after: Decimal
) -> tuple[Decimal, bool]:
    """A fill's price from the averages before and after it, and whether it
    was rounded: exact where the division is."""
    paid = average_after * after - (average_before or Decimal(0)) * before
    quantity = after - before
    price = paid / quantity
    rounded = price.quantize(Decimal(1).scaleb(-PRICE_PLACES), rounding=ROUND_HALF_EVEN)
    exact = rounded * quantity == paid
    return (rounded.normalize() if exact else rounded), not exact


def execution_id(brokerage_order_id: str, filled: Decimal) -> str:
    """A fill's derived ID: the order and the filled quantity it brought the
    order to, the same however often it is read (requirement 33)."""
    return f"{brokerage_order_id}:{filled.normalize():f}"


def bust_id(fill: str) -> str:
    return f"{fill}:busted"


class Sent:
    """The placements this plugin has sent to SnapTrade, noted in its storage
    before each send, so a restart never sends one twice (Q17)."""

    def __init__(self, file: Path | None) -> None:
        self._file = file
        self._held: set[str] = set()
        if file is not None and file.exists():
            with contextlib.suppress(OSError, ValueError):
                held = json.loads(file.read_text())
                if isinstance(held, list):
                    self._held = {str(each) for each in held}

    def __contains__(self, placement_id: object) -> bool:
        return placement_id in self._held

    def note(self, placement_id: str) -> None:
        self._held.add(placement_id)
        if self._file is None:
            return
        self._file.parent.mkdir(parents=True, exist_ok=True)
        made, written = tempfile.mkstemp(dir=self._file.parent, suffix=".tmp")
        try:
            with os.fdopen(made, "w") as file:
                json.dump(sorted(self._held), file)
            os.replace(written, self._file)
        except BaseException:
            Path(written).unlink(missing_ok=True)
            raise


@dataclass
class Worked:
    """A placement SnapTrade holds an order for, as this plugin works it."""

    placement_id: str
    order_id: str
    code: str
    snaptrade_account_id: str
    instrument_id: str
    currency: str
    brokerage_order_id: str = ""
    # The filled quantity reported, and SnapTrade's average price then.
    filled: Decimal = Decimal(0)
    average: Decimal | None = None
    # Each fill reported: its derived ID, the filled quantity it brought the
    # order to, and its trade date; and the book's execution ID where known.
    fills: list[tuple[str, Decimal, str]] = field(default_factory=list)
    executions: dict[str, str] = field(default_factory=dict)
    # Its outcome is unknown: the send was never answered, or SnapTrade's
    # status is one the mapping does not know (that status).
    unknown: str = ""
    unanswered: bool = False
    acknowledged: bool = False
    # What ended it, once SnapTrade did: "filled", "cancelled", ...
    ended: str = ""
    cancel_sent: bool = False
    charge_polls: int = 0
    # Polls since it ended (CHARGE_POLLS).
    ended_polls: int = 0
    # A fill waits for its settlement date (said once).
    waiting: bool = False
    charged: set[str] = field(default_factory=set)


@dataclass
class Account:
    """One SnapTrade account, as a placement's venue account code names it."""

    code: str
    snaptrade_account_id: str
    connection: Json
    trading: bool
    slug: str


@dataclass
class Shown:
    """What the Connections page shows of trading: counts and states only."""

    working: int = 0
    held: int = 0
    unknown: int = 0
    trading_connections: tuple[str, ...] = ()
    last_poll: datetime | None = None
    error: str = ""


class Trader:
    """SnapTrade's connectivity work, beside its custody reads."""

    def __init__(
        self,
        plugin: Any,
        venue: Callable[[], Venue | None],
        *,
        now: Callable[[], datetime],
        raw: RawStore | None = None,
        synthetic: Callable[[], bool] = lambda: False,
        secrets: Callable[[], Sequence[str]] = lambda: (),
        sent: Sent | None = None,
    ) -> None:
        self._plugin = plugin
        self._venue = venue
        self._now = now
        self._raw = raw
        self._synthetic = synthetic
        self._secrets = secrets
        self.me: str = plugin.identity.instance_id
        self._sent = sent if sent is not None else Sent(None)
        # The instructions in force, by their target and setter.
        self.instructions: dict[tuple[Any, ...], ops.TradingInstruction] = {}
        self.working: dict[str, Worked] = {}
        # Placements heard and held back by an instruction; decided ones.
        self.held: dict[str, ops.Placement] = {}
        self.decided: set[str] = set()
        # The venue account codes an `ems` mapped on this plugin, as the
        # placements naming it showed; and every venue order ID the book's
        # placements name.
        self.codes: set[str] = set()
        self.venue_orders: set[str] = set()
        self.watermark: ops.Watermark | None = None
        self.published: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {}
        self.shown = Shown()
        self._lock = asyncio.Lock()
        # Each account's orders as last kept, and the reference to them: an
        # account's orders are kept again only when SnapTrade's answer changed.
        self._kept_orders: dict[str, tuple[str, ops.RawRecordRef]] = {}
        # SnapTrade's accounts, read at most once a minute.
        self._accounts_read: tuple[datetime, dict[str, Account]] | None = None

    # ── Reading the book ────────────────────────────────────────────────

    async def start(self) -> None:
        """What it reads on start: the instructions in force, its placements'
        orders, to work again what it was working; then its catalogue."""
        listed = await self._plugin.list_trading_instructions()
        for instruction in listed.instructions:
            self._hold(instruction)
        await self.catch_up(since=None)
        await self.publish(fresh=True)

    async def catch_up(self, since: ops.Watermark | None) -> None:
        """The placements naming it, as the book holds them now or changed
        since `since` (a loss, or this plugin's own backstop each poll)."""
        cursor = ""
        while True:
            listed = await self._plugin.list_orders(since=since, cursor=cursor)
            orders = {order.order_id: order for order in listed.orders}
            for order in listed.orders:
                for placement in order.placements:
                    await self._resume(placement, order, listed.executions)
            for placement in listed.placements:
                await self._resume(placement, orders.get(placement.order_id), listed.executions)
            if listed.HasField("as_of"):
                self.watermark = listed.as_of
            if not listed.next_cursor:
                return
            cursor = listed.next_cursor

    async def _resume(
        self,
        placement: ops.Placement,
        order: ops.Order | None,
        executions: Sequence[ops.Execution],
    ) -> None:
        if placement.connectivity_instance_id != self.me:
            return
        self.venue_orders.update(placement.venue_order_ids)
        if placement.venue_order_id:
            self.venue_orders.add(placement.venue_order_id)
        if placement.venue_account_code:
            self.codes.add(placement.venue_account_code)
        state = placement.status.state
        worked = self.working.get(placement.placement_id)
        if worked is None and state in (ops.PLACEMENT_STATE_SENT, ops.PLACEMENT_STATE_WORKING):
            worked = await self._worked(placement, order)
            if worked is None:
                return
            worked.acknowledged = bool(placement.venue_order_id)
            for execution in executions:
                if execution.placement_id != placement.placement_id:
                    continue
                worked.executions[execution.venue_execution_id] = execution.execution_id
            filled = self._filled_in_book(placement.placement_id, executions)
            worked.filled = filled
            self.working[placement.placement_id] = worked
            self.decided.add(placement.placement_id)
            log.info("working %s again: %s filled", placement.placement_id, filled)
        if (
            state == ops.PLACEMENT_STATE_QUEUED
            and placement.placement_id not in self.decided
            and placement.placement_id not in self.working
        ):
            await self.on_queued(placement, order)
        asked = [
            held
            for held in placement.status.conditions
            if held.condition == ops.PLACEMENT_CONDITION_CHANGE_REQUESTED
        ]
        if worked is not None and asked:
            await self._asked_to_cancel(placement, order, worked, asked[0])

    @staticmethod
    def _filled_in_book(placement_id: str, executions: Sequence[ops.Execution]) -> Decimal:
        busted = {e.supersedes for e in executions if e.supersedes}
        total = Decimal(0)
        for execution in executions:
            if execution.placement_id != placement_id or execution.execution_id in busted:
                continue
            if execution.supersedes:
                continue
            total += as_decimal(execution.quantity)
        return total

    async def _order_of(self, placement: ops.Placement) -> ops.Order | None:
        listed = await self._plugin.list_orders(account_id=placement.account_id)
        for order in listed.orders:
            if order.order_id == placement.order_id:
                found: ops.Order = order
                return found
        return None

    # ── Hearing ─────────────────────────────────────────────────────────

    async def on_placement(self, heard: Any) -> None:
        """A placement naming this instance, as the book changed it."""
        placement: ops.Placement = heard.message.placement
        if placement.connectivity_instance_id != self.me:
            # Delivered to the instance a placement names alone; one naming
            # another is never acted on.
            return
        async with self._lock:
            await self._resume(placement, None, ())

    async def on_instruction(self, heard: Any) -> None:
        """A trading instruction set, changed or lifted."""
        instruction: ops.TradingInstruction = heard.message.instruction
        async with self._lock:
            self._hold(instruction)
            if instruction.value == ops.TRADING_INSTRUCTION_VALUE_STOP:
                await self._stop(instruction)
            elif instruction.value == ops.TRADING_INSTRUCTION_VALUE_ACTIVE:
                await self._release_held()

    @staticmethod
    def _key(instruction: ops.TradingInstruction) -> tuple[Any, ...]:
        target = instruction.target
        return (
            target.all_accounts,
            target.account_id,
            target.instance_id,
            target.subject,
            target.service_account_id,
            instruction.setter_level,
        )

    def _hold(self, instruction: ops.TradingInstruction) -> None:
        key = self._key(instruction)
        if instruction.value in HOLDING:
            self.instructions[key] = instruction
        else:
            self.instructions.pop(key, None)

    def _covers(self, instruction: ops.TradingInstruction, account_id: str) -> bool:
        """Whether an instruction binds this plugin's placements in an account:
        on All accounts, this instance, or the account (at this instance where
        it names one). One aimed at a person or a service account binds the
        plugins acting for them, not a venue's."""
        target = instruction.target
        if target.subject or target.service_account_id:
            return False
        if target.instance_id and target.instance_id != self.me:
            return False
        if target.account_id:
            return target.account_id == account_id
        return target.all_accounts or target.instance_id == self.me

    def in_force(self, account_id: str) -> ops.TradingInstruction | None:
        """The instruction holding this plugin's placements in an account, a
        stop before a pause, or None."""
        binding = [i for i in self.instructions.values() if self._covers(i, account_id)]
        binding.sort(key=lambda i: i.value != ops.TRADING_INSTRUCTION_VALUE_STOP)
        return binding[0] if binding else None

    async def _stop(self, instruction: ops.TradingInstruction) -> None:
        """A stop's cancels, at SnapTrade at once, each reported as a fact
        naming the stop's entry (W11.9), never waiting for the book."""
        for worked in list(self.working.values()):
            if worked.ended or worked.cancel_sent:
                continue
            account = await self._account_id_of(worked)
            if account and not self._covers(instruction, account):
                continue
            await self._cancel(worked, cause=instruction.entry, reason="a stop is in force")

    async def _account_id_of(self, worked: Worked) -> str:
        listed = await self._plugin.list_orders()
        for order in listed.orders:
            if order.order_id == worked.order_id:
                return str(order.account_id)
        return ""

    async def _release_held(self) -> None:
        """Placements held back, sent once nothing holds them, if the book
        still holds them queued."""
        for placement_id, placement in list(self.held.items()):
            if self.in_force(placement.account_id) is not None:
                continue
            self.held.pop(placement_id, None)
            order = await self._order_of(placement)
            current = next(
                (
                    p
                    for p in (order.placements if order else ())
                    if p.placement_id == placement_id
                ),
                placement,
            )
            if current.status.state == ops.PLACEMENT_STATE_QUEUED:
                await self.on_queued(current, order)

    # ── Sending ─────────────────────────────────────────────────────────

    async def on_queued(self, placement: ops.Placement, order: ops.Order | None) -> None:
        """A queued placement naming this instance: held, rejected, or sent."""
        pid = placement.placement_id
        if placement.venue_account_code:
            self.codes.add(placement.venue_account_code)
        if self.in_force(placement.account_id) is not None:
            self.held[pid] = placement
            log.info("holding %s back: trading is paused or stopped", pid)
            return
        if pid in self._sent:
            # Sent before this process: asked about, never sent again.
            worked = await self._worked(placement, order)
            if worked is not None:
                worked.unanswered = True
                self.working[pid] = worked
                self.decided.add(pid)
            return
        self.decided.add(pid)
        order = order if order is not None else await self._order_of(placement)
        venue = self._venue()
        if order is None or venue is None:
            self.decided.discard(pid)
            return
        account = await self._account(venue, placement.venue_account_code)
        if account is None:
            await self._reject_unsupported(
                placement, None, "SnapTrade holds no account under this venue account code"
            )
            return
        why = self._unsupported(placement, order, account)
        if why:
            await self._reject_unsupported(placement, account, why)
            return
        found = await self._plugin.resolve_instrument(instrument_id=order.instrument_id)
        ticker = ticker_of(found.instrument) if found.found else ""
        if not ticker:
            await self._reject_unsupported(
                placement, account, "the instrument's record holds no ticker SnapTrade names"
            )
            return
        asked = self._asked(placement, order, ticker)
        worked = Worked(
            placement_id=pid,
            order_id=order.order_id,
            code=account.code,
            snaptrade_account_id=account.snaptrade_account_id,
            instrument_id=order.instrument_id,
            currency=found.instrument.currency or "USD",
        )
        self._sent.note(pid)
        try:
            answer = await venue.place(account.snaptrade_account_id, asked)
        except VenueError as failed:
            await self._unanswered_or_refused(placement, worked, asked, failed)
            return
        raw = self._keep(
            account, [trading_call(PLACE, answer, json.dumps(asked.body()))], "place"
        )
        worked.brokerage_order_id = str(answer.get("brokerage_order_id") or "")
        self.working[pid] = worked
        await self._report_status(worked, answer, raw)

    def _unsupported(self, placement: ops.Placement, order: ops.Order, account: Account) -> str:
        if not account.trading:
            return "trading is not enabled on the account's connection: it is read-only"
        if placement.HasField("strategy") and placement.strategy.kind:
            return "SnapTrade works no strategy: it has no broker algorithms"
        if order.side not in ACTIONS:
            return "SnapTrade places buys and sells: no short sale"
        offered = dict(self.catalogue_of(account))
        order_type = ORDER_TYPES.get(order.order_type)
        if order_type is None or order_type not in offered:
            return "the account's catalogue lists no such order type"
        tif = placement.time_in_force or order.time_in_force
        if TIMES_IN_FORCE.get(tif) not in offered[order_type]:
            return "the account's catalogue lists no such time in force for the order type"
        return ""

    def _asked(self, placement: ops.Placement, order: ops.Order, ticker: str) -> OrderAsked:
        price = (
            placement.limit_price
            if placement.HasField("limit_price")
            else (order.limit_price if order.HasField("limit_price") else None)
        )
        stop = order.stop_price if order.HasField("stop_price") else None
        tif = placement.time_in_force or order.time_in_force
        expiry = date.fromisoformat(order.expire_date) if order.expire_date else None
        return OrderAsked(
            action=ACTIONS[order.side],
            order_type=ORDER_TYPES[order.order_type],
            time_in_force=TIMES_IN_FORCE[tif],
            symbol=ticker,
            units=as_decimal(placement.quantity),
            client_order_id=placement.placement_id,
            price=None if price is None else as_decimal(price.amount),
            stop=None if stop is None else as_decimal(stop.amount),
            expiry_date=expiry,
        )

    async def _unanswered_or_refused(
        self, placement: ops.Placement, worked: Worked, asked: OrderAsked, failed: VenueError
    ) -> None:
        account = Account(worked.code, worked.snaptrade_account_id, {}, True, "")
        kept = trading_failed(PLACE, str(failed), failed.said or None)
        kept["note"] = json.dumps(asked.body())
        raw = self._keep(account, [kept], "place")
        if failed.answered:
            code = str(failed.said.get("code") or failed.status or "")
            text = str(failed.said.get("detail") or "SnapTrade refused the order")
            await self._plugin.report_placement(
                placement_id=placement.placement_id,
                event=ops.PLACEMENT_EVENT_REJECTED,
                rejection_reason=ops.REJECTION_REASON_VENUE_REJECTED,
                rejection_as_reported=ops.AsReported(scheme=ERROR_SCHEME, code=code, text=text),
                raw_record=raw,
            )
            return
        # Never answered: asked about on the next poll, never sent again.
        worked.unanswered = True
        self.working[placement.placement_id] = worked
        await self._plugin.report_placement(
            placement_id=placement.placement_id,
            event=ops.PLACEMENT_EVENT_OUTCOME_UNKNOWN,
            unknown_reason=ops.UNKNOWN_REASON_REQUEST_UNANSWERED,
            raw_record=raw,
        )

    async def _reject_unsupported(
        self, placement: ops.Placement, account: Account | None, why: str
    ) -> None:
        stated: Json = {"why": why, "venue_account_code": placement.venue_account_code}
        if account is not None:
            stated["connection"] = account.connection
            stated["catalogue"] = [
                {"order_type": t, "times_in_force": list(f)}
                for t, f in self.catalogue_of(account)
            ]
        holder = account or Account(placement.venue_account_code, "", {}, False, "")
        raw = self._keep(holder, [trading_call(OFFERED, stated, why)], "offered")
        log.info("rejected %s unsupported: %s", placement.placement_id, why)
        await self._plugin.report_placement(
            placement_id=placement.placement_id,
            event=ops.PLACEMENT_EVENT_REJECTED,
            rejection_reason=ops.REJECTION_REASON_UNSUPPORTED,
            raw_record=raw,
        )

    async def _worked(self, placement: ops.Placement, order: ops.Order | None) -> Worked | None:
        order = order if order is not None else await self._order_of(placement)
        venue = self._venue()
        if order is None or venue is None:
            return None
        account = await self._account(venue, placement.venue_account_code)
        if account is None:
            return None
        found = await self._plugin.resolve_instrument(instrument_id=order.instrument_id)
        return Worked(
            placement_id=placement.placement_id,
            order_id=order.order_id,
            code=account.code,
            snaptrade_account_id=account.snaptrade_account_id,
            instrument_id=order.instrument_id,
            currency=(found.instrument.currency if found.found else "") or "USD",
            brokerage_order_id=placement.venue_order_id,
        )

    # ── Cancelling ──────────────────────────────────────────────────────

    async def _asked_to_cancel(
        self,
        placement: ops.Placement,
        order: ops.Order | None,
        worked: Worked,
        condition: ops.StandingPlacementCondition,
    ) -> None:
        """A change requested on a placement it works: a cancel is sent at
        SnapTrade. The placement does not carry the request's kind, and its
        order carries only its own requests, so a cancel is known by the
        order's pending request naming the placement where there is one, else
        by the condition's reason the book writes for a cancel (CANCEL_ASKED);
        a replace is not sent in this version."""
        order = order if order is not None else await self._order_of(placement)
        if order is None or worked.cancel_sent or worked.ended:
            return
        for request in order.change_requests:
            if (
                request.placement_id == placement.placement_id
                and request.outcome == ops.CHANGE_REQUEST_OUTCOME_PENDING
                and request.HasField("placement_change")
            ):
                if request.placement_change.kind == ops.PLACEMENT_CHANGE_KIND_CANCEL:
                    await self._cancel(worked, cause=None, reason="")
                return
        if condition.reason == CANCEL_ASKED:
            await self._cancel(worked, cause=None, reason="")

    async def _cancel(
        self, worked: Worked, *, cause: ops.EntryMeta | None, reason: str
    ) -> None:
        venue = self._venue()
        if venue is None or not worked.brokerage_order_id:
            return
        account = Account(worked.code, worked.snaptrade_account_id, {}, True, "")
        worked.cancel_sent = True
        try:
            answer = await venue.cancel(worked.snaptrade_account_id, worked.brokerage_order_id)
        except VenueError as failed:
            # Refused (already filled, say) or never answered: no cancel was
            # taken, so none is reported; SnapTrade's next answer says what
            # became of the order.
            self._keep(
                account, [trading_failed(CANCEL, str(failed), failed.said or None)], "cancel"
            )
            log.info("%s: the cancel was not taken: %s", worked.placement_id, failed)
            return
        self._keep(account, [trading_call(CANCEL, answer, worked.brokerage_order_id)], "cancel")
        if cause is not None:
            # The cancel already sent at SnapTrade, reported as a fact
            # naming the stop (W11.9; v21's Q17).
            await self._plugin.request_placement_change(
                placement_id=worked.placement_id,
                change=meridian.PlacementChange(kind=ops.PLACEMENT_CHANGE_KIND_CANCEL),
                reason=reason,
                cause=ops.EntryMeta(entry_id=cause.entry_id),
                idempotency_key=f"stop:{cause.entry_id}:{worked.placement_id}",
            )

    # ── Polling SnapTrade ───────────────────────────────────────────────

    async def poll(self) -> None:
        """One poll: the book's placements changed since the last (a backstop
        for a delivery lost), then each account's orders at SnapTrade."""
        async with self._lock:
            venue = self._venue()
            if venue is None:
                return
            if self.watermark is not None:
                await self.catch_up(since=self.watermark)
            for worked in self.working.values():
                if worked.ended:
                    worked.ended_polls += 1
            watched = [w for w in self.working.values() if w.ended_polls <= CHARGE_POLLS]
            accounts = {w.snaptrade_account_id: w.code for w in watched}
            known = await self._accounts(venue)
            for code in self.codes:
                if code in known:
                    accounts.setdefault(known[code].snaptrade_account_id, code)
            for snaptrade_account_id, code in accounts.items():
                try:
                    orders = await venue.orders(snaptrade_account_id)
                except VenueError as failed:
                    self.shown.error = str(failed)
                    log.warning("%s", failed)
                    continue
                await self._read_orders(known.get(code), code, snaptrade_account_id, orders)
            await self._charges(venue)
            self.shown = Shown(
                working=sum(1 for w in self.working.values() if not w.ended),
                held=len(self.held),
                unknown=sum(1 for w in self.working.values() if w.unknown or w.unanswered),
                trading_connections=self.shown.trading_connections,
                last_poll=self._now(),
            )

    async def _read_orders(
        self, account: Account | None, code: str, snaptrade_account_id: str, orders: list[Json]
    ) -> None:
        holder = account or Account(code, snaptrade_account_id, {}, True, "")
        by_id = {str(o.get("brokerage_order_id") or ""): o for o in orders}
        by_client = {
            str(o.get("client_order_id") or ""): o for o in orders if o.get("client_order_id")
        }
        mine = [
            w for w in self.working.values() if w.snaptrade_account_id == snaptrade_account_id
        ]
        changed = [
            w
            for w in mine
            if (not w.ended or (w.ended == "filled" and w.ended_polls <= CHARGE_POLLS))
            and (w.brokerage_order_id in by_id or w.unanswered)
        ]
        if not changed and not any(oid not in self.venue_orders for oid in by_id):
            return
        said = json.dumps(orders, sort_keys=True, default=str)
        kept = self._kept_orders.get(snaptrade_account_id)
        if kept is not None and kept[0] == said:
            raw = kept[1]
        else:
            raw = self._keep(holder, [trading_call(ORDERS, orders)], "orders")
            self._kept_orders[snaptrade_account_id] = (said, raw)
            for listed in orders:
                for scheme, name in seen_in_order(listed):
                    self._plugin.note_not_carried(scheme, name)
        for worked in changed:
            record = by_id.get(worked.brokerage_order_id)
            if worked.unanswered:
                record = by_client.get(worked.placement_id)
                if record is None:
                    continue
                worked.brokerage_order_id = str(record.get("brokerage_order_id") or "")
                worked.unanswered = False
            if record is not None:
                await self._report_status(worked, record, raw)
        # An order SnapTrade has not answered for is never taken for one the
        # brokerage took outside the platform.
        unanswered = any(w.unanswered for w in mine)
        if code in self.codes and not unanswered:
            placed = {w.brokerage_order_id for w in self.working.values()} | self.venue_orders
            for oid, record in by_id.items():
                if oid and oid not in placed and record.get("client_order_id") is None:
                    try:
                        await self._venue_originated(holder, record, raw)
                    except meridian.MeridianError as refused:
                        self.venue_orders.add(oid)
                        log.warning("the venue's order %s was not recorded: %s", oid, refused)

    async def _report_placement(self, worked: Worked, **fields: Any) -> None:
        """A placement's fact; none for a venue-originated order the book
        holds no placement of (below), whose fills are reported on it."""
        if worked.placement_id:
            await self._plugin.report_placement(placement_id=worked.placement_id, **fields)

    async def _report_status(self, worked: Worked, record: Json, raw: ops.RawRecordRef) -> None:
        """What SnapTrade's record of the order says now, as facts."""
        status = str(record.get("status") or "")
        if status not in MAPPED:
            if worked.unknown != status:
                worked.unknown = status
                await self._report_placement(
                    worked,
                    event=ops.PLACEMENT_EVENT_OUTCOME_UNKNOWN,
                    unknown_reason=ops.UNKNOWN_REASON_VENUE_STATUS_NOT_MAPPED,
                    status_as_reported=ops.AsReported(
                        scheme=STATUS_SCHEME, code=status, text=status
                    ),
                    raw_record=raw,
                )
            return
        if status in REJECTED and not worked.ended:
            worked.ended = "rejected"
            await self._report_placement(
                worked,
                event=ops.PLACEMENT_EVENT_REJECTED,
                rejection_reason=ops.REJECTION_REASON_VENUE_REJECTED,
                rejection_as_reported=ops.AsReported(
                    scheme=STATUS_SCHEME, code=status, text=status
                ),
                raw_record=raw,
            )
            return
        if not worked.acknowledged or worked.unknown:
            worked.acknowledged = True
            worked.unknown = ""
            if worked.brokerage_order_id:
                self.venue_orders.add(worked.brokerage_order_id)
            await self._report_placement(
                worked,
                event=ops.PLACEMENT_EVENT_ACKNOWLEDGED,
                venue_order_id=worked.brokerage_order_id,
                routed_at_ns=_ns(_moment(record.get("time_placed")) or self._now()),
                routed_precision=ops.TIME_PRECISION_MILLISECOND,
                raw_record=raw,
            )
        await self._fills(worked, record, raw)
        if status in CANCELLED and not worked.ended:
            worked.ended = "cancelled"
            await self._report_placement(
                worked,
                event=ops.PLACEMENT_EVENT_CANCELLED,
                raw_record=raw,
            )
        elif status in EXPIRED and not worked.ended:
            worked.ended = "expired"
            await self._report_placement(
                worked,
                event=ops.PLACEMENT_EVENT_EXPIRED,
                raw_record=raw,
            )
        elif status in SUSPENDED:
            await self._report_placement(
                worked,
                event=ops.PLACEMENT_EVENT_SUSPENDED,
                raw_record=raw,
            )
        elif status in FILLED:
            worked.ended = "filled"

    async def _fills(self, worked: Worked, record: Json, raw: ops.RawRecordRef) -> None:
        filled = _number(record.get("filled_quantity")) or Decimal(0)
        average = _number(record.get("execution_price"))
        if filled < worked.filled:
            await self._busts(worked, filled, raw)
        if filled <= worked.filled or average is None:
            return
        price, rounded = fill_price(worked.filled, worked.average, filled, average)
        if rounded:
            log.info(
                "%s: a fill's price rounded to %s places: %s",
                worked.placement_id,
                PRICE_PLACES,
                price,
            )
        moment = _moment(record.get("time_executed")) or _moment(record.get("time_updated"))
        moment = moment or self._now()
        traded = trade_date_of(record, moment)
        settles = await self._plugin.compute_date(
            settlement=ops.SettlementDateInputs(
                instrument_id=worked.instrument_id, trade_date=traded.isoformat()
            ),
            as_of_ns=_ns(moment),
        )
        computed = settles.outcome == ops.DATE_OUTCOME_COMPUTED and bool(settles.date)
        stated = "" if computed else await self._stated_settlement(worked, traded)
        if not computed and not stated:
            # Never sent with a guessed date: asked again on the next poll,
            # said once.
            if not worked.waiting:
                worked.waiting = True
                log.info(
                    "%s: no settlement date yet (%s); asked again each poll",
                    worked.placement_id or worked.order_id,
                    ops.DateOutcome.Name(settles.outcome),
                )
            return
        worked.waiting = False
        fill = execution_id(worked.brokerage_order_id, filled)
        done = await self._plugin.report_execution(
            placement_id=worked.placement_id,
            order_id="" if worked.placement_id else worked.order_id,
            venue_account_code=worked.code,
            venue_execution_id=fill,
            kind=ops.EXECUTION_KIND_INFERRED,
            source=ops.EXECUTION_SOURCE_PRIMARY,
            quantity=filled - worked.filled,
            price=meridian.Money(price, worked.currency),
            trade_time_ns=_ns(moment),
            trade_date=traded.isoformat(),
            finality=meridian.Finality(level=ops.FINALITY_LEVEL_FINAL, at_ns=_ns(moment)),
            settlement_date=stated,
            computed_settlement_date=settles.date if computed else "",
            settlement_date_basis=basis_of(settles.basis) if computed else None,
            raw_record=raw,
            idempotency_key=f"fill:{fill}",
        )
        if done.HasField("execution") and done.execution.execution_id:
            worked.executions[fill] = done.execution.execution_id
        worked.fills.append((fill, filled, traded.isoformat()))
        worked.filled, worked.average = filled, average

    async def _stated_settlement(self, worked: Worked, traded: date) -> str:
        """The settlement date SnapTrade states for the order's fill, where the
        date read answers none: its `BUY` or `SELL` activity naming the order's
        brokerage ID as its external reference, the one traded that day first;
        "" for none."""
        venue = self._venue()
        if venue is None:
            return ""
        try:
            activities = await venue.activities(
                worked.snaptrade_account_id,
                traded - timedelta(days=2),
                traded + timedelta(days=2),
            )
        except VenueError:
            return ""
        named = [
            activity
            for activity in activities
            if str(activity.get("type") or "") in ("BUY", "SELL")
            and str(activity.get("external_reference_id") or "") == worked.brokerage_order_id
            and str(activity.get("settlement_date") or "")
        ]
        named.sort(key=lambda a: str(a.get("trade_date") or "")[:10] != traded.isoformat())
        return str(named[0].get("settlement_date"))[:10] if named else ""

    async def _busts(self, worked: Worked, filled: Decimal, raw: ops.RawRecordRef) -> None:
        """Each fill reported beyond what SnapTrade now states filled, busted."""
        for fill, cumulative, traded in sorted(worked.fills, key=lambda f: f[1], reverse=True):
            if cumulative <= filled:
                break
            busted = worked.executions.get(fill) or await self._execution_in_book(worked, fill)
            if not busted:
                log.warning(
                    "%s: no execution in the book for %s to bust", worked.placement_id, fill
                )
                continue
            await self._plugin.report_execution(
                placement_id=worked.placement_id,
                order_id="" if worked.placement_id else worked.order_id,
                venue_account_code=worked.code,
                venue_execution_id=bust_id(fill),
                kind=ops.EXECUTION_KIND_INFERRED,
                source=ops.EXECUTION_SOURCE_PRIMARY,
                quantity=Decimal(0),
                price=meridian.Money(worked.average or Decimal(0), worked.currency),
                trade_date=traded,
                supersedes=busted,
                raw_record=raw,
                idempotency_key=f"bust:{fill}",
            )
            worked.fills.remove((fill, cumulative, traded))
        worked.filled = max([c for _, c, _ in worked.fills], default=Decimal(0))
        if not worked.fills:
            worked.average = None

    async def _execution_in_book(self, worked: Worked, fill: str) -> str:
        listed = await self._plugin.list_orders()
        for execution in listed.executions:
            if (
                execution.placement_id == worked.placement_id
                and execution.venue_execution_id == fill
            ):
                return str(execution.execution_id)
        return ""

    async def _charges(self, venue: Venue) -> None:
        """A cancel's fee on a placement that ended unfilled (above)."""
        ended = [
            w
            for w in self.working.values()
            if w.ended in ("cancelled", "expired", "rejected")
            and w.filled == 0
            and w.charge_polls < CHARGE_POLLS
        ]
        today = self._now().date()
        for worked in ended:
            worked.charge_polls += 1
            try:
                activities = await venue.activities(
                    worked.snaptrade_account_id, today - timedelta(days=7), today
                )
            except VenueError:
                continue
            for activity in activities:
                activity_id = str(activity.get("id") or "")
                if (
                    str(activity.get("type") or "") != "FEE"
                    or str(activity.get("external_reference_id") or "")
                    != worked.brokerage_order_id
                    or activity_id in worked.charged
                ):
                    continue
                amount = _number(activity.get("amount"))
                currency = _dict(activity.get("currency")).get("code") or worked.currency
                if amount is None:
                    continue
                account = Account(worked.code, worked.snaptrade_account_id, {}, True, "")
                raw = self._keep(
                    account, [trading_call(ACTIVITIES, activity, activity_id)], "activities"
                )
                worked.charged.add(activity_id)
                traded = str(activity.get("trade_date") or "")[:10]
                settles = str(activity.get("settlement_date") or "")[:10]
                await self._plugin.report_charge(
                    placement_id=worked.placement_id,
                    fee=meridian.Fee(
                        kind=ops.FEE_KIND_CANCEL
                        if worked.ended == "cancelled"
                        else ops.FEE_KIND_EXCHANGE,
                        amount=meridian.Money(abs(amount), str(currency)),
                    ),
                    trade_date=traded,
                    settlement_date=settles,
                    raw_record=raw,
                )

    async def _venue_originated(
        self, account: Account, record: Json, raw: ops.RawRecordRef
    ) -> None:
        """An order the brokerage took outside the platform, on an account an
        `ems` mapped on this plugin: recorded, placed on itself, worked."""
        oid = str(record.get("brokerage_order_id") or "")
        symbol = _dict(record.get("universal_symbol"))
        ticker = str(symbol.get("symbol") or "")
        placed_at = _moment(record.get("time_placed")) or self._now()
        identifiers = [meridian.Identifier(scheme="symbol", value=ticker, source=SOURCE)]
        found = await self._plugin.resolve_identifier(
            identifiers=identifiers, as_of_ns=_ns(placed_at)
        )
        self.venue_orders.add(oid)
        if not found.found:
            await self._plugin.report_missing_instrument(
                source=SOURCE,
                identifiers=identifiers,
                as_of_ns=_ns(placed_at),
                reason=found.miss_reason or ops.MISS_REASON_NOT_FOUND,
                observed_at_ns=_ns(self._now()),
            )
            return
        side = {"BUY": ops.SIDE_BUY, "SELL": ops.SIDE_SELL}.get(str(record.get("action") or ""))
        order_type = FROM_ORDER_TYPE.get(str(record.get("order_type") or ""))
        tif = FROM_TIME_IN_FORCE.get(str(record.get("time_in_force") or ""))
        quantity = _number(record.get("total_quantity"))
        if side is None or quantity is None:
            return
        limit = _number(record.get("limit_price"))
        currency = str(_dict(symbol.get("currency")).get("code") or "USD")
        recorded = await self._plugin.record_order(
            venue_account_code=account.code,
            instrument_id=found.instrument_id,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=None if limit is None else meridian.Money(limit, currency),
            time_in_force=tif,
            venue_order_id=oid,
            origin=ops.ORDER_ORIGIN_VENUE,
            raw_record=raw,
            idempotency_key=f"venue-order:{account.code}:{oid}",
        )
        if not recorded.order.order_id:
            return
        try:
            placed = await self._plugin.create_placement(
                order_id=recorded.order.order_id,
                connectivity_instance_id=self.me,
                venue_account_code=account.code,
                quantity=quantity,
                limit_price=None if limit is None else meridian.Money(limit, currency),
                time_in_force=tif,
                idempotency_key=f"venue-placement:{account.code}:{oid}",
            )
            pid = placed.placement.placement_id
        except meridian.MeridianError as refused:
            # The book takes a placement as a user's act: where it refuses one
            # sent for no one, the order stands recorded and its fills are
            # reported on it, as a done-away order's are.
            log.info(
                "%s: no placement for the venue's order (%s); fills on the order", oid, refused
            )
            pid = ""
        worked = Worked(
            placement_id=pid,
            order_id=recorded.order.order_id,
            code=account.code,
            snaptrade_account_id=account.snaptrade_account_id,
            instrument_id=found.instrument_id,
            currency=currency,
            brokerage_order_id=oid,
        )
        self.working[pid or f"order:{recorded.order.order_id}"] = worked
        if pid:
            self.decided.add(pid)
        await self._report_status(worked, record, raw)

    # ── Accounts and the catalogue ──────────────────────────────────────

    async def _accounts(self, venue: Venue, fresh: bool = False) -> dict[str, Account]:
        """Every SnapTrade account, by the venue account code that names it:
        the external account ID custody reports it under; read again at most
        once a minute unless `fresh`."""
        now = self._now()
        if not fresh and self._accounts_read is not None:
            read_at, held_then = self._accounts_read
            if now - read_at < timedelta(minutes=1):
                return held_then
        connections = {str(c.get("id", "")): c for c in await venue.connections()}
        held: dict[str, Account] = {}
        for listed in await venue.accounts():
            connection = connections.get(str(listed.get("brokerage_authorization", "")), {})
            identity = external_account(listed, connection)
            brokerage = _dict(connection.get("brokerage"))
            held[identity.external_account_id] = Account(
                code=identity.external_account_id,
                snaptrade_account_id=identity.snaptrade_account_id,
                connection=connection,
                trading=connection.get("type") == "trade" and not connection.get("disabled"),
                slug=str(brokerage.get("slug") or ""),
            )
        self.shown.trading_connections = tuple(
            sorted({str(a.connection.get("id", "")) for a in held.values() if a.trading})
        )
        self._accounts_read = (now, held)
        return held

    async def _account(self, venue: Venue, code: str) -> Account | None:
        try:
            return (await self._accounts(venue, fresh=True)).get(code)
        except VenueError as failed:
            log.warning("%s", failed)
            return None

    def catalogue_of(self, account: Account) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """What SnapTrade offers on an account: none on a read-only
        connection; else what its `POST /trade/place` takes, or a synthetic
        brokerage what it states."""
        if not account.trading:
            return ()
        venue = self._venue()
        trading = getattr(venue, "trading", None)
        if isinstance(trading, SyntheticTrading):
            types, tifs = trading.offered(account.slug)
        else:
            types, tifs = PLACE_ORDER_TYPES, PLACE_TIMES_IN_FORCE
        return tuple((each, tuple(tifs)) for each in types)

    async def publish(self, fresh: bool = False) -> None:
        """Each venue account code's catalogue, where it changed since the last
        published (W11.20): on start, and as SnapTrade's accounts are read
        again, at most once a minute unless `fresh`."""
        venue = self._venue()
        if venue is None:
            return
        try:
            accounts = await self._accounts(venue, fresh=fresh)
        except VenueError as failed:
            log.warning("the catalogue was not published: %s", failed)
            return
        stated_at = _ns(self._now())
        for code, account in sorted(accounts.items()):
            offered = self.catalogue_of(account)
            if self.published.get(code) == offered:
                continue
            await self._plugin.report_venue_catalogue(
                catalogue=meridian.VenueCatalogue(
                    venue_account_code=code,
                    order_types=[
                        meridian.VenueOrderType(
                            order_type=FROM_ORDER_TYPE[each],
                            times_in_force=[FROM_TIME_IN_FORCE[t] for t in tifs],
                            stated_at_ns=stated_at,
                        )
                        for each, tifs in offered
                    ],
                )
            )
            self.published[code] = offered

    # ── Raw records ─────────────────────────────────────────────────────

    def _keep(self, account: Account, calls: list[Json], call: str) -> ops.RawRecordRef:
        """An exchange kept in the plugin's storage, and its reference."""
        key = ""
        if self._raw is not None:
            key = self._raw.keep_one(
                trading_taken(
                    account.code,
                    account.snaptrade_account_id,
                    self._now(),
                    calls,
                    self._synthetic(),
                ),
                self._secrets(),
            )
        reference: ops.RawRecordRef = self._plugin.raw_record(
            f"{account.code}/{key or 'unkept'}/{call}"
        )
        return reference

    # ── Running ─────────────────────────────────────────────────────────

    async def run(self, poll_seconds: Callable[[], int]) -> None:
        """Start, hear placements and instructions, and poll until cancelled."""
        while self._venue() is None:
            await asyncio.sleep(1)
        await self.start()
        hearing = asyncio.create_task(
            self._plugin.receive(
                placement_updated=self.on_placement,
                trading_instruction_changed=self.on_instruction,
                seed=False,
            )
        )
        try:
            while True:
                try:
                    await self.poll()
                    await self.publish()
                except meridian.MeridianError as failed:
                    self.shown.error = str(failed)
                    log.warning("this poll stopped: %s", failed)
                await asyncio.sleep(poll_seconds())
        finally:
            hearing.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hearing


def basis_of(basis: ops.DateBasis) -> meridian.DateBasis:
    """The date read's basis, as the SDK's DateBasis carries it on a fill."""
    rule = basis.rule
    return meridian.DateBasis(
        rule=meridian.DateRuleRef(kind=rule.kind, subject=rule.subject, version=rule.version),
        calendars=list(basis.calendars),
    )


def sent_file(raw: RawStore | None) -> Path | None:
    """Where the placements sent are noted: beside the raw records."""
    return raw.root.parent / "trading" / "sent.json" if raw is not None else None
