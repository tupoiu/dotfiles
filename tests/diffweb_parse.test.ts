// Parser tests for web/diff/parse.ts.
//
// Many input diffs here are ported from diff2html's test suite
// (https://github.com/rtfpessoa/diff2html, src/__tests__/diff-parser-tests.ts):
//
// Copyright 2014-2016 Rodrigo Fernandes https://rtfpessoa.github.io/
//
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
// documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
// rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
// persons to whom the Software is furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
// Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
// WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
// COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
// OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
//
// The expectations are written by hand from what git's format means. The
// "real git" inputs come from git 2.34 in a throwaway repo.

import { describe, expect, test } from "bun:test";
import { type DiffFile, type Hunk, filePath, gitHeaderPaths, parseDiff, unquote } from "../diffweb/web/diff/parse.ts";

// One row per line: [kind, oldNo, newNo].
function rows(hunk: Hunk): [string, number | null, number | null][] {
  return hunk.lines.map((l) => [l.kind, l.oldNo, l.newNo]);
}

function texts(hunk: Hunk): string[] {
  return hunk.lines.map((l) => l.text);
}

// Parses a diff that must hold exactly one file.
function only(diff: string): DiffFile {
  const files = parseDiff(diff);
  expect(files).toHaveLength(1);
  return files[0]!;
}

function hunk(file: DiffFile, n = 0): Hunk {
  const h = file.hunks[n];
  if (!h) throw new Error(`no hunk ${n}`);
  return h;
}

