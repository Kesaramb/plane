# PMO announcement banner

A banner pinned to the top of the Plane UI, targetable at everyone / a workspace /
a project / a team. Written for pmo.gobrandfactory.com.

The coupling below is invisible from the codebase, which is the only reason this
file exists. **Read it before changing the proxy layer or upgrading Plane.**

## How it fits together

| Piece | Where |
|---|---|
| Models | `apps/api/plane/db/models/announcement.py` (+ migration `0123_…`) |
| Read / dismiss / manage API | `apps/api/plane/app/{views,serializers,urls}/announcement.py` |
| Banner script | `deploy/pmo-banner/banner.js` → server `/home/admin/web/pmo.gobrandfactory.com/pmo-banner/banner.js` |
| Script route | server `…/conf/web/pmo.gobrandfactory.com/nginx.ssl.conf_bannerasset` |
| HTML injection | server `…/conf/web/pmo.gobrandfactory.com/nginx.ssl.conf_banner` |
| Authoring UI | dashboard `index.html` → "Announce" tab |
| Watchdog | `/root/plane-dashboard/check_banner.sh` + `/etc/cron.d/plane-banner-check` |

**No part of this touches `apps/web`.** That is deliberate: the web bundle is built
into its image, and rebuilding it is a 7–12 min turbo+Vite run at an estimated
4–8 GB peak on a host with no swap that also serves ~100 unrelated client apps.

## The three things that silently break it

1. **Compression upstream.** `sub_filter` cannot rewrite a compressed body. Caddy
   currently does not compress (no `encode` in `/etc/caddy/Caddyfile`). Adding
   `encode gzip` there, or `gzip on` in the web container's nginx, turns the
   injection into a no-op with no error anywhere.
2. **The `</body>` anchor.** A Plane upgrade that changes the HTML tail stops the
   filter matching. Every route currently returns the same static SPA shell.
3. **Hestia regenerating `nginx.ssl.conf`.** The `nginx.ssl.conf_*` drop-ins
   survive domain operations, but the `include` line lives in a file Hestia owns.

All three fail the same way — the banner just disappears — which is why the
15-minute watchdog exists. It notifies the instance admin in Plane.

## Deploying a change

Script only (styling, behaviour) — no rebuild, no restart:

    scp deploy/pmo-banner/banner.js root@HOST:/home/admin/web/pmo.gobrandfactory.com/pmo-banner/banner.js

It is served `no-store`, so a browser refresh picks it up.

API change — rebuild **only** the Python services, never bare `docker compose build`:

    docker tag plane-api:latest plane-api:rollback-$(date +%Y%m%d)
    docker compose build api worker beat-worker
    docker compose up -d api worker beat-worker

## Design notes

- React owns `<body>` (`apps/web/app/root.tsx`), so the host element is appended at
  runtime and re-appended by a `MutationObserver`. Never inject static markup.
- The banner reclaims layout by pushing `.desktop-app-container` down, rather than
  covering Plane's top bar.
- A shadow root isolates it from Tailwind preflight in both directions.
- Bind-mounting a modified `index.html` was considered and rejected: asset names are
  content-hashed, so the next web build would leave the mount pinned to stale
  filenames and the whole app would render blank. Injection degrades to "no banner".
- Guests (role 5) are excluded from every announcement.
- `link_url` is validated to http(s) — it renders into an `<a href>`, so
  `javascript:` would be stored XSS.
- Dismissal records the revision dismissed. Bumping `revision` (the "re-show"
  toggle when editing) brings a corrected announcement back for people who had
  dismissed it; a silent edit does not.
