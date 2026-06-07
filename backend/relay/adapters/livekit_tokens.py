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
