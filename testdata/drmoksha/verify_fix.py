"""Re-run extraction + evaluation on saved drmoksha.com pages after the fixes."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.intelligence.website.extract import extract_page  # noqa: E402
from app.knowledge.evaluator import catalog_rules, evaluate  # noqa: E402


def run(name: str, html_path: Path, url: str) -> None:
    html = html_path.read_text(encoding="utf-8", errors="replace")
    result = extract_page(html, url=url, status_code=200)
    page = result.page

    print(f"===== {name} =====")
    print("questions extracted:")
    for q in page.questions:
        words = None if q.answer is None else len(q.answer.split())
        print(f"  - {q.text[:70]} | answer_words={words}")
        if q.answer:
            print(f"      answer: {q.answer[:100]}...")

    evaluation = evaluate(page, rules=catalog_rules())
    aeo = [h for h in evaluation.hits if h.rule_id.startswith("AEO")]
    kw = [h for h in evaluation.hits if h.rule_id == "SEO-KEYWORD-FOCUS-001"]
    imgs = [h for h in evaluation.hits if h.rule_id.startswith("SEO-IMAGE")]
    print(f"\nAEO hits: {len(aeo)}")
    for hit in aeo:
        print(f"  {hit.rule_id}: {json.dumps(hit.observed_value)[:120]}")
    print(f"\nkeyword hits: {len(kw)}")
    for hit in kw:
        ov = hit.observed_value
        print("  dominant:", ov.get("dominant_terms"))
        print("  missing :", ov.get("missing_from_title_and_description"))
    print(f"\nimage hits: {len(imgs)} (was duplicated before)")
    for hit in imgs:
        print(f"  {hit.rule_id}: {hit.affected_resource.rsplit('/', 1)[-1][:80]}")
    print()


if __name__ == "__main__":
    base = Path(__file__).parent
    run("HOME", base / "home.html", "https://drmoksha.com/")
    run("CORP-PAGE", base / "corp.html", "https://drmoksha.com/corporate-lawyer-in-andhra-pradesh")
