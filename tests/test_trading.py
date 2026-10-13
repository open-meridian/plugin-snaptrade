"""SnapTrade's connectivity work (trading.py) beyond what the suite asks:
how a fill's price and ID are derived, that a placement is never sent twice,
that a held placement is sent once the pause lifts, and that a fill waits
for its settlement date rather than guess one. SIMULATED ONLY: the synthetic
brokerage stands in for SnapTrade, the suites' recorder for the sidecar."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from meridian.operations import as_decimal
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder

from snaptrade import synthetic, synthetic_orders
from snaptrade.trading import Sent, execution_id, fill_price, ticker_of
from snaptrade.venue import VenueError
from trading_cases import (
    ALPACA_CODE,
    Rig,
    asking,
    decimal,
    instrument,
    placement,
    scope,
    usd,
)


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


def _working(rig: Rig, quantity: str = "200") -> ops.Placement:
    """The placement as the book holds it once SnapTrade acknowledged it."""
    worked = next(iter(rig.trader.working.values()))
    return placement(
        quantity=quantity,
        state=ops.PLACEMENT_STATE_WORKING,
        venue_order_id=worked.brokerage_order_id,
    )


def test_a_cancel_the_ems_asks_for_is_sent_at_snaptrade_then_reported() -> None:
    recorder = Recorder("snaptrade")

    async def produce() -> None:
        rig = Rig(recorder)
        held = placement(quantity="200")
        rig.holds(held)
        await rig.hear(held)
        asked = asking(_working(rig), ops.PLACEMENT_CHANGE_KIND_CANCEL)
        rig.holds(asked)
        await rig.hear(asked)
        # Heard again, the same request is not sent twice.
        await rig.hear(asked)
        await rig.trader.poll()

    asyncio.run(produce())
    events = [sent.event for sent in recorder.on("ReportPlacement")]
    assert events == [ops.PLACEMENT_EVENT_ACKNOWLEDGED, ops.PLACEMENT_EVENT_CANCELLED]
    assert recorder.on("RequestPlacementChange") == [], "a cancel the ems asked for is no fact"


def test_a_condition_without_a_request_is_never_read_as_a_cancel() -> None:
    """The kind is the request's, never a condition's words."""
    recorder = Recorder("snaptrade")

    async def produce() -> None:
        rig = Rig(recorder)
        held = placement(quantity="200")
        rig.holds(held)
        await rig.hear(held)
        worded = _working(rig)
        worded.status.conditions.append(
            ops.StandingPlacementCondition(
                condition=ops.PLACEMENT_CONDITION_CHANGE_REQUESTED, reason="cancel requested"
            )
        )
        rig.holds(worded)
        await rig.hear(worded)
        await rig.trader.poll()
        statuses = [o["status"] for o in rig.trading.orders(synthetic.ALPACA_MARGIN, "")]
        assert "CANCELED" not in statuses

    asyncio.run(produce())
    events = [sent.event for sent in recorder.on("ReportPlacement")]
    assert events == [ops.PLACEMENT_EVENT_ACKNOWLEDGED]


def test_a_replace_is_sent_and_reported_with_snaptrades_new_order() -> None:
    recorder = Recorder("snaptrade")

    async def produce() -> None:
        rig = Rig(recorder)
        held = placement(quantity="200")
        rig.holds(held)
        await rig.hear(held)
        first = _working(rig)
        asked = asking(first, ops.PLACEMENT_CHANGE_KIND_REPLACE, limit_price=usd("229.50"))
        rig.holds(asked)
        await rig.hear(asked)
        await rig.trader.poll()
        # Then a cancel, which SnapTrade takes on the order now.
        now = _working(rig)
        assert now.venue_order_id != first.venue_order_id
        cancel = asking(now, ops.PLACEMENT_CHANGE_KIND_CANCEL, "CRQ-01JAE00000000000000000002")
        rig.holds(cancel)
        await rig.hear(cancel)
        await rig.trader.poll()
        held_now = {
            o["brokerage_order_id"]: o for o in rig.trading.orders(synthetic.ALPACA_MARGIN, "")
        }
        assert held_now[first.venue_order_id]["status"] == "REPLACED"
        assert held_now[now.venue_order_id]["status"] == "CANCELED"
        assert held_now[now.venue_order_id]["limit_price"] == "229.50"

    asyncio.run(produce())
    reported = recorder.on("ReportPlacement")
    events = [sent.event for sent in reported]
    assert events == [
        ops.PLACEMENT_EVENT_ACKNOWLEDGED,
        ops.PLACEMENT_EVENT_REPLACED,
        ops.PLACEMENT_EVENT_CANCELLED,
    ]
    replaced = reported[1]
    assert replaced.venue_order_id == "SYN-ORD-0002"
    assert as_decimal(replaced.limit_price.amount) == Decimal("229.50")
    assert as_decimal(replaced.quantity) == Decimal(200)
    assert replaced.raw_record.key.endswith("/replace")


def test_a_replace_the_catalogue_lacks_is_the_change_rejected_unsupported() -> None:
    """Good till date at the synthetic Alpaca: refused with nothing sent; the
    book reads a rejection while it works as the change refused (row 14)."""
    recorder = Recorder("snaptrade")

    async def produce() -> None:
        rig = Rig(recorder)
        held = placement(quantity="200")
        rig.holds(held)
        await rig.hear(held)
        asked = asking(
            _working(rig),
            ops.PLACEMENT_CHANGE_KIND_REPLACE,
            time_in_force=ops.TIME_IN_FORCE_GOOD_TILL_DATE,
        )
        rig.holds(asked)
        await rig.hear(asked)
        statuses = [o["status"] for o in rig.trading.orders(synthetic.ALPACA_MARGIN, "")]
        assert "REPLACED" not in statuses, "nothing sent"

    asyncio.run(produce())
    reported = recorder.on("ReportPlacement")
    assert [sent.event for sent in reported] == [
        ops.PLACEMENT_EVENT_ACKNOWLEDGED,
        ops.PLACEMENT_EVENT_REJECTED,
    ]
    assert reported[1].rejection_reason == ops.REJECTION_REASON_UNSUPPORTED


def test_a_cancel_snaptrade_refuses_is_the_change_rejected_as_it_said() -> None:
    recorder = Recorder("snaptrade")

    async def produce() -> None:
        rig = Rig(recorder)
        held = placement(quantity="200")
        rig.holds(held)
        await rig.hear(held)
        working = _working(rig)
        # Ended at the brokerage before the plugin read it.
        rig.trading.cancel(synthetic.ALPACA_MARGIN, working.venue_order_id)
        asked = asking(working, ops.PLACEMENT_CHANGE_KIND_CANCEL)
        rig.holds(asked)
        await rig.hear(asked)

    asyncio.run(produce())
    reported = recorder.on("ReportPlacement")
    assert [sent.event for sent in reported] == [
        ops.PLACEMENT_EVENT_ACKNOWLEDGED,
        ops.PLACEMENT_EVENT_REJECTED,
    ]
    refused = reported[1]
    assert refused.rejection_reason == ops.REJECTION_REASON_VENUE_REJECTED
    assert (refused.rejection_as_reported.scheme, refused.rejection_as_reported.code) == (
        "snaptrade:error",
        "1070",
    )


def test_fills_after_a_replace_count_on_from_the_old_order() -> None:
    """4 of 10 filled, replaced to 12: SnapTrade's new order works the 8 left,
    its fills counted on from the 4, each ID naming the order it filled on."""
    recorder = Recorder("snaptrade")

    async def produce() -> None:
        rig = Rig(recorder)
        held = placement(quantity="10")
        rig.holds(held)
        await rig.hear(held)
        await rig.trader.poll()
        asked = asking(
            _working(rig, "10"), ops.PLACEMENT_CHANGE_KIND_REPLACE, quantity=decimal("12")
        )
        rig.holds(asked)
        await rig.hear(asked)
        await rig.trader.poll()
        await rig.trader.poll()

    asyncio.run(produce())
    fills = [
        (f.venue_execution_id, as_decimal(f.quantity)) for f in recorder.on("ReportExecution")
    ]
    assert fills == [
        ("SYN-ORD-0001:4", Decimal(4)),
        ("SYN-ORD-0002:3", Decimal(3)),
        ("SYN-ORD-0002:8", Decimal(5)),
    ]


def test_an_unanswered_replace_is_never_sent_again_and_read_from_snaptrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder("snaptrade")

    async def produce() -> None:
        rig = Rig(recorder)
        held = placement(quantity="200")
        rig.holds(held)
        await rig.hear(held)
        taken = rig.trading.replace
        sent: list[str] = []

        def lost(account_id: str, asked: Any) -> Any:
            sent.append(asked.brokerage_order_id)
            taken(account_id, asked)
            raise VenueError("replacing an order")

        monkeypatch.setattr(rig.trading, "replace", lost)
        asked = asking(
            _working(rig), ops.PLACEMENT_CHANGE_KIND_REPLACE, limit_price=usd("229.00")
        )
        rig.holds(asked)
        await rig.hear(asked)
        assert [s.event for s in recorder.on("ReportPlacement")][-1] != (
            ops.PLACEMENT_EVENT_REPLACED
        )
        await rig.hear(asked)
        await rig.trader.poll()
        await rig.trader.poll()
        assert sent == ["SYN-ORD-0001"], "sent once"

    asyncio.run(produce())
    reported = recorder.on("ReportPlacement")
    assert [sent.event for sent in reported] == [
        ops.PLACEMENT_EVENT_ACKNOWLEDGED,
        ops.PLACEMENT_EVENT_REPLACED,
    ]
    assert reported[1].venue_order_id == "SYN-ORD-0002"


def test_an_order_placed_outside_the_platform_is_kept_and_never_recorded() -> None:
    """SYN-OUT-0001 on the mapped Alpaca account: read, kept as SnapTrade
    answered it, and left alone: no order recorded, no placement, no fill
    (ruled 2026-10-13; venue-originated fills are v23's)."""
    recorder = Recorder("snaptrade")
    rig = Rig(recorder)

    async def produce() -> None:
        await rig.trader.start()
        await rig.trader.poll()
        await rig.trader.poll()

    asyncio.run(produce())
    for row in ("RecordOrder", "CreatePlacement", "ReportExecution", "ReportPlacement"):
        assert recorder.on(row) == [], row
    (read,) = rig.raw.reads(ALPACA_CODE)
    record = rig.raw.record(ALPACA_CODE, read.key)
    assert record is not None
    (call,) = record.calls
    assert [o["brokerage_order_id"] for o in call["body"]] == [synthetic_orders.OUTSIDE_ID]
    assert rig.trader.outside == {synthetic_orders.OUTSIDE_ID}


def test_the_accounts_read_are_the_ones_the_account_scope_maps() -> None:
    """Never inferred from the placements seen: an account its ems has not
    mapped on it, and on which it works nothing, is not read."""
    recorder = Recorder("snaptrade")
    rig = Rig(recorder, mapped=())

    async def produce() -> None:
        await rig.trader.start()
        await rig.trader.poll()
        assert rig.raw.reads(ALPACA_CODE) == []
        rig.scope = scope(ALPACA_CODE)
        await rig.trader.poll()
        assert len(rig.raw.reads(ALPACA_CODE)) == 1
        # Another instance's mapping is not this one's.
        other = Rig(Recorder("snaptrade"), mapped=())
        other.scope = scope(ALPACA_CODE, instance="elsewhere")
        assert other.trader.mapped() == frozenset()

    asyncio.run(produce())
