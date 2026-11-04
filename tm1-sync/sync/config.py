import os
from dataclasses import dataclass


def _require_env(name: str) -> str:
    v = os.getenv(name)
    if not v or not v.strip():
        raise RuntimeError(f"Missing required env var: {name}")
    return v.strip()


def _get_env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v.strip() if v and v.strip() else default


def _get_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if not v or not v.strip():
        return default
    return int(v.strip())


def _get_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if not v or not v.strip():
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass(frozen=True)
class SyncConfig:
    # DB
    database_url: str

    # TM1
    tm1_api_key: str
    tm1_base_url: str

    # runtime
    client_id: str
    page_size: int
    sleep_seconds: int
    sync_enabled: bool

    # state defaults
    default_since: str

    # retry basics
    tm1_timeout_seconds: int
    tm1_max_retries: int

    # normalize / publish sizes
    normalize_batch_size: int
    publish_batch_size: int

    # HubSpot OAuth (Projects)
    hubspot_base_url: str
    hubspot_portal_id: str
    hubspot_client_id: str
    hubspot_client_secret: str

    # HubSpot object naming / association
    hubspot_order_object_type: str
    orders_to_contacts_assoc_type_id: int

    # HubSpot rate limiting
    hubspot_rate_limit_max: int
    hubspot_rate_limit_window_ms: int

    # Discovery API (events)
    discovery_base_url: str
    discovery_api_key: str
    discovery_view_internal: bool
    discovery_timeout_seconds: int
    discovery_max_retries: int
    discovery_rps_limit: int

    # Event cache behavior
    event_cache_ttl_hours: int
    discovery_max_per_run: int


def load_config() -> SyncConfig:
    return SyncConfig(
        database_url=_require_env("DATABASE_URL"),

        tm1_api_key=_require_env("TM1_API_KEY"),
        tm1_base_url=_get_env("TM1_BASE_URL", "https://app.ticketmaster.com/tm1-reports-api/v1"),

        client_id=_require_env("CLIENT_ID"),
        page_size=_get_int("PAGE_SIZE", 200),
        sleep_seconds=_get_int("SLEEP_SECONDS", 300),
        sync_enabled=_get_bool("SYNC_ENABLED", True),

        default_since=_get_env("DEFAULT_SINCE", "2026-01-01T00:00:00Z"),

        tm1_timeout_seconds=_get_int("TM1_TIMEOUT_SECONDS", 45),
        tm1_max_retries=_get_int("TM1_MAX_RETRIES", 4),

        normalize_batch_size=_get_int("NORMALIZE_BATCH_SIZE", 2000),
        publish_batch_size=_get_int("PUBLISH_BATCH_SIZE", 100),

        hubspot_base_url=_get_env("HUBSPOT_BASE_URL", "https://api.hubapi.com"),
        hubspot_portal_id=_require_env("HUBSPOT_PORTAL_ID"),
        hubspot_client_id=_require_env("HUBSPOT_CLIENT_ID"),
        hubspot_client_secret=_require_env("HUBSPOT_CLIENT_SECRET"),

        hubspot_order_object_type=_get_env("HUBSPOT_ORDER_OBJECT_TYPE", "orders"),
        orders_to_contacts_assoc_type_id=_get_int("ORDERS_TO_CONTACTS_ASSOC_TYPE_ID", 507),

        hubspot_rate_limit_max=_get_int("HUBSPOT_RATE_LIMIT_MAX", 90),
        hubspot_rate_limit_window_ms=_get_int("HUBSPOT_RATE_LIMIT_WINDOW_MS", 10000),

        discovery_base_url=_get_env("DISCOVERY_BASE_URL", "https://app.ticketmaster.com/discovery/v2"),
        discovery_api_key=_require_env("DISCOVERY_API_KEY"),
        discovery_view_internal=_get_bool("DISCOVERY_VIEW_INTERNAL", False),
        discovery_timeout_seconds=_get_int("DISCOVERY_TIMEOUT_SECONDS", 20),
        discovery_max_retries=_get_int("DISCOVERY_MAX_RETRIES", 4),
        discovery_rps_limit=_get_int("DISCOVERY_RPS_LIMIT", 5),

        event_cache_ttl_hours=_get_int("EVENT_CACHE_TTL_HOURS", 24),

        # IMPORTANT: stay under 5000/day. With a 15-min cron = 96 runs/day.
        # 5000/96 ≈ 52. Use 50 safe default.
        discovery_max_per_run=_get_int("DISCOVERY_MAX_PER_RUN", 50),
    )