"""TrueFoundry deploy spec — Relay DB migration (one-off Job).

Runs `alembic upgrade head` to provision the schema on Supabase Postgres: the vector
extension, all tables, ivfflat indexes, the `relay_app` role, and the org_isolation RLS
policies. Trigger it manually from the TrueFoundry UI (or `tfy ... trigger`) after the
services are built.

    cd backend && python deploy/deploy_migrate.py     # registers the job
    # then trigger the job once from the TrueFoundry UI.

IMPORTANT: migrations use the DIRECT Supabase connection (port 5432), NOT the pooled
pgBouncer DSN (6543) the app uses. Store it as a separate secret `database-url-direct`.
"""
import os

from truefoundry.deploy import Build, DockerFileBuild, Job, Resources

WORKSPACE_FQN = os.environ["WORKSPACE_FQN"]

job = Job(
    name="relay-migrate",
    image=Build(
        build_spec=DockerFileBuild(
            dockerfile_path="./Dockerfile",
            build_context_path="./",
            command="alembic -c alembic.ini upgrade head",
        )
    ),
    resources=Resources(
        cpu_request=0.25,
        cpu_limit=0.5,
        memory_request=256,
        memory_limit=512,
    ),
    env={
        # Direct (5432) connection for DDL — NOT the pooled app DSN.
        "DATABASE_URL": "tfy-secret://relay/database-url-direct",
    },
    # Manual trigger; do not retry destructive DDL automatically.
    # TODO: confirm TrueFoundry Job trigger/retry field names for your SDK version.
    retries=0,
)

job.deploy(workspace_fqn=WORKSPACE_FQN)
