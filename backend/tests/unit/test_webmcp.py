"""Phase 12 WebMCP detector and agent-accessibility rules."""

from app.connectors.model import AgentTool, Page
from app.intelligence.website.extract import extract_page
from app.intelligence.website.render import RenderResult, apply_render
from app.intelligence.website.webmcp import (
    detect_agent_tools,
    merge_agent_tools,
    tools_from_runtime,
)
from app.knowledge.evaluator import catalog_rules, evaluate
from app.retrieval.scoring import (
    SCORE_AI_GEO,
    SCORE_CONTENT_AEO,
    SCORE_TECHNICAL_SEO,
    compute_scores,
)
from tests.unit.rules.conftest import hits_for
from tests.unit.test_scoring import _finding
from app.models.knowledge import RuleCategory, RuleSeverity


_PAGE = "https://example.com/tools"


def _html(body: str) -> str:
    return f"<!DOCTYPE html><html><head><title>Tools</title></head><body>{body}</body></html>"


def _names(tools: list[AgentTool]) -> list[str]:
    return [tool.name for tool in tools]


def test_a_page_with_no_agent_surface_has_no_tools() -> None:
    html = _html("<p>registerTool is mentioned here, not registered.</p>")
    assert detect_agent_tools(html) == []
    page = extract_page(html, url=_PAGE).page
    result = evaluate([page])
    assert hits_for("AGENT-TOOLS-DISCOVERED-001", result) == []
    assert hits_for("AGENT-TOOL-DESCRIPTION-001", result) == []
    assert hits_for("AGENT-TOOL-INPUT-SCHEMA-001", result) == []
    assert hits_for("AGENT-TOOL-ANNOTATION-001", result) == []
    assert hits_for("SEO-TITLE-MISSING-001", result) == []


def test_imperative_tool_with_a_full_contract_is_discovery_only() -> None:
    html = _html(
        """
        <script>
          document.modelContext.registerTool({
            name: "search-cars",
            description: "Perform a car make/model search",
            inputSchema: { type: "object", properties: { make: { type: "string" } } },
            annotations: { readOnlyHint: true, consequentialHint: false },
            execute: async () => ({ results: [] }),
          });
        </script>
        """
    )
    tools = detect_agent_tools(html)
    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "search-cars"
    assert tool.registration == "imperative"
    assert tool.description_missing is False
    assert tool.input_schema is True
    assert tool.semantic_annotation is True
    assert tool.structured_response is True

    result = evaluate([extract_page(html, url=_PAGE).page])
    assert len(hits_for("AGENT-TOOLS-DISCOVERED-001", result)) == 1
    discovered = hits_for("AGENT-TOOLS-DISCOVERED-001", result)[0]
    assert discovered.affected_resource == _PAGE
    assert discovered.observed_value["tools"][0]["structured_response"] is True
    assert hits_for("AGENT-TOOL-DESCRIPTION-001", result) == []
    assert hits_for("AGENT-TOOL-INPUT-SCHEMA-001", result) == []
    assert hits_for("AGENT-TOOL-ANNOTATION-001", result) == []


def test_imperative_gaps_are_one_finding_per_missing_field() -> None:
    html = _html(
        """
        <script>
          // .registerTool({ name: "commented-out", description: "no" })
          document.modelContext.registerTool({
            name: "search-cars",
            description: "",
            execute: async () => "ok",
          });
        </script>
        """
    )
    tools = detect_agent_tools(html)
    assert _names(tools) == ["search-cars"]
    assert tools[0].description_missing is True
    assert tools[0].input_schema is False
    assert tools[0].semantic_annotation is False
    result = evaluate([extract_page(html, url=_PAGE).page])
    assert len(hits_for("AGENT-TOOLS-DISCOVERED-001", result)) == 1
    for rule_id in (
        "AGENT-TOOL-DESCRIPTION-001",
        "AGENT-TOOL-INPUT-SCHEMA-001",
        "AGENT-TOOL-ANNOTATION-001",
    ):
        hits = hits_for(rule_id, result)
        assert len(hits) == 1
        assert hits[0].affected_resource == f"{_PAGE}#search-cars"


def test_invalid_imperative_name_is_not_a_tool() -> None:
    html = _html(
        """
        <script>
          document.modelContext.registerTool({
            name: "has space",
            description: "nope",
            inputSchema: {},
          });
        </script>
        """
    )
    assert detect_agent_tools(html) == []


def test_declarative_form_is_a_tool_and_records_its_gaps() -> None:
    html = _html(
        """
        <form toolname="search-cars" tooldescription="Perform a car make/model search">
          <input type="text" name="make" toolparamdescription="The vehicle's make" required>
          <input type="text" name="model" required>
          <button type="submit">Search</button>
        </form>
        <template>
          <form toolname="hidden-tool" tooldescription="not inserted">
            <input name="q">
          </form>
        </template>
        """
    )
    tools = detect_agent_tools(html)
    assert _names(tools) == ["search-cars"]
    assert tools[0].registration == "declarative"
    assert tools[0].description_missing is False
    assert tools[0].input_schema is True
    assert tools[0].semantic_annotation is False
    assert tools[0].structured_response is False
    result = evaluate([extract_page(html, url=_PAGE).page])
    assert hits_for("AGENT-TOOL-DESCRIPTION-001", result) == []
    assert hits_for("AGENT-TOOL-INPUT-SCHEMA-001", result) == []
    assert len(hits_for("AGENT-TOOL-ANNOTATION-001", result)) == 1


