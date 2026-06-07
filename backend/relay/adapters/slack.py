"""Slack lead-routing notifier adapter.

Posts lead-routing notifications to a Slack incoming webhook.

Required creds: ``slack_webhook_url``.
"""
from __future__ import annotations

import logging

import httpx

from relay.config import settings

logger = logging.getLogger(__name__)


class SlackNotifier:
    """Slack notifier for lead-routing events.

    Posts a formatted message to the configured incoming webhook URL when
    a lead is routed to a sales rep or channel.

    Required settings
    -----------------
    slack_webhook_url : str — Slack incoming webhook URL
    """

    def __init__(self) -> None:
        if not settings.slack_webhook_url:
            raise RuntimeError(
                "SlackNotifier requires SLACK_WEBHOOK_URL to be set in the environment."
            )
        self._webhook_url = settings.slack_webhook_url
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def route_lead(self, text: str) -> str:
        """Post *text* to the Slack webhook as a lead-routing notification.

        Args:
            text: Formatted notification text to post.  The caller is
                  responsible for composing a human-readable message that
                  does NOT include PII beyond what the sales rep needs.

        Returns:
            The target channel name/identifier if returned by Slack, or
            ``"#routed"`` as a placeholder when the webhook response is
            opaque (Slack incoming webhooks return ``"ok"`` as plain text).
        """
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
        """Close the underlying HTTP client. Call on application shutdown."""
        await self._client.aclose()
