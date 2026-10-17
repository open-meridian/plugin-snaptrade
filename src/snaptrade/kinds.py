"""The storage this plugin asks for at the edge, for SnapTrade's raw
responses (decisions/028), with the three kinds of raw record it keeps there
(contract v16; meridian-design spec/an-edge-plugins-older-records-move-to-the-
archive): a reported activity's record, seven years in storage by default
(the custody guidance of 2026-10-05); each read's raw responses, 30 days by
default; and from contract v22 each trading exchange (`trading`, approved
2026-10-13), seven years by default; all archivable, since a unit of any is
files written once.

The SDK declares each kind's two settings from these, its window and what is
done past it (settings.py), and the plugin moves its records past their
window itself (archive.py). The retention stays the longest a window may be,
the SDK's bound, so the deployment never keeps the storage for less than an
admin may choose. declaration.py carries it in the version's declaration;
the pages read the kinds from here, so they need not import the declaration,
which imports them.
"""

from __future__ import annotations

from meridian.declaration import RecordKind, Storage

from .raw import MOST_ACTIVITY_RETENTION_DAYS
from .settings import (
    ACTIVITY,
    DEFAULT_ACTIVITY_RETENTION_DAYS,
    DEFAULT_RAW_RETENTION_DAYS,
    DEFAULT_TRADING_RETENTION_DAYS,
    RESPONSES,
    TRADING,
)

#: The kinds of raw record this version keeps, each with its default window
#: and whether a unit of it can be archived (W4.1, contract v16).
ACTIVITY_KIND = RecordKind(
    ACTIVITY, "Reported activity", window_days=DEFAULT_ACTIVITY_RETENTION_DAYS, archivable=True
)
RESPONSES_KIND = RecordKind(
    RESPONSES, "Raw responses", window_days=DEFAULT_RAW_RETENTION_DAYS, archivable=True
)
TRADING_KIND = RecordKind(
    TRADING, "Trading", window_days=DEFAULT_TRADING_RETENTION_DAYS, archivable=True
)
KINDS = (ACTIVITY_KIND, RESPONSES_KIND, TRADING_KIND)

STORAGE = Storage(retention_days=MOST_ACTIVITY_RETENTION_DAYS, kinds=KINDS)
