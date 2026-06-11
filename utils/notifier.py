"""Instant notification hub for DingTalk custom robots."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Any

from config.settings import load_secure_config
from utils.logger import get_logger

logger = get_logger(__name__)


class SystemNotifier:
    """Dispatch async alerts to DingTalk without blocking the trading event loop."""

    def __init__(self) -> None:
        config = load_secure_config()
        self.dingtalk_webhook = (
            os.getenv("DINGTALK_WEBHOOK_URL")
            or config.get("DINGTALK_WEBHOOK_URL")
            or config.get("dingtalk_webhook_url")
            or ""
        ).strip()
        self.dingtalk_secret = (
            os.getenv("DINGTALK_SECRET")
            or config.get("DINGTALK_SECRET")
            or config.get("dingtalk_secret")
            or ""
        ).strip()

    @property
    def enabled(self) -> bool:
        return bool(self.dingtalk_webhook)

    async def send_notification(self, text: str) -> None:
        """Asynchronously dispatch textual alerts to DingTalk."""
        if not text:
            return

        if not self.enabled:
            logger.debug("DingTalk notifier not configured; skipped: %s", text)
            return

        await self._send_dingtalk(text)
        logger.debug("DingTalk notification dispatched: %s", text)

    def _build_signed_url(self) -> str:
        if not self.dingtalk_secret:
            return self.dingtalk_webhook

        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.dingtalk_secret}"
        sign = urllib.parse.quote_plus(
            base64.b64encode(
                hmac.new(
                    self.dingtalk_secret.encode("utf-8"),
                    string_to_sign.encode("utf-8"),
                    digestmod=hashlib.sha256,
                ).digest()
            ).decode("utf-8")
        )
        separator = "&" if "?" in self.dingtalk_webhook else "?"
        return f"{self.dingtalk_webhook}{separator}timestamp={timestamp}&sign={sign}"

    def _format_text(self, text: str) -> str:
        plain = re.sub(r"<[^>]+>", "", text)
        return f"【Quant Crypto System】\n{plain}"

    async def _send_dingtalk(self, text: str) -> None:
        url = self._build_signed_url()
        payload = {
            "msgtype": "text",
            "text": {"content": self._format_text(text)},
        }
        try:
            await asyncio.to_thread(self._execute_post, url, payload)
        except Exception as exc:
            logger.error("Failed to send DingTalk notification: %s", exc)

    def _execute_post(self, url: str, data: dict[str, Any]) -> None:
        req = urllib.request.Request(url, method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        body = json.dumps(data).encode("utf-8")
        with urllib.request.urlopen(req, data=body, timeout=5) as response:
            response.read()
