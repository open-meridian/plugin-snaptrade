"""The connectivity suite (contract v23): every case SnapTrade presents, from
its own trading code and its synthetic brokerage.

Each case is mapped to what SnapTrade's trading API answers for it, through
the synthetic brokerage (synthetic_orders.py: a ticker for each answer, a
quantity that works or fills), and run through this plugin's own Trader
(trading.py) against the runner's recorder, as a placement heard is. Only
the sidecar is stood in for. SIMULATED ONLY: nothing reaches SnapTrade.

Twelve cases are not presented, each with why (`NOT_PRESENTED`), as the
suite lets a case marked `about` or `about_field` be: SnapTrade reads no
calendar's days (it asks the date read for each fill's settlement date),
offers no locate (so resolves no locate's symbol), states no fill's place of
execution, places what its `ems` released whatever releases wait for, and
from contract v23 rolls nothing (its trades settle), from v24 gives
nothing up to a prime broker, and from v27 asks no dealer for a quote (it
takes DMA placements alone). From
contract v28 an option's expiry and a package's fill are not presented (it
places single-leg equity orders), and reports-a-sessions-round-trip is
(ruling 518): the Trader's round trips, reported. Every other case
passes: among them v23's venue-originated fills -- an order placed in the
broker's own app (SYN-OUT-0001), its fills reported on no placement, a later
one on the placement an accept created, and the commission SnapTrade states
on it re-reported there -- and the `about` cases SnapTrade does present: a
bust, derived from a later read showing less filled, and a charge with no
fill, a cancel's `FEE` activity naming the order.
"""

from __future__ import annotations

from typing import Any

from meridian.plugin.v1 import operations_pb2 as ops
from meridian.roles import Latency
from meridian.suites import ANSWERED_BY_SDK, Recorder, names, run, suite

from snaptrade import synthetic_orders
from trading_cases import (
    IBKR_CODE,
    Producer,
    Rig,
    accept,
    accepted_then_filled,
    originated,
    placed_and_filled,
    placement,
)

from conftest import NOW

NOT_PRESENTED = {
    "reads-a-calendar": (
        "SnapTrade asks the date read for each fill's settlement date (ComputeDate); it "
        "reads no calendar's days"
    ),
    "records-a-locate": "SnapTrade offers no locate API",
    "resolves-a-venue-symbol-by-the-facts-date": (
        "SnapTrade offers no locate API, so it resolves no locate's symbol"
    ),
    "resolves-an-executions-venue": "SnapTrade states no fill's place of execution",
    "reports-a-venue-not-held": "SnapTrade states no fill's place of execution",
    "reads-whether-releases-wait-for-compliance": (
        "it places what its ems released; whether releases wait for compliance changes "
        "nothing it sends"
    ),
    "hears-an-accounts-attributes-change": (
        "it places what its ems released; whether releases wait for compliance changes "
        "nothing it sends"
    ),
    "reports-a-give-up-rejected": (
        "SnapTrade's brokerages execute and clear each order in the account that placed "
        "it: no trade is given up to a prime broker, so none is rejected"
    ),
    "reports-a-value-date-roll": (
        "SnapTrade's brokerages settle each trade on its settlement date: none rolls a "
        "spot position's value date at a daily cut"
    ),
    # Contract v27: its placement types are DMA alone (trading.PLACEMENT_TYPES).
    "records-a-dealers-quote-on-an-rfq-placement": (
        "SnapTrade asks no dealer for a quote: it takes DMA placements alone, and an RFQ "
        "placement is rejected unsupported"
    ),
    # Contract v28: an option's expiry (about options) and a package's fill
    # (about packages; ruling 602), named through a table: meridian 0.1.52's
    # check holds contract v27's suites.
    **{
        case: why
        for case, why in (
            (
                "reports-an-expiry",
                "it places equity orders through SnapTrade's trading API: no option placement "
                "to expire",
            ),
            (
                "reports-a-package-fill",
                "it places single-leg orders alone: SnapTrade's API takes no package here",
            ),
        )
    },
}


async def acknowledged(recorder: Recorder) -> None:
    rig = Rig(recorder)
    held = placement()
    rig.holds(held)
    await rig.hear(held)