def test_declarative_form_without_controls_has_no_input_schema() -> None:
    html = _html('<form toolname="Search flights"></form>')
    tools = detect_agent_tools(html)
    assert _names(tools) == ["Search flights"]
    assert tools[0].description_missing is True
    assert tools[0].input_schema is False


def test_json_ld_script_is_not_scanned_as_javascript() -> None:
    html = _html(
        """
        <script type="application/ld+json">
          { "registerTool": "document.modelContext.registerTool({ name: \\"search-cars\\" })" }
        </script>
        """
    )
    assert detect_agent_tools(html) == []


def test_runtime_tools_replace_source_imperative_and_keep_forms() -> None:
    html_tools = [
        AgentTool(
            name="search-cars",
            registration="imperative",
            description="from source",
            description_missing=False,
            input_schema=True,
            semantic_annotation=True,
            structured_response=True,
        ),
        AgentTool(
            name="book",
            registration="declarative",
            description="Book a table",
            description_missing=False,
            input_schema=True,
        ),
    ]
    runtime = tools_from_runtime(
        [
            {
                "name": "search-cars",
                "description": "from runtime",
                "inputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": False},
                "outputSchema": {"type": "object"},
            }
        ]
    )
    merged = merge_agent_tools(html_tools, runtime)
    by_name = {tool.name: tool for tool in merged}
    assert set(by_name) == {"search-cars", "book"}
    assert by_name["search-cars"].description == "from runtime"
    assert by_name["search-cars"].structured_response is True
    assert by_name["book"].registration == "declarative"

    assert merge_agent_tools(html_tools, []) == [html_tools[1]]
    assert merge_agent_tools(html_tools, None) == html_tools


def test_apply_render_uses_runtime_tools_when_the_browser_has_them() -> None:
    raw = extract_page(_html("<p>no tools</p>"), url=_PAGE).page
    result = RenderResult(
        url=_PAGE,
        state="observed",
        rendered_html=_html("<p>no tools</p>"),
        rendered_title="Tools",
        rendered_dom_hash=raw.raw_html_hash,
        rendered_text="no tools",
        raw_to_rendered_changed=False,
        runtime_agent_tools=[
            {
                "name": "search-cars",
                "description": "",
                "inputSchema": None,
                "annotations": None,
                "outputSchema": None,
            }
        ],
    )
    merged = apply_render(raw, result)
    assert _names(merged.agent_tools) == ["search-cars"]
    result_eval = evaluate([merged])
    assert len(hits_for("AGENT-TOOLS-DISCOVERED-001", result_eval)) == 1
    assert len(hits_for("AGENT-TOOL-DESCRIPTION-001", result_eval)) == 1


def test_agent_findings_do_not_change_the_three_scores() -> None:
    agent = _finding(
        finding_id="AGENT-TOOLS-DISCOVERED-001:abc",
        rule="AGENT-TOOLS-DISCOVERED-001",
        category=RuleCategory.AGENT_ACCESSIBILITY,
        severity=RuleSeverity.CRITICAL,
    )
    scores = compute_scores([agent])
    assert scores[SCORE_TECHNICAL_SEO].value == 100
    assert scores[SCORE_CONTENT_AEO].value == 100
    assert scores[SCORE_AI_GEO].value == 100
    assert scores[SCORE_TECHNICAL_SEO].signals == ()


def test_catalog_contains_the_agent_rules() -> None:
    ids = {rule.rule_id for rule in catalog_rules()}
    assert {
        "AGENT-TOOLS-DISCOVERED-001",
        "AGENT-TOOL-DESCRIPTION-001",
        "AGENT-TOOL-INPUT-SCHEMA-001",
        "AGENT-TOOL-ANNOTATION-001",
    } <= ids


def test_rendered_form_is_detected_when_the_dom_changes() -> None:
    raw = extract_page(_html("<p>shell</p>"), url=_PAGE)
    rendered = _html(
        '<form toolname="search-cars" tooldescription="Search">'
        '<input name="q"></form>'
    )
    result = RenderResult(
        url=_PAGE,
        state="observed",
        rendered_html=rendered,
        rendered_title="Tools",
        rendered_dom_hash="different",
        rendered_text="Search",
        raw_to_rendered_changed=True,
        runtime_agent_tools=None,
    )
    merged = apply_render(raw.page, result)
    assert _names(merged.agent_tools) == ["search-cars"]
    assert isinstance(merged, Page)
