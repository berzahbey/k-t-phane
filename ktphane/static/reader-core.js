/* Kitaplık okuyucu çekirdeği: ayarlar, temalar, senkronizasyon, notlar paneli, dokunma bölgeleri.
   EPUB ve PDF okuyucuları bunu kullanır: window.Reader.init(engine) */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const INFO = window.BOOK;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k, d) { try { const v = localStorage.getItem("kt.r." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("kt.r." + k, JSON.stringify(v)); } catch {} },
  };
  const device = (() => {
    const ua = navigator.userAgent;
    if (/iPhone/.test(ua)) return "iPhone";
    if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "iPad";
    if (/Android/.test(ua)) return /Mobile/.test(ua) ? "Android telefon" : "Android tablet";
    if (/Macintosh/.test(ua)) return "Mac";
    if (/Windows/.test(ua)) return "Windows";
    return "Tarayıcı";
  })();
  const timeAgo = (ms) => {
    const s = (Date.now() - ms) / 1000;
    if (s < 60) return "az önce"; if (s < 3600) return Math.round(s / 60) + " dk önce";
    if (s < 86400) return Math.round(s / 3600) + " saat önce"; return Math.round(s / 86400) + " gün önce";
  };

  // ---------- ayarlar (cihaza özel: telefonda ve Mac'te farklı yazı boyutu doğal) ----------
  const RTL = !!INFO.rtl;
  const DEFAULTS = {
    theme: matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light",
    size: RTL ? 125 : 105, font: RTL ? "Amiri" : "Literata", line: RTL ? 1.9 : 1.55, margin: 1, align: "justify",
    flow: "paginated", spread: "auto", pdfFilter: "none", pdfMode: "page", dir: RTL ? "rtl" : "ltr",
  };
  const settingsKey = (INFO.ext === "pdf" ? "pdf" : "epub") + (RTL ? ".rtl" : "");
  const S = Object.assign({}, DEFAULTS, store.get("settings." + settingsKey, {}), store.get("book." + INFO.ckey + ".dir", null) ? { dir: store.get("book." + INFO.ckey + ".dir") } : {});
  const THEMES = [
    ["light", "#faf7f0", "#24221d", "Açık"], ["sepia", "#f4ecd8", "#3b3124", "Sepya"], ["green", "#e3eddc", "#22301f", "Yeşil"],
    ["gray", "#4a4c4f", "#e7e5e0", "Gri"], ["dark", "#1b1b1a", "#d9d6cf", "Koyu"], ["black", "#000000", "#c8c5be", "Siyah"],
  ];
  const themeColors = () => { const t = THEMES.find((x) => x[0] === S.theme) || THEMES[0]; return { bg: t[1], fg: t[2] }; };
  function saveSettings() {
    const { dir, ...rest } = S; store.set("settings." + settingsKey, rest);
  }
  function applyTheme() {
    document.body.className = document.body.className.replace(/\bt-\w+/g, "").trim() + " t-" + S.theme;
    document.body.classList.toggle("rtl-book", S.dir === "rtl");
    const meta = document.querySelector('meta[name="theme-color"]'); if (meta) meta.content = themeColors().bg;
  }

  // ---------- kısa bildirim ----------
  let toastTimer;
  function toast(msg, action) {
    let t = $(".toast"); if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
    t.innerHTML = esc(msg); t.hidden = false;
    if (action) { const b = document.createElement("button"); b.textContent = action.label; b.onclick = () => { t.hidden = true; action.run(); }; t.appendChild(b); }
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), action ? 9000 : 2600);
  }

  // ---------- sunucu ----------
  const api = (path, opt = {}) => fetch(`/api/reading/${INFO.id}${path}`, { headers: { "Content-Type": "application/json" }, ...opt }).then((r) => r.json());
  let engine = null, data = { pos: null, notes: [] }, lastSent = 0, pendingPos = null, posTimer = null;

  function localPos() { return store.get("pos." + INFO.ckey, null); }
  function savePos(pos) {
    pos = { ...pos, device, updated: Date.now() };
    store.set("pos." + INFO.ckey, pos);
    pendingPos = pos;
    clearTimeout(posTimer);
    posTimer = setTimeout(flushPos, 1500);
    $("#pct").textContent = pos.percent != null ? Math.round(pos.percent * 100) + "%" : "";
  }
  function flushPos(beacon) {
    if (!pendingPos) return;
    const body = JSON.stringify(pendingPos); pendingPos = null; lastSent = Date.now();
    if (beacon && navigator.sendBeacon) { navigator.sendBeacon(`/api/reading/${INFO.id}/pos`, new Blob([body], { type: "application/json" })); return; }
    api("/pos", { method: "PUT", body }).then((r) => { data.pos = r.pos; }).catch(() => {});
  }
  addEventListener("pagehide", () => flushPos(true));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushPos(true);
    else checkOtherDevice();
  });
  // Başka cihazda ilerlediysen haber ver
  async function checkOtherDevice() {
    try {
      const d = await api("");
      data.notes = d.notes || [];
      renderNotes(); engine.refreshNotes && engine.refreshNotes(data.notes);
      const sp = d.pos, lp = localPos();
      if (sp && (!lp || sp.updated > lp.updated + 3000) && sp.device !== device && Math.abs((sp.percent || 0) - (lp?.percent || 0)) > 0.002) {
        toast(`${sp.device || "Başka cihaz"}: %${Math.round((sp.percent || 0) * 100)} · ${timeAgo(sp.updated)}`, { label: "Oraya git", run: () => engine.goToPos(sp) });
      }
    } catch {}
  }

  // ---------- notlar ----------
  const COLORS = { yellow: "#f5d76e", green: "#9fd89a", blue: "#9cc8f0", pink: "#f2a7c3", orange: "#f6b26b" };
  async function saveNote(n) {
    const r = await api("/notes", { method: "POST", body: JSON.stringify(n) });
    if (r.note) {
      const i = data.notes.findIndex((x) => x.id === r.note.id);
      i >= 0 ? (data.notes[i] = r.note) : data.notes.push(r.note);
      renderNotes(); engine.refreshNotes && engine.refreshNotes(data.notes);
    }
    return r.note;
  }
  async function deleteNote(id) {
    await api("/notes/" + id, { method: "DELETE" });
    const n = data.notes.find((x) => x.id === id);
    data.notes = data.notes.filter((x) => x.id !== id);
    renderNotes(); engine.removeNote && engine.removeNote(n); engine.refreshNotes && engine.refreshNotes(data.notes);
  }
  function noteDialog({ quote, note = "", title = "Not ekle" }) {
    return new Promise((res) => {
      const d = $("#noteDlg");
      $("#ndTitle").textContent = title;
      $("#ndQuote").hidden = !quote; $("#ndQuote").textContent = quote || "";
      $("#ndText").value = note;
      d.showModal(); setTimeout(() => $("#ndText").focus(), 50);
      $("#ndSave").onclick = () => { d.close(); res($("#ndText").value); };
      $("#ndCancel").onclick = () => { d.close(); res(null); };
      d.oncancel = () => res(null);
    });
  }
  function noteSortKey(n) { return n.percent ?? (n.page ? n.page / 1e6 : 0); }
  function renderNotes() {
    const box = $("#notesList"); if (!box) return;
    const kind = box.dataset.kind || "all";
    const list = data.notes.filter((n) => kind === "all" || (kind === "bookmark" ? n.type === "bookmark" : n.type !== "bookmark")).sort((a, b) => noteSortKey(a) - noteSortKey(b));
    $("#notesCount").textContent = data.notes.length ? `(${data.notes.length})` : "";
    if (!list.length) { box.innerHTML = `<div class="empty">${kind === "bookmark" ? "Yer imi yok. Üstteki yer imi düğmesine basarak ekleyin." : "Henüz not veya vurgu yok.<br>Metni seçip renk veya “Not” seçin."}</div>`; return; }
    box.innerHTML = list.map((n) => {
      const where = n.page ? `Sayfa ${n.page}` : n.percent != null ? `%${Math.round(n.percent * 100)}` : "";
      const label = n.type === "bookmark" ? "Yer imi" : n.type === "highlight" ? "Vurgu" : "Not";
      return `<div class="list-item" data-go="${n.id}" style="--hl:${COLORS[n.color] || COLORS.yellow}">
        <div class="meta"><b>${label}</b><span>${esc(where)}</span>${n.chapter ? `<span>· ${esc(n.chapter)}</span>` : ""}</div>
        ${n.text ? `<div class="q" dir="auto">${esc(n.text.slice(0, 400))}</div>` : ""}
        ${n.note ? `<div class="n" dir="auto">${esc(n.note)}</div>` : ""}
        <div class="acts">${n.type !== "bookmark" ? `<button class="ib" data-edit="${n.id}" title="Notu düzenle"><svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/></svg></button>` : ""}
        <button class="ib" data-del="${n.id}" title="Sil"><svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg></button></div></div>`;
    }).join("");
  }
  function exportNotes() {
    const lines = [`# ${INFO.title}`, INFO.author ? `*${INFO.author}*` : "", ""];
    for (const n of [...data.notes].sort((a, b) => noteSortKey(a) - noteSortKey(b))) {
      const where = n.page ? `s. ${n.page}` : n.percent != null ? `%${Math.round(n.percent * 100)}` : "";
      lines.push(`## ${n.type === "bookmark" ? "Yer imi" : n.type === "highlight" ? "Vurgu" : "Not"} (${where}${n.chapter ? " · " + n.chapter : ""})`);
      if (n.text) lines.push("> " + n.text.replace(/\n/g, "\n> "));
      if (n.note) lines.push("", n.note);
      lines.push("");
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/markdown" }));
    a.download = `${INFO.title} - notlar.md`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  // ---------- çekmece (içindekiler / notlar / arama) ----------
  function openDrawer(tab) {
    $("#drawer").classList.add("open"); $("#scrim").hidden = false; $("#scrim").classList.remove("clear"); $("#settings").hidden = true;
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
    document.querySelectorAll(".pane").forEach((p) => (p.hidden = p.dataset.pane !== tab));
    if (tab === "search") setTimeout(() => $("#searchInput")?.focus(), 200);
    if (tab === "toc") engine.renderToc && engine.renderToc($("#tocBox"));
  }
  function closeAll() {
    $("#drawer").classList.remove("open"); $("#settings").hidden = true; $("#scrim").hidden = true;
  }

  // ---------- ayarlar paneli ----------
  function renderSettings() {
    const sw = THEMES.map(([k, bg, fg, lbl]) => `<button class="swatch ${S.theme === k ? "on" : ""}" data-theme="${k}" title="${lbl}" style="background:${bg};color:${fg}">Aa</button>`).join("");
    const fonts = INFO.ext === "epub" ? [["Literata", "Literata (kitap)"], ["serif", "Serif (sistem)"], ["sans", "Sans (sistem)"], ["original", "Kitabın kendi yazı tipi"], ["Amiri", "Amiri (Osmanlıca/Arapça)"], ["Noto Naskh Arabic", "Noto Naskh (Osmanlıca/Arapça)"]] : [];
    $("#settingsBody").innerHTML = `
      <h4>Tema</h4><div class="row"><div class="ctl">${sw}</div></div>
      ${INFO.ext === "epub" ? `
      <h4>Yazı</h4>
      <div class="row"><label>Boyut</label><div class="ctl"><div class="step"><button data-size="-10">A−</button><span>%${S.size}</span><button data-size="10">A+</button></div></div></div>
      <div class="row"><label>Yazı tipi</label><div class="ctl"><select id="sFont">${fonts.map(([v, l]) => `<option value="${v}" ${S.font === v ? "selected" : ""}>${l}</option>`).join("")}</select></div></div>
      <div class="row"><label>Satır aralığı</label><div class="ctl"><div class="step"><button data-line="-0.1">−</button><span>${S.line.toFixed(1)}</span><button data-line="0.1">+</button></div></div></div>
      <div class="row"><label>Kenar boşluğu</label><div class="ctl seg">${[["0", "Dar"], ["1", "Orta"], ["2", "Geniş"]].map(([v, l]) => `<button data-margin="${v}" class="${S.margin == v ? "on" : ""}">${l}</button>`).join("")}</div></div>
      <div class="row"><label>Hizalama</label><div class="ctl seg"><button data-align="justify" class="${S.align === "justify" ? "on" : ""}">İki yana</button><button data-align="start" class="${S.align === "start" ? "on" : ""}">Başa</button></div></div>
      <h4>Sayfa</h4>
      <div class="row"><label>Akış</label><div class="ctl seg"><button data-flow="paginated" class="${S.flow === "paginated" ? "on" : ""}">Sayfa sayfa</button><button data-flow="scrolled" class="${S.flow === "scrolled" ? "on" : ""}">Kaydırarak</button></div></div>
      <div class="row"><label>Geniş ekranda</label><div class="ctl seg"><button data-spread="auto" class="${S.spread === "auto" ? "on" : ""}">Çift sayfa</button><button data-spread="none" class="${S.spread === "none" ? "on" : ""}">Tek sayfa</button></div></div>` : `
      <h4>Sayfa görünümü</h4>
      <div class="row"><label>Renk</label><div class="ctl seg">${[["none", "Orijinal"], ["sepia", "Sepya"], ["dim", "Kısık"], ["invert", "Ters (gece)"]].map(([v, l]) => `<button data-pdffilter="${v}" class="${S.pdfFilter === v ? "on" : ""}">${l}</button>`).join("")}</div></div>
      <div class="row"><label>Görünüm</label><div class="ctl seg"><button data-pdfmode="page" class="${S.pdfMode === "page" ? "on" : ""}">Sayfa sayfa</button><button data-pdfmode="spread" class="${S.pdfMode === "spread" ? "on" : ""}">Çift sayfa</button><button data-pdfmode="scroll" class="${S.pdfMode === "scroll" ? "on" : ""}">Kaydırarak</button></div></div>
      <div class="row"><label>Yakınlaştır</label><div class="ctl"><div class="step"><button data-zoom="-0.25">−</button><span id="zoomVal">${Math.round((engine.zoom || 1) * 100)}%</span><button data-zoom="0.25">+</button></div><button class="btn" data-zoom="0">Sığdır</button></div></div>`}
      <div class="row"><label>Yön</label><div class="ctl seg"><button data-dir="ltr" class="${S.dir === "ltr" ? "on" : ""}">Soldan sağa</button><button data-dir="rtl" class="${S.dir === "rtl" ? "on" : ""}">Sağdan sola</button></div></div>
      <div class="sync-info" id="syncInfo"></div>`;
    const sp = data.pos;
    $("#syncInfo").textContent = sp ? `Kaldığın yer sunucuda: %${Math.round((sp.percent || 0) * 100)} · ${sp.device || ""} · ${timeAgo(sp.updated)}. Ayarlar bu cihaza özel.` : "Kaldığın yer ve notlar sunucuda saklanır; her cihazdan devam edebilirsin.";
  }
  function onSettingsClick(e) {
    const b = e.target.closest("button"); if (!b) return;
    const d = b.dataset;
    if (d.theme) S.theme = d.theme;
    else if (d.size) S.size = Math.max(60, Math.min(300, S.size + +d.size));
    else if (d.line) S.line = Math.round(Math.max(1.0, Math.min(3, S.line + +d.line)) * 10) / 10;
    else if (d.margin) S.margin = +d.margin;
    else if (d.align) S.align = d.align;
    else if (d.flow) S.flow = d.flow;
    else if (d.spread) S.spread = d.spread;
    else if (d.pdffilter) S.pdfFilter = d.pdffilter;
    else if (d.pdfmode) S.pdfMode = d.pdfmode;
    else if (d.zoom != null) { engine.setZoom && engine.setZoom(+d.zoom); renderSettings(); return; }
    else if (d.dir) { S.dir = d.dir; store.set("book." + INFO.ckey + ".dir", S.dir); }
    else return;
    saveSettings(); applyTheme(); renderSettings();
    engine.applySettings(S, d);
  }

  // ---------- çubuklar, dokunma, klavye ----------
  let immersiveTimer;
  function setImmersive(on) { document.body.classList.toggle("immersive", on); }
  function toggleUI() { setImmersive(!document.body.classList.contains("immersive")); }
  // Ekrana dokunma: kenarlar sayfa çevirir, orta çubukları gösterir/gizler
  function handleTap(x) {
    if (document.querySelector("dialog[open]") || !$("#settings").hidden || $("#drawer").classList.contains("open")) { closeAll(); return; }
    if (!$("#selbar").hidden) { engine.clearSelection && engine.clearSelection(); return; }
    const w = innerWidth, edge = Math.min(w * 0.28, 220);
    if (x < edge) tapSide("left"); else if (x > w - edge) tapSide("right"); else toggleUI();
  }
  function tapSide(side) {  // side: "left" | "right"
    // Soldan sağa kitapta sağ = ileri; sağdan sola (Osmanlıca) kitapta sol = ileri
    const goNext = (side === "right") !== (S.dir === "rtl");
    goNext ? engine.next() : engine.prev();
  }
  function setupInput() {
    document.addEventListener("keydown", (e) => {
      if ((e.target.closest && e.target.closest("input, textarea, select")) || document.querySelector("dialog[open]")) return;
      if (e.key === "ArrowRight" || e.key === "PageDown" || (e.key === " " && !e.shiftKey)) { e.preventDefault(); e.key === "ArrowRight" ? tapSide("right") : engine.next(); }
      else if (e.key === "ArrowLeft" || e.key === "PageUp" || (e.key === " " && e.shiftKey)) { e.preventDefault(); e.key === "ArrowLeft" ? tapSide("left") : engine.prev(); }
      else if (e.key === "Escape") closeAll();
      else if (e.key.toLowerCase() === "f" && !e.metaKey && !e.ctrlKey) toggleUI();
    });
    // kaydırma (swipe)
    let sx = null, sy = null, st = 0;
    const host = $("#stage");
    host.addEventListener("click", (e) => {
      if (engine.ownsTaps) return;
      if (e.target.closest("a, button, input, .hl, .note-pin")) return;
      if (String(getSelection()).trim()) return;
      handleTap(e.clientX);
    });
    host.addEventListener("touchstart", (e) => { if (e.touches.length !== 1) { sx = null; return; } sx = e.touches[0].clientX; sy = e.touches[0].clientY; st = Date.now(); }, { passive: true });
    host.addEventListener("touchend", (e) => {
      if (sx == null || (engine.allowSwipe && !engine.allowSwipe())) return;
      const dx = e.changedTouches[0].clientX - sx, dy = e.changedTouches[0].clientY - sy;
      if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5 && Date.now() - st < 800) tapSide(dx < 0 ? "right" : "left");
      sx = null;
    }, { passive: true });
  }

  function init(eng) {
    engine = eng;
    applyTheme();
    setupInput();
    $("#btnToc").onclick = () => openDrawer("toc");
    $("#btnNotes").onclick = () => openDrawer("notes");
    $("#btnSearch") && ($("#btnSearch").onclick = () => openDrawer("search"));
    $("#btnSettings").onclick = () => { const s = $("#settings"); s.hidden = !s.hidden; $("#scrim").hidden = s.hidden; $("#scrim").classList.toggle("clear", !s.hidden); if (!s.hidden) renderSettings(); };
    $("#btnFull").onclick = () => { const el = document.documentElement; document.fullscreenElement ? document.exitFullscreen() : (el.requestFullscreen ? el.requestFullscreen().catch(() => setImmersive(true)) : setImmersive(true)); };
    $("#btnBookmark").onclick = async () => {
      const cur = engine.currentBookmark && engine.currentBookmark(data.notes);
      if (cur) { await deleteNote(cur.id); toast("Yer imi kaldırıldı"); }
      else { const loc = engine.location(); await saveNote({ type: "bookmark", ...loc }); toast("Yer imi eklendi"); }
      updateBookmarkBtn();
    };
    $("#scrim").onclick = closeAll;
    $("#btnCloseDrawer").onclick = closeAll;
    document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => openDrawer(b.dataset.tab)));
    $("#settings").addEventListener("click", onSettingsClick);
    $("#settings").addEventListener("change", (e) => { if (e.target.id === "sFont") { S.font = e.target.value; saveSettings(); engine.applySettings(S, { font: 1 }); } });
    document.querySelectorAll("[data-nkind]").forEach((b) => (b.onclick = () => {
      $("#notesList").dataset.kind = b.dataset.nkind;
      document.querySelectorAll("[data-nkind]").forEach((x) => x.classList.toggle("on", x === b)); renderNotes();
    }));
    $("#btnExport").onclick = exportNotes;
    $("#btnAddNote").onclick = async () => {
      const loc = engine.location();
      const t = await noteDialog({ title: loc.page ? `Sayfa ${loc.page} için not` : "Bu konum için not" });
      if (t) { await saveNote({ type: "note", ...loc, note: t }); toast("Not kaydedildi"); }
    };
    $("#notesList").addEventListener("click", async (e) => {
      const del = e.target.closest("[data-del]"), ed = e.target.closest("[data-edit]"), go = e.target.closest("[data-go]");
      if (del) { e.stopPropagation(); if (confirm("Bu not silinsin mi?")) await deleteNote(del.dataset.del); return; }
      if (ed) { e.stopPropagation(); const n = data.notes.find((x) => x.id === ed.dataset.edit); const t = await noteDialog({ quote: n.text, note: n.note || "", title: "Notu düzenle" }); if (t != null) await saveNote({ ...n, type: t ? "note" : n.type, note: t }); return; }
      if (go) { const n = data.notes.find((x) => x.id === go.dataset.go); closeAll(); engine.goToNote(n); }
    });
    // ilk yükleme: sunucu ve yerel konumdan en yenisi
    return api("").then((d) => {
      data = { pos: d.pos, notes: d.notes || [] };
      renderNotes();
      const lp = localPos(), sp = d.pos;
      const start = (sp && (!lp || sp.updated >= lp.updated)) ? sp : lp;
      if (sp && lp && sp.updated > lp.updated + 3000 && sp.device !== device) {
        setTimeout(() => toast(`${sp.device || "Başka cihaz"} cihazında kaldığın yerden devam ediliyor (%${Math.round((sp.percent || 0) * 100)})`), 1200);
      }
      return { start, notes: data.notes };
    }).catch(() => ({ start: localPos(), notes: [] }));
  }
  function updateBookmarkBtn() {
    const on = !!(engine && engine.currentBookmark && engine.currentBookmark(data.notes));
    $("#btnBookmark").classList.toggle("on", on);
  }

  window.Reader = { init, handleTap, tapSide, S, INFO, savePos, saveNote, deleteNote, noteDialog, toast, esc, store, themeColors, COLORS,
    updateBookmarkBtn, setImmersive, toggleUI, closeAll, device, get notes() { return data.notes; } };
})();