async def round_trip(recorder: Recorder) -> None:
    """Contract v28 (ruling 518): the trading API's round trips, each call
    the Trader makes to SnapTrade, reported."""
    latency = Latency(recorder)
    rig = Rig(recorder, latency=latency)
    held = placement()
    rig.holds(held)
    await rig.hear(held)
    await rig.trader.poll()
    await latency.report()


async def another_instances(recorder: Recorder) -> None:
    rig = Rig(recorder)
    held = placement(instance="elsewhere")
    rig.holds(held)
    await rig.hear(held)
    await rig.trader.poll()


async def not_offered(recorder: Recorder) -> None:
    """Good till date at the synthetic Alpaca, which takes none."""
    rig = Rig(recorder)
    held = placement(tif=ops.TIME_IN_FORCE_GOOD_TILL_DATE)
    rig.holds(held)
    await rig.hear(held)


async def a_strategy(recorder: Recorder) -> None:
    rig = Rig(recorder)
    held = placement(strategy=ops.StrategyInstruction(kind=ops.STRATEGY_KIND_VWAP))
    rig.holds(held)
    await rig.hear(held)


def by_ticker(ticker: str, quantity: str = "10", polls: int = 0) -> Producer:
    async def produce(recorder: Recorder) -> None:
        rig = Rig(recorder, ticker)
        held = placement(quantity=quantity)
        rig.holds(held)
        await rig.hear(held)
        for _ in range(polls):
            await rig.trader.poll()

    return produce


async def the_same_fill_twice(recorder: Recorder) -> None:
    """One fill read, then read again by the plugin started again before the
    book held it: the same derived ID both times."""
    rig = Rig(recorder)
    held = placement(quantity="1")
    rig.holds(held)
    await rig.hear(held)
    await rig.trader.poll()
    worked = next(iter(rig.trader.working.values()))
    again = placement(
        quantity="1",
        state=ops.PLACEMENT_STATE_WORKING,
        venue_order_id=worked.venue_order_id,
    )
    rig.holds(again)
    rig.trader = rig.make()
    await rig.trader.start()
    await rig.trader.poll()


async def busted(recorder: Recorder) -> None:
    rig = Rig(recorder, synthetic_orders.BUSTED)
    held = placement(quantity="1")
    rig.holds(held)
    await rig.hear(held)
    for _ in range(3):
        await rig.trader.poll()


async def charged(recorder: Recorder) -> None:
    """Cancelled under a stop with nothing filled: the brokerage's fee."""
    rig = Rig(recorder, synthetic_orders.FEE_ON_CANCEL)
    held = placement(quantity="200")
    rig.holds(held)
    await rig.hear(held)
    await rig.instruct(ops.TRADING_INSTRUCTION_VALUE_STOP)
    await rig.trader.poll()


async def renamed(recorder: Recorder) -> None:
    """Filled under a symbol SnapTrade renamed while it worked, which names
    none of the deployment's records."""
    rig = Rig(recorder, synthetic_orders.RENAMED)
    recorder.answer(
        "ResolveIdentifier",
        lambda _: ops.ResolveIdentifierResult(
            found=False, miss_reason=ops.MISS_REASON_NOT_FOUND
        ),
    )
    held = placement(quantity="1")
    rig.holds(held)
    await rig.hear(held)
    await rig.trader.poll()


async def fill_on_no_placement(recorder: Recorder) -> None:
    await originated(recorder)


async def charge_re_reported(recorder: Recorder) -> None:
    """The commission SnapTrade states on the fill of SYN-OUT-0001, kept
    unplaced, then reported again on the placement the accept created."""
    rig = await originated(recorder)
    await accept(rig)


async def paused(recorder: Recorder) -> None:
    rig = Rig(recorder)
    await rig.instruct(ops.TRADING_INSTRUCTION_VALUE_PAUSE)
    held = placement()
    rig.holds(held)
    await rig.hear(held)


async def stopped(recorder: Recorder) -> None:
    rig = Rig(recorder)
    held = placement(quantity="200")
    rig.holds(held)
    await rig.hear(held)
    await rig.instruct(ops.TRADING_INSTRUCTION_VALUE_STOP)


async def started(recorder: Recorder) -> None:
    rig = Rig(recorder)
    await rig.trader.start()


async def caught_up(recorder: Recorder) -> None:
    rig = Rig(recorder)
    await rig.trader.start()
    await rig.trader.poll()


