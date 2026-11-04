# Architecture Decision Records

Short records of the main design decisions behind the integration, as reflected in the code.

---

## ADR-001: Use PostgreSQL as both the system of record and the delivery queue
**Context.** Changes from TM1 must reach HubSpot reliably despite API rate limits, transient
failures and an occasionally unavailable CRM, without adding infrastructure to operate.

**Options.** A message broker (RabbitMQ, SQS, Pub/Sub), or an outbox table in the existing database.

**Decision.** An outbox table (`hubspot_outbox`). Messages are claimed with
`FOR UPDATE SKIP LOCKED`, dedupe keys are enforced by a partial unique index, and retry
scheduling uses `next_retry_at`.

**Consequences.** There is no extra infrastructure, and queue state can be inspected with SQL.
Throughput is bounded by the database and the HubSpot rate limit rather than by a broker. Normalized state
and outbox writes should share one transaction; this is tracked on the roadmap.

---

## ADR-002: Incremental ingestion with a persisted cursor
**Context.** The legacy integration re-exported full files over SFTP.

**Decision.** Store a per-tenant cursor (`sync_state.last_since`) and advance it to the
largest `updatedSince` value observed, but only after every page of the run succeeds.
Raw payloads are stored before normalization.

**Consequences.** Failed runs are retried from the same cursor. Some records may be
re-fetched, which is harmless because all downstream writes are idempotent.

---

## ADR-003: Idempotent HubSpot writes keyed on external identifiers
**Context.** At-least-once delivery means a message can be published more than once.

**Decision.** Every HubSpot object has a unique external key (`tm1_unique_id`, `tm_event_id`,
`seat_unique_id`, email). Publishing performs an upsert on that key.

**Consequences.** Redelivery updates the existing record instead of creating a new one.
Moving to HubSpot's batch-upsert endpoints is planned, to reduce API calls.

---

## ADR-004: Provision the HubSpot schema at runtime
**Context.** The integration needs custom objects, properties and association labels that
may not exist yet in a newly connected portal.

**Decision.** Before publishing, the job ensures that the required schema exists and creates
anything missing. "Already exists" responses are treated as success.

**Consequences.** Connecting a new portal needs no manual setup. The extra API calls at the
start of each publish should be cached; this is tracked on the roadmap.

---

## ADR-005: Run the sync as a scheduled batch job
**Context.** TM1 exposes a polling API, not webhooks.

**Decision.** Package the sync as a container that runs one iteration and exits
(`--once`). The hosting platform's scheduler triggers it, for example every 15 minutes.
OAuth runs as a separate, small web service.

**Consequences.** The job is stateless between runs, and scaling or pausing is a scheduler
setting (`SYNC_ENABLED` provides a kill switch). The per-run Discovery cap
(`DISCOVERY_MAX_PER_RUN`) is sized from the run frequency and the daily API quota.
