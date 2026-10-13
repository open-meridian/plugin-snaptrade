"""SnapTrade, behind the small interface the rest of the plugin uses.

`Venue` is what the plugin asks of SnapTrade: the SnapTrade users under the
key, the user's brokerage connections (SnapTrade calls them brokerage
authorizations), their accounts, each account's positions and balances, a
Connection Portal link, and a refresh. Answers are SnapTrade's JSON as its API
documents it, with every JSON number read as a `Decimal` from its text and
never as a float: a quantity or amount is exact from the first moment this
process holds it (decisions/023).

Two implementations: `SnapTradeVenue` here, the only module that imports
SnapTrade's SDK, and `synthetic.SyntheticVenue`, which serves built-in
responses of the same shape. Tests substitute either.

**Trading** (contract v22; trading.py). An order is placed through
SnapTrade's `POST /trade/place` (`place`), carrying the placement's own ID as
its client order ID; an account's orders are read through `GET
/accounts/{accountId}/orders` (`orders`), each as SnapTrade's order record;
and a cancel is `POST /accounts/{accountId}/trading/cancel` (`cancel`). A
call SnapTrade answered with a refusal (a 4xx) is a `VenueError` that was
`answered`, with SnapTrade's own error code and words in `said`, credentials
never among them; one it did not answer (no reply, a 5xx) is not, and the
plugin then asks SnapTrade what became of the order, never sending it again.
Enabling trading on a connection (step 5's Q10) is the Connection Portal
opened to reconnect it with trading access (`connection_portal(...,
trade=True)`).

Credentials never leave this module except as SnapTrade's SDK sends them. A
failed call is described by what was asked, the exception's type and the HTTP
status, and nothing else: the SDK sends the user secret as a query parameter,
so its exception text and its traceback can carry it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from .settings import Credentials

Json = dict[str, Any]

# How far back the activities are read for trades not yet settled: a
# settlement cycle and a long weekend.
ACTIVITIES_DAYS = 10
# The most activities SnapTrade answers in one page of an account's history.
MOST_PER_PAGE = 1000


class VenueError(Exception):
    """A call to SnapTrade failed. Its text is safe to log and show.

    `answered` is whether SnapTrade answered it with a refusal (an HTTP status
    from 400 to 499), and `said` SnapTrade's own `code` and `detail` from that
    answer, where it gave them: the venue's words, kept as reported and never
    a credential (each is redacted by raw.py before it is kept or sent)."""

    def __init__(
        self,
        asked: str,
        failed: BaseException | None = None,
        said: Json | None = None,
        status: int | None = None,
    ) -> None:
        self.asked = asked
        self.status = (
            status if status is not None else (_status(failed) if failed is not None else None)
        )
        self.said: Json = dict(said) if said is not None else _said(failed)
        kind = type(failed).__name__ if failed is not None else "no answer"
        text = f"{asked} failed: {kind}"
        if self.status is not None:
            text += f" (HTTP {self.status})"
        super().__init__(text)

    @property
    def answered(self) -> bool:
        """SnapTrade answered and refused: never to be read as no answer."""
        return self.status is not None and 400 <= self.status < 500


def _said(failed: BaseException | None) -> Json:
    """SnapTrade's `code` and `detail` from a refusal's body, each a short
    string, or nothing: the body is read only for those two fields."""
    if failed is None:
        return {}
    for holder in (failed, getattr(failed, "api_response", None)):
        body = getattr(holder, "body", None)
        raw = getattr(getattr(holder, "response", None), "data", None)
        for candidate in (body, raw):
            if isinstance(candidate, (bytes, str)) and candidate:
                try:
                    candidate = parse_exact(candidate)
                except ValueError:
                    continue
            if isinstance(candidate, dict):
                kept = {
                    name: str(candidate[name])[:200]
                    for name in ("code", "detail")
                    if isinstance(candidate.get(name), (str, int))
                }
                if kept:
                    return kept
    return {}


def _status(failed: BaseException) -> int | None:
    for holder in (failed, getattr(failed, "api_response", None)):
        status = getattr(holder, "status", None)
        if isinstance(status, int) and not isinstance(status, bool):
            return status
    return None


class Venue(Protocol):
    """What the plugin asks of SnapTrade."""

    async def users(self) -> list[str]:
        """The SnapTrade user IDs registered under the key."""
        ...

    async def connections(self) -> list[Json]:
        """The configured user's brokerage authorizations."""
        ...

    async def accounts(self) -> list[Json]:
        """Every account across those connections."""
        ...

    async def positions(self, account_id: str) -> Json:
        """`GET /accounts/{accountId}/positions/all`: `results` and `data_freshness`."""
        ...

    async def balances(self, account_id: str) -> list[Json]:
        """`GET /accounts/{accountId}/balances`: one balance per currency."""
        ...

    async def activities(self, account_id: str, start: date, end: date) -> list[Json]:
        """`GET /accounts/{accountId}/activities`: the account's activities
        dated from `start` to `end`, each with its trade and settlement dates
        (contract v11: what the settled and pending quantities come from)."""
        ...

    async def activity_page(
        self, account_id: str, start: date | None, end: date, offset: int, limit: int
    ) -> ActivityPage:
        """One page of `GET /accounts/{accountId}/activities`: the account's
        activities dated from `start` (from the first SnapTrade holds, where
        None) to `end`, `limit` of them from `offset`, as SnapTrade answers
        them, with the total it says there are."""
        ...

    async def connection_portal(self, reconnect: str | None = None, trade: bool = False) -> str:
        """A Connection Portal link: to connect a brokerage, or to reconnect
        one; with `trade`, to reconnect one with trading access (step 5's Q10:
        a connection is read-only until trading is enabled on it)."""
        ...

    async def place(self, account_id: str, asked: OrderAsked) -> Json:
        """`POST /trade/place`: the order placed, SnapTrade's order record of it."""
        ...

    async def orders(self, account_id: str) -> list[Json]:
        """`GET /accounts/{accountId}/orders`: the account's orders of the
        last `ORDER_DAYS`, every state, each as SnapTrade's order record."""
        ...

    async def cancel(self, account_id: str, brokerage_order_id: str) -> Json:
        """`POST /accounts/{accountId}/trading/cancel`: SnapTrade's answer."""
        ...

    async def refresh(self, connection_id: str) -> str:
        """Ask SnapTrade to read a connection's brokerage again; its confirmation.

        Only a connection SnapTrade serves on a delay benefits, and each call
        may be charged. On a Real-time plan SnapTrade refuses one for a
        real-time connection: a VenueError whose `status` is 403."""
        ...


