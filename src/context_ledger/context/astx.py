"""Layer 3 - structure.

The single largest source of wasted context is reading a whole file to look at
one function. This layer makes the narrow read possible: it turns a source file
into a list of symbols with exact line ranges, so a caller can ask for
``auth.py::TokenStore.refresh`` and pay for forty lines instead of nine hundred.

Two engines, one interface:

* **Python** goes through the standard library's own ``ast`` module. That is a
  real parse - decorators, nested classes, async defs, docstrings and end line
  numbers all come out exact.
* **Everything else** goes through a structural line scanner with per-language
  declaration patterns and brace/indent tracking. It is not a parser and does
  not pretend to be; it recovers the declarations that matter for navigation and
  degrades to "no symbols" rather than to wrong ones.

Named ``astx`` rather than ``ast`` so it never shadows the stdlib module it uses.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Sequence

# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------


@dataclass
class Symbol:
    """One declaration, located precisely enough to slice it back out.

    ``start`` and ``end`` are 1-based and inclusive, matching every editor and
    every ``sed -n`` the output might be pasted into.
    """

    name: str
    kind: str  # function | method | class | interface | type | const | section | ...
    start: int
    end: int
    signature: str
    parent: Optional[str] = None
    doc: Optional[str] = None
    language: str = ""
    decorators: List[str] = field(default_factory=list)

    @property
    def qualname(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name

    @property
    def lines(self) -> int:
        return self.end - self.start + 1

    def to_dict(self) -> dict:
        data = asdict(self)
        data["qualname"] = self.qualname
        data["lines"] = self.lines
        return data


# --------------------------------------------------------------------------
# Language detection
# --------------------------------------------------------------------------

EXTENSION_LANGUAGES: Dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".cs": "csharp", ".rb": "ruby", ".php": "php", ".swift": "swift", ".scala": "scala",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".sql": "sql", ".md": "markdown", ".markdown": "markdown",
    ".yml": "yaml", ".yaml": "yaml", ".json": "json", ".tf": "terraform",
}


#: Content signatures used only when the path carries no usable extension -
#: piped stdin, a file named `-`, an extensionless script. Each pattern must be
#: distinctive enough that a false positive is unlikely; ambiguity returns
#: "text" and the caller falls back to line windows.
_SNIFF_PATTERNS = (
    ("python", re.compile(r"^\s*(?:def\s+\w+\s*\(|class\s+\w+[\s(:]|from\s+[\w.]+\s+import\s|import\s+\w)", re.MULTILINE)),
    ("typescript", re.compile(r"^\s*(?:export\s+(?:interface|type|class|const|function)\b|interface\s+\w+\s*\{)", re.MULTILINE)),
    ("javascript", re.compile(r"^\s*(?:function\s+\w+\s*\(|const\s+\w+\s*=\s*(?:async\s*)?\(|module\.exports\b)", re.MULTILINE)),
    ("go", re.compile(r"^\s*(?:package\s+\w+|func\s+(?:\(\w)?)", re.MULTILINE)),
    ("rust", re.compile(r"^\s*(?:pub\s+)?(?:fn\s+\w+|struct\s+\w+|impl\s+)", re.MULTILINE)),
    ("markdown", re.compile(r"^#{1,6}\s+\S", re.MULTILINE)),
)


def sniff_language(source: str) -> str:
    """Guess a language from content alone. Returns "text" when unsure."""
    sample = (source or "")[:8000]
    if not sample.strip():
        return "text"
    for language, pattern in _SNIFF_PATTERNS:
        if pattern.search(sample):
            return language
    return "text"


def detect_language(path: str = "", source: str = "") -> str:
    """Language for ``path``, falling back to a shebang, then to a content sniff.

    The content sniff matters more than it looks: reading from stdin gives a path
    of ``-`` or nothing at all, and without it every piped source silently
    extracted zero symbols.
    """
    lowered = (path or "").lower()
    if lowered not in ("", "-"):
        for ext, lang in sorted(EXTENSION_LANGUAGES.items(), key=lambda kv: -len(kv[0])):
            if lowered.endswith(ext):
                return lang
    head = (source or "")[:200]
    if head.startswith("#!"):
        first = head.split("\n", 1)[0]
        for token, lang in (("python", "python"), ("node", "javascript"), ("bash", "bash"), ("sh", "bash")):
            if token in first:
                return lang
    return sniff_language(source)


# --------------------------------------------------------------------------
# Python engine - the real AST
# --------------------------------------------------------------------------

_PY_KINDS = {
    ast.FunctionDef: "function",
    ast.AsyncFunctionDef: "function",
    ast.ClassDef: "class",
}


def _py_signature(node: ast.AST, lines: Sequence[str]) -> str:
    """The declaration header, joined across a multi-line argument list."""
    start = getattr(node, "lineno", 1) - 1
    collected = []
    for raw in lines[start : start + 12]:
        collected.append(raw.strip())
        joined = " ".join(collected)
        # A header ends at the colon that opens the suite, once brackets balance.
        if joined.rstrip().endswith(":") and joined.count("(") <= joined.count(")"):
            return re.sub(r"\s+", " ", joined).rstrip(":").strip() + ":"
    return re.sub(r"\s+", " ", " ".join(collected)).strip()


def _py_decorators(node: ast.AST) -> List[str]:
    out = []
    for dec in getattr(node, "decorator_list", []) or []:
        try:
            out.append("@" + ast.unparse(dec))
        except Exception:  # pragma: no cover - unparse is best-effort
            out.append("@<decorator>")
    return out


def _extract_python(source: str) -> List[Symbol]:
    """Walk the real Python AST. Returns [] on a syntax error, never raises."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.split("\n")
    symbols: List[Symbol] = []

    def visit(node: ast.AST, parent: Optional[str]) -> None:
        for child in ast.iter_child_nodes(node):
            kind = _PY_KINDS.get(type(child))
            if kind is None:
                # Module-level assignments read as constants when SHOUTY_CASE.
                if parent is None and isinstance(child, (ast.Assign, ast.AnnAssign)):
                    targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id.isupper():
                            line = child.lineno
                            symbols.append(
                                Symbol(
                                    name=target.id,
                                    kind="const",
                                    start=line,
                                    end=getattr(child, "end_lineno", line) or line,
                                    signature=lines[line - 1].strip(),
                                    parent=None,
                                    language="python",
                                )
                            )
                continue

            if kind == "function" and parent is not None:
                kind = "method"
            symbols.append(
                Symbol(
                    name=child.name,  # type: ignore[attr-defined]
                    kind=kind,
                    start=child.lineno,  # type: ignore[attr-defined]
                    end=getattr(child, "end_lineno", child.lineno) or child.lineno,  # type: ignore[attr-defined]
                    signature=_py_signature(child, lines),
                    parent=parent,
                    doc=ast.get_docstring(child),  # type: ignore[arg-type]
                    language="python",
                    decorators=_py_decorators(child),
                )
            )
            if isinstance(child, ast.ClassDef):
                visit(child, child.name)

    visit(tree, None)
    symbols.sort(key=lambda s: (s.start, s.end))
    return symbols


