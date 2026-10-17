# Setup

Each step is taken by an **admin of this plugin** (a deployment admin is
one, through the All plugins (admin) group, unless that was withdrawn). The
plugin must already be launched, its roles approved by a deployment admin.
The steps follow every custody plugin's first-use path; what is SnapTrade's
own is below.

## Before you start: which key

SnapTrade issues one of two kinds of key:

- **Personal key.** Belongs to one SnapTrade user, the account holder, for
  their own accounts.
- **Commercial key.** Your firm registers SnapTrade users of its own under
  it, each with a secret. One instance of this plugin reads one of those
  users' connections.

Which one you may use, and for whose accounts, is SnapTrade's to say: see
[Terms](terms.md).

## 1. Enter the key

**Manage → Settings.** The key is entered here and nowhere else.

1. **Key type**: Personal or Commercial. It decides which fields follow.
2. **Client ID**, from the SnapTrade dashboard's API keys page.
3. **Consumer key**, shown once, when SnapTrade issues the key.
4. With a commercial key, the **User ID** of the SnapTrade user to read, and
   its **User secret**.
5. **Save.** The plugin picks it up without a restart.

Secret fields are write-only: they show only "set" or "not set", and nobody
reads them back. Until every required field is given, the plugin calls
nothing and its status dot names what is missing.

The other settings have sensible defaults: how often to read (**Read
every**, five minutes) and when a sync counts as **Stale after** (24 hours).

## How long records stay

Three kinds of record are kept: **Raw responses**, what SnapTrade answered
each read; **Reported activity**, each activity's own record; and
**Trading**, each order sent to SnapTrade, each cancel and replace, and
SnapTrade's answers about orders.

Each has a **window** on Settings, how long it stays in the plugin's storage
(30 days, seven years and seven years by default; the defaults are not a
claim that any rule your firm follows is met), and a choice of what happens
past it:

- **Archived**: moved to your deployment's archive, where it can be
  restored; the default where your deployment's admin has allowed this
  plugin an archive;
- **Kept**: left where it is; the default otherwise;
- **Deleted**.

Nothing is deleted unless an admin chooses it, never inside a hold your
deployment's admin set, and an activity record never within the history
SnapTrade reported. A window cannot be set below the hold.

## 2. Connect a brokerage

**Manage → Connections → Connect.** This opens SnapTrade's Connection Portal,
outside the dashboard. Choose the brokerage and sign in to it there:
SnapTrade holds that sign-in, and your deployment never sees it.

Back on Connections, **Refresh** reads SnapTrade now. Nothing is recorded
for a new connection until SnapTrade's first sync of it is done; until then
it shows as stale.

## 3. Link accounts

**Manage → Account links.** Every account the connections reach, opening on
the unlinked ones. For each:

- **Link** it to one of your deployment's accounts, found by typing; or
- a deployment admin may **create a new account** from it, named after it,
  its custodian and type filled in, all editable.

Where an account's name or number matches exactly one of yours, the row
suggests it: **Link** takes it, and **Link N suggested…** takes several at
once. Each deployment account takes one external account.

Nothing is recorded for an account until it is linked. Then a deployment
admin grants people read or write on it, so they see it under View or Open.

## 4. Plan-code links, only if needed

A tab under Manage, an editable table of up to 200 rows; each row records
who added or last changed it, and when.

**Use it when** a retirement plan, such as a 401(k), names a fund in its
activity by the plan's own code rather than a public symbol, so that
activity arrives with no instrument.

A row is the **account**, the **plan code** as SnapTrade names it, and the
**instrument** record it is, found by search. After that, that code's
activity is that instrument, naming who linked it.

Activity that arrived before you added the row is re-resolved too: on the
next read, each earlier activity under that code on that account is
recorded as that instrument, naming who linked it and when, beside the
activity as it first arrived, which is kept. Changing the row re-resolves
them again.

## Account currency, only if needed

**Use it when** SnapTrade states no currency for an account's positions. A
row is the **account** (leave it blank for every linked account) and its
**currency** as an ISO code, such as USD; a row naming the account comes
first. From the next read, such a position is in that currency, naming who
set it. Without a row, it is taken to be in the account's only cash
currency, or US dollars, and the statement says it was derived.

## Cash links, only if needed

**Use it when** a custodian holds cash as a position SnapTrade does not mark
as cash, such as an FDIC-insured bank deposit held as an IRA's core
position. It then arrives as a holding.

A row is the **account** (leave it blank for every account holding the
symbol), the **symbol** as SnapTrade names it, and the **currency** as an
ISO code, such as USD. From the next read, the position is counted as cash
in that currency, naming who listed it.

- Where SnapTrade marks the position as cash itself, no row is needed.
- A money market fund is never listed here: a fund stays a fund.
- Adding a row changes the next statement: the position goes and the cash
  rises. Your operations plugin shows that as a difference for a person to
  confirm.

## 5. Trading, only if you place orders through it

A connection is read-only until trading is enabled on it. To place orders
through SnapTrade, an admin of the plugin's **connectivity** role opens
Connections and presses **Enable trading** on the connection: SnapTrade's
Connection Portal opens to sign in to that brokerage again with trading
access. The connection's account links stay as they were. With sample data
on, it takes effect at once.

Then your execution plugin's admin maps each account the desk trades to the
code this plugin names it by: the external account shown on Account links
(for example `ALPACA:SYN-ALP-1001` in sample data). After that, an order
placed in the broker's own app on that account has its fills reported for
the execution plugin to accept or answer DK; what the account had filled
before is its history, which the statement holds, and is not reported.

## Agents and sample data

An agent you delegate to can connect a brokerage, link accounts and read
what you can, through the deployment's tools; the Reference lists them. The
key is entered only on the Settings form.

On a development deployment, **Sample data** serves invented data shaped
like SnapTrade's, so the plugin can be tried before any key exists. Every
figure it shows is invented, its pages say so, and it trades with a
simulated brokerage, never SnapTrade.
