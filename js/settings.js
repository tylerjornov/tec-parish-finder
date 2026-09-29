// Display settings: theme, contrast, and motion. Each is "auto" (follow the
// device) or a manual override. Loaded in <head> so the resolved values are on
// <html> before the page paints; the menu itself is built once the DOM exists.
(() => {
  const KEY = "tec-display-settings";
  const OPTIONS = {
    theme: {
      legend: "Theme",
      query: "(prefers-color-scheme: light)",
      choices: [["auto", "Automatic"], ["light", "Light"], ["dark", "Dark"]],
      // [value when the media query matches, value when it doesn't]
      resolve: ["light", "dark"],
    },
    contrast: {
      legend: "High contrast",
      query: "(prefers-contrast: more)",
      choices: [["auto", "Automatic"], ["more", "On"], ["normal", "Off"]],
      resolve: ["more", "normal"],
    },
    motion: {
      legend: "Reduce motion",
      query: "(prefers-reduced-motion: reduce)",
      choices: [["auto", "Automatic"], ["reduce", "On"], ["full", "Off"]],
      resolve: ["reduce", "full"],
    },
  };

  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(KEY)) || {};
  } catch {
    // Storage blocked or corrupt: fall back to automatic.
  }

  function chosen(name) {
    const v = saved[name];
    return OPTIONS[name].choices.some(([value]) => value === v) ? v : "auto";
  }

  function apply() {
    for (const [name, opt] of Object.entries(OPTIONS)) {
      const v = chosen(name);
      document.documentElement.dataset[name] =
        v === "auto" ? opt.resolve[matchMedia(opt.query).matches ? 0 : 1] : v;
    }
  }

  apply();
  for (const opt of Object.values(OPTIONS)) {
    matchMedia(opt.query).addEventListener("change", apply);
  }

  function buildMenu() {
    const header = document.querySelector("header");
    if (!header) return;
    const wrap = document.createElement("div");
    wrap.className = "settings";
    wrap.innerHTML = `
      <button type="button" id="settings-btn" aria-expanded="false" aria-controls="settings-panel"
        aria-label="Display settings" title="Display settings">
        <svg aria-hidden="true" focusable="false" width="18" height="18" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="3"></circle>
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
        </svg>
      </button>
      <div id="settings-panel" class="settings-panel" role="group" aria-label="Display settings" hidden>
        ${Object.entries(OPTIONS)
          .map(
            ([name, opt]) => `
        <fieldset>
          <legend>${opt.legend}</legend>
          ${opt.choices
            .map(
              ([value, label]) =>
                `<label><input type="radio" name="${name}" value="${value}"${
                  chosen(name) === value ? " checked" : ""
                }> ${label}</label>`
            )
            .join("")}
        </fieldset>`
          )
          .join("")}
        <p class="settings-hint">Automatic follows your device settings.</p>
      </div>`;
    header.appendChild(wrap);

    const btn = wrap.querySelector("#settings-btn");
    const panel = wrap.querySelector("#settings-panel");
    const setOpen = (open) => {
      panel.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
    };

    btn.addEventListener("click", () => {
      setOpen(panel.hidden);
      if (!panel.hidden) panel.querySelector("input:checked").focus();
    });
    panel.addEventListener("change", (e) => {
      saved[e.target.name] = e.target.value;
      try {
        localStorage.setItem(KEY, JSON.stringify(saved));
      } catch {
        // Not persisted; the choice still applies for this page view.
      }
      apply();
    });
    wrap.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !panel.hidden) {
        setOpen(false);
        btn.focus();
      }
    });
    document.addEventListener("click", (e) => {
      if (!panel.hidden && !wrap.contains(e.target)) setOpen(false);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildMenu);
  } else {
    buildMenu();
  }
})();
