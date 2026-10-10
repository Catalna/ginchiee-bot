"""
database/migrations.py
Versioned database migrations.

Migration files live in database/migrations/ and must be named NNN_description.sql
(0001_initial.sql, 0002_add_water_tracking.sql, ...). They are applied in version
order exactly once, and every applied version is recorded in the
schema_migrations table so the process is safe to run on every boot.

Unlike the previous "run schema.sql with CREATE TABLE IF NOT EXISTS" approach,
this supports real schema evolution: ALTER TABLE, new constraints, backfills,
data fixes, etc.
"""

import hashlib
import logging
import re
from pathlib import Path

from database.connection import get_db

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
MIGRATION_TIMEOUT_SECONDS = 300


def split_sql_statements(sql: str) -> list[str]:
    """Split a migration file into individual statements.

    Understands single-quoted string literals (with '' escapes),
    double-quoted identifiers, line comments (--) and dollar-quoted bodies
    ($tag$ ... $tag$) so future migrations can contain PL/pgSQL functions.
    """
    statements: list[str] = []
    buffer: list[str] = []
    index = 0
    length = len(sql)
    dollar_tag: str | None = None

    while index < length:
        char = sql[index]

        # Inside a dollar-quoted body, copy verbatim until the closing tag.
        if dollar_tag is not None:
            closing = sql.find(dollar_tag, index)
            if closing == -1:
                buffer.append(sql[index:])
                index = length
                continue
            buffer.append(sql[index:closing + len(dollar_tag)])
            index = closing + len(dollar_tag)
            dollar_tag = None
            continue

        # Comments: skip to end of line.
        if char == "-" and index + 1 < length and sql[index + 1] == "-":
            newline = sql.find("\n", index)
            index = length if newline == -1 else newline
            continue

        # Single-quoted literal.
        if char == "'":
            buffer.append(char)
            index += 1
            while index < length:
                buffer.append(sql[index])
                if sql[index] == "'":
                    if index + 1 < length and sql[index + 1] == "'":
                        buffer.append(sql[index + 1])
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue

        # Double-quoted identifier.
        if char == '"':
            buffer.append(char)
            index += 1
            while index < length:
                buffer.append(sql[index])
                if sql[index] == '"':
                    if index + 1 < length and sql[index + 1] == '"':
                        buffer.append(sql[index + 1])
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue

        # Start of a dollar-quoted body.
        if char == "$":
            match = re.match(r"\$[A-Za-z_0-9]*\$", sql[index:])
            if match:
                dollar_tag = match.group(0)
                buffer.append(dollar_tag)
                index += len(dollar_tag)
                continue

        # Statement terminator.
        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            index += 1
            continue

        buffer.append(char)
        index += 1

    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)

    return statements


def _available_migrations() -> list[tuple[int, str, str, Path]]:
    """Return (version, name, checksum, path) for every migration on disk."""
    migrations = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = re.match(r"^(\d+)_(.+)\.sql$", path.name)
        if not match:
            logger.warning("Skipping migration with invalid filename: %s", path.name)
            continue
        version = int(match.group(1))
        statement = match.group(2)
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        migrations.append((version, statement, checksum, path))
    return migrations


async def run_migrations() -> list[str]:
    """Apply any pending migrations. Returns the names applied this run."""
    db = await get_db()

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT        NOT NULL,
            checksum   TEXT        NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    applied = {
        row["version"]: row["checksum"]
        for row in await db.fetchall("SELECT version, checksum FROM schema_migrations")
    }

    applied_now: list[str] = []

    for version, name, checksum, path in _available_migrations():
        previous = applied.get(version)
        if previous is not None:
            if previous != checksum:
                # Refuse to silently skip a changed migration — that is how you
                # end up with schemas that differ per environment.
                raise RuntimeError(
                    f"Migration {path.name} was modified after it was applied "
                    f"(expected checksum {previous[:12]}, found {checksum[:12]}). "
                    "Add a new migration instead of editing an applied one."
                )
            continue

        logger.info("Applying migration %s ...", path.name)
        statements = split_sql_statements(path.read_text(encoding="utf-8"))

        async with db.transaction() as tx:
            for statement in statements:
                await tx.execute(statement)
            await tx.execute(
                """
                INSERT INTO schema_migrations (version, name, checksum)
                VALUES ($1, $2, $3)
                """,
                (version, name, checksum),
            )

        applied_now.append(path.name)
        logger.info("Applied migration %s (%d statements).", path.name, len(statements))

    if not applied_now:
        logger.info("Database schema is up to date.")
    else:
        logger.info("Applied %d migration(s): %s", len(applied_now), ", ".join(applied_now))

    return applied_now
