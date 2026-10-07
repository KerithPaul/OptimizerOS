"""Lighthouse lab runner — checkpoint 3.C.3.

Lazy: invoked only when requested, then discarded. Results are raw lab
signals. It is forbidden to present the Lighthouse SEO score as a Google
ranking or to fold it into an ArchitectOS score without a Phase 4 rule.

Every record, including failures, carries provenance
`lab_signal_not_ranking`.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.config import Settings, get_settings
from app.intelligence.website.fetch import LookupFn, is_private_host, parse_public_url

logger = logging.getLogger("architectos.intelligence.website.lighthouse")

PROVENANCE = "lab_signal_not_ranking"
PROVENANCE_LABEL = "lab signal, not ranking"

LighthouseRunner = Callable[[str], dict]


@dataclass
class LabSignal:
    url: str
    state: str
    provenance: str
    provenance_label: str
    categories: dict[str, float | None] = field(default_factory=dict)
    audits: list[dict] = field(default_factory=list)
    raw: dict | None = None
    message: str | None = None


def run_lighthouse(
    url: str,
    *,
    settings: Settings | None = None,
    lookup: LookupFn | None = None,
    runner: LighthouseRunner | None = None,
) -> LabSignal:
    """Run Lighthouse (or `runner`) and label the result as a lab signal."""

    settings = settings or get_settings()
    if not settings.lighthouse_enabled:
        return _labelled(
            url,
            state="unavailable",
            message="Lighthouse is disabled (LIGHTHOUSE_ENABLED=false).",
        )
    try:
        parsed = parse_public_url(url)
    except Exception as exc:
        return _labelled(url, state="unavailable", message=str(exc))
    host = parsed.hostname or ""
    if is_private_host(host, lookup=lookup):
        return _labelled(
            url,
            state="blocked",
            message="The target resolves to a private or local network address.",
        )
    try:
        raw = runner(url) if runner is not None else _invoke_cli(url)
    except FileNotFoundError as exc:
        return _labelled(url, state="unavailable", message=str(exc))
    except subprocess.TimeoutExpired:
        return _labelled(
            url,
            state="timeout",
            message="Lighthouse exceeded the bounded timeout.",
        )
    except Exception as exc:
        logger.warning("lighthouse failed: %s", exc)
        timed_out = "timeout" in str(exc).lower()
        return _labelled(
            url,
            state="timeout" if timed_out else "unavailable",
            message=str(exc),
        )
    return label_lighthouse_result(url, raw)


def label_lighthouse_result(url: str, raw: dict) -> LabSignal:
    """Attach the required provenance flag to a Lighthouse JSON result."""

    categories_in = raw.get("categories") or {}
    categories: dict[str, float | None] = {}
    for key in ("performance", "accessibility", "best-practices", "seo"):
        entry = categories_in.get(key) or {}
        score = entry.get("score")
        categories[key] = score if isinstance(score, (int, float)) else None
    audits_in = raw.get("audits") or {}
    audits: list[dict] = []
    for audit_id, audit in list(audits_in.items())[:80]:
        if not isinstance(audit, dict):
            continue
        audits.append(
            {
                "id": audit.get("id") or audit_id,
                "title": audit.get("title"),
                "score": audit.get("score"),
                "displayValue": audit.get("displayValue"),
            }
        )
    return _labelled(
        url,
        state="observed",
        categories=categories,
        audits=audits,
        raw=raw,
    )


def lab_signal_to_record(signal: LabSignal) -> dict:
    """Persistable labelled signal. Omits the bulky raw Lighthouse JSON."""

    return {
        "url": signal.url,
        "state": signal.state,
        "provenance": signal.provenance,
        "provenance_label": signal.provenance_label,
        "categories": signal.categories,
        "audits": signal.audits,
        "message": signal.message,
    }


def _labelled(
    url: str,
    *,
    state: str,
    categories: dict[str, float | None] | None = None,
    audits: list[dict] | None = None,
    raw: dict | None = None,
    message: str | None = None,
) -> LabSignal:
    return LabSignal(
        url=url,
        state=state,
        provenance=PROVENANCE,
        provenance_label=PROVENANCE_LABEL,
        categories=categories or {},
        audits=audits or [],
        raw=raw,
        message=message,
    )


def _invoke_cli(url: str) -> dict:
    binary = shutil.which("lighthouse")
    if binary is None:
        raise FileNotFoundError(
            "Lighthouse CLI is not installed; lab signals are unavailable."
        )
    completed = subprocess.run(
        [
            binary,
            url,
            "--output=json",
            "--quiet",
            "--only-categories=performance,accessibility,best-practices,seo",
            "--chrome-flags=--headless --no-sandbox",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip() or f"lighthouse exited {completed.returncode}"
        )
    return json.loads(completed.stdout)
