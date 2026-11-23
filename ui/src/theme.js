/* Light / dark theme: localStorage wins, otherwise prefers-color-scheme. */
(function (global) {
  "use strict";
  var KEY = "logbook.theme";

  function systemTheme() {
    try {
      return (global.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light";
    } catch (e) { return "light"; }
  }
  function stored() {
    try { return global.localStorage && localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function save(theme) {
    try { localStorage.setItem(KEY, theme); } catch (e) {}
  }
  function resolved() {
    var t = stored();
    if (t === "light" || t === "dark") return t;
    return systemTheme();
  }
  function apply(theme) {
    var t = (theme === "dark" || theme === "light") ? theme : resolved();
    document.documentElement.setAttribute("data-theme", t);
    var btn = document.getElementById("theme-toggle");
    if (btn) btn.setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme");
    return t;
  }
  function toggle() {
    var next = (resolved() === "dark") ? "light" : "dark";
    save(next);
    apply(next);
    // redraw charts if visible
    if (global.Logbook && global.Logbook.render) {
      try { global.Logbook.render(); } catch (e) {}
    }
    return next;
  }
  function bind() {
    apply(resolved());
    var btn = document.getElementById("theme-toggle");
    if (btn && !btn._bound) {
      btn._bound = true;
      btn.addEventListener("click", function () { toggle(); });
    }
    try {
      if (global.matchMedia) {
        matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
          if (stored() !== "light" && stored() !== "dark") apply(systemTheme());
        });
      }
    } catch (e) {}
  }
  global.LogbookTheme = { apply: apply, toggle: toggle, resolved: resolved, key: KEY };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})(typeof window !== "undefined" ? window : globalThis);
