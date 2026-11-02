"""The SnapTrade plugin: brokerage accounts, cash and positions into an Open
Meridian deployment, through SnapTrade (plans/proving-the-plugin-system, step 1).

It connects to the sidecar it is launched beside, declares its settings and
its pages (page.py: Connections and Account links under Manage, Statements
under Open and View), serves them, and then, on every poll and every settings
change, reads SnapTrade (or, in synthetic mode, its built-in responses),
normalises what it read to the platform's convention (normalise.py), and
records it through the SDK's typed operations (contract.py): each account's
sync status, a holdings statement per account, and each linked account's
activities, a backfill back to its history's first date and then each read's
(contract v14). It holds nothing between
reads that it needs; a restart reads again. What SnapTrade answered each read
is kept per account, as received, for the Raw responses tab (raw.py), in the
storage the deployment grants an edge plugin (decisions/028), which it
declares it asks for with its two kinds of raw record (declaration.py), and
past each kind's window archived, kept or deleted as the settings say
(archive.py, contract v16). Which of its external
accounts are linked, and to what, it reads beside its account scope, holding
the first delivery before its pages are served and each one after
(linking.py).

From contract v22 it is also a connectivity plugin (trading.py, on the SDK's
`meridian.roles.connectivity.Trader` from 0.19.0): it hears each placement
naming its instance, places it at SnapTrade's trading API in the account its
venue account code names, and reports what SnapTrade did with it, polling
SnapTrade for each working order; it honours the trading instructions in
force, and publishes what SnapTrade offers per account, its environment live
(contract v26). Synthetic mode trades with a simulated brokerage
(synthetic_orders.py), never SnapTrade.

Everything goes through the sidecar. The SnapTrade credentials arrive as the
plugin's own secret settings, set by an admin of the plugin in the dashboard,
and are never logged, shown or sent anywhere but SnapTrade.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

import meridian
from meridian import edge
from meridian.errors import NotRegistered
from meridian.roles import Latency, connectivity, custody

from .declaration import DECLARATION, PORT
from .linking import Links, failure
from .page import hold, pages
from .raw import RawStore, storage_root
from .settings import config_from
from .sync import Syncer
from .trading import (
    POLL_SECONDS,
    SYNTHETIC_POLL_SECONDS,
    Trader,
    TradingVenue,
    charged,
    storage_base,
)

log = logging.getLogger("snaptrade")


async def watch_links(scopes: AsyncIterator[meridian.AccountScope], links: Links) -> None:
    """Hold each delivery of the account scope, and so the plugin's links."""
    async for scope in scopes:
        await links.hold(scope)


async def follow_links(plugin: meridian.Plugin, links: Links) -> asyncio.Task[None]:
    """Hold the first delivery of the plugin's links, which comes at once, so
    a page served after this names every link and its account; then hold each
    one after it in a task, which is returned (the pages' tests; the plugin
    itself holds them in its first class's `run`, `Process.follow`)."""
    scopes = plugin.account_scope()
    await links.hold(await anext(scopes))
    return asyncio.create_task(watch_links(scopes, links))


class Process:
    """What the plugin's two role classes share: the syncer and the links the
    pages read, the settings applied, and the account scope followed by the
    first of them started."""

    def __init__(self) -> None:
        self.roles: list[Any] = []
        self.wake = asyncio.Event()
        self.custody = Custody(self)
        self.links = Links(self.custody)
        # The storage the deployment grants it (decisions/028), or the
        # stand-in where none is mounted; and the links, which say whose
        # activities to report (contract v14). Its acts are made through the
        # custody role's class (SDK 0.35).
        self.syncer = Syncer(
            self.custody,
            raw=RawStore(storage_root(), edge.storage_dir()),
            linked=lambda external_account_id: (
                self.links.scope.link_of(external_account_id) is not None
            ),
        )
        self.desk: Any = None
        self.scopes: AsyncIterator[meridian.AccountScope] | None = None

    def connection(self) -> meridian.Plugin:
        """The connection, through any class registered on it."""
        for role in self.roles:
            try:
                held: meridian.Plugin = role.plugin
            except NotRegistered:
                continue
            return held
        raise NotRegistered("no role class of the plugin is registered yet")

    async def on_settings(self, delivered: Any) -> None:
        """Each delivery of the settings, the first before any class starts:
        applied, the custody reads woken, and what is past its window moved
        as the settings now say it (archive.py)."""
        self.syncer.configure(config_from(delivered.values, delivered.missing_required))
        self.wake.set()
        await self.syncer.tend()

    async def started(self, role: Any) -> None:
        """A class started: the first of them holds the first delivery of the
        plugin's links, which comes at once, so a page names every link and
        its account, and follows the scope from then on (`follow`)."""
        if self.desk is not None:
            return
        self.desk = role
        self.scopes = self.connection().account_scope()
        await self.links.hold(await anext(self.scopes))
        log.info("holding %d links to the deployment's accounts", len(self.links.scope.links))

    async def follow(self, role: Any) -> None:
        """Each delivery of the account scope after the first, held, where
        `role` is the class that holds them; a stream that ends is raised,
        so the class's work starts again, the scope asked anew."""
        if role is not self.desk:
            return
        if self.scopes is None:
            self.scopes = self.connection().account_scope()
        scopes, self.scopes = self.scopes, None
        await watch_links(scopes, self.links)
        raise ConnectionError("the account scope's stream ended")


