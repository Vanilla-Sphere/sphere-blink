/* Vanilla Sphere - one-line notes on the controls.
   A control names its note with data-hint="key" (the text is in STR, in both languages) or carries
   the text itself in data-hint-text. The note shows when the pointer rests on the control or the
   keyboard reaches it, and goes on a press. Touch has no hover, so nothing shows there. */
"use strict";

(() => {
  const SEL = "[data-hint],[data-hint-text]";
  const box = document.createElement("div");
  box.id = "hintbox"; box.className = "hintbox"; box.setAttribute("role", "tooltip"); box.hidden = true;
  document.body.appendChild(box);
  let cur = null, shown = false, timer = 0, lastHide = -1e9, pressed = null, lastDown = -1e9;

  const text = (el) => el.dataset.hintText || (el.dataset.hint ? tr(el.dataset.hint) : "");
  function place(el) {
    const r = el.getBoundingClientRect(), w = box.offsetWidth, h = box.offsetHeight, gap = 8, edge = 8;
    const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
    // inside a column of controls (data-hint-side="right") the note goes beside the column, and with "top" above the control, so it never covers the next one
    const col = el.closest("[data-hint-side]");
    const above = col && col.dataset.hintSide === "top";
    if (col && !above) {
      const c = col.getBoundingClientRect(), right = col.dataset.hintSide === "right";
      const sx = right ? c.right + gap : c.left - gap - w;
      if (sx >= edge && sx + w <= vw - edge) {
        box.style.left = Math.round(sx) + "px";
        box.style.top = Math.round(Math.max(edge, Math.min(vh - h - edge, r.top + r.height / 2 - h / 2))) + "px";
        return;
      }
    }
    const x = Math.max(edge, Math.min(vw - w - edge, r.left + r.width / 2 - w / 2));
    const below = above ? r.top - gap - h < edge : r.bottom + gap + h <= vh - edge || r.top - gap - h < edge;
    box.style.left = Math.round(x) + "px";
    box.style.top = Math.round(below ? r.bottom + gap : r.top - gap - h) + "px";
  }
  function show(el) {
    const t = text(el);
    if (!t || !el.isConnected || !el.getClientRects().length) return;
    box.textContent = t; box.hidden = false; place(el); shown = true;
    if (!el.hasAttribute("aria-describedby")) { el.setAttribute("aria-describedby", "hintbox"); el.dataset.hintOn = "1"; }
  }
  function hide() {
    clearTimeout(timer);
    if (cur && cur.dataset.hintOn) { cur.removeAttribute("aria-describedby"); delete cur.dataset.hintOn; }
    if (shown) lastHide = performance.now();
    cur = null; shown = false; box.hidden = true;
  }
  /* The first note waits a moment; moving on to the next control shows its note almost at once. */
  function aim(el, quick) {
    if (el === cur) return;
    const warm = shown || performance.now() - lastHide < 500;
    hide();
    if (!el) return;
    cur = el;
    timer = setTimeout(() => show(el), quick || warm ? 80 : 400);
  }

  document.addEventListener("pointerover", (ev) => {
    if (ev.pointerType === "touch") return;
    const el = ev.target.closest ? ev.target.closest(SEL) : null;
    if (el && el === pressed) return;  // a control just pressed stays quiet until the pointer leaves it
    pressed = null; aim(el);
  });
  document.documentElement.addEventListener("pointerleave", hide);
  document.addEventListener("pointerdown", (ev) => { pressed = ev.target.closest ? ev.target.closest(SEL) : null; lastDown = performance.now(); hide(); }, true);
  document.addEventListener("keydown", hide, true);
  document.addEventListener("scroll", hide, true);
  window.addEventListener("blur", hide);
  window.addEventListener("resize", hide);
  document.addEventListener("focusin", (ev) => {
    const el = ev.target.closest ? ev.target.closest(SEL) : null;
    if (el && ev.target.matches(":focus-visible") && performance.now() - lastDown > 300) aim(el, true);  // keyboard focus only
  });
  document.addEventListener("focusout", hide);
})();
