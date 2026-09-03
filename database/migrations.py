"""
database/migrations.py
Auto-run schema.sql on startup.
"""

import logging
from pathlib import Path
from database.connection import get_db

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


async def run_migrations() -> None:
    """Execute schema.sql to create tables if they don't exist."""
    db = await get_db()
    schema = SCHEMA_PATH.read_text(encoding="utf-8")

    # Split by semicolon to execute statements individually
    statements = [s.strip() for s in schema.split(";") if s.strip()]
    for stmt in statements:
        await db.execute(stmt)

    await db.commit()
    logger.info("Database migrations applied successfully.")