PRODUCERS: dict[str, Producer] = {
    "acknowledges-a-placement-naming-it": acknowledged,
    "reports-a-sessions-round-trip": round_trip,
    "hears-no-placement-of-another-instance": another_instances,
    "sends-the-venue-account-code-its-ems-set": acknowledged,
    "rejects-what-the-venue-does-not-offer": not_offered,
    "rejects-a-strategy-its-broker-lacks": a_strategy,
    "venue-rejected-with-its-reason-as-reported": by_ticker(synthetic_orders.REJECTED),
    "unmapped-status-is-outcome-unknown": by_ticker(synthetic_orders.ODD),
    "resolves-an-unanswered-send-by-asking": by_ticker(synthetic_orders.LOST, "200", 1),
    "reports-a-fill-with-its-settlement-date": placed_and_filled,
    "derives-the-same-execution-id-twice": the_same_fill_twice,
    "reports-a-bust": busted,
    "reports-a-charge": charged,
    "sends-nothing-new-on-a-pause": paused,
    "cancels-at-the-venue-on-a-stop": stopped,
    "publishes-its-catalogue": started,
    "reads-the-instrument-it-places": acknowledged,
    "reads-its-placements-orders-on-start": started,
    "catches-up-placements-after-a-loss": caught_up,
    "reads-the-instructions-in-force-on-start": started,
    "reports-a-symbol-it-cannot-resolve": renamed,
    # Contract v23: venue-originated fills.
    "reports-a-fill-on-no-placement": fill_on_no_placement,
    "reports-a-later-fill-on-the-accepted-placement": accepted_then_filled,
    "re-reports-a-charge-on-the-accepted-placement": charge_re_reported,
}


def test_every_case_is_mapped_or_said_not_presented_with_why() -> None:
    # Contract v24: the two cases every suite holds, which the SDK answers.
    assert set(names("connectivity")) == set(PRODUCERS) | set(NOT_PRESENTED) | set(
        ANSWERED_BY_SDK
    )
    assert not set(PRODUCERS) & set(NOT_PRESENTED)
    held = {case.name: case.about or case.about_field for case in suite("connectivity").cases}
    assert all(held[name] for name in NOT_PRESENTED), "a required case is never skipped"


def test_the_connectivity_suite_passes() -> None:
    report = run(
        "connectivity", PRODUCERS, not_presented=NOT_PRESENTED, instance_id="snaptrade"
    )
    assert report.passed, report.failures
    assert report.not_presented == NOT_PRESENTED
    assert set(report.passed_cases) == set(PRODUCERS) | set(ANSWERED_BY_SDK)
    assert report.answered_by_sdk == list(ANSWERED_BY_SDK)
    # Contract v26 adds the event group every suite holds, answered by the SDK.
    # Contract v28: the session's round trip too.
    assert len(report.passed_cases) == 27


def test_a_plugin_sending_on_a_pause_fails_the_suite() -> None:
    """The suite tests the outcome: a trader that ignores a pause fails the
    case that says so."""

    async def ignoring(recorder: Recorder) -> None:
        rig = Rig(recorder)
        await rig.instruct(ops.TRADING_INSTRUCTION_VALUE_PAUSE)
        rig.trader.instructions.clear()
        held = placement()
        rig.holds(held)
        await rig.hear(held)

    report = run(
        "connectivity",
        {**PRODUCERS, "sends-nothing-new-on-a-pause": ignoring},
        not_presented=NOT_PRESENTED,
        instance_id="snaptrade",
    )
    assert set(report.failures) == {"sends-nothing-new-on-a-pause"}


def test_a_read_only_connection_rejects_every_order_unsupported() -> None:
    async def read_only(recorder: Recorder) -> None:
        rig = Rig(recorder)
        held = placement(code=IBKR_CODE)
        rig.holds(held)
        await rig.hear(held)

    recorder = Recorder("snaptrade")
    import asyncio

    asyncio.run(read_only(recorder))
    (sent,) = recorder.on("ReportPlacement")
    assert sent.event == ops.PLACEMENT_EVENT_REJECTED
    assert sent.rejection_reason == ops.REJECTION_REASON_UNSUPPORTED
    assert recorder.on("ResolveInstrument") == [], "nothing read for what is not sent"
    assert NOW


def sent_on(recorder: Recorder, row: str) -> list[Any]:
    return recorder.on(row)
