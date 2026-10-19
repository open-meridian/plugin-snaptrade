# Overview

The SnapTrade plugin brings in what your brokerage accounts hold, through
[SnapTrade](https://snaptrade.com), a service that connects to many
brokerages with one sign-in each. You connect a brokerage once; the plugin
then reads its accounts every few minutes, and can place your desk's orders
in them.

It is a custody plugin: its first-use path, its common pages and its Summary
figures are every custody plugin's, described once on
[Using a custody plugin](https://open-meridian.dev/how-to/using-a-custody-plugin/).
These sections cover what is SnapTrade's own.

## Its two roles

**Custody**: it brings in the custodian's view of an account. It does not
keep your own book or value your positions. Your book is kept by an
operations plugin, which compares the two and shows each difference.

**Connectivity**: it places the orders your execution plugin (an `ems`)
sends it at SnapTrade, in the brokerage account the order names, and reports
what SnapTrade did with each: accepted, filled, cancelled, rejected and why.
It trades only on a connection where an admin enabled trading
([Setup](setup.md)); every connection starts read-only. It sends nothing new
while trading is paused or stopped, and a stop cancels what it is working at
SnapTrade at once. It also reports the fills of orders placed in the
broker's own app, on a mapped account, for your execution plugin to accept
to an order or answer DK; SnapTrade is told nothing of either answer, since
it takes none.

**Every connection is live.** SnapTrade has no demo environment: each
connection is the brokerage's own account, and the plugin reports it live.
While the deployment lets agents place orders on demo connections only, an
agent's new order through SnapTrade is refused; a person's own order is not.

## What it reads

From SnapTrade, for the connections under your key:

- each connection's accounts, and how fresh SnapTrade's data about them is;
- each account's positions and its cash in each currency;
- each account's activity: buys, sells, dividends, reinvestments, splits,
  fees, taxes, transfers, contributions and withdrawals.

It reads on start, whenever its settings change, and on a timer you set. The
first time it reads an account you linked, it reads the whole history
SnapTrade holds for it.

## What it records

Only for an account you have linked to one of your deployment's accounts:

- **a statement** each read: every position and the cash in each currency,
  with the average cost and the tax lots where SnapTrade gives them;
- **the account's sync state**: current, stale, needing sign-in, and the
  others in [Troubleshooting](troubleshooting.md);
- **each activity** once, as SnapTrade states it.

An account nobody has linked is listed for linking, and nothing of it is
recorded. A holding SnapTrade names only by the brokerage's own symbol, which
the platform does not know, is recorded unresolved, with what SnapTrade
said, never guessed.

## What it never makes up

- **No market value.** SnapTrade reports none, and the plugin does not work
  one out from a price.
- **No lot SnapTrade did not state.** Lots it can propose from the history
  are only proposals, for a person to accept (the History tab).
- **No asset counted twice.** A money market fund SnapTrade also counts in
  cash is kept as a fund, and the cash is sent net of it.
- **Nothing decided by what a symbol looks like.** Cash is cash because
  SnapTrade says so, or because an admin listed it on Cash links.

## What it keeps

What SnapTrade answered on each read, so you can see it on the Raw records
tab, in storage your deployment gives this plugin alone. Credentials are
removed before anything is written. Each read's answers stay in storage 30
days by default, each activity's own record and each trading exchange seven
years; after that they move to your deployment's archive, where you can
restore them, or stay, or are deleted, as the plugin's admin chooses. Every
move is recorded. None of it leaves your deployment.

## Who uses it

- **An admin of the plugin** sets it up under **Manage**: the key, the
  connections, the account links. Manage shows no account's holdings.
- **People granted read or write on accounts** see those accounts'
  statements, history and raw records under **View** or **Open**.