#: How far back an account's orders are read: a good-till-cancelled order
#: SnapTrade still works, and the fills and cancels of the month.
ORDER_DAYS = 30


@dataclass(frozen=True)
class OrderAsked:
    """One order as SnapTrade's `POST /trade/place` takes it, in its own words:
    `action` BUY or SELL, `order_type` Market, Limit, Stop or StopLimit,
    `time_in_force` Day, GTC, FOK, IOC or GTD, the ticker SnapTrade names it
    by, the units and prices as Decimals, and the client order ID, which is
    the placement's own (W11.10's identifiers)."""

    action: str
    order_type: str
    time_in_force: str
    symbol: str
    units: Decimal
    client_order_id: str
    price: Decimal | None = None
    stop: Decimal | None = None
    expiry_date: date | None = None

    def body(self) -> Json:
        """The request as SnapTrade's API documents it, numbers as their text."""
        body: Json = {
            "action": self.action,
            "order_type": self.order_type,
            "time_in_force": self.time_in_force,
            "symbol": self.symbol,
            "units": str(self.units),
            "client_order_id": self.client_order_id,
        }
        if self.price is not None:
            body["price"] = str(self.price)
        if self.stop is not None:
            body["stop"] = str(self.stop)
        if self.expiry_date is not None:
            body["expiry_date"] = self.expiry_date.isoformat()
        return body


@dataclass(frozen=True)
class ActivityPage:
    """One page of an account's activities: each as SnapTrade reports it,
    the total it says the range holds (None where it says none), and its
    answer whole, as received, for the raw record."""

    activities: list[Json]
    total: int | None
    body: Any


def activity_page_of(body: Any) -> ActivityPage:
    """A page from SnapTrade's answer: paged as `{"data": [...],
    "pagination": {"total": n}}`, or a plain list, as its API has it."""
    data = body.get("data") if isinstance(body, dict) else body
    total: int | None = None
    if isinstance(body, dict):
        said = body.get("pagination")
        given = said.get("total") if isinstance(said, dict) else None
        if isinstance(given, int) and not isinstance(given, bool) and given >= 0:
            total = given
    return ActivityPage(_objects(data), total, body)


