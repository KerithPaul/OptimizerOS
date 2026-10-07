"""Assemble a manager-facing site health report from stored evidence.

Numbers come from GSC rows, crawl stats, and the latest analysis run.
Missing inputs are named. Metric movement is correlation, not causation.
Indexability here is ArchitectOS crawl evidence, not Google Index Coverage.
"""

from __future__ import annotations

from collections import Counter
from html import escape
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finding import AnalysisRun, Finding, FindingStatus
from app.models.project import Project
from app.models.search import SearchConsoleDimension, SearchConsoleRow
from app.models.website import CrawlRun, Website
from app.retrieval.prioritize import prioritize_findings
from app.services.keyword_suggestions import load_query_rows, suggestions_from_query_rows
from app.services.measurement import CAUSATION_NOTE, CAUSATION_NOT_CLAIMED

INDEXABILITY_NOTE = (
    "These figures are ArchitectOS crawl evidence (fetched pages, sitemap "
    "membership, HTTP status). They are not Google Index Coverage."
)
TOP_N = 20
FINDING_LIMIT = 50
TOP_COUNTRIES = 10
PERIOD_WINDOWS = (
    ("day", "Last day vs prior day", 1),
    ("week", "Last 7 days vs prior 7 days", 7),
    ("four_weeks", "Last 28 days vs prior 28 days", 28),
    ("month", "Last 30 days vs prior 30 days", 30),
)
HIGHER_IS_BETTER = frozenset({"impressions", "clicks", "ctr", "crawlability"})
LOWER_IS_BETTER = frozenset({"position", "technical_errors"})


def _enum_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _metric_cell(metrics: dict[str, Any], key: str) -> dict[str, Any]:
    item = (metrics.get("metrics") or {}).get(key) or {}
    return {
        "key": key,
        "status": item.get("status"),
        "value": item.get("value"),
        "detail": item.get("detail"),
        "source": item.get("source"),
    }


def _gsc_rows(
    db: Session,
    project_id: int,
    *,
    analysis_run_id: int | None,
    dimension: SearchConsoleDimension,
    limit: int,
) -> list[dict[str, Any]]:
    stmt = select(SearchConsoleRow).where(
        SearchConsoleRow.project_id == project_id,
        SearchConsoleRow.dimension == dimension,
    )
    if analysis_run_id is not None:
        stmt = stmt.where(SearchConsoleRow.analysis_run_id == analysis_run_id)
    stmt = stmt.order_by(SearchConsoleRow.impressions.desc(), SearchConsoleRow.id.desc()).limit(
        limit
    )
    rows = list(db.scalars(stmt))
    return [
        {
            "page": row.page,
            "query": row.query,
            "impressions": row.impressions,
            "clicks": row.clicks,
            "ctr": row.ctr,
            "position": row.position,
            "start_date": row.start_date.isoformat() if row.start_date else None,
            "end_date": row.end_date.isoformat() if row.end_date else None,
        }
        for row in rows
    ]


def _indexability(crawl: CrawlRun | None, metrics: dict[str, Any]) -> dict[str, Any]:
    stats = crawl.stats_json if crawl is not None and isinstance(crawl.stats_json, dict) else {}
    nested = stats.get("crawl") if isinstance(stats.get("crawl"), dict) else stats
    sitemap = nested.get("sitemap") if isinstance(nested, dict) else None
    sitemap_count = None
    if isinstance(sitemap, dict) and isinstance(sitemap.get("page_url_count"), int):
        sitemap_count = sitemap["page_url_count"]
    page_count = nested.get("page_count") if isinstance(nested, dict) else None
    crawl_metrics = metrics.get("metrics") or {}
    return {
        "note": INDEXABILITY_NOTE,
        "crawl_status": _enum_value(crawl.status) if crawl is not None else None,
        "cap_reason": crawl.cap_reason if crawl is not None else None,
        "crawled_pages": page_count,
        "sitemap_url_count": sitemap_count,
        "crawlability": crawl_metrics.get("crawlability"),
        "technical_errors": crawl_metrics.get("technical_errors"),
    }


def _finding_summaries(findings: list[Finding]) -> list[dict[str, Any]]:
    by_id = {finding.finding_id: finding for finding in findings}
    ranked = prioritize_findings(findings)
    out: list[dict[str, Any]] = []
    for item in ranked[:FINDING_LIMIT]:
        finding = by_id.get(item.finding_id)
        if finding is None:
            continue
        out.append(
            {
                "finding_id": finding.finding_id,
                "rule": finding.rule,
                "category": _enum_value(finding.category),
                "severity": _enum_value(finding.severity),
                "status": _enum_value(finding.status),
                "affected_url": finding.affected_url or finding.affected_resource,
                "problem": finding.problem,
                "recommendation": finding.recommendation,
                "impressions": finding.impressions,
                "priority": item.priority,
                "actionability": finding.actionability,
            }
        )
    return out


