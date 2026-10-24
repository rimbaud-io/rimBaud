(function () {
  var btn = document.getElementById("ham");
  var panel = document.getElementById("ham-panel");
  if (!btn || !panel) return;
  function setOpen(open) {
    panel.hidden = !open;
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  }
  btn.addEventListener("click", function (ev) {
    ev.stopPropagation();
    setOpen(panel.hidden);
  });
  document.addEventListener("click", function (ev) {
    if (panel.hidden) return;
    if (panel.contains(ev.target) || btn.contains(ev.target)) return;
    setOpen(false);
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") setOpen(false);
  });
})();
