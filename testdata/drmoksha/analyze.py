"""Offline dry-run of the tool's extraction + rule evaluation on drmoksha.com.

Fetches nothing: uses the saved HTML. Mirrors what the crawler -> extract ->
evaluate pipeline sees, so we can validate what the tool would report.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.intelligence.website.extract import extract_page  # noqa: E402
from app.knowledge.evaluator import evaluate, catalog_rules  # noqa: A003,E402

HTML = Path(__file__).parent / "home.html"
URL = "https://drmoksha.com/"


def main() -> None:
    html = HTML.read_text(encoding="utf-8", errors="replace")
    result = extract_page(html, url=URL, status_code=200)
    page = result.page

    print("=== BASIC SEO FIELDS ===")
    print("title:", page.title)
    print("title_len:", len(page.title or ""))
    print("meta_description:", (page.meta_description or "")[:300])
    print("meta_desc_len:", len(page.meta_description or ""))
    print("canonical:", page.canonical)
    print("language:", page.language)
    print("viewport:", page.viewport)
    print("robots meta:", page.robots.meta, "| x-robots:", page.robots.x_robots_tag)
    print("h1s:", [h.text for h in page.headings if h.level == 1])
    print("h2/h3 headings (first 30):")
    for h in [h for h in page.headings if h.level in (2, 3)][:30]:
        print(f"  h{h.level}: {h.text}")
    print("word_count:", page.word_count)
    print("open_graph:", json.dumps(page.open_graph, indent=1)[:800])
    print("twitter:", json.dumps(page.twitter, indent=1)[:500])
    print("structured_data types:", [b.type for b in page.structured_data])
    print("images:", len(page.images), "| missing alt:", sum(1 for i in page.images if not i.alt))
    print("links:", len(page.links),
          "| internal:", sum(1 for l in page.links if l.internal is True),
          "| external:", sum(1 for l in page.links if l.internal is False))

    print("\n=== AEO OBSERVATIONS ===")
    obs = result.observations
    print("question_headings:", obs.question_headings)
    print("concise_answer_leads:", obs.concise_answer_leads)
    print("page.questions:", [(q.text, (q.answer or "")[:80]) for q in page.questions])

    print("\n=== KEYWORD RELEVANT: title/desc/h1/content top terms ===")
    text = page.content or ""
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    stop = {
        "the", "and", "for", "with", "your", "this", "that", "have", "from",
        "will", "more", "when", "what", "which", "they", "their", "them",
        "been", "were", "are", "was", "not", "but", "can", "our", "you",
        "all", "how", "who", "why", "get", "out", "into", "about", "also",
        "than", "then", "them", "these", "those", "there", "here", "just",
    }
    freq: dict[str, int] = {}
    for word in words:
        if word in stop:
            continue
        freq[word] = freq.get(word, 0) + 1
    top = sorted(freq.items(), key=lambda kv: -kv[1])[:25]
    print("top 25 content terms:", top)

    print("\n=== RULE EVALUATION (tool's own evaluator) ===")
    evaluation = evaluate(page, rules=catalog_rules())
    print(f"hits: {len(evaluation.hits)} | skips: {len(evaluation.skips)}")
    for hit in evaluation.hits:
        print(f"  [{hit.severity}] {hit.rule_id} @ {hit.affected_resource}")
        print(f"      observed: {json.dumps(hit.observed_value, default=str)[:220]}")
    skipped_checks = sorted({s.rule_id for s in evaluation.skips})
    print("skipped rule_ids:", skipped_checks[:40])


if __name__ == "__main__":
    main()
