# Golden project E — expected extraction observations

Static HTML fixture for checkpoint 3.C.1. These are **planted defects
and observations**, not findings and not scores. Extraction must surface
them in the Common Website Model exactly as listed. Scoring and rules
are Phase 4.

Base URL used in tests: `https://golden-e.example/`

## Duplicate titles

- `index.html` title is `Golden E`.
- `about.html` title is `Golden E`.
- Two pages therefore share the same title.

## Missing canonical

- `about.html` has no canonical link.
- `products.html` has no canonical link.
- `index.html` has canonical `https://golden-e.example/index.html`.
- `contact.html` has canonical `https://golden-e.example/contact.html`.
- `orphan.html` has canonical `https://golden-e.example/orphan.html`.

## Missing alt

- `products.html` contains an image `widget.png` with **no** `alt` attribute.

## Broken internal link

- `index.html` contains an internal link to `missing.html`.
- There is no `missing.html` file in this fixture.

## Heading skip

- `about.html` heading levels in document order include `1` then `3`
  with no `2` between them (`<h1>About</h1>` then `<h3>History</h3>`).

## Malformed JSON-LD

- `contact.html` contains a `application/ld+json` script whose body is
  not valid JSON. Extraction must record a parse failure, not drop the
  block.

## Orphan page

- `orphan.html` is not linked from `index.html`, `about.html`,
  `products.html`, or `contact.html`.
- After persistence (checkpoint 3.D), this page is discoverable as an
  orphan by a Neo4j query: a `Page` whose `URL` has no inbound `LINKS_TO`.

## AEO-lite observations (not scores)

- `about.html` has a question-form heading `What is Golden E?`.
- The paragraph immediately after that heading is a concise answer lead
  (40–80 words).
- `about.html` has authorship markup (`meta name="author"`).
- `about.html` has a `<time datetime>` element.

## Notes

- Stack: static HTML only. No framework.
- `llms.txt` is present at the site root and starts with a markdown heading.
- Extraction must not treat Lighthouse or Playwright output as part of
  this fixture. Those are separate 3.C.2 / 3.C.3 concerns.
