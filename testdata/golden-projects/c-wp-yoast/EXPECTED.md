# Golden project C — WordPress + Yoast (recorded REST fixtures)

These are **recorded WordPress REST fixtures**, not a live WordPress
install (IMPLEMENTATION_PLAN_V2.md Q3). Tests must not claim live WP
coverage. The connector talks to the same REST shapes a real site
exposes; the transport in tests is `httpx.MockTransport`.

Base URL used in tests: `https://golden-c.example/`

SEO plugin: Yoast (`yoast/v1` namespace). Yoast meta keys are registered
on the REST schema so adapter writes can be verified.

## Planted defects

- Page `/about/` title is `Golden C` (duplicate of the home title).
- Page `/about/` has no Yoast meta description.
- Page `/about/` has no canonical.
- Home content includes an internal link to `/missing/` which is not a
  REST resource.

## Notes

- No git repository.
- Theme source is not available over REST.
- Yoast's own `/yoast/v1/` routes are read-only; writes go through
  registered `_yoast_wpseo_*` post meta when the schema exposes them.