def _keyword_summaries(db: Session, project_id: int, analysis_run_id: int | None) -> list[dict[str, Any]]:
    rows = load_query_rows(db, project_id, analysis_run_id=analysis_run_id)
    suggestions = suggestions_from_query_rows(rows)[:TOP_N]
    return [
        {
            "query": item.query,
            "impressions": item.impressions,
            "clicks": item.clicks,
            "ctr": item.ctr,
            "position": item.position,
            "reason": item.reason,
            "source": item.source,
        }
        for item in suggestions
    ]


def _dimension_rows(
    db: Session,
    project_id: int,
    *,
    analysis_run_id: int | None,
    dimension: SearchConsoleDimension,
) -> list[SearchConsoleRow]:
    stmt = select(SearchConsoleRow).where(
        SearchConsoleRow.project_id == project_id,
        SearchConsoleRow.dimension == dimension,
    )
    if analysis_run_id is not None:
        stmt = stmt.where(SearchConsoleRow.analysis_run_id == analysis_run_id)
    if dimension is SearchConsoleDimension.DATE:
        stmt = stmt.order_by(SearchConsoleRow.start_date.asc(), SearchConsoleRow.id.asc())
    else:
        stmt = stmt.order_by(SearchConsoleRow.impressions.desc(), SearchConsoleRow.id.desc())
    return list(db.scalars(stmt))


def _previous_analysis_run_id(
    db: Session, project_id: int, current_run_id: int | None
) -> int | None:
    stmt = select(func.max(SearchConsoleRow.analysis_run_id)).where(
        SearchConsoleRow.project_id == project_id,
        SearchConsoleRow.dimension == SearchConsoleDimension.QUERY,
        SearchConsoleRow.analysis_run_id.is_not(None),
    )
    if current_run_id is not None:
        stmt = stmt.where(SearchConsoleRow.analysis_run_id != current_run_id)
    return db.scalar(stmt)


def daily_series_from_rows(rows: list[SearchConsoleRow]) -> list[dict[str, Any]]:
    by_day: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.start_date is None:
            continue
        key = row.start_date.isoformat()
        by_day[key] = {
            "date": key,
            "impressions": int(row.impressions or 0),
            "clicks": int(row.clicks or 0),
            "ctr": float(row.ctr or 0.0),
            "position": float(row.position or 0.0),
        }
    return [by_day[key] for key in sorted(by_day)]


def _aggregate_points(points: list[dict[str, Any]]) -> dict[str, float | int]:
    impressions = sum(int(point.get("impressions") or 0) for point in points)
    clicks = sum(int(point.get("clicks") or 0) for point in points)
    ctr = (clicks / impressions) if impressions else 0.0
    weighted = sum(
        float(point.get("position") or 0.0) * int(point.get("impressions") or 0) for point in points
    )
    position = (weighted / impressions) if impressions else 0.0
    return {
        "impressions": impressions,
        "clicks": clicks,
        "ctr": ctr,
        "position": position,
    }


