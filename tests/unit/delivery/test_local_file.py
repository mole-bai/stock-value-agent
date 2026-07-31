from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from stock_agent.delivery import LocalFileDelivery


class LocalFileDeliveryTests(unittest.TestCase):
    def test_delivers_utf8_report_and_returns_receipt(self) -> None:
        content = "# 日报\n\n三情景估值。\n"
        with tempfile.TemporaryDirectory() as directory:
            delivery = LocalFileDelivery(directory)
            receipt = delivery.deliver(content, filename="daily-2026-08-01.md")

            self.assertEqual(receipt.path.read_text(encoding="utf-8"), content)
            self.assertEqual(receipt.bytes_written, len(content.encode("utf-8")))
            self.assertEqual(
                receipt.sha256, hashlib.sha256(content.encode("utf-8")).hexdigest()
            )
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_atomically_replaces_an_existing_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "daily.md"
            target.write_text("旧报告", encoding="utf-8")
            LocalFileDelivery(directory).deliver("新报告", filename="daily.md")
            self.assertEqual(target.read_text(encoding="utf-8"), "新报告")
            self.assertEqual(list(Path(directory).glob(".*.tmp")), [])

    def test_failed_replace_preserves_old_report_and_cleans_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "daily.md"
            target.write_text("旧报告", encoding="utf-8")
            delivery = LocalFileDelivery(directory)
            with patch("stock_agent.delivery.local_file.os.replace", side_effect=OSError("boom")):
                with self.assertRaisesRegex(OSError, "boom"):
                    delivery.deliver("未投递的新报告", filename="daily.md")
            self.assertEqual(target.read_text(encoding="utf-8"), "旧报告")
            self.assertEqual(list(Path(directory).glob(".*.tmp")), [])

    def test_rejects_paths_outside_delivery_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            delivery = LocalFileDelivery(directory)
            for filename in ("../outside.md", "/tmp/outside.md", "nested/report.md"):
                with self.subTest(filename=filename):
                    with self.assertRaises(ValueError):
                        delivery.deliver("内容", filename=filename)


if __name__ == "__main__":
    unittest.main()
