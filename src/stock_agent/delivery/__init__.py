"""Delivery adapters for generated reports."""

from .local_file import DeliveryReceipt, LocalFileDelivery

__all__ = ["DeliveryReceipt", "LocalFileDelivery"]
