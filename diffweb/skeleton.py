"""The skeleton of a range: every public name it adds, with its signature and
the first paragraph of its docstring - the table of contents of a change.

Public means what another module could use:

- Python: a `def` or `class` whose name has no leading `_`, at module level or
  in a public class, but not a function nested in a function. Read with `ast`,
  so a multi-line signature comes back whole.
- TypeScript: `export function` and exported classes. The doc is the `//` or
  `/** */` comment block right above.
- Rust: `pub fn`, `pub struct`, `pub enum` and `pub trait` (bare `pub` only:
  `pub(crate)` is not public). The doc is the `///` block above, past any
  `#[...]` attributes.

Only names the range adds count: a name is new when the start of the range
does not define it in the same file (the old path, for a rename). Changed
docstrings on names that already existed are not shown.

TypeScript and Rust are read with line rules, not a parser, so a signature
whose return type opens a `{` on a line of its own is cut short there.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath


@dataclass(frozen=True)
class Name:
    kind: str       # function, method, class, struct, enum, trait
    name: str       # as written, e.g. `mute`
    qual: str       # with its owner, `State.mute` or `Backend::new`; what "new" compares
    signature: str  # `def mute(self, pr_id: str) -> None`, without a body
    doc: str        # first paragraph of the docstring, "" when there is none
    line: int       # 1-based, the `def`/`function`/`fn` line on the end side


@dataclass
class FileSkeleton:
    path: str
    names: list[Name] = field(default_factory=list)
    error: str = ""  # set when the end side could not be read as its language

    def as_json(self) -> dict[str, object]:
        documented = sum(1 for n in self.names if n.doc)
        return {
            "path": self.path,
            "names": [asdict(n) for n in self.names],
            "documented": documented,
            "error": self.error,
        }


def first_paragraph(text: str) -> str:
    """The doc up to its first blank line, as one line of prose."""
    lines: list[str] = []
    for line in text.strip().splitlines():
        if not line.strip():
            break
        lines.append(line.strip())
    return " ".join(" ".join(lines).split())


# --- Python ---------------------------------------------------------------


def _py_args(a: ast.arguments) -> str:
    """Parameters as PEP 8 writes them: `x: int = 1`, but `x=1` unannotated.
    (`ast.unparse` writes `x: int=1`.)"""

    def one(arg: ast.arg, default: ast.expr | None, star: str = "") -> str:
        ann = f": {ast.unparse(arg.annotation)}" if arg.annotation else ""
        if default is None:
            return f"{star}{arg.arg}{ann}"
        return f"{star}{arg.arg}{ann}{' = ' if ann else '='}{ast.unparse(default)}"

    positional = [*a.posonlyargs, *a.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(a.defaults)) + list(a.defaults)
    parts = [one(arg, d) for arg, d in zip(positional, defaults)]
    if a.posonlyargs:
        parts.insert(len(a.posonlyargs), "/")
    if a.vararg:
        parts.append(one(a.vararg, None, "*"))
    elif a.kwonlyargs:
        parts.append("*")
    parts += [one(arg, d) for arg, d in zip(a.kwonlyargs, a.kw_defaults)]
    if a.kwarg:
        parts.append(one(a.kwarg, None, "**"))
    return ", ".join(parts)


def _py_signature(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    if isinstance(node, ast.ClassDef):
        parents = [ast.unparse(b) for b in node.bases] + [ast.unparse(k) for k in node.keywords]
        return f"class {node.name}" + (f"({', '.join(parents)})" if parents else "")
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({_py_args(node.args)}){returns}"


def _py_walk(body: Iterable[ast.stmt], owner: str | None) -> Iterable[Name]:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.startswith("_"):
                continue  # private, and so is everything inside a private class
            is_class = isinstance(node, ast.ClassDef)
            kind = "class" if is_class else ("method" if owner else "function")
            qual = f"{owner}.{node.name}" if owner else node.name
            doc = first_paragraph(ast.get_docstring(node) or "")
            yield Name(kind, node.name, qual, _py_signature(node), doc, node.lineno)
            if is_class:
                yield from _py_walk(node.body, qual)
            # A function's body holds only locals, never public names.
        elif isinstance(node, ast.If):
            # `if TYPE_CHECKING:` and version switches define real names.
            yield from _py_walk([*node.body, *node.orelse], owner)
        elif isinstance(node, ast.Try):
            handlers = [s for h in node.handlers for s in h.body]
            yield from _py_walk([*node.body, *handlers, *node.orelse, *node.finalbody], owner)


def python_names(text: str) -> list[Name]:
    """Raises SyntaxError when `text` is not Python."""
    return list(_py_walk(ast.parse(text).body, None))


# --- TypeScript -----------------------------------------------------------

_TS_FN = re.compile(r"^export\s+(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)")
_TS_CLASS = re.compile(r"^export\s+(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)")
_TS_EXPORT = re.compile(r"^export\s+(?:default\s+)?")


def _signature_from(lines: list[str], i: int, *, stops: str = "{;") -> str:
    """The declaration starting at line i, up to (not including) the `{` or
    `;` that ends it at bracket depth 0, as one line."""
    parts: list[str] = []
    depth = 0
    for line in lines[i : i + 20]:
        for j, ch in enumerate(line):
            if ch in "([<":
                depth += 1
            elif ch in ")]>" and depth and not (ch == ">" and line[j - 1 : j] in ("=", "-")):  # => and ->
                depth -= 1
            elif ch in stops and depth == 0:
                parts.append(line[:j])
                return " ".join(" ".join(parts).split())
        parts.append(line)
    return " ".join(" ".join(parts).split())


def _comment_above(lines: list[str], i: int, *, line_prefix: str, skip: re.Pattern[str] | None) -> str:
    """The comment block that ends right above line i, first paragraph only."""
    j = i - 1
    while skip is not None and j >= 0 and skip.match(lines[j].strip()):
        j -= 1
    if j < 0:
        return ""
    if lines[j].strip().endswith("*/") and line_prefix == "//":
        end = j
        while j >= 0 and "/*" not in lines[j]:
            j -= 1
        if j < 0:
            return ""
        block = "\n".join(lines[j : end + 1])
        block = block[block.index("/*") + 2 : block.rindex("*/")].lstrip("*")
        text = [re.sub(r"^\s*\*\s?", "", ln) for ln in block.splitlines()]
    else:
        text = []
        while j >= 0 and lines[j].strip().startswith(line_prefix):
            text.append(lines[j].strip()[len(line_prefix) :].removeprefix(" "))
            j -= 1
        text.reverse()
    # A JSDoc tag ends the prose as surely as a blank line does.
    prose: list[str] = []
    for ln in text:
        if ln.strip().startswith("@"):
            break
        prose.append(ln)
    return first_paragraph("\n".join(prose))


_TS_DECORATOR = re.compile(r"^@")


def typescript_names(text: str) -> list[Name]:
    lines = text.splitlines()
    out: list[Name] = []
    for i, line in enumerate(lines):
        m = _TS_FN.match(line) or _TS_CLASS.match(line)
        if not m:
            continue
        kind = "function" if _TS_FN.match(line) else "class"
        sig = _TS_EXPORT.sub("", _signature_from(lines, i))
        doc = _comment_above(lines, i, line_prefix="//", skip=_TS_DECORATOR)
        out.append(Name(kind, m.group(1), m.group(1), sig, doc, i + 1))
    return out


# --- Rust -----------------------------------------------------------------

_RS_FN = re.compile(
    r"^\s*pub\s+(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+\"[^\"]*\"\s+)?fn\s+([A-Za-z_]\w*)"
)
_RS_TYPE = re.compile(r"^\s*pub\s+(struct|enum|trait)\s+([A-Za-z_]\w*)")
_RS_ATTR = re.compile(r"^#!?\[")


_RS_IMPL = re.compile(r"^(\s*)impl\b")


def _impl_type(line: str) -> str | None:
    """The type an `impl` line is for: `Foo` for `impl<T: Into<X>> Foo<T> {`,
    and `X` for `impl Trait for X`. None when the line is no impl."""
    m = _RS_IMPL.match(line)
    if not m:
        return None
    rest = line[m.end() :].lstrip()
    if rest.startswith("<"):  # the impl's own generics, which may nest
        depth = 0
        for j, ch in enumerate(rest):
            depth += {"<": 1, ">": -1}.get(ch, 0)
            if depth == 0:
                rest = rest[j + 1 :]
                break
    rest = re.split(r"[{]|\swhere\s", rest)[0]
    target = re.split(r"\sfor\s", rest)[-1].strip()
    name = re.match(r"[&\w:]+", target)
    return name.group(0).lstrip("&").split("::")[-1] if name else None


def rust_names(text: str) -> list[Name]:
    lines = text.splitlines()
    out: list[Name] = []
    # The impl blocks open around the current line, as (indent, type). An impl
    # ends at the `}` on its own indent - rustfmt's layout, which is the norm.
    impls: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        indent = len(line) - len(line.lstrip())
        if impls and line.strip().startswith("}") and indent == impls[-1][0]:
            impls.pop()
            continue
        owner = _impl_type(line)
        if owner is not None:
            if not line.rstrip().endswith("}"):  # not a one-line `impl X {}`
                impls.append((indent, owner))
            continue
        fn = _RS_FN.match(line)
        ty = None if fn else _RS_TYPE.match(line)
        if not (fn or ty):
            continue
        if fn:
            kind, name = "function", fn.group(1)
            sig = _signature_from(lines, i)
            sig = re.split(r"\swhere\s", sig)[0]
        else:
            assert ty is not None
            kind, name = ty.group(1), ty.group(2)
            sig = _signature_from(lines, i, stops="{;(")
        sig = re.sub(r"^pub\s+", "", sig.strip())
        doc = _comment_above(lines, i, line_prefix="///", skip=_RS_ATTR)
        inside = [t for at, t in impls if at < indent]
        qual = f"{inside[-1]}::{name}" if fn and inside else name
        out.append(Name("method" if qual != name else kind, name, qual, sig, doc, i + 1))
    return out


# --- per file ---------------------------------------------------------------

READERS = {
    ".py": python_names,
    ".ts": typescript_names,
    ".tsx": typescript_names,
    ".mts": typescript_names,
    ".rs": rust_names,
}


def supported(path: str) -> bool:
    return PurePosixPath(path).suffix in READERS


def _defined(read: object, text: str) -> set[str] | None:
    try:
        return {n.qual for n in read(text)}  # type: ignore[operator]
    except (SyntaxError, ValueError, RecursionError):
        return None


def added_names(path: str, start_text: str | None, end_text: str) -> FileSkeleton:
    """The public names `end_text` defines that `start_text` does not."""
    read = READERS[PurePosixPath(path).suffix]
    result = FileSkeleton(path)
    try:
        names = read(end_text)
    except (SyntaxError, ValueError, RecursionError) as exc:
        result.error = f"could not read this file: {exc.__class__.__name__}"
        return result
    before = _defined(read, start_text) if start_text else set()
    if before is None:
        # The start side is not valid code: fall back to "the name appears
        # nowhere in it", as blind-name-review's extractor does.
        assert start_text is not None
        result.names = [n for n in names if not re.search(rf"\b{re.escape(n.name)}\b", start_text)]
    else:
        result.names = [n for n in names if n.qual not in before]
    return result