def period_comparisons(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Calendar windows from daily GSC rows. Missing history is named, not invented."""

    ordered = [point for point in daily if point.get("date")]
    out: list[dict[str, Any]] = []
    for key, label, length in PERIOD_WINDOWS:
        need = length * 2
        if len(ordered) < need:
            out.append(
                {
                    "key": key,
                    "label": label,
                    "status": "unavailable",
                    "detail": f"need {need} daily Search Console rows, have {len(ordered)}",
                    "length": length,
                }
            )
            continue
        current_points = ordered[-length:]
        previous_points = ordered[-need:-length]
        current = _aggregate_points(current_points)
        previous = _aggregate_points(previous_points)
        out.append(
            {
                "key": key,
                "label": label,
                "status": "observed",
                "length": length,
                "current_start": current_points[0]["date"],
                "current_end": current_points[-1]["date"],
                "previous_start": previous_points[0]["date"],
                "previous_end": previous_points[-1]["date"],
                "current": current,
                "previous": previous,
                "delta": {
                    metric: current[metric] - previous[metric] for metric in current
                },
            }
        )
    return out


def _movement_tone(metric: str, delta: float | int | None) -> str:
    if delta is None or delta == 0:
        return "flat"
    if metric in LOWER_IS_BETTER:
        return "up" if delta < 0 else "down"
    if metric in HIGHER_IS_BETTER:
        return "up" if delta > 0 else "down"
    return "flat"


def attach_movement(
    rows: list[dict[str, Any]],
    previous_rows: list[dict[str, Any]],
    key_field: str,
) -> list[dict[str, Any]]:
    previous_map = {
        str(row.get(key_field)): row for row in previous_rows if row.get(key_field)
    }
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        key = row.get(key_field)
        prior = previous_map.get(str(key)) if key else None
        if not previous_rows:
            item["movement"] = "unknown"
            out.append(item)
            continue
        if prior is None:
            item["movement"] = "new"
            item["delta_impressions"] = None
            item["delta_clicks"] = None
            item["delta_ctr"] = None
            item["delta_position"] = None
            out.append(item)
            continue
        di = int(row.get("impressions") or 0) - int(prior.get("impressions") or 0)
        dc = int(row.get("clicks") or 0) - int(prior.get("clicks") or 0)
        dctr = float(row.get("ctr") or 0) - float(prior.get("ctr") or 0)
        dpos = float(row.get("position") or 0) - float(prior.get("position") or 0)
        item["delta_impressions"] = di
        item["delta_clicks"] = dc
        item["delta_ctr"] = dctr
        item["delta_position"] = dpos
        if di > 0:
            item["movement"] = "up"
        elif di < 0:
            item["movement"] = "down"
        else:
            item["movement"] = "flat"
        out.append(item)
    return out


def _breakdown_rows(rows: list[SearchConsoleRow], *, label_field: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        label = row.query if label_field == "query" else row.page
        out.append(
            {
                "label": label,
                "impressions": row.impressions,
                "clicks": row.clicks,
                "ctr": row.ctr,
                "position": row.position,
            }
        )
    return out


def assemble_document(
    db: Session,
    *,
    project: Project,
    analysis_run: AnalysisRun | None,
    crawl: CrawlRun | None,
    website: Website | None,
    metrics: dict[str, Any],
    deltas: dict[str, Any] | None,
    gaps: list[dict[str, str]],
) -> dict[str, Any]:
    findings: list[Finding] = []
    if analysis_run is not None:
        findings = list(
            db.scalars(select(Finding).where(Finding.analysis_run_id == analysis_run.id))
        )
    analysis_run_id = analysis_run.id if analysis_run is not None else None
    gsc_pages = _gsc_rows(
        db, project.id, analysis_run_id=analysis_run_id, dimension=SearchConsoleDimension.PAGE, limit=TOP_N
    )
    gsc_queries = _gsc_rows(
        db,
        project.id,
        analysis_run_id=analysis_run_id,
        dimension=SearchConsoleDimension.QUERY,
        limit=TOP_N,
    )
    period = None
    if gsc_pages:
        period = {"start_date": gsc_pages[0]["start_date"], "end_date": gsc_pages[0]["end_date"]}
    elif gsc_queries:
        period = {"start_date": gsc_queries[0]["start_date"], "end_date": gsc_queries[0]["end_date"]}
    open_findings = [f for f in findings if f.status is FindingStatus.OPEN]
    previous_run_id = _previous_analysis_run_id(db, project.id, analysis_run_id)
    previous_queries = (
        _gsc_rows(
            db,
            project.id,
            analysis_run_id=previous_run_id,
            dimension=SearchConsoleDimension.QUERY,
            limit=500,
        )
        if previous_run_id is not None
        else []
    )
    previous_pages = (
        _gsc_rows(
            db,
            project.id,
            analysis_run_id=previous_run_id,
            dimension=SearchConsoleDimension.PAGE,
            limit=500,
        )
        if previous_run_id is not None
        else []
    )
    daily = daily_series_from_rows(
        _dimension_rows(
            db, project.id, analysis_run_id=analysis_run_id, dimension=SearchConsoleDimension.DATE
        )
    )
    devices = _breakdown_rows(
        _dimension_rows(
            db, project.id, analysis_run_id=analysis_run_id, dimension=SearchConsoleDimension.DEVICE
        ),
        label_field="query",
    )
    countries = _breakdown_rows(
        _dimension_rows(
            db, project.id, analysis_run_id=analysis_run_id, dimension=SearchConsoleDimension.COUNTRY
        )[:TOP_COUNTRIES],
        label_field="query",
    )
    keywords = attach_movement(
        _keyword_summaries(db, project.id, analysis_run_id),
        previous_queries,
        "query",
    )
    return {
        "project_id": project.id,
        "project_name": project.name,
        "website_url": website.url if website is not None else None,
        "period": period,
        "causation": CAUSATION_NOT_CLAIMED,
        "causation_note": CAUSATION_NOTE,
        "search_console": metrics.get("search_console"),
        "totals": {
            "impressions": _metric_cell(metrics, "impressions"),
            "clicks": _metric_cell(metrics, "clicks"),
            "ctr": _metric_cell(metrics, "ctr"),
            "position": _metric_cell(metrics, "position"),
        },
        "onsite": {
            "crawlability": _metric_cell(metrics, "crawlability"),
            "technical_errors": _metric_cell(metrics, "technical_errors"),
            "structured_data_validity": _metric_cell(metrics, "structured_data_validity"),
            "page_performance": _metric_cell(metrics, "page_performance"),
        },
        "deltas": deltas,
        "period_comparisons": period_comparisons(daily),
        "daily_series": daily,
        "devices": devices,
        "countries": countries,
        "scores": analysis_run.scores_json if analysis_run is not None else None,
        "indexability": _indexability(crawl, metrics),
        "top_pages": attach_movement(gsc_pages, previous_pages, "page"),
        "top_queries": attach_movement(gsc_queries, previous_queries, "query"),
        "findings": _finding_summaries(findings),
        "finding_count": len(findings),
        "open_finding_count": len(open_findings),
        "findings_by_severity": dict(Counter(_enum_value(item.severity) for item in findings)),
        "findings_by_category": dict(Counter(_enum_value(item.category) for item in findings)),
        "keywords": keywords,
        "previous_analysis_run_id": previous_run_id,
        "gaps": gaps,
        "analysis_run_id": analysis_run_id,
        "analysis_run_status": _enum_value(analysis_run.status) if analysis_run is not None else None,
        "crawl_run_id": crawl.id if crawl is not None else None,
    }


def _fmt_number(value: object) -> str:
    if isinstance(value, float):
        if value < 1:
            return f"{value:.4f}".rstrip("0").rstrip(".")
        return f"{value:.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return "—"


def _fmt_metric(cell: dict[str, Any]) -> str:
    if cell.get("status") != "measured":
        return escape(str(cell.get("detail") or cell.get("status") or "unavailable"))
    return escape(_fmt_number(cell.get("value")))


def _fmt_ctr_value(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value * 100:.2f}%"


def _fmt_ctr(cell: dict[str, Any]) -> str:
    if cell.get("status") != "measured":
        return escape(str(cell.get("detail") or cell.get("status") or "unavailable"))
    return escape(_fmt_ctr_value(cell.get("value")))


def _fmt_signed(value: object, *, percent: bool = False, position: bool = False) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    if percent:
        text = f"{value * 100:+.2f}pp"
    elif position:
        text = f"{value:+.2f}"
    elif isinstance(value, float) and abs(value) < 1:
        text = f"{value:+.4f}".rstrip("0").rstrip(".")
    else:
        sign = "+" if value > 0 else ""
        text = f"{sign}{_fmt_number(value)}"
    return text


def _fmt_delta(deltas: dict[str, Any] | None, key: str) -> str:
    if not deltas:
        return "—"
    item = (deltas.get("observed_delta") or {}).get(key) or {}
    if item.get("status") != "observed":
        return "—"
    delta = item.get("delta")
    if not isinstance(delta, (int, float)):
        return "—"
    if key == "ctr":
        return escape(_fmt_signed(delta, percent=True))
    if key == "position":
        return escape(_fmt_signed(delta, position=True))
    return escape(_fmt_signed(delta))


def _delta_class(metric: str, delta: object) -> str:
    if not isinstance(delta, (int, float)) or delta == 0:
        return "flat"
    return _movement_tone(metric, delta)


def _kpi_card(label: str, value_html: str, delta_html: str, tone: str) -> str:
    return (
        f'<div class="kpi {escape(tone)}"><div class="kpi-label">{escape(label)}</div>'
        f'<div class="kpi-value">{value_html}</div>'
        f'<div class="kpi-delta">{delta_html}</div></div>'
    )


def _svg_line(daily: list[dict[str, Any]], metric: str, color: str) -> str:
    values = [float(point.get(metric) or 0) for point in daily]
    if len(values) < 2:
        return "<p class='muted'>Need at least two daily Search Console rows for this chart.</p>"
    width, height, pad = 640, 148, 18
    vmax = max(values)
    vmin = min(values)
    span = (vmax - vmin) or (vmax or 1.0)
    coords: list[tuple[float, float]] = []
    last_index = len(values) - 1
    for index, value in enumerate(values):
        x = pad + (width - 2 * pad) * index / last_index
        y = height - pad - (height - 2 * pad) * ((value - vmin) / span)
        coords.append((x, y))
    line = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    fill = (
        f"{line} L {coords[-1][0]:.1f},{height - pad:.1f} "
        f"L {coords[0][0]:.1f},{height - pad:.1f} Z"
    )
    start_label = escape(str(daily[0].get("date") or ""))
    end_label = escape(str(daily[-1].get("date") or ""))
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'role="img" aria-label="{escape(metric)} daily trend">'
        f'<rect width="{width}" height="{height}" fill="#f8fafc"/>'
        f'<path d="{fill}" fill="{color}" fill-opacity="0.14"/>'
        f'<path d="{line}" fill="none" stroke="{color}" stroke-width="2.4"/>'
        f'<circle cx="{coords[-1][0]:.1f}" cy="{coords[-1][1]:.1f}" r="3.5" fill="{color}"/>'
        f'<text x="{pad}" y="{height - 4}" font-size="11" fill="#64748b">{start_label}</text>'
        f'<text x="{width - pad}" y="{height - 4}" font-size="11" fill="#64748b" '
        f'text-anchor="end">{end_label}</text></svg>'
    )


def _svg_bars(rows: list[dict[str, Any]], label_key: str, value_key: str, color: str) -> str:
    usable = [row for row in rows if row.get(label_key)][:12]
    if not usable:
        return "<p class='muted'>None stored for this run.</p>"
    max_value = max(float(row.get(value_key) or 0) for row in usable) or 1.0
    bars = ""
    row_h = 22
    height = 16 + row_h * len(usable)
    width = 640
    label_w = 210
    for index, row in enumerate(usable):
        y = 8 + index * row_h
        value = float(row.get(value_key) or 0)
        bar_w = (width - label_w - 70) * (value / max_value)
        label = escape(str(row.get(label_key) or "")[:42])
        bars += (
            f'<text x="0" y="{y + 12}" font-size="11" fill="#334155">{label}</text>'
            f'<rect x="{label_w}" y="{y}" width="{bar_w:.1f}" height="14" rx="3" fill="{color}"/>'
            f'<text x="{label_w + bar_w + 6:.1f}" y="{y + 12}" font-size="11" fill="#0f172a">'
            f"{escape(_fmt_number(int(value) if value_key != 'ctr' else value))}</text>"
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'role="img" aria-label="bar chart">'
        f"{bars}</svg>"
    )


def _score_bar(label: str, value: object, max_value: object) -> str:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return f"<div class='score'><span>{escape(label)}</span><span>unavailable</span></div>"
    ceiling = float(max_value) if isinstance(max_value, (int, float)) and max_value else 100.0
    pct = max(0.0, min(100.0, 100.0 * numeric / ceiling))
    if pct >= 80:
        tone = "#059669"
    elif pct >= 60:
        tone = "#d97706"
    else:
        tone = "#dc2626"
    return (
        f'<div class="score"><div class="score-head"><span>{escape(label)}</span>'
        f"<strong>{escape(_fmt_number(int(numeric) if numeric.is_integer() else numeric))} / "
        f"{escape(_fmt_number(int(ceiling) if ceiling.is_integer() else ceiling))}</strong></div>"
        f'<div class="bar"><span style="width:{pct:.1f}%;background:{tone}"></span></div></div>'
    )


def _chip(text: str, tone: str) -> str:
    return f'<span class="chip {escape(tone)}">{escape(text)}</span>'


def render_html(document: dict[str, Any]) -> str:
    """Deterministic HTML for the dashboard and later email."""

    name = escape(str(document.get("project_name") or "Project"))
    url = document.get("website_url")
    url_html = f'<a href="{escape(url)}">{escape(url)}</a>' if url else "—"
    period = document.get("period") or {}
    period_html = "—"
    if period.get("start_date") and period.get("end_date"):
        period_html = f"{escape(str(period['start_date']))} to {escape(str(period['end_date']))}"
    totals = document.get("totals") or {}
    deltas = document.get("deltas") or {}
    observed = deltas.get("observed_delta") if isinstance(deltas, dict) else {}
    if not isinstance(observed, dict):
        observed = {}

    def _kpi_from_cell(label: str, key: str, cell: dict[str, Any], *, ctr: bool = False) -> str:
        item = observed.get(key) or {}
        delta = item.get("delta") if item.get("status") == "observed" else None
        tone = _delta_class(key, delta)
        value_html = _fmt_ctr(cell) if ctr else _fmt_metric(cell)
        if cell.get("status") != "measured":
            return _kpi_card(label, value_html, value_html, "flat")
        delta_html = _fmt_delta(deltas, key)
        delta_line = (
            f"{delta_html} vs previous report"
            if delta_html != "—"
            else "No prior report snapshot yet"
        )
        return _kpi_card(label, value_html, delta_line, tone)

    kpis = "".join(
        [
            _kpi_from_cell("Impressions", "impressions", totals.get("impressions") or {}),
            _kpi_from_cell("Clicks", "clicks", totals.get("clicks") or {}),
            _kpi_from_cell("CTR", "ctr", totals.get("ctr") or {}, ctr=True),
            _kpi_from_cell("Avg position", "position", totals.get("position") or {}),
        ]
    )

    scores = document.get("scores") or {}
    score_html = ""
    for key in ("technical_seo_health", "content_aeo_readiness", "ai_search_geo_readiness"):
        score = scores.get(key) or {}
        if not score:
            continue
        score_html += _score_bar(
            str(score.get("label") or key), score.get("value"), score.get("max_value") or 100
        )
    if not score_html:
        score_html = "<p class='muted'>No audit scores on this run</p>"

    def _table(headers: list[str], rows: list[list[str]]) -> str:
        head = "".join(f"<th>{escape(h)}</th>" for h in headers)
        body = ""
        if not rows:
            body = f"<tr><td colspan='{len(headers)}'>None stored for this run</td></tr>"
        else:
            for row in rows:
                body += "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    def _move_cell(row: dict[str, Any], field: str, *, percent: bool = False, position: bool = False) -> str:
        delta = row.get(field)
        metric = field.replace("delta_", "")
        tone = _delta_class(metric, delta) if isinstance(delta, (int, float)) else (
            "new" if row.get("movement") == "new" else "flat"
        )
        if row.get("movement") == "new":
            return _chip("new", "new")
        if delta is None:
            return "—"
        return f'<span class="delta {tone}">{escape(_fmt_signed(delta, percent=percent, position=position))}</span>'

    page_rows = [
        [
            escape(str(row.get("page") or "")),
            escape(_fmt_number(row.get("impressions") or 0)),
            _move_cell(row, "delta_impressions"),
            escape(_fmt_number(row.get("clicks") or 0)),
            escape(f"{float(row.get('ctr') or 0) * 100:.1f}%"),
            escape(f"{float(row.get('position') or 0):.1f}"),
        ]
        for row in (document.get("top_pages") or [])
    ]
    query_rows = [
        [
            escape(str(row.get("query") or "")),
            escape(_fmt_number(row.get("impressions") or 0)),
            _move_cell(row, "delta_impressions"),
            escape(_fmt_number(row.get("clicks") or 0)),
            _move_cell(row, "delta_clicks"),
            escape(f"{float(row.get('ctr') or 0) * 100:.1f}%"),
            _move_cell(row, "delta_ctr", percent=True),
            escape(f"{float(row.get('position') or 0):.1f}"),
            _move_cell(row, "delta_position", position=True),
        ]
        for row in (document.get("top_queries") or [])
    ]
    keyword_rows = [
        [
            escape(str(row.get("query") or "")),
            escape(_fmt_number(row.get("impressions") or 0)),
            escape(_fmt_number(row.get("clicks") or 0)),
            escape(f"{float(row.get('ctr') or 0) * 100:.1f}%"),
            escape(f"{float(row.get('position') or 0):.1f}"),
            _move_cell(row, "delta_impressions"),
            escape(str(row.get("reason") or "")),
        ]
        for row in (document.get("keywords") or [])
    ]
    finding_rows = [
        [
            _chip(str(row.get("severity") or ""), str(row.get("severity") or "flat").lower()),
            escape(str(row.get("rule") or "")),
            escape(str(row.get("affected_url") or "")),
            escape(str(row.get("problem") or "")),
            escape(_fmt_number(row["impressions"]) if row.get("impressions") is not None else "—"),
        ]
        for row in (document.get("findings") or [])
    ]

    comparison_rows: list[list[str]] = []
    for window in document.get("period_comparisons") or []:
        if window.get("status") != "observed":
            comparison_rows.append(
                [
                    escape(str(window.get("label") or window.get("key") or "")),
                    escape(str(window.get("detail") or "unavailable")),
                    "—",
                    "—",
                    "—",
                    "—",
                ]
            )
            continue
        delta = window.get("delta") or {}
        current = window.get("current") or {}
        span = f"{window.get('current_start')} → {window.get('current_end')}"
        position_now = float(current.get("position") or 0)
        comparison_rows.append(
            [
                f"{escape(str(window.get('label')))}<br/><span class='muted'>{escape(str(span))}</span>",
                (
                    f"{escape(_fmt_number(int(current.get('impressions') or 0)))} "
                    f"<span class='delta {_delta_class('impressions', delta.get('impressions'))}'>"
                    f"{escape(_fmt_signed(delta.get('impressions')))}</span>"
                ),
                (
                    f"{escape(_fmt_number(int(current.get('clicks') or 0)))} "
                    f"<span class='delta {_delta_class('clicks', delta.get('clicks'))}'>"
                    f"{escape(_fmt_signed(delta.get('clicks')))}</span>"
                ),
                (
                    f"{escape(_fmt_ctr_value(current.get('ctr')))} "
                    f"<span class='delta {_delta_class('ctr', delta.get('ctr'))}'>"
                    f"{escape(_fmt_signed(delta.get('ctr'), percent=True))}</span>"
                ),
                (
                    f"{escape(f'{position_now:.1f}')} "
                    f"<span class='delta {_delta_class('position', delta.get('position'))}'>"
                    f"{escape(_fmt_signed(delta.get('position'), position=True))}</span>"
                ),
                "observed",
            ]
        )

    onsite = document.get("onsite") or {}
    onsite_rows = [
        ["Crawlability (fetched / attempted)", _fmt_metric(onsite.get("crawlability") or {})],
        ["Technical errors (crawl)", _fmt_metric(onsite.get("technical_errors") or {})],
        ["Structured-data validity", _fmt_metric(onsite.get("structured_data_validity") or {})],
        ["Lighthouse performance (lab)", _fmt_metric(onsite.get("page_performance") or {})],
    ]

    daily = document.get("daily_series") or []
    impressions_chart = _svg_line(daily, "impressions", "#4f46e5")
    clicks_chart = _svg_line(daily, "clicks", "#059669")
    query_chart = _svg_bars(document.get("top_queries") or [], "query", "impressions", "#6366f1")
    device_chart = _svg_bars(document.get("devices") or [], "label", "impressions", "#0ea5e9")
    country_chart = _svg_bars(document.get("countries") or [], "label", "impressions", "#d97706")

    severity = document.get("findings_by_severity") or {}
    severity_html = "".join(
        _chip(f"{key}: {value}", str(key).lower()) for key, value in severity.items()
    ) or "<span class='muted'>No findings</span>"

    gaps = document.get("gaps") or []
    gap_html = (
        "<ul>"
        + "".join(
            f"<li><strong>{escape(str(g.get('capability')))}</strong>: {escape(str(g.get('detail')))}</li>"
            for g in gaps
        )
        + "</ul>"
        if gaps
        else "<p>No named gaps on this run.</p>"
    )
    indexability = document.get("indexability") or {}
    idx_crawl = escape(str(indexability.get("crawled_pages") if indexability.get("crawled_pages") is not None else "—"))
    idx_sitemap = escape(
        str(indexability.get("sitemap_url_count") if indexability.get("sitemap_url_count") is not None else "—")
    )
    idx_status = escape(str(indexability.get("crawl_status") or "—"))
    idx_cap = escape(str(indexability.get("cap_reason") or "none"))
    tech = indexability.get("technical_errors") or {}
    tech_value = tech.get("value") if isinstance(tech, dict) else None
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Site health report — {name}</title>
<style>
  body {{ font-family: "Segoe UI", system-ui, sans-serif; color: #0f172a; margin: 0; background: #eef2ff; }}
  .wrap {{ max-width: 980px; margin: 0 auto; padding: 28px 20px 48px; }}
  h1, h2 {{ font-weight: 700; letter-spacing: -0.02em; }}
  h1 {{ font-size: 28px; margin: 0 0 8px; }}
  h2 {{ font-size: 18px; margin: 28px 0 10px; }}
  a {{ color: #4338ca; }}
  .meta {{ color: #475569; font-size: 14px; line-height: 1.5; }}
  .note {{ color: #475569; font-size: 13px; }}
  .warn {{ background: #fff7ed; border: 1px solid #fdba74; padding: 10px 12px; border-radius: 8px; }}
  .kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 18px 0; }}
  .kpi {{ background: #fff; border-radius: 12px; padding: 14px; border: 1px solid #e2e8f0; }}
  .kpi.up {{ border-top: 4px solid #059669; }}
  .kpi.down {{ border-top: 4px solid #dc2626; }}
  .kpi.flat {{ border-top: 4px solid #94a3b8; }}
  .kpi-label {{ font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: .04em; }}
  .kpi-value {{ font-size: 26px; font-weight: 700; margin: 6px 0 4px; }}
  .kpi-delta {{ font-size: 12px; color: #475569; }}
  .panel {{ background: #fff; border-radius: 12px; padding: 14px 16px; border: 1px solid #e2e8f0; margin-bottom: 12px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 8px 0 4px; }}
  th, td {{ border-bottom: 1px solid #e2e8f0; padding: 7px 8px; text-align: left; vertical-align: top; font-size: 13px; }}
  th {{ background: #f8fafc; color: #334155; font-weight: 600; }}
  .muted {{ color: #64748b; font-size: 12px; }}
  .delta.up {{ color: #047857; font-weight: 600; }}
  .delta.down {{ color: #b91c1c; font-weight: 600; }}
  .delta.flat {{ color: #64748b; }}
  .chip {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; margin: 0 4px 4px 0; }}
  .chip.up, .chip.new {{ background: #d1fae5; color: #065f46; }}
  .chip.down, .chip.high, .chip.critical {{ background: #fee2e2; color: #991b1b; }}
  .chip.flat, .chip.medium {{ background: #fef3c7; color: #92400e; }}
  .chip.low, .chip.info {{ background: #e0e7ff; color: #3730a3; }}
  .score {{ margin: 8px 0 14px; }}
  .score-head {{ display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 4px; }}
  .bar {{ height: 10px; background: #e2e8f0; border-radius: 99px; overflow: hidden; }}
  .bar span {{ display: block; height: 10px; }}
  @media (max-width: 800px) {{ .kpis {{ grid-template-columns: 1fr 1fr; }} }}
</style>
</head>
<body>
<div class="wrap">
<h1>Site health report — {name}</h1>
<p class="meta">Website: {url_html}<br/>
Search Console 28-day totals: {period_html}<br/>
Search Console: {escape(str(document.get("search_console") or "unavailable"))}<br/>
Audit run: {escape(str(document.get("analysis_run_id") or "none"))}
 ({escape(str(document.get("analysis_run_status") or "none"))})</p>
<p class="note">{escape(CAUSATION_NOTE)}</p>
<div class="kpis">{kpis}</div>
<h2>Day, week, and month comparisons</h2>
<p class="note">Windows are summed from daily Google Search Console rows. Green means the metric moved in the helpful direction (higher impressions, clicks, and CTR; lower average position). This is observed movement, not a ranking claim.</p>
{_table(["Window", "Impressions", "Clicks", "CTR", "Avg position", "Status"], comparison_rows)}
<h2>Daily impressions</h2>
<div class="panel">{impressions_chart}</div>
<h2>Daily clicks</h2>
<div class="panel">{clicks_chart}</div>
<h2>On-site scores</h2>
<div class="panel">{score_html}</div>
<h2>Indexability (crawl evidence)</h2>
<p class="warn">{escape(INDEXABILITY_NOTE)}</p>
<p>Crawl status: {idx_status}. Crawled pages: {idx_crawl}. Sitemap URLs: {idx_sitemap}. Cap: {idx_cap}. Technical errors: {escape(_fmt_number(tech_value) if tech_value is not None else "—")}.</p>
{_table(["On-site metric", "Value"], onsite_rows)}
<h2>Top pages by impressions</h2>
{_table(["Page", "Impressions", "Δ impressions", "Clicks", "CTR", "Position"], page_rows)}
<h2>Keywords (top queries)</h2>
<div class="panel">{query_chart}</div>
{_table(["Query", "Impressions", "Δ imp.", "Clicks", "Δ clicks", "CTR", "Δ CTR", "Position", "Δ pos."], query_rows)}
<h2>Observed GSC query opportunities</h2>
<p class="note">Queries already in Search Console with position ≥ 8 or CTR below 2%. ArchitectOS does not invent volume or keyword difficulty.</p>
{_table(["Query", "Impressions", "Clicks", "CTR", "Position", "Δ impressions", "Why listed"], keyword_rows)}
<h2>Devices</h2>
<div class="panel">{device_chart}</div>
<h2>Countries (top 10 by impressions)</h2>
<div class="panel">{country_chart}</div>
<h2>Findings ({escape(str(document.get("finding_count") or 0))} total, {escape(str(document.get("open_finding_count") or 0))} open)</h2>
<p>{severity_html}</p>
{_table(["Severity", "Rule", "URL", "Problem", "GSC impressions"], finding_rows)}
<h2>Gaps</h2>
{gap_html}
</div>
</body>
</html>
"""

