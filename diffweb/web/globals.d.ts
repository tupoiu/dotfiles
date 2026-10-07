// The diff page loads diff2html's UI bundle from static/vendor/ with a
// <script> tag, which puts the class on window. The npm package is a dev
// dependency only for these types, pinned to the version fetch-assets.sh vendors.

import type { Diff2HtmlUI as Diff2HtmlUIClass } from "diff2html/lib/ui/js/diff2html-ui";

declare global {
  const Diff2HtmlUI: typeof Diff2HtmlUIClass;
}
