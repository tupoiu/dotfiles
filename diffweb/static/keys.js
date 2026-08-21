// Keyboard navigation for the diff page.
//
// Reviewing a branch is a linear pass over files, so the whole flow should be
// reachable without the mouse. Exposed as window.diffwebKeys so worktree.js can
// tell it when the file list has been rebuilt.

(function () {
  const KEYS = [
    ["j", "next file"],
    ["k", "previous file"],
    ["o / Enter", "collapse or expand the focused file"],
    ["v", "mark the focused file reviewed"],
    ["c", "collapse or expand all"],
    ["s", "toggle side-by-side"],
    ["g / G", "first / last file"],
    ["?", "this help"],
  ];

  let files = [];
  let cursor = -1;

  function refresh() {
    // "hide reviewed" takes files out of the page; navigation must not land on
    // one, so only what is actually on screen counts.
    files = [...document.querySelectorAll("#diff .d2h-file-wrapper")].filter(
      (f) => f.offsetParent !== null,
    );
    if (cursor >= files.length) cursor = files.length - 1;
    paint();
  }

  function paint() {
    // Clear across every wrapper, not just the navigable ones: a file that the
    // reviewed filter has just hidden is no longer in `files` and would keep
    // the class forever.
    for (const f of document.querySelectorAll("#diff .d2h-file-wrapper.focused")) {
      f.classList.remove("focused");
    }
    if (cursor >= 0 && files[cursor]) files[cursor].classList.add("focused");
  }

  function focus(index) {
    if (!files.length) return;
    cursor = Math.max(0, Math.min(index, files.length - 1));
    paint();
    const header = files[cursor].querySelector(".d2h-file-header");
    // Sticky toolbar sits on top, so scroll the header clear of it.
    const top = header.getBoundingClientRect().top + window.scrollY - toolbarHeight() - 8;
    window.scrollTo({ top, behavior: "smooth" });
  }

  function toolbarHeight() {
    const bar = document.querySelector(".topbar");
    return bar ? bar.getBoundingClientRect().height : 0;
  }

  // Without an explicit cursor, "next" should mean the first file actually
  // on screen rather than jumping back to the top of a long diff.
  function nearestVisible() {
    const limit = toolbarHeight() + 8;
    const i = files.findIndex((f) => f.getBoundingClientRect().bottom > limit);
    return i === -1 ? files.length - 1 : i;
  }

  function act(name) {
    if (!files.length) return;
    if (cursor === -1) cursor = nearestVisible();
    const file = files[cursor];
    if (name === "toggle") file.querySelector(".d2h-file-header").click();
    if (name === "review") {
      file.querySelector(".review-tick input")?.click();
      // The file may have just been filtered away underneath the cursor.
      const wasAt = cursor;
      refresh();
      if (!files.includes(file)) cursor = Math.min(wasAt, files.length - 1);
    }
    paint();
  }

  function helpOverlay() {
    let box = document.getElementById("key-help");
    if (box) {
      box.remove();
      return;
    }
    box = document.createElement("div");
    box.id = "key-help";
    box.innerHTML =
      "<h2>Keyboard</h2><dl>" +
      KEYS.map(([k, d]) => `<dt><kbd>${k}</kbd></dt><dd>${d}</dd>`).join("") +
      "</dl><p class='dim'>Any key to dismiss.</p>";
    document.body.appendChild(box);
  }

  document.addEventListener("keydown", (e) => {
    // Never steal keys from the base-ref box or the commit selects.
    const t = e.target;
    if (t.matches("input, select, textarea") || e.metaKey || e.ctrlKey || e.altKey) return;

    const help = document.getElementById("key-help");
    if (help) {
      help.remove();
      if (e.key !== "?") return;
    }

    switch (e.key) {
      case "j": focus(cursor + 1); break;
      case "k": focus(cursor === -1 ? nearestVisible() : cursor - 1); break;
      case "g": focus(0); break;
      case "G": focus(files.length - 1); break;
      case "o":
      case "Enter": act("toggle"); break;
      case "v": act("review"); break;
      case "c": document.getElementById("toggle-all")?.click(); break;
      case "s": {
        const sbs = document.getElementById("sbs");
        sbs.checked = !sbs.checked;
        sbs.dispatchEvent(new Event("change"));
        break;
      }
      case "?": helpOverlay(); break;
      default: return;
    }
    e.preventDefault();
  });

  document.getElementById("key-help-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    helpOverlay();
  });

  window.diffwebKeys = { refresh };
})();
