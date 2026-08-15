# PMO branding

Plane is our project management office. Staff should call it PMO, so it should call
itself PMO — in email, in the browser tab, and on every screen they use.

| Surface | Mechanism | Survives a Plane upgrade? | Survives `docker compose up`? |
|---|---|---|---|
| Email | Django templates in `apps/api/templates/emails/` | yes | no — re-`docker cp` |
| All UI text, tab title, meta | the built bundle + `index.html`, patched in place | **no — rebuild reverts it** | no — re-run the patch |
| Favicons, app icons, spinner, og:image, manifests | exact-path `location` overrides in nginx | **no — build hashes change** | yes |
| Wordmark + customer-logo strip | `brand.css`, appended to the app's own stylesheet | no | no — re-append |

## The four things that make this harder than it looks

**1. React owns the document.** React Router reconciles the whole document during
hydration and removes anything nginx injects — a `<style>` block simply vanishes. The
banner script survives only because it re-attaches from a MutationObserver. This is why
the CSS lives inside the app's own stylesheet rather than an injected tag.

**2. Rewriting `<head>` breaks hydration.** An earlier version used `sub_filter` to
rewrite `<title>` and the meta tags. React's virtual DOM then disagreed with the served
HTML and threw React #418/#423 on every load. `index.html` and the bundle are now patched
to say the same thing, and nginx no longer touches the head.

**3. Caching hides the whole rebrand.** This is the one that actually bites.

The app's HTML document shipped with **no `Cache-Control` at all**, and so did every
hashed asset. Patching files in place keeps the filename, so the cache key never changes
and a returning browser keeps serving the old Plane bundle — with nothing in any log to
say so. `nginx.ssl.conf_brandasset` now sets `no-store` on the document and a short
`max-age` on `/assets/`.

When checking this, test **`/sign-in`, not `/`**. The root path is a separate static
landing page that has always been `no-store`; reading its headers and concluding the app
was fine is a mistake already made once here.

**4. A deferred injected script loses a race.** `banner.js` was injected before `</body>`
with `defer`. With a warm cache React's module script executes first and removes the tag
before the deferred script runs, so the banner silently never starts. It is now a plain
synchronous script in `<head>`.

## Regenerating the artwork

`make_logo.py` draws everything from one cap height and one brand blue (`#2AA9E0`, taken
from the BrandFactory landing page), so the whole set stays consistent:

```bash
python3 make_logo.py
```

The gear drops to six teeth below 48px because eight blur into a smudge at favicon size,
and the loading spinner is the wordmark holding still while the gear turns — eight teeth
means one 45° rotation is a seamless loop.

**The wordmark is a reconstruction**, not the original vector file: Arial Bold beside a
generated gear. To use the real artwork, drop files in beside these names and skip
`make_logo.py` — nothing else changes:

`pmo-logo.png` · `pmo-logo-white.png` · `pmo-mark.png` · `pmo-mark-white.png` ·
`favicon-16/32.png` · `favicon.ico` · `icon-180/192/512.png` · `gradient-logo.webp` ·
`og-image.png` · `logo-spinner-light.gif` · `logo-spinner-dark.gif`

Plane's spinner naming is inverted from intuition: `-light` holds the **dark** artwork,
because it is the one shown on a light background.

## The bundle rewrite

Text is rebranded by rewriting string literals in the built JS and `index.html`, not by
patching the DOM at runtime. That covers every locale and every signed-in screen, and
there is no flash of "Plane" before a script gets to it.

It is safe here, and it was checked rather than assumed:

- Subresource integrity is **off** (`unstable_subResourceIntegrity: false`), so changed
  bytes do not fail a hash check.
- A whole-word capital-`Plane` match provably misses everything structural: zero
  `X-Plane-*` headers, zero property accesses, zero URLs, zero unquoted object keys, zero
  occurrences preceded by an identifier character. The six followed by one are foreign
  words (`Planejado`, `Planen`) that the trailing word boundary excludes.
- i18n **keys** are lowercase (`new_to_plane`, `powered_by_plane_pages`) and untouched.
- User-authored content never lives in the bundle, so there is no risk of rewriting a work
  item titled "Plane tickets for the Colombo shoot".
- All 52 modified files were re-parsed with `node --check` afterwards.

Stock copies are kept at `/root/pmo-assets-stock.tgz` on the server, and pristine files
can always be pulled from the image itself:

```bash
CID=$(docker create $(docker inspect web --format '{{.Config.Image}}')); docker cp "$CID":/usr/share/nginx/html/assets ./stock-assets; docker rm "$CID"
```

## After a Plane upgrade or a container recreate

Both revert the bundle, `index.html`, `brand.css` and the API templates. Re-apply, then
re-check the hashed filenames the nginx overrides target:

```bash
docker exec web sh -c "ls /usr/share/nginx/html/assets/ | grep -E 'favicon|icon-|og-image|gradient-logo|logo-spinner'"
```

Also confirm the two CSS selectors still match: the wordmark `viewBox` appears once in the
bundle (`icons-*.js`) and the customer-logo cell class once (`auth-base-*.js`).

## Removed rather than renamed

Renaming these would have made BrandFactory assert things that are not true:

- "Join 10,000+ teams building with Plane" on the sign-in page — emptied in the bundle.
- Plane's customer logos (Zerodha, Sony, Dolby, Accenture) — hidden by `brand.css`. They
  read as our clients.
- The activation and deactivation emails, which were 1,570-line campaign exports selling
  Plane's roadmap and community to our staff. Now short account notices.
- "Despite our popularity, we are humbly early-stage…" in the webhook and invitation
  emails, and Plane's social-link row.

## Deliberately left alone

- `X-Plane-*` webhook headers — wire protocol; renaming breaks consumers.
- The `plane` Python package name, module paths, and the AGPL copyright headers.
- React hydration errors #418/#423 in the console. These were measured against the
  **stock** bundle with all injection disabled and appear identically — 38 either way.
  They are Plane's own, not ours, and the app renders correctly regardless.

## Temporary

`nginx.ssl.conf_zflush` sends `Clear-Site-Data: "cache"` on the document so browsers
holding the pre-rebrand bundle drop it. Delete it once the team has loaded the app:

```bash
rm /home/admin/conf/web/pmo.gobrandfactory.com/nginx.ssl.conf_zflush && nginx -t && systemctl reload nginx
```
