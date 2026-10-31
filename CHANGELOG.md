# Changelog

What each release of the SnapTrade plugin changed. Releases before 0.11.0
are described in their commits (`git log`) and the README's "Depends on".

## 0.21.1

SDK 0.33.4: raw-record writes and enum conversion fixes (ruling 937).

- **The pins:** open-meridian 0.33.1 to 0.33.4, and plugin-python:0.33.4 by
  tag and digest (`meridian plugin migrate --to 0.33.4`: only the pins move),
  and the pin README.md states beside them.

## 0.21.0

Contract v28, on open-meridian 0.33.1 (plugin-common-v28), on core v28's
harness.

- **The session's latency, reported** (ruling 518, required again from
  contract v28): `__main__` gives the SDK's Trader a `roles.Latency`, so each
  call to SnapTrade's trading API has its round trip recorded, a placement
  its acknowledgement and a fill its delay, reported every 10 seconds
  (`ReportLatency`); `reports-a-sessions-round-trip` is presented.
- **Not presented** (ruling 602): connectivity -- an option's expiry and a
  package's fill (it places single-leg equity orders); custody -- an
  assignment: this version reports a broker's option assignment, exercise
  or expiry as an activity of a kind not known, SnapTrade's type beside it,
  never as a lifecycle event (ruling 563), a later version's work.
- **tests/test_contract.py**: the SDK's v28 operations and parameters
  (chains, filings, lifecycle events, derived series, scenarios, packages'
  fields, the stale-reference read) known and unused.
- **The pins:** open-meridian 0.33.1 and plugin-python:0.33.1 by tag and
  digest; core v28's runtime and harness at `8def82a` (chart 0.1.313) by
  digest; `meridian` stays 0.1.52 in check.yaml.
- The README's "not released" is gone, and its release number current.

## 0.20.0

Contract v27, on open-meridian 0.32.2 (meridian-design plans/the-v27-build,
§G; spec/the-contract-gaps-v26-and-v27-close, B).

- **A fill on no placement, resolved by the SDK** (0.32.0): SnapTrade's own
  resolution, which 0.19.0 restored in its code (its FIGI and stated
  currency, kept beside each order), is deleted. `order_of` reads what
  SnapTrade states of an order's listing onto the order
  (`VenueOrder.identifiers`, its FIGI; `.currency`), and the SDK's Trader
  resolves it by all of them and the symbol under SnapTrade's source, priced
  in the stated currency, else the record's. The tests that held 0.19.0's
  resolution pass unchanged.
- **Placement types** (contract v27, W11.20): DMA alone. SnapTrade's
  `POST /trade/place` sends an order straight to the brokerage -- no broker
  algorithm, care desk or request for quote -- so a trading account's
  catalogue lists `PLACEMENT_TYPE_DMA`, a read-only one none, and an algo,
  care or RFQ placement is rejected `unsupported`, nothing sent.
