"""WebMCP detector (Phase 12, step 12.1).

Observes the W3C WebMCP draft surface only:

- imperative `registerTool({...})` in executable script
- declarative `<form toolname>` (and form-associated named controls)
- `document.modelContext.getTools()` when a render already exposes it

Does not fetch a well-known manifest. Does not call `executeTool`.
A document with no agent surface returns an empty list.
"""

from __future__ import annotations

import re
from typing import Any

from selectolax.parser import HTMLParser, Node

from app.connectors.model import AgentTool

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_ANNOTATION_KEYS = ("readOnlyHint", "untrustedContentHint", "consequentialHint")
_SKIP_SCRIPT_TYPES = frozenset(
    {
        "application/ld+json",
        "application/json",
        "text/template",
        "importmap",
        "speculationrules",
    }
)
_CONTROL_SELECTOR = "input, select, textarea, button"
_ABSENT = frozenset({"null", "undefined"})


def detect_agent_tools(html: str) -> list[AgentTool]:
    """Tools declared in this document. Empty when WebMCP is absent."""

    if not html or ("registerTool" not in html and "toolname" not in html):
        return []
    tree = HTMLParser(html)
    tools: list[AgentTool] = []
    for script in tree.css("script"):
        if _skip_script(script):
            continue
        tools.extend(_imperative_tools(script.text() or ""))
    for form in tree.css("form"):
        if _inside_template(form):
            continue
        tool = _declarative_tool(tree, form)
        if tool is not None:
            tools.append(tool)
    return _dedupe(tools)


def tools_from_runtime(payload: Any) -> list[AgentTool] | None:
    """Map a `getTools()` result. `None` means the API was not there."""

    if payload is None:
        return None
    if not isinstance(payload, list):
        return None
    tools: list[AgentTool] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            continue
        description = item.get("description")
        text = description.strip() if isinstance(description, str) else ""
        annotations = item.get("annotations")
        semantic = False
        if isinstance(annotations, dict):
            semantic = any(
                key in annotations and annotations[key] is not None for key in _ANNOTATION_KEYS
            )
        tools.append(
            AgentTool(
                name=name,
                registration="imperative",
                description=text or None,
                description_missing=not text,
                input_schema=_runtime_schema_present(item.get("inputSchema")),
                semantic_annotation=semantic,
                structured_response=_runtime_schema_present(item.get("outputSchema")),
            )
        )
    return _dedupe(tools)


def merge_agent_tools(
    html_tools: list[AgentTool],
    runtime_tools: list[AgentTool] | None,
) -> list[AgentTool]:
    """Runtime registrations replace same-named source tools.

    `runtime_tools is None` means this browser has no `modelContext`, so
    the source scan stands. An empty runtime list means the API answered
    and nothing is registered: source `registerTool` calls are dropped,
    declarative forms the runtime list does not already name are kept.
    """

    if runtime_tools is None:
        return _dedupe(html_tools)
    runtime_names = {tool.name for tool in runtime_tools}
    declarative = [
        tool
        for tool in html_tools
        if tool.registration == "declarative" and tool.name not in runtime_names
    ]
    return _dedupe([*runtime_tools, *declarative])


def _runtime_schema_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _dedupe(tools: list[AgentTool]) -> list[AgentTool]:
    seen: set[tuple[str, str]] = set()
    ordered: list[AgentTool] = []
    for tool in tools:
        key = (tool.registration, tool.name)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(tool)
    return ordered


def _skip_script(script: Node) -> bool:
    if _inside_template(script):
        return True
    kind = ((script.attributes or {}).get("type") or "").strip().lower()
    return kind in _SKIP_SCRIPT_TYPES


def _inside_template(node: Node) -> bool:
    parent = node.parent
    while parent is not None:
        if getattr(parent, "tag", None) == "template":
            return True
        parent = parent.parent
    return False


def _declarative_tool(tree: HTMLParser, form: Node) -> AgentTool | None:
    attrs = form.attributes or {}
    raw_name = attrs.get("toolname")
    if raw_name is None:
        return None
    name = raw_name.strip()
    if not name or len(name) > 128:
        return None
    description = (attrs.get("tooldescription") or "").strip()
    return AgentTool(
        name=name,
        registration="declarative",
        description=description or None,
        description_missing=not description,
        input_schema=bool(_named_controls(tree, form)),
        semantic_annotation=False,
        structured_response=False,
    )


def _named_controls(tree: HTMLParser, form: Node) -> list[str]:
    form_id = ((form.attributes or {}).get("id") or "").strip()
    names: list[str] = []
    for node in form.css(_CONTROL_SELECTOR):
        if _inside_template(node):
            continue
        owner = ((node.attributes or {}).get("form") or "").strip()
        if owner and owner != form_id:
            continue
        control_name = ((node.attributes or {}).get("name") or "").strip()
        if control_name:
            names.append(control_name)
    if form_id:
        for node in tree.css(_CONTROL_SELECTOR):
            if ((node.attributes or {}).get("form") or "").strip() != form_id:
                continue
            control_name = ((node.attributes or {}).get("name") or "").strip()
            if control_name:
                names.append(control_name)
    return names


def _imperative_tools(source: str) -> list[AgentTool]:
    tools: list[AgentTool] = []
    for obj in _call_argument_objects(source):
        tool = _tool_from_object(obj)
        if tool is not None:
            tools.append(tool)
    return tools


