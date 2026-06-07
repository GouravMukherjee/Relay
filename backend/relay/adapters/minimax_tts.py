"""MiniMax text-to-speech (T2A) adapter — the "whisper-back" voice for cards.

Calls MiniMax's T2A v2 API (Speech-02-Turbo, low-latency) and returns MP3 bytes so a
grounded card can be read aloud to the rep. The API returns the audio as a hex string in
``data.audio``; we decode it to raw MP3.

Required creds: ``minimax_api_key`` + ``minimax_group_id``.
"""
from __future__ import annotations

import logging

import httpx

from relay.config import settings

logger = logging.getLogger(__name__)


class MinimaxTTS:
    """MiniMax T2A adapter. ``synthesize(text) -> mp3 bytes``."""

    def __init__(self) -> None:
        if not settings.minimax_api_key or not settings.minimax_group_id:
            raise RuntimeError(
                "MinimaxTTS requires MINIMAX_API_KEY and MINIMAX_GROUP_ID."
            )
        self._url = (
            f"{settings.minimax_base_url.rstrip('/')}/v1/t2a_v2"
            f"?GroupId={settings.minimax_group_id}"
        )
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {settings.minimax_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(60.0),
        )

    async def synthesize(self, text: str, *, voice_id: str | None = None) -> bytes:
        """Return MP3 bytes for *text* (empty bytes if text is blank)."""
        text = (text or "").strip()
        if not text:
            return b""
        payload = {
            "model": settings.minimax_tts_model,
            "text": text[:5000],  # keep cards short; cap defensively
            "stream": False,
            "voice_setting": {
                "voice_id": voice_id or settings.minimax_voice_id,
                "speed": 1.0,
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        }
        resp = await self._client.post(self._url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        base = data.get("base_resp") or {}
        if base.get("status_code") not in (0, None):
            raise RuntimeError(f"MiniMax TTS failed: {base.get('status_msg')}")
        audio_hex = (data.get("data") or {}).get("audio")
        if not audio_hex:
            raise RuntimeError("MiniMax TTS returned no audio")
        mp3 = bytes.fromhex(audio_hex)
        logger.info("minimax_tts_ok", extra={"text_len": len(text), "bytes": len(mp3)})
        return mp3

    async def aclose(self) -> None:
        await self._client.aclose()
