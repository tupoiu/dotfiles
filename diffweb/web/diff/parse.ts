// A parser for the unified diffs diffweb itself asks git for.
//
// The input is always one of:
// - `git diff --no-color -M -C` (and `git show --remerge-diff`, same format),
// - the untracked files' `git diff --no-index /dev/null <file>` patches,
// - the structural view's patch, which is git's patch with difftastic's hunks.
// So this is not a general patch reader: svn, hg and `diff -u` formats are out.
//
// Hunk bodies are read by their line counts, not by what a line looks like, so
// a content line that starts with `---`, `+++` or `diff --git` stays content.

export type LineKind = "ctx" | "add" | "del";

export interface DiffLine {
  kind: LineKind;
  oldNo: number | null;  // null on an added line
  newNo: number | null;  // null on a deleted line
  text: string;          // without the +/-/space marker and without a trailing CR
  noNewline: boolean;    // followed by "\ No newline at end of file"
}

export interface Hunk {
  header: string;        // the whole `@@ ... @@ context` line
  oldStart: number;
  oldCount: number;
  newStart: number;
  newCount: number;
  lines: DiffLine[];
}

export type FileStatus = "added" | "deleted" | "renamed" | "copied" | "modified";

export interface DiffFile {
  oldPath: string;       // the same as newPath unless renamed or copied
  newPath: string;
  status: FileStatus;
  binary: boolean;
  oldMode: string;       // "" when git did not say
  newMode: string;
  similarity: number | null;  // percent, on a rename or a copy
  notes: string[];       // extended header lines this parser does not model, e.g. `remerge CONFLICT ...`
  hunks: Hunk[];
}

const HUNK_RE = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;
// git's own prefixes, and the ones `diff.mnemonicPrefix` uses instead.
const PREFIX_RE = /^[abciwo]\//;

// git quotes a path with C escapes when it holds a quote, a backslash, a
// control character or (with the default core.quotePath) any non-ASCII byte,
// which it writes as octal. The octal escapes are UTF-8 bytes, not characters.
export function unquote(s: string): string {
  if (!s.startsWith('"') || !s.endsWith('"') || s.length < 2) return s;
  const bytes: number[] = [];
  const named: Record<string, number> = { a: 7, b: 8, t: 9, n: 10, v: 11, f: 12, r: 13, '"': 34, "\\": 92 };
  const enc = new TextEncoder();
  for (let i = 1; i < s.length - 1; i++) {
    const c = s[i]!;
    if (c !== "\\") {
      bytes.push(...enc.encode(c));
      continue;
    }
    const next = s[i + 1] ?? "";
    const octal = /^[0-7]{3}/.exec(s.slice(i + 1, i + 4));
    if (octal) {
      bytes.push(parseInt(octal[0], 8));
      i += 3;
    } else {
      bytes.push(named[next] ?? next.charCodeAt(0));
      i += 1;
    }
  }
  return new TextDecoder().decode(new Uint8Array(bytes));
}

function stripPrefix(p: string): string {
  return p.replace(PREFIX_RE, "");
}

// A path from a `---`/`+++` line. git adds a trailing tab when the name has a
// space in it, so that `patch` can tell where the name ends.
function headerPath(rest: string): string | null {
  const raw = rest.replace(/\t$/, "");
  if (raw === "/dev/null") return null;
  return stripPrefix(unquote(raw));
}

// The two names on a `diff --git a/x b/y` line. Unquoted names may hold
// spaces, so the line alone is ambiguous; git's own rule is to assume both
// names are the same, which they are unless the file was renamed or copied,
// and those cases have `rename from`/`copy from` lines that win anyway.
export function gitHeaderPaths(rest: string): [string, string] {
  if (rest.startsWith('"')) {
    const end = closingQuote(rest, 0);
    const a = rest.slice(0, end + 1);
    const b = rest.slice(end + 2);
    return [stripPrefix(unquote(a)), stripPrefix(unquote(b))];
  }
  const quotedB = rest.indexOf(' "');
  if (quotedB !== -1) {
    return [stripPrefix(rest.slice(0, quotedB)), stripPrefix(unquote(rest.slice(quotedB + 1)))];
  }
  if ((rest.length - 1) % 2 === 0) {
    const half = (rest.length - 1) / 2;
    const a = rest.slice(0, half);
    const b = rest.slice(half + 1);
    if (rest[half] === " " && stripPrefix(a) === stripPrefix(b)) return [stripPrefix(a), stripPrefix(b)];
  }
  const split = rest.indexOf(" b/");
  if (split !== -1) return [stripPrefix(rest.slice(0, split)), stripPrefix(rest.slice(split + 1))];
  const space = rest.indexOf(" ");
  return [stripPrefix(rest.slice(0, space)), stripPrefix(rest.slice(space + 1))];
}

function closingQuote(s: string, open: number): number {
  for (let i = open + 1; i < s.length; i++) {
    if (s[i] === "\\") i++;
    else if (s[i] === '"') return i;
  }
  return s.length - 1;
}

function newFile(oldPath: string, newPath: string): DiffFile {
  return {
    oldPath, newPath, status: "modified", binary: false, oldMode: "", newMode: "",
    similarity: null, notes: [], hunks: [],
  };
}

