# RAG golden dataset (Phase 5.D)

Ground truth for `backend/tests/e2e/rag_eval.py`.

The five `[SPEC]` seed queries (`AGENTS.md` §63 / `IMPLEMENTATION_PLAN_V2.md` step 5.D.1) are instantiated against golden projects **A**, **B**, and **E**. Expected evidence is taken from each project's `EXPECTED.md` and from the symbols the Phase 2 extractor already asserts on golden A. It is **not** a dump of whatever the retriever currently returns — the harness is supposed to fail when retrieval misses this evidence.

## Spec query mapping

| Spec query | What the gold records |
|---|---|
| Where is product metadata generated? | Golden A: `generateMetadata` in `app/products/[slug]/page.tsx`. There is no symbol named `ProductMetadata`. |
| Which component renders Product schema? | Golden A: `ProductPage` in that same file. `EXPECTED.md` states product pages emit **no** Product JSON-LD; there is no schema component to retrieve. |
| What controls canonical URLs? | The files that would emit a canonical and currently do not (A: `generateMetadata` + root `metadata`; B: `frontend/index.html` + `backend/app.py`; E: `about.html` and `products.html`). |
| Which routes use ProductMetadata? | Golden A: route `/products/[slug]`, component `ProductPage`, metadata function `generateMetadata` (`GENERATES_METADATA` in the code graph). |
| Which SEO rule applies to this issue? | The spec query names no issue. Each gold row binds **one** planted defect from `EXPECTED.md` to the `rule_id` whose mechanical condition matches that defect. |

## Projects

| Id | Path | Used for |
|---|---|---|
| `a-nextjs-ts-mysql` | `testdata/golden-projects/a-nextjs-ts-mysql/` | Code + metadata/canonical/schema |
| `b-react-fastapi` | `testdata/golden-projects/b-react-fastapi/` | Canonical / title / description |
| `e-static-html` | `testdata/golden-projects/e-static-html/` | Pages + rule hits |

## File

`queries.json` — the machine-readable set. `source_types` matches how `AgentTools` scopes retrieval (`retrieve_code` / `retrieve_pages` / `retrieve_knowledge`), not an unscoped dump of all three collections.
