// The diff2html options the diff page renders with.
//
// Kept in its own module so tests can assert against exactly what ships rather
// than against a copy that can drift.

import type { Diff2HtmlUIConfig } from "diff2html/lib/ui/js/diff2html-ui-base";

export function diffwebRenderOptions(
  { sideBySide = false, drawFileList = true }: { sideBySide?: boolean; drawFileList?: boolean } = {},
): Diff2HtmlUIConfig {
  return {
    drawFileList,
    // "lines" and "words" pair each deletion with an insertion, which renders a
    // hunk as -,+,-,+ even though git emitted -,-,+,+. Two related removals then
    // their two replacements read far better than four alternating rows, and
    // "none" costs nothing: the inline word-level highlighting survives it.
    matching: "none",
    outputFormat: sideBySide ? "side-by-side" : "line-by-line",
    fileListToggle: true,
    fileContentToggle: false,
    stickyFileHeaders: true,
  };
}
