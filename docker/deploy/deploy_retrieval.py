"""TrueFoundry deploy spec — Relay Retrieval (internal Service, port 8001).

Not publicly exposed: only the gateway and worker call it, by the in-cluster
DNS name `relay-retrieval`. Keep it in the SAME workspace/region as the gateway
and worker so calls are intra-cluster (protects the <10ms budget, ADR-001).

    python deploy/deploy_retrieval.py
"""
import os

from truefoundry.deploy import Build, DockerFileBuild, Service, Port, Resources

WORKSPACE_FQN = os.environ["WORKSPACE_FQN"]

service = Service(
    name="relay-retrieval",
    image=Build(
        build_spec=DockerFileBuild(
            dockerfile_path="./Dockerfile",
            build_context_path="./",
            command="uvicorn app.retrieval:app --host 0.0.0.0 --port 8001",
        )
    ),
    # No `host` => internal-only ClusterIP service, reachable as relay-retrieval:8001.
    ports=[Port(port=8001, expose=False)],
    resources=Resources(
        cpu_request=0.5,
        cpu_limit=1,
        memory_request=512,
        memory_limit=1024,
    ),
    env={
        "DATABASE_URL": "tfy-secret://relay/database-url",
        "MOSS_API_KEY": "tfy-secret://relay/moss-api-key",
        "MOSS_ENDPOINT": "tfy-secret://relay/moss-endpoint",
    },
    replicas=1,
    liveness_probe={"config": {"type": "http", "path": "/healthz", "port": 8001}},
    readiness_probe={"config": {"type": "http", "path": "/readyz", "port": 8001}},
)

service.deploy(workspace_fqn=WORKSPACE_FQN)