- **Connections' row actions in the kit's row menu**: Reconnect (first
  where the connection needs sign-in), Refresh (on a delayed connection) and
  Enable trading (for the connectivity role's admin, on a read-only one) sit
  in each row's `…` menu at every width, one menu open at a time. "Enable
  trading" was clipped at 1280 px beside the row's other buttons; a phone
  now offers a delayed connection's Refresh too.
- **The suites**: the connectivity suite's
  `records-a-dealers-quote-on-an-rfq-placement` not presented (SnapTrade
  takes DMA alone), and `reports-a-sessions-round-trip` not presented, "core
  receives ReportLatency from contract v28, ruling 515" (ruling 518);
  `tests/test_contract.py` knows v27's operations and parameters this
  plugin does not call. Nothing sends `report_latency`.
- **The SDK's shared connectivity pages do not apply**: `trading_pages`
  would declare Trading at `/admin/connections`, where SnapTrade's
  Connections is, and enables trading per account by a note in storage,
  where SnapTrade enables it per connection through its Connection Portal;
  `raw_records_page` is the plain Raw records, where SnapTrade's is the
  custody role's page with per-account reads, kept reads, the archive and
  downloads. Neither was copied here, so nothing is deleted.
- **Agents trade demo only**: unchanged. Every connection reports `live`,
  so the agents' guard refuses an agent's new placement on SnapTrade.
- **The pins**: open-meridian 0.32.2 and plugin-python:0.32.2, core v27's
  runtime and harness (core a5da59a, merged as 6d346b3, chart 0.1.310, by
  tag and digest), and `meridian` 0.1.52 in `check.yaml`; the e2e
  workflows upload `.e2e/` logs with `include-hidden-files`.
- **Docs**: Pages says where each connection's actions are; Overview says
  it places DMA alone.

## 0.19.0

Contract v26, on open-meridian 0.31.0 (meridian-design plans/the-v26-build,
§8; spec/the-contract-gaps-v26-and-v27-close, G1).

- **Trading on the SDK's Trader** (ruling 213): `trading.py` keeps only
  SnapTrade's venue (`TradingVenue`: its statuses, its order records, its
  charges from the activities, the settlement date it states, its
  catalogue) under `meridian.roles.connectivity.Trader`, which now does the
  bookkeeping, the firm's controls, changes and venue-originated fills that
  0.18.0 kept by hand. A Trader subclass keeps what SnapTrade decides:
  trading enabled per connection; a good-till-date order sent with its
  expire date; and a fill of an order placed in the broker's own app
  resolved, as 0.18.0 resolved it, by every identifier SnapTrade states --
  its FIGI, then its symbol -- and priced in the currency SnapTrade states
  for the listing, since a bare symbol can name several share classes or
  listings across SnapTrade's brokerages. The storage files are read as they were; a charge 0.18.0
  noted is carried over to the SDK's notation, so none is reported twice.
  An order SnapTrade lists `REPLACED` is followed to the order carrying its
  client order ID, rather than reported as a status not mapped.
- **Every connection is live**: SnapTrade has no demo environment, so its
  venue catalogue reports the environment its session reached as `live` on
  every code, and Connections says every connection is **Live** in a line
  under the section, with each row's badge in an Environment column on a
  wide screen (`read_connections` answers `environment`). While the
  deployment lets agents place orders on demo connections only, an agent's
  new placement on SnapTrade is refused; a person's own, and every cancel,
  is not.
- **The pins**: open-meridian 0.31.0 and plugin-python:0.31.0, core v26's
  runtime and harness (core 17a13a2, chart 0.1.308, by tag and digest), and
  `meridian` 0.1.49 in `check.yaml`.
- **The suites**: the connectivity suite's count with contract v26's
  `hears-and-reads-an-event-group`, answered by the SDK; `tests/test_contract.py`
  knows v26's operations and deliveries this plugin does not call.
- **Docs**: Overview, Pages and Setup say every connection is live and what
  that means for agents.

## 0.18.0

Contract v25, on open-meridian 0.29.0 (meridian-design tasks/sdk-contract/
plugins-are-shared-and-documented-contract; plans/a-plugin-carries-its-own-
documentation).

- **The whole declaration** (`declaration.py`): its settings, its interface
  -- the pages with their levels, and the tools their typed routes make --
  on a port fixed at 8000, that it reads external accounts, what it does not
  carry, its storage, and its documentation. `__main__` connects with the
  declaration alone, so what registers is what the version was uploaded
  with. The raw record kinds moved to `kinds.py` and the names not carried
  to `not_carried.py`, so the declaration can name the pages.
- **Its documentation** (`docs/`): overview, setup, troubleshooting, terms
  and pages, uploaded with each version and named by digest in its
  declaration, linking the custody role's page for what every custody
  plugin shares. `docs/README.md` stays the repository's index, not
  uploaded.
