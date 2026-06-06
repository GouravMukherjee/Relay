"""TrueFoundry deploy spec — Relay LiveKit Worker (always-on, NO inbound port).

This is the component Vercel could not host: a long-lived process that holds a
LiveKit room connection for the whole call. On TrueFoundry it's a Service with
no ports — it dials OUT to LiveKit Cloud and the retrieval/gateway services.

    python deploy/deploy_worker.py

Scaling note: one worker handles a bounded number of concurrent rooms. Bump
`replicas` (or add an autoscaler) when you need more concurrent live calls.
"""
import os

from truefoundry.deploy import Build, DockerFileBuild, Service, Resources

WORKSPACE_FQN = os.environ["WORKSPACE_FQN"]

service = Service(
    name="relay-worker",
    image=Build(
        build_spec=DockerFileBuild(
            dockerfile_path="./Dockerfile",
            build_context_path="./",
            command="python -m app.worker",
        )
    ),
    ports=[],  # no inbound traffic — outbound-only worker
    resources=Resources(
        cpu_request=0.5,
        cpu_limit=1,
        memory_request=512,
        memory_limit=1024,
    ),
    env={
        "DATABASE_URL": "tfy-secret://relay/database-url",
        "RETRIEVAL_URL": "http://relay-retrieval:8001",
        "DEEPGRAM_API_KEY": "tfy-secret://relay/deepgram-api-key",
        "ANTHROPIC_API_KEY": "tfy-secret://relay/anthropic-api-key",
        "ANTHROPIC_MODEL": "claude-opus-4-8",
        "LIVEKIT_URL": "tfy-secret://relay/livekit-url",
        "LIVEKIT_API_KEY": "tfy-secret://relay/livekit-api-key",
        "LIVEKIT_API_SECRET": "tfy-secret://relay/livekit-api-secret",
    },
    replicas=1,
)

service.deploy(workspace_fqn=WORKSPACE_FQN)
