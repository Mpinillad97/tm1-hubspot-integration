from pathlib import Path
from typing import Optional

from db.connection import get_conn, ping_db


def _schema_path() -> Path:
    return Path(__file__).resolve().parent / "sql" / "schema.sql"


def _read_schema_sql(schema_path: Optional[str] = None) -> str:
    p = Path(schema_path).resolve() if schema_path else _schema_path()
    if not p.exists():
        raise FileNotFoundError(
            f"schema.sql not found at: {p}. Expected exactly: tm1-sync/db/sql/schema.sql"
        )
    return p.read_text(encoding="utf-8")


def migrate(schema_path: Optional[str] = None) -> None:
    """
    Apply idempotent schema.sql to Postgres.
    """
    ping_db()
    sql = _read_schema_sql(schema_path)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)


if __name__ == "__main__":
    migrate()
    print("✅ Migration applied successfully.")