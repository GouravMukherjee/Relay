"""TrueFoundry deploy spec — Relay Gateway (public Service: REST + WebSocket hub).

Usage:
    pip install truefoundry
    tfy login --host <your-truefoundry-host>
    export WORKSPACE_FQN=<cluster>:<workspace>          # e.g. tfy-use1:relay
    export GATEWAY_HOST=relay-gateway.<your-tfy-domain>  # public ingress host
    cd backend && python deploy/deploy_gateway.py

This is the ONLY service exposed publicly — the Vercel frontend calls its URL over
HTTPS (REST) and WSS (WebSocket ride the same host/port). `backend/relay` is a monolith:
retrieval + orchestrator run IN-PROCESS here, so there is no separate retrieval service
and no RETRIEVAL_URL.

Secrets are referenced as tfy-secret://<group>/<key>. Create them once in the TrueFoundry
UI under Secrets, group "relay" (see deploy/README.md for the full list).
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
# The deployed frontend origin — drives CORS + WS origin checks in relay.gateway.app.
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "https://relay.vercel.app")

service = Service(
    name="relay-gateway",
    image=Build(
        build_spec=DockerFileBuild(
            dockerfile_path="./Dockerfile",
            build_context_path="./",  # run this script from the backend/ directory
            command="uvicorn relay.gateway.app:app --host 0.0.0.0 --port 8000",
        )
    ),
    ports=[
        # Public ingress + TLS. WebSockets (/ws/sessions/{id}) ride this same host/port.
        Port(port=8000, host=HOST, path="/")
    ],
    resources=Resources(
        cpu_request=0.5,
        cpu_limit=1,
        memory_request=512,
        memory_limit=1024,
    ),
    # Env keys MUST match relay.config.Settings field env names. Pooled DSN for the app
    # (port 6543); the migrate job uses the direct DSN. No RETRIEVAL_URL (in-process).
    env={
        "DATABASE_URL": "tfy-secret://relay/database-url",
        "REDIS_URL": "tfy-secret://relay/redis-url",
        "FRONTEND_ORIGIN": FRONTEND_ORIGIN,
        "SUPABASE_URL": "tfy-secret://relay/supabase-url",
        "SUPABASE_ANON_KEY": "tfy-secret://relay/supabase-anon-key",
        "SUPABASE_SERVICE_KEY": "tfy-secret://relay/supabase-service-key",
        "MOSS_API_KEY": "tfy-secret://relay/moss-api-key",
        "MOSS_BASE_URL": "tfy-secret://relay/moss-base-url",
        "TFY_API_KEY": "tfy-secret://relay/tfy-api-key",
        "TFY_GATEWAY_URL": "tfy-secret://relay/tfy-gateway-url",
        "ANTHROPIC_API_KEY": "tfy-secret://relay/anthropic-api-key",
        "QWEN_API_KEY": "tfy-secret://relay/qwen-api-key",
        "MINIMAX_API_KEY": "tfy-secret://relay/minimax-api-key",
        "LLM_MODEL": "claude",
        "UNSILOED_API_KEY": "tfy-secret://relay/unsiloed-api-key",
        "LIVEKIT_URL": "tfy-secret://relay/livekit-url",
        "LIVEKIT_API_KEY": "tfy-secret://relay/livekit-api-key",
        "LIVEKIT_API_SECRET": "tfy-secret://relay/livekit-api-secret",
        "AWS_ACCESS_KEY_ID": "tfy-secret://relay/aws-access-key-id",
        "AWS_SECRET_ACCESS_KEY": "tfy-secret://relay/aws-secret-access-key",
        "AWS_REGION": "us-east-1",
        "S3_BUCKET": "tfy-secret://relay/s3-bucket",
        "SLACK_WEBHOOK_URL": "tfy-secret://relay/slack-webhook-url",
        "DEFAULT_ORG_ID": "tfy-secret://relay/default-org-id",
    },
    replicas=1,
    # relay.gateway.app exposes GET /health (not /healthz|/readyz).
    liveness_probe={"config": {"type": "http", "path": "/health", "port": 8000}},
    readiness_probe={"config": {"type": "http", "path": "/health", "port": 8000}},
)

service.deploy(workspace_fqn=WORKSPACE_FQN)
