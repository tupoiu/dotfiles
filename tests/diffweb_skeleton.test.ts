// The skeleton view's HTML (web/skeleton.ts). Plain strings, so no browser.

import { expect, test } from "bun:test";
import { docHtml, skeletonHtml, type SkeletonResponse } from "../diffweb/web/skeleton.ts";

const data: SkeletonResponse = {
  names: 2,
  documented: 1,
  files: [{
    path: "diffweb/state.py",
    documented: 1,
    error: "",
    names: [
      { kind: "method", name: "mute", qual: "State.mute", signature: "def mute(self) -> None", doc: "Mute `pr`.", line: 7 },
      { kind: "function", name: "loud", qual: "loud", signature: "def loud()", doc: "", line: 12 },
    ],
  }],
};

test("names link to their line, with the class before a method", () => {
  const html = skeletonHtml(data);
  expect(html).toContain('data-path="diffweb/state.py" data-line="7"');
  expect(html).toContain('def <span class="owner">State.</span><span class="nm">mute</span>(self) -&gt; None');
  expect(html).not.toContain("documented</span>");  // no per-file count
  expect(html).toContain("2 public names added · 1 without a docstring");
});

test("a Rust method shows its impl type", () => {
  const n = { kind: "method", name: "new", qual: "Backend::new", signature: "fn new() -> Self", doc: "", line: 3 };
  const html = skeletonHtml({ names: 1, documented: 0, files: [{ path: "a.rs", documented: 0, error: "", names: [n] }] });
  expect(html).toContain('fn <span class="owner">Backend::</span><span class="nm">new</span>() -&gt; Self');
});

test("a name with no docstring is flagged, and a fully documented file can hide", () => {
  const html = skeletonHtml(data);
  expect(html).toContain('<div class="skel-item nodoc">');
  expect(html).toContain("No docstring.");
  expect(html).not.toContain("all-doc");
  const done = skeletonHtml({ ...data, documented: 2, files: [{ ...data.files[0]!, documented: 2 }] });
  expect(done).toContain('class="skel-group all-doc"');
});

test("docstrings draw backticks as code and escape everything else", () => {
  expect(docHtml("Read `path` <now>")).toBe("Read <code>path</code> &lt;now&gt;");
  expect(docHtml("A [`Backend`] for it")).toBe("A <code>Backend</code> for it");
});

test("an empty range says so", () => {
  expect(skeletonHtml({ names: 0, documented: 0, files: [] })).toContain("adds no public names");
});
