"""Document parser interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ParsedBlock:
    """A single logical block within a parsed document (paragraph, table, etc.)."""
    text: str
    kind: str = "text"    # text | table | heading | list_item
    metadata: dict = field(default_factory=dict)


@dataclass
class ParsedDoc:
    """Full result of parsing a raw document file."""
    text: str                          # concatenated plain text (whole doc)
    blocks: list[ParsedBlock] = field(default_factory=list)


class DocumentParser(ABC):
    """Abstract document parser — Unsiloed in production."""

    @abstractmethod
    async def parse(
        self,
        raw: bytes,
        content_type: str,
        filename: str | None = None,
    ) -> ParsedDoc:
        """Parse *raw* bytes into a ParsedDoc.

        Args:
            raw:          Raw file bytes.
            content_type: MIME type (e.g. "application/pdf", "text/plain").
            filename:     Optional original filename — used for format hints.

        Returns:
            ParsedDoc with full text and optionally structured blocks.
        """
        ...
