"""Slack lead-routing notifier adapter.

Posts lead-routing notifications to a Slack incoming webhook.

Slack is OPTIONAL: if ``slack_webhook_url`` is unset the notifier is created in a
disabled state — :meth:`route_lead` then logs and returns ``None`` instead of
raising, so Intake-mode lead routing still succeeds (the caller falls back to the
default channel). This adapter therefore does NOT raise at construction.
"""
from __future__ import annotations

import logging

import httpx

from relay.config import settings

logger = logging.getLogger(__name__)


class SlackNotifier:
    """Slack notifier for lead-routing events.

    Posts a formatted message to the configured incoming webhook URL when a lead
    is routed to a sales rep or channel. If no webhook is configured the notifier
    is *disabled*: ``route_lead`` becomes a no-op that logs and returns ``None``.

    Optional settings
    -----------------
    slack_webhook_url : str — Slack incoming webhook URL. If empty, Slack posting
        is skipped (logged), not an error.
    """

    def __init__(self) -> None:
        self._webhook_url = settings.slack_webhook_url or ""
        self._enabled = bool(self._webhook_url)
        # Only build an HTTP client when actually enabled.
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0)) if self._enabled else None
        if not self._enabled:
            logger.info("slack_notifier_disabled (SLACK_WEBHOOK_URL unset) — posts will be skipped")

    @property
    def enabled(self) -> bool:
        """Whether a webhook is configured and posting is active."""
        return self._enabled

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def route_lead(self, text: str) -> str | None:
        """Post *text* to the Slack webhook as a lead-routing notification.

        Args:
            text: Formatted notification text to post.  The caller is
                  responsible for composing a human-readable message that
                  does NOT include PII beyond what the sales rep needs.

        Returns:
            The target channel name/identifier (``"#routed"`` placeholder, since
            standard incoming webhooks return only ``"ok"``), or ``None`` when
            Slack is not configured (the post is skipped and logged).
        """
        if not self._enabled or self._client is None:
            logger.info("slack_route_skipped (no webhook configured)", extra={"text_len": len(text)})
            return None

        payload = {"text": text}
        response = await self._client.post(self._webhook_url, json=payload)
        response.raise_for_status()

        # Slack incoming webhooks respond with the plain text "ok" — there is
        # no channel name in the response for standard webhooks.
        # TODO: if using the Slack Web API (/chat.postMessage) instead, extract
        # the channel from the JSON response.
        logger.info("slack_lead_routed", extra={"text_len": len(text)})
        return "#routed"

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Safe to call when disabled."""
        if self._client is not None:
            await self._client.aclose()
