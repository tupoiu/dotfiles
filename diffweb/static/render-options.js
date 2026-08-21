// The diff2html options the diff page renders with.
//
// Kept in its own file so tests can assert against exactly what ships rather
// than against a copy that can drift.

function diffwebRenderOptions({ sideBySide = false, drawFileList = true } = {}) {
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

// Loaded with a <script> tag by the page, and required directly by the tests.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { diffwebRenderOptions };
}
