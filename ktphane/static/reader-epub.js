/* Kitaplık EPUB okuyucusu (epub.js) */
(() => {
  "use strict";
  const R = window.Reader, INFO = R.INFO, S = R.S, $ = (s) => document.querySelector(s);
  let book, rendition, curLoc = null, toc = [], hlMap = new Map(), selCtx = null, firstShown = false;
  const ORIGIN = location.origin;

  // ---------- kitabın içine uygulanan stil ----------
  function bookCss() {
    const c = R.themeColors();
    const fam = { Literata: '"Literata"', serif: 'Georgia, "Times New Roman"', sans: '-apple-system, "Segoe UI", Roboto, "Noto Sans"', Amiri: '"Amiri"', "Noto Naskh Arabic": '"Noto Naskh Arabic"' }[S.font];
    // Arapça harfler her zaman düzgün bir Nesih yazı tipiyle gösterilsin (Osmanlıca karışık metinler için)
    const family = fam ? `${fam}, "Amiri", "Noto Naskh Arabic", serif` : null;
    const dark = ["gray", "dark", "black"].includes(S.theme);
    return `
      html { background: ${c.bg} !important; -webkit-text-size-adjust: 100% !important; }
      body { color: ${c.fg} !important; background: transparent !important; font-size: ${S.size}% !important; line-height: ${S.line} !important; }
      body p, body li, body blockquote, body dd, body dt, body td, body th, body div { font-size: 1em !important; }
      body p, body li, body blockquote, body dd, body dt, body td, body th { line-height: ${S.line} !important; text-align: ${S.align} !important; }
      ${family ? `body, body p, body div, body span, body li, body td, body blockquote, body a, body em, body i, body b, body strong, body h1, body h2, body h3, body h4, body h5, body h6 { font-family: ${family} !important; }` : `body { font-family: inherit; } :lang(ar), :lang(fa), :lang(ota), [dir=rtl] { font-family: "Amiri", "Noto Naskh Arabic", serif; }`}
      body p, body div, body li, body h1, body h2, body h3, body h4, body h5, body h6, body blockquote, body td { unicode-bidi: plaintext; }
      body *:not(img):not(svg):not(image) { color: inherit !important; ${dark ? "background-color: transparent !important; border-color: " + c.fg + "33 !important;" : ""} }
      a, a * { color: ${dark ? "#7dc8b5" : "#2f6f62"} !important; }
      ${dark ? "img { opacity: .9; }" : ""}
      ${S.dir === "rtl" ? "body { letter-spacing: normal !important; word-spacing: normal !important; }" : ""}
      ::selection { background: rgba(80, 140, 255, .35); }
      img, svg { max-width: 100% !important; height: auto; }
    `;
  }
  function injectStyle(contents) {
    const doc = contents.document;
    if (!doc.getElementById("kt-fonts")) {
      const l = doc.createElement("link"); l.id = "kt-fonts"; l.rel = "stylesheet"; l.href = ORIGIN + "/static/fonts/fonts.css"; doc.head.appendChild(l);
    }
    let st = doc.getElementById("kt-style");
    if (!st) { st = doc.createElement("style"); st.id = "kt-style"; doc.head.appendChild(st); }
    st.textContent = bookCss();
  }
  function restyleAll() { rendition && rendition.getContents().forEach(injectStyle); }

  function layoutViewer() {
    const v = $("#viewer"), wide = innerWidth >= 1000 && S.spread !== "none" && S.flow === "paginated";
    const pad = [Math.min(12, innerWidth * 0.03), Math.min(48, innerWidth * 0.05), Math.min(120, innerWidth * 0.11)][S.margin] || 24;
    v.style.maxWidth = wide ? "1500px" : (S.flow === "scrolled" ? "860px" : "900px");
    v.style.padding = `0 ${pad}px`;
  }

  // ---------- konum ----------
  function chapterOf(href) {
    if (!href) return "";
    const base = href.split("#")[0];
    let best = null;
    for (const t of toc) { const h = t.href.split("#")[0]; if (h === base || h.endsWith("/" + base) || base.endsWith("/" + h)) { best = t; break; } }
    if (!best) {  // bölüm TOC'da yoksa, omurgada kendinden önceki son TOC girişini al
      const idx = book.spine.get(href)?.index ?? -1;
      for (const t of toc) { const i = book.spine.get(t.href)?.index ?? -1; if (i <= idx && i >= 0) best = t; }
    }
    return best ? best.label : "";
  }
  function percentOf(cfi, href) {
    if (book.locations.length()) return book.locations.percentageFromCfi(cfi);
    const sec = book.spine.get(href); return sec ? sec.index / Math.max(1, book.spine.length) : 0;
  }
  function onRelocated(loc) {
    curLoc = loc;
    const cfi = loc.start.cfi, href = loc.start.href, pct = percentOf(cfi, href), chap = chapterOf(href);
    $("#chap").textContent = chap; $("#miniTop").textContent = chap;
    $("#slider").value = Math.round(pct * 1000);
    $("#miniBot").textContent = Math.round(pct * 100) + "%";
    R.savePos({ cfi, href, chapter: chap.slice(0, 200), percent: pct });
    R.updateBookmarkBtn();
    if (firstShown && !document.body.classList.contains("immersive") && !$("#drawer").classList.contains("open") && $("#settings").hidden) R.setImmersive(true);
  }

  // ---------- vurgular ----------
  const hlStyle = (color) => ({ fill: R.COLORS[color] || R.COLORS.yellow, "fill-opacity": "0.42", "mix-blend-mode": ["gray", "dark", "black"].includes(S.theme) ? "normal" : "multiply" });
  function addHl(n) {
    if (!n.cfi || !n.text || n.type === "bookmark" || hlMap.has(n.id)) return;
    try {
      rendition.annotations.highlight(n.cfi, { id: n.id }, (e) => showSelbar({ existing: n }), "kt-hl", hlStyle(n.color));
      hlMap.set(n.id, n.cfi);
    } catch {}
  }
  function refreshNotes(notes) {
    if (!rendition) return;
    const ids = new Set(notes.map((n) => n.id));
    for (const [id, cfi] of hlMap) if (!ids.has(id)) { try { rendition.annotations.remove(cfi, "highlight"); } catch {} hlMap.delete(id); }
    notes.forEach(addHl);
    R.updateBookmarkBtn();
  }
  function removeNote(n) { if (n && hlMap.has(n.id)) { try { rendition.annotations.remove(hlMap.get(n.id), "highlight"); } catch {} hlMap.delete(n.id); } }
  function redrawHl() { for (const [, cfi] of hlMap) { try { rendition.annotations.remove(cfi, "highlight"); } catch {} } hlMap.clear(); R.notes.forEach(addHl); }

  function showSelbar(ctx) {
    selCtx = ctx;
    $("#selbar").hidden = false;
    $("#selbar [data-act=remove]").hidden = !ctx.existing;
    $("#selbar [data-act=copy]").hidden = !!ctx.existing;
  }
  function clearSelection() {
    $("#selbar").hidden = true; selCtx = null;
    rendition && rendition.getContents().forEach((c) => { try { c.window.getSelection().removeAllRanges(); } catch {} });
  }
  $("#selbar").addEventListener("click", async (e) => {
    const b = e.target.closest("button"); if (!b || !selCtx) return;
    const ctx = selCtx;
    if (b.dataset.color) {
      if (ctx.existing) { await R.saveNote({ ...ctx.existing, color: b.dataset.color }); removeNote(ctx.existing); refreshNotes(R.notes); }
      else await R.saveNote({ type: "highlight", cfi: ctx.cfi, text: ctx.text, color: b.dataset.color, chapter: ctx.chapter, percent: ctx.percent });
    } else if (b.dataset.act === "note") {
      const base = ctx.existing || {};
      const t = await R.noteDialog({ quote: ctx.existing ? base.text : ctx.text, note: base.note || "" });
      if (t != null) {
        if (ctx.existing) await R.saveNote({ ...base, type: t ? "note" : "highlight", note: t });
        else await R.saveNote({ type: "note", cfi: ctx.cfi, text: ctx.text, note: t, color: "yellow", chapter: ctx.chapter, percent: ctx.percent });
      }
    } else if (b.dataset.act === "copy") {
      try { await navigator.clipboard.writeText(ctx.text); R.toast("Kopyalandı"); } catch { R.toast("Kopyalanamadı"); }
    } else if (b.dataset.act === "remove" && ctx.existing) {
      await R.deleteNote(ctx.existing.id); R.toast("Vurgu kaldırıldı");
    }
    clearSelection();
  });

  // ---------- dokunma / tıklama (kitap iframe'in içinde) ----------
  function frameX(e, clientX) { const fr = e.view && e.view.frameElement; return (fr ? fr.getBoundingClientRect().left : 0) + clientX; }
  let tStart = null, lastTouchEnd = 0;
  function bindContentEvents(r) {
    r.on("touchstart", (e) => { if (e.touches.length === 1) tStart = { x: e.touches[0].clientX, y: e.touches[0].clientY, t: Date.now(), e }; else tStart = null; });
    r.on("touchend", (e) => {
      if (!tStart) return;
      const t = e.changedTouches[0], dx = t.clientX - tStart.x, dy = t.clientY - tStart.y, dt = Date.now() - tStart.t;
      tStart = null; lastTouchEnd = Date.now();
      const sel = e.view && String(e.view.getSelection()).trim();
      if (sel) return;
      if (S.flow === "paginated" && Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy) * 1.4 && dt < 800) { R.tapSide(dx < 0 ? "right" : "left"); return; }
      if (Math.abs(dx) < 10 && Math.abs(dy) < 10 && dt < 400 && !e.target.closest("a")) {
        if (S.flow === "scrolled") { $("#selbar").hidden ? R.toggleUI() : clearSelection(); }
        else R.handleTap(frameX(e, t.clientX));
      }
    });
    r.on("click", (e) => {
      if (Date.now() - lastTouchEnd < 700) return;  // dokunmatikte touchend zaten işlendi
      if (e.target.closest && e.target.closest("a")) return;
      if (String(e.view.getSelection()).trim()) return;
      if (S.flow === "scrolled") { $("#selbar").hidden ? R.toggleUI() : clearSelection(); return; }
      R.handleTap(frameX(e, e.clientX));
    });
    r.on("keydown", (e) => document.dispatchEvent(new KeyboardEvent("keydown", { key: e.key, shiftKey: e.shiftKey, metaKey: e.metaKey, ctrlKey: e.ctrlKey })));
    r.on("selected", (cfiRange, contents) => {
      const text = String(contents.window.getSelection()).trim();
      if (!text) return;
      const pct = curLoc ? percentOf(curLoc.start.cfi, curLoc.start.href) : 0;
      showSelbar({ cfi: cfiRange, text: text.slice(0, 4000), chapter: chapterOf(curLoc?.start.href).slice(0, 200), percent: pct });
    });
    r.on("relocated", onRelocated);
    r.on("rendered", () => setTimeout(redrawHl, 250));
    r.hooks.content.register(injectStyle);
  }

  // ---------- oluşturma ----------
  function createRendition(target) {
    if (rendition) { try { rendition.destroy(); } catch {} hlMap.clear(); $("#viewer").innerHTML = ""; }
    layoutViewer();
    const scrolled = S.flow === "scrolled";
    rendition = book.renderTo("viewer", {
      width: "100%", height: "100%", flow: scrolled ? "scrolled" : "paginated",
      manager: scrolled ? "continuous" : "default", spread: S.spread === "none" ? "none" : "auto", minSpreadWidth: 1000,
      allowScriptedContent: false, defaultDirection: S.dir,
    });
    bindContentEvents(rendition);
    return rendition.display(target || undefined).then(() => {
      $("#msg").hidden = true; restyleAll(); setTimeout(redrawHl, 300);
      if (!firstShown) { firstShown = true; setTimeout(() => { if ($("#settings").hidden && !$("#drawer").classList.contains("open")) R.setImmersive(true); }, 2500); }
    });
  }

  function renderToc(box) {
    if (!toc.length) { box.innerHTML = '<div class="empty">Bu kitapta içindekiler yok.</div>'; return; }
    const cur = chapterOf(curLoc?.start.href);
    box.innerHTML = toc.map((t, i) => `<a href="#" data-i="${i}" class="lvl${Math.min(3, t.level)} ${t.label === cur ? "cur" : ""}" dir="auto">${R.esc(t.label)}</a>`).join("");
    box.onclick = (e) => { const a = e.target.closest("a[data-i]"); if (!a) return; e.preventDefault(); R.closeAll(); rendition.display(toc[+a.dataset.i].href); };
    box.querySelector(".cur")?.scrollIntoView({ block: "center" });
  }

  // ---------- arama (Osmanlıca/Arapça harekeler ve Türkçe harfler esnek eşleşir) ----------
  function norm(s) {
    let out = ""; const map = [];
    for (let i = 0; i < s.length; i++) {
      let c = s[i];
      if (/[ً-ٰٟـ­‌‍]/.test(c)) continue;
      c = c.replace(/[أإآٱ]/, "ا").replace(/[ىی]/, "ي").replace(/ک/, "ك").replace(/ۀ/, "ه");
      c = c.toLocaleLowerCase("tr").normalize("NFD").replace(/[̀-ͯ]/g, "");
      for (const ch of c) { out += ch; map.push(i); }
    }
    return { out, map };
  }
  let searchToken = 0;
  async function search(q) {
    const token = ++searchToken, box = $("#searchResults");
    const nq = norm(q.trim()).out; if (nq.length < 2) { box.innerHTML = '<div class="empty">En az 2 harf yazın.</div>'; return; }
    box.innerHTML = '<div class="empty" id="sProg">Aranıyor…</div>';
    const results = [];
    const items = book.spine.spineItems;
    for (let si = 0; si < items.length && results.length < 300; si++) {
      if (token !== searchToken) return;
      const item = items[si];
      try {
        await item.load(book.load.bind(book));
        const doc = item.document, walker = doc.createTreeWalker(doc.body || doc.documentElement, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode()) && results.length < 300) {
          const { out, map } = norm(node.nodeValue);
          let from = 0, idx;
          while ((idx = out.indexOf(nq, from)) >= 0) {
            const s0 = map[idx], s1 = map[idx + nq.length - 1] + 1;
            const range = doc.createRange(); range.setStart(node, s0); range.setEnd(node, s1);
            const v = node.nodeValue;
            results.push({ cfi: item.cfiFromRange(range), pre: v.slice(Math.max(0, s0 - 50), s0), hit: v.slice(s0, s1), post: v.slice(s1, s1 + 60), chap: chapterOf(item.href) });
            from = idx + nq.length;
          }
        }
      } catch {}
      item.unload();
      const p = $("#sProg"); if (p) p.textContent = `Aranıyor… %${Math.round(((si + 1) / items.length) * 100)} · ${results.length} sonuç`;
    }
    if (token !== searchToken) return;
    box.innerHTML = results.length
      ? `<div class="empty" style="padding:4px 0 10px">${results.length}${results.length >= 300 ? "+" : ""} sonuç</div>` + results.map((r, i) =>
        `<div class="list-item search-hit" data-r="${i}"><div class="meta">${R.esc(r.chap)}</div><div dir="auto">…${R.esc(r.pre)}<mark>${R.esc(r.hit)}</mark>${R.esc(r.post)}…</div></div>`).join("")
      : '<div class="empty">Sonuç bulunamadı.</div>';
    box.onclick = (e) => {
      const it = e.target.closest("[data-r]"); if (!it) return;
      const r = results[+it.dataset.r]; R.closeAll();
      rendition.display(r.cfi).then(() => {
        try { rendition.annotations.highlight(r.cfi, {}, null, "kt-search", { fill: "#ff9800", "fill-opacity": "0.45" }); setTimeout(() => rendition.annotations.remove(r.cfi, "highlight"), 4000); } catch {}
      });
    };
  }
  $("#searchForm").addEventListener("submit", (e) => { e.preventDefault(); search($("#searchInput").value); });

  // ---------- motor ----------
  const engine = {
    next: () => rendition && rendition.next(),
    prev: () => rendition && rendition.prev(),
    ownsTaps: true,  // dokunmalar kitabın iframe'inden gelir
    allowSwipe: () => false,
    clearSelection,
    renderToc,
    refreshNotes, removeNote,
    goToPos: (p) => rendition.display(p.cfi || p.href),
    goToNote: (n) => n.cfi && rendition.display(n.cfi),
    location: () => {
      const cfi = curLoc?.start.cfi, href = curLoc?.start.href;
      return { cfi, chapter: chapterOf(href).slice(0, 200), percent: cfi ? percentOf(cfi, href) : 0 };
    },
    currentBookmark: (notes) => {
      if (!curLoc) return null;
      const a = curLoc.start.cfi, b = curLoc.end.cfi, C = ePub.CFI ? new ePub.CFI() : null;
      return notes.find((n) => n.type === "bookmark" && n.cfi && (n.cfi === a || (C && C.compare(n.cfi, a) >= 0 && C.compare(n.cfi, b) <= 0)));
    },
    applySettings(s, changed) {
      if (changed.flow || changed.dir || changed.spread) { createRendition(curLoc?.start.cfi); return; }
      if (changed.margin) { layoutViewer(); rendition.resize(); }
      restyleAll();
      // yazı boyutu değişince sayfa düzeni yeniden hesaplanır; aynı yerde kal
      if (changed.size || changed.line || changed.font || changed.margin) { const c = curLoc?.start.cfi; setTimeout(() => c ? rendition.display(c).then(() => setTimeout(redrawHl, 150)) : redrawHl(), 80); }
      else setTimeout(redrawHl, 150);  // vurgu kutuları yeni düzene göre yeniden çizilsin
    },
  };

  R.init(engine).then(async ({ start }) => {
    book = ePub(INFO.file, { openAs: "epub" });
    try {
      await book.opened;
    } catch (e) {
      $("#msg").innerHTML = `<div>Kitap açılamadı.<br><small>${R.esc(e?.message || e)}</small><br><br><a href="${INFO.download}">Dosyayı indir</a></div>`;
      return;
    }
    // Kitap kendisi sağdan sola diyorsa ve kullanıcı seçmediyse ona uy
    if (book.package?.metadata?.direction === "rtl" && R.store.get("book." + INFO.ckey + ".dir", null) == null && !INFO.rtl) S.dir = "rtl";
    const nav = await book.loaded.navigation.catch(() => null);
    const flat = (items, level) => (items || []).forEach((t) => { toc.push({ label: (t.label || "").trim(), href: t.href, level }); flat(t.subitems, level + 1); });
    flat(nav?.toc, 1);
    let target = start?.cfi || start?.href;
    try { await createRendition(target); } catch { await createRendition(); }
    // konum yüzdeleri: önbellekten veya hesaplayarak
    const lkey = "loc." + INFO.ckey;
    const cached = R.store.get(lkey, null);
    if (cached) { try { book.locations.load(cached); } catch {} }
    if (!book.locations.length()) {
      await book.locations.generate(1600).catch(() => {});
      try { R.store.set(lkey, book.locations.save()); } catch {}
    }
    if (curLoc) onRelocated(curLoc);
  });

  $("#slider").addEventListener("input", (e) => { $("#pct").textContent = Math.round(e.target.value / 10) + "%"; });
  $("#slider").addEventListener("change", (e) => {
    if (!book.locations.length()) { const i = Math.floor((e.target.value / 1000) * book.spine.length); rendition.display(book.spine.get(Math.min(i, book.spine.length - 1)).href); return; }
    rendition.display(book.locations.cfiFromPercentage(e.target.value / 1000));
  });
  let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { layoutViewer(); rendition && rendition.resize(); }, 200); });
})();
