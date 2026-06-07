"""Unsiloed document parser adapter.

Implements ``DocumentParser`` using the Unsiloed document-intelligence API.

Required creds: ``unsiloed_api_key``.

# TODO: confirm <Unsiloed> API — the endpoint path, multipart field names,
# and response shape below are based on best-guess from available docs.
# Verify against the actual Unsiloed API before shipping.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from relay.config import settings
from relay.interfaces.parser import DocumentParser, ParsedBlock, ParsedDoc

logger = logging.getLogger(__name__)

# TODO: confirm <Unsiloed> API — base URL.
_UNSILOED_BASE_URL = "https://api.unsiloed.ai"


class UnsiloedParser(DocumentParser):
    """Document parser backed by the Unsiloed document-intelligence API.

    Supports PDF, DOCX, PPTX, XLSX, HTML, plain-text, and other formats that
    Unsiloed handles natively.

    Required settings
    -----------------
    unsiloed_api_key : str — API key passed as ``Authorization: Bearer …``
    """

    def __init__(self) -> None:
        if not settings.unsiloed_api_key:
            raise RuntimeError(
                "UnsiloedParser requires UNSILOED_API_KEY to be set in the environment."
            )
        self._client = httpx.AsyncClient(
            base_url=_UNSILOED_BASE_URL,
            headers={"Authorization": f"Bearer {settings.unsiloed_api_key}"},
            # Parsing large PDFs may take a few seconds.
            timeout=httpx.Timeout(60.0),
        )

    # ------------------------------------------------------------------
    # DocumentParser interface
    # ------------------------------------------------------------------

    async def parse(
        self,
        raw: bytes,
        content_type: str,
        filename: str | None = None,
    ) -> ParsedDoc:
        """Upload *raw* to Unsiloed and return a structured ``ParsedDoc``.

        # TODO: confirm <Unsiloed> API — verify /parse path, multipart field
        # names (``file``, ``content_type``?), and response schema
        # (``text``, ``blocks[].text``, ``blocks[].kind``).
        """
        files: dict[str, Any] = {
            "file": (filename or "document", raw, content_type),
        }
        # TODO: confirm <Unsiloed> API — any additional form fields needed.
        data: dict[str, str] = {"content_type": content_type}
        if filename:
            data["filename"] = filename

        response = await self._client.post(
            "/v1/parse",  # TODO: confirm <Unsiloed> API — endpoint path
            files=files,
            data=data,
        )
        response.raise_for_status()
        payload = response.json()

        # TODO: confirm <Unsiloed> API — exact field names in the response.
        full_text: str = payload.get("text", "")

        raw_blocks: list[dict[str, Any]] = payload.get("blocks", [])
        blocks: list[ParsedBlock] = []
        for blk in raw_blocks:
            blk_text: str = blk.get("text", "")
            if blk_text:
                blocks.append(
                    ParsedBlock(
                        text=blk_text,
                        kind=blk.get("kind", "text"),
                        metadata={
                            k: v
                            for k, v in blk.items()
                            if k not in ("text", "kind")
                        },
                    )
                )

        # If the API returns blocks but no top-level ``text``, reconstruct it.
        if not full_text and blocks:
            full_text = "\n\n".join(b.text for b in blocks)

        logger.info(
            "unsiloed_parse_ok",
            extra={
                "content_type": content_type,
                "filename": filename,
                "block_count": len(blocks),
                "text_len": len(full_text),
            },
        )
        return ParsedDoc(text=full_text, blocks=blocks)

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Call on application shutdown."""
        await self._client.aclose()
