"""Relay ingestion sub-package.

Async ingestion pipeline: upload → parse → chunk → embed → index.
Entry points:
  - :func:`relay.ingestion.pipeline.ingest_document` — core coroutine
  - :mod:`relay.ingestion.worker` — arq WorkerSettings + enqueue helper
"""
