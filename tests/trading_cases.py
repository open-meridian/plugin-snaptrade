"""SnapTrade's connectivity work run against the suites' recorder: the
plugin's own Trader (trading.py) and its synthetic brokerage
(synthetic_orders.py), only the sidecar stood in for by the recorder.

Shared by the connectivity suite's test and the custody suite's
`asks-for-a-settlement-date`, which the same fill path answers: SnapTrade's
order states no settlement date, so each fill asks the date read.
SIMULATED ONLY: nothing here reaches SnapTrade or a brokerage.
"""

from __future__ import annotations

import tempfile
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder

from snaptrade import synthetic
from snaptrade.raw import RawStore
from snaptrade.synthetic import SyntheticVenue
from snaptrade.synthetic_orders import SyntheticTrading
from snaptrade.trading import Sent, Trader

from conftest import NOW, clock

Producer = Callable[[Recorder], Awaitable[None]]

#: The venue account codes the synthetic accounts are named by: the external
#: account IDs custody reports them under.
ALPACA_CODE = "ALPACA:SYN-ALP-1001"
IBKR_CODE = "INTERACTIVE-BROKERS-FLEX:SYN-IB-2002"
ACCOUNT = "ACC-01JAE000000000000000000001"
INSTRUMENT = "INS-01J8XQ4M7K0000000000AAPL"
CALENDAR = "CAL-01JAE0000000000000000USSS"


def decimal(text: str) -> ops.Decimal:
    exact = Decimal(text)
    scale = max(0, -int(exact.as_tuple().exponent))
    units = int(exact.scaleb(scale))
    return ops.Decimal(high=units >> 64, low=units & ((1 << 64) - 1), scale=scale)


def usd(text: str) -> ops.Money:
    return ops.Money(amount=decimal(text), currency_code="USD")


def placement(
    pid: str = "PLC-01JAE00000000000000000001",
    *,
    quantity: str = "10",
    instance: str = "snaptrade",
    code: str = ALPACA_CODE,
    tif: ops.TimeInForce = ops.TIME_IN_FORCE_DAY,
    state: ops.PlacementState = ops.PLACEMENT_STATE_QUEUED,
    strategy: ops.StrategyInstruction | None = None,
    venue_order_id: str = "",
) -> ops.Placement:
    held = ops.Placement(
        placement_id=pid,
        order_id="ORD-" + pid[4:],
        account_id=ACCOUNT,
        ems_instance_id="desk",
        connectivity_instance_id=instance,
        venue_account_code=code,
        quantity=decimal(quantity),
        limit_price=usd("230.00"),
        time_in_force=tif,
        status=ops.PlacementStatus(state=state),
        venue_order_id=venue_order_id,
    )
    if strategy is not None:
        held.strategy.CopyFrom(strategy)
    return held


def order_of(held: ops.Placement, **changes: Any) -> ops.Order:
    order = ops.Order(
        order_id=held.order_id,
        account_id=ACCOUNT,
        instrument_id=changes.pop("instrument_id", INSTRUMENT),
        side=changes.pop("side", ops.SIDE_BUY),
        quantity=held.quantity,
        order_type=changes.pop("order_type", ops.ORDER_TYPE_LIMIT),
        limit_price=usd("230.00"),
        time_in_force=changes.pop("time_in_force", held.time_in_force),
    )
    order.placements.append(held)
    return order


def instrument(ticker: str) -> ops.InstrumentRecord:
    return ops.InstrumentRecord(
        instrument_id=INSTRUMENT,
        currency="USD",
        identifiers=[ops.Identifier(scheme="symbol", value=ticker, source="snaptrade")],
    )


class Rig:
    """A Trader over the recorder and a synthetic brokerage, trading enabled
    on Alpaca's connection; the book's answers as the recorder gives them."""

    def __init__(
        self, recorder: Recorder, ticker: str = "AAPL", sent: Sent | None = None
    ) -> None:
        self.recorder = recorder
        self.sent = sent
        self.trading = SyntheticTrading(clock(NOW))
        self.trading.enable_trading(synthetic.ALPACA)
        self.venue = SyntheticVenue(clock(NOW), self.trading)
        self.raw = RawStore(Path(tempfile.mkdtemp()) / "raw-responses")
        self.orders: list[ops.Order] = []
        self.executions: list[ops.Execution] = []
        self.minted = 0
        recorder.answer("ListOrders", self._listed)
        recorder.answer(
            "ResolveInstrument",
            lambda _: ops.ResolveInstrumentResult(found=True, instrument=instrument(ticker)),
        )
        recorder.answer("ComputeDate", lambda _: settled())
        recorder.answer("ReportExecution", self._executed)
        self.trader = self.make()

    def make(self) -> Trader:
        return Trader(
            self.recorder,
            lambda: self.venue,
            now=clock(NOW),
            raw=self.raw,
            synthetic=lambda: True,
            sent=self.sent,
        )

    def _listed(self, _: Any) -> ops.ListOrdersResult:
        return ops.ListOrdersResult(
            orders=self.orders,
            executions=self.executions,
            as_of=ops.Watermark(
                partitions=[ops.PartitionSequence(partition="book", sequence=7)]
            ),
        )

    def _executed(self, params: ops.ReportExecutionParams) -> ops.ReportExecutionResult:
        self.minted += 1
        held = ops.Execution(
            execution_id=f"EXE-{self.minted}",
            placement_id=params.placement_id,
            venue_execution_id=params.venue_execution_id,
            quantity=params.quantity,
            supersedes=params.supersedes,
        )
        return ops.ReportExecutionResult(execution=held)

    def holds(self, *placements: ops.Placement, **changes: Any) -> None:
        self.orders = [order_of(each, **changes) for each in placements]

    async def hear(self, *placements: ops.Placement) -> None:
        self.recorder.answer(
            "PlacementUpdated",
            lambda _: [ops.PlacementUpdatedEvent(placement=each) for each in placements],
        )
        await self.recorder.receive(placement_updated=self.trader.on_placement)

    async def instruct(
        self, value: ops.TradingInstructionValue, *, instance: str = "snaptrade"
    ) -> None:
        instruction = ops.TradingInstruction(
            target=ops.TradingInstructionTarget(instance_id=instance),
            value=value,
            entry=ops.EntryMeta(entry_id="ENT-stop-1"),
        )
        self.recorder.answer(
            "TradingInstructionChanged",
            lambda _: ops.TradingInstructionChangedEvent(instruction=instruction),
        )
        await self.recorder.receive(trading_instruction_changed=self.trader.on_instruction)


def settled() -> ops.ComputeDateResult:
    return ops.ComputeDateResult(
        date="2026-09-29",
        outcome=ops.DATE_OUTCOME_COMPUTED,
        basis=ops.DateBasis(
            rule=ops.DateRuleRef(subject="XNAS", version=1),
            calendars=[ops.CalendarRef(calendar_id=CALENDAR, version=3)],
        ),
        business_day=True,
    )


async def placed_and_filled(recorder: Recorder) -> None:
    """A placement of 10 placed and filled at the synthetic brokerage: 4,
    then the rest, each with the date read's settlement date."""
    rig = Rig(recorder)
    held = placement()
    rig.holds(held)
    await rig.hear(held)
    await rig.trader.poll()
    await rig.trader.poll()