- **The pins**: open-meridian 0.29.0 and plugin-python:0.29.0, core v25's
  runtime and harness, and `meridian` 0.1.46 in `check.yaml`, each as released
  (core abf028f, chart 0.1.306, by tag and digest).
- **The suites**: custody's v24 case, a holding named by the brokerage's own
  symbol alone, recorded unresolved; connectivity's give-up case not
  presented, since no trade is given up at SnapTrade; the two cases every
  suite holds answered by the SDK.
- **`make e2e`** takes the harness down with `--profile runner`, so the
  runner's volume goes too.

## 0.17.0

Contract v23, on open-meridian 0.27.0 (meridian-design tasks/sdk-contract/
venue-originated-fills-and-the-lakes-outcomes-contract; plans/an-order-
reaches-a-venue-through-the-book, "v23: venue-originated fills", rulings 1
to 19).

- **The pins**: moved as `meridian plugin migrate --to 0.27.0` moves them,
  rewriting nothing; `check.yaml` holds the plugin to `meridian` 0.1.43.
- **Fills of orders placed in the broker's own app** (README, "Trading"):
  an order SnapTrade lists on a mapped account that no placement names has
  each fill reported on no placement, naming the instrument its symbol
  resolves to, the side, the venue order ID and the client order ID where
  SnapTrade states one, for the `ems` to accept or answer DK. A venue order
  with no fill is not reported, nor what an order had filled when the
  plugin first read the account (its history). Once an accept's placement
  names the order, its later fills and busts go on that placement; after a
  DK they arrive unmatched again. Kept in storage (`trading/originated.json`)
  so a restart reports nothing twice. The plugin hears the executions it
  reported (ExecutionRecorded).
- **The catalogue says SnapTrade takes no execution acknowledgement**
  (`EXECUTION_ACKNOWLEDGEMENT_NONE`): nothing is sent to SnapTrade on an
  accept or a DK.
- **A commission is a charge** (`FEE_KIND_COMMISSION`): the `fee` SnapTrade
  states on the `BUY` or `SELL` activity naming an order, on its placement,
  or on no placement for an order placed in the broker's app, then again on
  the placement an accept created. Each charge is reported once, noted in
  storage (`trading/charged.json`).
- **The account's currency** (`account_currency`, the SDK's common setting):
  where SnapTrade states no currency for a position or its listing, the
  currency a person set for the account comes before the rule, supplied by
  them; without one, the account's only cash currency or USD, derived, as
  before. Either is offered on the resolve with its provenance
  (`ResolveIdentifierRequest.provenance`), narrowing nothing; custody's
  `resolve-states-what-the-source-states` is now
  `resolve-gives-the-currency-with-its-provenance`.
- **The synthetic order placed in the broker's app**, `SYN-OUT-0001`, now
  fills 1 then 2 after the plugin first reads the account, each fill with a
  commission of 1.00; `make e2e` answers the first DK and accepts the second
  to a new order through the stand-in desk.
