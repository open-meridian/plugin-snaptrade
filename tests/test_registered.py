"""The plugin's storage read once registered (open-meridian 0.37.0): on an
instance run as shards, its shard's own folder, known only then, and read
afresh when it is registered again. SIMULATED ONLY."""

from __future__ import annotations

from pathlib import Path

import pytest
from meridian import edge

from snaptrade.__main__ import Process, Trading


def test_the_plugin_reads_its_storage_once_registered_and_afresh_when_registered_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(edge.STORAGE_DIR, str(tmp_path))
    monkeypatch.setenv("MERIDIAN_PLUGIN_SHARDS", "2")
    monkeypatch.setattr(edge, "_serving", None)
    # Made before Register, as `main` makes it: no storage read, which on
    # an instance of shards raises.
    process = Process()
    process.roles = [process.custody, Trading(process)]

    edge._registered_as(1, 2)
    process.open_storage()
    raw = process.syncer.raw
    assert raw is not None
    shard = tmp_path / "shard-1"
    assert (raw.root, raw.storage) == (shard / "raw-responses", shard)

    # Deposed, then admitted again as the other shard.
    process.stopped()
    edge._registered_as(0, 2)
    process.open_storage()
    shard = tmp_path / "shard-0"
    assert (raw.root, raw.storage) == (shard / "raw-responses", shard)
