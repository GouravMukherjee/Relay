"""Route modules for the Relay REST API.

Each sub-module exposes a ``router`` (FastAPI ``APIRouter``) that is included by
``relay.gateway.app.create_app()`` under the ``/api/v1`` prefix.
"""
