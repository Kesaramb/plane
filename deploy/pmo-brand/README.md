# PMO branding

Plane is our project management office. Staff should call it PMO, so it should call
itself PMO — in email, in the browser tab, and on the screens they sign in through.

There are three separate mechanisms here, because the three surfaces fail differently.

| Surface | Mechanism | Survives a Plane upgrade? |
|---|---|---|
| Email | Django templates in `apps/api/templates/emails/` | yes, unless upstream rewrites them |
| Favicons, app icons, PWA manifests, og:image | exact-path `location` overrides in nginx | **no — build hashes change** |
| Tab title, sign-in copy, wordmark | `brand.js`, injected by nginx | yes |

## The build-hash coupling

`nginx.ssl.conf_brandasset` overrides paths like `/assets/favicon-32x32-cfWXc3P9.png`.
That suffix is a Vite content hash. A Plane upgrade rebuilds the bundle, the hashes
change, the overrides stop matching, and **the Plane logo quietly comes back** — no
error anywhere.

After any Plane upgrade, re-read the hashes and update the config:

```bash
docker exec web sh -c "ls /usr/share/nginx/html/assets/ | grep -E 'favicon|icon-|og-image|gradient-logo'"
```

Then check the wordmark is still the same drawing — `brand.js` finds it by
`svg[viewBox="0 0 253 53"]`:

```bash
curl -s https://pmo.gobrandfactory.com/ | grep -c 'brand.js'
```

## Regenerating the artwork

`make_logo.py` draws everything from one cap height and one brand blue (`#2AA9E0`,
taken from the BrandFactory landing page), so the set stays internally consistent:

```bash
python3 make_logo.py
```

The gear below 48px drops to six teeth deliberately — eight blur into a smudge at
favicon size, and a favicon is how people find the tab.

**The wordmark is a reconstruction**, not the original vector file. It is Arial Bold
set next to a generated gear, which is close but not identical to the real logo. To
use the real artwork, drop files with these names in beside the generated ones and
skip `make_logo.py`; nothing else has to change:

`pmo-logo.png` · `pmo-logo-white.png` · `pmo-mark.png` · `pmo-mark-white.png` ·
`favicon-16.png` · `favicon-32.png` · `favicon.ico` · `icon-180/192/512.png` ·
`gradient-logo.webp` · `og-image.png`

## Why the naming layer is narrow

`brand.js` renames Plane freely **only on signed-out routes**, where nothing on the
page was written by a user. Everywhere else it rewrites a fixed list of Plane's own
UI strings and nothing more.

This is not caution for its own sake. A blanket `Plane` → `PMO` pass would rewrite a
work item titled "Plane tickets for the Colombo shoot" in front of the person who
wrote it. We are a brand agency; that sentence is not hypothetical.

The same reasoning removed rather than renamed two things: Plane's "10,000+ teams
building with Plane" line, and the Sony / Dolby / Accenture customer logos on the
sign-in page. Renamed, both would have become claims about BrandFactory that are not
true.

## Deployment

Assets and `brand.js` live at
`/home/admin/web/pmo.gobrandfactory.com/pmo-brand/`. The nginx drop-ins install as
`nginx.ssl.conf_brandasset` and `nginx.ssl.conf_banner` under
`/home/admin/conf/web/pmo.gobrandfactory.com/`. Always `nginx -t` before reloading.

`brand.js` is served `no-store`, and the injected `<script src>` carries a `?v=`
query. Bump it when a change has to reach browsers that already hold a copy — an
earlier deploy shipped the file with a long expiry, and the fix sat behind that cache
until the URL changed.

API templates and Python reach the containers by `docker cp` into `api`, `bgworker`
and `beatworker`, followed by a restart. **This does not survive `docker compose up`
recreating a container** — the image still holds the stock Plane templates. After any
recreate, re-copy and restart.

## Things that were deliberately left alone

- `X-Plane-*` webhook headers — part of the wire protocol; renaming breaks consumers.
- The `plane` Python package name, module paths and the AGPL copyright headers.
- Localised UI strings. Staff use English; the fixed-string list covers English only.
- Signed-in chrome not on that list — the help menu and a few error toasts still say
  Plane. Extending the list is safe; a blanket rename is not.