- **The suites**: connectivity, 23 of its 31 cases passing, v23's three
  venue-originated cases among them, and eight not presented with why (v22's
  seven, and the value-date roll: SnapTrade's trades settle); custody, every
  case but `reads-a-calendar`, v23's `about` cases presented since SnapTrade
  states each. No suite test skipped or expected to fail.

## 0.16.0 (not released)

Contract v22, on open-meridian 0.26.0 (meridian-design plans/an-order-
reaches-a-venue-through-the-book, build row 16).

- **The pins**: moved as `meridian plugin migrate --to 0.26.0` moves them,
  rewriting nothing.
- **`connectivity`, beside `custody`** (README, "Trading"): the placements
  naming its instance placed at SnapTrade's trading API in the account the
  venue account code names; acknowledgements, rejections (`unsupported`,
  `venue rejected` as reported), outcome unknown resolved by asking, never
  by sending again, cancels, expiries; fills inferred from SnapTrade's
  filled quantity, each with a derived ID and the date read's settlement
  date; busts from a later read; a cancel's fee from the activities; pause
  and stop honoured, a stop's cancels reported naming the stop; the venue's
  catalogue per venue account code. A cancel or a replace the `ems` asks for
  told by the placement's change request (`Placement.change_requests`), a
  replace sent at SnapTrade's replace endpoint and its new order followed, a
  change refused reported as the placement rejected while it works. The
  codes mapped on it read from the account scope's venue account map. No
  order recorded and no placement created: an order placed at the brokerage
  outside the platform is kept as received and not reported (ruled
  2026-10-13; venue-originated fills are planned for v23). Every request and
  answer kept as a raw record of its own kind, `trading`. Synthetic mode
  trades with a simulated brokerage (`synthetic_orders.py`), never
  SnapTrade.
- **A third kind of raw record, `trading`** (approved 2026-10-13): each
  exchange with SnapTrade's trading API, kept 2555 days (seven years) by
  default and then archived, kept or deleted as an admin sets
  (`trading_window_days`, `trading_past_window`, the SDK's settings). A
  default, not a claim about any rule.
- **Enable trading** on Connections, per connection, by an admin of the
  `connectivity` role (`enable_trading`, checked by `check_enable_trading`):
  SnapTrade's Connection Portal reconnects the brokerage with trading
  access. A connection is read-only until then.
- **Every page names the roles it serves** (two roles, contract v15):
  Connections and its way in for `custody` and `connectivity`, every other
  for `custody`, whose pages read the custody role's accounts.
- **The suites**: the connectivity suite, 20 of its 27 cases passing and
  seven not presented with why (no calendar read, no locate nor a locate's
  symbol, no place of execution, nothing depending on whether releases wait
  for compliance); the custody
  suite's `asks-for-a-settlement-date` from the fill path, its
  `reads-a-calendar` not presented.
- **The declaration**: SnapTrade's order record's `trailing_stop` and
  `child_brokerage_order_ids`, received and not carried.
- **Account links' form is offered to agents as `link_account`** alone,
  the route no longer kept from them by hand (meridian 0.1.42 holds the tool
  replacing it to its check tool).
- **A refusal is named by its type and code** in the log and on the pages,
  never assumed: "refused" only where a component answered with one.

## 0.15.0 (not released)

Contract v21, on open-meridian 0.25.0 (meridian-design plans/a-service-
account-runs-a-plugins-autonomous-work).

- **The pins**: moved as `meridian plugin migrate --to 0.25.0` moves them,
  rewriting nothing. Contract v21 lets a plugin's autonomous work act as its
  role's service account; this plugin changes nothing of the deployment's of
  its own accord, so nothing else changes.

## 0.14.0 (not released)

Contract v20, on open-meridian 0.24.0 (meridian-design plans/an-agents-
prepared-change-waits-for-the-person; spec/plugin-pages-follow-their-role,
accepted 2026-10-10).

- **The pins**: `meridian plugin migrate --to 0.24.0` moved them and had
  nothing to rewrite; the plugin holds `custody`, which kept its name.
- **The custody role's common pages** (`meridian.roles.custody`), each
  extending the SDK's role template, SnapTrade's nuance only in its blocks:
  - **Connections**: "No credentials yet" until one is set; the role's
    "Connections" section, a row each -- the connection, its state, the
    accounts it reaches, when it was read -- with what to do and Refresh and
    Reconnect on its row as SnapTrade allows (on a phone a row keeps
    Reconnect); **Connect** (was "+ Add") and Refresh in the section's head
    through the template's `action`, each drawn as Prepare under its act's
    second pair of eyes; the SnapTrade user below. Refresh posts no `back`:
    under Manage a read answers with Connections.
    `read_connections` answers the role's fields first (`credentials`, each
    set or not, never a value; each connection's `connection`, `state`,
    `accounts_reached`, `last_read`), SnapTrade's beside them (`accounts`
    is `accounts_reached` now).
  - **Account links**: "No accounts granted yet" until a deployment admin
    grants this instance accounts (contract v20, W6.30); the kit's account
    map through the template's approved options -- grouped by connection,
    several links at once, no new account for a plugin admin who is not a
    deployment admin -- the plain forms inside it. `read_account_links`
    answers the role's `external_accounts`, `accounts` (those granted) and
    `links`, then the plan-code links and the activity window.
  - **Statements**: the role's row per account the person may read (account,
    custodian, as of, state), one account's statement at a time below it,
    its rows searched (`as_of_date` is `as_of` in `read_statements`).
  - **History**: the role's columns -- Account, Trade date, Settlement date,
    Kind, Instrument, Units, Amount, Description -- SnapTrade's price and
    currency beside them, "No activity yet" where the range holds none; the
    first account the person may read where none is chosen.
    `read_account_activities` answers each activity in the role's
    `ActivityRead` words (`external_activity_id`, `kind` as it converts,
    `instrument_id` as SnapTrade names it, numbers as written).
  - **Raw records** (was "Raw responses"): the role's section -- each kind,
    its window as set, what becomes of it past the window, how many in
    storage -- then SnapTrade's reads, kept reads and archive. Its path and
    tools are unchanged.
