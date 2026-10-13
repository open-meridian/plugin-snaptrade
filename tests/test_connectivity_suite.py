"""The connectivity suite (contract v22): every case SnapTrade presents, from
its own trading code and its synthetic brokerage.

Each case is mapped to what SnapTrade's trading API answers for it, through
the synthetic brokerage (synthetic_orders.py: a ticker for each answer, a
quantity that works or fills), and run through this plugin's own Trader
(trading.py) against the runner's recorder, as a placement heard is. Only
the sidecar is stood in for. SIMULATED ONLY: nothing reaches SnapTrade.

Six cases are not presented, each with why (`NOT_PRESENTED`), as the suite
lets a case marked `about` or `about_field` be: SnapTrade reads no
calendar's days (it asks the date read for each fill's settlement date),
offers no locate, states no fill's place of execution, and places what its
`ems` released whatever releases wait for.
"""

from __future__ import annotations

from typing import Any

from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder, names, run, suite

from snaptrade import synthetic_orders
from trading_cases import (
    IBKR_CODE,
    Producer,
    Rig,
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
}


async def acknowledged(recorder: Recorder) -> None:
    rig = Rig(recorder)
    held = placement()
    rig.holds(held)
    await rig.hear(held)


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
        venue_order_id=worked.brokerage_order_id,
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


def venue_originated(resolves: bool = True) -> Producer:
    """The order the synthetic brokerage took outside the platform, on the
    account a placement showed an ems mapped on this plugin."""

    async def produce(recorder: Recorder) -> None:
        rig = Rig(recorder)
        if not resolves:
            recorder.answer(
                "ResolveIdentifier",
                lambda _: ops.ResolveIdentifierResult(
                    found=False, miss_reason=ops.MISS_REASON_NOT_FOUND
                ),
            )
        recorder.answer(
            "RecordOrder",
            lambda _: ops.RecordOrderResult(order=ops.Order(order_id="ORD-VENUE-1")),
        )
        recorder.answer(
            "CreatePlacement",
            lambda _: ops.CreatePlacementResult(
                placement=ops.Placement(placement_id="PLC-VENUE-1")
            ),
        )
        held = placement(quantity="200")
        rig.holds(held)
        await rig.hear(held)
        await rig.trader.poll()

    return produce


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
    "records-a-venue-originated-order": venue_originated(),
    "sends-nothing-new-on-a-pause": paused,
    "cancels-at-the-venue-on-a-stop": stopped,
    "publishes-its-catalogue": started,
    "reads-the-instrument-it-places": acknowledged,
    "reads-its-placements-orders-on-start": started,
    "catches-up-placements-after-a-loss": caught_up,
    "reads-the-instructions-in-force-on-start": started,
    "places-a-direct-order": venue_originated(),
    "resolves-a-venue-symbol-by-the-facts-date": venue_originated(),
    "reports-a-symbol-it-cannot-resolve": venue_originated(resolves=False),
}


def test_every_case_is_mapped_or_said_not_presented_with_why() -> None:
    assert set(names("connectivity")) == set(PRODUCERS) | set(NOT_PRESENTED)
    assert not set(PRODUCERS) & set(NOT_PRESENTED)
    held = {case.name: case.about or case.about_field for case in suite("connectivity").cases}
    assert all(held[name] for name in NOT_PRESENTED), "a required case is never skipped"


def test_the_connectivity_suite_passes() -> None:
    report = run(
        "connectivity", PRODUCERS, not_presented=NOT_PRESENTED, instance_id="snaptrade"
    )
    assert report.passed, report.failures
    assert len(report.passed_cases) == 23
    assert sorted(report.not_presented) == sorted(NOT_PRESENTED)


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
