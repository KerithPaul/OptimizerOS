"""Semantic chunk boundaries (step 2.D.1 verify)."""

from pathlib import Path

from app.intelligence.repository.ast import extract_repository
from app.intelligence.repository.chunker import chunk_repository
from app.intelligence.repository.filter import filter_repository

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOLDEN_A = _REPO_ROOT / "testdata" / "golden-projects" / "a-nextjs-ts-mysql"


def test_function_chunk_starts_on_the_function_boundary() -> None:
    filtered = filter_repository(_GOLDEN_A)
    extracted = extract_repository(_GOLDEN_A, filtered.included_paths)
    chunks = chunk_repository(_GOLDEN_A, extracted)

    get_product = next(c for c in chunks if c.symbol == "getProduct")
    assert get_product.chunk_type == "function"
    assert get_product.file_path == "lib/product-service.ts"
    assert get_product.start_line == 3
    assert get_product.text.lstrip().startswith("export async function getProduct")
    assert "return query(" in get_product.text
    # Not a mid-body slice: the chunk includes the function signature.
    assert not get_product.text.lstrip().startswith("return query")

    product_page = next(c for c in chunks if c.symbol == "ProductPage")
    assert product_page.chunk_type == "component"
    assert product_page.start_line == 12
    assert "export default async function ProductPage" in product_page.text
