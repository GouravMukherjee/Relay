"""relay.interfaces — Abstract base classes for all sponsor service integrations.

Each interface has exactly one production adapter in relay/adapters/ and a
mock implementation in tests/. Business logic must depend only on these ABCs,
never on concrete adapter classes.
"""
from relay.interfaces.retrieval import RetrievalService, RetrievedChunk, RetrievalResult
from relay.interfaces.parser import DocumentParser, ParsedBlock, ParsedDoc
from relay.interfaces.embeddings import Embeddings
from relay.interfaces.llm import CardDraft, LLMClient

__all__ = [
    # retrieval
    "RetrievalService",
    "RetrievedChunk",
    "RetrievalResult",
    # parser
    "DocumentParser",
    "ParsedBlock",
    "ParsedDoc",
    # embeddings
    "Embeddings",
    # llm
    "CardDraft",
    "LLMClient",
]
