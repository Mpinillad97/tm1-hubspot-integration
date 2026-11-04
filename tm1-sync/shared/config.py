import os
from dataclasses import dataclass
from typing import Optional


def require_env(name: str) -> str:
    v = os.getenv(name)
    if not v or not v.strip():
        raise RuntimeError(f"Missing required env var: {name}")
    return v.strip()


def opt_env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    return v if v else default


@dataclass(frozen=True)
class SyncConfig:
    portal_id: str
    tm1_api_key: str
    tm1_base_url: str
    database_url: str
    page_size: int

    @staticmethod
    def from_env() -> "SyncConfig":
        portal_id = require_env("PORTAL_ID")
        tm1_api_key = require_env("TM1_API_KEY")
        tm1_base_url = opt_env("TM1_BASE_URL", "https://app.ticketmaster.com/tm1-reports-api/v1")
        database_url = require_env("DATABASE_URL")

        page_size_raw = opt_env("PAGE_SIZE", "200")
        try:
            page_size = int(page_size_raw)
        except Exception:
            page_size = 200

        return SyncConfig(
            portal_id=portal_id,
            tm1_api_key=tm1_api_key,
            tm1_base_url=tm1_base_url,
            database_url=database_url,
            page_size=page_size,
        )