# --------------------------------------------------------------------------
# Structural engine - everything else
# --------------------------------------------------------------------------

#: Per-language declaration patterns. Each must expose a ``name`` group; ``kind``
#: is taken from the pattern's label. Deliberately conservative: a missed symbol
#: costs a wider read, a wrong one costs the caller's trust.
_PATTERNS: Dict[str, List[tuple]] = {
    "typescript": [
        ("class", re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(?P<name>\w+)")),
        ("interface", re.compile(r"^\s*(?:export\s+)?interface\s+(?P<name>\w+)")),
        ("type", re.compile(r"^\s*(?:export\s+)?type\s+(?P<name>\w+)\s*=")),
        ("enum", re.compile(r"^\s*(?:export\s+)?(?:const\s+)?enum\s+(?P<name>\w+)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s+(?P<name>\w+)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>\w+)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*(?::[^=]+)?=>")),
        ("const", re.compile(r"^\s*export\s+(?:const|let|var)\s+(?P<name>\w+)\s*[:=]")),
        ("method", re.compile(r"^\s{2,}(?:public|private|protected|static|readonly|async|get|set|\s)*\s*(?P<name>\w+)\s*\([^;]*\)\s*(?::\s*[^;{]+)?\{")),
    ],
    "go": [
        ("function", re.compile(r"^func\s+(?P<name>\w+)\s*\(")),
        ("method", re.compile(r"^func\s+\([^)]*\)\s+(?P<name>\w+)\s*\(")),
        ("type", re.compile(r"^type\s+(?P<name>\w+)\s+")),
        ("const", re.compile(r"^(?:const|var)\s+(?P<name>\w+)\s")),
    ],
    "rust": [
        ("function", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(?P<name>\w+)")),
        ("struct", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?struct\s+(?P<name>\w+)")),
        ("enum", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?enum\s+(?P<name>\w+)")),
        ("trait", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?trait\s+(?P<name>\w+)")),
        ("impl", re.compile(r"^\s*impl(?:<[^>]*>)?\s+(?P<name>[\w:<>, ]+?)\s*\{")),
    ],
    "java": [
        ("class", re.compile(r"^\s*(?:public|private|protected|abstract|final|static|\s)*class\s+(?P<name>\w+)")),
        ("interface", re.compile(r"^\s*(?:public|private|protected|\s)*interface\s+(?P<name>\w+)")),
        ("enum", re.compile(r"^\s*(?:public|private|protected|\s)*enum\s+(?P<name>\w+)")),
        ("method", re.compile(r"^\s+(?:public|private|protected|static|final|synchronized|abstract|native|\s)+[\w<>\[\],.?]+\s+(?P<name>\w+)\s*\([^;]*\)\s*(?:throws [\w., ]+)?\{")),
    ],
    "csharp": [
        ("class", re.compile(r"^\s*(?:public|private|protected|internal|abstract|sealed|static|partial|\s)*class\s+(?P<name>\w+)")),
        ("interface", re.compile(r"^\s*(?:public|private|protected|internal|\s)*interface\s+(?P<name>\w+)")),
        ("record", re.compile(r"^\s*(?:public|private|protected|internal|\s)*record\s+(?P<name>\w+)")),
        ("method", re.compile(r"^\s+(?:public|private|protected|internal|static|async|override|virtual|sealed|\s)+[\w<>\[\],.?]+\s+(?P<name>\w+)\s*\([^;]*\)\s*\{?")),
    ],
    "ruby": [
        ("class", re.compile(r"^\s*class\s+(?P<name>[\w:]+)")),
        ("module", re.compile(r"^\s*module\s+(?P<name>[\w:]+)")),
        ("method", re.compile(r"^\s*def\s+(?P<name>[\w.?!=]+)")),
    ],
    "php": [
        ("class", re.compile(r"^\s*(?:abstract\s+|final\s+)?class\s+(?P<name>\w+)")),
        ("interface", re.compile(r"^\s*interface\s+(?P<name>\w+)")),
        ("trait", re.compile(r"^\s*trait\s+(?P<name>\w+)")),
        ("function", re.compile(r"^\s*(?:public|private|protected|static|abstract|final|\s)*function\s+(?P<name>\w+)\s*\(")),
    ],
    "bash": [
        ("function", re.compile(r"^\s*(?:function\s+)?(?P<name>[\w.-]+)\s*\(\s*\)\s*\{")),
    ],
    "sql": [
        ("table", re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[\w.\"`]+)", re.IGNORECASE)),
        ("view", re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+(?P<name>[\w.\"`]+)", re.IGNORECASE)),
        ("function", re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE)\s+(?P<name>[\w.\"`]+)", re.IGNORECASE)),
    ],
    "terraform": [
        ("resource", re.compile(r"^\s*resource\s+\"(?P<name>[\w.-]+)\"\s+\"(?P<label>[\w.-]+)\"")),
        ("module", re.compile(r"^\s*module\s+\"(?P<name>[\w.-]+)\"")),
        ("variable", re.compile(r"^\s*variable\s+\"(?P<name>[\w.-]+)\"")),
    ],
}
_PATTERNS["javascript"] = _PATTERNS["typescript"]
_PATTERNS["kotlin"] = [
    ("class", re.compile(r"^\s*(?:public|private|internal|open|abstract|sealed|data|\s)*class\s+(?P<name>\w+)")),
    ("interface", re.compile(r"^\s*(?:public|private|internal|\s)*interface\s+(?P<name>\w+)")),
    ("function", re.compile(r"^\s*(?:public|private|internal|suspend|override|inline|\s)*fun\s+(?:<[^>]+>\s*)?(?P<name>[\w.]+)\s*\(")),
]
_PATTERNS["swift"] = [
    ("class", re.compile(r"^\s*(?:public|private|internal|open|final|\s)*class\s+(?P<name>\w+)")),
    ("struct", re.compile(r"^\s*(?:public|private|internal|\s)*struct\s+(?P<name>\w+)")),
    ("protocol", re.compile(r"^\s*(?:public|private|internal|\s)*protocol\s+(?P<name>\w+)")),
    ("function", re.compile(r"^\s*(?:public|private|internal|static|override|\s)*func\s+(?P<name>\w+)")),
]
_PATTERNS["c"] = [
    ("function", re.compile(r"^[\w][\w \t*]*\s\**(?P<name>\w+)\s*\([^;]*\)\s*\{")),
    ("struct", re.compile(r"^\s*(?:typedef\s+)?struct\s+(?P<name>\w+)")),
]
_PATTERNS["cpp"] = _PATTERNS["c"] + [
    ("class", re.compile(r"^\s*class\s+(?P<name>\w+)")),
    ("namespace", re.compile(r"^\s*namespace\s+(?P<name>\w+)")),
]
_PATTERNS["scala"] = [
    ("class", re.compile(r"^\s*(?:case\s+)?class\s+(?P<name>\w+)")),
    ("object", re.compile(r"^\s*(?:case\s+)?object\s+(?P<name>\w+)")),
    ("trait", re.compile(r"^\s*trait\s+(?P<name>\w+)")),
    ("function", re.compile(r"^\s*(?:private|protected|override|implicit|\s)*def\s+(?P<name>\w+)")),
]

#: Languages whose blocks end when braces balance back to the opening depth.
_BRACE_LANGUAGES = {
    "typescript", "javascript", "go", "rust", "java", "csharp", "php", "kotlin",
    "swift", "c", "cpp", "scala", "bash", "terraform",
}

_COMMENT_PREFIXES = ("//", "#", "--", "/*", "*")

#: Control-flow and declaration keywords that structurally look like a call site
#: to the "method" patterns. Without this, `if (x) {` reads as a method named
#: `if`. Any real symbol sharing one of these names is not worth the false
#: positives it would buy back.
_RESERVED_NAMES = frozenset({
    "if", "else", "for", "while", "switch", "case", "catch", "try", "finally",
    "do", "return", "with", "using", "lock", "when", "match", "loop", "unless",
    "elif", "except", "foreach", "select", "where", "yield", "await", "new",
})
# Note: `get`, `set` and `constructor` are deliberately NOT reserved. They are
# real, frequently-searched member names, and excluding them to avoid the rare
# accessor-keyword false positive loses far more than it saves.


def _strip_noise(line: str) -> str:
    """Blank out string and comment content so their braces do not skew depth."""
    out = re.sub(r"\"(?:\\.|[^\"\\])*\"", '""', line)
    out = re.sub(r"'(?:\\.|[^'\\])*'", "''", out)
    out = re.sub(r"`(?:\\.|[^`\\])*`", "``", out)
    out = re.sub(r"//.*$", "", out)
    out = re.sub(r"/\*.*?\*/", "", out)
    return out


def _block_end_braces(lines: Sequence[str], start_idx: int) -> int:
    """1-based end line of the brace block opening at ``start_idx``."""
    depth = 0
    opened = False
    for idx in range(start_idx, min(len(lines), start_idx + 4000)):
        cleaned = _strip_noise(lines[idx])
        for char in cleaned:
            if char == "{":
                depth += 1
                opened = True
            elif char == "}":
                depth -= 1
                if opened and depth <= 0:
                    return idx + 1
        if not opened and idx > start_idx + 8:
            # A declaration with no body within a few lines: a signature only.
            return start_idx + 1
    return min(len(lines), start_idx + 1)


def _block_end_indent(lines: Sequence[str], start_idx: int) -> int:
    """1-based end line of an indentation-delimited block (Ruby-style uses `end`)."""
    base = len(lines[start_idx]) - len(lines[start_idx].lstrip())
    last = start_idx
    for idx in range(start_idx + 1, len(lines)):
        line = lines[idx]
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= base:
            stripped = line.strip()
            if stripped == "end" or stripped.startswith("end "):
                return idx + 1
            return last + 1
        last = idx
    return last + 1


def _preceding_doc(lines: Sequence[str], start_idx: int) -> Optional[str]:
    """Contiguous comment block immediately above a declaration, if any."""
    collected = []
    idx = start_idx - 1
    while idx >= 0 and len(collected) < 20:
        stripped = lines[idx].strip()
        if not stripped:
            break
        if stripped.startswith(_COMMENT_PREFIXES) or stripped.endswith("*/"):
            collected.append(stripped)
            idx -= 1
            continue
        break
    if not collected:
        return None
    return "\n".join(reversed(collected))


def _extract_markdown(source: str) -> List[Symbol]:
    """Headings become symbols; a section runs until the next heading of equal or
    higher rank. Fenced code is skipped so ``# comment`` inside a block is not a
    heading."""
    lines = source.split("\n")
    heads = []
    in_fence = False
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(?P<name>.+?)\s*#*\s*$", line)
        if match:
            heads.append((idx, len(match.group(1)), match.group("name").strip()))

    symbols: List[Symbol] = []
    for position, (idx, level, name) in enumerate(heads):
        end = len(lines)
        for later_idx, later_level, _ in heads[position + 1 :]:
            if later_level <= level:
                end = later_idx
                break
        parent = None
        for prev_idx, prev_level, prev_name in reversed(heads[:position]):
            if prev_level < level:
                parent = prev_name
                break
        symbols.append(
            Symbol(
                name=name,
                kind=f"h{level}",
                start=idx + 1,
                end=max(idx + 1, end),
                signature=lines[idx].strip(),
                parent=parent,
                language="markdown",
            )
        )
    return symbols


def _extract_structural(source: str, language: str) -> List[Symbol]:
    patterns = _PATTERNS.get(language)
    if not patterns:
        return []
    lines = source.split("\n")
    symbols: List[Symbol] = []
    in_fence = False

    for idx, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith(_COMMENT_PREFIXES):
            continue
        for kind, pattern in patterns:
            match = pattern.match(line)
            if not match:
                continue
            name = match.group("name")
            if name in _RESERVED_NAMES and kind in {"method", "function"}:
                break
            label = match.groupdict().get("label")
            if label:
                name = f"{name}.{label}"
            if language in _BRACE_LANGUAGES:
                end = _block_end_braces(lines, idx)
            else:
                end = _block_end_indent(lines, idx)
            symbols.append(
                Symbol(
                    name=name,
                    kind=kind,
                    start=idx + 1,
                    end=max(end, idx + 1),
                    signature=re.sub(r"\s+", " ", line.strip()).rstrip("{").strip(),
                    doc=_preceding_doc(lines, idx),
                    language=language,
                )
            )
            break

    # Attribute nested declarations to the enclosing container by line range, so
    # `Class.method` addressing works the same way it does for Python.
    containers = [s for s in symbols if s.kind in {"class", "interface", "struct", "trait", "impl", "module", "object", "record", "namespace", "enum"}]
    for symbol in symbols:
        if symbol in containers:
            continue
        enclosing = [c for c in containers if c.start < symbol.start and symbol.end <= c.end]
        if enclosing:
            innermost = max(enclosing, key=lambda c: c.start)
            symbol.parent = innermost.name
            if symbol.kind == "function":
                symbol.kind = "method"

    symbols.sort(key=lambda s: (s.start, s.end))
    return symbols


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def extract_symbols(source: str, language: str = "", path: str = "") -> List[Symbol]:
    """Extract every declaration from ``source``.

    Never raises on malformed input - a file that cannot be parsed yields an
    empty list, and callers fall back to a line-window read.
    """
    lang = language or detect_language(path, source)
    if lang == "python":
        return _extract_python(source)
    if lang == "markdown":
        return _extract_markdown(source)
    return _extract_structural(source, lang)


def find_symbol(symbols: Sequence[Symbol], selector: str) -> Optional[Symbol]:
    """Resolve ``name`` or ``Parent.name`` against extracted symbols.

    An exact qualified match wins; a bare name matches the shortest qualname that
    ends with it, so ``refresh`` finds ``TokenStore.refresh`` when unambiguous.
    """
    selector = selector.strip()
    for symbol in symbols:
        if symbol.qualname == selector:
            return symbol
    for symbol in symbols:
        if symbol.name == selector:
            return symbol
    tail_matches = [s for s in symbols if s.qualname.endswith(f".{selector}")]
    if tail_matches:
        return min(tail_matches, key=lambda s: len(s.qualname))
    lowered = selector.lower()
    for symbol in symbols:
        if symbol.name.lower() == lowered:
            return symbol
    return None


def slice_symbol(source: str, selector: str, language: str = "", path: str = "") -> Optional[dict]:
    """Return just the source text of one symbol, with its line range.

    This is the payoff of the whole layer: the caller pays for the symbol, not
    for the file.
    """
    symbols = extract_symbols(source, language, path)
    symbol = find_symbol(symbols, selector)
    if symbol is None:
        return None
    lines = source.split("\n")
    text = "\n".join(lines[symbol.start - 1 : symbol.end])
    return {
        "symbol": symbol.to_dict(),
        "text": text,
        "start": symbol.start,
        "end": symbol.end,
        "tokens": None,  # filled by callers that care; keeps this module dependency-light
    }


def skeleton(source: str, language: str = "", path: str = "", include_docs: bool = True) -> str:
    """The file with every body elided - signatures, docstrings, line numbers.

    A skeleton typically costs 5-15% of the file and answers most of the
    questions that would otherwise have triggered a full read.
    """
    symbols = extract_symbols(source, language, path)
    lang = language or detect_language(path, source)
    if not symbols:
        return ""

    out: List[str] = []
    for symbol in symbols:
        indent = "  " if symbol.parent else ""
        for decorator in symbol.decorators:
            out.append(f"{indent}{symbol.start:>5}: {decorator}")
        out.append(f"{indent}{symbol.start:>5}: {symbol.signature}")
        if include_docs and symbol.doc:
            first = symbol.doc.strip().split("\n")[0][:110]
            if first:
                out.append(f"{indent}       | {first}")
        body = symbol.lines - 1
        if body > 0 and symbol.kind not in {"const", "h1", "h2", "h3", "h4", "h5", "h6"}:
            out.append(f"{indent}       ... {body} lines ({symbol.start}-{symbol.end})")
    plural = "" if len(symbols) == 1 else "s"
    header = (
        f"# skeleton: {path or '<source>'} [{lang}] "
        f"{len(symbols)} symbol{plural}, {len(source.splitlines())} lines"
    )
    return "\n".join([header, *out])


def outline(source: str, language: str = "", path: str = "") -> List[dict]:
    """A nested tree of symbols - the machine-readable sibling of ``skeleton``."""
    symbols = extract_symbols(source, language, path)
    by_name: Dict[str, dict] = {}
    roots: List[dict] = []
    for symbol in symbols:
        node = {**symbol.to_dict(), "children": []}
        by_name.setdefault(symbol.name, node)
        parent = by_name.get(symbol.parent) if symbol.parent else None
        if parent is not None:
            parent["children"].append(node)
        else:
            roots.append(node)
    return roots
