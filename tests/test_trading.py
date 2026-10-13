"""SnapTrade's connectivity work (trading.py) beyond what the suite asks:
how a fill's price and ID are derived, that a placement is never sent twice,
that a held placement is sent once the pause lifts, and that a fill waits
for its settlement date rather than guess one. SIMULATED ONLY: the synthetic
brokerage stands in for SnapTrade, the suites' recorder for the sidecar."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder

from snaptrade import synthetic, synthetic_orders
from snaptrade.trading import Sent, execution_id, fill_price, ticker_of
from trading_cases import Rig, instrument, placement


def test_a_fills_price_is_worked_out_from_the_two_averages() -> None:
    # 4 at 227.41, then 6 more bringing the average to 227.50.
    price, rounded = fill_price(Decimal(4), Decimal("227.41"), Decimal(10), Decimal("227.50"))
    assert (price, rounded) == (Decimal("227.56"), False)
    # One that does not divide is rounded, and says so.
    price, rounded = fill_price(Decimal(1), Decimal("10"), Decimal(4), Decimal("10.01"))
    assert rounded and price == Decimal("10.01333333")


def test_a_fills_id_is_the_order_and_the_quantity_it_brought_it_to() -> None:
    assert execution_id("SYN-ORD-0001", Decimal("10.0")) == "SYN-ORD-0001:10"
    assert execution_id("SYN-ORD-0001", Decimal("10")) == execution_id(
        "SYN-ORD-0001", Decimal("10.000")
    )


def test_the_ticker_is_snaptrades_own_symbol_first() -> None:
    record = instrument("AAPL")
    record.identifiers.insert(0, ops.Identifier(scheme="symbol", value="AAPL.O", source="x"))
    assert ticker_of(record) == "AAPL"
    assert ticker_of(ops.InstrumentRecord()) == ""


def test_a_placement_noted_sent_is_asked_about_and_never_sent_again(tmp_path: Path) -> None:
    recorder = Recorder("snaptrade")

    held = placement()
    # Noted before a send the process did not live to report.
    Sent(tmp_path / "sent.json").note(held.placement_id)

    async def produce() -> None:
        # The plugin started again reads what it noted.
        rig = Rig(recorder, sent=Sent(tmp_path / "sent.json"))
        rig.holds(held)
        await rig.hear(held)
        assert rig.trading.orders(synthetic.IBKR_INDIVIDUAL, "") == []
        placed = [o for o in rig.trading.orders(synthetic.ALPACA_MARGIN, "")]
        assert placed == [], "nothing placed"

    asyncio.run(produce())
    assert recorder.on("ReportPlacement") == []


def test_a_placement_held_by_a_pause_is_sent_when_it_lifts() -> None:
    recorder = Recorder("snaptrade")

    async def produce() -> None:
        rig = Rig(recorder)
        await rig.instruct(ops.TRADING_INSTRUCTION_VALUE_PAUSE)
        held = placement()
        rig.holds(held)
        await rig.hear(held)
        assert recorder.on("ReportPlacement") == []
        await rig.instruct(ops.TRADING_INSTRUCTION_VALUE_ACTIVE)

    asyncio.run(produce())
    (acknowledged,) = recorder.on("ReportPlacement")
    assert acknowledged.event == ops.PLACEMENT_EVENT_ACKNOWLEDGED


def test_a_status_not_mapped_is_resolved_by_the_next_answer() -> None:
    recorder = Recorder("snaptrade")

    async def produce() -> None:
        rig = Rig(recorder, synthetic_orders.ODD)
        held = placement(quantity="200")
        rig.holds(held)
        await rig.hear(held)
        await rig.trader.poll()

    asyncio.run(produce())
    events = [sent.event for sent in recorder.on("ReportPlacement")]
    assert events == [ops.PLACEMENT_EVENT_OUTCOME_UNKNOWN, ops.PLACEMENT_EVENT_ACKNOWLEDGED]


def test_a_fill_waits_for_its_settlement_date_and_is_never_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The date read answers none, and SnapTrade states none: no fill sent."""
    recorder = Recorder("snaptrade")

    async def produce() -> None:
        rig = Rig(recorder)
        # A brokerage whose activity feed states nothing for the fill yet.
        monkeypatch.setattr(rig.trading, "_filled", lambda *_: None)
        recorder.answer(
            "ComputeDate",
            lambda _: ops.ComputeDateResult(outcome=ops.DATE_OUTCOME_RULE_NOT_HELD),
        )
        held = placement(quantity="1")
        rig.holds(held)
        await rig.hear(held)
        await rig.trader.poll()

    asyncio.run(produce())
    assert recorder.on("ComputeDate")
    assert recorder.on("ReportExecution") == []


def test_where_the_date_read_answers_none_the_date_snaptrade_states_is_sent() -> None:
    recorder = Recorder("snaptrade")

    async def produce() -> None:
        rig = Rig(recorder)
        recorder.answer(
            "ComputeDate",
            lambda _: ops.ComputeDateResult(outcome=ops.DATE_OUTCOME_RULE_NOT_HELD),
        )
        held = placement(quantity="1")
        rig.holds(held)
        await rig.hear(held)
        await rig.trader.poll()

    asyncio.run(produce())
    (fill,) = recorder.on("ReportExecution")
    # 2026-09-28, a Monday: the synthetic brokerage states the next weekday.
    assert (fill.settlement_date, fill.computed_settlement_date) == ("2026-09-29", "")
    assert not fill.HasField("settlement_date_basis")


def test_a_cancel_the_ems_asks_for_is_sent_at_snaptrade_then_reported() -> None:
    recorder = Recorder("snaptrade")

    async def produce() -> None:
        rig = Rig(recorder)
        held = placement(quantity="200")
        rig.holds(held)
        await rig.hear(held)
        worked = next(iter(rig.trader.working.values()))
        asked = placement(
            quantity="200",
            state=ops.PLACEMENT_STATE_WORKING,
            venue_order_id=worked.brokerage_order_id,
        )
        asked.status.conditions.append(
            ops.StandingPlacementCondition(
                condition=ops.PLACEMENT_CONDITION_CHANGE_REQUESTED, reason="cancel requested"
            )
        )
        rig.holds(asked)
        await rig.hear(asked)
        await rig.trader.poll()

    asyncio.run(produce())
    events = [sent.event for sent in recorder.on("ReportPlacement")]
    assert events == [ops.PLACEMENT_EVENT_ACKNOWLEDGED, ops.PLACEMENT_EVENT_CANCELLED]
    assert recorder.on("RequestPlacementChange") == [], "a cancel the ems asked for is no fact"


def test_a_replace_asked_for_is_not_sent_as_a_cancel() -> None:
    recorder = Recorder("snaptrade")

    async def produce() -> None:
        rig = Rig(recorder)
        held = placement(quantity="200")
        rig.holds(held)
        await rig.hear(held)
        worked = next(iter(rig.trader.working.values()))
        asked = placement(
            quantity="200",
            state=ops.PLACEMENT_STATE_WORKING,
            venue_order_id=worked.brokerage_order_id,
        )
        asked.status.conditions.append(
            ops.StandingPlacementCondition(
                condition=ops.PLACEMENT_CONDITION_CHANGE_REQUESTED, reason="replace requested"
            )
        )
        rig.holds(asked)
        await rig.hear(asked)
        await rig.trader.poll()

    asyncio.run(produce())
    events = [sent.event for sent in recorder.on("ReportPlacement")]
    assert events == [ops.PLACEMENT_EVENT_ACKNOWLEDGED]
