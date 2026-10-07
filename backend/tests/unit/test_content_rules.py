"""Content change rules (step 7.9 verify, `[SPEC about-ArchitectOS.md §68]`)."""

from __future__ import annotations

from app.agents.content_rules import check_content_change


def test_wiping_content_is_flagged() -> None:
    violations = check_content_change("Some real product description text.", "")
    assert any(v.rule == "content_removed" for v in violations)


def test_clean_minimal_edit_is_not_flagged() -> None:
    before = "Buy our running shoes. Great for daily training."
    after = "Buy our running shoes online. Great for daily training."
    assert check_content_change(before, after) == []


def test_new_unsupported_statistic_is_flagged() -> None:
    before = "Our shoes are comfortable and durable."
    after = "Our shoes are comfortable and durable. 97% of runners agree."
    violations = check_content_change(before, after)
    assert any(v.rule == "unsupported_statistic" for v in violations)


def test_new_attribution_phrase_is_flagged() -> None:
    before = "Our shoes are comfortable."
    after = "Our shoes are comfortable. Studies show they reduce injury."
    violations = check_content_change(before, after)
    assert any(v.rule == "unsupported_attribution" for v in violations)


def test_keyword_stuffing_is_flagged() -> None:
    before = "Buy running shoes today. Great running shoes for everyone."
    after = " ".join(["running shoes"] * 20)
    violations = check_content_change(before, after)
    assert any(v.rule == "keyword_stuffing" for v in violations)


def test_preexisting_statistics_are_not_re_flagged() -> None:
    before = "Our shoes are worn by 50% of marathon runners."
    after = "Our lightweight shoes are worn by 50% of marathon runners."
    violations = check_content_change(before, after)
    assert not any(v.rule == "unsupported_statistic" for v in violations)


_HTML_BEFORE = """<!doctype html>
<html lang="en">
  <head>
    <title>Lex Fintech</title>
    <link rel="canonical" href="https://lexfintech.io/" />
  </head>
  <body>
    <a href="https://lexfintech.io">Lex Fintech</a>
  </body>
</html>
"""

_JSON_LD = """
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "name": "Lex Fintech",
      "url": "https://lexfintech.io",
      "logo": "https://lexfintech.io/logo.png",
      "sameAs": [
        "https://lexfintech.io",
        "https://www.linkedin.com/company/lexfintech"
      ]
    },
    {
      "@type": "WebSite",
      "name": "Lex Fintech",
      "url": "https://lexfintech.io"
    }
  ]
}
</script>
"""


def test_json_ld_brand_urls_are_not_keyword_stuffing() -> None:
    after = _HTML_BEFORE.replace("</head>", _JSON_LD + "</head>")
    assert check_content_change(_HTML_BEFORE, after) == []


def test_stuffed_json_ld_description_is_flagged() -> None:
    stuffed = _HTML_BEFORE.replace(
        "</head>",
        '<script type="application/ld+json">{"@type":"Organization",'
        '"description":"' + " lexfintech" * 8 + '"}</script></head>',
    )
    violations = check_content_change(_HTML_BEFORE, stuffed)
    assert any(v.rule == "keyword_stuffing" for v in violations)


def test_markdown_link_urls_are_not_counted() -> None:
    before = "Read more on our site."
    after = (
        "Read more on our site. See [Lex Fintech](https://lexfintech.io) "
        "and [docs](https://lexfintech.io/docs) and [blog](https://lexfintech.io/blog)."
    )
    assert check_content_change(before, after) == []
