// Phone-width menu: the site links (Directory / About) collapse behind a
// waffle button. Loaded in <head> so the "nav-js" class is set before paint;
// without JavaScript the links simply stay visible.
document.documentElement.classList.add("nav-js");
document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("menu-btn");
  const nav = document.getElementById("site-nav");
  if (!btn || !nav) return;

  const setOpen = (open) => {
    nav.classList.toggle("open", open);
    btn.setAttribute("aria-expanded", String(open));
  };
  btn.addEventListener("click", () => setOpen(!nav.classList.contains("open")));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && nav.classList.contains("open")) {
      setOpen(false);
      btn.focus();
    }
  });
  document.addEventListener("click", (e) => {
    if (!nav.contains(e.target) && !btn.contains(e.target)) setOpen(false);
  });
});

// "Daily Office Readings" becomes "Sunday Service Liturgy" on Sundays,
// by the viewer's local clock.
document.addEventListener("DOMContentLoaded", () => {
  const link = document.getElementById("office-link");
  if (!link) return;
  const update = () => {
    link.textContent = new Date().getDay() === 0 ? "Sunday Service Liturgy" : "Daily Office Readings";
  };
  update();
  setInterval(update, 60000);
});
