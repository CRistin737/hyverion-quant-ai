// Copy-to-clipboard for the install block and a small EN/ES toggle.
(() => {
  "use strict";

  const status = document.querySelector(".copy-status");
  let lang = "en";

  document.querySelectorAll(".copy").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.target);
      if (!target) return;
      let ok = false;
      try {
        await navigator.clipboard.writeText(target.textContent);
        ok = true;
      } catch {
        const range = document.createRange();
        range.selectNodeContents(target);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      }
      if (status) {
        const es = lang === "es";
        status.textContent = ok
          ? (es ? "Comandos copiados." : "Commands copied.")
          : (es ? "Texto seleccionado; pulsa Cmd+C." : "Text selected; press Cmd+C.");
        window.setTimeout(() => { status.textContent = ""; }, 2500);
      }
    });
  });

  // Store the English originals once, then swap between them and data-es.
  const texts = [...document.querySelectorAll("[data-es]")].map((el) => [el, el.textContent]);
  const alts = [...document.querySelectorAll("[data-es-alt]")].map((el) => [el, el.alt]);
  const toggle = document.querySelector(".lang");

  const apply = (next) => {
    lang = next;
    const es = next === "es";
    document.documentElement.lang = next;
    texts.forEach(([el, en]) => { el.textContent = es ? el.dataset.es : en; });
    alts.forEach(([el, en]) => { el.alt = es ? el.dataset.esAlt : en; });
    if (toggle) {
      toggle.textContent = es ? "EN" : "ES";
      toggle.setAttribute("aria-label", es ? "View in English" : "Ver en español");
    }
    try { localStorage.setItem("hyverion-site-lang", next); } catch { /* storage unavailable */ }
  };

  if (toggle) toggle.addEventListener("click", () => apply(lang === "es" ? "en" : "es"));

  let saved = null;
  try { saved = localStorage.getItem("hyverion-site-lang"); } catch { /* storage unavailable */ }
  const preferred = saved || ((navigator.language || "").toLowerCase().startsWith("es") ? "es" : "en");
  if (preferred === "es") apply("es");
})();
