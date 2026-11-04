# tm1-sync

Python services for the TM1 → HubSpot integration. See the [root README](../README.md)
for the architecture, data flow and full setup guide.

| Directory | Contents | Entry point |
|---|---|---|
| `sync/` | Sync job: ingest, normalize, enrich, enqueue, publish; API clients; HubSpot schema provisioning; structured logging | `python -m sync.main [--once]` |
| `db/` | PostgreSQL connection, idempotent `sql/schema.sql`, repositories, advisory locks | used by `sync/` |
| `oauth/` | FastAPI OAuth service (`/oauth/install`, `/oauth/callback`, `/healthz`) | `uvicorn main:app` (from `oauth/`) |
| `shared/` | Legacy helpers that nothing imports any more (scheduled for removal) | — |

## Quick start

```bash
cp sync/.env.example sync/.env      # fill in the placeholders
cp oauth/.env.example oauth/.env
pip install -r sync/requirements.txt -r oauth/requirements.txt
python -m sync.main --once
```

## Docker

```bash
docker build -f sync/Dockerfile -t tm1-sync-job .   # build context: tm1-sync/
docker build -t tm1-oauth oauth                      # build context: tm1-sync/oauth/
```

`tm1-sync/.dockerignore` keeps `.env` files, local state, virtual environments and data
exports out of the sync image. `oauth/.dockerignore` does the same for the OAuth image.
