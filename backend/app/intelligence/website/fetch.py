"""SSRF-safe outbound fetch — checkpoint 3.B.1.

Every robots.txt, sitemap, and page fetch goes through `fetch_public`.
Policy harvested from ClearSite `fetchPublic` / `isPrivateHost` (plan
Appendix D): HTTP/HTTPS only, block localhost / `.local` / RFC1918 /
link-local / IPv6 ULA, DNS fail-closed, manual redirects re-checked on
every hop. Timeout, byte cap, and User-Agent come from settings.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urljoin, urlsplit

import httpx

from app.core.config import Settings, get_settings

LookupFn = Callable[[str], list[str]]

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 10
_ACCEPT = "text/html,application/xhtml+xml,application/xml,text/plain,*/*"


class FetchError(Exception):
    """Outbound fetch failed. Callers must not convert this into success."""


class PrivateHostError(FetchError):
    """The URL's host is local, private, or failed DNS (fail closed)."""


class UnsupportedSchemeError(FetchError):
    """Only http and https are allowed."""


class FetchTimeoutError(FetchError):
    """The fetch exceeded CRAWL_FETCH_TIMEOUT_SECONDS."""


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    body: bytes
    content_type: str
    redirect_chain: list[str] = field(default_factory=list)
    truncated: bool = False
    fetch_ms: int = 0

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def parse_public_url(value: str) -> SplitResult:
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https"):
        raise UnsupportedSchemeError("Only public HTTP and HTTPS websites can be fetched.")
    if parsed.username or parsed.password:
        raise FetchError("credentials in URL are not allowed")
    if not parsed.hostname:
        raise FetchError("URL is missing a hostname")
    return parsed


def lookup_ips(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise FetchError(f"DNS lookup failed for {hostname}") from exc
    return list({info[4][0] for info in infos})


def is_private_host(hostname: str, *, lookup: LookupFn | None = None) -> bool:
    """True if `hostname` must not be fetched.

    Lookup failure is treated as private (fail closed).
    """

    host = hostname.strip().lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True
    if _as_ip(host) is not None:
        return _ip_is_blocked(host)
    resolve = lookup or lookup_ips
    try:
        addresses = resolve(host)
    except Exception:
        return True
    if not addresses:
        return True
    return any(is_private_host(address, lookup=lookup) for address in addresses)


def _as_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _ip_is_blocked(value: str) -> bool:
    addr = ipaddress.ip_address(value)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr.is_loopback or addr.is_unspecified or addr.is_link_local:
        return True
    if addr.version == 4:
        return bool(
            addr in ipaddress.ip_network("10.0.0.0/8")
            or addr in ipaddress.ip_network("172.16.0.0/12")
            or addr in ipaddress.ip_network("192.168.0.0/16")
        )
    return addr in ipaddress.ip_network("fc00::/7")


def fetch_public(
    url: str,
    *,
    method: str = "GET",
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    lookup: LookupFn | None = None,
    timeout: float | None = None,
) -> FetchResult:
    """Fetch `url` after an SSRF check. Redirects are followed manually."""

    settings = settings or get_settings()
    timeout_s = timeout if timeout is not None else settings.crawl_fetch_timeout_seconds
    headers = {
        "user-agent": settings.crawl_user_agent,
        "accept": _ACCEPT,
    }
    owned = client is None
    http = client or httpx.Client(
        trust_env=False,
        follow_redirects=False,
        timeout=timeout_s,
    )
    started = time.perf_counter()
    try:
        result = _fetch_hops(
            url,
            method=method,
            http=http,
            headers=headers,
            lookup=lookup,
            max_bytes=settings.crawl_max_html_bytes,
            timeout_s=timeout_s,
        )
        result.fetch_ms = max(0, int((time.perf_counter() - started) * 1000))
        return result
    finally:
        if owned:
            http.close()


def _fetch_hops(
    url: str,
    *,
    method: str,
    http: httpx.Client,
    headers: dict[str, str],
    lookup: LookupFn | None,
    max_bytes: int,
    timeout_s: float,
) -> FetchResult:
    current = url
    chain: list[str] = []
    for _hop in range(_MAX_REDIRECTS + 1):
        parsed = parse_public_url(current)
        host = parsed.hostname or ""
        if is_private_host(host, lookup=lookup):
            raise PrivateHostError(
                "The target resolves to a private or local network address."
            )
        try:
            response = http.request(
                method if _hop == 0 else "GET",
                current,
                headers=headers,
                follow_redirects=False,
                timeout=timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise FetchTimeoutError(
                f"fetch timed out after {timeout_s}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"fetch failed: {exc}") from exc

        if response.status_code in _REDIRECT_STATUSES:
            location = response.headers.get("location")
            if not location:
                return _result_from_response(
                    response, requested=url, final=current, chain=chain, max_bytes=max_bytes
                )
            nxt = urljoin(current, location)
            chain.append(nxt)
            current = nxt
            continue
        return _result_from_response(
            response, requested=url, final=current, chain=chain, max_bytes=max_bytes
        )
    raise FetchError("too many redirects")


def _result_from_response(
    response: httpx.Response,
    *,
    requested: str,
    final: str,
    chain: list[str],
    max_bytes: int,
) -> FetchResult:
    truncated = False
    body = response.content
    if len(body) > max_bytes:
        body = body[:max_bytes]
        truncated = True
    content_type = response.headers.get("content-type") or ""
    headers = {k.lower(): v for k, v in response.headers.items()}
    return FetchResult(
        url=requested,
        final_url=str(response.url) if response.url else final,
        status_code=response.status_code,
        headers=headers,
        body=body,
        content_type=content_type,
        redirect_chain=chain,
        truncated=truncated,
    )
