"""TrueFoundry deploy spec — Relay Gateway (public Service: REST + WebSocket hub).

Usage:
    pip install truefoundry
    tfy login --host <your-truefoundry-host>
    export WORKSPACE_FQN=<cluster>:<workspace>     # e.g. tfy-use1:relay
    python deploy/deploy_gateway.py

The gateway is the only service exposed publicly — the frontend (Vercel) calls
its URL. Secrets are referenced as tfy-secret://<group>/<key> (create them once
in the TrueFoundry UI under Secrets, group "relay").
"""
import os

from truefoundry.deploy import (
    Build,
    DockerFileBuild,
    Service,
    Port,
    Resources,
)

WORKSPACE_FQN = os.environ["WORKSPACE_FQN"]
HOST = os.environ.get("GATEWAY_HOST", "relay-gateway.<your-tfy-domain>")

service = Service(
    name="relay-gateway",
    image=Build(
        build_spec=DockerFileBuild(
            dockerfile_path="./Dockerfile",
            build_context_path="./",
            command="uvicorn app.gateway:app --host 0.0.0.0 --port 8000",
        )
    ),
    ports=[
        # Public ingress + TLS. WebSockets ride this same host/port.
        Port(port=8000, host=HOST, path="/")
    ],
    resources=Resources(
        cpu_request=0.5,
        cpu_limit=1,
        memory_request=512,
        memory_limit=1024,
    ),
    env={
        "DATABASE_URL": "tfy-secret://relay/database-url",
        "RETRIEVAL_URL": "http://relay-retrieval:8001",  # intra-cluster service DNS
        "ANTHROPIC_API_KEY": "tfy-secret://relay/anthropic-api-key",
        "ANTHROPIC_MODEL": "claude-opus-4-8",
        "MOSS_API_KEY": "tfy-secret://relay/moss-api-key",
        "MOSS_ENDPOINT": "tfy-secret://relay/moss-endpoint",
        "LIVEKIT_URL": "tfy-secret://relay/livekit-url",
        "LIVEKIT_API_KEY": "tfy-secret://relay/livekit-api-key",
        "LIVEKIT_API_SECRET": "tfy-secret://relay/livekit-api-secret",
        "UNSILOED_API_KEY": "tfy-secret://relay/unsiloed-api-key",
    },
    replicas=1,
    liveness_probe={"config": {"type": "http", "path": "/healthz", "port": 8000}},
    readiness_probe={"config": {"type": "http", "path": "/readyz", "port": 8000}},
)

service.deploy(workspace_fqn=WORKSPACE_FQN)
