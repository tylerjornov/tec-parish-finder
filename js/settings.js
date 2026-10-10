// Display settings: theme, contrast, and motion. Each is "auto" (follow the
// device) or a manual override. Pin size scales the map markers; Diocesan boundaries toggles the boundary overlay. Loaded in <head> so the resolved values are on
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

  // The slider shows 50%–250%, where 100% is PIN_BASE times Leaflet's default 25×41
  // marker. data-pin-size on <html> holds that real multiple for app.js.
  const PIN = { min: 0.5, max: 2.5, step: 0.1 };
  const PIN_BASE = 0.6;

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

  function pinSize() {
    const v = Number(saved.pinScale);
    return v >= PIN.min && v <= PIN.max ? v : 1;
  }

  const pct = (v) => `${Math.round(v * 100)}%`;

  function apply() {
    document.documentElement.dataset.pinSize = String(+(pinSize() * PIN_BASE).toFixed(3));
    document.documentElement.dataset.boundaries = saved.boundaries === true ? "on" : "off";
    document.documentElement.dataset.dioceseNames = saved.dioceseNames === true ? "always" : "hover";
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
        <fieldset>
          <div class="settings-switch">
            <span id="boundaries-label">Diocesan boundaries</span>
            <label class="switch">
              <input type="checkbox" role="switch" id="boundaries" aria-labelledby="boundaries-label"${saved.boundaries === true ? " checked" : ""}>
              <span class="switch-track" aria-hidden="true"></span>
            </label>
          </div>
          <div class="settings-switch names-switch">
            <span id="diocese-names-label">Always show names</span>
            <label class="switch">
              <input type="checkbox" role="switch" id="dioceseNames" aria-labelledby="diocese-names-label"${saved.dioceseNames === true ? " checked" : ""}${saved.boundaries === true ? "" : " disabled"}>
              <span class="switch-track" aria-hidden="true"></span>
            </label>
          </div>
        </fieldset>
        <fieldset>
          <legend id="pin-size-legend">Pin size</legend>
          <div class="settings-range">
            <input type="range" id="pin-size" min="${PIN.min}" max="${PIN.max}" step="${PIN.step}"
              value="${pinSize()}" aria-labelledby="pin-size-legend" aria-valuetext="${pct(pinSize())}">
            <output for="pin-size" id="pin-size-out">${pct(pinSize())}</output>
          </div>
        </fieldset>
        <p class="settings-hint">Automatic follows your device settings.</p>
      </div>`;
    header.appendChild(wrap);
    // Phones show the page's copyright line here instead (see app.css).
    const copyright = document.querySelector(".copyright");
    if (copyright) wrap.querySelector("#settings-panel").append(copyright.cloneNode(true));

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
    const save = () => {
      try {
        localStorage.setItem(KEY, JSON.stringify(saved));
      } catch {
        // Not persisted; the choice still applies for this page view.
      }
      apply();
    };
    panel.addEventListener("change", (e) => {
      if (e.target.type === "checkbox") {
        saved[e.target.id] = e.target.checked;
        // Names only matter while the boundaries are showing.
        panel.querySelector("#dioceseNames").disabled = saved.boundaries !== true;
        save();
        return;
      }
      if (e.target.type !== "radio") return;
      saved[e.target.name] = e.target.value;
      save();
    });
    // "input" fires while dragging, so the pins resize live.
    const range = panel.querySelector("#pin-size");
    range.addEventListener("input", () => {
      saved.pinScale = Number(range.value);
      range.setAttribute("aria-valuetext", pct(saved.pinScale));
      panel.querySelector("#pin-size-out").textContent = pct(saved.pinScale);
      save();
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
