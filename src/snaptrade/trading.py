"""SnapTrade as a connectivity plugin (contract v22, v23's venue-originated
fills, v26's environment; meridian-design plans/an-order-reaches-a-venue-
through-the-book; W11.8 to W11.12, W11.20, W11.23, W11.24), on the SDK's
`meridian.roles.connectivity` (ruling 213).

**The SDK's** (`connectivity.Trader`): what every connectivity plugin does
the same, whatever its venue. It hears each placement naming its instance
and no other's; reads its order and its instrument's record; places it once
(noted in storage before the send, so a restart asks about it and never
sends it twice); rejects `unsupported`, nothing sent, what the account or
its catalogue does not offer (a read-only connection, an order type or time
in force not listed, a short sale, a strategy, an instrument with no ticker,
a code this plugin holds no account for); reports what the venue did in the
contract's meanings, every fill and bust, and every charge the venue states
apart from its orders; holds what a pause or a stop covers and cancels under
a stop; answers the `ems`'s cancels and replaces; publishes the catalogue;
and reports the fills of an order placed in the broker's own app on no
placement, adopting the placement an accept creates (`Watched`). How it
does each is the SDK's module's documentation.

**SnapTrade's** (`TradingVenue`, the Trader's venue, and this module's
`Trader`): its words read into the SDK's and back, nothing else.

- *Its trading API*: an order is placed through `POST /trade/place` with the
  placement's ID as its client order ID, an account's orders read through
  `GET /accounts/{accountId}/orders`, a cancel and a replace sent at
  `POST /accounts/{accountId}/trading/cancel` and `.../replace`, each request
  and answer kept as a trading record of the account (raw.py). A refusal
  SnapTrade answered (a 4xx) is the venue refusing, its code and words as
  reported; no answer (none, a 5xx) is an unknown outcome, resolved from the
  account's orders by the client order ID.
- *Its statuses* (`STATUSES`): working while `ACCEPTED`, `PENDING`,
  `QUEUED`, `PARTIAL`, `TRIGGERED`, `ACTIVATED`, `PENDING_RISK_REVIEW`,
  `CONTINGENT_ORDER`, `CANCEL_PENDING` or `REPLACE_PENDING`; `EXECUTED`
  filled; `CANCELED` and `PARTIAL_CANCELED` cancelled; `REJECTED` and
  `FAILED` rejected; `EXPIRED`; `SUSPENDED`; `REPLACED` replaced by the order
  carrying its client order ID after it. Any other is outcome unknown, the
  status as reported.
- *Fills*: SnapTrade states an order's filled quantity and average price, not
  each fill, so each is inferred from the two averages (kind `inferred`),
  its trade date the day its time falls on in the listing exchange's zone as
  SnapTrade states it (else UTC). Where the date read answers no settlement
  date, the one SnapTrade states is sent: its `BUY` or `SELL` activity naming
  the order (`stated_settlement`).
- *Charges* (`charges`): a `FEE` activity naming an order is a fee; the `fee`
  of a `BUY` or `SELL` activity naming one is its commission.
- *Trading enabled* per connection (step 5's Q10): a connection is read-only
  until it is reconnected with trading access (the Connections page's
  "Enable trading"), so trading is on for an account whose connection
  SnapTrade lists as `trade` and not disabled (`Trader.trading_on`).
- *Its catalogue*: what `POST /trade/place` takes, or a synthetic
  brokerage's own (synthetic_orders.py); none on a read-only connection.
  SnapTrade takes no execution acknowledgement (BN) and no DontKnowTrade
  (Q), as the SDK's catalogue says.
- *A good-till-date order's date*: the order's expire date, sent with it.
- *The listing a fill on no placement names*: what SnapTrade states of an
  order's listing beyond its symbol -- its FIGI, and the currency its price
  is in -- is read onto the order (`VenueOrder.identifiers`, `.currency`),
  and the SDK resolves an order placed in the broker's own app by all of
  them and its symbol under SnapTrade's own source (0.32.0), since SnapTrade
  spans many brokerages and exchanges and a bare symbol can name several
  share classes or listings; the fill is priced in the stated currency, else
  the record's. Where they name no record, it is reported missing and the
  fill waits. (0.19.0 did this in its own code; the SDK's is the same.)
- *Its placement types* (contract v27, W11.20): DMA alone
  (`PLACEMENT_TYPES`, beside DMA none). `POST /trade/place` sends an order
  straight to the brokerage: SnapTrade works no broker algorithm, takes no
  care order for a broker's desk and asks no dealer for a quote, so an algo,
  care or RFQ placement is rejected `unsupported`, nothing sent.
- *Its environment* (contract v26, W11.20; G1 and question M4): SnapTrade
  has no demo environment -- no sandbox host, no paper connection -- so every
  connection is a brokerage's live account. Its catalogue reports `live`
  (`ENVIRONMENT`), and the agents' demo-only guard, which counts only
  `sandbox` and `paper` as demo, refuses an agent's placement on it.

What it does not do in this version: a locate (SnapTrade has no locate API),
a place of execution (SnapTrade states none), an execution acknowledgement
(SnapTrade takes none), a value-date roll (its trades settle; nothing rolls).
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from meridian.plugin.v1 import operations_pb2 as ops
from meridian.roles import connectivity
from meridian.roles.connectivity import (
    Asked,
    Catalogue,
    Change,
    VenueCharge,
    VenueOrder,
    VenueRefused,
    VenueUnanswered,
)

from .normalise import SOURCE, external_account
from .not_carried import seen_in_order
from .raw import (
    ACTIVITIES,
    CANCEL,
    OFFERED,
    ORDERS,
    PLACE,
    REPLACE,
    RawStore,
    trading_call,
    trading_failed,
    trading_taken,
)
from .synthetic_orders import SyntheticTrading
from .venue import Json, OrderAsked, ReplaceAsked, Venue, VenueError

log = logging.getLogger("snaptrade.trading")

#: How often SnapTrade is asked about working orders: real SnapTrade allows
#: an account ten requests a minute; the simulated brokerage none.
POLL_SECONDS = 15
SYNTHETIC_POLL_SECONDS = 1

#: The environment every SnapTrade connection is in (contract v26): SnapTrade
#: offers no demo environment, so its session reports live, and an agent's
#: placement on it is refused while the agents' guard is on.
ENVIRONMENT = "live"

#: How long SnapTrade's accounts and connections are held before they are
#: read again; a queued placement reads them afresh.
ACCOUNTS_READ_AGAIN = timedelta(minutes=1)
#: How far back the activities are read for an ended order's charges, and
#: either side of a fill's trade date for the settlement date stated.
CHARGE_DAYS = 7
SETTLEMENT_DAYS = 2

#: What SnapTrade's `POST /trade/place` takes, the catalogue of an account
#: whose connection has trading enabled.
PLACE_ORDER_TYPES = ("Market", "Limit", "Stop", "StopLimit")
PLACE_TIMES_IN_FORCE = ("Day", "GTC", "FOK", "IOC", "GTD")

ORDER_TYPES: dict[int, str] = {
    ops.ORDER_TYPE_MARKET: "Market",
    ops.ORDER_TYPE_LIMIT: "Limit",
    ops.ORDER_TYPE_STOP: "Stop",
    ops.ORDER_TYPE_STOP_LIMIT: "StopLimit",
}
TIMES_IN_FORCE: dict[int, str] = {
    ops.TIME_IN_FORCE_DAY: "Day",
    ops.TIME_IN_FORCE_GOOD_TILL_CANCELLED: "GTC",
    ops.TIME_IN_FORCE_IMMEDIATE_OR_CANCEL: "IOC",
    ops.TIME_IN_FORCE_FILL_OR_KILL: "FOK",
    ops.TIME_IN_FORCE_GOOD_TILL_DATE: "GTD",
}
#: The placement types SnapTrade takes beside DMA (contract v27): none.
#: `POST /trade/place` is direct market access through the brokerage --
#: no broker algorithm, care desk or request for quote.
PLACEMENT_TYPES: tuple[int, ...] = ()

#: The sides SnapTrade places: buys and sells, no short sale.
ACTIONS: dict[int, str] = {ops.SIDE_BUY: "BUY", ops.SIDE_SELL: "SELL"}
FROM_ORDER_TYPE: dict[str, int] = {said: ours for ours, said in ORDER_TYPES.items()}
FROM_TIME_IN_FORCE: dict[str, int] = {said: ours for ours, said in TIMES_IN_FORCE.items()}

#: SnapTrade's order statuses by the SDK's categories (above).
STATUSES: dict[str, str] = {
    **{
        status: connectivity.WORKING
        for status in (
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
        )
    },
    "EXECUTED": connectivity.FILLED,
    "CANCELED": connectivity.CANCELLED,
    "PARTIAL_CANCELED": connectivity.CANCELLED,
    "REJECTED": connectivity.REJECTED,
    "FAILED": connectivity.REJECTED,
    "EXPIRED": connectivity.EXPIRED,
    "SUSPENDED": connectivity.SUSPENDED,
    "REPLACED": connectivity.REPLACED,
}

#: The contract's side for each of SnapTrade's order actions on a security,
#: for a fill on no placement (contract v23); any other action (an option's
#: opening or closing) is not reported.
SIDES_SAID: dict[str, int] = {
    "BUY": ops.SIDE_BUY,
    "BUY_COVER": ops.SIDE_BUY,
    "SELL": ops.SIDE_SELL,
    "SELL_SHORT": ops.SIDE_SELL_SHORT,
}


def _dict(value: Any) -> Json:
    return value if isinstance(value, dict) else {}


def _zone(record: Json) -> tzinfo | None:
    """The listing exchange's zone, as SnapTrade states it on an order."""
    zone = _dict(_dict(record.get("universal_symbol")).get("exchange")).get("timezone")
    if isinstance(zone, str) and zone:
        with contextlib.suppress(ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(zone)
    return None


def order_of(record: Json) -> VenueOrder:
    """One of SnapTrade's order records, as the Trader reads it."""
    return VenueOrder(
        order_id=str(record.get("brokerage_order_id") or ""),
        client_order_id=str(record.get("client_order_id") or ""),
        status=str(record.get("status") or ""),
        symbol=str(_dict(record.get("universal_symbol")).get("symbol") or ""),
        side=str(record.get("action") or ""),
        quantity=connectivity.number(record.get("total_quantity")),
        filled=connectivity.number(record.get("filled_quantity")) or Decimal(0),
        average=connectivity.number(record.get("execution_price")),
        limit=connectivity.number(record.get("limit_price")),
        placed_at=connectivity.moment(record.get("time_placed")),
        updated_at=connectivity.moment(record.get("time_updated")),
        filled_at=connectivity.moment(record.get("time_executed")),
        market_zone=_zone(record),
        # What SnapTrade states of the listing beyond its symbol (SDK 0.32.0
        # resolves a venue-originated order by all of it): its FIGI, and the
        # currency its price is in.
        identifiers=_identifiers(record),
        currency=_currency(record),
    )


def _currency(record: Json) -> str:
    """The currency SnapTrade states an order's listing is priced in, "" for
    none."""
    symbol = _dict(record.get("universal_symbol"))
    return str(_dict(symbol.get("currency")).get("code") or "")


def _identifiers(record: Json) -> tuple[ops.Identifier, ...]:
    """Every identifier SnapTrade states for an order's listing beside its
    symbol: its FIGI, where it states one."""
    figi = str(_dict(record.get("universal_symbol")).get("figi_code") or "")
    return (ops.Identifier(scheme="figi", value=figi),) if figi else ()


def orders_of(records: Sequence[Json]) -> list[VenueOrder]:
    """An account's orders as the Trader reads them: an order SnapTrade
    replaced (`REPLACED`) names the order carrying its client order ID after
    it, which names it back, as SnapTrade's replace leaves them."""
    read = [order_of(record) for record in records]
    chains: dict[str, list[int]] = {}
    for at, order in enumerate(read):
        if order.client_order_id and order.order_id:
            chains.setdefault(order.client_order_id, []).append(at)
    for chain in chains.values():
        chain.sort(key=lambda at: (read[at].placed_at or datetime.min.replace(tzinfo=UTC), at))
        for before, after in zip(chain, chain[1:], strict=False):
            if read[before].status != "REPLACED":
                continue
            read[before] = replace(read[before], replaced_by=read[after].order_id)
            read[after] = replace(read[after], replaces=read[before].order_id)
    return read


@dataclass(frozen=True)
class Account:
    """One SnapTrade account, as a placement's venue account code names it:
    the external account ID custody reports it under."""

    code: str
    snaptrade_account_id: str
    connection: Json
    trading: bool
    slug: str


class TradingVenue:
    """The Trader's venue: SnapTrade's trading API, in its words, for the
    accounts the configured SnapTrade user's connections reach."""

    statuses: dict[str, str] = STATUSES
    sides: dict[int, str] = ACTIONS
    sides_said: dict[str, int] = SIDES_SAID
    source: str = SOURCE
    #: The environment its session reached (contract v26): always live.
    environment: str = ENVIRONMENT

    def __init__(
        self,
        plugin: Any,
        venue: Callable[[], Venue | None],
        *,
        now: Callable[[], datetime],
        raw: RawStore | None = None,
        synthetic: Callable[[], bool] = lambda: False,
        secrets: Callable[[], Sequence[str]] = lambda: (),
    ) -> None:
        self._plugin = plugin
        self._venue = venue
        self._now = now
        self._raw = raw
        self._synthetic = synthetic
        self._secrets = secrets
        #: A good-till-date placement's expire date, by its ID, until sent.
        self.expiries: dict[str, date] = {}
        # SnapTrade's accounts by their codes, the venue they were read from
        # and when (None to read them again at the next ask); and each
        # account's orders and charges as last kept, by the answer's text, so
        # an unchanged answer is kept once.
        self._accounts: dict[str, Account] = {}
        self._read: tuple[Venue, datetime] | None = None
        self._kept: dict[tuple[str, str], tuple[str, str]] = {}

    def _snaptrade(self) -> Venue:
        held = self._venue()
        if held is None:
            raise VenueUnanswered("SnapTrade's settings are not given yet", "")
        return held

    # ── Accounts and the catalogue ──────────────────────────────────────

    def forget_accounts(self) -> None:
        """Read SnapTrade's accounts afresh at the next ask: a placement may
        name one on a connection trading was enabled on a moment ago. Those
        read last stand until then."""
        self._read = None

    async def accounts(self) -> tuple[str, ...]:
        """The venue account codes SnapTrade's accounts are named by, read
        again at most once a minute."""
        return tuple(sorted(await self._read_accounts()))

    async def _read_accounts(self) -> dict[str, Account]:
        venue = self._snaptrade()
        now = self._now()
        if self._read is not None:
            held_from, read_at = self._read
            if held_from is venue and now - read_at < ACCOUNTS_READ_AGAIN:
                return self._accounts
        try:
            connections = {str(c.get("id", "")): c for c in await venue.connections()}
            listed = await venue.accounts()
        except VenueError as failed:
            raise _refused_or_unanswered(failed, "", "SnapTrade refused the read") from None
        held: dict[str, Account] = {}
        for each in listed:
            connection = connections.get(str(each.get("brokerage_authorization", "")), {})
            identity = external_account(each, connection)
            held[identity.external_account_id] = Account(
                code=identity.external_account_id,
                snaptrade_account_id=identity.snaptrade_account_id,
                connection=connection,
                trading=connection.get("type") == "trade" and not connection.get("disabled"),
                slug=str(_dict(connection.get("brokerage")).get("slug") or ""),
            )
        self._accounts, self._read = held, (venue, now)
        return held

    def account(self, code: str) -> Account | None:
        """The account a code names, as last read."""
        return self._accounts.get(code)

    def trading(self, code: str) -> bool:
        """Whether trading is enabled on the account's connection."""
        held = self.account(code)
        return held is not None and held.trading

    def placement_types(self, code: str) -> tuple[int, ...]:
        """The placement types SnapTrade takes beside DMA: none
        (`PLACEMENT_TYPES`). The SDK lists DMA on an account with trading
        enabled, and none on a read-only one."""
        return PLACEMENT_TYPES

    def catalogue(self, code: str) -> Catalogue:
        """What SnapTrade offers on an account: what its `POST /trade/place`
        takes, or a synthetic brokerage what it states."""
        held = self.account(code)
        if held is None:
            return ()
        trading = getattr(self._venue(), "trading", None)
        if isinstance(trading, SyntheticTrading):
            types, tifs = trading.offered(held.slug)
        else:
            types, tifs = PLACE_ORDER_TYPES, PLACE_TIMES_IN_FORCE
        return tuple(
            (FROM_ORDER_TYPE[each], tuple(FROM_TIME_IN_FORCE[t] for t in tifs))
            for each in types
        )

    # ── Orders ──────────────────────────────────────────────────────────

    async def _held(self, code: str) -> Account:
        """The account a code names, read where it was not yet."""
        held = self.account(code)
        if held is None:
            held = (await self._read_accounts()).get(code)
        if held is None:
            raise VenueRefused(
                "", "SnapTrade holds no account under this venue account code", ""
            )
        return held

    async def place(self, code: str, asked: Asked) -> tuple[VenueOrder, str]:
        held = await self._held(code)
        expiry = self.expiries.pop(asked.placement_id, None)
        said = OrderAsked(
            action=ACTIONS[asked.side],
            order_type=ORDER_TYPES[asked.order_type],
            time_in_force=TIMES_IN_FORCE[asked.time_in_force],
            symbol=asked.symbol,
            units=asked.quantity,
            client_order_id=asked.placement_id,
            price=asked.limit,
            stop=asked.stop,
            expiry_date=expiry
            if asked.time_in_force == ops.TIME_IN_FORCE_GOOD_TILL_DATE
            else None,
        )
        body = json.dumps(said.body())
        try:
            answer = await self._snaptrade().place(held.snaptrade_account_id, said)
        except VenueError as failed:
            kept = trading_failed(PLACE, str(failed), failed.said or None)
            kept["note"] = body
            key = self._keep(held, [kept], "place")
            raise _refused_or_unanswered(failed, key, "SnapTrade refused the order") from None
        return order_of(answer), self._keep(held, [trading_call(PLACE, answer, body)], "place")

    async def orders(self, code: str) -> tuple[list[VenueOrder], str]:
        """An account's orders, kept whenever SnapTrade's answer changed."""
        held = await self._held(code)
        try:
            records = await self._snaptrade().orders(held.snaptrade_account_id)
        except VenueError as failed:
            raise _refused_or_unanswered(failed, "", "SnapTrade refused the read") from None
        said = json.dumps(records, sort_keys=True, default=str)
        kept = self._kept.get((code, "orders"))
        if kept is not None and kept[0] == said:
            key = kept[1]
        else:
            key = self._keep(held, [trading_call(ORDERS, records)], "orders")
            self._kept[(code, "orders")] = (said, key)
            for listed in records:
                for scheme, name in seen_in_order(listed):
                    self._plugin.note_not_carried(scheme, name)
        return orders_of(records), key

    async def cancel(self, code: str, order_id: str) -> str:
        held = await self._held(code)
        try:
            answer = await self._snaptrade().cancel(held.snaptrade_account_id, order_id)
        except VenueError as failed:
            key = self._keep(
                held, [trading_failed(CANCEL, str(failed), failed.said or None)], "cancel"
            )
            raise _refused_or_unanswered(failed, key, "SnapTrade refused the change") from None
        return self._keep(held, [trading_call(CANCEL, answer, order_id)], "cancel")

    def not_replaceable(self, change: Change) -> str:
        if change.side not in ACTIONS or change.order_type not in ORDER_TYPES:
            return "SnapTrade replaces buys and sells of the order types it places"
        if change.time_in_force not in TIMES_IN_FORCE:
            return "SnapTrade takes no such time in force"
        return ""

    async def replace(
        self, code: str, order: VenueOrder | None, order_id: str, change: Change
    ) -> tuple[VenueOrder | None, str]:
        """A replace, SnapTrade's answer the order's record, whose brokerage
        order ID may be new."""
        held = await self._held(code)
        said = ReplaceAsked(
            brokerage_order_id=order_id,
            action=ACTIONS[change.side],
            order_type=ORDER_TYPES[change.order_type],
            time_in_force=TIMES_IN_FORCE[change.time_in_force],
            units=change.quantity,
            price=change.limit,
            stop=change.stop,
        )
        body = json.dumps(said.body())
        try:
            answer = await self._snaptrade().replace(held.snaptrade_account_id, said)
        except VenueError as failed:
            kept = trading_failed(REPLACE, str(failed), failed.said or None)
            kept["note"] = body
            key = self._keep(held, [kept], "replace")
            raise _refused_or_unanswered(failed, key, "SnapTrade refused the change") from None
        return order_of(answer), self._keep(
            held, [trading_call(REPLACE, answer, body)], "replace"
        )

    # ── The activities: charges and a stated settlement date ────────────

    async def charges(self, code: str) -> list[VenueCharge]:
        """The charges an account's activities of the last `CHARGE_DAYS`
        state, each naming an order: a `FEE` activity's amount, a fee; a
        `BUY` or `SELL` activity's `fee`, its commission. Kept whenever they
        changed."""
        held = await self._held(code)
        today = self._now().date()
        try:
            activities = await self._snaptrade().activities(
                held.snaptrade_account_id, today - timedelta(days=CHARGE_DAYS), today
            )
        except VenueError as failed:
            raise _refused_or_unanswered(failed, "", "SnapTrade refused the read") from None
        stated = [(activity, _charge_of(activity)) for activity in activities]
        charging = [(activity, said) for activity, said in stated if said is not None]
        if not charging:
            return []
        records = [activity for activity, _ in charging]
        text = json.dumps(records, sort_keys=True, default=str)
        kept = self._kept.get((code, "activities"))
        if kept is not None and kept[0] == text:
            key = kept[1]
        else:
            key = self._keep(held, [trading_call(ACTIVITIES, records, "charges")], "activities")
            self._kept[(code, "activities")] = (text, key)
        return [
            VenueCharge(
                charge_id=str(activity.get("id")),
                venue_order_id=str(activity.get("external_reference_id")),
                kind=kind,
                amount=amount,
                currency=str(_dict(activity.get("currency")).get("code") or ""),
                trade_date=str(activity.get("trade_date") or "")[:10],
                settlement_date=str(activity.get("settlement_date") or "")[:10],
                raw_key=key,
            )
            for activity, (kind, amount) in charging
        ]

    async def stated_settlement(self, code: str, record: VenueOrder, traded: date) -> str:
        """The settlement date SnapTrade states for an order's fill: its `BUY`
        or `SELL` activity naming the order's brokerage ID, the one traded
        that day first; "" for none."""
        held = self.account(code)
        if held is None:
            return ""
        try:
            activities = await self._snaptrade().activities(
                held.snaptrade_account_id,
                traded - timedelta(days=SETTLEMENT_DAYS),
                traded + timedelta(days=SETTLEMENT_DAYS),
            )
        except VenueError:
            return ""
        named = [
            activity
            for activity in activities
            if str(activity.get("type") or "") in ("BUY", "SELL")
            and str(activity.get("external_reference_id") or "") == record.order_id
            and str(activity.get("settlement_date") or "")
        ]
        named.sort(key=lambda a: str(a.get("trade_date") or "")[:10] != traded.isoformat())
        return str(named[0].get("settlement_date"))[:10] if named else ""

    # ── Raw records ─────────────────────────────────────────────────────

    async def keep(self, name: str, record: Json) -> str:
        """What the Trader decided a placement from before anything was sent
        (`offered`), kept as a trading record of the account it names."""
        stated = _dict(record.get("stated")) or record
        code = str(stated.get("venue_account_code") or "")
        held = self.account(code) or Account(code, "", {}, False, "")
        why = str(stated.get("why") or record.get("why") or "")
        return self._keep(held, [trading_call(OFFERED, stated, why)], "offered")

    def _keep(self, held: Account, calls: list[Json], call: str) -> str:
        """An exchange kept in the plugin's storage, and its record key."""
        key = ""
        if self._raw is not None:
            key = self._raw.keep_trading(
                trading_taken(
                    held.code, held.snaptrade_account_id, self._now(), calls, self._synthetic()
                ),
                self._secrets(),
            )
        return f"{held.code}/{key or 'unkept'}/{call}"


def _charge_of(activity: Json) -> tuple[str, Decimal] | None:
    """The charge an activity states, and its amount: a `FEE` naming an
    order, or the `fee` of a `BUY` or `SELL` naming one; None for none."""
    if not activity.get("id") or not activity.get("external_reference_id"):
        return None
    kind = str(activity.get("type") or "")
    if kind == "FEE":
        said, amount = "fee", connectivity.number(activity.get("amount"))
    elif kind in ("BUY", "SELL"):
        said, amount = "commission", connectivity.number(activity.get("fee"))
    else:
        return None
    return (said, amount) if amount else None


def _refused_or_unanswered(failed: VenueError, key: str, said: str) -> Exception:
    """SnapTrade's refusal (a 4xx), its code and words as reported; or no
    answer, which may have reached it."""
    if failed.answered:
        return VenueRefused(
            str(failed.said.get("code") or failed.status or ""),
            str(failed.said.get("detail") or said),
            key,
        )
    return VenueUnanswered(str(failed), key)


class Trader(connectivity.Trader):
    """The SDK's Trader over SnapTrade: trading is enabled on an account
    whose connection SnapTrade lists with trading access, never by a note in
    storage (the Connections page's "Enable trading" reconnects it); and a
    queued placement reads SnapTrade's accounts afresh and carries its
    order's expire date to a good-till-date order. A venue-originated
    order's listing is the SDK's to resolve (0.32.0), from what `order_of`
    reads onto it."""

    def __init__(
        self,
        plugin: Any,
        venue: Callable[[], TradingVenue | None],
        **settings: Any,
    ) -> None:
        settings.setdefault("market_zone", UTC)
        # The venue's calls are async, which the Trader awaits (0.31.0).
        super().__init__(
            plugin, cast(Callable[[], connectivity.Venue | None], venue), **settings
        )
        self._trading_venue = venue

    def trading_on(self, code: str) -> bool:
        venue = self._trading_venue()
        return venue is not None and venue.trading(code)

    async def on_queued(self, placement: ops.Placement, order: ops.Order | None) -> None:
        venue = self._trading_venue()
        if venue is not None:
            venue.forget_accounts()
            if order is None:
                order = await self._order_named(placement)
            if order is not None and order.expire_date:
                with contextlib.suppress(ValueError):
                    venue.expiries[placement.placement_id] = date.fromisoformat(
                        order.expire_date
                    )
        await super().on_queued(placement, order)

    async def _order_named(self, placement: ops.Placement) -> ops.Order | None:
        listed = await self._plugin.list_orders(account_id=placement.account_id)
        for order in listed.orders:
            if order.order_id == placement.order_id:
                found: ops.Order = order
                return found
        return None


def charged(file: Path | None) -> connectivity.Sent:
    """The charges reported, each once (contract v23), as the SDK notes them
    (`<charge>|<placement>`); one noted by 0.18.0 and earlier
    (`<activity>:<placement>`) is noted again as the SDK's, so an upgrade
    reports none twice. SnapTrade's activity IDs hold no colon."""
    held = connectivity.Sent(file)
    for name in list(held):
        if "|" in name:
            continue
        activity, colon, placement = name.partition(":")
        if colon and activity:
            held.note(f"{activity}|{placement}")
    return held


def storage_base(raw: RawStore | None) -> Path | None:
    """Where the trading files are kept: beside the raw records."""
    return raw.root.parent if raw is not None else None


__all__ = [
    "ENVIRONMENT",
    "PLACEMENT_TYPES",
    "POLL_SECONDS",
    "SYNTHETIC_POLL_SECONDS",
    "Account",
    "Trader",
    "TradingVenue",
    "charged",
    "order_of",
    "orders_of",
    "storage_base",
]
