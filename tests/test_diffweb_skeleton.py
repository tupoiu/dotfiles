"""diffweb: the skeleton view - the public names a range adds, with docstrings."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from diffweb import gitio, skeleton
from diffweb.config import load_config
from diffweb.gitio import INDEX, WORKTREE

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env={**os.environ, **GIT_ENV})
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


def src(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


def quals(names: list[skeleton.Name]) -> list[str]:
    return [n.qual for n in names]


# --- Python ---------------------------------------------------------------


PY = src('''
    def public(a: int, b: str = "x", *, c=1) -> bool:
        """Say whether it worked.

        Everything after the first paragraph is left out.
        """
        def flush():
            """A local helper, not public."""

    def _private():
        pass

    class Store(Base, metaclass=Meta):
        """Holds things,
        across two lines."""

        def get(self, key: str) -> str | None:
            pass

        def _cache(self):
            pass

        class Inner:
            def deep(self): ...

    class _Hidden:
        def visible_name_in_private_class(self): ...

    async def fetch(url: str) -> bytes:
        """Fetch it."""

    if TYPE_CHECKING:
        def typed() -> None: ...
''')


def test_python_public_names_skip_underscores_and_nested_functions() -> None:
    assert quals(skeleton.python_names(PY)) == [
        "public", "Store", "Store.get", "Store.Inner", "Store.Inner.deep", "fetch", "typed",
    ]


def test_python_signatures_and_first_paragraph() -> None:
    by = {n.qual: n for n in skeleton.python_names(PY)}
    assert by["public"].signature == 'def public(a: int, b: str = \'x\', *, c=1) -> bool'
    assert by["public"].doc == "Say whether it worked."
    assert by["public"].kind == "function" and by["public"].line == 1
    assert by["Store"].signature == "class Store(Base, metaclass=Meta)"
    assert by["Store"].doc == "Holds things, across two lines."
    assert by["Store.get"].kind == "method" and by["Store.get"].doc == ""
    assert by["fetch"].signature == "async def fetch(url: str) -> bytes"


# --- TypeScript -----------------------------------------------------------


TS = src('''
    import { x } from "./x";

    // Split a file into lines.
    //
    // The second paragraph is left out.
    export function fileLines(text: string): string[] {
      return text.split("\\n");
    }

    /**
     * Render one file.
     * @param v the view
     */
    export async function renderFile(
      v: FileView,
      attrs: { id: string },
    ): Promise<string> {
    }

    function internal(): void {}

    export class Store<T> extends Base {
      method(): void {}
    }

    export const notAFunction = 1;
    export function cb(f: (a: number) => void): void {}
''')


def test_typescript_exports_with_comment_docs() -> None:
    names = skeleton.typescript_names(TS)
    assert quals(names) == ["fileLines", "renderFile", "Store", "cb"]
    by = {n.qual: n for n in names}
    assert by["fileLines"].doc == "Split a file into lines."
    assert by["fileLines"].signature == "function fileLines(text: string): string[]"
    assert by["renderFile"].doc == "Render one file."  # stops at the @param tag
    assert by["renderFile"].signature == "async function renderFile( v: FileView, attrs: { id: string }, ): Promise<string>"
    assert by["Store"].signature == "class Store<T> extends Base" and by["Store"].doc == ""
    assert by["cb"].signature == "function cb(f: (a: number) => void): void"


# --- Rust -----------------------------------------------------------------


RS = src('''
    /// Parse a range.
    ///
    /// More detail.
    #[inline]
    pub fn parse<'a>(s: &'a str) -> Result<Range, Error> where Error: Debug {
    }

    pub(crate) fn crate_only() {}
    fn private() {}

    /// A span of lines.
    pub struct Span { from: u32 }

    pub enum Kind { A, B }
''')


def test_rust_pub_items_with_doc_comments() -> None:
    names = skeleton.rust_names(RS)
    assert quals(names) == ["parse", "Span", "Kind"]
    by = {n.qual: n for n in names}
    assert by["parse"].signature == "fn parse<'a>(s: &'a str) -> Result<Range, Error>"
    assert by["parse"].doc == "Parse a range."
    assert by["Span"].signature == "struct Span" and by["Span"].doc == "A span of lines."
    assert by["Kind"].doc == ""


RS_IMPLS = src('''
    pub struct Backend;

    impl<T: Into<String>> Backend<T> {
        /// Make one.
        pub fn new() -> Self {
            fn helper() {}
        }
    }

    impl fmt::Display for crate::errors::Backend {
        pub fn fmt(&self) {}
    }

    impl Marker for Backend {}

    pub fn free() {}
''')


def test_rust_methods_carry_their_impl_type() -> None:
    names = skeleton.rust_names(RS_IMPLS)
    assert [(n.qual, n.kind) for n in names] == [
        ("Backend", "struct"), ("Backend::new", "method"), ("Backend::fmt", "method"), ("free", "function"),
    ]
    assert names[1].doc == "Make one."


def test_a_new_rust_method_with_a_known_name_is_new() -> None:
    before = "pub struct A;\nimpl A {\n    pub fn new() {}\n}\n"
    after = before + "pub struct B;\nimpl B {\n    pub fn new() {}\n}\n"
    assert quals(skeleton.added_names("lib.rs", before, after).names) == ["B", "B::new"]


# --- only what the range adds ---------------------------------------------


def test_only_names_new_to_the_file_count() -> None:
    before = src('''
        def kept():
            """Old."""

        class Store:
            def get(self): ...
    ''')
    after = before + src('''
        def added(): ...

        class Other:
            def get(self): ...
    ''')
    found = skeleton.added_names("m.py", before, after)
    # Other.get is new even though Store.get already existed.
    assert quals(found.names) == ["added", "Other", "Other.get"]
    assert quals(skeleton.added_names("m.py", None, after).names)[:2] == ["kept", "Store"]


def test_unreadable_start_side_falls_back_to_a_word_search() -> None:
    found = skeleton.added_names("m.py", "def old(:\n", "def old(): ...\ndef new(): ...\n")
    assert quals(found.names) == ["new"]


def test_unreadable_end_side_is_reported() -> None:
    found = skeleton.added_names("m.py", None, "def broken(:\n")
    assert found.names == [] and "SyntaxError" in found.error


# --- reading many files at once --------------------------------------------


def test_blobs_text_reads_commits_the_index_and_the_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    git(tmp_path, "init", "-q", "-b", "master", str(repo))
    (repo / "a.py").write_text("one\n")
    (repo / "b.bin").write_bytes(b"\0\1")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    (repo / "a.py").write_text("two\n")
    git(repo, "add", "a.py")
    (repo / "a.py").write_text("three\n")
    files = ["a.py", "b.bin", "missing.py"]
    assert gitio.blobs_text(str(repo), "HEAD", files) == {"a.py": "one\n", "b.bin": None, "missing.py": None}
    assert gitio.blobs_text(str(repo), INDEX, ["a.py"]) == {"a.py": "two\n"}
    assert gitio.blobs_text(str(repo), WORKTREE, ["a.py"]) == {"a.py": "three\n"}


# --- the endpoint ------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    repo = tmp_path / "proj"
    git(tmp_path, "init", "-q", "-b", "master", str(repo))
    (repo / "lib.py").write_text('def old():\n    """Was here."""\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "lib.py").write_text('def old():\n    """Was here."""\n\n\ndef new(x: int) -> int:\n    """Double it."""\n')
    (repo / "tests").mkdir()
    (repo / "tests" / "test_lib.py").write_text("def test_new(): ...\n")
    (repo / "notes.md").write_text("# not code\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add new")
    (repo / "web.ts").write_text("export function undocumented(): void {}\n")  # untracked

    cfg = tmp_path / "diffweb.yaml"
    cfg.write_text(yaml.safe_dump({
        "roots": [str(repo)], "state_db": str(tmp_path / "state.db"),
        "profile_log": str(tmp_path / "profiles.jsonl"), "tests": {"paths": ["tests/**"]},
    }))
    monkeypatch.setenv("DIFFWEB_CONFIG", str(cfg))
    from diffweb import app as app_module

    app_module.reset_for_tests()
    with TestClient(app_module.app) as c:
        c.wt = gitio.discover(load_config())[0].id  # type: ignore[attr-defined]
        yield c
    app_module.reset_for_tests()


def test_skeleton_endpoint_lists_added_public_names(client: TestClient) -> None:
    r = client.get(f"/api/w/{client.wt}/skeleton", params={"base": "master"})  # type: ignore[attr-defined]
    assert r.status_code == 200
    data = r.json()
    by_file = {f["path"]: f for f in data["files"]}
    assert set(by_file) == {"lib.py", "tests/test_lib.py", "web.ts"}
    assert [(n["name"], n["doc"], n["line"]) for n in by_file["lib.py"]["names"]] == [("new", "Double it.", 5)]
    assert by_file["web.ts"]["documented"] == 0
    assert (data["names"], data["documented"]) == (3, 1)

    # tests=false leaves the test files out; a commit end reads git, not disk.
    r = client.get(f"/api/w/{client.wt}/skeleton", params={"base": "master", "end": "HEAD", "tests": "false"})  # type: ignore[attr-defined]
    assert [f["path"] for f in r.json()["files"]] == ["lib.py"]
