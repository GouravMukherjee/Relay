"""Targeted, idempotent demo seed for ONE org — customer + history + lead only.

Unlike relay.seed.northwind (which also seeds documents and uses fixed global IDs that
collide across orgs), this seeds just the Desk customer (Sarah Chen / Acme Corp) with her
ticket history and the Intake lead (Jordan Mraz) into a SPECIFIC org, with fresh per-org
IDs. Safe to re-run: it checks by (org, name) before inserting.

Usage:
    python -m scripts.seed_org_demo <ORG_UUID>
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

from sqlalchemy import and_, select

from relay.db.base import privileged_session
from relay.db.models import Customer, Lead, Memory, Session
from relay.ids import new_id

_MEMORIES = [
    "CRM export sync — resolved (Mar 12 2026). Sarah Chen (Acme Corp, Growth plan) "
    "reported the Salesforce CRM export sync had stopped updating. Root cause: OAuth "
    "token expiry after 90 days. Resolved by re-authenticating the Salesforce integration "
    "from Settings → Integrations. Ticket #1023.",
    "Onboarding — resolved (Feb 2 2026). Initial workspace setup and document ingestion "
    "configured for Acme Corp on the Growth plan. Sarah Chen is the primary contact.",
]


async def seed(org_id: str) -> None:
    async with privileged_session() as s:
        # --- Customer: Sarah Chen / Acme Corp -------------------------------------
        cust = (
            await s.execute(
                select(Customer).where(
                    Customer.organization_id == org_id, Customer.name == "Sarah Chen"
                )
            )
        ).scalar_one_or_none()
        if cust is None:
            cust_id = new_id("cus")
            s.add(
                Customer(
                    id=cust_id,
                    organization_id=org_id,
                    name="Sarah Chen",
                    company="Acme Corp",
                    email="sarah.chen@acme.corp",
                )
            )
            await s.flush()
            print(f"created customer Sarah Chen / Acme Corp -> {cust_id}")
        else:
            cust_id = cust.id
            print(f"customer already present -> {cust_id}")

        # --- Memories (ticket history) --------------------------------------------
        added = 0
        for txt in _MEMORIES:
            dup = (
                await s.execute(
                    select(Memory).where(
                        and_(Memory.customer_id == cust_id, Memory.text == txt)
                    )
                )
            ).scalar_one_or_none()
            if dup is None:
                s.add(
                    Memory(
                        id=new_id("mem"),
                        customer_id=cust_id,
                        organization_id=org_id,
                        kind="ticket",
                        text=txt,
                        embedding=None,  # panel/history doesn't need vectors
                    )
                )
                added += 1
        await s.flush()
        print(f"memories: +{added} (total intended {len(_MEMORIES)})")

        # --- Lead: Jordan Mraz / Brightwave Inc. ----------------------------------
        lead = (
            await s.execute(
                select(Lead).where(
                    Lead.organization_id == org_id, Lead.name == "Jordan Mraz"
                )
            )
        ).scalar_one_or_none()
        if lead is None:
            now = datetime.now(timezone.utc)
            ses_id = new_id("ses")
            s.add(
                Session(
                    id=ses_id,
                    organization_id=org_id,
                    mode="intake",
                    status="ended",
                    started_at=now,
                    ended_at=now,
                )
            )
            await s.flush()
            s.add(
                Lead(
                    id=new_id("lead"),
                    session_id=ses_id,
                    organization_id=org_id,
                    name="Jordan Mraz",
                    company="Brightwave Inc.",
                    email="vp.eng@brightwave.io",
                    qualifiers={
                        "budget": "$40k-$60k/year",
                        "authority": "VP of Engineering",
                        "timeline": "this quarter",
                        "need": "reduce onboarding latency; reps spend too long on technical specs",
                    },
                    score=82,
                    status="hot",
                    routed_to=None,
                )
            )
            print("created lead Jordan Mraz / Brightwave Inc. (score 82, hot)")
        else:
            print("lead Jordan Mraz already present")

    print("done.")


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m scripts.seed_org_demo <ORG_UUID>", file=sys.stderr)
        sys.exit(1)
    asyncio.run(seed(sys.argv[1]))


if __name__ == "__main__":
    main()
