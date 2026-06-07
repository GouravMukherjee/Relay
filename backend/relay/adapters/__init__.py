"""Relay sponsor adapters — concrete implementations of the abstract interfaces.

Each adapter is constructed from ``relay.config.settings`` and raises
``RuntimeError`` at construction time if a required credential is absent.

Adapters
--------
MossRetrieval          – Moss retrieval service (primary)
PgVectorRetrieval      – pgvector cosine fallback
UnsiloedParser         – Unsiloed document parser
TfyEmbeddings          – TrueFoundry-hosted 1024-d embeddings
TfyLLMClient           – TrueFoundry AI Gateway (Claude / Qwen / Minimax)
S3Storage              – AWS S3 raw-file storage
SlackNotifier          – Slack lead-routing webhook
mint_livekit_token     – Server-side LiveKit access-token minting
"""
