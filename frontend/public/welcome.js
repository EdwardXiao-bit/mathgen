/* mathgen 老师端落地页的交互。
   实时卷面用的二十道题，是页面里那张截图同一套卷子的真实题目。 */

(function () {
  "use strict";

  var ITEMS = [
    "4 + 12 =", "10 − 6 =", "3 + 12 =", "18 − 12 =",
    "7 + 6 =", "15 − 13 =", "11 + 3 =", "17 − 6 =",
    "17 + 2 =", "4 − 4 =", "19 + 1 =", "16 − 16 =",
    "3 + 2 =", "18 − 14 =", "16 + 2 =", "11 − 7 =",
    "14 + 1 =", "20 − 9 =", "12 + 8 =", "6 − 1 ="
  ];

  /* 手机上默认两栏、字号小一档，否则四栏 14pt 在 390px 宽度里会挤成一团 */
  var narrow = window.matchMedia("(max-width: 700px)").matches;
  var state = {
    count: 20,
    cols: narrow ? 2 : 4,
    font: narrow ? 12 : 14,
    margin: narrow ? 16 : 22
  };
  var sheet = document.querySelector(".live-sheet");
  var list = document.querySelector(".live-items");

  function render() {
    if (!sheet || !list) return;
    sheet.style.setProperty("--sheet-cols", state.cols);
    sheet.style.setProperty("--sheet-fs", state.font + "px");
    sheet.style.setProperty("--sheet-pad", Math.round(state.margin * 1.35) + "px");

    var html = "";
    for (var i = 0; i < state.count; i++) {
      html += '<li><span class="seq">' + (i + 1) + ".</span> " + ITEMS[i % ITEMS.length] + "</li>";
    }
    list.innerHTML = html;
  }

  function pressGroup(selector, key, read) {
    var buttons = Array.prototype.slice.call(document.querySelectorAll(selector));
    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        buttons.forEach(function (other) {
          other.setAttribute("aria-pressed", String(other === button));
        });
        state[key] = read(button);
        render();
      });
    });
  }

  function syncControls() {
    document.querySelectorAll("[data-cols]").forEach(function (button) {
      button.setAttribute("aria-pressed", String(Number(button.dataset.cols) === state.cols));
    });
    document.querySelectorAll("[data-count]").forEach(function (button) {
      button.setAttribute("aria-pressed", String(Number(button.dataset.count) === state.count));
    });
    var font = document.getElementById("font-range");
    var fontOut = document.getElementById("font-out");
    if (font) { font.value = String(state.font); if (fontOut) fontOut.textContent = state.font + " pt"; }
    var margin = document.getElementById("margin-range");
    var marginOut = document.getElementById("margin-out");
    if (margin) { margin.value = String(state.margin); if (marginOut) marginOut.textContent = (state.margin / 10).toFixed(1) + " cm"; }
  }

  function initSheet() {
    if (!sheet) return;
    syncControls();
    pressGroup("[data-count]", "count", function (b) { return Number(b.dataset.count); });
    pressGroup("[data-cols]", "cols", function (b) { return Number(b.dataset.cols); });

    var font = document.getElementById("font-range");
    var fontOut = document.getElementById("font-out");
    if (font) {
      font.addEventListener("input", function () {
        state.font = Number(font.value);
        if (fontOut) fontOut.textContent = state.font + " pt";
        render();
      });
    }

    var margin = document.getElementById("margin-range");
    var marginOut = document.getElementById("margin-out");
    if (margin) {
      margin.addEventListener("input", function () {
        state.margin = Number(margin.value) / 10;
        if (marginOut) marginOut.textContent = state.margin.toFixed(1) + " cm";
        render();
      });
    }

    render();
  }

  /* 三个样子 */
  function initShowcase() {
    var tabs = Array.prototype.slice.call(document.querySelectorAll(".showcase-names button"));
    var images = Array.prototype.slice.call(document.querySelectorAll(".showcase-img"));
    var notes = Array.prototype.slice.call(document.querySelectorAll(".showcase-note"));
    if (!tabs.length) return;

    function pick(view) {
      tabs.forEach(function (tab) {
        tab.setAttribute("aria-selected", String(tab.dataset.view === view));
      });
      images.forEach(function (img) {
        var on = img.dataset.view === view;
        img.hidden = !on;
        img.classList.toggle("is-on", on);
      });
      notes.forEach(function (note) {
        note.hidden = note.dataset.note !== view;
      });
    }

    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () { pick(tab.dataset.view); });
    });

    var current = images.find(function (img) { return img.dataset.view === "paper"; });
    if (current) current.classList.add("is-on");
  }

  /* 入场：标题、句子、图，各按 60–90ms 错开 */
  function initReveal() {
    var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var targets = document.querySelectorAll(
      ".intro-copy > *, .intro-media, .make-title, .make-stage, .make-block, " +
      ".showcase-list > *, .showcase-stage, .step, .install-title, .install-say, " +
      ".install-row, .install-foot, .faq-grid > *, .close-say, .close-media, .foot-line"
    );

    if (reduce || !("IntersectionObserver" in window)) {
      targets.forEach(function (el) { el.classList.add("is-in"); });
      return;
    }

    var groups = new Map();
    targets.forEach(function (el) {
      el.classList.add("reveal");
      var parent = el.parentElement;
      if (!groups.has(parent)) groups.set(parent, 0);
      var index = groups.get(parent);
      groups.set(parent, index + 1);
      el.style.transitionDelay = Math.min(index, 4) * 70 + "ms";
    });

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-in");
        observer.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -12% 0px", threshold: 0.08 });

    targets.forEach(function (el) { observer.observe(el); });

    /* 首屏两行标题：先进入，再让句子和按钮跟上 */
    var lines = document.querySelectorAll(".hero-line > span");
    lines.forEach(function (line, i) {
      setTimeout(function () { line.classList.add("is-in"); }, 120 + i * 90);
    });
    document.querySelectorAll(".hero-say, .hero-media, .hero-left .action, .hero-left .figure")
      .forEach(function (el) {
        el.classList.add("reveal");
        setTimeout(function () { el.classList.add("is-in"); }, 420);
      });
  }

  /* 首屏那张卷面随滚动轻微上移（只动 transform） */
  function initParallax() {
    var media = document.querySelector(".hero-media img");
    if (!media) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    var ticking = false;
    function update() {
      var y = window.scrollY;
      if (y < 1200) {
        media.style.transform = "scale(1.02) translateY(" + Math.min(y * 0.06, 26) + "px)";
      }
      ticking = false;
    }

    window.addEventListener("scroll", function () {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(update);
    }, { passive: true });
  }

  function init() {
    initSheet();
    initShowcase();
    initReveal();
    initParallax();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