@dataclass(frozen=True)
class Snapshot:
    """One read of SnapTrade: everything the plugin knows until the next."""

    read_at: datetime
    connections: list[Json]
    accounts: list[Json]
    positions: dict[str, Json] = field(default_factory=dict)
    balances: dict[str, list[Json]] = field(default_factory=dict)
    # By SnapTrade account ID: why that account's positions or balances could
    # not be read this time. Its statement is not recorded.
    failures: dict[str, str] = field(default_factory=dict)
    # By SnapTrade account ID: its recent activities; absent where they
    # could not be read, which the settled quantities then say.
    activities: dict[str, list[Json]] = field(default_factory=dict)
    # By SnapTrade account ID: why its activities could not be read.
    unread_activities: dict[str, str] = field(default_factory=dict)


async def read(venue: Venue, now: Callable[[], datetime]) -> Snapshot:
    """Read every connection and account, then each account's positions and
    balances. A connection or account list that cannot be read fails the whole
    read; one account that cannot be read is noted and the rest go on."""
    read_at = now()
    connections = await venue.connections()
    accounts = await venue.accounts()
    positions: dict[str, Json] = {}
    balances: dict[str, list[Json]] = {}
    failures: dict[str, str] = {}
    activities: dict[str, list[Json]] = {}
    unread: dict[str, str] = {}
    for account in accounts:
        account_id = str(account.get("id", ""))
        try:
            positions[account_id] = await venue.positions(account_id)
            balances[account_id] = await venue.balances(account_id)
        except VenueError as failed:
            failures[account_id] = str(failed)
            continue
        try:
            activities[account_id] = await venue.activities(
                account_id,
                (read_at - timedelta(days=ACTIVITIES_DAYS)).date(),
                read_at.date(),
            )
        except VenueError as failed:
            # Not a reason to withhold: the settled quantities say they
            # could not be read from SnapTrade.
            unread[account_id] = str(failed)
    return Snapshot(
        read_at, connections, accounts, positions, balances, failures, activities, unread
    )


def parse_exact(text: str | bytes) -> Any:
    """SnapTrade's JSON with every number that has a fraction or an exponent
    read as a Decimal from its text. Integers stay ints, which are exact."""
    return json.loads(text, parse_float=Decimal)


def exact_body(response: Any) -> Any:
    """The body of an SDK response, re-read exactly from the bytes received.

    The SDK decodes JSON numbers to floats. Its raw urllib3 response keeps the
    bytes, and those are what is parsed here. Should they be unavailable, the
    SDK's own body is returned, and a float in it is converted by the
    normaliser's single rule, Decimal(repr(value)).
    """
    raw = getattr(getattr(response, "response", None), "data", None)
    if isinstance(raw, (bytes, str)) and raw:
        return parse_exact(raw)
    return getattr(response, "body", None)


def _sdk_for(credentials: Credentials) -> Any:
    # Imported here so that nothing else in the plugin, synthetic mode
    # included, loads SnapTrade's SDK.
    from snaptrade_client import SnapTrade, SnapTradeAuth

    make = (
        SnapTradeAuth.personal_api_key
        if credentials.personal
        else (SnapTradeAuth.commercial_api_key)
    )
    return SnapTrade(
        auth=make(consumer_key=credentials.consumer_key, client_id=credentials.client_id)
    )


