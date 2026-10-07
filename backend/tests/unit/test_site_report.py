"""Site health report assembly is deterministic and does not invent metrics."""

from app.jobs.registry import get_handler
from app.services.measurement import CAUSATION_NOTE
from app.services.site_report import INDEXABILITY_NOTE, period_comparisons, render_html


def test_site_report_handler_is_registered() -> None:
    import app.jobs.handlers  # noqa: F401

    assert get_handler("site_report") is not None


def test_render_html_includes_measured_gsc_and_indexability_disclaimer() -> None:
    html = render_html(
        {
            "project_name": "drmoksha",
            "website_url": "https://www.drmoksha.com/",
            "period": {"start_date": "2026-09-02", "end_date": "2026-09-29"},
            "search_console": "Search Console: attached",
            "analysis_run_id": 142,
            "analysis_run_status": "partial",
            "causation_note": CAUSATION_NOTE,
            "totals": {
                "impressions": {"status": "measured", "value": 12000},
                "clicks": {"status": "measured", "value": 80},
                "ctr": {"status": "measured", "value": 0.0067},
                "position": {"status": "measured", "value": 18.2},
            },
            "deltas": {
                "observed_delta": {
                    "impressions": {"status": "observed", "delta": -1500},
                    "clicks": {"status": "observed", "delta": -10},
                    "ctr": {"status": "unavailable"},
                    "position": {"status": "observed", "delta": 1.2},
                }
            },
            "scores": {
                "technical_seo_health": {
                    "label": "Technical SEO Health",
                    "value": 72,
                    "max_value": 100,
                }
            },
            "indexability": {
                "crawled_pages": 21,
                "sitemap_url_count": 40,
                "crawl_status": "partial",
                "cap_reason": None,
                "technical_errors": {"status": "measured", "value": 2},
            },
            "top_pages": [
                {
                    "page": "https://www.drmoksha.com/",
                    "impressions": 4000,
                    "clicks": 40,
                    "ctr": 0.01,
                    "position": 8.0,
                }
            ],
            "top_queries": [
                {
                    "query": "corporate lawyer",
                    "impressions": 900,
                    "clicks": 4,
                    "ctr": 0.004,
                    "position": 22.0,
                }
            ],
            "findings": [
                {
                    "severity": "medium",
                    "rule": "SEO-METADESC-LENGTH-001",
                    "affected_url": "https://www.drmoksha.com/",
                    "problem": "Meta description is longer than 160 characters.",
                    "impressions": 4000,
                }
            ],
            "finding_count": 1,
            "open_finding_count": 1,
            "keywords": [
                {
                    "query": "corporate lawyer",
                    "reason": "900 impressions, position 22.0, CTR 0.4%",
                }
            ],
            "gaps": [{"capability": "crawl_coverage", "detail": "evaluated 2 pages"}],
        }
    )
    assert "12,000" in html
    assert "Technical SEO Health" in html
    assert INDEXABILITY_NOTE in html
    assert "not Google Index Coverage" in html
    assert CAUSATION_NOTE in html
    assert "corporate lawyer" in html
    assert "SEO-METADESC-LENGTH-001" in html
    assert "-1,500" in html
    assert "unavailable" not in html.split("Impressions")[1][:200]


def test_period_comparisons_need_enough_daily_rows() -> None:
    daily = [
        {
            "date": f"2026-08-{day:02d}",
            "impressions": 100 + day,
            "clicks": 2,
            "ctr": 0.02,
            "position": 10.0,
        }
        for day in range(1, 15)
    ]
    windows = {item["key"]: item for item in period_comparisons(daily)}
    assert windows["day"]["status"] == "observed"
    assert windows["week"]["status"] == "observed"
    assert windows["week"]["current"]["impressions"] == sum(100 + day for day in range(8, 15))
    assert windows["month"]["status"] == "unavailable"
    assert "need 60" in windows["month"]["detail"]


def test_render_html_includes_charts_and_keyword_metrics() -> None:
    daily = [
        {
            "date": f"2026-08-{day:02d}",
            "impressions": 100 * day,
            "clicks": day,
            "ctr": 0.01,
            "position": 12.0,
        }
        for day in range(1, 15)
    ]
    html = render_html(
        {
            "project_name": "drmoksha",
            "website_url": "https://www.drmoksha.com/",
            "search_console": "Search Console: attached",
            "totals": {
                "impressions": {"status": "measured", "value": 12000},
                "clicks": {"status": "measured", "value": 80},
                "ctr": {"status": "measured", "value": 0.0067},
                "position": {"status": "measured", "value": 18.2},
            },
            "period_comparisons": period_comparisons(daily),
            "daily_series": daily,
            "top_queries": [
                {
                    "query": "corporate lawyer",
                    "impressions": 900,
                    "clicks": 4,
                    "ctr": 0.004,
                    "position": 22.0,
                    "delta_impressions": 120,
                    "movement": "up",
                }
            ],
            "keywords": [
                {
                    "query": "corporate lawyer",
                    "impressions": 900,
                    "clicks": 4,
                    "ctr": 0.004,
                    "position": 22.0,
                    "reason": "900 impressions, position 22.0, CTR 0.4%",
                    "delta_impressions": 120,
                    "movement": "up",
                }
            ],
            "devices": [{"label": "MOBILE", "impressions": 8000, "clicks": 50, "ctr": 0.006, "position": 18.0}],
            "countries": [{"label": "ind", "impressions": 7000, "clicks": 40, "ctr": 0.005, "position": 19.0}],
            "scores": {},
            "indexability": {},
            "findings": [],
            "gaps": [],
        }
    )
    assert "Daily impressions" in html
    assert "<svg" in html
    assert "Last 7 days vs prior 7 days" in html
    assert "corporate lawyer" in html
    assert "+120" in html
    assert "MOBILE" in html
    assert "ind" in html


def test_render_html_does_not_invent_missing_gsc() -> None:
    html = render_html(
        {
            "project_name": "example",
            "totals": {
                "impressions": {"status": "unavailable", "detail": "no Search Console credentials"},
                "clicks": {"status": "unavailable", "detail": "no Search Console credentials"},
                "ctr": {"status": "unavailable", "detail": "no Search Console credentials"},
                "position": {"status": "unavailable", "detail": "no Search Console credentials"},
            },
            "scores": {},
            "indexability": {},
            "top_pages": [],
            "top_queries": [],
            "findings": [],
            "keywords": [],
            "gaps": [],
        }
    )
    assert "no Search Console credentials" in html
    assert "12,000" not in html