describe("ported from diff2html", () => {
  test("a diff with LF line endings gives one modified file", () => {
    const diff =
      "diff --git a/sample b/sample\n" +
      "index 0000001..0ddf2ba\n" +
      "--- a/sample\n" +
      "+++ b/sample\n" +
      "@@ -1 +1 @@\n" +
      "-test\n" +
      "+test1r\n";
    const f = only(diff);
    expect(f).toMatchObject({
      oldPath: "sample", newPath: "sample", status: "modified", binary: false,
      oldMode: "", newMode: "", similarity: null, notes: [],
    });
    expect(hunk(f)).toMatchObject({ header: "@@ -1 +1 @@", oldStart: 1, oldCount: 1, newStart: 1, newCount: 1 });
    expect(hunk(f).lines).toEqual([
      { kind: "del", oldNo: 1, newNo: null, text: "test", noNewline: false },
      { kind: "add", oldNo: null, newNo: 1, text: "test1r", noNewline: false },
    ]);
  });

  test("a diff with CRLF line endings parses the same and drops the CR", () => {
    const diff =
      "diff --git a/sample b/sample\r\n" +
      "index 0000001..0ddf2ba\r\n" +
      "--- a/sample\r\n" +
      "+++ b/sample\r\n" +
      "@@ -1 +1 @@\r\n" +
      "-test\r\n" +
      "+test1r\r\n";
    const f = only(diff);
    expect([f.oldPath, f.newPath, f.status]).toEqual(["sample", "sample", "modified"]);
    expect(hunk(f).header).toBe("@@ -1 +1 @@");
    expect(rows(hunk(f))).toEqual([["del", 1, null], ["add", null, 1]]);
    expect(texts(hunk(f))).toEqual(["test", "test1r"]);
  });

  test("a quoted path with a raw tab and spaces keeps them and loses the prefix", () => {
    // diff2html's input has a real tab character inside the quotes.
    const diff =
      'diff --git "a/bla with \ttab.scala" "b/bla with \ttab.scala"\n' +
      "index 4c679d7..e9bd385 100644\n" +
      '--- "a/bla with \ttab.scala"\n' +
      '+++ "b/bla with \ttab.scala"\n' +
      "@@ -1 +1,2 @@\n" +
      "-cenas\n" +
      "+cenas com ananas\n" +
      "+bananas";
    const f = only(diff);
    expect([f.oldPath, f.newPath]).toEqual(["bla with \ttab.scala", "bla with \ttab.scala"]);
    expect([f.oldMode, f.newMode]).toEqual(["100644", "100644"]);
    expect(rows(hunk(f))).toEqual([["del", 1, null], ["add", null, 1], ["add", null, 2]]);
    expect(texts(hunk(f))).toEqual(["cenas", "cenas com ananas", "bananas"]);
  });

  test("a quoted path with an escaped tab, as git writes it, becomes a real tab", () => {
    const diff =
      'diff --git "a/bla with \\ttab.scala" "b/bla with \\ttab.scala"\n' +
      "index 4c679d7..e9bd385 100644\n" +
      '--- "a/bla with \\ttab.scala"\n' +
      '+++ "b/bla with \\ttab.scala"\n' +
      "@@ -1 +1,2 @@\n" +
      "-cenas\n" +
      "+cenas com ananas\n" +
      "+bananas\n";
    const f = only(diff);
    expect([f.oldPath, f.newPath]).toEqual(["bla with \ttab.scala", "bla with \ttab.scala"]);
    expect(hunk(f).lines).toHaveLength(3);
  });

  test("a deleted file has its old mode, its old path and only deleted lines", () => {
    const diff =
      "diff --git a/src/var/strundefined.js b/src/var/strundefined.js\n" +
      "deleted file mode 100644\n" +
      "index 04e16b0..0000000\n" +
      "--- a/src/var/strundefined.js\n" +
      "+++ /dev/null\n" +
      "@@ -1,3 +0,0 @@\n" +
      "-define(() => {\n" +
      "-  return typeof undefined;\n" +
      "-});\n";
    const f = only(diff);
    expect(f).toMatchObject({ status: "deleted", oldMode: "100644", newMode: "", oldPath: "src/var/strundefined.js" });
    expect(filePath(f)).toBe("src/var/strundefined.js");
    expect(hunk(f)).toMatchObject({ oldStart: 1, oldCount: 3, newStart: 0, newCount: 0 });
    expect(rows(hunk(f))).toEqual([["del", 1, null], ["del", 2, null], ["del", 3, null]]);
    expect(texts(hunk(f))).toEqual(["define(() => {", "  return typeof undefined;", "});"]);
  });

  test("a new file has its new mode and only added lines, empty ones included", () => {
    const diff =
      "diff --git a/test.js b/test.js\n" +
      "new file mode 100644\n" +
      "index 0000000..e1e22ec\n" +
      "--- /dev/null\n" +
      "+++ b/test.js\n" +
      "@@ -0,0 +1,5 @@\n" +
      "+var parser = require('./source/git-parser');\n" +
      "+\n" +
      "+var patchLineList = [ false, false, false, false ];\n" +
      "+\n" +
      "+console.log(parser.parsePatchDiffResult(text, patchLineList));\n";
    const f = only(diff);
    expect(f).toMatchObject({ status: "added", oldMode: "", newMode: "100644", newPath: "test.js" });
    expect(filePath(f)).toBe("test.js");
    expect(hunk(f)).toMatchObject({ oldStart: 0, oldCount: 0, newStart: 1, newCount: 5 });
    expect(rows(hunk(f))).toEqual([1, 2, 3, 4, 5].map((n) => ["add", null, n]));
    expect(texts(hunk(f))).toEqual([
      "var parser = require('./source/git-parser');",
      "",
      "var patchLineList = [ false, false, false, false ];",
      "",
      "console.log(parser.parsePatchDiffResult(text, patchLineList));",
    ]);
  });

  test("diff text inside an added line stays content", () => {
    // diff2html's header says `-1,6 +1,5` for 6 added lines; git counts right.
    const nested =
      "var text = 'diff --git a/components/app/app.html b/components/app/app.html\\nindex ecb7a95..027bd9b 100644\\n--- a/components/app/app.html\\n+++ b/components/app/app.html\\n@@ -52,0 +53,3 @@\\n+\\n+\\n+\\n@@ -56,0 +60,3 @@\\n+\\n+\\n+\\n'";
    const diff =
      "diff --git a/src/offset.js b/src/offset.js\n" +
      "index cc6ffb4..fa51f18 100644\n" +
      "--- a/src/offset.js\n" +
      "+++ b/src/offset.js\n" +
      "@@ -0,0 +1,6 @@\n" +
      "+var parser = require('./source/git-parser');\n" +
      "+\n" +
      "+var text = 'diff --git a/components/app/app.html b/components/app/app.html\\nindex ecb7a95..027bd9b 100644\\n--- a/components/app/app.html\\n+++ b/components/app/app.html\\n@@ -52,0 +53,3 @@\\n+\\n+\\n+\\n@@ -56,0 +60,3 @@\\n+\\n+\\n+\\n'\n" +
      "+var patchLineList = [ false, false, false, false ];\n" +
      "+\n" +
      "+console.log(parser.parsePatchDiffResult(text, patchLineList));\n";
    const f = only(diff);
    expect(f.hunks).toHaveLength(1);
    expect(f.notes).toEqual([]);
    expect(rows(hunk(f))).toEqual([1, 2, 3, 4, 5, 6].map((n) => ["add", null, n]));
    expect(hunk(f).lines[2]!.text).toBe(nested);
  });

  test("two hunks in one file count their lines from their own starts", () => {
    const diff =
      "diff --git a/src/attributes/classes.js b/src/attributes/classes.js\n" +
      "index c617824..c8d1393 100644\n" +
      "--- a/src/attributes/classes.js\n" +
      "+++ b/src/attributes/classes.js\n" +
      "@@ -1,10 +1,9 @@\n" +
      " define([\n" +
      '   "../core",\n' +
      '   "../var/rnotwhite",\n' +
      '-  "../var/strundefined",\n' +
      '   "../data/var/dataPriv",\n' +
      '   "../core/init"\n' +
      "-], function( jQuery, rnotwhite, strundefined, dataPriv ) {\n" +
      "+], function( jQuery, rnotwhite, dataPriv ) {\n" +
      " \n" +
      " var rclass = /[\\t\\r\\n\\f]/g;\n" +
      " \n" +
      "@@ -128,7 +127,7 @@ jQuery.fn.extend({\n" +
      "         }\n" +
      " \n" +
      "       // Toggle whole class name\n" +
      '-      } else if ( type === strundefined || type === "boolean" ) {\n' +
      '+      } else if ( value === undefined || type === "boolean" ) {\n' +
      "         if ( this.className ) {\n" +
      "           // store className if set\n" +
      '           dataPriv.set( this, "__className__", this.className );\n';
    const f = only(diff);
    expect(f.hunks).toHaveLength(2);
    expect(hunk(f, 0)).toMatchObject({ header: "@@ -1,10 +1,9 @@", oldStart: 1, oldCount: 10, newStart: 1, newCount: 9 });
    expect(rows(hunk(f, 0))).toEqual([
      ["ctx", 1, 1], ["ctx", 2, 2], ["ctx", 3, 3],
      ["del", 4, null],
      ["ctx", 5, 4], ["ctx", 6, 5],
      ["del", 7, null], ["add", null, 6],
      ["ctx", 8, 7], ["ctx", 9, 8], ["ctx", 10, 9],
    ]);
    expect(hunk(f, 0).lines[9]!.text).toBe("var rclass = /[\\t\\r\\n\\f]/g;");
    expect(hunk(f, 1)).toMatchObject({
      header: "@@ -128,7 +127,7 @@ jQuery.fn.extend({", oldStart: 128, oldCount: 7, newStart: 127, newCount: 7,
    });
    expect(rows(hunk(f, 1))).toEqual([
      ["ctx", 128, 127], ["ctx", 129, 128], ["ctx", 130, 129],
      ["del", 131, null], ["add", null, 130],
      ["ctx", 132, 131], ["ctx", 133, 132], ["ctx", 134, 133],
    ]);
    expect(hunk(f, 1).lines[0]!.text).toBe("        }");
    expect(hunk(f, 1).lines[1]!.text).toBe("");
  });

  test("two files each get their own paths and hunks", () => {
    const diff =
      "diff --git a/src/core/init.js b/src/core/init.js\n" +
      "index e49196a..50f310c 100644\n" +
      "--- a/src/core/init.js\n" +
      "+++ b/src/core/init.js\n" +
      "@@ -101,7 +101,7 @@ var rootjQuery,\n" +
      "     // HANDLE: $(function)\n" +
      "     // Shortcut for document ready\n" +
      "     } else if ( jQuery.isFunction( selector ) ) {\n" +
      '-      return typeof rootjQuery.ready !== "undefined" ?\n' +
      "+      return rootjQuery.ready !== undefined ?\n" +
      "         rootjQuery.ready( selector ) :\n" +
      "         // Execute immediately if ready is not present\n" +
      "         selector( jQuery );\n" +
      "diff --git a/src/event.js b/src/event.js\n" +
      "index 7336f4d..6183f70 100644\n" +
      "--- a/src/event.js\n" +
      "+++ b/src/event.js\n" +
      "@@ -1,6 +1,5 @@\n" +
      " define([\n" +
      '   "./core",\n' +
      '-  "./var/strundefined",\n' +
      '   "./var/rnotwhite",\n' +
      '   "./var/hasOwn",\n' +
      '   "./var/slice",\n';
    const files = parseDiff(diff);
    expect(files.map((f) => [f.oldPath, f.newPath, f.status, f.hunks.length])).toEqual([
      ["src/core/init.js", "src/core/init.js", "modified", 1],
      ["src/event.js", "src/event.js", "modified", 1],
    ]);
    expect(rows(hunk(files[0]!))).toEqual([
      ["ctx", 101, 101], ["ctx", 102, 102], ["ctx", 103, 103],
      ["del", 104, null], ["add", null, 104],
      ["ctx", 105, 105], ["ctx", 106, 106], ["ctx", 107, 107],
    ]);
    expect(rows(hunk(files[1]!))).toEqual([
      ["ctx", 1, 1], ["ctx", 2, 2], ["del", 3, null], ["ctx", 4, 3], ["ctx", 5, 4], ["ctx", 6, 5],
    ]);
    expect(hunk(files[1]!).lines[5]!.text).toBe('  "./var/slice",');
  });

  test("a copy with a dissimilarity index keeps both names and turns it into similarity", () => {
    const diff =
      "diff --git a/index.js b/more-index.js\n" +
      "dissimilarity index 5%\n" +
      "copy from index.js\n" +
      "copy to more-index.js\n";
    const f = only(diff);
    expect(f).toMatchObject({ status: "copied", oldPath: "index.js", newPath: "more-index.js", similarity: 95, hunks: [] });
  });

  test("a pure rename has no hunks and keeps its similarity", () => {
    const diff =
      "diff --git a/more-index.js b/other-index.js\n" +
      "similarity index 86%\n" +
      "rename from more-index.js\n" +
      "rename to other-index.js\n";
    const f = only(diff);
    expect(f).toMatchObject({
      status: "renamed", oldPath: "more-index.js", newPath: "other-index.js", similarity: 86, hunks: [], notes: [],
    });
    expect(filePath(f)).toBe("other-index.js");
  });

  test("a rename with content changes keeps both names and its hunk", () => {
    const diff =
      "diff --git a/src/test-bar.js b/src/test-baz.js\n" +
      "similarity index 98%\n" +
      "rename from src/test-bar.js\n" +
      "rename to src/test-baz.js\n" +
      "index e01513b..f14a870 100644\n" +
      "--- a/src/test-bar.js\n" +
      "+++ b/src/test-baz.js\n" +
      "@@ -1,4 +1,32 @@\n" +
      " function foo() {\n" +
      '-var bar = "Whoops!";\n' +
      '+var baz = "Whoops!";\n' +
      " }\n" +
      " ";
    const f = only(diff);
    expect(f).toMatchObject({
      status: "renamed", oldPath: "src/test-bar.js", newPath: "src/test-baz.js", similarity: 98,
      oldMode: "100644", newMode: "100644",
    });
    expect(rows(hunk(f))).toEqual([["ctx", 1, 1], ["del", 2, null], ["add", null, 2], ["ctx", 3, 3], ["ctx", 4, 4]]);
    expect(texts(hunk(f))).toEqual(["function foo() {", 'var bar = "Whoops!";', 'var baz = "Whoops!";', "}", ""]);
  });

  test("a hunk that ends early at the end of input keeps the lines it has", () => {
    // The header says 2 new lines but the input stops after 1.
    const diff =
      "diff --git a/sample b/sample\n" +
      "index 0000001..0ddf2ba\n" +
      "--- a/sample\n" +
      "+++ b/sample\n" +
      "@@ -1 +1,2 @@\n" +
      "-test\n" +
      "+test1r\n";
    const f = only(diff);
    expect(hunk(f)).toMatchObject({ oldStart: 1, oldCount: 1, newStart: 1, newCount: 2 });
    expect(rows(hunk(f))).toEqual([["del", 1, null], ["add", null, 1]]);
  });

  test("lines that start with -- or ++ after the marker stay content", () => {
    // Adapted: diff2html's input has no `diff --git` line and says `-1,8 +1,8`
    // for 6 lines on each side. A second file follows to show the hunk ends
    // where its counts say.
    const diff =
      "diff --git a/sample.js b/sample.js\n" +
      "index 0000001..0ddf2ba 100644\n" +
      "--- a/sample.js\n" +
      "+++ b/sample.js\n" +
      "@@ -1,6 +1,6 @@\n" +
      " test\n" +
      " \n" +
      "-- 1\n" +
      "--- 1\n" +
      "---- 1\n" +
      " \n" +
      "++ 2\n" +
      "+++ 2\n" +
      "++++ 2\n" +
      "diff --git a/other.js b/other.js\n" +
      "index 0000002..0ddf2bb 100644\n" +
      "--- a/other.js\n" +
      "+++ b/other.js\n" +
      "@@ -1 +1 @@\n" +
      "--- a/x\n" +
      "++++ b/x\n";
    const files = parseDiff(diff);
    expect(files.map((f) => [f.oldPath, f.newPath])).toEqual([["sample.js", "sample.js"], ["other.js", "other.js"]]);
    const h = hunk(files[0]!);
    expect(rows(h)).toEqual([
      ["ctx", 1, 1], ["ctx", 2, 2],
      ["del", 3, null], ["del", 4, null], ["del", 5, null],
      ["ctx", 6, 3],
      ["add", null, 4], ["add", null, 5], ["add", null, 6],
    ]);
    expect(texts(h)).toEqual(["test", "", "- 1", "-- 1", "--- 1", "", "+ 2", "++ 2", "+++ 2"]);
    expect(files[0]!.notes).toEqual([]);
    expect(texts(hunk(files[1]!))).toEqual(["-- a/x", "+++ b/x"]);
  });

  test("a binary file is marked binary and has no hunks", () => {
    const diff =
      "diff --git a/last-changes-config.png b/last-changes-config.png\n" +
      "index 322248b..56fc1f2 100644\n" +
      "--- a/last-changes-config.png\n" +
      "+++ b/last-changes-config.png\n" +
      "Binary files differ";
    const f = only(diff);
    expect(f).toMatchObject({ binary: true, status: "modified", newPath: "last-changes-config.png", hunks: [], notes: [] });
  });

  test("a GIT binary patch payload is skipped up to the next file", () => {
    const diff =
      "diff --git a/favicon.png b/favicon.png\n" +
      "deleted file mode 100644\n" +
      "index 2a9d516a5647205d7be510dd0dff93a3663eff6f..0000000000000000000000000000000000000000\n" +
      "GIT binary patch\n" +
      "literal 0\n" +
      "HcmV?d00001\n" +
      "\n" +
      "literal 471\n" +
      "zcmeAS@N?(olHy`uVBq!ia0vp^0wB!61|;P_|4#%`EX7WqAsj$Z!;#Vf<Z~8yL>4nJ\n" +
      "za0`Jj<E6WGe}IBwC9V-A&PAz-C7Jno3L%-fsSJk3`UaNzMkcGzh!g=;$beJ?=ckpF\n" +
      "zCl;kLIHu$$r7E~(7NwTw7iAYKI0u`(*t4mJfq_xq)5S5wqIc=!hrWj$cv|<b{x!c(\n" +
      "z;3r#y;31Y&=1q>qPVOAS4ANVKzqmCp=Cty@U^(7zk!jHsvT~YI{F^=Ex6g|gox78w\n" +
      "z+Sn2Du3GS9U7qU`1*NYYlJi3u-!<?H-eky}wyIIL;8VU@wCDrb0``&v(jQ*DWSR4K\n" +
      "zPq(3;isEyho{emNa=%%!jDPE`l3u;5d=q=<+v8kO-=C`*G#t-*AiE-D>-_B#8k9H0\n" +
      "zGl{FnZs<2$wz5^=Q2h-1XI^s{LQL1#T4epqNPC%Orl(tD_@!*EY++~^Lt2<2&!&%=\n" +
      "z`m>(TYj6uS7jDdt=eH>iOyQg(QMR<-Fw8)Dk^ZG)XQTuzEgl{`GpS?Cfq9818R9~=\n" +
      "z{&h9@9n8F^?|qusoPy{k#%tVHzu7H$t26CR`BJZk*Ixf&u36WuS=?6m2^ho-p00i_\n" +
      "I>zopr0Nz-&lmGw#\n" +
      "diff --git a/src/test-bar.js b/src/test-baz.js\n" +
      "similarity index 98%\n" +
      "rename from src/test-bar.js\n" +
      "rename to src/test-baz.js\n" +
      "index e01513b..f14a870 100644\n" +
      "--- a/src/test-bar.js\n" +
      "+++ b/src/test-baz.js\n" +
      "@@ -1,4 +1,32 @@\n" +
      " function foo() {\n" +
      '-var bar = "Whoops!";\n' +
      '+var baz = "Whoops!";\n' +
      " }\n" +
      " ";
    const files = parseDiff(diff);
    expect(files).toHaveLength(2);
    expect(files[0]).toMatchObject({
      oldPath: "favicon.png", status: "deleted", binary: true, oldMode: "100644", hunks: [], notes: [],
    });
    expect(files[1]).toMatchObject({ status: "renamed", oldPath: "src/test-bar.js", newPath: "src/test-baz.js" });
    expect(hunk(files[1]!).lines).toHaveLength(5);
  });

  test("\\ No newline at end of file marks the line before it", () => {
    // From diff2html-tests.ts; the ---/+++ paths are made to match the
    // `diff --git` line, as git writes them.
    const diff =
      "diff --git a/sample.scala b/sample.scala\n" +
      "index b583263..8b2fc3e 100644\n" +
      "--- a/sample.scala\n" +
      "+++ b/sample.scala\n" +
      "@@ -50,5 +50,7 @@ case class Response[+A](value: Option[A],\n" +
      " object ResponseErrorCode extends JsonEnumeration {\n" +
      "  val NoError, ServiceError, JsonError,\n" +
      "  InvalidPermissions, MissingPermissions, GenericError,\n" +
      "-  TokenRevoked, MissingToken = Value\n" +
      "-}\n" +
      "\\ No newline at end of file\n" +
      "+  TokenRevoked, MissingToken,\n" +
      "+  IndexLock, RepositoryError, NotValidRepo, PullRequestNotMergeable, BranchError,\n" +
      "+  PluginError, CodeParserError, EngineError = Value\n" +
      "+}\n";
    const f = only(diff);
    const h = hunk(f);
    expect(h).toMatchObject({ oldStart: 50, oldCount: 5, newStart: 50, newCount: 7 });
    expect(h.header).toBe("@@ -50,5 +50,7 @@ case class Response[+A](value: Option[A],");
    expect(rows(h)).toEqual([
      ["ctx", 50, 50], ["ctx", 51, 51], ["ctx", 52, 52],
      ["del", 53, null], ["del", 54, null],
      ["add", null, 53], ["add", null, 54], ["add", null, 55], ["add", null, 56],
    ]);
    expect(h.lines.map((l) => l.noNewline)).toEqual([false, false, false, false, true, false, false, false, false]);
    expect(h.lines[4]!.text).toBe("}");
    expect(h.lines[1]!.text).toBe(" val NoError, ServiceError, JsonError,");
  });
});