class SnapTradeVenue:
    """SnapTrade itself, through its official Python SDK.

    The SDK is synchronous; each call runs in a worker thread so the plugin's
    event loop, and its heartbeat, carry on meanwhile.
    """

    def __init__(self, credentials: Credentials, sdk: Any | None = None) -> None:
        self._credentials = credentials
        self._sdk = sdk if sdk is not None else _sdk_for(credentials)

    def __repr__(self) -> str:
        return "SnapTradeVenue(personal)" if self._credentials.personal else "SnapTradeVenue()"

    def _user(self) -> dict[str, str]:
        if self._credentials.personal:
            return {}
        return {
            "user_id": self._credentials.user_id,
            "user_secret": self._credentials.user_secret,
        }

    async def _call(self, asked: str, call: Callable[[], Any]) -> Any:
        try:
            response = await asyncio.to_thread(call)
        except Exception as failed:
            # `from None`: the SDK's exception, its message and its traceback
            # can each carry the request URL, and the URL the user secret.
            raise VenueError(asked, failed) from None
        return exact_body(response)

    async def users(self) -> list[str]:
        body = await self._call(
            "listing SnapTrade users", self._sdk.authentication.list_snap_trade_users
        )
        return [str(user) for user in body] if isinstance(body, list) else []

    async def connections(self) -> list[Json]:
        body = await self._call(
            "listing connections",
            lambda: self._sdk.connections.list_brokerage_authorizations(**self._user()),
        )
        return _objects(body)

    async def accounts(self) -> list[Json]:
        body = await self._call(
            "listing accounts",
            lambda: self._sdk.account_information.list_user_accounts(**self._user()),
        )
        return _objects(body)

    async def positions(self, account_id: str) -> Json:
        body = await self._call(
            "reading positions",
            lambda: self._sdk.account_information.get_all_account_positions(
                account_id=account_id, **self._user()
            ),
        )
        if not isinstance(body, dict):
            raise VenueError("reading positions")
        return body

    async def balances(self, account_id: str) -> list[Json]:
        body = await self._call(
            "reading balances",
            lambda: self._sdk.account_information.get_user_account_balance(
                account_id=account_id, **self._user()
            ),
        )
        return _objects(body)

    async def activities(self, account_id: str, start: date, end: date) -> list[Json]:
        def call() -> Any:
            ask = getattr(self._sdk.account_information, "get_account_activities", None)
            if ask is None:
                raise AttributeError("this SnapTrade SDK reads no account activities")
            return ask(
                account_id=account_id,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                **self._user(),
            )

        body = await self._call("reading activities", call)
        # Listed, or paged as {"data": [...]}, as SnapTrade's API has it.
        if isinstance(body, dict):
            body = body.get("data")
        return _objects(body)

    async def activity_page(
        self, account_id: str, start: date | None, end: date, offset: int, limit: int
    ) -> ActivityPage:
        def call() -> Any:
            ask = getattr(self._sdk.account_information, "get_account_activities", None)
            if ask is None:
                raise AttributeError("this SnapTrade SDK reads no account activities")
            return ask(
                account_id=account_id,
                **({"start_date": start.isoformat()} if start is not None else {}),
                end_date=end.isoformat(),
                offset=offset,
                limit=limit,
                **self._user(),
            )

        return activity_page_of(await self._call("reading activities", call))

    async def connection_portal(self, reconnect: str | None = None, trade: bool = False) -> str:
        body = await self._call(
            "opening the Connection Portal",
            lambda: self._sdk.authentication.login_snap_trade_user(
                # Read-only unless trading is being enabled on a connection
                # (step 5's Q10): a new connection never trades.
                connection_type="trade" if trade else "read",
                **({"reconnect": reconnect} if reconnect else {}),
                **self._user(),
            ),
        )
        link = body.get("redirectURI") if isinstance(body, dict) else None
        if not isinstance(link, str) or not link:
            raise VenueError("opening the Connection Portal")
        return link

    async def refresh(self, connection_id: str) -> str:
        body = await self._call(
            "refreshing a connection",
            lambda: self._sdk.connections.refresh_brokerage_authorization(
                authorization_id=connection_id, **self._user()
            ),
        )
        detail = body.get("detail") if isinstance(body, dict) else None
        return detail if isinstance(detail, str) else "SnapTrade will read it again."

    async def place(self, account_id: str, asked: OrderAsked) -> Json:
        # SnapTrade's SDK takes its numbers as floats: each is given from its
        # Decimal's text, which a price or a unit count of the precision a
        # brokerage takes survives exactly (JSON writes a float's shortest
        # text). What SnapTrade answered is re-read exactly.
        def number(value: Decimal | None) -> float | None:
            return None if value is None else float(str(value))

        body = await self._call(
            "placing an order",
            lambda: self._sdk.trading.place_force_order(
                account_id=account_id,
                action=asked.action,
                order_type=asked.order_type,
                time_in_force=asked.time_in_force,
                symbol=asked.symbol,
                units=number(asked.units),
                price=number(asked.price),
                stop=number(asked.stop),
                client_order_id=asked.client_order_id,
                **(
                    {
                        "expiry_date": datetime.combine(
                            asked.expiry_date, datetime.min.time(), UTC
                        )
                    }
                    if asked.expiry_date is not None
                    else {}
                ),
                **self._user(),
            ),
        )
        if not isinstance(body, dict):
            raise VenueError("placing an order")
        return body

    async def orders(self, account_id: str) -> list[Json]:
        body = await self._call(
            "reading orders",
            lambda: self._sdk.account_information.get_user_account_orders(
                account_id=account_id, state="all", days=ORDER_DAYS, **self._user()
            ),
        )
        return _objects(body)

    async def cancel(self, account_id: str, brokerage_order_id: str) -> Json:
        body = await self._call(
            "cancelling an order",
            lambda: self._sdk.trading.cancel_order(
                account_id=account_id, brokerage_order_id=brokerage_order_id, **self._user()
            ),
        )
        return body if isinstance(body, dict) else {}


def _objects(body: Any) -> list[Json]:
    return [item for item in body if isinstance(item, dict)] if isinstance(body, list) else []


def utc_now() -> datetime:
    return datetime.now(UTC)
