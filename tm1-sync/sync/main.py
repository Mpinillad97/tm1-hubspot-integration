import argparse
import time

from dotenv import load_dotenv

from db.locks import release_lock, try_acquire_lock
from db.migrate import migrate
from db.events_repo import backfill_order_events_from_outbox
from db.repo import count_outbox_by_status, reclaim_stale_processing_rows
from sync.config import load_config
from sync.enqueue import enqueue_from_normalized_batch
from sync.events_fetch import fetch_events_once
from sync.ingest import ingest_orders_once
from sync.logging import log, new_run_id, set_run_context
from sync.normalize import normalize_once
from sync.publisher import publish_once


def _ms(start: float, end: float) -> int:
    return int((end - start) * 1000)


def run_once() -> None:
    cfg = load_config()
    run_id = new_run_id()
    set_run_context(run_id=run_id, client_id=cfg.client_id)

    if not cfg.sync_enabled:
        log("run.skipped_disabled", level="INFO")
        return

    lock_name = f"tm1_sync:{cfg.client_id}"
    if not try_acquire_lock(lock_name):
        log("run.skipped_lock_not_acquired", level="WARN", lock_name=lock_name)
        return

    t0 = time.perf_counter()
    log(
        "run.start",
        level="INFO",
        portal_id=cfg.hubspot_portal_id,
        publish_batch_size=cfg.publish_batch_size,
        normalize_batch_size=cfg.normalize_batch_size,
        rate_limit_max=getattr(cfg, "hubspot_rate_limit_max", None),
        rate_limit_window_ms=getattr(cfg, "hubspot_rate_limit_window_ms", None),
        discovery_rps_limit=getattr(cfg, "discovery_rps_limit", None),
        discovery_max_per_run=getattr(cfg, "discovery_max_per_run", None),
        event_cache_ttl_hours=getattr(cfg, "event_cache_ttl_hours", None),
        discovery_view_internal=getattr(cfg, "discovery_view_internal", None),
    )

    try:
        q_before = count_outbox_by_status(cfg.client_id)
        log("queue.depth.before", level="INFO", by_status=dict(q_before))
    except Exception:
        log("queue.depth.before.error", level="WARN")

    try:
        migrate()

        reclaimed = reclaim_stale_processing_rows(cfg.client_id, older_than_minutes=30)
        log(
            "outbox.processing.reclaimed",
            level="INFO",
            reclaimed=reclaimed,
            older_than_minutes=30,
        )

        # Ingest
        t_ing0 = time.perf_counter()
        ingest_counts = ingest_orders_once(
            client_id=cfg.client_id,
            default_since=cfg.default_since,
            tm1_base_url=cfg.tm1_base_url,
            tm1_api_key=cfg.tm1_api_key,
            page_size=cfg.page_size,
            timeout_seconds=cfg.tm1_timeout_seconds,
            max_retries=cfg.tm1_max_retries,
        )
        t_ing1 = time.perf_counter()

        # Normalize
        t_norm0 = time.perf_counter()
        norm_counts, normalized_items = normalize_once(cfg.client_id, batch_size=cfg.normalize_batch_size)
        t_norm1 = time.perf_counter()

        # Backfill: ensure pending event outbox entries are in tm_order_events
        backfill_order_events_from_outbox(client_id=cfg.client_id)

        # Events Fetch (Discovery -> tm_events cache)
        t_evt0 = time.perf_counter()
        evt_counts = fetch_events_once(
            client_id=cfg.client_id,
            discovery_base_url=cfg.discovery_base_url,
            discovery_api_key=cfg.discovery_api_key,
            cache_ttl_hours=cfg.event_cache_ttl_hours,
            max_per_run=cfg.discovery_max_per_run,
            view_internal=cfg.discovery_view_internal,
            timeout_seconds=cfg.discovery_timeout_seconds,
            max_retries=cfg.discovery_max_retries,
            rps_limit=cfg.discovery_rps_limit,
        )
        t_evt1 = time.perf_counter()

        # Enqueue
        t_enq0 = time.perf_counter()
        enq_counts = enqueue_from_normalized_batch(client_id=cfg.client_id, normalized_items=normalized_items)
        t_enq1 = time.perf_counter()

        # Publish
        t_pub0 = time.perf_counter()
        pub_counts = publish_once(
            client_id=cfg.client_id,
            portal_id=cfg.hubspot_portal_id,
            hubspot_base_url=cfg.hubspot_base_url,
            hubspot_client_id=cfg.hubspot_client_id,
            hubspot_client_secret=cfg.hubspot_client_secret,
            order_object_type=cfg.hubspot_order_object_type,
            assoc_type_id=cfg.orders_to_contacts_assoc_type_id,
            batch_size=cfg.publish_batch_size,
            rate_limit_max=getattr(cfg, "hubspot_rate_limit_max", 90),
            rate_limit_window_ms=getattr(cfg, "hubspot_rate_limit_window_ms", 10000),
        )
        t_pub1 = time.perf_counter()

        try:
            q_after = count_outbox_by_status(cfg.client_id)
            log("queue.depth.after", level="INFO", by_status=dict(q_after))
        except Exception:
            log("queue.depth.after.error", level="WARN")

        t1 = time.perf_counter()
        log(
            "run.done",
            level="INFO",
            durations_ms={
                "ingest": _ms(t_ing0, t_ing1),
                "normalize": _ms(t_norm0, t_norm1),
                "events_fetch": _ms(t_evt0, t_evt1),
                "enqueue": _ms(t_enq0, t_enq1),
                "publish": _ms(t_pub0, t_pub1),
                "total": _ms(t0, t1),
            },
            ingest=ingest_counts,
            normalize=norm_counts,
            events_fetch=evt_counts,
            enqueue=enq_counts,
            publish=pub_counts,
        )

    except Exception as e:
        log("run.error", level="ERROR", err=type(e).__name__)
        raise
    finally:
        release_lock(lock_name)


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="TM1 -> DO -> HubSpot Sync Job")
    parser.add_argument("--once", action="store_true", help="Run one iteration and exit")
    args = parser.parse_args()

    if args.once:
        run_once()
        return

    while True:
        run_once()
        cfg = load_config()
        log("run.sleep", level="INFO", seconds=cfg.sleep_seconds)
        time.sleep(cfg.sleep_seconds)


if __name__ == "__main__":
    main()