- **The role's settings, in its order**: Client ID, Consumer key; Read
  every, Stale after (the SDK's helpers); Key type with the user it reads
  gated on it, Plan-code links, Cash links; and last the developer's.
  `synthetic` is `sample_data` (`meridian.sample_data()`, ruled 2026-10-02):
  a value saved under the old name is not delivered, so a development
  deployment turns Sample data on again.
- **The role's figures** through `custody.figures(...)`: the same three.
- **Linking only to accounts granted**: a link the sidecar refuses as
  REFUSAL_REASON_ACCOUNT_NOT_LINKABLE is said plainly, and refused to a
  tool by `account_id`, reason `account_not_linkable`.
- **Every act preparable** (approved names): `open_connection_portal`,
  `refresh_connection`, `reconnect_connection`, `read_snaptrade_now` and
  `link_account` each name their check -- `check_open_connection_portal`,
  `check_refresh_connection`, `check_reconnect_connection`,
  `check_snaptrade_now`, `check_link_account` -- a read taking the act's
  record and asking nothing of SnapTrade or the sidecar that changes
  anything; `link_account` names the account it touches. Where an act's
  second pair of eyes is on for the person, its button prepares it instead
  ("Prepare Refresh", "Prepare Connect"), and the plain link forms offer
  Prepare beside Link. A page opened from a prepared change says what it
  asks, "prepared by" its preparer, with their reason.
- Kit 0.12.0.

## 0.13.1 (not released)

Contract v18, on open-meridian 0.22.0 (meridian-design
plans/the-lake-prices-the-book, row 5a). Only the pins move, so a patch
release: nothing the plugin calls changed shape, and it does nothing it did
not.

- **0.13.0's image ran unchanged on contract v18 first**: its `make e2e` on
  core b1b8420's runtime and harness, every step as before.
- **The pins**: `open-meridian==0.22.0`, the `Dockerfile`'s base
  `plugin-python:0.22.0`, and `make e2e` on core b1b8420's runtime and
  harness, by tag and digest.
- **A Money names its cash instrument** from contract v18. The plugin still
  names each currency by its ISO 4217 code, `Money(amount, "USD")`, the
  Cash links' currency column's code alike, and core resolves it, dated, to
  the currency's cash instrument; a code that is no ISO 4217 code's shape
  (a token's) was never sent, and still is not. **A date is a date** from
  v18: every date the plugin sends was a date's ISO text already, which
  0.22.0 still takes.
- `tests/test_contract.py` knows what v18 adds that custody neither calls
  nor hears: the lake's operations (`record_prices`, `record_bars`,
  `decline_want`, `list_prices`, `list_bars`, `list_datasets`),
  `resolve_venue`, `report_missing_venue`, and `receive(prices_recorded=,
  bars_recorded=, observations_wanted=, want_withdrawn=, subjects=)`.

0.22.0 is not on PyPI yet: `make` builds it from the sibling meridian-python
checkout, and CI, which has none, cannot install it until it is released.

## 0.13.0 (not released)

Contract v16, on open-meridian 0.21.0 (meridian-design
plans/an-edge-plugins-older-records-move-to-the-archive, row 6;
tasks/sdk-contract/an-edge-plugins-older-records-move-to-the-archive).

- **Its two kinds of raw record are declared** (`declaration.py`): Reported
  activity (`activity`, a window of 2555 days, seven years, the custody
  guidance of 2026-10-05) and Raw responses (`responses`, 30 days), both
  archivable. The storage asked for stays 36,500 days, the longest a window
  may be.
- **The windows are the SDK's settings**: `activity_window_days`,
  `activity_past_window`, `responses_window_days` and
  `responses_past_window`, declared by the SDK from the kinds, the same for
  every edge plugin, set in the dashboard's Settings form. Past the window a
  record is archived (the default where the instance has an archive), kept
  (otherwise) or deleted.
- **Upgrading from 0.12.0: set the two windows once.** 0.12.0's settings are
  dropped, and their values are not carried over (the product owner,
  2026-10-07). At upgrade, the deployment admin sets each window once in the
  plugin's Settings form, to the value the old setting held:

  | 0.12.0's setting | 0.13.0's setting | default |
  |---|---|---|
  | `raw_retention_days` (Keep raw responses for) | `responses_window_days` (Raw responses: window) | 30 days |
  | `activity_retention_days` (Keep activity records for) | `activity_window_days` (Reported activity: window) | 2555 days |

  Where the old setting was never saved, or held the default, there is
  nothing to set. Until a window is set it is its default, and a record
  past it is kept unless `<kind>_past_window` says archived or deleted. The
  deployment's hold applies to the windows; what 0.12.0 deleted past its
  retention is not brought back.
- **Past the window, in units, through the SDK's helpers** (`archive.py`):
  an account's day of reads and its month of activity records, moved whole
  once the day or month has ended. Archived with `archive_unit` (written to
  the archive, checked, recorded naming the window, then removed); kept; or
  deleted with `delete_unit`, recorded first, so a deletion inside the
  deployment's hold is refused (REFUSAL_REASON_WITHIN_HOLD) and the unit
  kept, said once, tried again each pass and recorded never. A reported
  activity is never deleted within the history SnapTrade reported. With no
  archive allowed, a unit past its window is kept. A pass runs when the
  settings arrive and after each read; a restart's records nothing twice.
  Each unit is noted first in its account's ledger (`moved.json`), so it is
  listed and a record in it is found again; the SDK's index says where it
  stands.
- **What storage holds goes on the heartbeat**: `plugin.stored`, one span
  per kind, count and first and last received, after each pass; the SDK adds
  the bytes each kind uses of the archive (`StoredSpan.bytes`), which the
  plugin's Summary shows against the archive's bound. Where the archive is
  bounded (`MERIDIAN_ARCHIVE_MOST_BYTES`) and a unit would take it past, the
  SDK refuses it: the unit is kept, said once, and tried again each pass.
- **The layout is in units**: a read in its day, an activity's record in the
  month it was received. 0.12.0's flat records are moved into their units on
  the first pass, keeping their times; until then they read as before.
- **The Raw responses tab fits one screen** (kit 0.10.0), one account at a
  time: Read, each call one line with SnapTrade's JSON a click away; Kept
  reads, paged by `om-pager`; and Archive (`/raw/archive`), every unit moved
  for the accounts the person may read, archived, restored or deleted, with
  Restore under Open through the SDK's `POST /archive/restore`; on it the
  plugin's tab row marks Raw responses, the tab it is a view of. Restored
  reads are read on the tab as any kept read. A row's reference to a record
  that moved resolves to where it stands, with Restore beside it.
- **Offered to agents**: `read_archived_units` (`GET /raw/archive`), and the
  SDK's `restore_unit` on the plugin's host.
- The Account links tab says the activity window and what is done past it.
- `make e2e` on core 3af1b0f's runtime and harness (contract v16, its revision of 2026-10-07), the plugin
  given the harness's archive: a read 40 days old archived past its window,
  recorded, its reference resolving to archived, restorable, restored on the
  tab through the SDK's route for the person and read back; a read 5 days
  old, past a window of a day with `deleted` chosen, kept inside a hold of
  30 days with nothing recorded; and a new container's pass recording
  nothing twice. `make preview` writes the tab's kept reads and archive too.

## 0.12.0 (not released)

Contract v15, on open-meridian 0.20.0 (meridian-design
plans/access-is-granted-per-role, build 7; tasks/sdk-contract/an-activity-is-
re-resolved-when-its-instrument-resolves).

- **Access per role needs nothing of this plugin.** It holds one role,
  `custody`, so its pages and settings name none and serve as before. 0.11.1's
  image ran its `make e2e` unchanged on core 7b0b2a8's v15 runtime and
  harness before the pin moved.
- **A plan code linked later re-resolves the earlier activity.** When a
  settings delivery adds or changes a Plan-code links row, the account is
  backfilled again, and each activity the street already holds under the
  linked code is re-resolved through the link (`ReResolveActivity`, W2.15):
  the instrument record chosen, who added or last changed the row, and when
  the row says. The street keeps the activity as first recorded and the
  re-resolution beside it; the start's backfill re-resolves whatever a
  current link now resolves, and the street answers already recorded what it
  holds so, so a restart or a delivery again re-resolves nothing twice. A
  row that says no time re-resolves nothing, saying why; a row removed sends
  nothing.
- The custody suite's v15 case, `activity-re-resolved-when-a-plan-code-is-
  linked-later`, runs from SnapTrade's own words.
- `make e2e` on core 7b0b2a8's runtime and harness (contract v15): IBKR is
  linked first, its reinvestment under OQKR reported as the code, then OQKR
  is linked in the settings, and the street holds the reinvestment as first
  recorded with its re-resolution beside it (`e2e/expected.re-resolved`); a
  new container re-resolves nothing twice.

## 0.11.1 (not released)

The two table settings read alike (the product owner, 2026-10-05). Nothing
the plugin sends or reads changed.

- **`counted_as_cash` is labelled Cash links**, beside Plan-code links: the
  dashboard draws each table setting as its own tab under Manage, titled by
  its label. The setting's key is unchanged.
- **One column order for both:** Account | Plan code / Symbol | Instrument /
  Currency. Cash links' account moves first and stays optional, a blank one
  applying the row to every account holding the symbol, as its description
  now says. A row is kept by column name, so one saved in 0.11.0's order
  (symbol, currency, account) reads the same.
- **No page sets a setting:** the README no longer says
  `activity_retention_days` is also set on the Account links tab, or that a
  view refuses a value. Every setting is set in the dashboard's Settings
  form; the Account links tab only shows what they hold, now counting the
  cash links too.
- `make e2e` on core 7d4ce0f's runtime and harness.
- **Its documentation, in `docs/`**: an overview, setup (the key, connecting
  a brokerage, linking accounts, Plan-code links and Cash links), each page,
  troubleshooting by state, and terms, in plain Markdown ahead of the format
  a plugin's documentation will ship in. No reference is hand-written: that
  part will be generated from the declaration.

## 0.11.0 (not released)

Contract v14, on open-meridian 0.19.0 (meridian-design
plans/the-custodians-activity-explains-a-break, build 5).

- **The custodian's activity reaches the street.** Each activity on a linked
  account is reported as SnapTrade states it (W2.10): on the account's first
  read in a process, a backfill of every activity SnapTrade holds back to its
  `history_from`; on each read after, the activities that read fetched. The
  street keeps each once by SnapTrade's activity ID, so a restart's backfill
  records nothing twice.
- **SnapTrade's types mapped to the closed list:** `BUY` purchase, `SELL`
  sale, `REI` reinvestment, `DIVIDEND`, `INTEREST`, `FEE`, `TAX`, `SPLIT`,
  `STOCK_DIVIDEND` corporate action, the transfers in and out,
  `CONTRIBUTION`, `WITHDRAWAL`, `JOURNALED` journal; anything else not known,
  with the type as reported. Units signed by what they did to the account; a
  value SnapTrade writes as 0 where none applies sent unset.
- **The instrument:** a held security resolved by its holding's
  identifiers; a plan's own fund code linked by a person resolved as the
  linked symbol, with that person as its provenance; any other code as
  reported.
- **The plan-code link** (the product owner's option A, 2026-10-05): the
  table setting `plan_code_links` -- per row an external account, a plan's
  own code and a deployment instrument record -- entered by an admin of the
  plugin in the dashboard's Settings form as an editable typed table. An
  activity under a linked code is that record, naming who changed the row
  and when. The plugin only reads its settings; no page sets one.
- **`activity_retention_days`** (the product owner, 2026-10-05): how long
  each reported activity's raw record is kept, seven years by default, an
  admin of the plugin may set it longer (up to 36,500 days, which the
  storage declaration now asks for) on the dashboard's Settings form; never
  shorter than the history SnapTrade reported, which a shorter value is kept
  to, as the Account links tab says.
- **A custodian's deposit is cash** (the product owner, 2026-10-05;
  meridian-design tasks/sdk-contract/snaptrade-counts-a-core-deposit-as-cash).
  A position SnapTrade marks `cash_equivalent` that is no fund (Fidelity's
  FDIC-insured deposit as an IRA's core position, say) is sent as the cash
  of its currency, not a holding: SnapTrade counts what it marks so in that
  cash already, so the cash row stands for it and nothing is added twice,
  derived by a named rule with the deposit's symbol and description as
  reported. A money market fund stays a fund, its cash net of it as before.
- **`counted_as_cash`**, a table setting (symbol, currency, optionally the
  account), set by an admin of the plugin on the dashboard's Settings form:
  a position SnapTrade does not mark, listed there, is added to the cash of
  the row's currency, derived by a named rule and supplied by the person who
  changed the row, when. SnapTrade's own flag decides first; a fund stays a
  fund; a row that cannot count (no currency code, a currency SnapTrade
  contradicts, no price) is said, and the position sent as it is. Never
  decided from what a symbol looks like.
- Synthetic Fidelity: a Traditional IRA with a deposit SnapTrade marks a
  cash equivalent (SYNDP) and one it does not (SYNFD, 2.99 at 1); `make e2e`
  links it, then lists SYNFD, and its next statement is USD cash 12.99 and
  no SYNFD.
- **The sync status carries `history_from`**, the first date SnapTrade's
  history of the account reaches.
- **Raw-record retention covers what was reported:** each reported
  activity's raw record is its own, `activities/<external account>/<activity
  ID>`, kept for `activity_retention_days` from receipt (seven years by
  default), and the Raw responses tab opens it by that reference. Each read's
  records keep `raw_retention_days`.
- An activity's `fee` and `fx_rate` are declared as received and not carried,
  and counted as reads see them.
- Synthetic Alpaca has a history: one activity of each kind and an option's
  expiry, on fixed dates from 2025-06-02; synthetic IBKR a reinvestment
  under a plan's own code (OQKR).
- `make e2e` on core 1d16c52's runtime and harness: the street's activity
  is `e2e/expected.activity`, a new container's backfill records nothing
  twice, and after OQKR is linked to SYNXX's record in the Settings form,
  IBKR's first read reports its reinvestment under OQKR as that record.
