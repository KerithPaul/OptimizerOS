# ArchitectOS WordPress Connector Plugin — design only

This is the capability surface of a **future** ArchitectOS WordPress plugin.
It is **not shipped**. The basic REST connector in
`backend/app/connectors/wordpress/` must not import, call, or depend on
this plugin.

Status: design only (IMPLEMENTATION_PLAN_V2.md step 10.5, AGENTS.md §40).

## Purpose

WordPress REST + Application Passwords already cover pages, posts, media,
taxonomies, revisions, and (when registered) SEO plugin post meta. A
first-party plugin would expose a **controlled** ArchitectOS namespace for
operations core REST does not honestly support:

- site discovery that does not require `manage_options`
- page discovery across custom types with a stable identity
- SEO metadata **writes** without requiring `register_post_meta` in the theme
- schema reads/writes as structured objects, not HTML `yoast_head`
- content with before/after hashes
- theme information (name, template, not theme PHP source)
- plugin information (active slugs, versions)
- snapshots taken inside WordPress (not only ArchitectOS JSON copies)
- controlled mutations with an allowlist of fields
- rollback that restores a WordPress revision by id
- capability discovery that reports what this site can actually mutate

## Proposed namespace

`/wp-json/architectos/v1/`

Suggested routes (not implemented):

| Method | Path | Capability |
|---|---|---|
| GET | `/capabilities` | Honest matrix: source/theme/content/metadata/SEO/AEO/GEO/rollback |
| GET | `/site` | Site name, home URL, language, timezone |
| GET | `/pages` | Pages/posts/CPTs the authenticated user can edit |
| GET | `/pages/{id}` | Content, metadata, schema, media, revisions summary |
| POST | `/pages/{id}` | Controlled mutation of allowlisted fields |
| GET | `/pages/{id}/revisions` | Revision list |
| POST | `/pages/{id}/revisions/{revision_id}/restore` | Restore that revision |
| GET | `/plugins` | Active plugins (SEO plugin detection without admin REST) |
| GET | `/theme` | Theme name/stylesheet — not PHP source |
| POST | `/snapshots` | WordPress-side snapshot token |
| POST | `/snapshots/{id}/restore` | Restore snapshot |

## Rules

- Authentication remains WordPress Application Passwords (or a later
  least-privilege token). Credentials stay on ArchitectOS
  `platform_connections`, encrypted.
- The plugin must not become a required dependency of
  `WordPressConnector`. If `/architectos/v1/capabilities` is absent, the
  REST connector continues to use `wp/v2`, `yoast/v1` (read-only), and
  `rankmath/v1` (read-only) plus registered post meta.
- Plugin-specific SEO fields remain adapter-scoped. Yoast keys never
  write on a Rank Math site.
- Theme PHP and filesystem source stay out of v1 even with this plugin
  (AGENTS.md P12).
