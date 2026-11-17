// The skeleton view: every public name a range adds, grouped by file, with its
// signature and the first paragraph of its docstring (diffweb/skeleton.py
// finds them). A name with no docstring is drawn in amber, so the view is a
// docs check too.

import { esc } from "./diff/render.ts";

export interface SkeletonName {
  kind: string;
  name: string;
  qual: string;       // `State.mute` or `Backend::new` for a method
  signature: string;
  doc: string;
  line: number;       // end side, 1-based
}

export interface SkeletonFile {
  path: string;
  names: SkeletonName[];
  documented: number;
  error: string;
}

export interface SkeletonResponse {
  error?: string;
  files: SkeletonFile[];
  names: number;
  documented: number;
}

// The signature as highlighted code: the keyword before the name (`def`,
// `async function`, `struct`...) in its own colour, then a method's owner,
// dimmed, then the name, then the rest as plain text.
export function signatureHtml(n: SkeletonName): string {
  const at = n.signature.indexOf(n.name);
  if (at < 0) return esc(n.signature);
  // `State.` for a Python method, `Backend::` for a Rust one.
  const owner = n.qual !== n.name && n.qual.endsWith(n.name) ? n.qual.slice(0, -n.name.length) : "";
  const before = n.signature.slice(0, at);
  const keyword = before.trimEnd();
  return (keyword ? `<span class="k">${esc(keyword)}</span>${esc(before.slice(keyword.length))}` : esc(before)) +
    (owner ? `<span class="owner">${esc(owner)}</span>` : "") +
    `<span class="nm">${esc(n.name)}</span>` +
    esc(n.signature.slice(at + n.name.length));
}

// Docstrings use `backticks` for code; draw those as code, everything else as
// text. A rustdoc link, [`Name`], loses its brackets: there is nothing to link to.
export function docHtml(doc: string): string {
  return esc(doc).replace(/\[?`([^`]+)`\]?/g, "<code>$1</code>");
}

export function skeletonHtml(data: SkeletonResponse): string {
  const missing = data.names - data.documented;
  const plural = (k: number, word: string) => `${k} ${word}${k === 1 ? "" : "s"}`;
  const head =
    `<div class="skel-head"><span>${plural(data.names, "public name")} added` +
    ` · ${missing} without a docstring</span>` +
    `<label class="chk"><input type="checkbox" id="skel-undoc"> only undocumented</label></div>`;
  if (!data.files.length) return head + `<p class="skel-empty">This range adds no public names.</p>`;
  const groups = data.files.map((f) => {
    const allDoc = !f.error && f.documented === f.names.length;
    const items = f.names.map((n) =>
      `<div class="skel-item${n.doc ? "" : " nodoc"}">` +
      `<a class="sg" href="#" data-path="${esc(f.path)}" data-line="${n.line}"` +
      ` title="Go to ${esc(n.qual)} in the diff (line ${n.line})">${signatureHtml(n)}</a>` +
      `<div class="doc">${n.doc ? docHtml(n.doc) : "No docstring."}</div></div>`,
    );
    if (f.error) items.push(`<div class="skel-item nodoc"><div class="doc">${esc(f.error)}</div></div>`);
    return `<section class="skel-group${allDoc ? " all-doc" : ""}">` +
      `<div class="skel-file">${esc(f.path)}</div>` +
      items.join("") + `</section>`;
  });
  return head + `<div class="skel">${groups.join("")}</div>`;
}
