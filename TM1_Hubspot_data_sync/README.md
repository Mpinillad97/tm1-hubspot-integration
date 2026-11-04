# HubSpot app definition

A HubSpot Developer Project (platform `2025.2`) that declares the private OAuth app used by
the sync service.

- [`hsproject.json`](hsproject.json): project metadata
- [`src/app/app-hsmeta.json`](src/app/app-hsmeta.json): app name, OAuth scopes and redirect URL

## Scopes requested

Contacts (read/write), Orders (schema, objects and pipelines), and custom objects and schemas
(read/write). The custom-object scopes are needed because the sync job creates the
*Ticketmaster Event* and *Ticketmaster Seat* objects at runtime.

## Deploy

1. Replace the placeholder in `redirectUrls` with your OAuth service's
   `https://<host>/oauth/callback` URL.
2. Authenticate the HubSpot CLI (`hs init` / `hs auth`), then run `hs project upload` from this directory.
3. Install the app in your portal. The OAuth service stores the resulting tokens.

The `support` block in `app-hsmeta.json` contains placeholder values. Replace them before
distributing the app.
