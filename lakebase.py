"""
Lakebase (Databricks-managed Postgres) connection helper.

Connects using a single LAKEBASE_URL (a standard Postgres connection URL,
e.g. postgresql://role:password@host:5432/databricks_postgres?sslmode=require)
pointing at a native Postgres role with a static, non-expiring password.
This keeps setup to a single secret instead of five separate env vars.
"""

import base64
import os
from contextlib import contextmanager

import psycopg2
from databricks.sdk import WorkspaceClient
from psycopg2.extras import RealDictCursor

_w = WorkspaceClient()

_SCOPE = os.environ.get("LAKEBASE_SECRET_SCOPE", "database")
_KEY = os.environ.get("LAKEBASE_SECRET_KEY", "lakebase-url")


def _lakebase_url() -> str:
    """Fetch and decode the Lakebase connection URL from the Databricks secret scope."""
    secret = _w.secrets.get_secret(scope=_SCOPE, key=_KEY)
    return base64.b64decode(secret.value).decode("utf-8")


@contextmanager
def get_connection():
    """Yield a raw psycopg2 connection with a RealDictCursor factory."""
    conn = psycopg2.connect(_lakebase_url(), cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


def run_query(sql: str, params: tuple | dict | None = None) -> list[dict]:
    """Run a read query against Lakebase and return rows as list[dict]."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def run_write(sql: str, params: tuple | dict | None = None) -> int:
    """Run an INSERT/UPDATE/DELETE against Lakebase, return affected row count."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount


def init_weather_tables():
    """Create weather document and embedding tables if they do not exist."""

    sql = """
    CREATE EXTENSION IF NOT EXISTS vector;

    CREATE TABLE IF NOT EXISTS weather_documents (
        id TEXT PRIMARY KEY,
        location TEXT NOT NULL,
        source_type TEXT NOT NULL,
        headline TEXT,
        narrative_text TEXT NOT NULL,
        issued_at TIMESTAMPTZ,
        effective_at TIMESTAMPTZ,
        payload JSONB,
        synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS weather_embeddings (
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL
            REFERENCES weather_documents(id)
            ON DELETE CASCADE,
        chunk_index INTEGER NOT NULL,
        chunk_text TEXT NOT NULL,
        embedding vector(384) NOT NULL,
        model_name TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

        UNIQUE (document_id, chunk_index)
    );

    -- Skip vector index for now - ivfflat needs more data (1000+ vectors)
    -- Will use sequential scan which is fine for small datasets
    -- CREATE INDEX IF NOT EXISTS idx_weather_embeddings_vector
    -- ON weather_embeddings USING ivfflat (embedding vector_cosine_ops)
    -- WITH (lists = 100);
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()