"""Tree-sitter extraction of meaningful code entities (step 2.C.1).

Grammars: Python, JavaScript, TypeScript, TSX, HTML, JSON, CSS `[P12]`.
A parse failure on one file is recorded and skipped; it does not abort
the run. Unparsed files are part of the result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from tree_sitter import Language, Node, Parser

logger = logging.getLogger("architectos.intelligence.repository.ast")

_LANGUAGES: dict[str, Language] | None = None

_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".html": "html",
    ".htm": "html",
    ".json": "json",
    ".css": "css",
}

_JS_FAMILY = frozenset({"javascript", "typescript", "tsx"})


@dataclass
class Symbol:
    kind: str
    name: str
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    language: str | None = None
    exported: bool = False
    role: str | None = None


@dataclass
class Relation:
    type: str
    from_kind: str
    from_name: str
    from_file: str | None
    from_start_line: int | None
    to_kind: str
    to_name: str
    to_file: str | None
    to_start_line: int | None = None


@dataclass
class ExtractResult:
    symbols: list[Symbol] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    parsed_files: list[str] = field(default_factory=list)
    unparsed_files: list[dict[str, str]] = field(default_factory=list)

    @property
    def parsed_count(self) -> int:
        return len(self.parsed_files)

    @property
    def unparsed_count(self) -> int:
        return len(self.unparsed_files)


def parse_symbols(source: bytes, rel_path: str) -> list[Symbol]:
    """Parse one file's content in isolation -- no repository, no relation
    resolution, no graph write. Used by `app.changes.units` to compare a
    file's function/class/component boundaries before and after a Code
    Agent patch, where "before" may only ever exist in memory. A parse
    failure (unknown extension, syntax error) returns an empty list, never
    raises -- the caller falls back to whole-file diffing.
    """
    language_name = _EXTENSIONS.get(Path(rel_path).suffix.lower())
    if language_name is None:
        return []
    parser = Parser(_languages()[language_name])
    try:
        tree = parser.parse(source)
    except Exception:  # noqa: BLE001 - a bad parse means no symbols, not a crash
        return []
    if tree.root_node.has_error:
        return []
    result = ExtractResult()
    raw_imports: list[tuple[str, list[str], str, int]] = []
    raw_calls: list[tuple[Symbol, str, str]] = []
    if language_name in _JS_FAMILY:
        _extract_js_family(tree.root_node, rel_path, language_name, result, raw_imports, raw_calls)
    elif language_name == "python":
        _extract_python(tree.root_node, rel_path, result, raw_imports, raw_calls)
    return [
        symbol
        for symbol in result.symbols
        if symbol.kind in {"Function", "Class", "Component"} and symbol.start_line and symbol.end_line
    ]


def extract_repository(root: Path, included_paths: list[str]) -> ExtractResult:
    """Extract meaningful entities from filtered files under `root`."""
    root = root.resolve()
    result = ExtractResult()
    languages = _languages()
    raw_imports: list[tuple[str, list[str], str, int]] = []
    raw_calls: list[tuple[Symbol, str, str]] = []

    for rel in included_paths:
        path = root / rel
        language_name = _EXTENSIONS.get(Path(rel).suffix.lower())
        if language_name is None:
            continue
        source, error = _read_bytes(path)
        if error is not None:
            result.unparsed_files.append({"path": rel, "reason": error})
            continue
        parser = Parser(languages[language_name])
        try:
            tree = parser.parse(source)
        except Exception as exc:  # noqa: BLE001 - a single file must not abort the run
            result.unparsed_files.append({"path": rel, "reason": str(exc)})
            logger.warning("tree-sitter parse raised (path=%s): %s", rel, exc)
            continue
        if tree.root_node.has_error:
            result.unparsed_files.append({"path": rel, "reason": "syntax error"})
            continue

        result.parsed_files.append(rel)
        result.symbols.append(
            Symbol(kind="File", name=rel, file_path=rel, start_line=1, language=language_name)
        )
        for directory in _directories_of(rel):
            if not any(s.kind == "Directory" and s.name == directory for s in result.symbols):
                result.symbols.append(Symbol(kind="Directory", name=directory, file_path=directory))

        if language_name in _JS_FAMILY:
            _extract_js_family(
                tree.root_node,
                rel,
                language_name,
                result,
                raw_imports,
                raw_calls,
            )
        elif language_name == "python":
            _extract_python(
                tree.root_node,
                rel,
                result,
                raw_imports,
                raw_calls,
            )

        _maybe_route(rel, result)
        _maybe_api_route(rel, result)

    _resolve_imports(result, raw_imports)
    _resolve_calls(result, raw_calls)
    _link_metadata(result)
    _link_database(result, raw_calls)
    return result


def _languages() -> dict[str, Language]:
    global _LANGUAGES
    if _LANGUAGES is None:
        import tree_sitter_css as tscss
        import tree_sitter_html as tshtml
        import tree_sitter_javascript as tsjs
        import tree_sitter_json as tsjson
        import tree_sitter_python as tspy
        import tree_sitter_typescript as tsts

        _LANGUAGES = {
            "python": Language(tspy.language()),
            "javascript": Language(tsjs.language()),
            "typescript": Language(tsts.language_typescript()),
            "tsx": Language(tsts.language_tsx()),
            "html": Language(tshtml.language()),
            "json": Language(tsjson.language()),
            "css": Language(tscss.language()),
        }
    return _LANGUAGES


def _read_bytes(path: Path) -> tuple[bytes | None, str | None]:
    try:
        return path.read_bytes(), None
    except OSError as exc:
        return None, str(exc)


def _directories_of(rel: str) -> list[str]:
    parts = PurePosixPath(rel).parts[:-1]
    out: list[str] = []
    for i in range(len(parts)):
        out.append("/".join(parts[: i + 1]))
    return out


def _extract_js_family(
    root: Node,
    rel: str,
    language_name: str,
    result: ExtractResult,
    raw_imports: list[tuple[str, list[str], str, int]],
    raw_calls: list[tuple[Symbol, str, str]],
) -> None:
    enclosing: list[Symbol] = []

    def visit(node: Node, exported: bool) -> None:
        exported_here = exported or node.type == "export_statement"

        if node.type in {"function_declaration", "generator_function_declaration"}:
            name = _child_text(node, "identifier")
            if name:
                symbol = _function_or_component(name, rel, node, language_name, exported_here)
                result.symbols.append(symbol)
                if exported_here:
                    _export(result, rel, symbol)
                enclosing.append(symbol)
                for child in node.children:
                    visit(child, False)
                enclosing.pop()
                return

        if node.type == "class_declaration":
            name = _child_text(node, "type_identifier") or _child_text(node, "identifier")
            if name:
                symbol = Symbol(
                    kind="Class",
                    name=name,
                    file_path=rel,
                    start_line=_start_line(node),
                    end_line=_end_line(node),
                    language=language_name,
                    exported=exported_here,
                )
                result.symbols.append(symbol)
                if exported_here:
                    _export(result, rel, symbol)
                enclosing.append(symbol)
                for child in node.children:
                    visit(child, False)
                enclosing.pop()
                return

        if node.type == "lexical_declaration":
            for decl in _descendants(node, "variable_declarator"):
                name = _child_text(decl, "identifier")
                value = _named_child(decl, "arrow_function") or _named_child(
                    decl, "function_expression"
                )
                if name and value is not None and _is_module_scope(decl):
                    symbol = _function_or_component(
                        name, rel, decl, language_name, exported_here
                    )
                    result.symbols.append(symbol)
                    if exported_here:
                        _export(result, rel, symbol)
                if name == "metadata" and exported_here:
                    meta = Symbol(
                        kind="SEOImplementation",
                        name="metadata",
                        file_path=rel,
                        start_line=_start_line(decl),
                        end_line=_end_line(decl),
                        language=language_name,
                        exported=True,
                        role="metadata",
                    )
                    result.symbols.append(meta)
                    _export(result, rel, meta)

        if node.type == "import_statement":
            source = _import_source(node)
            names = _import_names(node)
            if source:
                raw_imports.append((rel, names, source, _start_line(node)))

        if node.type == "call_expression" and enclosing:
            callee = _callee_name(node)
            if callee:
                raw_calls.append((enclosing[-1], callee, rel))

        for child in node.children:
            visit(child, exported_here)

    visit(root, False)


def _extract_python(
    root: Node,
    rel: str,
    result: ExtractResult,
    raw_imports: list[tuple[str, list[str], str, int]],
    raw_calls: list[tuple[Symbol, str, str]],
) -> None:
    enclosing: list[Symbol] = []

    def visit(node: Node) -> None:
        if node.type == "function_definition":
            name = _child_text(node, "identifier")
            if name:
                symbol = Symbol(
                    kind="Function",
                    name=name,
                    file_path=rel,
                    start_line=_start_line(node),
                    end_line=_end_line(node),
                    language="python",
                )
                result.symbols.append(symbol)
                enclosing.append(symbol)
                for child in node.children:
                    visit(child)
                enclosing.pop()
                return
        if node.type == "class_definition":
            name = _child_text(node, "identifier")
            if name:
                symbol = Symbol(
                    kind="Class",
                    name=name,
                    file_path=rel,
                    start_line=_start_line(node),
                    end_line=_end_line(node),
                    language="python",
                )
                result.symbols.append(symbol)
                enclosing.append(symbol)
                for child in node.children:
                    visit(child)
                enclosing.pop()
                return
        if node.type in {"import_statement", "import_from_statement"}:
            module, names = _python_import(node)
            if module:
                raw_imports.append((rel, names, module, _start_line(node)))
        if node.type == "call" and enclosing:
            callee = _python_callee(node)
            if callee:
                raw_calls.append((enclosing[-1], callee, rel))
        for child in node.children:
            visit(child)

    visit(root)


def _function_or_component(
    name: str,
    rel: str,
    node: Node,
    language_name: str,
    exported: bool,
) -> Symbol:
    is_component = language_name in {"tsx", "javascript"} and name[:1].isupper()
    kind = "Component" if is_component else "Function"
    role = "metadata" if name == "generateMetadata" else None
    if name in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} and Path(rel).name.startswith("route."):
        kind = "API"
        role = "api_handler"
    return Symbol(
        kind=kind,
        name=name,
        file_path=rel,
        start_line=_start_line(node),
        end_line=_end_line(node),
        language=language_name,
        exported=exported,
        role=role,
    )


def _maybe_route(rel: str, result: ExtractResult) -> None:
    path = _next_app_route(rel)
    if path is None:
        return
    route = Symbol(kind="Route", name=path, file_path=rel, start_line=1, language="tsx")
    result.symbols.append(route)
    page_components = [
        s
        for s in result.symbols
        if s.kind == "Component" and s.file_path == rel
    ]
    for component in page_components:
        result.relations.append(
            _rel("ROUTES_TO", component, route)
        )
        result.relations.append(
            _rel("RENDERS", route, component)
        )


def _app_dir_parts(posix: PurePosixPath) -> tuple[str, ...] | None:
    """Parts after the Next.js `app/` router root, allowing an optional `src/` prefix."""
    parts = posix.parts
    if parts[:1] == ("src",):
        parts = parts[1:]
    if not parts or parts[0] != "app":
        return None
    return parts[1:]


def _maybe_api_route(rel: str, result: ExtractResult) -> None:
    posix = PurePosixPath(rel)
    app_parts = _app_dir_parts(posix)
    if app_parts is None or len(app_parts) < 2 or app_parts[0] != "api":
        return
    if posix.name not in {"route.ts", "route.js", "route.tsx", "route.jsx"}:
        return
    segs = list(app_parts[:-1])
    path = "/" + "/".join(segs)
    api = Symbol(kind="API", name=path, file_path=rel, start_line=1)
    if not any(s.kind == "API" and s.name == path and s.file_path == rel for s in result.symbols):
        result.symbols.append(api)


def _next_app_route(rel: str) -> str | None:
    posix = PurePosixPath(rel)
    app_parts = _app_dir_parts(posix)
    if app_parts is None:
        return None
    if posix.name not in {"page.tsx", "page.ts", "page.jsx", "page.js"}:
        return None
    segs = [
        part
        for part in app_parts[:-1]
        if not (part.startswith("(") and part.endswith(")"))
    ]
    if not segs:
        return "/"
    return "/" + "/".join(segs)


def _resolve_imports(
    result: ExtractResult,
    raw_imports: list[tuple[str, list[str], str, int]],
) -> None:
    files = {s.name for s in result.symbols if s.kind == "File"}
    for from_file, names, spec, _line in raw_imports:
        target_file = _resolve_module(from_file, spec, files)
        file_symbol = _file_symbol(result, from_file)
        if target_file and file_symbol:
            target = _file_symbol(result, target_file)
            if target:
                result.relations.append(_rel("IMPORTS", file_symbol, target))
                result.relations.append(_rel("DEPENDS_ON", file_symbol, target))
        for name in names:
            imported = _find_symbol(result, name, file_path=target_file) if target_file else None
            if imported and file_symbol:
                result.relations.append(_rel("IMPORTS", file_symbol, imported))
            for owner in _symbols_in_file(result, from_file):
                if owner.kind in {"Function", "Component", "Class", "API"} and imported:
                    result.relations.append(_rel("IMPORTS", owner, imported))
                    result.relations.append(_rel("DEPENDS_ON", owner, imported))


def _resolve_calls(
    result: ExtractResult,
    raw_calls: list[tuple[Symbol, str, str]],
) -> None:
    by_name: dict[str, list[Symbol]] = {}
    for symbol in result.symbols:
        if symbol.kind in {"Function", "Component", "Class", "API"}:
            by_name.setdefault(symbol.name, []).append(symbol)
    seen: set[tuple] = set()
    for caller, callee_name, from_file in raw_calls:
        if "." in callee_name:
            continue
        targets = by_name.get(callee_name) or []
        same_file = [t for t in targets if t.file_path == from_file]
        chosen = same_file or targets
        for target in chosen:
            key = (caller.kind, caller.name, caller.file_path, caller.start_line, "CALLS", target.kind, target.name, target.file_path, target.start_line)
            if key in seen:
                continue
            seen.add(key)
            result.relations.append(_rel("CALLS", caller, target))
            result.relations.append(_rel("DEPENDS_ON", caller, target))


def _link_metadata(result: ExtractResult) -> None:
    by_file: dict[str, list[Symbol]] = {}
    for symbol in result.symbols:
        if symbol.file_path:
            by_file.setdefault(symbol.file_path, []).append(symbol)
    for _file_path, symbols in by_file.items():
        metas = [
            s
            for s in symbols
            if s.role == "metadata" or (s.kind == "SEOImplementation")
        ]
        components = [s for s in symbols if s.kind == "Component"]
        for component in components:
            for meta in metas:
                result.relations.append(_rel("GENERATES_METADATA", component, meta))


def _link_database(
    result: ExtractResult,
    raw_calls: list[tuple[Symbol, str, str]],
) -> None:
    db_callees = {"createConnection", "mysql.createConnection", "query"}
    callers = [caller for caller, callee, _rel_file in raw_calls if callee in db_callees or callee.endswith(".createConnection") or callee.endswith(".query")]
    if not callers:
        return
    database = Symbol(kind="Database", name="MySQL", file_path="lib/db.ts", start_line=1)
    if not any(s.kind == "Database" and s.name == "MySQL" for s in result.symbols):
        result.symbols.append(database)
    query_fn = _find_symbol(result, "query", file_path="lib/db.ts")
    if query_fn:
        result.relations.append(_rel("QUERIES", query_fn, database))
        result.relations.append(_rel("DEPENDS_ON", query_fn, database))


def _resolve_module(from_file: str, spec: str, files: set[str]) -> str | None:
    if not spec.startswith("."):
        return None
    origin = PurePosixPath(from_file).parent
    base = PurePosixPath(origin, spec)
    normalized = _posix_norm(base)
    candidates = [
        normalized,
        normalized + ".ts",
        normalized + ".tsx",
        normalized + ".js",
        normalized + ".jsx",
        normalized + ".json",
        normalized + "/index.ts",
        normalized + "/index.tsx",
        normalized + "/index.js",
    ]
    for candidate in candidates:
        if candidate in files:
            return candidate
    return None


def _posix_norm(path: PurePosixPath) -> str:
    parts: list[str] = []
    for part in path.parts:
        if part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _export(result: ExtractResult, rel: str, symbol: Symbol) -> None:
    file_symbol = _file_symbol(result, rel)
    if file_symbol:
        result.relations.append(_rel("EXPORTS", file_symbol, symbol))


def _rel(rel_type: str, source: Symbol, target: Symbol) -> Relation:
    return Relation(
        type=rel_type,
        from_kind=source.kind,
        from_name=source.name,
        from_file=source.file_path,
        from_start_line=source.start_line,
        to_kind=target.kind,
        to_name=target.name,
        to_file=target.file_path,
        to_start_line=target.start_line,
    )


def _file_symbol(result: ExtractResult, rel: str) -> Symbol | None:
    for symbol in result.symbols:
        if symbol.kind == "File" and symbol.name == rel:
            return symbol
    return None


def _find_symbol(result: ExtractResult, name: str, file_path: str | None = None) -> Symbol | None:
    for symbol in result.symbols:
        if symbol.name != name:
            continue
        if file_path is not None and symbol.file_path != file_path:
            continue
        if symbol.kind in {"Function", "Component", "Class", "API", "SEOImplementation", "Route", "Database"}:
            return symbol
    return None


def _symbols_in_file(result: ExtractResult, rel: str) -> list[Symbol]:
    return [s for s in result.symbols if s.file_path == rel]


def _start_line(node: Node) -> int:
    return node.start_point[0] + 1


def _end_line(node: Node) -> int:
    return node.end_point[0] + 1


def _child_text(node: Node, child_type: str) -> str | None:
    for child in node.children:
        if child.type == child_type:
            return child.text.decode("utf-8")
    return None


def _named_child(node: Node, child_type: str) -> Node | None:
    for child in node.children:
        if child.type == child_type:
            return child
    return None


def _descendants(node: Node, node_type: str) -> list[Node]:
    found: list[Node] = []
    stack = list(node.children)
    while stack:
        current = stack.pop()
        if current.type == node_type:
            found.append(current)
        stack.extend(current.children)
    return found


def _is_module_scope(node: Node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type in {
            "function_declaration",
            "arrow_function",
            "function_expression",
            "class_declaration",
            "method_definition",
        }:
            return False
        if parent.type == "program":
            return True
        parent = parent.parent
    return True


def _import_source(node: Node) -> str | None:
    for child in node.children:
        if child.type == "string":
            fragment = _child_text(child, "string_fragment")
            if fragment is not None:
                return fragment
            text = child.text.decode("utf-8")
            return text.strip("'\"")
    return None


def _import_names(node: Node) -> list[str]:
    names: list[str] = []
    for child in _descendants(node, "identifier"):
        names.append(child.text.decode("utf-8"))
    for child in _descendants(node, "import_specifier"):
        ident = _child_text(child, "identifier")
        if ident:
            names.append(ident)
    return list(dict.fromkeys(names))


def _callee_name(node: Node) -> str | None:
    func = node.child_by_field_name("function")
    if func is None and node.children:
        func = node.children[0]
    if func is None:
        return None
    if func.type == "identifier":
        return func.text.decode("utf-8")
    if func.type == "member_expression":
        return func.text.decode("utf-8")
    if func.type == "await_expression":
        inner = func.child_by_field_name("argument") or (func.children[-1] if func.children else None)
        if inner is not None and inner.type == "call_expression":
            return _callee_name(inner)
    return None


def _python_import(node: Node) -> tuple[str | None, list[str]]:
    module = None
    names: list[str] = []
    for child in node.children:
        if child.type == "dotted_name":
            module = child.text.decode("utf-8")
        if child.type == "aliased_import":
            ident = _child_text(child, "dotted_name") or _child_text(child, "identifier")
            if ident:
                names.append(ident.split(".")[-1])
        if child.type == "identifier":
            names.append(child.text.decode("utf-8"))
    return module, names


def _python_callee(node: Node) -> str | None:
    func = node.child_by_field_name("function")
    if func is None and node.children:
        func = node.children[0]
    if func is None:
        return None
    if func.type == "identifier":
        return func.text.decode("utf-8")
    if func.type == "attribute":
        return func.text.decode("utf-8")
    return None
