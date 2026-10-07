# Golden project D — WordPress + Rank Math (recorded REST fixtures)

These are **recorded WordPress REST fixtures**, not a live WordPress
install (IMPLEMENTATION_PLAN_V2.md Q3). Tests must not claim live WP
coverage.

Base URL used in tests: `https://golden-d.example/`

SEO plugin: Rank Math (`rankmath/v1` namespace). Rank Math meta keys are
registered on the REST schema so adapter writes can be verified.

## Planted defects

- Page `/about/` title is `Golden D` (duplicate of the home title).
- Page `/about/` has no Rank Math description.
- Home content is thin (under 50 words).

## Notes

- No git repository.
- A Yoast-only meta key written against this site must fail at the
  Rank Math adapter boundary, not silently.
- Rank Math `getHead` is read-only; writes go through registered
  `rank_math_*` post meta when the schema exposes them.