def _tool_from_object(obj: str) -> AgentTool | None:
    fields = _top_level_fields(obj)
    name = _static_string(fields.get("name"))
    if name is None or not _NAME_RE.fullmatch(name):
        return None
    description, missing = _description_state(fields.get("description"))
    return AgentTool(
        name=name,
        registration="imperative",
        description=description,
        description_missing=missing,
        input_schema=_value_present(fields.get("inputSchema")),
        semantic_annotation=_annotation_present(fields.get("annotations")),
        structured_response=(
            _value_present(fields.get("execute")) or _value_present(fields.get("outputSchema"))
        ),
    )


def _description_state(raw: str | None) -> tuple[str | None, bool]:
    if raw is None:
        return None, True
    text = _static_string(raw)
    if text is not None:
        text = text.strip()
        return (text or None), not text
    token = raw.strip()
    if token in _ABSENT or token in {'""', "''", "``"}:
        return None, True
    return None, False


def _value_present(raw: str | None) -> bool:
    if raw is None:
        return False
    token = raw.strip()
    return bool(token) and token not in _ABSENT


def _annotation_present(raw: str | None) -> bool:
    if raw is None or not raw.strip().startswith("{"):
        return False
    fields = _top_level_fields(raw.strip())
    for key in _ANNOTATION_KEYS:
        value = fields.get(key)
        if value is None:
            continue
        if value.strip() in _ABSENT:
            continue
        return True
    return False


def _static_string(raw: str | None) -> str | None:
    if raw is None:
        return None
    token = raw.strip()
    if len(token) < 2 or token[0] not in "'\"`":
        return None
    quote = token[0]
    if token[-1] != quote:
        return None
    if quote == "`" and "${" in token:
        return None
    return _unescape(token[1:-1])


def _unescape(body: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            nxt = body[index + 1]
            out.append({"n": "\n", "r": "\r", "t": "\t", "\\": "\\"}.get(nxt, nxt))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _call_argument_objects(source: str) -> list[str]:
    objects: list[str] = []
    index = 0
    length = len(source)
    while index < length:
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        char = source[index]
        if char in "'\"`":
            index = _skip_string(source, index)
            continue
        start = _register_call_at(source, index)
        if start is None:
            index += 1
            continue
        paren = source.find("(", start)
        if paren < 0:
            break
        cursor = _skip_space(source, paren + 1)
        if cursor < length and source[cursor] == "{":
            obj, after = _read_delimited(source, cursor)
            if obj is not None:
                objects.append(obj)
                index = after
                continue
        index = paren + 1
    return objects


def _register_call_at(source: str, index: int) -> int | None:
    if source.startswith(".registerTool(", index):
        return index
    if not source.startswith("registerTool(", index):
        return None
    if index == 0:
        return index
    previous = source[index - 1]
    if previous.isalnum() or previous in "_.$":
        return None
    return index


def _skip_space(source: str, index: int) -> int:
    length = len(source)
    while index < length:
        if source[index].isspace():
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        break
    return index


def _skip_string(source: str, index: int) -> int:
    quote = source[index]
    index += 1
    length = len(source)
    while index < length:
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == quote:
            return index + 1
        index += 1
    return length


def _read_delimited(source: str, start: int) -> tuple[str | None, int]:
    opener = source[start]
    closer = {"{": "}", "[": "]", "(": ")"}[opener]
    depth = 0
    index = start
    length = len(source)
    while index < length:
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        char = source[index]
        if char in "'\"`":
            index = _skip_string(source, index)
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return source[start : index + 1], index + 1
        index += 1
    return None, length


def _top_level_fields(obj: str) -> dict[str, str]:
    if len(obj) < 2 or obj[0] != "{":
        return {}
    fields: dict[str, str] = {}
    index = 1
    length = len(obj)
    while index < length - 1:
        index = _skip_space(obj, index)
        if index >= length - 1:
            break
        if obj[index] == ",":
            index += 1
            continue
        key, index = _read_key(obj, index)
        if key is None:
            break
        index = _skip_space(obj, index)
        if index >= length or obj[index] != ":":
            break
        index = _skip_space(obj, index + 1)
        value, index = _read_value(obj, index)
        if value is None:
            break
        fields[key] = value
    return fields


def _read_key(source: str, index: int) -> tuple[str | None, int]:
    char = source[index]
    if char in "'\"":
        end = _skip_string(source, index)
        text = _static_string(source[index:end])
        return text, end
    end = index
    while end < len(source) and (source[end].isalnum() or source[end] in "_$"):
        end += 1
    if end == index:
        return None, index
    return source[index:end], end


def _read_value(source: str, index: int) -> tuple[str | None, int]:
    if index >= len(source):
        return None, index
    char = source[index]
    if char in "'\"`":
        end = _skip_string(source, index)
        return source[index:end], end
    if char in "{[(":
        return _read_delimited(source, index)
    end = index
    while end < len(source) and source[end] not in ",}":
        if source[end] in "'\"`":
            end = _skip_string(source, end)
            continue
        if source[end] in "{[(":
            _block, end = _read_delimited(source, end)
            continue
        end += 1
    token = source[index:end].strip()
    if not token:
        return None, index
    return token, end
