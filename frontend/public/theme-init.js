// Applies the saved theme before first paint (external file: CSP forbids inline scripts).
(function () {
  try {
    var ui = JSON.parse(localStorage.getItem("jarvis.ui") || "{}");
    var theme = ui.theme || "dark";
    if (theme === "system") {
      theme = window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    }
    document.documentElement.dataset.theme = theme;
    if (ui.accent) document.documentElement.style.setProperty("--accent", ui.accent);
  } catch (e) {}
})();
