"""Trigger detector — decides when to fire retrieval from a live transcript.

Strategy (configurable via constructor parameters):

1. **Question detection** — fires on any final utterance that contains an
   interrogative word or ends with "?".  This is the primary, low-latency path.

2. **Debounced continuous** — fires on the rolling transcript window every
   ``continuous_interval_s`` seconds even if no explicit question was detected,
   as long as there is new text since the last firing.  This is the fallback that
   keeps the co-pilot surfacing context during monologues.

3. **Deduplication** — a fired query is hashed and compared against the last
   ``_dedup_window`` fired hashes; if identical it is silently dropped.

Usage::

    detector = TriggerDetector()

    # On each final STT utterance:
    query = detector.should_fire(utterance_text, speaker="prospect")
    if query is not None:
        await orchestrator.synthesize(query_text=query, ...)

    # On each partial STT update (for continuous debounce bookkeeping only):
    detector.on_partial(partial_text)
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import deque
from typing import Deque

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Interrogative patterns used by the question detector
# ---------------------------------------------------------------------------

# Sentence-initial interrogative words (case-insensitive).
_INTERROGATIVE_STARTERS: tuple[str, ...] = (
    "who",
    "what",
    "when",
    "where",
    "why",
    "how",
    "which",
    "whose",
    "whom",
    "is",
    "are",
    "was",
    "were",
    "do",
    "does",
    "did",
    "can",
    "could",
    "would",
    "will",
    "should",
    "shall",
    "have",
    "has",
    "had",
    "may",
    "might",
    "must",
)

# Compile once.
_INTERROGATIVE_RE = re.compile(
    r"^\s*(?:" + "|".join(_INTERROGATIVE_STARTERS) + r")\b",
    re.IGNORECASE,
)


class TriggerDetector:
    """Stateful trigger detector for the Relay live co-pilot path.

    Parameters
    ----------
    continuous_interval_s:
        Minimum seconds between continuous-mode fires (default 15 s).
        Set to 0 to disable continuous mode; set very high to rely on
        question detection only.
    dedup_window:
        Number of recently-fired query hashes to keep for deduplication.
        If the same query text (normalised) fires within this window it is
        dropped.  Default 8 is enough to avoid repeat cards in a typical
        conversation.
    min_text_length:
        Ignore utterances shorter than this many characters (avoids firing
        on "uh", "yeah", etc.).
    """

    def __init__(
        self,
        continuous_interval_s: float = 15.0,
        dedup_window: int = 8,
        min_text_length: int = 8,
    ) -> None:
        self._continuous_interval_s = continuous_interval_s
        self._min_text_length = min_text_length

        # Dedup ring buffer — stores hex digests of normalised query strings.
        self._recent_hashes: Deque[str] = deque(maxlen=dedup_window)

        # Rolling window text: concatenation of recent finals for continuous mode.
        self._window_parts: list[str] = []
        self._window_max_parts: int = 6  # keep last N finals in the window

        # Monotonic timestamp of the last continuous fire.
        # Initialise to now so the first interval starts from worker startup,
        # not from epoch-zero (which would cause an immediate fire on the very
        # first utterance).
        self._last_continuous_fire_ts: float = time.monotonic()

        # Whether there is *new* text since the last continuous fire (to avoid
        # re-firing the same window text).
        self._has_new_text_since_continuous: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_fire(
        self,
        utterance: str,
        speaker: str = "unknown",
    ) -> str | None:
        """Decide whether a *final* utterance should trigger retrieval.

        Args:
            utterance: The complete final transcript text for this turn.
            speaker:   Speaker label (for logging only).

        Returns:
            The query string to send to the Orchestrator, or ``None`` if no
            trigger was detected.
        """
        text = utterance.strip()
        if len(text) < self._min_text_length:
            logger.debug("trigger: skip short utterance (%d chars)", len(text))
            return None

        # Update rolling window regardless of whether we fire.
        self._update_window(text)
        self._has_new_text_since_continuous = True

        # --- 1. Question detection (highest priority) ---
        if self._is_question(text):
            logger.debug("trigger: question detected from %s: %r", speaker, text[:80])
            return self._emit(text)

        # --- 2. Debounced continuous ---
        if self._continuous_interval_s > 0:
            now = time.monotonic()
            elapsed = now - self._last_continuous_fire_ts
            if elapsed >= self._continuous_interval_s and self._has_new_text_since_continuous:
                window_text = self._get_window_text()
                logger.debug(
                    "trigger: continuous fire after %.1fs, window=%r",
                    elapsed,
                    window_text[:80],
                )
                self._last_continuous_fire_ts = now
                self._has_new_text_since_continuous = False
                return self._emit(window_text)

        return None

    def on_partial(self, partial_text: str) -> None:
        """Notify the detector of a STT partial transcript.

        Partials are NOT used for question detection — they are too noisy.
        This method exists so callers can call it uniformly; currently it is
        a no-op but kept as an extension point (e.g. future topic-shift
        detection on partials).
        """
        # No-op: partials not used for triggering, only finals count.

    def reset(self) -> None:
        """Clear all state — call when a new session starts."""
        self._recent_hashes.clear()
        self._window_parts.clear()
        self._last_continuous_fire_ts = time.monotonic()
        self._has_new_text_since_continuous = False

    def clear_window(self) -> None:
        """Drop the rolling context window WITHOUT clearing dedup history.

        Call this right after an answer is produced so the debounced continuous mode does
        NOT re-fire the now-answered Q&A again (the cause of the live agent "looping" the
        previous answer when the caller pauses or just says "thanks"). Dedup hashes are
        kept so an immediate repeat of the same question is still suppressed.
        """
        self._window_parts.clear()
        self._has_new_text_since_continuous = False
        self._last_continuous_fire_ts = time.monotonic()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_question(self, text: str) -> bool:
        """Return True if ``text`` looks like a question."""
        # Explicit question mark anywhere in the utterance.
        if "?" in text:
            return True
        # Sentence starts with an interrogative word.
        if _INTERROGATIVE_RE.match(text):
            return True
        return False

    def _update_window(self, final_text: str) -> None:
        """Add *final_text* to the rolling context window."""
        self._window_parts.append(final_text)
        if len(self._window_parts) > self._window_max_parts:
            self._window_parts = self._window_parts[-self._window_max_parts :]

    def _get_window_text(self) -> str:
        """Return the concatenated rolling window as a single string."""
        return " ".join(self._window_parts).strip()

    def _normalise(self, text: str) -> str:
        """Normalise *text* for deduplication comparison."""
        # Lowercase, collapse whitespace, strip punctuation from edges.
        return re.sub(r"\s+", " ", text.lower()).strip("?.!, ")

    def _hash(self, text: str) -> str:
        return hashlib.sha256(self._normalise(text).encode()).hexdigest()[:16]

    def _emit(self, query_text: str) -> str | None:
        """Check dedup and return the query (or None if duplicate)."""
        h = self._hash(query_text)
        if h in self._recent_hashes:
            logger.debug("trigger: deduplicated query hash=%s", h)
            return None
        self._recent_hashes.append(h)
        logger.info("trigger: firing query %r (hash=%s)", query_text[:80], h)
        return query_text
