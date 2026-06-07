"""Manual agent-dispatch helper (verification / ops).

Dispatch the named Relay agent to a room on demand — primarily to verify the
inbound-phone demo without a real SIP call:

    python -m relay.agent.dispatch                 # dispatch relay-agent -> relay-demo
    python -m relay.agent.dispatch --room my-room  # dispatch to a specific room

After dispatching, open the dashboard (Live mode, Phone source — the default) and
have a participant join ``relay-demo`` and speak a question; a cited card should
stream to the dashboard. The same dispatch also happens automatically when the
dashboard hits ``GET /sessions/demo`` — this script is just the explicit path the
verification steps describe (equivalent to ``lk dispatch create --agent-name
relay-agent --room relay-demo``).
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from relay.config import settings
from relay.ids import stable_session_id


async def _dispatch(room: str, org_id: str) -> None:
    from relay.adapters.livekit_tokens import ensure_agent_dispatch, ensure_room

    session_id = stable_session_id(room)
    metadata = {
        "session_id": session_id,
        "org_id": org_id,
        "mode": "live",
        "customer_id": "",
    }
    await ensure_room(room, metadata)
    await ensure_agent_dispatch(room, metadata)
    print(
        f"Dispatched agent {settings.livekit_agent_name!r} to room {room!r}\n"
        f"  session_id (dashboard watches this): {session_id}\n"
        f"  ws channel: /ws/sessions/{session_id}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m relay.agent.dispatch",
        description="Dispatch the named Relay agent to a room (verification helper).",
    )
    parser.add_argument(
        "--room",
        default=settings.livekit_demo_room or "relay-demo",
        help=f"Target room (default: {settings.livekit_demo_room or 'relay-demo'}).",
    )
    parser.add_argument(
        "--org-id",
        default=settings.default_org_id,
        help="Organisation UUID to stamp into dispatch metadata.",
    )
    args = parser.parse_args()

    if not settings.livekit_api_key or not settings.livekit_api_secret:
        print("ERROR: LIVEKIT_API_KEY / LIVEKIT_API_SECRET must be set.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(_dispatch(args.room, args.org_id))


if __name__ == "__main__":
    main()
