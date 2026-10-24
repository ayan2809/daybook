"""Web Push with VAPID via pywebpush (plan 10, 12.3). The VAPID `sub` claim is the site URL, so no
email address goes to the push service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


@dataclass
class SendResult:
    ok: bool
    gone: bool = False  # 404/410: delete the subscription
    status: int | None = None


class Sender(Protocol):
    def send(self, sub: dict, payload: dict) -> SendResult: ...


class VapidSender:
    def __init__(self, private_key: str, site_url: str):
        self.private_key = private_key
        self.sub = (
            site_url if site_url.startswith(("mailto:", "https://")) else "https://example.invalid"
        )

    def send(self, sub: dict, payload: dict) -> SendResult:
        from pywebpush import WebPushException, webpush

        try:
            r = webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=json.dumps(payload),
                vapid_private_key=self.private_key,
                vapid_claims={"sub": self.sub},
                ttl=3600,
            )
            return SendResult(True, status=getattr(r, "status_code", 201))
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            return SendResult(False, gone=status in (404, 410), status=status)


class FakeSender:
    def __init__(self):
        self.sent: list[tuple[dict, dict]] = []
        self.gone_endpoints: set[str] = set()

    def send(self, sub: dict, payload: dict) -> SendResult:
        if sub["endpoint"] in self.gone_endpoints:
            return SendResult(False, gone=True, status=410)
        self.sent.append((sub, payload))
        return SendResult(True, status=201)