describe("real git output", () => {
  test("files of many kinds in one diff come out in order", () => {
    // `git diff --cached -M -C` after a chmod, a submodule bump, an edit to a
    // non-ASCII and a spaced name, a delete hunk, and an empty file renamed.
    const diff =
      'diff --git "a/caf\\303\\251.txt" "b/caf\\303\\251.txt"\n' +
      "index 587be6b..b77b4eb 100644\n" +
      '--- "a/caf\\303\\251.txt"\n' +
      '+++ "b/caf\\303\\251.txt"\n' +
      "@@ -1 +1,2 @@\n" +
      " x\n" +
      "+y\n" +
      "diff --git a/del.txt b/del.txt\n" +
      "index f00c965..c3a2d1c 100644\n" +
      "--- a/del.txt\n" +
      "+++ b/del.txt\n" +
      "@@ -1,9 +1,6 @@\n" +
      " 1\n" +
      " 2\n" +
      " 3\n" +
      "-4\n" +
      "-5\n" +
      "-6\n" +
      " 7\n" +
      " 8\n" +
      " 9\n" +
      "diff --git a/empty-gone b/empty-new\n" +
      "similarity index 100%\n" +
      "rename from empty-gone\n" +
      "rename to empty-new\n" +
      "diff --git a/eof.txt b/eof.txt\n" +
      "index efb7da3..08d1404 100644\n" +
      "--- a/eof.txt\n" +
      "+++ b/eof.txt\n" +
      "@@ -1,2 +1,2 @@\n" +
      "-old\n" +
      "-last\n" +
      "\\ No newline at end of file\n" +
      "+new\n" +
      "+last2\n" +
      "\\ No newline at end of file\n" +
      "diff --git a/run.sh b/run.sh\n" +
      "old mode 100644\n" +
      "new mode 100755\n" +
      "diff --git a/sub b/sub\n" +
      "index f00f577..7be43c4 160000\n" +
      "--- a/sub\n" +
      "+++ b/sub\n" +
      "@@ -1 +1 @@\n" +
      "-Subproject commit f00f5772b6f8aa64658ecb3194c9cab274465628\n" +
      "+Subproject commit 7be43c45b72a8e7818c71362af5241ead035c8f2\n" +
      "diff --git a/with space.txt b/with space.txt\n" +
      "index 587be6b..b77b4eb 100644\n" +
      "--- a/with space.txt\t\n" +
      "+++ b/with space.txt\t\n" +
      "@@ -1 +1,2 @@\n" +
      " x\n" +
      "+y\n";
    const files = parseDiff(diff);
    expect(files.map((f) => [f.oldPath, f.newPath, f.status, f.hunks.length])).toEqual([
      ["café.txt", "café.txt", "modified", 1],
      ["del.txt", "del.txt", "modified", 1],
      ["empty-gone", "empty-new", "renamed", 0],
      ["eof.txt", "eof.txt", "modified", 1],
      ["run.sh", "run.sh", "modified", 0],
      ["sub", "sub", "modified", 1],
      ["with space.txt", "with space.txt", "modified", 1],
    ]);
    expect(files.every((f) => f.notes.length === 0 && !f.binary)).toBe(true);
    expect(rows(hunk(files[1]!))).toEqual([
      ["ctx", 1, 1], ["ctx", 2, 2], ["ctx", 3, 3],
      ["del", 4, null], ["del", 5, null], ["del", 6, null],
      ["ctx", 7, 4], ["ctx", 8, 5], ["ctx", 9, 6],
    ]);
    expect(files[2]!.similarity).toBe(100);
  });

  test("a mode-only change has both modes and no hunks", () => {
    const diff =
      "diff --git a/run.sh b/run.sh\n" +
      "old mode 100644\n" +
      "new mode 100755\n";
    const f = only(diff);
    expect(f).toMatchObject({
      oldPath: "run.sh", newPath: "run.sh", status: "modified", oldMode: "100644", newMode: "100755",
      hunks: [], binary: false, notes: [],
    });
  });

  test("an added empty file has no hunks", () => {
    const diff =
      "diff --git a/only-new b/only-new\n" +
      "new file mode 100644\n" +
      "index 0000000..e69de29\n";
    const f = only(diff);
    expect(f).toMatchObject({
      oldPath: "only-new", newPath: "only-new", status: "added", newMode: "100644", hunks: [], binary: false, notes: [],
    });
  });

  test("a deleted empty file has no hunks", () => {
    const diff =
      "diff --git a/only-new b/only-new\n" +
      "deleted file mode 100644\n" +
      "index e69de29..0000000\n";
    const f = only(diff);
    expect(f).toMatchObject({ oldPath: "only-new", status: "deleted", oldMode: "100644", hunks: [], notes: [] });
    expect(filePath(f)).toBe("only-new");
  });

  test("a submodule pointer change is a one-line hunk with the gitlink mode", () => {
    const diff =
      "diff --git a/sub b/sub\n" +
      "index f00f577..7be43c4 160000\n" +
      "--- a/sub\n" +
      "+++ b/sub\n" +
      "@@ -1 +1 @@\n" +
      "-Subproject commit f00f5772b6f8aa64658ecb3194c9cab274465628\n" +
      "+Subproject commit 7be43c45b72a8e7818c71362af5241ead035c8f2\n";
    const f = only(diff);
    expect(f).toMatchObject({ newPath: "sub", status: "modified", oldMode: "160000", newMode: "160000" });
    expect(hunk(f).lines).toEqual([
      { kind: "del", oldNo: 1, newNo: null, text: "Subproject commit f00f5772b6f8aa64658ecb3194c9cab274465628", noNewline: false },
      { kind: "add", oldNo: null, newNo: 1, text: "Subproject commit 7be43c45b72a8e7818c71362af5241ead035c8f2", noNewline: false },
    ]);
  });

  test("a non-ASCII path quoted with octal escapes decodes as UTF-8", () => {
    const diff =
      'diff --git "a/caf\\303\\251.txt" "b/caf\\303\\251.txt"\n' +
      "index 587be6b..b77b4eb 100644\n" +
      '--- "a/caf\\303\\251.txt"\n' +
      '+++ "b/caf\\303\\251.txt"\n' +
      "@@ -1 +1,2 @@\n" +
      " x\n" +
      "+y\n";
    const f = only(diff);
    expect([f.oldPath, f.newPath]).toEqual(["café.txt", "café.txt"]);
    expect(rows(hunk(f))).toEqual([["ctx", 1, 1], ["add", null, 2]]);
  });

  test("a rename between quoted non-ASCII names takes them from rename from/to", () => {
    const diff =
      'diff --git "a/caf\\303\\251.txt" "b/na\\303\\257ve.txt"\n' +
      "similarity index 100%\n" +
      'rename from "caf\\303\\251.txt"\n' +
      'rename to "na\\303\\257ve.txt"\n';
    const f = only(diff);
    expect(f).toMatchObject({ status: "renamed", oldPath: "café.txt", newPath: "naïve.txt", similarity: 100, hunks: [] });
  });

  test("a rename to a name with a quote in it unquotes the new name", () => {
    const diff =
      'diff --git a/run.sh "b/say \\"hi\\".sh"\n' +
      "similarity index 100%\n" +
      "rename from run.sh\n" +
      'rename to "say \\"hi\\".sh"\n';
    const f = only(diff);
    expect(f).toMatchObject({ status: "renamed", oldPath: "run.sh", newPath: 'say "hi".sh' });
  });

  test("a path with a space loses the trailing tab git puts on ---/+++", () => {
    // git 2.34 adds the tab; the `diff --git` line has none.
    const diff =
      "diff --git a/with space.txt b/with space.txt\n" +
      "index 587be6b..b77b4eb 100644\n" +
      "--- a/with space.txt\t\n" +
      "+++ b/with space.txt\t\n" +
      "@@ -1 +1,2 @@\n" +
      " x\n" +
      "+y\n";
    const f = only(diff);
    expect([f.oldPath, f.newPath]).toEqual(["with space.txt", "with space.txt"]);
  });

  test('a path that holds " b/" is split where both halves match', () => {
    const diff =
      "diff --git a/x b/y.txt b/x b/y.txt\n" +
      "index bca70f3..8a08eba 100644\n" +
      "--- a/x b/y.txt\t\n" +
      "+++ b/x b/y.txt\t\n" +
      "@@ -1 +1,2 @@\n" +
      " q\n" +
      "+r\n";
    const f = only(diff);
    expect([f.oldPath, f.newPath]).toEqual(["x b/y.txt", "x b/y.txt"]);
  });

  test("mnemonic i/ and w/ prefixes are stripped", () => {
    const diff =
      "diff --git i/x b/y.txt w/x b/y.txt\n" +
      "index bca70f3..8a08eba 100644\n" +
      "--- i/x b/y.txt\t\n" +
      "+++ w/x b/y.txt\t\n" +
      "@@ -1 +1,2 @@\n" +
      " q\n" +
      "+r\n";
    const f = only(diff);
    expect([f.oldPath, f.newPath]).toEqual(["x b/y.txt", "x b/y.txt"]);
  });

  test("an untracked file patch from --no-index against /dev/null is an added file", () => {
    const diff =
      "diff --git a/untracked.txt b/untracked.txt\n" +
      "new file mode 100644\n" +
      "index 0000000..94954ab\n" +
      "--- /dev/null\n" +
      "+++ b/untracked.txt\n" +
      "@@ -0,0 +1,2 @@\n" +
      "+hello\n" +
      "+world\n";
    const f = only(diff);
    expect(f).toMatchObject({ oldPath: "untracked.txt", newPath: "untracked.txt", status: "added", newMode: "100644" });
    expect(rows(hunk(f))).toEqual([["add", null, 1], ["add", null, 2]]);
    expect(texts(hunk(f))).toEqual(["hello", "world"]);
  });

  test("a new binary file is added and binary", () => {
    const diff =
      "diff --git a/img.bin b/img.bin\n" +
      "new file mode 100644\n" +
      "index 0000000..8352675\n" +
      "Binary files /dev/null and b/img.bin differ\n";
    const f = only(diff);
    expect(f).toMatchObject({ newPath: "img.bin", status: "added", binary: true, hunks: [], notes: [] });
  });

  test("a copy with changes keeps both names, the similarity and the hunk", () => {
    const diff =
      "diff --git a/del.txt b/del-copy.txt\n" +
      "similarity index 77%\n" +
      "copy from del.txt\n" +
      "copy to del-copy.txt\n" +
      "index f00c965..fa53661 100644\n" +
      "--- a/del.txt\n" +
      "+++ b/del-copy.txt\n" +
      "@@ -8,3 +8,4 @@\n" +
      " 8\n" +
      " 9\n" +
      " 10\n" +
      "+extra\n";
    const f = only(diff);
    expect(f).toMatchObject({ status: "copied", oldPath: "del.txt", newPath: "del-copy.txt", similarity: 77 });
    expect(rows(hunk(f))).toEqual([["ctx", 8, 8], ["ctx", 9, 9], ["ctx", 10, 10], ["add", null, 11]]);
  });

  test("a remerge CONFLICT header line goes to notes and the hunk still parses", () => {
    // Written by hand in the format of `git show --remerge-diff` (git 2.36+).
    const diff =
      "diff --git a/a.txt b/a.txt\n" +
      "remerge CONFLICT (content): Merge conflict in a.txt\n" +
      "index 6d9b2a1..9f9ee6e 100644\n" +
      "--- a/a.txt\n" +
      "+++ b/a.txt\n" +
      "@@ -1,7 +1,3 @@\n" +
      " top\n" +
      "-<<<<<<< 1a2b3c4 (left)\n" +
      "-left\n" +
      "-=======\n" +
      "-right\n" +
      "->>>>>>> 5d6e7f8 (right)\n" +
      "+merged\n" +
      " bottom\n";
    const f = only(diff);
    expect(f.notes).toEqual(["remerge CONFLICT (content): Merge conflict in a.txt"]);
    expect(f).toMatchObject({ oldPath: "a.txt", newPath: "a.txt", status: "modified" });
    expect(rows(hunk(f))).toEqual([
      ["ctx", 1, 1],
      ["del", 2, null], ["del", 3, null], ["del", 4, null], ["del", 5, null], ["del", 6, null],
      ["add", null, 2],
      ["ctx", 7, 3],
    ]);
    expect(hunk(f).lines[1]!.text).toBe("<<<<<<< 1a2b3c4 (left)");
  });

  test("a pure deletion hunk with +N,0 has no new lines", () => {
    // From `git diff -U0`.
    const diff =
      "diff --git a/del.txt b/del.txt\n" +
      "index f00c965..c3a2d1c 100644\n" +
      "--- a/del.txt\n" +
      "+++ b/del.txt\n" +
      "@@ -4,3 +3,0 @@\n" +
      "-4\n" +
      "-5\n" +
      "-6\n";
    const f = only(diff);
    expect(hunk(f)).toMatchObject({ oldStart: 4, oldCount: 3, newStart: 3, newCount: 0 });
    expect(rows(hunk(f))).toEqual([["del", 4, null], ["del", 5, null], ["del", 6, null]]);
    expect(texts(hunk(f))).toEqual(["4", "5", "6"]);
  });

  test("no newline at end of file on both sides marks the last line of each side", () => {
    const diff =
      "diff --git a/eof.txt b/eof.txt\n" +
      "index efb7da3..08d1404 100644\n" +
      "--- a/eof.txt\n" +
      "+++ b/eof.txt\n" +
      "@@ -1,2 +1,2 @@\n" +
      "-old\n" +
      "-last\n" +
      "\\ No newline at end of file\n" +
      "+new\n" +
      "+last2\n" +
      "\\ No newline at end of file\n";
    const f = only(diff);
    expect(hunk(f).lines).toEqual([
      { kind: "del", oldNo: 1, newNo: null, text: "old", noNewline: false },
      { kind: "del", oldNo: 2, newNo: null, text: "last", noNewline: true },
      { kind: "add", oldNo: null, newNo: 1, text: "new", noNewline: false },
      { kind: "add", oldNo: null, newNo: 2, text: "last2", noNewline: true },
    ]);
    expect(f.notes).toEqual([]);
  });

  test("CRLF content lines in an LF diff lose their CR", () => {
    const diff =
      "diff --git a/crlf.txt b/crlf.txt\n" +
      "index 4e349b5..24fe5dc 100644\n" +
      "--- a/crlf.txt\n" +
      "+++ b/crlf.txt\n" +
      "@@ -1,2 +1,2 @@\n" +
      " one\r\n" +
      "-two\r\n" +
      "+TWO\r\n";
    const f = only(diff);
    expect(rows(hunk(f))).toEqual([["ctx", 1, 1], ["del", 2, null], ["add", null, 2]]);
    expect(texts(hunk(f))).toEqual(["one", "two", "TWO"]);
  });

  test("an empty input gives no files", () => {
    expect(parseDiff("")).toEqual([]);
  });
});

