"""SMTP delivery for stored site health reports.

Credentials come from Settings. The password is never logged or written
onto SiteReport.email_detail.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr
from typing import Any, Sequence

from app.core.config import Settings, get_settings
from app.models.report import SiteReportEmailStatus

logger = logging.getLogger("architectos.mailer")

_MAX_DETAIL = 512


@dataclass(frozen=True)
class DeliveryResult:
    status: SiteReportEmailStatus
    detail: str
    recipients: tuple[str, ...] = ()


def parse_recipients(raw: str | Sequence[str] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = raw.replace(";", ",").split(",")
    else:
        parts = []
        for item in raw:
            parts.extend(str(item).replace(";", ",").split(","))
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        address = parseaddr(part.strip())[1].strip()
        if not address or "@" not in address:
            continue
        key = address.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(address)
    return out


def _plain_summary(document: dict[str, Any]) -> str:
    totals = document.get("totals") if isinstance(document.get("totals"), dict) else {}

    def cell(key: str) -> str:
        item = totals.get(key) if isinstance(totals.get(key), dict) else {}
        if item.get("status") == "measured" and item.get("value") is not None:
            value = item["value"]
            if key == "ctr":
                return f"{float(value) * 100:.2f}%"
            if key == "position":
                return f"{float(value):.1f}"
            if isinstance(value, (int, float)):
                return f"{int(value):,}"
            return str(value)
        return str(item.get("detail") or item.get("status") or "unavailable")

    lines = [
        "ArchitectOS site health report",
        f"Project: {document.get('project_name') or '-'}",
        f"Website: {document.get('website_url') or '-'}",
        f"Search Console: {document.get('search_console') or '-'}",
        f"Impressions: {cell('impressions')}",
        f"Clicks: {cell('clicks')}",
        f"CTR: {cell('ctr')}",
        f"Average position: {cell('position')}",
        f"Open findings: {document.get('open_finding_count') if document.get('open_finding_count') is not None else '-'}",
        "",
        "Charts, keyword tables, and day/week/month comparisons are in the HTML part of this email.",
        "Metric movement is correlation, not a ranking claim.",
        "Crawl figures are not Google Index Coverage.",
    ]
    return "\n".join(lines)


def _clip(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= _MAX_DETAIL:
        return text
    return text[: _MAX_DETAIL - 3] + "..."


def deliver_site_report(
    *,
    html: str | None,
    document: dict[str, Any] | None,
    recipients: Sequence[str] | None = None,
    settings: Settings | None = None,
) -> DeliveryResult:
    settings = settings or get_settings()
    document = document if isinstance(document, dict) else {}
    to_list = parse_recipients(recipients)
    if not settings.smtp_is_configured:
        return DeliveryResult(
            SiteReportEmailStatus.UNAVAILABLE,
            "SMTP is not configured; the report is stored in ArchitectOS",
        )
    if not to_list:
        return DeliveryResult(
            SiteReportEmailStatus.UNAVAILABLE,
            "no recipients; set SITE_REPORT_EMAIL_TO or schedule email_to",
        )
    if not html:
        return DeliveryResult(
            SiteReportEmailStatus.UNAVAILABLE,
            "report HTML is empty; nothing to send",
        )

    from_addr = settings.smtp_from_address
    if not from_addr:
        return DeliveryResult(
            SiteReportEmailStatus.UNAVAILABLE,
            "SMTP_FROM and SMTP_USER are empty",
        )

    project_name = str(document.get("project_name") or "project")
    website = str(document.get("website_url") or "")
    subject = f"ArchitectOS site health report — {project_name}"
    if website:
        subject = f"{subject} — {website}"

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = formataddr(("ArchitectOS", from_addr))
    message["To"] = ", ".join(to_list)
    message.attach(MIMEText(_plain_summary(document), "plain", "utf-8"))
    message.attach(MIMEText(html, "html", "utf-8"))
    payload = message.as_string()

    try:
        _send_smtp(settings, from_addr, to_list, payload)
    except Exception as exc:
        logger.exception("site report email failed")
        return DeliveryResult(
            SiteReportEmailStatus.FAILED,
            _clip(f"SMTP send failed: {exc}"),
            recipients=tuple(to_list),
        )

    logger.info("site report email sent recipients=%s", len(to_list))
    return DeliveryResult(
        SiteReportEmailStatus.SENT,
        _clip("sent to " + ", ".join(to_list)),
        recipients=tuple(to_list),
    )


def _send_smtp(settings: Settings, from_addr: str, to_list: list[str], payload: str) -> None:
    password = settings.smtp_password.replace(" ", "")
    context = ssl.create_default_context()
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
            context=context,
        ) as client:
            client.login(settings.smtp_user, password)
            client.sendmail(from_addr, to_list, payload)
        return
    with smtplib.SMTP(
        settings.smtp_host,
        settings.smtp_port,
        timeout=settings.smtp_timeout_seconds,
    ) as client:
        client.ehlo()
        client.starttls(context=context)
        client.ehlo()
        client.login(settings.smtp_user, password)
        client.sendmail(from_addr, to_list, payload)
