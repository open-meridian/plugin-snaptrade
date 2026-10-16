"""What reaches the sidecar, through the pinned SDK's operations."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

import meridian
from meridian.plugin.v1 import operations_pb2 as ops

from snaptrade.contract import _SYNC_STATE, Recorder
from snaptrade.normalise import (
    Closed,
    ExternalAccount,
    Freshness,
    Holding,
    Identifier,
    Lot,
    Side,
    Statement,
    SyncState,
    ns,
)

from conftest import NOW, Sidecar, ambiguous, found

ACCOUNT = ExternalAccount(
    external_account_id="ALPACA:INST-1",
    stable=True,
    name="Margin",
    account_type="margin",
    institution="Alpaca",
    connection_id="c1",
    snaptrade_account_id="a1",
    kind="margin",
)

LONG = Holding(
    identifiers=(Identifier("figi", "BBG000B9XRY4"), Identifier("symbol", "AAPL", "snaptrade")),
    description="Apple",
    kind="stock",
    side=Side.LONG,
    quantity=Decimal("12.50"),
    currency="USD",
    exchange_mic="XNAS",
    asset_class="equity",
)
SHORT = Holding(
    identifiers=(Identifier("symbol", "ZZTOP", "snaptrade"),),
    description="ZZTOP",
    kind="stock",
    side=Side.SHORT,
    quantity=Decimal("-40"),
    currency="USD",
    currency_assumed=True,
    asset_class="equity",
)
CASH = Holding(
    identifiers=(Identifier("iso4217", "CAD"),),
    description="CAD cash",
    kind="cash",
    asset_class="cash",
    side=Side.LONG,
    quantity=Decimal("200.00"),
    currency="CAD",
    market_value=meridian.Money(Decimal("200.00"), "CAD"),
    call="balances",
)
STATEMENT = Statement(
    external_statement_id=f"snaptrade:ALPACA:INST-1:{ns(NOW)}",
    as_of_date="2026-09-28",
    read_at_ns=ns(NOW),
    holdings=(LONG, SHORT, CASH),
    buying_power=(meridian.Money(Decimal("3046.90"), "USD"),),
)
SIGN_IN = Freshness(
    SyncState.NEEDS_SIGN_IN,
    NOW - timedelta(days=5),
    NOW.date(),
    "SnapTrade disabled it.",
    last_synced=NOW - timedelta(days=4),
    closed=(Closed("state", "derived", "SnapTrade disabled the connection"),),
)


# ── A statement and its rows ─────────────────────────────────────────────────


async def test_a_statement_is_opened_with_its_row_count_and_every_row_recorded() -> None:
    sidecar = Sidecar()
    outcome = await Recorder(sidecar.plugin()).record(ACCOUNT, STATEMENT, ns(NOW))
    (opened,) = sidecar.sent("RecordHoldingsStatement")
    assert opened.source == "snaptrade"
    assert opened.external_statement_id == STATEMENT.external_statement_id
    assert opened.expected_rows == 3
    assert opened.as_of_date == "2026-09-28"
    rows = sidecar.sent("RecordHolding")
    assert [meridian.as_decimal(row.quantity) for row in rows] == [
        Decimal("12.50"),
        Decimal("-40"),
        Decimal("200.00"),
    ]
    # The scale it was stated with crosses the wire: 12.50 is 1250 at two places.
    assert (rows[0].quantity.low, rows[0].quantity.scale) == (1250, 2)
    assert {row.external_account_id for row in rows} == {"ALPACA:INST-1"}
    assert {row.statement_id for row in rows} == {"STMT-1"}
    assert outcome.recorded == 3 and not outcome.stopped


async def test_resolution_is_dated_to_the_statement_and_qualified() -> None:
    sidecar = Sidecar()
    await Recorder(sidecar.plugin()).record(ACCOUNT, STATEMENT, ns(NOW))
    first = sidecar.sent("ResolveIdentifier")[0]
    assert [(i.scheme, i.value, i.source) for i in first.identifiers] == [
        ("figi", "BBG000B9XRY4", ""),
        ("symbol", "AAPL", "snaptrade"),
    ]
    assert first.as_of_ns == ns(NOW.replace(hour=0))
    assert (first.exchange_mic, first.currency) == ("XNAS", "USD")
    cash = sidecar.sent("ResolveIdentifier")[2]
    assert [(i.scheme, i.value) for i in cash.identifiers] == [("iso4217", "CAD")]


async def test_a_row_says_its_side_and_what_was_and_was_not_reported() -> None:
    sidecar = Sidecar()
    await Recorder(sidecar.plugin()).record(ACCOUNT, STATEMENT, ns(NOW))
    rows = sidecar.sent("RecordHolding")
    assert [ops.HoldingSide.Name(row.side) for row in rows] == [
        "HOLDING_SIDE_LONG",
        "HOLDING_SIDE_SHORT",
        "HOLDING_SIDE_LONG",
    ]
    # A value SnapTrade did not report is left unset, never zero; cash's is its amount.
    assert [row.HasField("market_value") for row in rows] == [False, False, True]
    assert meridian.as_money(rows[2].market_value) == meridian.Money(Decimal("200.00"), "CAD")
    # Contract v11 deprecates both marks: a currency closed by a rule says so
    # in its provenance, and the street counts each asset once.
    assert not any(row.currency_assumed or row.also_counted_in_cash for row in rows)
    # No settled quantity was closed for these rows, so none is sent.
    assert not any(row.HasField("settle_date_quantity") for row in rows)
    # Each row names the raw record it was converted from, kept by this instance.
    assert {row.raw_record.instance_id for row in rows} == {"snaptrade"}
    assert [row.raw_record.key.rsplit("/", 1)[1] for row in rows] == [
        "positions",
        "positions",
        "balances",
    ]


async def test_a_fund_snaptrade_counts_in_cash_is_stated_a_money_market_fund() -> None:
    sidecar = Sidecar()
    fund = Holding(
        identifiers=(Identifier("symbol", "SWVXX", "snaptrade"),),
        description="Money market",
        kind="mutualfund",
        side=Side.LONG,
        quantity=Decimal("10"),
        currency="USD",
        cash_equivalent=True,
        asset_class="fund",
    )
    statement = Statement(STATEMENT.external_statement_id, "2026-09-28", ns(NOW), (fund,))
    await Recorder(sidecar.plugin()).record(ACCOUNT, statement, ns(NOW))
    (resolve,) = sidecar.sent("ResolveIdentifier")
    assert resolve.stated_asset_class == ops.ASSET_CLASS_FUND
    assert resolve.stated_instrument_type == ops.INSTRUMENT_TYPE_MONEY_MARKET_FUND
    # Not marked: the cash beside it is sent net of it (normalise.py).
    (row,) = sidecar.sent("RecordHolding")
    assert not row.also_counted_in_cash


async def test_a_statement_names_its_external_account_and_institution() -> None:
    sidecar = Sidecar()
    await Recorder(sidecar.plugin()).record(ACCOUNT, STATEMENT, ns(NOW))
    (opened,) = sidecar.sent("RecordHoldingsStatement")
    assert opened.external_account_id == "ALPACA:INST-1"
    assert opened.institution == "Alpaca"


async def test_buying_power_in_one_currency_is_the_accounts_one_set_of_figures() -> None:
    sidecar = Sidecar()
    await Recorder(sidecar.plugin()).record(ACCOUNT, STATEMENT, ns(NOW))
    (opened,) = sidecar.sent("RecordHoldingsStatement")
    (figures,) = opened.figures
    # No segment: the account's figures as a whole. SnapTrade names none.
    assert figures.segment == ""
    assert meridian.as_money(figures.buying_power) == meridian.Money(Decimal("3046.90"), "USD")
    assert not figures.HasField("net_liquidation")
    # What SnapTrade has no field for is left unset, never derived.
    for unreported in (
        "margin_requirement",
        "maintenance_excess",
        "initial_margin",
        "variation_margin",
    ):
        assert not figures.HasField(unreported)
    assert list(figures.collateral) == []
    # The flat figures a plugin before v7 sent are sent no more.
    for flat in ("buying_power", "margin_requirement", "maintenance_excess"):
        assert not opened.HasField(flat)


async def test_the_accounts_total_value_is_its_net_liquidation_exactly() -> None:
    sidecar = Sidecar()
    total = meridian.Money(Decimal("9876.54"), "USD")
    await Recorder(sidecar.plugin()).record(
        ACCOUNT, replace(STATEMENT, net_liquidation=total), ns(NOW)
    )
    (figures,) = sidecar.sent("RecordHoldingsStatement")[0].figures
    assert meridian.as_money(figures.net_liquidation) == total
    assert (figures.net_liquidation.amount.low, figures.net_liquidation.amount.scale) == (
        987654,
        2,
    )
    assert meridian.as_money(figures.buying_power) == meridian.Money(Decimal("3046.90"), "USD")


async def test_buying_power_in_several_currencies_is_not_summed() -> None:
    sidecar = Sidecar()
    several = Statement(
        STATEMENT.external_statement_id,
        "2026-09-28",
        ns(NOW),
        (),
        (meridian.Money(Decimal("1"), "USD"), meridian.Money(Decimal("2"), "CAD")),
        net_liquidation=meridian.Money(Decimal("10"), "USD"),
    )
    await Recorder(sidecar.plugin()).record(ACCOUNT, several, ns(NOW))
    (opened,) = sidecar.sent("RecordHoldingsStatement")
    (figures,) = opened.figures
    assert not figures.HasField("buying_power")
    assert meridian.as_money(figures.net_liquidation) == meridian.Money(Decimal("10"), "USD")


async def test_a_statement_with_no_figure_reported_sends_no_set() -> None:
    sidecar = Sidecar()
    await Recorder(sidecar.plugin()).record(
        ACCOUNT, replace(STATEMENT, buying_power=()), ns(NOW)
    )
    (opened,) = sidecar.sent("RecordHoldingsStatement")
    assert list(opened.figures) == []


async def test_a_rows_average_cost_and_lots_reach_the_sidecar_as_reported() -> None:
    sidecar = Sidecar()
    long = replace(
        LONG,
        average_cost=meridian.Money(Decimal("198.10"), "USD"),
        lots=(
            Lot(Decimal("10"), meridian.Money(Decimal("1890.00"), "USD"), "2024-03-11"),
            Lot(Decimal("2.500"), None, ""),
            Lot(Decimal("0.012345678"), meridian.Money(Decimal("-5.25"), "USD"), ""),
        ),
    )
    short = replace(
        SHORT, lots=(Lot(Decimal("-40"), meridian.Money(Decimal("160.80"), "USD")),)
    )
    statement = replace(STATEMENT, holdings=(long, short, CASH))
    await Recorder(sidecar.plugin()).record(ACCOUNT, statement, ns(NOW))
    first, second, cash = sidecar.sent("RecordHolding")
    # Per unit, as SnapTrade reports it; never multiplied into a total.
    assert meridian.as_money(first.average_cost) == meridian.Money(Decimal("198.10"), "USD")
    assert [
        (
            meridian.as_decimal(lot.quantity),
            meridian.as_money(lot.cost) if lot.HasField("cost") else None,
            lot.acquired_date,
        )
        for lot in first.lots
    ] == [
        (Decimal("10"), meridian.Money(Decimal("1890.00"), "USD"), "2024-03-11"),
        (Decimal("2.500"), None, ""),
        (Decimal("0.012345678"), meridian.Money(Decimal("-5.25"), "USD"), ""),
    ]
    # Each lot's scale crosses the wire as written.
    assert [(lot.quantity.low, lot.quantity.scale) for lot in first.lots][1:] == [
        (2500, 3),
        (12345678, 9),
    ]
    # A short holding's lots are short.
    assert [meridian.as_decimal(lot.quantity) for lot in second.lots] == [Decimal("-40")]
    assert not second.HasField("average_cost")
    # A cash row has no cost and no lots.
    assert not cash.HasField("average_cost") and list(cash.lots) == []


async def test_a_total_cost_basis_and_a_margin_requirement_are_never_sent() -> None:
    sidecar = Sidecar()
    long = replace(LONG, average_cost=meridian.Money(Decimal("198.10"), "USD"))
    await Recorder(sidecar.plugin()).record(
        ACCOUNT, replace(STATEMENT, holdings=(long, SHORT, CASH)), ns(NOW)
    )
    for row in sidecar.sent("RecordHolding"):
        assert not row.HasField("cost_basis")
        assert not row.HasField("margin_requirement")


async def test_a_minted_record_is_recorded_against_and_counted() -> None:
    sidecar = Sidecar(resolve=lambda p: found("LCL-1", minted=True))
    outcome = await Recorder(sidecar.plugin()).record(ACCOUNT, STATEMENT, ns(NOW))
    assert {row.instrument_id for row in sidecar.sent("RecordHolding")} == {"LCL-1"}
    assert outcome.minted == 3
    # The instrument store reports a not-found; the connector does not.
    assert sidecar.sent("ReportMissingInstrument") == []


async def test_an_ambiguous_row_is_still_recorded_and_its_miss_published_once() -> None:
    sidecar = Sidecar(
        resolve=lambda p: ambiguous() if p.identifiers[0].value == "ZZTOP" else found()
    )
    outcome = await Recorder(sidecar.plugin()).record(ACCOUNT, STATEMENT, ns(NOW))
    rows = sidecar.sent("RecordHolding")
    assert len(rows) == 3
    assert rows[1].instrument_id == ""
    assert [(i.scheme, i.value) for i in rows[1].unresolved_identifiers] == [
        ("symbol", "ZZTOP")
    ]
    (miss,) = sidecar.sent("ReportMissingInstrument")
    assert miss.reason == ops.MISS_REASON_AMBIGUOUS and miss.source == "snaptrade"
    assert miss.asset_class == ops.ASSET_CLASS_EQUITY
    assert outcome.ambiguous == 1


async def test_a_miss_carries_the_rows_asset_class_and_none_where_it_has_none() -> None:
    sidecar = Sidecar(resolve=lambda p: ambiguous())
    await Recorder(sidecar.plugin()).record(ACCOUNT, STATEMENT, ns(NOW))
    # The two stocks are equity; the cash is a holding of its cash instrument.
    misses = sidecar.sent("ReportMissingInstrument")
    assert [ops.AssetClass.Name(miss.asset_class) for miss in misses] == [
        "ASSET_CLASS_EQUITY",
        "ASSET_CLASS_EQUITY",
        "ASSET_CLASS_CASH",
    ]


async def test_a_redelivered_statement_records_no_rows() -> None:
    sidecar = Sidecar(already_recorded=True)
    outcome = await Recorder(sidecar.plugin()).record(ACCOUNT, STATEMENT, ns(NOW))
    assert outcome.already_recorded and sidecar.sent("RecordHolding") == []


async def test_a_refused_row_stops_the_statement_and_is_not_retried() -> None:
    def refuse(name: str, params: Any) -> Exception | None:
        if name == "RecordHolding":
            return meridian.CallFailed("RecordHolding", "refused", "no link for ALPACA:INST-1")
        return None

    sidecar = Sidecar(refuse=refuse)
    outcome = await Recorder(sidecar.plugin()).record(ACCOUNT, STATEMENT, ns(NOW))
    assert outcome.recorded == 0 and "no link" in outcome.stopped
    assert len(sidecar.sent("RecordHoldingsStatement")) == 1
    # Refused for something other than a missing link.
    assert not outcome.unlinked


async def test_a_statement_refused_for_want_of_a_link_records_no_row_and_says_so() -> None:
    # Since v7 the statement names its account, and is refused before any
    # row. The SDK raises NotLinked by the refusal's code; its words are not
    # read.
    def refuse(name: str, params: Any) -> Exception | None:
        if name == "RecordHoldingsStatement":
            return meridian.NotLinked("RecordHoldingsStatement", "reworded at some release")
        return None

    sidecar = Sidecar(refuse=refuse)
    outcome = await Recorder(sidecar.plugin()).record(ACCOUNT, STATEMENT, ns(NOW))
    assert outcome.unlinked and outcome.recorded == 0 and outcome.statement_id == ""
    assert "reworded at some release" in outcome.stopped
    assert sidecar.sent("RecordHoldingsStatement") == []
    assert sidecar.sent("RecordHolding") == []


async def test_a_row_refused_for_want_of_a_link_says_so() -> None:
    # A link removed between the statement and its rows.
    def refuse(name: str, params: Any) -> Exception | None:
        if name == "RecordHolding":
            return meridian.NotLinked("RecordHolding", "reworded at some release")
        return None

    sidecar = Sidecar(refuse=refuse)
    outcome = await Recorder(sidecar.plugin()).record(ACCOUNT, STATEMENT, ns(NOW))
    assert outcome.unlinked and outcome.recorded == 0
    assert "reworded at some release" in outcome.stopped


async def test_a_refusal_saying_not_linked_without_the_code_is_not_taken_for_one() -> None:
    def refuse(name: str, params: Any) -> Exception | None:
        if name == "RecordHolding":
            return meridian.CallFailed(
                "RecordHolding", "refused", "external account ALPACA:INST-1 is not linked"
            )
        return None

    sidecar = Sidecar(refuse=refuse)
    outcome = await Recorder(sidecar.plugin()).record(ACCOUNT, STATEMENT, ns(NOW))
    assert outcome.stopped and not outcome.unlinked


async def test_sync_status_carries_a_state_and_both_freshnesses() -> None:
    """When SnapTrade last synced, apart from when what it served is as of;
    and a state closed by a rule, with the rule in the detail."""
    sidecar = Sidecar()
    await Recorder(sidecar.plugin()).report_sync(ACCOUNT, SIGN_IN, ns(NOW))
    (sent,) = sidecar.sent("ReportSyncStatus")
    assert sent.state == ops.SYNC_STATE_NEEDS_SIGN_IN
    assert not sent.connection_healthy
    assert sent.holdings_as_of_ns == ns(NOW - timedelta(days=5))
    assert sent.last_synced_at_ns == ns(NOW - timedelta(days=4))
    assert sent.history_as_of_ns == ns(NOW.replace(hour=0))
    assert sent.status_detail == (
        'SnapTrade disabled it. state derived by the rule "SnapTrade disabled the connection".'
    )
    assert sent.external_account_id == "ALPACA:INST-1"


async def test_a_state_snaptrade_said_carries_its_detail_alone() -> None:
    sidecar = Sidecar()
    stale = Freshness(SyncState.STALE, NOW - timedelta(days=2), None, "As of then.")
    await Recorder(sidecar.plugin()).report_sync(ACCOUNT, stale, ns(NOW))
    (sent,) = sidecar.sent("ReportSyncStatus")
    assert sent.status_detail == "As of then." and sent.last_synced_at_ns == 0


async def test_delayed_by_design_is_healthy() -> None:
    sidecar = Sidecar()
    late = Freshness(SyncState.DELAYED_BY_DESIGN, NOW, NOW.date(), "")
    await Recorder(sidecar.plugin()).report_sync(ACCOUNT, late, ns(NOW))
    (sent,) = sidecar.sent("ReportSyncStatus")
    assert sent.connection_healthy and sent.state == ops.SYNC_STATE_DELAYED_BY_DESIGN


async def test_holdings_unavailable_is_sent_as_the_contracts_own_state() -> None:
    """Not stale: SYNC_STATE_HOLDINGS_UNAVAILABLE, ruled for exactly this
    (the product owner, 2026-09-28)."""
    sidecar = Sidecar()
    hidden = Freshness(
        SyncState.HOLDINGS_UNAVAILABLE, NOW, NOW.date(), "Not shown to SnapTrade."
    )
    await Recorder(sidecar.plugin()).report_sync(ACCOUNT, hidden, ns(NOW))
    (sent,) = sidecar.sent("ReportSyncStatus")
    assert sent.state == ops.SYNC_STATE_HOLDINGS_UNAVAILABLE
    assert not sent.connection_healthy
    assert sent.status_detail == "Not shown to SnapTrade."


def test_every_sync_state_has_the_contracts_value() -> None:
    """Each of the plugin's states is sent as the contract's value of the same
    name, so none is sent as another."""
    for state in SyncState:
        assert _SYNC_STATE[state] == ops.SyncState.Value(f"SYNC_STATE_{state.name}")


async def test_the_accounts_a_connection_reaches_are_reported(sidecar: Sidecar) -> None:
    await Recorder(sidecar.plugin()).report_accounts([ACCOUNT])
    (sent,) = sidecar.sent("ReportExternalAccounts")
    # The kind converted from SnapTrade's type; the type itself is not sent.
    assert list(sent.accounts) == [
        ops.ExternalAccount(
            external_account_id="ALPACA:INST-1",
            name="Margin",
            account_kind=ops.ACCOUNT_KIND_MARGIN,
        )
    ]


async def test_a_type_that_says_no_kind_travels_as_reported(sidecar: Sidecar) -> None:
    joint = replace(ACCOUNT, account_type="Joint", kind="")
    await Recorder(sidecar.plugin()).report_accounts([joint])
    ((sent,),) = [list(call.accounts) for call in sidecar.sent("ReportExternalAccounts")]
    assert sent.account_kind == ops.ACCOUNT_KIND_UNSPECIFIED
    assert (sent.account_kind_as_reported.scheme, sent.account_kind_as_reported.code) == (
        "snaptrade:account-type",
        "Joint",
    )


# ── The tripwire for the next SDK ────────────────────────────────────────────

# Every parameter of the pinned SDK's operations this plugin knows. Moving the
# pin to an SDK with an operation or a parameter outside this set fails here,
# naming it, so what the new contract adds is decided rather than missed.
KNOWN_PARAMETERS = {
    "report_sync_status": {
        "source",
        "last_synced_at_ns",
        "connection_healthy",
        "status_detail",
        "observed_at_ns",
        "external_account_id",
        "state",
        "holdings_as_of_ns",
        "history_as_of_ns",
        # v14: the first date SnapTrade's history of the account reaches.
        "history_from",
    },
    # v14 (W2.10): each activity, as the custodian states it; a person's
    # acting_for is for an activity a person reports, which this plugin's
    # reads never are.
    "record_activity": {"acting_for", "activity", "external_account_id", "source"},
    # Used (v15): an activity held under a code a person has since linked,
    # re-resolved through the link, naming who and when; never acting for one.
    "re_resolve_activity": {
        "acting_for",
        "external_account_id",
        "external_activity_id",
        "instrument_id",
        "provenance",
        "resolved_at_ns",
        "source",
    },
    # Known, and not used (v14): operations and the dashboard read the
    # street's activity and sync statuses; custody reports them.
    "list_activities": {
        "account_id",
        "cursor",
        "page_size",
        "since",
        "trade_date_from",
        "trade_date_to",
    },
    "list_sync_statuses": {"account_id", "cursor", "page_size", "since"},
    "record_holdings_statement": {
        "source",
        "external_statement_id",
        "as_of_date",
        "read_at_ns",
        "expected_rows",
        "acting_for",
        # Known, and never sent: v7 refuses the flat figures beside `figures`.
        "buying_power",
        "margin_requirement",
        "maintenance_excess",
        "currency_assumed",
        "external_account_id",
        "institution",
        "figures",
        # Known, and never sent (v8): SnapTrade does not say whether an
        # account is pledged, so the statement leaves it unstated.
        "security_interest",
        # v11: the raw record it was converted from, and the provenance of
        # each value closed rather than read.
        "raw_record",
        "provenance",
    },
    "record_holding": {
        "statement_id",
        "instrument_id",
        "unresolved_identifiers",
        "quantity",
        "market_value",
        "external_account_id",
        "acting_for",
        "side",
        "settle_date_quantity",
        "currency_assumed",
        "also_counted_in_cash",
        "average_cost",
        "lots",
        # Known, and never sent: SnapTrade reports no total cost basis and no
        # margin requirement for a holding.
        "cost_basis",
        "margin_requirement",
        # Known, and never sent (v8): SnapTrade reports no available or
        # not-available quantity and no encumbered sub-balance for a holding,
        # and the contract has them stated as the venue says, never derived.
        "available_quantity",
        "not_available_quantity",
        "available_basis",
        "encumbrances",
        # v11: the raw record, the provenance, the pending quantities by value
        # date; and, known and never sent, a backfill: a row is not sent again.
        "raw_record",
        "provenance",
        "pending",
        "backfill",
    },
    "resolve_identifier": {
        "identifiers",
        "as_of_ns",
        "exchange_mic",
        "currency",
        # v10 and v11: what SnapTrade states of the security, offered only.
        "stated_asset_class",
        "stated_currency",
        "stated_description",
        "stated_instrument_type",
        # v23: where SnapTrade states no currency, the one supplied or
        # derived, offered with its provenance.
        "provenance",
    },
    "report_missing_instrument": {
        "source",
        "asset_class",
        "asset_class_as_reported",
        "identifiers",
        "as_of_ns",
        "reason",
        "observed_at_ns",
    },
    "report_external_accounts": {"accounts"},
    "link_external_account": {
        "external_account_id",
        "account_id",
        "new_account_name",
        # Pre-filled from the venue on the Accounts tab (W6.4).
        "new_account_custodian",
        "new_account_type",
        # Known, and not sent: the admin gives an owner and a note on the
        # dashboard (W6.3), not here.
        "new_account_owner",
        "new_account_note",
        "acting_for",
    },
    "read_accounts_for_linking": {"acting_for"},
    # Known, and not used: a custody plugin records the street; it neither
    # reads nor hears it.
    "list_custodial_positions": {
        "account_id",
        "cursor",
        "include_unresolved",
        "page_size",
        "since",
    },
    "list_statements": {"account_id", "as_of_date", "cursor", "page_size", "since"},
    "receive": {
        "custodial_position_updated",
        "seed",
        "statement_recorded",
        # Known, and not heard (v8): the book of record's deliveries go to the
        # roles that read the book, and custody is not one of them.
        "position_changed",
        "break_changed",
        "account_figures_recorded",
        "account_attribute_changed",
        # Known, and not heard (v14): the street's activity and sync statuses
        # are delivered to operations, and custody reports them.
        "activity_recorded",
        "sync_status_recorded",
        # Known, and not heard (v15): a re-resolution is delivered to
        # operations; custody sends it, and the street answers its own.
        "activity_re_resolved",
        # Known, and not heard (v19): trades and quotes are the lake's, heard
        # by the reading roles.
        "trades_recorded",
        "quotes_recorded",
        # Known, and not heard (v18): the lake's prices and bars go to the
        # reading roles, and the wants to a dgm; custody is neither.
        "prices_recorded",
        "bars_recorded",
        "observations_wanted",
        "want_withdrawn",
        # v22 (connectivity; trading.py): the placements naming this instance
        # and the trading instructions, heard; and from v23 the executions it
        # reported, as the book recorded them, so it hears a fill on no
        # placement accepted or answered DK. Known, and not heard: the order,
        # unplaced fact and locate deliveries go to the roles that read the
        # book, and a catalogue to the ems and oms.
        "placement_updated",
        "trading_instruction_changed",
        "order_updated",
        "execution_recorded",
        "unplaced_fact_recorded",
        "locate_updated",
        "report_venue_catalogue",
        "subjects",
        # Known, and not heard (v23): a market's outcomes go to the readers.
        "outcomes_recorded",
        # Known, and not heard (v24): a booking and an alert go to the roles
        # that book and review them; a merged instrument's replacement is
        # heard by a plugin keeping instrument IDs, and this one keeps none.
        "booking_updated",
        "alert_changed",
        "replace_merged_instrument",
    },
    # Known, and not used (v8): the book of record is an operations plugin's
    # to write and five other roles' to read (operations, portfolio,
    # reporting, compliance, oms); custody writes and reads only the street.
    "record_opening_balance": {
        "account_id",
        "acting_for",
        "as_of_date",
        "idempotency_key",
        "positions",
        "reason",
        "replaces_entry_id",
        "sources",
    },
    "record_break": {
        "account_id",
        "acting_for",
        "book_watermark",
        "break_id",
        "business_date",
        "candidate_causes",
        "category",
        "differences",
        "figure",
        "idempotency_key",
        "position",
        "street",
        # Known, and not used (v25): an operations plugin's break names what
        # did not resolve; custody records no break.
        "unresolved_identifiers",
    },
    "record_account_figures": {
        "account_id",
        "acting_for",
        "agreements",
        "business_date",
        "idempotency_key",
        "source",
    },
    "record_encumbrances": {
        "account_id",
        "acting_for",
        "business_date",
        "idempotency_key",
        "positions",
        "source",
    },
    "handle_break": {
        "account_id",
        "acting_for",
        "break_id",
        "confirmed_cause",
        "handling",
        "idempotency_key",
        "reason",
    },
    "resolve_break": {
        "account_id",
        "acting_for",
        "adjustment",
        "break_ids",
        "entries",
        "explanation",
        "idempotency_key",
        "reason",
        "reversal",
    },
    "close_breaks_as_cleared": {
        "account_id",
        "acting_for",
        "break_ids",
        "cleared_at",
        "idempotency_key",
        "reason",
    },
    "list_positions": {"account_id", "at", "business_date", "cursor", "page_size", "since"},
    "list_breaks": {"account_id", "cursor", "page_size", "since", "states"},
    "list_account_figures": {
        "account_id",
        "agreement",
        "at",
        "cursor",
        "from_date",
        "page_size",
        "since",
        "to_date",
    },
    "list_account_attributes": {"account_id", "cursor", "page_size", "since"},
    # v22 (W3.6, connectivity): the record of the instrument a placement
    # names, for the ticker SnapTrade places it under (trading.py).
    "resolve_instrument": {"as_of_ns", "instrument_id"},
    # Known, and not used (v18): the lake is a dgm's to write and the reading
    # roles' to read, and venues are resolved by whoever records a price on
    # one; custody records the street, whose instruments name no venue.
    "record_prices": {"acting_for", "prices", "want_id"},
    "record_bars": {"acting_for", "bars", "want_id"},
    "decline_want": {"acting_for", "reason", "subjects", "want_id"},
    "list_prices": {
        "as_of_ns",
        "at_ns",
        "business_date",
        "cursor",
        "kinds",
        "page_size",
        "sources",
        "subjects",
        "valid_from_ns",
        "valid_until_ns",
    },
    "list_bars": {
        "as_of_ns",
        "at_ns",
        "business_date",
        "cursor",
        "interval_ns",
        "page_size",
        "sources",
        "subjects",
        "valid_from_ns",
        "valid_until_ns",
    },
    "list_datasets": set(),
    # v25: a venue resolved by its ID beside its identifiers (D9).
    "resolve_venue": {"as_of_ns", "identifiers", "venue_id"},
    "report_missing_venue": {"as_of_ns", "identifiers", "observed_at_ns", "reason", "source"},
    # Known, and not used (v19, v20): trades and quotes are a data plugin's to
    # record and the reading roles' to read; custody records the street.
    "record_trades": {"acting_for", "trades", "want_id"},
    "record_quotes": {"acting_for", "quotes", "want_id"},
    "list_trades": {
        "after_watermark",
        "as_of_ns",
        "cursor",
        "latest_recorded",
        "page_size",
        "sources",
        "subjects",
        "valid_from_ns",
        "valid_until_ns",
    },
    "list_quotes": {
        "as_of_ns",
        "at_ns",
        "cursor",
        "page_size",
        "sources",
        "subjects",
        "valid_from_ns",
        "valid_until_ns",
    },
    # v22 (W11, connectivity; trading.py): what a connectivity plugin sends
    # and reads. Its own placements' facts, each with its raw record (a
    # replace among them, with the quantity and limit now); a stop's cancel
    # reported naming the stop; the instructions in force and the book's
    # orders read; the catalogue published; each fill's settlement date
    # asked of the date read (W3.20). It records no order and creates no
    # placement (ruled 2026-10-13: the oms's and the ems's, below).
    "report_placement": {
        "acting_for",
        "event",
        "limit_price",
        "placement_id",
        "quantity",
        "raw_record",
        "rejection_as_reported",
        "rejection_reason",
        "restatement_reason",
        "routed_at_ns",
        "routed_precision",
        "status_as_reported",
        "unknown_reason",
        "venue_order_id",
    },
    "report_execution": {
        "account_id",
        "acting_for",
        "computed_settlement_date",
        "counterparty",
        "effective_date",
        "fees",
        "finality",
        "given_up_to",
        "idempotency_key",
        "kind",
        "liquidity_indicator",
        "order_id",
        "placement_id",
        "price",
        "prior_uti",
        "quantity",
        "raw_record",
        "settlement_counterparty",
        "settlement_date",
        "settlement_date_basis",
        "source",
        "supersedes",
        "trade_chain_reference",
        "trade_date",
        "trade_time_ns",
        "upi",
        "uti",
        "uti_generated_by",
        "venue_account_code",
        "venue_execution_id",
        "venue_id",
        # Used (v23): a fill on no placement names its instrument, side, the
        # venue's order and its client order ID where SnapTrade states one;
        # a fill on a placement names the venue order too.
        "instrument_id",
        "side",
        "venue_order_id",
        "venue_client_order_id",
    },
    "report_charge": {
        "acting_for",
        "fee",
        "placement_id",
        "raw_record",
        "settlement_date",
        "trade_date",
    },
    "request_placement_change": {
        "acting_for",
        "cause",
        "change",
        "idempotency_key",
        "placement_id",
        "reason",
    },
    # v23's `execution_state` known, and not used: the unmatched listing is
    # the ems's and operations' read; this plugin hears its fills' answers.
    "list_orders": {"account_id", "cursor", "page_size", "execution_state", "since"},
    "list_trading_instructions": {"cursor", "page_size", "since"},
    "report_venue_catalogue": {"catalogue"},
    "compute_date": {"as_of_ns", "business_day", "roll", "settlement", "spot", "tenor"},
    # Known, and not used (v22): a calendar's days (SnapTrade's work asks
    # the date read for each fill's date, never a calendar); a locate
    # (SnapTrade has no locate API); the venue catalogue read back (it
    # publishes its own); and what the portfolio, oms, ems, compliance and
    # operations roles send -- an order recorded is the oms's, a placement
    # the ems's, and the sidecar refuses either from a connectivity plugin.
    "record_order": {
        "account_id",
        "acting_for",
        "idempotency_key",
        "instrument_id",
        "limit_price",
        "order_type",
        "origin",
        "quantity",
        "side",
        "sleeve",
        "time_in_force",
        "venue_order_id",
    },
    "create_placement": {
        "acting_for",
        "axe_id",
        "connectivity_instance_id",
        "destination",
        "handling_instructions",
        "idempotency_key",
        "limit_price",
        "order_id",
        "quantity",
        "rfq_id",
        "route_type",
        "strategy",
        "time_in_force",
        "venue_account_code",
    },
    "read_calendar": {"as_of_ns", "calendar_id", "from_date", "to_date"},
    "record_locate": {
        "acting_for",
        "answer",
        "idempotency_key",
        "instrument_id",
        "locate_record_id",
        "quantity_requested",
        "raw_record",
        "venue_account_code",
    },
    "list_locates": {"account_id", "cursor", "page_size", "since"},
    "record_locate_use": {"acting_for", "idempotency_key", "locate_record_id", "use"},
    "read_venue_catalogue": {"acting_for", "connectivity_instance_id", "venue_account_code"},
    "read_order_history": {"account_id", "cursor", "order_id", "page_size"},
    "map_venue_account": {
        "account_id",
        "acting_for",
        "connectivity_instance_id",
        "venue_account_code",
    },
    "propose_order": {
        "account_id",
        # v24: a block's member orders, the oms's to name.
        "block_member_order_ids",
        "acting_for",
        "allocation_instruction",
        "axe_counterparty_id",
        "axe_source",
        "capacity",
        "cash_quantity",
        "customer_id",
        "dealt_currency",
        "decided_by",
        "direction",
        "execution_instructions",
        "expire_date",
        "expire_time_ns",
        "firm_designated_id",
        "handling_instructions",
        "idempotency_key",
        "instrument_id",
        "legs",
        "limit_price",
        "link",
        "locate_id",
        "lot_relief_method",
        "order_type",
        "parties",
        "quantity",
        "quote_at_receipt",
        # v23: an FX order's quote currency, portfolio's to propose.
        "quote_currency",
        "received_at_ns",
        "received_precision",
        "relieve_lot_ids",
        "represents_order_ids",
        "side",
        "sleeve",
        "stop_price",
        "time_in_force",
    },
    "release_order": {"acting_for", "idempotency_key", "order_id"},
    "request_order_change": {"acting_for", "change", "idempotency_key", "order_id", "reason"},
    "set_working_condition": {
        "acting_for",
        "condition",
        "idempotency_key",
        "order_id",
        "reason",
        "set",
    },
    "decide_approval": {"acting_for", "decision", "idempotency_key", "order_id", "reason"},
    "record_check": {
        "acting_for",
        "idempotency_key",
        "note",
        "order_id",
        "order_version",
        "placement_id",
        "rule",
        "verdict",
    },
    "acknowledge_flag": {"acting_for", "condition", "idempotency_key", "order_id", "reason"},
    # Known, and not used (v23): the accept and the DK are the ems's acts,
    # which this plugin only hears answered (ExecutionRecorded); a value-date
    # roll is a rolling venue's, and SnapTrade's trades settle; a market's
    # outcome is a data plugin's to record and the readers' to list.
    "accept_execution": {"acting_for", "execution_ids", "new_order", "note", "order_id"},
    "dont_know_execution": {"acting_for", "execution_ids", "reason", "text"},
    "report_value_date_roll": {
        "acting_for",
        "charge_ids",
        "from_value_date",
        "idempotency_key",
        "pair",
        "raw_record",
        "rolled_at_ns",
        "to_value_date",
        "value_date_basis",
        "venue_account_code",
    },
    "record_outcomes": {"acting_for", "outcomes"},
    "list_outcomes": {"after_watermark", "as_of_ns", "subjects"},
    # Known, and not used (v24): booking, allocation, sleeves and alerts are
    # the book's and the oms's, operations' and compliance's; a give-up is a
    # prime broker's or a confirmation service's, and SnapTrade is neither;
    # the replacements and templates are read by plugins keeping instrument
    # IDs or minting from a template, and this one does neither.
    "book_order": {"acting_for", "allocations", "execution_ids", "hand_off", "order_id"},
    "correct_booking": {
        "acting_for",
        "allocations",
        "booking_id",
        "effective_date",
        "kind",
        "reason",
    },
    "hand_off_booking": {"acting_for", "booking_id", "close"},
    "list_bookings": {"account_id", "cursor", "page_size", "since", "state"},
    "set_allocation_instruction": {
        "acting_for",
        "allocation_instruction",
        "order_id",
        "reason",
    },
    "record_sleeve_cross": {"acting_for", "sleeve_cross"},
    "raise_alert": {"account_id", "acting_for", "kind", "reason", "subject"},
    "acknowledge_alert": {"acting_for", "alert_id", "reason"},
    "list_alerts": {"account_id", "cursor", "kind", "page_size", "since", "state"},
    "report_give_up": {
        "acting_for",
        "allocation_id",
        "answer",
        "execution_id",
        "prime_reference",
        "raw_record",
        "reason",
    },
    "list_replacements": {"after_sequence"},
    "list_templates": {"asset_class"},
    "read_template": {"template_id", "template_version"},
    "offer_template_fields": {
        "acting_for",
        "fields",
        "instrument_id",
        "template_id",
        "template_version",
    },
    # Known, and not used: whether orders may name a local record, the
    # oms's and the ems's to weigh; from v25 connectivity is not granted it.
    "read_local_record_orders": set(),
}


def test_the_pinned_sdk_has_no_operation_or_parameter_this_plugin_does_not_know() -> None:
    import inspect

    from meridian.operations import Operations

    operations = {
        name
        for name, member in vars(Operations).items()
        if inspect.iscoroutinefunction(member) and not name.startswith("_")
    }
    unknown_operations = operations - set(KNOWN_PARAMETERS)
    assert not unknown_operations, f"decide what to do with: {unknown_operations}"
    for name in operations:
        parameters = set(inspect.signature(getattr(Operations, name)).parameters) - {"self"}
        unknown = parameters - KNOWN_PARAMETERS[name]
        assert not unknown, f"{name} has parameters this plugin does not know: {unknown}"
