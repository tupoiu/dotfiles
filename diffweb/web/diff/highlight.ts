// Syntax highlighting with highlight.js, which the page loads as a global.
//
// A diff line on its own is often not valid code: the middle of a multi-line
// string or comment highlights as code. So each side of a file is highlighted
// whole, once, and the result is cut into lines. A span that crosses a line
// break is closed at the end of the line and opened again on the next one.

export interface Seg {
  text: string;      // plain text, not HTML
  scopes: string[];  // highlight.js class attributes, outermost first
}
export type HlLine = Seg[];

interface Hljs {
  highlight(code: string, options: { language: string; ignoreIllegals: boolean }): { value: string };
  getLanguage(name: string): unknown;
}

interface Mode {
  className?: string;
  begin?: RegExp | string;
  end?: RegExp | string;
  contains?: Mode[];
  variants?: Mode[];
}

// highlight.js's Markdown starts italics at any `_`, inside a word too, and
// ends them at the next `_` however many lines later. So `rendered_pin_sha`
// italicised the text up to the next underscore. Like GitHub, start only at
// an `_` with no word character before it, and end at a closing `_` or at the
// end of the line. This must run before the grammar is first compiled.
function fixMarkdownEmphasis(h: Hljs): void {
  const seen = new Set<Mode>();
  const visit = (m: Mode | undefined): void => {
    if (!m || typeof m !== "object" || seen.has(m)) return;
    seen.add(m);
    if (m.className === "emphasis") {
      for (const v of m.variants ?? []) {
        if (String(v.begin) === String(/_(?![_\s])/)) {
          v.begin = /(?<![A-Za-z0-9_])_(?![_\s])/;
          v.end = /_(?![A-Za-z0-9])|\n/;
        }
      }
    }
    for (const c of [...(m.contains ?? []), ...(m.variants ?? [])]) visit(c);
  };
  visit(h.getLanguage("markdown") as Mode | undefined);
}

let fixed: Hljs | undefined;
const hljs = (): Hljs | undefined => {
  const h = (globalThis as { hljs?: Hljs }).hljs;
  if (h && h !== fixed) {
    fixMarkdownEmphasis(h);
    fixed = h;
  }
  return h;
};

// Names highlight.js does not know as aliases. Anything else is tried as-is.
// Maps, not plain objects: a file called `constructor` must not find Object's.
const BY_EXTENSION = new Map(Object.entries({
  jsonc: "json", jsonl: "json", lock: "", tsx: "typescript", mjs: "javascript", cjs: "javascript",
  pyi: "python",
  // No Svelte grammar comes with highlight.js. Its HTML one draws the markup,
  // <script> as JavaScript and <style> as CSS; only the {#if} blocks stay plain.
  svelte: "xml",
}));
const BY_NAME = new Map(Object.entries({
  Dockerfile: "dockerfile", Makefile: "makefile", "CMakeLists.txt": "cmake",
  ".bashrc": "bash", ".zshrc": "bash", ".profile": "bash", ".gitconfig": "ini",
}));

export function languageFor(path: string): string | null {
  const h = hljs();
  if (!h) return null;
  const base = path.slice(path.lastIndexOf("/") + 1);
  const named = BY_NAME.get(base);
  if (named) return h.getLanguage(named) ? named : null;
  const dot = base.lastIndexOf(".");
  if (dot <= 0) return null;
  const ext = base.slice(dot + 1).toLowerCase();
  const lang = BY_EXTENSION.get(ext) ?? ext;
  return lang && h.getLanguage(lang) ? lang : null;
}

const ENTITIES: Record<string, string> = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#x27;": "'", "&#39;": "'" };

function unescapeHtml(s: string): string {
  return s.replace(/&(?:amp|lt|gt|quot|#x27|#39);/g, (e) => ENTITIES[e]!);
}

// highlight.js output is only text and <span class="..."> pairs.
export function splitHighlighted(html: string): HlLine[] {
  const lines: HlLine[] = [[]];
  const stack: string[] = [];
  for (const m of html.matchAll(/<span class="([^"]*)">|<\/span>|([^<]+)/g)) {
    if (m[1] !== undefined) stack.push(m[1]);
    else if (m[2] === undefined) stack.pop();
    else {
      unescapeHtml(m[2]).split("\n").forEach((part, i) => {
        if (i > 0) lines.push([]);
        if (part) lines[lines.length - 1]!.push({ text: part, scopes: [...stack] });
      });
    }
  }
  return lines;
}

// The whole file, one entry per line (index 0 is line 1). CRs are dropped,
// as the parser drops them from diff lines, so the two can be compared.
export function highlightFile(text: string, language: string): HlLine[] {
  const h = hljs();
  const body = text.replace(/\r\n/g, "\n").replace(/\n$/, "");
  if (!h) return body.split("\n").map((t) => (t ? [{ text: t, scopes: [] }] : []));
  return splitHighlighted(h.highlight(body, { language, ignoreIllegals: true }).value);
}

// One line on its own: the fallback when the whole file is not available.
export function highlightLine(text: string, language: string): HlLine {
  const h = hljs();
  if (!h || !text) return text ? [{ text, scopes: [] }] : [];
  return splitHighlighted(h.highlight(text, { language, ignoreIllegals: true }).value)[0] ?? [];
}

// Plain text lines of a file, split the same way as highlightFile.
export function fileLines(text: string): string[] {
  const body = text.replace(/\r\n/g, "\n");
  if (!body) return [];
  return body.replace(/\n$/, "").split("\n");
}
