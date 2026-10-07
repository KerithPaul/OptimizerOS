"""Playwright renderer — checkpoint 3.C.2.

Lazy: the browser starts on first render and is shut down by `close()`.
1–2 contexts, reused. A render failure is `timeout` / `blocked` /
`unavailable` and does not abort a crawl. JS-injected metadata is
detected by comparing raw HTML hash vs rendered DOM hash; when observed,
the rendered title wins.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.connectors.model import Page
from app.core.config import Settings, get_settings
from app.intelligence.website.extract import extract_page, html_hash
from app.intelligence.website.webmcp import merge_agent_tools, tools_from_runtime
from app.intelligence.website.fetch import LookupFn, is_private_host, parse_public_url

logger = logging.getLogger("architectos.intelligence.website.render")

_MAX_RENDERED_TEXT = 200_000


@dataclass
class RenderResult:
    url: str
    state: str
    rendered_html: str | None = None
    rendered_title: str | None = None
    rendered_dom_hash: str | None = None
    rendered_text: str | None = None
    raw_html_hash: str | None = None
    raw_to_rendered_changed: bool | None = None
    console_errors: list[str] = field(default_factory=list)
    request_failures: list[str] = field(default_factory=list)
    message: str | None = None
    runtime_agent_tools: list | None = None


class Renderer:
    """Reusable Playwright browser. Not started until the first render."""

    def __init__(
        self,
        settings: Settings | None = None,
        lookup: LookupFn | None = None,
        preview_base_url: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.lookup = lookup
        self.preview_base_url = _loopback_preview_origin(preview_base_url)
        self._lock = threading.Lock()
        self._playwright = None
        self._browser = None
        self._contexts: list = []
        self._cursor = 0

    def render_html(
        self,
        html: str,
        *,
        url: str,
        raw_html_hash: str | None = None,
    ) -> RenderResult:
        """Render an HTML string (inline scripts run). No network fetch."""

        return self._render(
            url=url,
            raw_html_hash=raw_html_hash or html_hash(html),
            set_content=html,
            goto=None,
        )

    def render_url(self, url: str, *, raw_html_hash: str | None = None) -> RenderResult:
        try:
            parsed = parse_public_url(url)
        except Exception as exc:
            return _failed(url, "unavailable", str(exc), raw_html_hash)
        host = parsed.hostname or ""
        if self._is_allowed_preview(url):
            return self._render(
                url=url,
                raw_html_hash=raw_html_hash,
                set_content=None,
                goto=url,
                require_origin=self.preview_base_url,
            )
        if is_private_host(host, lookup=self.lookup):
            return _failed(
                url,
                "blocked",
                "The target resolves to a private or local network address.",
                raw_html_hash,
            )
        return self._render(url=url, raw_html_hash=raw_html_hash, set_content=None, goto=url)

    def _is_allowed_preview(self, url: str) -> bool:
        if not self.preview_base_url:
            return False
        return _origin(url) == self.preview_base_url

    def close(self) -> None:
        with self._lock:
            self._shutdown()

    def _ensure(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("The Playwright runtime is not installed.") from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        max_contexts = max(1, min(self.settings.playwright_max_contexts, 2))
        for _ in range(max_contexts):
            self._contexts.append(
                self._browser.new_context(
                    user_agent=self.settings.crawl_user_agent,
                    viewport={"width": 1280, "height": 900},
                )
            )

    def _context(self):
        context = self._contexts[self._cursor % len(self._contexts)]
        self._cursor += 1
        return context

    def _render(
        self,
        *,
        url: str,
        raw_html_hash: str | None,
        set_content: str | None,
        goto: str | None,
        require_origin: str | None = None,
    ) -> RenderResult:
        timeout_ms = int(self.settings.playwright_timeout_seconds * 1000)
        with self._lock:
            try:
                self._ensure()
            except Exception as exc:
                logger.warning("playwright unavailable: %s", exc)
                return _failed(url, "unavailable", str(exc), raw_html_hash)
            page = None
            console_errors: list[str] = []
            request_failures: list[str] = []
            try:
                from playwright.sync_api import TimeoutError as PlaywrightTimeout

                page = self._context().new_page()

                def on_console(message) -> None:
                    if message.type == "error" and len(console_errors) < 20:
                        console_errors.append(message.text)

                def on_request_failed(request) -> None:
                    if len(request_failures) < 20:
                        request_failures.append(f"{request.method} {request.url}")

                page.on("console", on_console)
                page.on("requestfailed", on_request_failed)
                if set_content is not None:
                    page.set_content(set_content, wait_until="domcontentloaded", timeout=timeout_ms)
                else:
                    assert goto is not None
                    page.goto(goto, wait_until="domcontentloaded", timeout=timeout_ms)
                    try:
                        page.wait_for_load_state(
                            "networkidle", timeout=min(5000, timeout_ms)
                        )
                    except PlaywrightTimeout:
                        pass
                    if require_origin and _origin(page.url) != require_origin:
                        return _failed(
                            url,
                            "unavailable",
                            f"preview navigated off-origin to {page.url}",
                            raw_html_hash,
                        )
                rendered_html = page.content()
                runtime_agent_tools = _read_runtime_tools(page)
                rendered_title = page.title() or None
                try:
                    rendered_text = page.inner_text("body")[:_MAX_RENDERED_TEXT]
                except Exception:
                    rendered_text = ""
                rendered_hash = html_hash(rendered_html)
                return RenderResult(
                    url=url,
                    state="observed",
                    rendered_html=rendered_html,
                    rendered_title=rendered_title,
                    rendered_dom_hash=rendered_hash,
                    rendered_text=rendered_text,
                    raw_html_hash=raw_html_hash,
                    raw_to_rendered_changed=(
                        raw_html_hash != rendered_hash if raw_html_hash else None
                    ),
                    console_errors=console_errors,
                    request_failures=request_failures,
                    runtime_agent_tools=runtime_agent_tools,
                )
            except PlaywrightTimeout:
                return _failed(
                    url,
                    "timeout",
                    "The browser-rendered page exceeded the bounded timeout.",
                    raw_html_hash,
                )
            except Exception as exc:
                timed_out = "timeout" in str(exc).lower()
                return _failed(
                    url,
                    "timeout" if timed_out else "unavailable",
                    str(exc),
                    raw_html_hash,
                )
            finally:
                if page is not None:
                    page.close()

    def _shutdown(self) -> None:
        for context in self._contexts:
            try:
                context.close()
            except Exception:
                pass
        self._contexts = []
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def __enter__(self) -> Renderer:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def apply_render(page: Page, result: RenderResult) -> Page:
    """Merge an observed render into the Common Website Model.

    Rendered title wins. When the rendered DOM differs from the raw HTML,
    headings, images, metadata, and word count are re-extracted from the
    rendered markup so client-hydrated pages are audited on what users
    (and crawlers that execute JS) actually see. A non-observed render
    leaves the page unchanged aside from recording the render hash fields.
    """

    updates: dict = {
        "rendered_dom_hash": result.rendered_dom_hash,
        "rendered_text": result.rendered_text,
    }
    source = page
    if (
        result.state == "observed"
        and result.rendered_html
        and result.raw_to_rendered_changed
    ):
        x_robots = page.robots.x_robots_tag if page.robots is not None else None
        extracted = extract_page(
            result.rendered_html,
            url=page.url,
            status_code=page.status_code,
            redirect_chain=list(page.redirect_chain),
            x_robots_tag=x_robots,
            sitemap_member=page.sitemap_member,
            crawl_depth=page.crawl_depth,
            discovery_source=page.discovery_source,
            fetch_ms=page.fetch_ms,
        ).page
        source = extracted.model_copy(
            update={
                "raw_html_hash": page.raw_html_hash,
                "status_code": page.status_code,
                "redirect_chain": list(page.redirect_chain),
                "sitemap_member": page.sitemap_member,
                "crawl_depth": page.crawl_depth,
                "discovery_source": page.discovery_source,
                "fetch_ms": page.fetch_ms,
            }
        )
    if result.state == "observed" and result.rendered_title:
        updates["title"] = result.rendered_title
    if result.state == "observed":
        updates["agent_tools"] = merge_agent_tools(
            list(source.agent_tools),
            tools_from_runtime(result.runtime_agent_tools),
        )
    return source.model_copy(update=updates)


_GET_TOOLS_JS = """
async () => {
  try {
    const mc = document.modelContext;
    if (!mc || typeof mc.getTools !== "function") return null;
    const tools = await mc.getTools();
    if (!Array.isArray(tools)) return null;
    return tools.filter((tool) => tool && typeof tool === "object").map((tool) => ({
      name: typeof tool.name === "string" ? tool.name : null,
      description: typeof tool.description === "string" ? tool.description : null,
      inputSchema: tool.inputSchema === undefined ? null : tool.inputSchema,
      annotations: tool.annotations && typeof tool.annotations === "object" ? {
        readOnlyHint: tool.annotations.readOnlyHint ?? null,
        untrustedContentHint: tool.annotations.untrustedContentHint ?? null,
        consequentialHint: tool.annotations.consequentialHint ?? null,
      } : null,
      outputSchema: tool.outputSchema === undefined ? null : tool.outputSchema,
    }));
  } catch (e) {
    return null;
  }
}
"""


def _read_runtime_tools(page) -> list | None:
    """Read registered tools. Never calls executeTool."""

    try:
        payload = page.evaluate(_GET_TOOLS_JS)
    except Exception:
        logger.info("webmcp getTools unavailable")
        return None
    if payload is None:
        return None
    return payload if isinstance(payload, list) else None


def _failed(
    url: str, state: str, message: str, raw_html_hash: str | None
) -> RenderResult:
    return RenderResult(
        url=url,
        state=state,
        raw_html_hash=raw_html_hash,
        message=message,
    )


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _loopback_preview_origin(preview_base_url: str | None) -> str | None:
    """Only 127.0.0.1 / localhost preview origins are allowed (no SSRF widen)."""

    if not preview_base_url:
        return None
    parts = urlsplit(preview_base_url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "http" or host not in {"127.0.0.1", "localhost"}:
        return None
    return _origin(preview_base_url)