class Custody(custody.Role):
    """The custody role's class (open-meridian 0.35): reads SnapTrade every
    poll, or sooner when woken (`run`), its custody work (sync.py,
    contract.py, linking.py) making each act through it. What that work
    reaches beyond the role -- raw records, what is not carried, the
    plugin's health and its stored records' moves -- it reaches on the
    connection, here."""

    def __init__(self, process: Process) -> None:
        self.process = process

    async def on_start(self) -> None:
        await self.process.started(self)

    async def run(self) -> None:
        await asyncio.gather(self._poll(), self.process.follow(self))

    async def _poll(self) -> None:
        """Read SnapTrade now, then every poll_seconds or sooner when woken."""
        syncer, wake = self.process.syncer, self.process.wake
        while True:
            wake.clear()
            try:
                await syncer.run_once()
            except meridian.MeridianError as failed:
                # The sidecar refused something the read needed; say so and
                # keep the schedule rather than spin.
                log.warning("this read stopped: %s", failure(failed))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=syncer.config.poll_seconds)

    # ── Plugin-wide, on the connection ───────────────────────────────────

    def raw_record(self, key: str) -> Any:
        return self.process.connection().raw_record(key)

    def note_not_carried(self, scheme: str, name: str) -> None:
        self.process.connection().note_not_carried(scheme, name)

    async def report(self, **said: Any) -> Any:
        return await self.process.connection().report(**said)

    async def archive_unit(self, *args: Any, **kwargs: Any) -> Any:
        return await self.process.connection().archive_unit(*args, **kwargs)

    async def delete_unit(self, *args: Any, **kwargs: Any) -> Any:
        return await self.process.connection().delete_unit(*args, **kwargs)

    def find_record(self, key: str) -> Any:
        return self.process.connection().find_record(key)

    @property
    def stored(self) -> Any:
        return self.process.connection().stored

    @stored.setter
    def stored(self, value: Any) -> None:
        self.process.connection().stored = value


class Trading(Trader):
    """The connectivity role's class: the SDK's Trader over SnapTrade's
    trading API (trading.py), its orders polled at its own `poll_seconds`;
    a refusal it meets is the Trader's to say, and the custody reads go on."""

    def __init__(self, process: Process) -> None:
        syncer = process.syncer
        # SnapTrade's trading API in the SDK's words, over the venue the
        # settings last gave (None until they are given).
        venue = TradingVenue(
            process.custody,
            lambda: syncer.venue,
            now=syncer.now,
            raw=syncer.raw,
            synthetic=lambda: syncer.config.synthetic,
            secrets=lambda: (
                syncer.config.credentials.secrets()
                if syncer.config.credentials is not None
                else ()
            ),
        )
        base = storage_base(syncer.raw)
        sent, _, originated = connectivity.trading_files(base)
        super().__init__(
            lambda: venue if syncer.venue is not None else None,
            now=syncer.now,
            # The placements sent, the venue-originated orders and the
            # charges reported, kept in storage so a restart sends and
            # reports none twice (contract v23).
            sent=connectivity.Sent(sent),
            watched=connectivity.Watched(originated),
            charged=charged(connectivity.charged_file(base)),
            # The venue account codes its `ems` mapped on it, as the account
            # scope delivers them (W11.22).
            scope=lambda: process.links.scope,
            # Contract v28 (ruling 518): each call to SnapTrade's trading API
            # its round trip, a placement its acknowledgement, a fill its
            # delay, reported every 10 seconds (ReportLatency) through this
            # class.
            latency=Latency(),
            poll_seconds=lambda: (
                SYNTHETIC_POLL_SECONDS if syncer.config.synthetic else POLL_SECONDS
            ),
        )
        self.process = process

    async def on_start(self) -> None:
        await self.process.started(self)
        await super().on_start()

    async def run(self) -> None:
        latency = self.latency
        assert latency is not None
        await asyncio.gather(super().run(), latency.run(), self.process.follow(self))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    process = Process()
    # One class per role it may hold (SDK 0.35): `meridian.run` registers
    # those the instance holds, applies the first settings, and starts each
    # one's `run`, again after a backoff should it fail.
    process.roles = [process.custody, Trading(process)]
    hold(process.syncer, process.links, process.wake)
    # The version's whole declaration (declaration.py): its settings, its
    # pages with the levels each serves, the accounts it names by SnapTrade's
    # identifiers, what it does not carry, its storage and its documentation.
    # A version registers exactly what it was uploaded with (contract v25).
    meridian.run(
        declaration=DECLARATION,
        roles=process.roles,
        pages=pages,
        port=PORT,
        on_settings=process.on_settings,
    )


if __name__ == "__main__":
    main()
