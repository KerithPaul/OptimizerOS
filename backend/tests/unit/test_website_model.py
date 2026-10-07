"""Common Website Model isolation and shape (step 3.A.1 verify)."""

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.connectors import model as model_mod
from app.connectors.model import (
    Heading,
    Page,
    PlatformMetadata,
    Website,
)


def test_model_module_does_not_import_platform_connectors() -> None:
    tree = ast.parse(Path(model_mod.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    joined = " ".join(imported).lower()
    assert "wordpress" not in joined
    assert "github" not in joined
    assert "clearsite" not in joined
    assert "connectors.wordpress" not in joined
    assert "connectors.github" not in joined


def test_page_accepts_the_section_8_tree() -> None:
    page = Page(
        url="https://example.com/",
        title="Home",
        meta_description="A description",
        canonical="https://example.com/",
        headings=[Heading(level=1, text="Home")],
        content="Hello",
        links=[],
        images=[],
        structured_data=[],
        entities=[],
        questions=[],
    )
    site = Website(
        home_url="https://example.com/",
        pages=[page],
        platform_metadata=PlatformMetadata(
            platform="url_only",
            home_url="https://example.com/",
        ),
    )
    assert site.pages[0].title == "Home"
    assert site.platform_metadata.platform == "url_only"


def test_platform_specific_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Page.model_validate(
            {
                "url": "https://example.com/",
                "yoast_title": "leaked wordpress field",
            }
        )