describe("unquote", () => {
  test("a name without quotes is returned as is", () => {
    expect(unquote("plain.txt")).toBe("plain.txt");
    expect(unquote('a"b')).toBe('a"b');
  });

  test("octal escapes are UTF-8 bytes", () => {
    expect(unquote('"caf\\303\\251.txt"')).toBe("café.txt");
    expect(unquote('"\\360\\237\\230\\200"')).toBe("😀");
  });

  test("named escapes become their characters", () => {
    expect(unquote('"a\\tb"')).toBe("a\tb");
    expect(unquote('"a\\nb"')).toBe("a\nb");
    expect(unquote('"say \\"hi\\""')).toBe('say "hi"');
    expect(unquote('"back\\\\slash"')).toBe("back\\slash");
  });
});

describe("gitHeaderPaths", () => {
  test("plain names lose their a/ and b/ prefixes", () => {
    expect(gitHeaderPaths("a/old.txt b/new.txt")).toEqual(["old.txt", "new.txt"]);
  });

  test("the same name with spaces on both sides is split in the middle", () => {
    expect(gitHeaderPaths("a/with space.txt b/with space.txt")).toEqual(["with space.txt", "with space.txt"]);
  });

  test('a name that holds " b/" is split in the middle, not at the first " b/"', () => {
    expect(gitHeaderPaths("a/x b/y.txt b/x b/y.txt")).toEqual(["x b/y.txt", "x b/y.txt"]);
  });

  test("two quoted names are both unquoted", () => {
    expect(gitHeaderPaths('"a/caf\\303\\251.txt" "b/na\\303\\257ve.txt"')).toEqual(["café.txt", "naïve.txt"]);
  });

  test("an escaped quote does not end a quoted name", () => {
    expect(gitHeaderPaths('"a/say \\"hi\\".sh" "b/say \\"hi\\".sh"')).toEqual(['say "hi".sh', 'say "hi".sh']);
  });

  test("a plain name and a quoted name can be mixed either way", () => {
    expect(gitHeaderPaths('a/run.sh "b/say \\"hi\\".sh"')).toEqual(["run.sh", 'say "hi".sh']);
    expect(gitHeaderPaths('"a/caf\\303\\251.txt" b/plain.txt')).toEqual(["café.txt", "plain.txt"]);
  });

  test("a quoted escaped tab becomes a real tab", () => {
    expect(gitHeaderPaths('"a/bla with \\ttab.scala" "b/bla with \\ttab.scala"')).toEqual([
      "bla with \ttab.scala", "bla with \ttab.scala",
    ]);
  });
});
