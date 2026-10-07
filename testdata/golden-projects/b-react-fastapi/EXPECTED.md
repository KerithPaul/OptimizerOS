# Golden project B — expected issues

React frontend + FastAPI backend. Planted for later SEO/AEO/GEO phases.

## Metadata

- `frontend/index.html` has a `<title>` but no `meta name="description"`.
- The FastAPI HTML response in `backend/app.py` uses the same title
  `Golden B` as the React document (duplicate title across front and back).

## Canonical

- `frontend/index.html` has no `<link rel="canonical">`.
- The FastAPI HTML page has no canonical link.

## Schema

- Neither the React app nor the FastAPI HTML emits JSON-LD or Schema.org.

## Notes

- Stack (profiler, no LLM): frontend React, backend FastAPI, language
  JavaScript + Python.
