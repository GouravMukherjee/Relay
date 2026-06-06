"""Relay LiveKit Agent Worker — the always-on real-time core (TDD §3.1).

This is NOT an HTTP service. It is a long-running process that joins LiveKit
rooms, subscribes to the audio track, streams it to STT, runs the trigger
detector, and emits queries to the retrieval service + transcripts to the
gateway. This is exactly the workload Vercel cannot host and TrueFoundry can:
a process that stays alive for the whole call with no inbound port.

The skeleton below runs a heartbeat loop so the container stays healthy while
the LiveKit Agents wiring (TODO T1.1–T1.3) is filled in.
"""
from __future__ import annotations

import asyncio
import logging
import signal

from .config import settings

logging.basicConfig(level=settings.log_level.upper())
log = logging.getLogger("relay.worker")

_stop = asyncio.Event()


async def run_worker() -> None:
    log.info("Relay worker starting (livekit_url=%s)", settings.livekit_url or "<unset>")

    # TODO(T1.1): connect to LiveKit and register the agent.
    #   from livekit import agents
    #   await agents.Worker(entrypoint=on_room, ...).run()
    #
    # on_room(ctx):
    #   T1.2  stream ctx.audio -> Deepgram STT -> partial/final transcripts
    #   T1.3  trigger detector on finals -> POST settings.retrieval_url/retrieve
    #   T1.6  retrieved chunks -> Claude synthesis -> push card to gateway WS

    # Heartbeat keeps the always-on process alive and observable until then.
    while not _stop.is_set():
        log.info("worker heartbeat — waiting for LiveKit wiring")
        try:
            await asyncio.wait_for(_stop.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass

    log.info("Relay worker stopped")


def _handle_signal(*_):
    _stop.set()


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            signal.signal(sig, _handle_signal)  # Windows fallback
    loop.run_until_complete(run_worker())


if __name__ == "__main__":
    main()
