"""Unsiloed document parser adapter.

Implements ``DocumentParser`` using the Unsiloed vision API (verified):
  POST {base}/parse   (multipart ``file`` OR ``url``, header ``api-key``) -> {job_id, status}
  GET  {base}/parse/{job_id}  -> poll until status == "Succeeded" | "Failed"

The completed response carries ``chunks[]``; each chunk has an ``embed`` field (the chunk's
content rolled up as Markdown, ready for an embedder) and ``segments[]`` (typed layout
regions with ``markdown``/``content``). We build ``ParsedDoc.text`` from the chunk markdown
and expose segments as blocks.

Required creds: ``unsiloed_api_key``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from relay.config import settings
from relay.interfaces.parser import DocumentParser, ParsedBlock, ParsedDoc

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 3.0
_POLL_TIMEOUT_S = 180.0


class UnsiloedParser(DocumentParser):
    """Document parser backed by the Unsiloed vision API (async job + poll).

    Required settings
    -----------------
    unsiloed_api_key  : str — API key sent as the ``api-key`` header
    unsiloed_base_url : str — API base (default https://prod.visionapi.unsiloed.ai)
    """

    def __init__(self) -> None:
        if not settings.unsiloed_api_key:
            raise RuntimeError(
                "UnsiloedParser requires UNSILOED_API_KEY to be set in the environment."
            )
        self._client = httpx.AsyncClient(
            base_url=settings.unsiloed_base_url.rstrip("/"),
            headers={"api-key": settings.unsiloed_api_key},
            timeout=httpx.Timeout(60.0),
        )

    async def parse(
        self,
        raw: bytes,
        content_type: str,
        filename: str | None = None,
    ) -> ParsedDoc:
        """Submit *raw* to Unsiloed, poll until done, return a structured ``ParsedDoc``."""
        # 1) Submit the parse job.
        files = {"file": (filename or "document", raw, content_type or "application/octet-stream")}
        resp = await self._client.post("/parse", files=files)
        resp.raise_for_status()
        submit = resp.json()
        job_id = submit.get("job_id")
        if not job_id:
            raise RuntimeError(f"Unsiloed: no job_id in submit response: {submit}")

        # 2) Poll until terminal.
        waited = 0.0
        result: dict[str, Any] = {}
        while waited < _POLL_TIMEOUT_S:
            await asyncio.sleep(_POLL_INTERVAL_S)
            waited += _POLL_INTERVAL_S
            poll = await self._client.get(f"/parse/{job_id}")
            poll.raise_for_status()
            result = poll.json()
            status = str(result.get("status", "")).lower()
            if status == "succeeded":
                break
            if status == "failed":
                raise RuntimeError(f"Unsiloed parse failed for job {job_id}: {result.get('message')}")
        else:
            raise RuntimeError(f"Unsiloed parse timed out for job {job_id} after {waited:.0f}s")

        # 3) Build text + blocks from chunks/segments.
        chunks: list[dict[str, Any]] = result.get("chunks", []) or []
        blocks: list[ParsedBlock] = []
        chunk_texts: list[str] = []
        for ch in chunks:
            embed = ch.get("embed")
            seg_texts: list[str] = []
            for seg in ch.get("segments", []) or []:
                seg_text = seg.get("markdown") or seg.get("content") or ""
                if seg_text:
                    seg_texts.append(seg_text)
                    blocks.append(
                        ParsedBlock(
                            text=seg_text,
                            kind=str(seg.get("segment_type", "text")),
                            metadata={
                                "page_number": seg.get("page_number"),
                                "segment_id": seg.get("segment_id"),
                            },
                        )
                    )
            chunk_text = embed if isinstance(embed, str) and embed.strip() else "\n\n".join(seg_texts)
            if chunk_text.strip():
                chunk_texts.append(chunk_text)

        full_text = "\n\n".join(chunk_texts)
        logger.info(
            "unsiloed_parse_ok",
            extra={
                "file_name": filename,
                "chunks": len(chunks),
                "blocks": len(blocks),
                "text_len": len(full_text),
            },
        )
        return ParsedDoc(text=full_text, blocks=blocks)

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Call on application shutdown."""
        await self._client.aclose()
