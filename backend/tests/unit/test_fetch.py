"""SSRF-safe fetcher (step 3.B.1 verify).

Blocked classes are ported from ClearSite's security suite (Appendix D):
localhost, RFC1918, link-local, IPv6 ULA, DNS-to-private, DNS failure,
and a public URL that 302-redirects to 127.0.0.1.
"""

import pytest
import httpx

from app.core.config import get_settings
from app.intelligence.website.fetch import (
    FetchTimeoutError,
    PrivateHostError,
    UnsupportedSchemeError,
    fetch_public,
    is_private_host,
)


def _public_lookup(host: str) -> list[str]:
    if host in {"public.example", "example.com"} or host.endswith(".example.com"):
        return ["93.184.216.34"]
    raise OSError("unrecognised test host")


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "foo.localhost",
        "printer.local",
        "127.0.0.1",
        "10.1.2.3",
        "192.168.1.10",
        "172.16.4.1",
        "169.254.1.1",
        "::1",
        "fe80::1",
        "fd12:3456:789a:1::1",
        "fc00::1",
    ],
)
def test_private_and_local_hosts_are_blocked(host: str) -> None:
    assert is_private_host(host) is True


def test_public_dns_result_is_allowed_and_private_dns_is_blocked() -> None:
    assert is_private_host("public.example", lookup=_public_lookup) is False
    assert is_private_host("internal.example", lookup=lambda h: ["10.1.2.3"]) is True


def test_dns_lookup_failure_is_fail_closed() -> None:
    assert is_private_host("no-such.example", lookup=lambda h: (_ for _ in ()).throw(OSError("nxdomain"))) is True


def test_file_scheme_is_rejected() -> None:
    with pytest.raises(UnsupportedSchemeError):
        fetch_public("file:///etc/passwd", lookup=_public_lookup)


def test_loopback_url_is_rejected_before_connect() -> None:
    with pytest.raises(PrivateHostError):
        fetch_public("http://127.0.0.1/secret", lookup=_public_lookup)


def test_public_url_redirecting_to_loopback_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/open":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        return httpx.Response(200, text="should not be fetched")

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    with pytest.raises(PrivateHostError, match="private or local"):
        fetch_public(
            "http://public.example/open",
            client=client,
            lookup=_public_lookup,
            settings=get_settings(),
        )


def test_public_url_redirecting_to_rfc1918_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://10.0.0.5/internal"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    with pytest.raises(PrivateHostError):
        fetch_public(
            "http://public.example/open",
            client=client,
            lookup=_public_lookup,
            settings=get_settings(),
        )


def test_public_fetch_returns_body_and_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html>ok</html>")

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    result = fetch_public(
        "http://public.example/",
        client=client,
        lookup=_public_lookup,
        settings=get_settings(),
    )
    assert result.status_code == 200
    assert result.text == "<html>ok</html>"
    assert result.redirect_chain == []
    assert result.fetch_ms >= 0


def test_byte_cap_truncates_body() -> None:
    settings = get_settings().model_copy(update={"crawl_max_html_bytes": 8})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="abcdefghijklmnop")

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    result = fetch_public(
        "http://public.example/",
        client=client,
        lookup=_public_lookup,
        settings=settings,
    )
    assert result.truncated is True
    assert result.body == b"abcdefgh"


def test_timeout_is_an_explicit_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    with pytest.raises(FetchTimeoutError):
        fetch_public(
            "http://public.example/",
            client=client,
            lookup=_public_lookup,
            settings=get_settings(),
        )
