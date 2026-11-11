/* Kitaplık PDF okuyucusu (pdf.js) */
(() => {
  "use strict";
  const R = window.Reader, INFO = R.INFO, S = R.S, $ = (s) => document.querySelector(s);
  const V = "/static/vendor/pdfjs/";
  pdfjsLib.GlobalWorkerOptions.workerSrc = V + "pdf.worker.min.js";
  const wrap = $("#pdfwrap");
  let pdf = null, pages = 0, cur = 1, zoom = 1, outline = [], baseVp = null, selCtx = null, gen = 0;
  const pageCache = new Map(), textCache = new Map(), vpCache = new Map();
  let searchHit = null;

  const getPage = (n) => { if (!pageCache.has(n)) pageCache.set(n, pdf.getPage(n)); return pageCache.get(n); };
  const isSpread = () => S.pdfMode === "spread" && wrap.clientWidth >= 700 && wrap.clientWidth > wrap.clientHeight * 0.9;
  const spreadStart = (p) => (p <= 1 ? 1 : p % 2 === 0 ? p : p - 1);
  const visiblePages = () => {
    if (S.pdfMode === "scroll") return [cur];
    if (!isSpread()) return [cur];
    const a = spreadStart(cur); return a === 1 ? [1] : [a, a + 1].filter((n) => n <= pages);
  };
  function applyFilter() {
    document.body.classList.remove("pf-sepia", "pf-invert", "pf-dim");
    if (S.pdfFilter !== "none") document.body.classList.add("pf-" + S.pdfFilter);
  }
  function chapterOf(p) { let best = ""; for (const o of outline) { if (o.page && o.page <= p) best = o.title; } return best; }

  // ---------- tek sayfa çizimi ----------
  function pageEl(n, w, h) {
    const el = document.createElement("div");
    el.className = "pg"; el.dataset.page = n; el.style.width = w + "px"; el.style.height = h + "px";
    el.innerHTML = `<div class="ph">${n}</div>`;
    return el;
  }
  async function drawPage(el, n, cssScale, myGen) {
    const page = await getPage(n);
    if (myGen !== gen) return;
    const vp = page.getViewport({ scale: cssScale });
    vpCache.set(n, page.getViewport({ scale: 1 }));
    el.style.width = Math.floor(vp.width) + "px"; el.style.height = Math.floor(vp.height) + "px";
    const dpr = Math.min(window.devicePixelRatio || 1, Math.sqrt(14e6 / (vp.width * vp.height)), 3);
    const canvas = document.createElement("canvas");
    canvas.width = Math.floor(vp.width * dpr); canvas.height = Math.floor(vp.height * dpr);
    const task = page.render({ canvasContext: canvas.getContext("2d", { alpha: false }), viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null });
    try { await task.promise; } catch { return; }
    if (myGen !== gen && S.pdfMode !== "scroll") return;
    el.innerHTML = ""; el.appendChild(canvas);
    el.dataset.rendered = cssScale;
    // seçilebilir metin katmanı
    const tl = document.createElement("div"); tl.className = "textLayer"; tl.style.setProperty("--scale-factor", vp.scale);
    el.appendChild(tl);
    try {
      const tc = await page.getTextContent();
      if (!textCache.has(n)) textCache.set(n, tc.items.map((i) => i.str + (i.hasEOL ? "\n" : "")).join(""));
      await pdfjsLib.renderTextLayer({ textContentSource: tc, container: tl, viewport: vp, textDivs: [] }).promise;
      if (searchHit && searchHit.page === n) markSearch(tl, searchHit.q);
    } catch {}
    const hl = document.createElement("div"); hl.className = "hl-layer"; el.appendChild(hl);
    const pn = document.createElement("div"); pn.className = "pnum"; pn.textContent = n; el.appendChild(pn);
    drawNotes(el, n);
  }
  function drawNotes(el, n) {
    el.querySelectorAll(".hl, .note-pin, .bm-pin").forEach((x) => x.remove());
    const layer = el.querySelector(".hl-layer"); if (!layer) return;
    for (const note of R.notes.filter((x) => x.page === n)) {
      if (note.type === "bookmark") { const b = document.createElement("div"); b.className = "bm-pin"; b.title = "Yer imi"; el.appendChild(b); continue; }
      if (note.rects && note.rects.length) {
        for (const [x, y, w, h] of note.rects) {
          const d = document.createElement("div"); d.className = "hl";
          Object.assign(d.style, { left: x * 100 + "%", top: y * 100 + "%", width: w * 100 + "%", height: h * 100 + "%", background: R.COLORS[note.color] || R.COLORS.yellow });
          d.onclick = (e) => { e.stopPropagation(); showSelbar({ existing: note }); };
          layer.appendChild(d);
        }
      }
      if (note.note && !(note.rects && note.rects.length) || note.type === "note") {
        if (!el.querySelector(".note-pin")) {
          const p = document.createElement("div"); p.className = "note-pin"; p.textContent = "Not";
          p.onclick = (e) => { e.stopPropagation(); R.closeAll(); document.querySelector("#btnNotes").click(); };
          el.appendChild(p);
        }
      }
    }
  }
  function markSearch(tl, q) {
    for (const sp of tl.querySelectorAll("span")) if (norm(sp.textContent).includes(q)) sp.style.background = "rgba(255,152,0,.45)";
  }

  // ---------- görünüm ----------
  function fitScale(vp1, count) {
    const W = wrap.clientWidth - 16, H = wrap.clientHeight - 24;
    const gap = count > 1 ? 8 : 0;
    return Math.min((W - gap) / count / vp1.width, H / vp1.height);
  }
  async function render() {
    const myGen = ++gen;
    clearSelection(true);
    if (S.pdfMode === "scroll") return renderScroll(myGen);
    wrap.className = zoom > 1.01 ? "zoomed" : "";
    const list = visiblePages();
    const vp1 = vpCache.get(list[0]) || baseVp;
    const scale = fitScale(vp1, list.length) * zoom;
    const order = S.dir === "rtl" ? [...list].reverse() : list;
    const els = order.map((n) => { const v = vpCache.get(n) || baseVp; return pageEl(n, Math.floor(v.width * scale), Math.floor(v.height * scale)); });
    const inner = document.createElement("div"); inner.id = "pdfpages";
    inner.style.cssText = zoom > 1.01 ? "display:flex;gap:8px;width:max-content;margin:12px auto" : "display:flex;gap:8px;align-items:center;justify-content:center";
    els.forEach((e) => inner.appendChild(e));
    wrap.innerHTML = ""; wrap.appendChild(inner);
    if (zoom > 1.01) { wrap.scrollLeft = (wrap.scrollWidth - wrap.clientWidth) / 2; wrap.scrollTop = 0; }
    await Promise.all(order.map((n, i) => drawPage(els[i], n, scale, myGen)));
    // sonraki sayfaları önceden hazırla
    for (let k = 1; k <= 2; k++) if (cur + k <= pages) getPage(cur + k);
    afterMove();
  }
  let io = null, scrollScale = 1;
  function renderScroll(myGen) {
    wrap.className = "scroll";
    scrollScale = (Math.min(wrap.clientWidth - 16, 1100) / baseVp.width) * zoom;
    const inner = document.createElement("div"); inner.id = "pdfpages";
    for (let n = 1; n <= pages; n++) { const v = vpCache.get(n) || baseVp; inner.appendChild(pageEl(n, Math.floor(v.width * scrollScale), Math.floor(v.height * scrollScale))); }
    wrap.innerHTML = ""; wrap.appendChild(inner);
    io && io.disconnect();
    io = new IntersectionObserver((ents) => {
      for (const e of ents) {
        const el = e.target, n = +el.dataset.page;
        if (e.isIntersecting && el.dataset.rendered != scrollScale && !el.dataset.busy) {
          el.dataset.busy = 1; drawPage(el, n, scrollScale, gen).finally(() => delete el.dataset.busy);
        }
      }
      // uzaktaki sayfaların belleğini bırak
      inner.querySelectorAll(".pg[data-rendered]").forEach((el) => { if (Math.abs(+el.dataset.page - cur) > 8) { el.innerHTML = `<div class="ph">${el.dataset.page}</div>`; delete el.dataset.rendered; } });
    }, { root: wrap, rootMargin: "150% 0px" });
    inner.querySelectorAll(".pg").forEach((el) => io.observe(el));
    const target = inner.querySelector(`.pg[data-page="${cur}"]`);
    if (target) wrap.scrollTop = target.offsetTop - 8;
    afterMove();
  }
  let scrollRaf = 0;
  wrap.addEventListener("scroll", () => {
    if (S.pdfMode !== "scroll" || scrollRaf) return;
    scrollRaf = requestAnimationFrame(() => {
      scrollRaf = 0;
      const mid = wrap.scrollTop + wrap.clientHeight * 0.4;
      for (const el of wrap.querySelectorAll(".pg")) { if (el.offsetTop + el.offsetHeight > mid) { const n = +el.dataset.page; if (n !== cur) { cur = n; afterMove(); } break; } }
    });
  }, { passive: true });

  function afterMove() {
    const chap = chapterOf(cur), last = visiblePages().slice(-1)[0];
    $("#chap").textContent = chap; $("#miniTop").textContent = chap;
    $("#pageInput").value = visiblePages().length > 1 ? `${visiblePages()[0]}-${last}` : cur;
    $("#slider").value = cur;
    $("#miniBot").textContent = `${cur} / ${pages}`;
    R.savePos({ page: cur, pages, percent: pages > 1 ? (cur - 1) / (pages - 1) : 0, chapter: chap.slice(0, 200) });
    R.updateBookmarkBtn();
  }
  function goTo(n, opts = {}) {
    n = Math.max(1, Math.min(pages, Math.round(n) || 1));
    cur = isSpread() && S.pdfMode !== "scroll" ? spreadStart(n) : n;
    if (S.pdfMode === "scroll") {
      const el = wrap.querySelector(`.pg[data-page="${n}"]`); if (el) wrap.scrollTop = el.offsetTop - 8;
      cur = n; afterMove();
      if (opts.flash && el) { el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1600); }
      return;
    }
    render().then(() => { if (opts.flash) wrap.querySelectorAll(".pg").forEach((el) => { el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1600); }); });
  }
  function step(d) {
    if (S.pdfMode === "scroll") { wrap.scrollBy({ top: d * wrap.clientHeight * 0.9, behavior: "smooth" }); return; }
    if (zoom > 1.01) {  // yakınlaştırılmışken önce sayfanın içinde kaydır
      const atEnd = wrap.scrollTop + wrap.clientHeight >= wrap.scrollHeight - 4, atStart = wrap.scrollTop <= 4;
      if (d > 0 && !atEnd) { wrap.scrollBy({ top: wrap.clientHeight * 0.85, behavior: "smooth" }); return; }
      if (d < 0 && !atStart) { wrap.scrollBy({ top: -wrap.clientHeight * 0.85, behavior: "smooth" }); return; }
    }
    if (isSpread()) { const a = spreadStart(cur); goTo(d > 0 ? (a === 1 ? 2 : a + 2) : (a <= 2 ? 1 : a - 2)); }
    else goTo(cur + d);
    if (d > 0 && !document.body.classList.contains("immersive") && $("#settings").hidden && !$("#drawer").classList.contains("open")) R.setImmersive(true);
  }
  function setZoom(d) {
    zoom = d === 0 ? 1 : Math.max(0.5, Math.min(5, Math.round((zoom + d) * 100) / 100));
    const z = $("#zoomVal"); if (z) z.textContent = Math.round(zoom * 100) + "%";
    render();
  }

  // ---------- yakınlaştırma: iki parmak ve Mac trackpad ----------
  let pinch = null;
  wrap.addEventListener("touchstart", (e) => {
    if (e.touches.length === 2) { const [a, b] = e.touches; pinch = { d: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY), r: 1 }; }
  }, { passive: true });
  wrap.addEventListener("touchmove", (e) => {
    if (!pinch || e.touches.length !== 2) return;
    e.preventDefault();
    const [a, b] = e.touches; pinch.r = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY) / pinch.d;
    const inner = $("#pdfpages"); if (inner) { inner.style.transformOrigin = "center"; inner.style.transform = `scale(${pinch.r})`; }
  }, { passive: false });
  wrap.addEventListener("touchend", () => {
    if (!pinch) return;
    const r = pinch.r; pinch = null;
    if (Math.abs(r - 1) > 0.05) { zoom = Math.max(0.5, Math.min(5, zoom * r)); render(); }
    else { const inner = $("#pdfpages"); if (inner) inner.style.transform = ""; }
  });
  let wheelT, wheelZ = null;
  wrap.addEventListener("wheel", (e) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    wheelZ = (wheelZ ?? zoom) * Math.exp(-e.deltaY * 0.01);
    wheelZ = Math.max(0.5, Math.min(5, wheelZ));
    const inner = $("#pdfpages"); if (inner) inner.style.transform = `scale(${wheelZ / zoom})`;
    clearTimeout(wheelT); wheelT = setTimeout(() => { zoom = wheelZ; wheelZ = null; render(); }, 250);
  }, { passive: false });

  // ---------- metin seçimi, vurgu ve not ----------
  function showSelbar(ctx) {
    selCtx = ctx; $("#selbar").hidden = false;
    $("#selbar [data-act=remove]").hidden = !ctx.existing;
    $("#selbar [data-act=copy]").hidden = !!ctx.existing;
  }
  function clearSelection(keepNative) {
    $("#selbar").hidden = true; selCtx = null;
    if (!keepNative) try { getSelection().removeAllRanges(); } catch {}
  }
  let selT;
  document.addEventListener("selectionchange", () => {
    clearTimeout(selT);
    selT = setTimeout(() => {
      const sel = getSelection(), text = String(sel).trim();
      if (!text || !sel.rangeCount) { if (selCtx && !selCtx.existing) clearSelection(true); return; }
      const range = sel.getRangeAt(0);
      const node = range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement;
      const pg = node && node.closest && node.closest(".pg"); if (!pg || !node.closest(".textLayer")) return;
      const pr = pg.getBoundingClientRect(), rects = [];
      for (const r of range.getClientRects()) {
        if (r.width < 1 || r.height < 1) continue;
        const x = (r.left - pr.left) / pr.width, y = (r.top - pr.top) / pr.height;
        if (x < -0.01 || y < -0.01 || x > 1 || y > 1) continue;  // başka sayfaya taşan kısım
        rects.push([x, y, r.width / pr.width, r.height / pr.height]);
      }
      const n = +pg.dataset.page;
      showSelbar({ page: n, text: text.slice(0, 4000), rects: rects.slice(0, 80), chapter: chapterOf(n).slice(0, 200), percent: pages > 1 ? (n - 1) / (pages - 1) : 0 });
    }, 350);
  });
  $("#selbar").addEventListener("mousedown", (e) => e.preventDefault());  // seçim kaybolmasın
  $("#selbar").addEventListener("click", async (e) => {
    const b = e.target.closest("button"); if (!b || !selCtx) return;
    const ctx = selCtx;
    const base = ctx.existing ? ctx.existing : { page: ctx.page, text: ctx.text, rects: ctx.rects, chapter: ctx.chapter, percent: ctx.percent };
    if (b.dataset.color) await R.saveNote({ ...base, type: ctx.existing ? ctx.existing.type : "highlight", color: b.dataset.color });
    else if (b.dataset.act === "note") {
      const t = await R.noteDialog({ quote: base.text, note: base.note || "" });
      if (t != null) await R.saveNote({ ...base, type: t ? "note" : "highlight", note: t, color: base.color || "yellow" });
    } else if (b.dataset.act === "copy") {
      try { await navigator.clipboard.writeText(ctx.text); R.toast("Kopyalandı"); } catch { R.toast("Kopyalanamadı"); }
    } else if (b.dataset.act === "remove") { await R.deleteNote(ctx.existing.id); R.toast("Vurgu kaldırıldı"); }
    clearSelection();
  });
  function refreshNotes() { wrap.querySelectorAll(".pg").forEach((el) => drawNotes(el, +el.dataset.page)); R.updateBookmarkBtn(); }

  // ---------- içindekiler ----------
  async function loadOutline() {
    const raw = await pdf.getOutline().catch(() => null);
    const out = [];
    const walk = async (items, level) => {
      for (const it of items || []) {
        let page = null;
        try {
          let dest = it.dest; if (typeof dest === "string") dest = await pdf.getDestination(dest);
          if (Array.isArray(dest)) page = typeof dest[0] === "number" ? dest[0] + 1 : (await pdf.getPageIndex(dest[0])) + 1;
        } catch {}
        out.push({ title: (it.title || "").trim(), page, level });
        if (out.length < 2000) await walk(it.items, level + 1);
      }
    };
    await walk(raw, 1);
    outline = out;
    afterMove();
  }
  function renderToc(box) {
    const jump = `<form class="search-box" id="jumpForm"><input id="jumpIn" inputmode="numeric" placeholder="Sayfaya git (1–${pages})"><button class="btn primary">Git</button></form>`;
    if (!outline.length) { box.innerHTML = jump + '<div class="empty">Bu PDF\'te içindekiler yok.<br>Sayfa numarasıyla gidebilirsiniz.</div>'; }
    else {
      const ch = chapterOf(cur);
      box.innerHTML = jump + outline.map((o, i) => `<a href="#" data-i="${i}" class="lvl${Math.min(3, o.level)} ${o.title === ch ? "cur" : ""}" dir="auto">${R.esc(o.title)}${o.page ? ` <span style="float:right;color:var(--muted);font-size:12px">${o.page}</span>` : ""}</a>`).join("");
    }
    box.onclick = (e) => { const a = e.target.closest("a[data-i]"); if (!a) return; e.preventDefault(); const o = outline[+a.dataset.i]; if (o.page) { R.closeAll(); goTo(o.page); } };
    $("#jumpForm").onsubmit = (e) => { e.preventDefault(); R.closeAll(); goTo(+$("#jumpIn").value); };
    box.querySelector(".cur")?.scrollIntoView({ block: "center" });
  }

  // ---------- arama ----------
  function norm(s) {
    return s.replace(/[ً-ٰٟـ­‌‍]/g, "").replace(/[أإآٱ]/g, "ا").replace(/[ىی]/g, "ي").replace(/ک/g, "ك").replace(/ۀ/g, "ه")
      .toLocaleLowerCase("tr").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/\s+/g, " ");
  }
  // normalleştirilmiş metindeki her harfin asıl metindeki yeri (sonuçta asıl yazım gösterilsin)
  function normMap(s) {
    let out = ""; const map = []; let prevSpace = false;
    for (let i = 0; i < s.length; i++) {
      const c = norm(s[i]);
      if (!c) continue;
      if (c === " ") { if (prevSpace) continue; prevSpace = true; } else prevSpace = false;
      for (const ch of c) { out += ch; map.push(i); }
    }
    return { out, map };
  }
  async function pageText(n) {
    if (!textCache.has(n)) {
      const tc = await (await getPage(n)).getTextContent();
      textCache.set(n, tc.items.map((i) => i.str + (i.hasEOL ? "\n" : "")).join(""));
    }
    return textCache.get(n);
  }
  let searchToken = 0;
  async function search(q) {
    const token = ++searchToken, box = $("#searchResults"), nq = norm(q.trim());
    if (nq.length < 2) { box.innerHTML = '<div class="empty">En az 2 harf yazın.</div>'; return; }
    box.innerHTML = '<div class="empty" id="sProg">Aranıyor…</div>';
    const results = []; let textChars = 0;
    for (let n = 1; n <= pages && results.length < 300; n++) {
      if (token !== searchToken) return;
      let raw = ""; try { raw = await pageText(n); } catch {}
      textChars += raw.trim().length;
      const { out: t, map } = normMap(raw);
      let from = 0, idx;
      while ((idx = t.indexOf(nq, from)) >= 0 && results.length < 300) {
        const s0 = map[idx], s1 = map[idx + nq.length - 1] + 1, clean = (x) => x.replace(/\s+/g, " ");
        results.push({ page: n, pre: clean(raw.slice(Math.max(0, s0 - 50), s0)), hit: clean(raw.slice(s0, s1)), post: clean(raw.slice(s1, s1 + 60)) });
        from = idx + nq.length;
      }
      if (n % 10 === 0) { const p = $("#sProg"); if (p) p.textContent = `Aranıyor… sayfa ${n}/${pages} · ${results.length} sonuç`; }
    }
    if (token !== searchToken) return;
    if (!results.length && textChars < pages * 20) {
      box.innerHTML = '<div class="empty">Bu PDF taranmış görüntülerden oluşuyor, içinde aranabilir metin yok.<br>Sayfa numarası veya içindekilerle gezinebilirsiniz.</div>'; return;
    }
    box.innerHTML = results.length
      ? `<div class="empty" style="padding:4px 0 10px">${results.length}${results.length >= 300 ? "+" : ""} sonuç</div>` + results.map((r, i) =>
        `<div class="list-item search-hit" data-r="${i}"><div class="meta">Sayfa ${r.page}${chapterOf(r.page) ? " · " + R.esc(chapterOf(r.page)) : ""}</div><div dir="auto">…${R.esc(r.pre)}<mark>${R.esc(r.hit)}</mark>${R.esc(r.post)}…</div></div>`).join("")
      : '<div class="empty">Sonuç bulunamadı.</div>';
    box.onclick = (e) => {
      const it = e.target.closest("[data-r]"); if (!it) return;
      const r = results[+it.dataset.r]; R.closeAll();
      searchHit = { page: r.page, q: nq }; setTimeout(() => (searchHit = null), 8000);
      if (S.pdfMode === "scroll") { const el = wrap.querySelector(`.pg[data-page="${r.page}"] .textLayer`); if (el) markSearch(el, nq); }
      goTo(r.page, { flash: true });
    };
  }
  $("#searchForm").addEventListener("submit", (e) => { e.preventDefault(); search($("#searchInput").value); });

  // ---------- motor ----------
  const engine = {
    get zoom() { return zoom; },
    next: () => step(1), prev: () => step(-1),
    allowSwipe: () => S.pdfMode !== "scroll" && zoom <= 1.01 && !String(getSelection()).trim(),
    clearSelection: () => clearSelection(),
    renderToc, refreshNotes, setZoom,
    removeNote: () => refreshNotes(),
    goToPos: (p) => goTo(p.page || Math.round((p.percent || 0) * (pages - 1)) + 1),
    goToNote: (n) => n.page && goTo(n.page, { flash: true }),
    location: () => ({ page: cur, chapter: chapterOf(cur).slice(0, 200), percent: pages > 1 ? (cur - 1) / (pages - 1) : 0 }),
    currentBookmark: (notes) => { const vis = visiblePages(); return notes.find((n) => n.type === "bookmark" && vis.includes(n.page)); },
    applySettings(s, changed) {
      applyFilter();
      if (changed.pdfmode || changed.dir) render();
    },
  };

  applyFilter();
  R.init(engine).then(async ({ start }) => {
    try {
      pdf = await pdfjsLib.getDocument({
        url: INFO.file, cMapUrl: V + "cmaps/", cMapPacked: true, standardFontDataUrl: V + "standard_fonts/",
        rangeChunkSize: 262144, disableAutoFetch: true, isEvalSupported: false,
      }).promise;
    } catch (e) {
      $("#msg").innerHTML = `<div>PDF açılamadı.<br><small>${R.esc(e?.message || e)}</small><br><br><a href="${INFO.download}">Dosyayı indir</a></div>`;
      return;
    }
    pages = pdf.numPages;
    baseVp = (await getPage(1)).getViewport({ scale: 1 });
    vpCache.set(1, baseVp);
    $("#pageTotal").textContent = pages;
    $("#slider").max = pages; $("#slider").min = 1;
    cur = Math.max(1, Math.min(pages, start?.page || (start?.percent ? Math.round(start.percent * (pages - 1)) + 1 : 1)));
    if (isSpread() && S.pdfMode !== "scroll") cur = spreadStart(cur);
    $("#msg").hidden = true;
    await render();
    setTimeout(() => { if ($("#settings").hidden && !$("#drawer").classList.contains("open")) R.setImmersive(true); }, 2500);
    loadOutline();
  });

  $("#slider").addEventListener("input", (e) => { $("#miniBot").textContent = $("#pct").textContent = `s. ${e.target.value}`; });
  $("#slider").addEventListener("change", (e) => goTo(+e.target.value));
  $("#pageInput").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); goTo(parseInt(e.target.value, 10)); e.target.blur(); } });
  $("#pageInput").addEventListener("focus", (e) => e.target.select());
  let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => pdf && render(), 250); });
})();
