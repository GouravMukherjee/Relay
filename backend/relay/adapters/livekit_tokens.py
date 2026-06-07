"""LiveKit access-token minting adapter.

Mints short-lived, room-scoped LiveKit JWT access tokens on the server side
using the official ``livekit-api`` Python SDK.

The LiveKit API secret NEVER leaves the server — clients receive only the
signed JWT and cannot reconstruct the secret from it.

Required creds: ``livekit_api_key``, ``livekit_api_secret``.
"""
from __future__ import annotations

import datetime
import json
import logging

from livekit.api import AccessToken, VideoGrants  # pypi: livekit-api

from relay.config import settings

logger = logging.getLogger(__name__)

# Default token TTL — short enough to limit blast radius if a token leaks.
_DEFAULT_TTL_SECONDS = 900  # 15 minutes


def mint_livekit_token(
    room: str,
    identity: str,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> str:
    """Mint a signed LiveKit access token scoped to a single room.

    This is a synchronous helper (JWT signing is CPU-bound, not I/O-bound)
    that can be called from any async or sync context.

    Args:
        room:        LiveKit room name the token grants access to.
        identity:    Participant identity (e.g. user ID or session ID).
                     Must be unique within the room.
        ttl_seconds: Token validity period in seconds. Defaults to 900 (15 min).

    Returns:
        A signed JWT string suitable for passing to ``Room.connect()`` on the
        client side or to the WS ``?token=`` query parameter.

    Raises:
        RuntimeError: If ``LIVEKIT_API_KEY`` or ``LIVEKIT_API_SECRET`` are not
                      set in the environment.
    """
    if not settings.livekit_api_key:
        raise RuntimeError(
            "mint_livekit_token requires LIVEKIT_API_KEY to be set in the environment."
        )
    if not settings.livekit_api_secret:
        raise RuntimeError(
            "mint_livekit_token requires LIVEKIT_API_SECRET to be set in the environment."
        )

    token = (
        AccessToken(
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        )
        .with_identity(identity)
        .with_ttl(datetime.timedelta(seconds=ttl_seconds))
        .with_grants(
            VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )

    logger.info(
        "livekit_token_minted",
        extra={
            "room": room,
            "identity": identity,
            "ttl_seconds": ttl_seconds,
        },
    )
    return token


async def ensure_room(room: str, metadata: dict[str, str]) -> None:
    """Create (or update) a LiveKit room carrying session metadata.

    The agent worker reads ``org_id`` / ``mode`` / ``customer_id`` from the room's
    metadata JSON (see ``relay.agent.worker.entrypoint``), so the gateway must stamp
    that metadata onto the room when a session starts — otherwise the agent falls
    back to the default org in "live" mode. Best-effort: callers should wrap this in
    try/except and not block session creation on failure.

    Args:
        room:     LiveKit room name (e.g. ``relay-ses_…``).
        metadata: Plain string→string dict; serialised to JSON as the room metadata.

    Raises:
        RuntimeError: if LiveKit credentials are not configured.
    """
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise RuntimeError("ensure_room requires LIVEKIT_API_KEY and LIVEKIT_API_SECRET.")

    # Imported here so token minting (the hot path) doesn't pull in the full API client.
    from livekit import api  # pypi: livekit-api

    lkapi = api.LiveKitAPI(
        url=settings.livekit_url or None,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )
    try:
        # create_room is idempotent for an existing room name; it sets metadata.
        # TODO: confirm livekit-api CreateRoomRequest field names for the installed version.
        await lkapi.room.create_room(
            api.CreateRoomRequest(name=room, metadata=json.dumps(metadata))
        )
        logger.info("livekit_room_ensured", extra={"room": room})
    finally:
        await lkapi.aclose()


async def ensure_agent_dispatch(
    room: str,
    metadata: dict[str, str],
    agent_name: str | None = None,
) -> None:
    """Explicitly dispatch the named agent worker to *room* (idempotent).

    With ``WorkerOptions.agent_name`` set, the worker is dispatched ONLY on demand —
    automatic dispatch is off — so the gateway must request a dispatch for every room
    it wants the agent in (each browser session room, and the fixed demo room). This
    mirrors the SIP pattern (``room_config.agents``) for the non-SIP paths.

    Idempotent: if a dispatch for ``agent_name`` already exists in the room, no second
    one is created (avoids spawning duplicate agent instances on dashboard remounts).
    Best-effort — callers wrap in try/except and never block session creation on it.

    Args:
        room:       Target LiveKit room name.
        metadata:   Stamped into the dispatch (the agent reads it from ``ctx.job.metadata``).
        agent_name: Dispatch name to target. Defaults to ``settings.livekit_agent_name``.
    """
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise RuntimeError("ensure_agent_dispatch requires LIVEKIT_API_KEY and LIVEKIT_API_SECRET.")

    name = agent_name or settings.livekit_agent_name
    if not name:
        return  # no named agent configured -> nothing to dispatch

    from livekit import api  # pypi: livekit-api

    lkapi = api.LiveKitAPI(
        url=settings.livekit_url or None,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )
    try:
        # Skip if an identical dispatch already exists for this room.
        try:
            existing = await lkapi.agent_dispatch.list_dispatch(room_name=room)
            if any(getattr(d, "agent_name", None) == name for d in existing):
                logger.info("livekit_dispatch_exists", extra={"room": room, "agent": name})
                return
        except Exception:  # noqa: BLE001 — listing is an optimisation, not required
            pass

        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=name,
                room=room,
                metadata=json.dumps(metadata),
            )
        )
        logger.info("livekit_agent_dispatched", extra={"room": room, "agent": name})
    finally:
        await lkapi.aclose()
