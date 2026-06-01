#!/usr/bin/env python3
"""Seed script to create an Ontology Demo spec for testing ontology lookup.

Run with: uv run python scripts/seed_ontology_demo.py
"""

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from metaseed_hub.config import get_settings
from metaseed_hub.models import SpecDraft, Tenant, User

ONTOLOGY_DEMO_SPEC = {
    "name": "ontology-demo",
    "version": "0.1",
    "display_name": "Ontology Demo",
    "description": "Demonstrates ontology term fields with autocomplete lookup from OLS4.",
    "root_entity": "Sample",
    "ontology": None,
    "validation_rules": [],
    "entities": {
        "Sample": {
            "description": "A biological or chemical sample with ontology-annotated properties.",
            "fields": [
                {
                    "name": "sample_id",
                    "type": "string",
                    "required": True,
                    "description": "Unique identifier for the sample.",
                },
                {
                    "name": "name",
                    "type": "string",
                    "required": True,
                    "description": "Human-readable name for the sample.",
                },
                {
                    "name": "organism",
                    "type": "ontology_term",
                    "required": True,
                    "description": "The organism this sample comes from (NCBI Taxonomy).",
                    "ontology_term": "ncbitaxon",
                },
                {
                    "name": "tissue",
                    "type": "ontology_term",
                    "required": False,
                    "description": "Plant tissue or organ type (Plant Ontology).",
                    "ontology_term": "po",
                },
                {
                    "name": "chemical_treatment",
                    "type": "ontology_term",
                    "required": False,
                    "description": "Chemical compound used in treatment (ChEBI).",
                    "ontology_term": "chebi",
                },
                {
                    "name": "environment",
                    "type": "ontology_term",
                    "required": False,
                    "description": "Environmental conditions (ENVO).",
                    "ontology_term": "envo",
                },
                {
                    "name": "phenotype",
                    "type": "ontology_term",
                    "required": False,
                    "description": "Observable phenotypic quality (PATO).",
                    "ontology_term": "pato",
                },
                {
                    "name": "measurement_unit",
                    "type": "ontology_term",
                    "required": False,
                    "description": "Unit of measurement (Unit Ontology).",
                    "ontology_term": "uo",
                },
                {
                    "name": "any_ontology_term",
                    "type": "ontology_term",
                    "required": False,
                    "description": "Any ontology term (no filter - searches all ontologies).",
                },
                {
                    "name": "notes",
                    "type": "string",
                    "required": False,
                    "description": "Additional notes about the sample.",
                },
            ],
        },
    },
}


async def seed_ontology_demo() -> None:
    """Create the ontology demo spec in the database."""
    settings = get_settings()

    # Create async engine
    db_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(db_url)
    async_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session_factory() as session:
        # Get the first tenant (or create one)
        result = await session.execute(select(Tenant).limit(1))
        tenant = result.scalar_one_or_none()

        if not tenant:
            print("No tenant found. Please log in first to create a tenant.")
            return

        # Get the first user in this tenant
        result = await session.execute(select(User).where(User.tenant_id == tenant.id).limit(1))
        user = result.scalar_one_or_none()

        if not user:
            print("No user found. Please log in first.")
            return

        # Check if spec already exists
        result = await session.execute(
            select(SpecDraft).where(
                SpecDraft.tenant_id == tenant.id,
                SpecDraft.name == "ontology-demo",
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing spec
            existing.spec_data = {"spec": ONTOLOGY_DEMO_SPEC}
            print(f"Updated existing ontology-demo spec (id: {existing.id})")
        else:
            # Create new spec
            spec_draft = SpecDraft(
                id=str(uuid.uuid4()),
                tenant_id=tenant.id,
                user_id=user.id,
                name="ontology-demo",
                version="0.1",
                spec_data={"spec": ONTOLOGY_DEMO_SPEC},
            )
            session.add(spec_draft)
            print(f"Created new ontology-demo spec (id: {spec_draft.id})")

        await session.commit()
        print("Done! The ontology-demo spec is now available in the Spec Builder.")


if __name__ == "__main__":
    asyncio.run(seed_ontology_demo())
