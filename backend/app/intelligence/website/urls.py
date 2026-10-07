"""URL normalisation and in-domain checks for website intelligence.

`www.example.com` and `example.com` are the same site for crawling.
HTTP and HTTPS on that host are also in-domain (sites routinely upgrade).
Other subdomains are not collapsed — only a leading `www.`.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from app.intelligence.website.fetch import parse_public_url

_HTML_TYPES = ("text/html", "application/xhtml+xml")
_SNIFFABLE_TYPES = ("", "application/octet-stream", "text/plain")
_DROP_QUERY_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "gclid",
        "gbraid",
        "wbraid",
        "fbclid",
        "mc_cid",
        "mc_eid",
        "_ga",
        "_gl",
        "search",
        "q",
        "query",
        "s",
        "keyword",
        "keywords",
        "sort",
        "order",
        "orderby",
        "dir",
        "filter",
        "filters",
        "facet",
        "ref",
        "referrer",
        "source",
        "share",
    }
)
_SKIP_PATH_PREFIXES = (
    "/cdn-cgi/",
    "/.well-known/",
)


def canonical_host(host: str | None) -> str | None:
    """Lowercased host with a single leading `www.` removed."""

    if not host:
        return None
    value = host.strip().lower().rstrip(".")
    if value.startswith("www."):
        stripped = value[4:]
        return stripped or value
    return value


def origin_of(url: str) -> str:
    parsed = parse_public_url(url)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port and not (
        (parsed.scheme == "http" and port == 80)
        or (parsed.scheme == "https" and port == 443)
    ):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    return f"{parsed.scheme.lower()}://{netloc}"


def normalize_url(value: str, base: str | None = None) -> str | None:
    try:
        raw = urljoin(base, value) if base else value
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    port = parts.port
    if port and not (
        (parts.scheme == "http" and port == 80)
        or (parts.scheme == "https" and port == 443)
    ):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    filtered = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _DROP_QUERY_PARAMS
    ]
    query = urlencode(filtered, doseq=True)
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def same_site(url: str, origin: str) -> bool:
    """True when `url` is on the same registrable crawl host as `origin`.

    HTTP and HTTPS count as in-domain. `www.` and the apex host count as
    the same site so a start URL of `https://www.example.com/` still
    crawls sitemap locs and links on `https://example.com/`.
    """

    left = _host_port(url)
    right = _host_port(origin)
    return left is not None and left == right


def same_origin(url: str, origin: str) -> bool:
    """In-domain check used by the crawler. See `same_site`."""

    return same_site(url, origin)


def align_url_to_origin(url: str, origin: str) -> str | None:
    """Rewrite a same-site URL onto the crawl origin's scheme and host.

    Collapses `www` vs apex (and http vs https) so the same path is not
    fetched twice after a canonical-host redirect.
    """

    normalized = normalize_url(url)
    if normalized is None or not same_site(normalized, origin):
        return None
    try:
        origin_norm = origin_of(origin)
    except Exception:
        return normalized
    url_parts = urlsplit(normalized)
    origin_parts = urlsplit(origin_norm)
    return urlunsplit(
        (
            origin_parts.scheme,
            origin_parts.netloc,
            url_parts.path or "/",
            url_parts.query,
            "",
        )
    )


def should_skip_path(url: str) -> bool:
    """True for infrastructure paths that are not website pages."""

    normalized = normalize_url(url) or url
    try:
        path = urlsplit(normalized).path or "/"
    except ValueError:
        return True
    lowered = path.lower()
    return any(lowered.startswith(prefix) for prefix in _SKIP_PATH_PREFIXES)


def is_html_content_type(content_type: str) -> bool:
    lowered = content_type.lower().split(";", 1)[0].strip()
    return lowered in _HTML_TYPES


def looks_like_html(body: bytes) -> bool:
    sample = body[:1024].lstrip().lower()
    return (
        sample.startswith(b"<!doctype html")
        or sample.startswith(b"<html")
        or b"<html" in sample[:512]
        or b"<head" in sample[:512]
    )


def is_crawlable_html(content_type: str, body: bytes) -> bool:
    if is_html_content_type(content_type):
        return True
    lowered = content_type.lower().split(";", 1)[0].strip()
    if lowered in _SNIFFABLE_TYPES:
        return looks_like_html(body)
    return False


def _host_port(url: str) -> tuple[str, int | None] | None:
    normalized = normalize_url(url)
    if normalized is None:
        return None
    try:
        parts = urlsplit(normalized)
    except ValueError:
        return None
    host = canonical_host(parts.hostname)
    if not host:
        return None
    port = parts.port
    if port in (80, 443):
        port = None
    return (host, port)
