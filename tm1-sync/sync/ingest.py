from typing import Any, Dict

from db.repo import build_unique_order_id, get_last_since, insert_order_raw, set_last_since
from sync.logging import log, log_item
from sync.tm1_client import extract_orders, parse_max_updated_since, reached_last_page, tm1_get_orders


def ingest_orders_once(
    *,
    client_id: str,
    default_since: str,
    tm1_base_url: str,
    tm1_api_key: str,
    page_size: int,
    timeout_seconds: int,
    max_retries: int,
) -> Dict[str, int]:
    entity = "orders"
    since = get_last_since(client_id, entity) or default_since

    counts = {
        "pages": 0,
        "orders_fetched": 0,
        "raw_inserted": 0,
        "errors_tm1": 0,
    }

    page = 0
    max_updated_since = since

    log("ingest.start", client_id=client_id, entity=entity, since=since, page_size=page_size)

    try:
        while True:
            payload = tm1_get_orders(
                tm1_base_url=tm1_base_url,
                api_key=tm1_api_key,
                since=since,
                page=page,
                size=page_size,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )

            orders = extract_orders(payload)
            n = len(orders)

            counts["pages"] += 1
            counts["orders_fetched"] += n

            log("ingest.page", level="DEBUG", client_id=client_id, entity=entity, page=page, n=n)

            if n == 0:
                log("ingest.page_empty", client_id=client_id, entity=entity, page=page)
                break

            max_updated_since = parse_max_updated_since(orders, max_updated_since)

            for idx, order in enumerate(orders):
                tm1_order_id_raw = str(order.get("id", "")).strip()
                creation_iso = order.get("creationDateTime") if isinstance(order.get("creationDateTime"), str) else ""
                tm1_order_id = build_unique_order_id(tm1_order_id_raw, creation_iso)

                if not tm1_order_id:
                    log_item(
                        "ingest.item.skipped_missing_tm1_order_id",
                        entity=entity,
                        page=page,
                        idx=idx,
                    )
                    continue

                # Insert RAW (audit)
                insert_order_raw(client_id, tm1_order_id, order)
                counts["raw_inserted"] += 1

                # Very verbose trace per item (sampled)
                log_item(
                    "ingest.item.inserted_raw",
                    entity=entity,
                    page=page,
                    idx=idx,
                    tm1_order_id=tm1_order_id,
                )

            if reached_last_page(payload, page):
                log("ingest.reached_last_page", client_id=client_id, entity=entity, page=page)
                break

            page += 1

        # Only advance state if everything succeeded
        if isinstance(max_updated_since, str) and max_updated_since >= since:
            set_last_since(client_id, entity, max_updated_since)

        log(
            "ingest.done",
            client_id=client_id,
            entity=entity,
            since=since,
            next_since=max_updated_since,
            pages=counts["pages"],
            orders_fetched=counts["orders_fetched"],
            raw_inserted=counts["raw_inserted"],
        )
        return counts

    except Exception as e:
        counts["errors_tm1"] += 1
        log("ingest.error", level="ERROR", client_id=client_id, entity=entity, since=since, err=type(e).__name__)
        # Important: we do NOT set_last_since here.
        return counts