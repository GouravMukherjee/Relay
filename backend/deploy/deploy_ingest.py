"""TrueFoundry deploy spec — Relay ingestion worker (arq, always-on, NO inbound port).

Consumes the arq queue (Redis): on an enqueued document it fetches the raw file from S3,
parses with Unsiloed, chunks, embeds (via the TrueFoundry gateway), writes chunks + indexes
to Moss/pgvector, and flips the document status to ready. Idempotent by document_id.

    cd backend && python deploy/deploy_ingest.py
"""
import os

from truefoundry.deploy import Build, DockerFileBuild, Service, Resources

WORKSPACE_FQN = os.environ["WORKSPACE_FQN"]

service = Service(
    name="relay-ingest",
    image=Build(
        build_spec=DockerFileBuild(
            dockerfile_path="./Dockerfile",
            build_context_path="./",
            command="arq relay.ingestion.worker.WorkerSettings",
        )
    ),
    ports=[],  # outbound-only worker — consumes Redis, no inbound traffic
    resources=Resources(
        cpu_request=0.5,
        cpu_limit=1,
        memory_request=512,
        memory_limit=1024,
    ),
    env={
        "DATABASE_URL": "tfy-secret://relay/database-url",
        "REDIS_URL": "tfy-secret://relay/redis-url",
        "UNSILOED_API_KEY": "tfy-secret://relay/unsiloed-api-key",
        "TFY_API_KEY": "tfy-secret://relay/tfy-api-key",
        "TFY_GATEWAY_URL": "tfy-secret://relay/tfy-gateway-url",
        "MOSS_API_KEY": "tfy-secret://relay/moss-api-key",
        "MOSS_BASE_URL": "tfy-secret://relay/moss-base-url",
        "AWS_ACCESS_KEY_ID": "tfy-secret://relay/aws-access-key-id",
        "AWS_SECRET_ACCESS_KEY": "tfy-secret://relay/aws-secret-access-key",
        "AWS_REGION": "us-east-1",
        "S3_BUCKET": "tfy-secret://relay/s3-bucket",
        "DEFAULT_ORG_ID": "tfy-secret://relay/default-org-id",
    },
    replicas=1,
)

service.deploy(workspace_fqn=WORKSPACE_FQN)
