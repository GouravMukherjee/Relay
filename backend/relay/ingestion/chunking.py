"""Text chunking for the ingestion pipeline.

:func:`chunk_text` splits a long document text into overlapping windows that
are sized for the embedding model (~400 tokens per chunk, ~50-token overlap).
It uses a two-pass strategy:

1. **Semantic split**: prefer breaking at double-newlines (paragraph boundaries)
   or single newlines / sentence-ending punctuation.
2. **Hard split**: if a candidate block still exceeds the token ceiling, split it
   at the nearest word boundary.

We deliberately avoid importing a tokeniser at runtime so that this module has
zero heavy dependencies and can be used in tests without any external creds.
Token count is approximated with the standard 4-chars-per-token heuristic —
good enough for chunking purposes.
"""

from __future__ import annotations

import re
from typing import Generator

# ---------------------------------------------------------------------------
# Public constants (same defaults referenced in the frozen contract sheet)
# ---------------------------------------------------------------------------

TARGET_TOKENS: int = 400   # approximate target chunk size
OVERLAP_TOKENS: int = 50   # approximate overlap between consecutive chunks

# 4 characters per token is a widely-used heuristic for English prose.
_CHARS_PER_TOKEN: int = 4


def _approx_tokens(text: str) -> int:
    """Return the approximate token count of *text* using the 4-char heuristic."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _split_into_paragraphs(text: str) -> list[str]:
    """Split *text* on blank lines, then fall back to single newlines.

    Returns a flat list of non-empty stripped segments.
    """
    # Normalise Windows line endings.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # First try paragraph-level split (blank line).
    paras = re.split(r"\n\s*\n", text)
    segments: list[str] = []
    for para in paras:
        para = para.strip()
        if not para:
            continue
        # If the paragraph is already small enough, keep it whole.
        if _approx_tokens(para) <= TARGET_TOKENS * 2:
            segments.append(para)
        else:
            # Fall back: split on single newlines.
            for line in para.split("\n"):
                line = line.strip()
                if line:
                    segments.append(line)
    return segments


def _hard_split(text: str, max_chars: int) -> Generator[str, None, None]:
    """Yield slices of *text* at most *max_chars* long, breaking at word edges."""
    start = 0
    length = len(text)
    while start < length:
        end = min(start + max_chars, length)
        # Walk backward to a word boundary if we're not at the string end.
        if end < length:
            boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            yield chunk
        start = end


def chunk_text(
    text: str,
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[str]:
    """Split *text* into overlapping chunks suitable for embedding.

    Args:
        text:           Full document text (plain text, no binary).
        target_tokens:  Desired chunk size in (approximate) tokens.
        overlap_tokens: Token overlap between consecutive chunks.

    Returns:
        Ordered list of chunk strings.  Each string is non-empty and stripped.
        Empty input → empty list.

    The algorithm:
    1. Split the text into paragraph-level segments.
    2. Accumulate segments into a running buffer until adding the next segment
       would exceed *target_tokens*.  When the limit is hit, flush the buffer
       as a chunk and seed the next buffer with the overlap tail.
    3. Any segment that is itself larger than the hard ceiling (2× target) is
       further split word-by-word before being accumulated.
    """
    text = text.strip()
    if not text:
        return []

    target_chars = target_tokens * _CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * _CHARS_PER_TOKEN
    max_segment_chars = target_chars * 2  # hard ceiling before word-splitting

    segments = _split_into_paragraphs(text)
    if not segments:
        return []

    # Expand any over-sized segments via hard word-boundary splitting.
    expanded: list[str] = []
    for seg in segments:
        if len(seg) > max_segment_chars:
            expanded.extend(_hard_split(seg, target_chars))
        else:
            expanded.append(seg)

    chunks: list[str] = []
    buffer: list[str] = []
    buffer_chars: int = 0

    def _flush() -> None:
        """Emit the current buffer as a chunk and seed overlap."""
        nonlocal buffer, buffer_chars
        if buffer:
            joined = " ".join(buffer).strip()
            if joined:
                chunks.append(joined)
        # Seed the next buffer with the trailing overlap window.
        if overlap_chars > 0 and buffer:
            overlap_text = " ".join(buffer)
            # Take the last overlap_chars characters, then trim to word boundary.
            tail = overlap_text[-overlap_chars:]
            # Find the first space to avoid starting mid-word.
            space_idx = tail.find(" ")
            if space_idx > 0:
                tail = tail[space_idx + 1 :]
            tail = tail.strip()
            if tail:
                buffer = [tail]
                buffer_chars = len(tail)
            else:
                buffer = []
                buffer_chars = 0
        else:
            buffer = []
            buffer_chars = 0

    for seg in expanded:
        seg_chars = len(seg)
        # If adding this segment would exceed the target, flush first.
        if buffer and (buffer_chars + 1 + seg_chars) > target_chars:
            _flush()
        buffer.append(seg)
        buffer_chars += seg_chars + 1  # +1 for the joining space

    # Flush any remaining content.
    if buffer:
        joined = " ".join(buffer).strip()
        if joined:
            chunks.append(joined)

    return chunks
