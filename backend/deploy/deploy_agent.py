"""TrueFoundry deploy spec — Relay LiveKit agent worker (always-on, NO inbound port).

A long-lived process that holds a LiveKit room connection for the duration of a call:
dials OUT to LiveKit Cloud, streams audio through Deepgram STT, runs the trigger detector,
and calls the in-process orchestrator to push grounded cards. It receives no inbound
traffic, so it has no ports.

    cd backend && python deploy/deploy_agent.py

Scaling: one worker handles a bounded number of concurrent rooms. Bump `replicas` (or add
an autoscaler) for more concurrent live calls.
"""
import os

from truefoundry.deploy import Build, DockerFileBuild, Service, Resources

WORKSPACE_FQN = os.environ["WORKSPACE_FQN"]

service = Service(
    name="relay-agent",
    image=Build(
        build_spec=DockerFileBuild(
            dockerfile_path="./Dockerfile",
            build_context_path="./",
            # `start` = production mode (connect to configured LIVEKIT_URL). See
            # relay/agent/worker.py docstring.
            command="python -m relay.agent.worker start",
        )
    ),
    ports=[],  # outbound-only worker — no inbound traffic
    resources=Resources(
        cpu_request=0.5,
        cpu_limit=1,
        memory_request=512,
        memory_limit=1024,
    ),
    env={
        "DATABASE_URL": "tfy-secret://relay/database-url",
        "REDIS_URL": "tfy-secret://relay/redis-url",
        "DEEPGRAM_API_KEY": "tfy-secret://relay/deepgram-api-key",
        "ANTHROPIC_API_KEY": "tfy-secret://relay/anthropic-api-key",
        "TFY_API_KEY": "tfy-secret://relay/tfy-api-key",
        "TFY_GATEWAY_URL": "tfy-secret://relay/tfy-gateway-url",
        "LLM_MODEL": "claude",
        "MOSS_API_KEY": "tfy-secret://relay/moss-api-key",
        "MOSS_BASE_URL": "tfy-secret://relay/moss-base-url",
        "LIVEKIT_URL": "tfy-secret://relay/livekit-url",
        "LIVEKIT_API_KEY": "tfy-secret://relay/livekit-api-key",
        "LIVEKIT_API_SECRET": "tfy-secret://relay/livekit-api-secret",
        "DEFAULT_ORG_ID": "tfy-secret://relay/default-org-id",
    },
    replicas=1,
)

service.deploy(workspace_fqn=WORKSPACE_FQN)