export function parseDiff(text: string): DiffFile[] {
  const lines = text.split(/\r?\n/);
  // A diff ends with a newline, which leaves one empty string that is not a line.
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  const files: DiffFile[] = [];
  let file: DiffFile | null = null;
  let i = 0;

  while (i < lines.length) {
    const line = lines[i]!;

    if (line.startsWith("diff --git ")) {
      const [a, b] = gitHeaderPaths(line.slice("diff --git ".length));
      file = newFile(a, b);
      files.push(file);
      i++;
      continue;
    }
    if (!file) { i++; continue; }  // anything before the first file header

    const hunk = HUNK_RE.exec(line);
    if (hunk) {
      i = readHunk(lines, i, hunk, file);
      continue;
    }

    readHeaderLine(line, file);
    if (line.startsWith("GIT binary patch")) {
      // Only `--binary` asks for one; skip its payload up to the next file.
      while (i + 1 < lines.length && !lines[i + 1]!.startsWith("diff --git ")) i++;
    }
    i++;
  }
  return files;
}

function readHeaderLine(line: string, file: DiffFile): void {
  const field = (prefix: string) => (line.startsWith(prefix) ? line.slice(prefix.length) : null);
  let v: string | null;
  if ((v = field("old mode ")) !== null) file.oldMode = v;
  else if ((v = field("new mode ")) !== null) file.newMode = v;
  else if ((v = field("new file mode ")) !== null) { file.status = "added"; file.newMode = v; }
  else if ((v = field("deleted file mode ")) !== null) { file.status = "deleted"; file.oldMode = v; }
  else if ((v = field("rename from ")) !== null) { file.status = "renamed"; file.oldPath = unquote(v); }
  else if ((v = field("rename to ")) !== null) { file.status = "renamed"; file.newPath = unquote(v); }
  else if ((v = field("copy from ")) !== null) { file.status = "copied"; file.oldPath = unquote(v); }
  else if ((v = field("copy to ")) !== null) { file.status = "copied"; file.newPath = unquote(v); }
  else if ((v = field("similarity index ")) !== null) file.similarity = parseInt(v, 10);
  else if ((v = field("dissimilarity index ")) !== null) file.similarity = 100 - parseInt(v, 10);
  else if ((v = field("index ")) !== null) {
    // `index abc..def 100644` carries the mode when it did not change.
    const mode = v.split(" ")[1];
    if (mode && !file.oldMode && !file.newMode) file.oldMode = file.newMode = mode;
  }
  else if (line.startsWith("Binary files ") || line.startsWith("GIT binary patch")) file.binary = true;
  else if ((v = field("--- ")) !== null) {
    const p = headerPath(v);
    if (p === null) file.status = "added";
    else if (file.status !== "renamed" && file.status !== "copied") file.oldPath = p;
  }
  else if ((v = field("+++ ")) !== null) {
    const p = headerPath(v);
    if (p === null) file.status = "deleted";
    else if (file.status !== "renamed" && file.status !== "copied") file.newPath = p;
  }
  else if (line.trim()) file.notes.push(line);
}

// Reads one hunk from lines[start] (its header) and returns the index after it.
function readHunk(lines: string[], start: number, m: RegExpExecArray, file: DiffFile): number {
  const hunk: Hunk = {
    header: lines[start]!,
    oldStart: +m[1]!,
    oldCount: m[2] === undefined ? 1 : +m[2],
    newStart: +m[3]!,
    newCount: m[4] === undefined ? 1 : +m[4],
    lines: [],
  };
  file.hunks.push(hunk);
  let oldNo = hunk.oldStart;
  let newNo = hunk.newStart;
  let oldLeft = hunk.oldCount;
  let newLeft = hunk.newCount;
  let i = start + 1;

  const push = (kind: LineKind, text: string) => {
    hunk.lines.push({
      kind,
      oldNo: kind === "add" ? null : oldNo++,
      newNo: kind === "del" ? null : newNo++,
      text,
      noNewline: false,
    });
  };

  while (i < lines.length && (oldLeft > 0 || newLeft > 0)) {
    const line = lines[i]!;
    const mark = line[0];
    if (mark === "\\") {
      markNoNewline(hunk);
    } else if (mark === "-" && oldLeft > 0) {
      push("del", line.slice(1)); oldLeft--;
    } else if (mark === "+" && newLeft > 0) {
      push("add", line.slice(1)); newLeft--;
    } else if ((mark === " " || line === "") && oldLeft > 0 && newLeft > 0) {
      // An empty line is a context line whose single space an editor or a
      // mail client trimmed away.
      push("ctx", line.slice(1)); oldLeft--; newLeft--;
    } else {
      break;  // the counts were wrong; stop rather than eat the next file
    }
    i++;
  }
  // The marker for the hunk's last line comes after the counts run out.
  if (i < lines.length && lines[i]!.startsWith("\\")) {
    markNoNewline(hunk);
    i++;
  }
  return i;
}

function markNoNewline(hunk: Hunk): void {
  const last = hunk.lines[hunk.lines.length - 1];
  if (last) last.noNewline = true;
}

// The path a file is known by: the new name, or the old one for a deletion.
export function filePath(file: DiffFile): string {
  return file.status === "deleted" ? file.oldPath : file.newPath;
}
