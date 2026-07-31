from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from stock_agent.history import (
    InvalidSnapshotId,
    SnapshotArchive,
    SnapshotArchiveError,
    SnapshotNotFound,
)


class SnapshotArchiveTests(unittest.TestCase):
    def test_saves_decimal_safe_history_and_latest_with_stable_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = SnapshotArchive(Path(directory) / "archive")
            first = {
                "run_at": "2026-07-30T09:00:00+00:00",
                "value": Decimal("123.450"),
                "observed_at": datetime(2026, 7, 30, 9, tzinfo=timezone.utc),
            }
            second = {"run_at": "2026-07-31T09:00:00+00:00", "value": "125"}
            archive.save(first, snapshot_id="20260730")
            archive.save(second, snapshot_id="20260731")

            self.assertEqual(archive.load("20260730")["value"], "123.450")
            self.assertEqual(
                archive.load("20260730")["observed_at"],
                "2026-07-30T09:00:00+00:00",
            )
            self.assertEqual(archive.load_latest(), second)
            self.assertEqual(
                archive.list_snapshot_ids(), ("20260730", "20260731")
            )
            self.assertEqual(
                [item["value"] for item in archive.load_history(newest_first=True)],
                ["125", "123.450"],
            )
            self.assertEqual(len(archive.load_history(limit=1)), 1)
            self.assertFalse(list((archive.root).rglob("*.tmp")))

    def test_default_id_is_stable_and_conflicting_history_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = SnapshotArchive(directory)
            snapshot = {"run_at": "2026-07-31T17:00:00+08:00", "stocks": []}
            first_id = archive.save(snapshot)
            second_id = archive.save(snapshot)
            self.assertEqual(first_id, second_id)
            self.assertTrue(first_id.startswith("20260731T090000.000000Z-"))
            with self.assertRaisesRegex(SnapshotArchiveError, "different content"):
                archive.save({"stocks": [1]}, snapshot_id=first_id)

    def test_rejects_traversal_absolute_and_reserved_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = SnapshotArchive(directory)
            for value in ("../outside", "/absolute", "a/b", r"a\b", "..", "latest"):
                with self.subTest(value=value), self.assertRaises(InvalidSnapshotId):
                    archive.load(value)
            with self.assertRaises(SnapshotNotFound):
                archive.load("missing")

    def test_failed_latest_replace_preserves_previous_snapshot_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = SnapshotArchive(directory)
            old = {"run_at": "2026-07-30T09:00:00+00:00", "value": "old"}
            new = {"run_at": "2026-07-31T09:00:00+00:00", "value": "new"}
            archive.save(old, snapshot_id="old")

            from stock_agent.history import archive as archive_module

            real_replace = archive_module.os.replace

            def fail_latest(source: str, destination: str | Path) -> None:
                if Path(destination).name == "latest.json":
                    raise OSError("simulated replace failure")
                real_replace(source, destination)

            with patch.object(archive_module.os, "replace", side_effect=fail_latest):
                with self.assertRaises(SnapshotArchiveError):
                    archive.save(new, snapshot_id="new")

            self.assertEqual(archive.load_latest(), old)
            self.assertFalse(list(Path(directory).rglob("*.tmp")))

    def test_rejects_symlinked_history_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory) / "archive"
            root.mkdir()
            try:
                (root / "history").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("symbolic links are unavailable")
            with self.assertRaises(SnapshotArchiveError):
                SnapshotArchive(root).load_latest()


if __name__ == "__main__":
    unittest.main()
