"""Auditable local notification delivery; no external network side effects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from stock_agent.delivery import DeliveryReceipt, LocalFileDelivery

from .models import Notification


@dataclass(frozen=True, slots=True)
class LocalOutboxReceipt:
    json: DeliveryReceipt
    markdown: DeliveryReceipt | None = None


class LocalOutbox:
    def __init__(self, root: str | Path, *, write_markdown: bool = True) -> None:
        self.delivery = LocalFileDelivery(root)
        self.write_markdown = write_markdown

    def deliver(self, notification: Notification) -> LocalOutboxReceipt:
        timestamp = notification.created_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stem = f"{timestamp}-{notification.fingerprint[:12]}"
        payload = json.dumps(notification.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        json_receipt = self.delivery.deliver(payload, filename=f"{stem}.json")
        markdown_receipt = None
        if self.write_markdown:
            link = f"\n\n来源：{notification.source_url}" if notification.source_url else ""
            markdown = (
                f"# {notification.to_dict()['priority']}｜{notification.title}\n\n"
                f"{notification.body}{link}\n\n"
                f"生成时间：{notification.created_at.isoformat()}\n"
            )
            markdown_receipt = self.delivery.deliver(markdown, filename=f"{stem}.md")
        return LocalOutboxReceipt(json=json_receipt, markdown=markdown_receipt)
