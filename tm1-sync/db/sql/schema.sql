BEGIN;

-- Useful for case-insensitive email later
CREATE EXTENSION IF NOT EXISTS citext;

-- -------------------------
-- sync_state (replaces state.json)
-- -------------------------
CREATE TABLE IF NOT EXISTS sync_state (
  client_id   text NOT NULL,
  entity      text NOT NULL,   -- e.g. 'orders'
  last_since  text NOT NULL,   -- TM1 expects ISO string
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, entity)
);

CREATE TABLE IF NOT EXISTS tm1_orders_raw (
  id            bigserial PRIMARY KEY,
  client_id     text NOT NULL,
  tm1_order_id  text NOT NULL,
  payload       jsonb NOT NULL,
  fetched_at    timestamptz NOT NULL DEFAULT now()
);

-- Marker to avoid re-normalizing the same raw rows
ALTER TABLE tm1_orders_raw
  ADD COLUMN IF NOT EXISTS normalized_at timestamptz;

-- Fast filtering by client + time
CREATE INDEX IF NOT EXISTS idx_tm1_orders_raw_client_fetched
  ON tm1_orders_raw (client_id, fetched_at DESC);

-- Fast filtering for pending normalization
CREATE INDEX IF NOT EXISTS idx_tm1_orders_raw_pending_normalize
  ON tm1_orders_raw (client_id, id)
  WHERE normalized_at IS NULL;

-- Prevent exact duplicates for same fetched_at timestamp
CREATE UNIQUE INDEX IF NOT EXISTS ux_tm1_orders_raw_client_order_fetched
  ON tm1_orders_raw (client_id, tm1_order_id, fetched_at);

-- -------------------------
-- tm1_orders (normalized current state)
-- -------------------------
CREATE TABLE IF NOT EXISTS tm1_orders (
  id           bigserial PRIMARY KEY,
  client_id    text NOT NULL,
  tm1_order_id text NOT NULL,  -- "{id}|{creationDateTime}"
  payload      jsonb NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (client_id, tm1_order_id)
);

CREATE INDEX IF NOT EXISTS idx_tm1_orders_client_updated
  ON tm1_orders (client_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS tm1_customers (
  id               bigserial PRIMARY KEY,
  client_id        text NOT NULL,
  email            citext NOT NULL,
  email_normalized text NOT NULL,
  payload          jsonb NOT NULL,
  first_seen_at    timestamptz NOT NULL DEFAULT now(),
  last_seen_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (client_id, email)
);

CREATE INDEX IF NOT EXISTS idx_tm1_customers_client_last_seen
  ON tm1_customers (client_id, last_seen_at DESC);

-- Status enum (idempotent)
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'outbox_status') THEN
    CREATE TYPE outbox_status AS ENUM ('pending','processing','sent','failed');
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS hubspot_outbox (
  id                bigserial PRIMARY KEY,
  client_id         text NOT NULL,
  entity            text NOT NULL,   -- 'contact' | 'order' | 'association' | 'event' | 'order_event_association' | 'seat' | 'order_seat_association' | 'event_seat_association' | 'contact_seat_association'
  dedupe_key        text NOT NULL,   -- email_norm | tm1_order_id | "order->email" | tm_event_id | "order->event" | seat_unique_id | "order->seat" | "seat->event" | "seat->email"
  payload           jsonb NOT NULL,

  status            outbox_status NOT NULL DEFAULT 'pending',
  attempts          int NOT NULL DEFAULT 0,
  next_retry_at     timestamptz NOT NULL DEFAULT now(),
  last_error        text NULL,
  hubspot_object_id text NULL,

  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

-- Read queue fast: pending ready to run
CREATE INDEX IF NOT EXISTS idx_outbox_pending_ready
  ON hubspot_outbox (status, next_retry_at);

-- Useful for troubleshooting per client/entity
CREATE INDEX IF NOT EXISTS idx_outbox_client_entity_created
  ON hubspot_outbox (client_id, entity, created_at DESC);

-- Anti-duplicate messages while still in-flight
CREATE UNIQUE INDEX IF NOT EXISTS ux_outbox_inflight_dedupe
  ON hubspot_outbox (client_id, entity, dedupe_key)
  WHERE status IN ('pending','processing');

-- -------------------------
-- tm_order_events (1 event per order + detector list)
-- -------------------------
CREATE TABLE IF NOT EXISTS tm_order_events (
  client_id         text NOT NULL,
  tm1_order_id      text NOT NULL,
  primary_event_id  text,
  event_ids         jsonb NOT NULL DEFAULT '[]'::jsonb,
  distinct_count    integer NOT NULL DEFAULT 0,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, tm1_order_id)
);

CREATE INDEX IF NOT EXISTS idx_tm_order_events_client_primary
  ON tm_order_events (client_id, primary_event_id);

-- -------------------------
-- tm_events (Discovery cache)
-- -------------------------
CREATE TABLE IF NOT EXISTS tm_events (
  client_id      text NOT NULL,
  tm_event_id    text NOT NULL,
  payload        jsonb,
  name           text,
  start_datetime timestamptz,
  venue_name     text,
  city           text,
  state          text,
  product_type   text,
  fetched_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, tm_event_id)
);

-- Add newer columns safely for already-existing tm_events tables
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS event_id text;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS event_type text;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS locale text;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS event_url text;

ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS local_date text;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS local_time text;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS timezone text;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS status_code text;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS sales_public_start_datetime timestamptz;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS sales_public_end_datetime timestamptz;

ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS venue_id text;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS venue_ids jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS country_code text;

ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS attraction_ids jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS attraction_names jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS subsystem_id text;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS subsystem_name text;

ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS please_note text;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS info text;

-- keep old/base columns safe too
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS payload jsonb;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS name text;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS start_datetime timestamptz;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS venue_name text;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS city text;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS state text;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS product_type text;
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS fetched_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE tm_events ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_tm_events_client_start
  ON tm_events (client_id, start_datetime DESC);

CREATE INDEX IF NOT EXISTS idx_tm_events_event_id
  ON tm_events (client_id, event_id);

CREATE INDEX IF NOT EXISTS idx_tm_events_subsystem_id
  ON tm_events (client_id, subsystem_id);

CREATE INDEX IF NOT EXISTS idx_tm_events_client_tm_event_id
  ON tm_events (client_id, tm_event_id);

-- -------------------------
-- tm1_seats (normalized items/seats from orders)
-- -------------------------
CREATE TABLE IF NOT EXISTS tm1_seats (
  client_id         text NOT NULL,
  seat_unique_id    text NOT NULL,
  tm1_order_id      text NOT NULL,
  tm_event_id       text,
  item_group_index  integer NOT NULL,
  item_index        integer NOT NULL,
  payload           jsonb NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, seat_unique_id)
);

CREATE INDEX IF NOT EXISTS idx_tm1_seats_client_order
  ON tm1_seats (client_id, tm1_order_id);

CREATE INDEX IF NOT EXISTS idx_tm1_seats_client_event
  ON tm1_seats (client_id, tm_event_id);

COMMIT;