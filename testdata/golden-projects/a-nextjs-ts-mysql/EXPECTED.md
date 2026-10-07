# Golden project A — expected issues

Planted for later SEO/AEO/GEO phases. Checkpoint 2.D records these as
ground truth; it does not yet raise findings.

## Metadata

- `app/layout.tsx` sets `title` to the generic string `Golden A`.
- `app/about/page.tsx` exports the same title `Golden A` (duplicate title).
- `app/products/[slug]/page.tsx` `generateMetadata` returns `{ title: "Product" }`
  for every slug. The title is not product-specific even though `getProduct`
  is called.

## Canonical

- No page sets a canonical URL (`alternates.canonical` is absent on the
  layout and on the product page).

## Schema

- Product pages do not emit Product JSON-LD or any other Schema.org block.
- There is no `schema-dts` (or equivalent) dependency.

## Notes

- Stack (profiler, no LLM): Next.js, TypeScript, MySQL.
- Product page symbols: `ProductPage` ROUTES_TO `/products/[slug]`, CALLS
  `getProduct`, GENERATES_METADATA `generateMetadata`.
