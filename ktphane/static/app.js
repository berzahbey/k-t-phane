(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const store = {
    get(k, d) { try { return localStorage.getItem("kt." + k) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem("kt." + k, v); } catch {} },
  };

  // Türkçe uyumlu sadeleştirme (sunucudaki fold() ile aynı mantık)
  const FOLD = { "ı": "i", "İ": "i", "I": "i", "ş": "s", "ğ": "g", "ç": "c", "ö": "o", "ü": "u", "â": "a", "î": "i", "û": "u" };
  const fold = (s) => (s || "").replace(/[ıİIşğçöüâîû]/g, (c) => FOLD[c] || c).toLowerCase()
    .normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/[’'`]/g, "");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isRtl = (s) => /[؀-ۿ]/.test(s || "");
  const fmtSize = (b) => b > 1 << 30 ? (b / (1 << 30)).toFixed(1) + " GB" : b > 1 << 20 ? (b / (1 << 20)).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
  const hue = (id) => { let h = 0; for (const c of String(id)) h = (h * 31 + c.charCodeAt(0)) >>> 0; return h % 360; };

  const state = {
    books: [], tree: {}, status: {},
    q: "", cat: null, sub: null, langs: new Set(), fmts: new Set(), states: new Set(),
    sort: store.get("sort", "title"), view: store.get("view", "grid"),
    filtered: [], shown: 0, page: 60,
  };

  const STATES = {
    text: { label: "Metin tabanlı PDF", test: (b) => b.formats.some((f) => f.ext === "pdf" && f.needs_ocr === false) },
    ocr: { label: "OCR gerekli", test: (b) => b.formats.some((f) => f.ext === "pdf" && f.needs_ocr === true) },
    broken: { label: "Bozuk / okunamadı", test: (b) => b.formats.some((f) => f.broken) },
    multi: { label: "Birden fazla format", test: (b) => b.formats.length > 1 },
    series: { label: "Koleksiyon (çok ciltli)", test: (b) => !!b.series },
  };
  const fmtGroup = (ext) => (ext === "epub" || ext === "pdf" ? ext.toUpperCase() : "Diğer");

  // ---------- veri ----------
  async function load() {
    try {
      const r = await fetch("/api/books", { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      const d = await r.json();
      state.status = d.status; state.tree = d.tree; state.analysis = d.analysis;
      state.books = d.books.map((b) => ({
        ...b,
        _s: fold([b.title, b.author, b.subcategory, b.category, (b.subjects || []).join(" "), (b.folders || []).join(" "), b.publisher, b.formats.map((f) => f.name).join(" ")].join(" ")),
      }));
      render();
      updateStatus(d.status);
    } catch (e) {
      showNotice("Kitap listesi alınamadı. Sunucu çalışıyor mu? (" + e.message + ")");
    }
  }

  let pollTimer = null;
  function updateStatus(s) {
    const p = $("#progress"), bar = $("#progressBar"), btn = $("#btnRescan");
    if (s.scanning) {
      p.hidden = false; btn.classList.add("spin");
      bar.style.width = (s.total ? (100 * s.done) / s.total : 5) + "%";
      $("#scanInfo").innerHTML = `Taranıyor… ${s.done.toLocaleString("tr")} / ${s.total.toLocaleString("tr")}` + (s.current ? `<br><small title="${esc(s.current)}">${esc(s.current.split("/").pop())}</small>` : "");
      if (!pollTimer) pollTimer = setInterval(poll, 2000);
    } else {
      p.hidden = true; btn.classList.remove("spin");
      clearInterval(pollTimer); pollTimer = null;
      if (state.analysis && (state.analysis.pending || state.analysis.running || state.analysis.ai_current)) pollTimer = setInterval(poll, 8000);
      $("#scanInfo").innerHTML = (s.last_scan ? "Son tarama: " + new Date(s.last_scan * 1000).toLocaleString("tr") : "") + analysisLine();
    }
    if (s.error) showNotice(s.error); else if (!state.noticeSticky) $("#notice").hidden = true;
  }
  function analysisLine() {
    const a = state.analysis; if (!a) return "";
    let h = "";
    if (a.pending) h += `<br>İçerik okunuyor: ${a.pending.toLocaleString("tr")} dosya kaldı` + (a.current ? `<br><small>${esc(a.current)}</small>` : "");
    if (a.ai && a.ai.enabled) {
      h += `<br>Gemini: bugün ${a.ai.today}/${a.ai.daily_limit}`;
      if (a.ai_pending) h += ` · ${a.ai_pending.toLocaleString("tr")} kitap sırada`;
      if (a.ai_current) h += `<br><small>Gemini: ${esc(a.ai_current)}</small>`;
      if (a.ai.paused_until) h += `<br><span style="color:var(--warn)">${esc(a.ai.error || "Duraklatıldı")} — ${new Date(a.ai.paused_until * 1000).toLocaleString("tr")} devam</span>`;
      else if (a.ai.error) h += `<br><span style="color:var(--warn)">${esc(a.ai.error)}</span>`;
    } else if (a.ai) h += `<br>Yapay zeka kapalı · <button class="linkish" data-open-settings>anahtar gir</button>`;
    return h;
  }
  let lastCount = -1;
  async function poll() {
    try {
      const d = await (await fetch("/api/status", { cache: "no-store" })).json();
      const wasScanning = state.status.scanning;
      state.status = d.status; state.analysis = d.analysis;
      updateStatus(d.status);
      // tarama sırasında yeni kitaplar kademeli görünsün
      const done = d.analysis ? d.analysis.done : 0;
      if (d.count !== lastCount && (d.count - lastCount >= 50 || !d.status.scanning)) { lastCount = d.count; load(); }
      else if (wasScanning && !d.status.scanning) load();
      else if (done - (state.lastDone || 0) >= 10 || (!d.analysis.running && state.lastDone !== done)) { state.lastDone = done; load(); }
    } catch {}
  }
  function showNotice(msg) { const n = $("#notice"); n.textContent = msg; n.hidden = false; }
  function toast(msg, action) {
    const t = $("#toast"); t.textContent = msg; t.hidden = false;
    if (action) {
      const b = document.createElement("button"); b.className = "toast-btn"; b.textContent = action.label;
      b.onclick = () => { t.hidden = true; action.run(); }; t.appendChild(b);
    }
    clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), action ? 9000 : 2600);
  }

  // ---------- filtreleme ----------
  function baseFilter(skip) {
    const terms = fold(state.q).split(/\s+/).filter(Boolean);
    return state.books.filter((b) => {
      if (terms.length && !terms.every((t) => b._s.includes(t))) return false;
      if (skip !== "cat" && state.cat && b.category !== state.cat) return false;
      if (skip !== "cat" && state.sub && b.subcategory !== state.sub) return false;
      if (skip !== "lang" && state.langs.size && !state.langs.has(b.language)) return false;
      if (skip !== "fmt" && state.fmts.size && !b.formats.some((f) => state.fmts.has(fmtGroup(f.ext)))) return false;
      if (skip !== "state" && state.states.size && ![...state.states].every((k) => STATES[k].test(b))) return false;
      return true;
    });
  }
  const cmp = (a, b) => a.localeCompare(b, "tr", { sensitivity: "base" });
  function sortBooks(list) {
    const s = state.sort;
    if (s === "author") return list.sort((a, b) => cmp(a.author || "￿", b.author || "￿") || cmp(a.title, b.title));
    if (s === "added") return list.sort((a, b) => b.added - a.added);
    if (s === "category") return list.sort((a, b) => cmp(a.category, b.category) || cmp(a.subcategory, b.subcategory) || cmp(a.title, b.title));
    return list.sort((a, b) => cmp(a.title, b.title));
  }
  const countBy = (list, fn) => list.reduce((m, b) => { for (const k of [].concat(fn(b))) m[k] = (m[k] || 0) + 1; return m; }, {});

  // ---------- çizim ----------
  function render() {
    renderSidebar();
    state.filtered = collapse(sortBooks(baseFilter()));
    state.shown = 0;
    const box = $("#books");
    box.className = "books " + state.view + (state.selecting ? " selecting" : "");
    box.innerHTML = "";
    document.querySelectorAll(".seg button").forEach((b) => b.classList.toggle("active", b.dataset.view === state.view));
    $("#sort").value = state.sort;
    $("#resultTitle").textContent = state.sub || state.cat || (state.q ? `“${state.q}” için sonuçlar` : "Tüm kitaplar");
    const n = state.filtered.reduce((t, x) => t + (x.isCollection ? x.volumes.length : 1), 0);
    $("#resultCount").textContent = `${n.toLocaleString("tr")} kitap` + (n !== state.books.length ? ` · toplam ${state.books.length.toLocaleString("tr")}` : "");
    renderActiveChips();
    const empty = $("#empty");
    if (!n) {
      empty.hidden = false;
      if (state.status.scanning && !state.books.length) {
        empty.innerHTML = `<strong>Kütüphane ilk kez taranıyor…</strong>Kitaplar bulundukça burada görünecek.`;
        box.innerHTML = Array.from({ length: 12 }, () => `<div class="skeleton"></div>`).join("");
        box.className = "books grid";
      } else if (!state.books.length) {
        empty.innerHTML = `<strong>Henüz kitap yok</strong>Kitap klasörü boş görünüyor. Klasöre kitap ekleyip “Tara”ya basın.`;
      } else {
        empty.innerHTML = `<strong>Sonuç bulunamadı</strong>Aramayı veya filtreleri değiştirmeyi deneyin.`;
      }
    } else empty.hidden = true;
    more();
  }

  function more() {
    const box = $("#books");
    const slice = state.filtered.slice(state.shown, state.shown + state.page);
    state.shown += slice.length;
    box.insertAdjacentHTML("beforeend", slice.map(state.view === "list" ? rowHtml : cardHtml).join(""));
    box.querySelectorAll("img[data-src]").forEach((img) => {
      img.addEventListener("load", () => img.classList.add("ok"), { once: true });
      img.addEventListener("error", () => img.remove(), { once: true });
      img.src = img.dataset.src; img.removeAttribute("data-src");
    });
  }

  function coverHtml(b, withFmt) {
    const h = hue(b.id), dir = isRtl(b.title) ? ' dir="rtl"' : "";
    const img = b.cover_id ? `<img data-src="/cover/${b.cover_id}" alt="" loading="lazy" decoding="async">` : "";
    const fmts = withFmt ? `<span class="fmt">${[...new Set(b.formats.map((f) => f.ext.toUpperCase()))].map((e) => `<span class="badge">${e}</span>`).join("")}</span>` : "";
    const lang = withFmt && b.language ? `<span class="lang-tag lang-${langCode(b.language).toLowerCase()}" title="${esc(b.language)}">${esc(b.language)}</span>` : "";
    return `<div class="cover"><div class="ph" style="background:linear-gradient(160deg,hsl(${h} 32% 38%),hsl(${(h + 30) % 360} 38% 24%))"><span class="t"${dir}>${esc(b.title)}</span><span class="a">${esc(b.author)}</span></div>${img}${lang}${fmts}</div>`;
  }
  const LANG_CODES = { "Türkçe": "TR", "Osmanlıca": "OSM", "Osmanlıca / Farsça": "OSM", "Arapça": "AR", "Farsça": "FA", "English": "EN", "Nederlands": "NL", "Deutsch": "DE", "Français": "FR" };
  const langCode = (l) => LANG_CODES[l] || "XX";
  function badgesHtml(b) {
    const out = [];
    for (const f of b.formats) {
      if (f.broken) out.push(`<span class="badge broken">${f.ext.toUpperCase()} bozuk</span>`);
      else if (f.ext === "epub") out.push(`<span class="badge epub">EPUB</span>`);
      else if (f.ext === "pdf") out.push(f.needs_ocr ? `<span class="badge ocr">PDF · OCR</span>` : `<span class="badge text">PDF · Metin</span>`);
      else out.push(`<span class="badge">${f.ext.toUpperCase()}</span>`);
    }
    out.push(`<span class="badge lang">${esc(b.language)}</span>`);
    return out.join("");
  }
  // Aynı eserin ciltleri tek "koleksiyon" kartında toplanır
  state.collections = {};
  function collapse(list) {
    const out = [], seen = {};
    state.collections = {};
    for (const b of list) {
      if (!b.series_id) { out.push(b); continue; }
      let c = seen[b.series_id];
      if (!c) {
        c = seen[b.series_id] = { isCollection: true, id: b.series_id, title: b.series, author: b.author, language: b.language,
          category: b.category, subcategory: b.subcategory, cover_id: null, volumes: [], formats: [] };
        out.push(c);
      }
      c.volumes.push(b); c.formats.push(...b.formats);
    }
    for (const c of Object.values(seen)) {
      c.volumes.sort((a, b) => (a.volume || 0) - (b.volume || 0));
      c.cover_id = (c.volumes.find((v) => v.cover_id) || {}).cover_id || null;
      c.author = c.author || (c.volumes.find((v) => v.author) || {}).author || "";
      state.collections[c.id] = c;
    }
    return out;
  }
  const volLabel = (c) => `${c.volumes.length} cilt`;
  const selCls = (id) => (state.sel && state.sel.has(id) ? " sel" : "");
  const cardHtml = (b) => {
    if (b.isCollection) {
      return `<button class="card collection${selCls(b.id)}" data-id="${b.id}"><span class="chk" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="m5 12 5 5 9-10"/></svg></span><div class="stack">${coverHtml(b, true)}</div><span class="ti">${esc(b.title)}</span><span class="au">${esc(b.author || "Yazar bilinmiyor")} · ${volLabel(b)}</span></button>`;
    }
    const dir = isRtl(b.title) ? ' dir="rtl"' : "";
    return `<button class="card${selCls(b.id)}" data-id="${b.id}"><span class="chk" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="m5 12 5 5 9-10"/></svg></span>${coverHtml(b, true)}<span class="ti"${dir}>${esc(b.title)}</span><span class="au">${esc(b.author || "Yazar bilinmiyor")}</span></button>`;
  };
  const rowHtml = (b) => b.isCollection ? `<button class="row${selCls(b.id)}" data-id="${b.id}"><span class="chk" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="m5 12 5 5 9-10"/></svg></span>${coverHtml(b, false)}
    <div style="min-width:0"><div class="ti">${esc(b.title)} <span class="badge">${volLabel(b)}</span></div><div class="sub au-m">${esc(b.author)}</div></div>
    <div class="sub col-au">${esc(b.author || "—")}</div><div class="sub col-cat">${esc(b.subcategory)}</div>
    <div class="badges"><span class="badge lang">${esc(b.language)}</span></div></button>` : `<button class="row${selCls(b.id)}" data-id="${b.id}"><span class="chk" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="m5 12 5 5 9-10"/></svg></span>${coverHtml(b, false)}
    <div style="min-width:0"><div class="ti"${isRtl(b.title) ? ' dir="rtl"' : ""}>${esc(b.title)}</div><div class="sub au-m">${esc(b.author)}</div></div>
    <div class="sub col-au">${esc(b.author || "—")}</div><div class="sub col-cat">${esc(b.subcategory)}</div>
    <div class="badges">${badgesHtml(b)}</div></button>`;

  function renderSidebar() {
    const pool = baseFilter("cat");
    const mainCounts = countBy(pool, (b) => b.category), subCounts = countBy(pool, (b) => b.category + "\u0000" + b.subcategory);
    const order = [...Object.keys(state.tree), "Diğer"];
    let html = `<button class="cat all ${!state.cat ? "active" : ""}" data-cat=""><svg viewBox="0 0 24 24"><path d="M4 19V5a2 2 0 0 1 2-2h12v18H6a2 2 0 0 1-2-2zm0 0a2 2 0 0 1 2-2h12"/></svg>Tüm kitaplar<span class="n">${pool.length}</span></button>`;
    for (const main of order) {
      if (!mainCounts[main]) continue;
      const subs = (state.tree[main] || ["Sınıflandırılmamış"]).filter((s) => subCounts[main + "\u0000" + s]);
      const open = state.cat === main;
      html += `<div class="cat-group ${open ? "open" : ""}"><button class="cat ${open && !state.sub ? "active" : ""}" data-cat="${esc(main)}"><svg class="caret" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg>${esc(main)}<span class="n">${mainCounts[main]}</span></button><div class="subs">` +
        subs.map((s) => `<button class="cat sub ${state.sub === s ? "active" : ""}" data-cat="${esc(main)}" data-sub="${esc(s)}">${esc(s)}<span class="n">${subCounts[main + "\u0000" + s]}</span></button>`).join("") + `</div></div>`;
    }
    $("#catNav").innerHTML = html;
    facet("#langFacet", countBy(baseFilter("lang"), (b) => b.language), state.langs, "lang");
    facet("#fmtFacet", countBy(baseFilter("fmt"), (b) => [...new Set(b.formats.map((f) => fmtGroup(f.ext)))]), state.fmts, "fmt");
    const sp = baseFilter("state"), sc = {};
    for (const k in STATES) sc[k] = sp.filter(STATES[k].test).length;
    facet("#stateFacet", sc, state.states, "state", (k) => STATES[k].label);
  }
  function facet(sel, counts, set, kind, label = (k) => k) {
    const keys = Object.keys(counts).filter((k) => counts[k] || set.has(k)).sort((a, b) => counts[b] - counts[a]);
    $(sel).innerHTML = keys.map((k) => `<button class="chip ${set.has(k) ? "on" : ""}" data-kind="${kind}" data-key="${esc(k)}">${esc(label(k))}<span class="n">${counts[k] || 0}</span></button>`).join("") || `<span class="muted" style="padding-left:4px;font-size:12px">—</span>`;
  }
  function renderActiveChips() {
    const chips = [];
    const x = `<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>`;
    for (const k of state.langs) chips.push(`<button class="chip on removable" data-kind="lang" data-key="${esc(k)}">${esc(k)}${x}</button>`);
    for (const k of state.fmts) chips.push(`<button class="chip on removable" data-kind="fmt" data-key="${esc(k)}">${esc(k)}${x}</button>`);
    for (const k of state.states) chips.push(`<button class="chip on removable" data-kind="state" data-key="${esc(k)}">${esc(STATES[k].label)}${x}</button>`);
    $("#activeChips").innerHTML = chips.join("");
  }

  // ---------- detay ----------
  function openCollection(c) {
    const vols = c.volumes.map((v) => {
      const btns = v.formats.map((f) => {
        const readable = (f.ext === "epub" || f.ext === "pdf" || f.ext === "txt") && !f.broken;
        const url = f.ext === "epub" ? `/read/${f.id}` : `/file/${f.id}`;
        return (readable ? `<a class="btn primary sm" href="${url}" target="_blank" rel="noopener">Oku ${f.ext.toUpperCase()}</a>` : "") +
          `<a class="btn sm" href="/download/${f.id}" title="İndir">↓</a>`;
      }).join("");
      return `<div class="vol-row"><button class="vol-open" data-vol="${v.id}"><span class="vol-n">${v.volume ?? "–"}</span><span class="vol-t">${esc(v.title)}</span></button><div class="vol-btns">${btns}<button class="btn sm danger" data-del="${v.formats.map((f) => f.id).join(",")}" data-title="${esc(v.title)}" data-what="bu cildi" title="Cildi sil"><svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg></button></div></div>`;
    }).join("");
    $("#detailBody").innerHTML = `<div class="d-wrap">${coverHtml(c, false)}<div class="d-info">
      <h2>${esc(c.title)}</h2><div class="by">${esc(c.author || "Yazar bilinmiyor")} · ${volLabel(c)}</div>
      <dl class="d-meta"><dt>Kategori</dt><dd>${esc(c.category)} › ${esc(c.subcategory)}</dd><dt>Dil</dt><dd>${esc(c.language)}</dd>
      <dt>Klasör</dt><dd>${esc(c.language)} / ${esc(c.category)} / Koleksiyonlar / ${esc(c.title)}</dd></dl>
      <div class="vols">${vols}</div>
      <button class="linkish danger-link" data-del="${c.formats.map((f) => f.id).join(",")}" data-title="${esc(c.title)}" data-what="koleksiyonun tamamını (${c.volumes.length} cilt)">Koleksiyonu tümüyle sil</button></div></div>`;
    const img = $("#detailBody img[data-src]");
    if (img) { img.addEventListener("load", () => img.classList.add("ok")); img.addEventListener("error", () => img.remove()); img.src = img.dataset.src; }
    $("#detailBody").querySelectorAll("[data-vol]").forEach((el) => el.addEventListener("click", () => openDetail(el.dataset.vol)));
    const dlg = $("#detail"); if (!dlg.open) dlg.showModal();
    history.replaceState(null, "", "#kitap=" + c.id);
  }

  function openDetail(id) {
    if (state.collections[id]) return openCollection(state.collections[id]);
    const b = state.books.find((x) => x.id === id);
    if (!b) return;
    const dir = isRtl(b.title) ? ' dir="rtl"' : "";
    const src = { ai: "Yapay zeka (Gemini)", content: "Kitabın içeriği", folder: "Senin klasörün", rules: "Dosya adı / etiketler" }[b.why?.source] || "";
    const whyTxt = b.pending ? "İçerik analizi sürüyor…" : b.why?.source === "rules" ? esc(b.why.text) :
      `${src}${b.why?.source === "ai" ? ` · %${Math.round(b.why.conf * 100)} emin` : ""}${b.why?.source === "folder" ? "" : ` — ${esc(b.why?.text || "")}`}`;
    const meta = [
      ["Kategori", `${esc(b.category)} › ${esc(b.subcategory)}`],
      ["Neden", whyTxt],
      ["Dil", esc(b.language)],
      b.year && ["Yıl", esc(b.year)],
      b.publisher && ["Yayınevi", esc(b.publisher)],
      b.subjects?.length && ["Konular", esc(b.subjects.join(", "))],
      b.folders?.length && ["Klasör", esc(b.folders.join(" / "))],
    ].filter(Boolean).map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
    const formats = b.formats.map((f) => {
      const readable = (f.ext === "epub" || f.ext === "pdf" || f.ext === "txt") && !f.broken;
      const readUrl = f.ext === "epub" ? `/read/${f.id}` : `/file/${f.id}`;
      const extra = [fmtSize(f.size), f.pages ? f.pages + " sayfa" : "", f.error ? `<span style="color:var(--warn)">${esc(f.error)}</span>` : ""].filter(Boolean).join(" · ");
      return `<div class="fmt-row"><div class="info"><div class="badges">${badgesHtml({ formats: [f], language: b.language }).replace(/<span class="badge lang">.*$/, "")}</div><div class="fn" title="${esc(f.path)}">${esc(f.name)} · ${extra}</div></div>
        ${readable ? `<a class="btn primary" href="${readUrl}" target="_blank" rel="noopener"><svg viewBox="0 0 24 24"><path d="M2 5h7a3 3 0 0 1 3 3v12a2 2 0 0 0-2-2H2zM22 5h-7a3 3 0 0 0-3 3v12a2 2 0 0 1 2-2h8z"/></svg>Oku</a>` : ""}
        <a class="btn" href="/download/${f.id}"><svg viewBox="0 0 24 24"><path d="M12 3v12m0 0-5-5m5 5 5-5M4 21h16"/></svg>İndir</a>
        <button class="btn danger" data-del="${f.id}" data-title="${esc(b.title)}" data-what="${esc(f.ext.toUpperCase())}" title="Bu dosyayı sil"><svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>Sil</button></div>`;
    }).join("");
    const ocrHint = b.formats.some((f) => f.ext === "pdf" && f.needs_ocr) ? `<p class="muted" style="font-size:12.5px">Bu PDF taranmış görüntüden oluşuyor; metin seçilemez ve aranamaz. İleride OCR ile metne çevrilebilir.</p>` : "";
    $("#detailBody").innerHTML = `<div class="d-wrap">${coverHtml(b, false)}<div class="d-info">
      <h2${dir}>${esc(b.title)}</h2><div class="by">${esc(b.author || "Yazar bilinmiyor")}</div>
      ${b.description ? `<p class="d-desc">${esc(b.description)}</p>` : ""}
      <dl class="d-meta">${meta}</dl>${ocrHint}<div class="d-formats">${formats}</div>
      ${b.formats.length > 1 ? `<button class="linkish danger-link" data-del="${b.formats.map((f) => f.id).join(",")}" data-title="${esc(b.title)}" data-what="tüm formatları (${b.formats.length} dosya)">Kitabı tümüyle sil</button>` : ""}</div></div>`;
    const img = $("#detailBody img[data-src]");
    if (img) { img.addEventListener("load", () => img.classList.add("ok")); img.addEventListener("error", () => img.remove()); img.src = img.dataset.src; }
    const dlg = $("#detail");
    if (!dlg.open) dlg.showModal();
    history.replaceState(null, "", "#kitap=" + b.id);
  }

  // ---------- olaylar ----------
  let qTimer;
  $("#q").addEventListener("input", (e) => { clearTimeout(qTimer); qTimer = setTimeout(() => { state.q = e.target.value.trim(); render(); }, 120); });
  $("#sort").addEventListener("change", (e) => { state.sort = e.target.value; store.set("sort", state.sort); render(); });
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => { state.view = b.dataset.view; store.set("view", state.view); render(); }));
  $("#books").addEventListener("click", (e) => {
    const c = e.target.closest("[data-id]"); if (!c) return;
    if (state.selecting) { toggleSel(c.dataset.id, c); return; }
    openDetail(c.dataset.id);
  });
  $("#catNav").addEventListener("click", (e) => {
    const c = e.target.closest(".cat"); if (!c) return;
    const cat = c.dataset.cat || null, sub = c.dataset.sub || null;
    if (!sub && cat && state.cat === cat && !state.sub) state.cat = null; else { state.cat = cat; state.sub = sub; }
    if (!state.cat) state.sub = null;
    render(); if (sub) closeSidebar(); window.scrollTo({ top: 0 });
  });
  document.addEventListener("click", (e) => {
    const c = e.target.closest(".chip[data-kind]"); if (!c) return;
    const set = { lang: state.langs, fmt: state.fmts, state: state.states }[c.dataset.kind];
    set.has(c.dataset.key) ? set.delete(c.dataset.key) : set.add(c.dataset.key);
    render();
  });
  $("#btnRescan").addEventListener("click", async (e) => {
    const full = e.shiftKey;
    const r = await fetch("/api/rescan" + (full ? "?full=1" : ""), { method: "POST" }).then((r) => r.json()).catch(() => null);
    if (!r) return toast("Sunucuya ulaşılamadı");
    toast(r.started ? (full ? "Tam tarama başladı" : "Tarama başladı — yeni kitaplar eklenecek") : "Zaten tarama yapılıyor");
    updateStatus({ ...r.status, scanning: true });
  });
  const openSidebar = () => { $("#sidebar").classList.add("open"); $("#scrim").hidden = false; };
  const closeSidebar = () => { $("#sidebar").classList.remove("open"); $("#scrim").hidden = true; };
  $("#btnFilters").addEventListener("click", openSidebar);
  $("#btnCloseFilters").addEventListener("click", closeSidebar);
  $("#scrim").addEventListener("click", closeSidebar);
  $("#detail").addEventListener("close", () => history.replaceState(null, "", location.pathname + location.search));
  $("#detail").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== $("#q") && !$("#detail").open) { e.preventDefault(); $("#q").focus(); }
    if (e.key === "Escape" && document.activeElement === $("#q") && $("#q").value) { $("#q").value = ""; state.q = ""; render(); }
    else if (e.key === "Escape" && state.selecting && !document.querySelector("dialog[open]")) setSelecting(false);
  });
  new IntersectionObserver((ents) => { if (ents[0].isIntersecting && state.shown < state.filtered.length) more(); }, { rootMargin: "800px" }).observe($("#sentinel"));

  // ---------- klasör düzenleme ----------
  const ARROW = `<svg viewBox="0 0 24 24" style="width:14px;height:14px"><path d="M5 12h14m-5-5 5 5-5 5"/></svg>`;
  let orgTimer = null;
  async function orgFetch(url = "/api/organize", body) {
    const r = await fetch(url, body === undefined ? { cache: "no-store" } : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || r.status);
    return d;
  }
  function orgRender(d) {
    const box = $("#orgContent"), st = d.status || {};
    let h = "";
    if (!d.writable) {
      h += `<div class="org-card warn"><b>Kitap klasörü salt okunur bağlanmış.</b><br>Düzenleme için ZimaOS'ta uygulamanın ayarlarında kitap klasörü satırının sonundaki <code>:ro</code> kısmını silip kaydedin.</div>`;
    }
    if (st.running) {
      const pct = st.total ? Math.round((100 * st.done) / st.total) : 5;
      h += `<div class="org-card"><b>Dosyalar taşınıyor…</b> ${st.done || 0} / ${st.total || "?"}<div class="org-progress"><div style="width:${pct}%"></div></div></div>`;
    } else if (d.scanning) {
      h += `<div class="org-card">Kütüphane taranıyor. Tarama bitince ${d.auto ? "düzenleme otomatik yapılacak." : "önizleme hazır olacak."}</div>`;
    }
    if (st.last && !st.running) {
      const l = st.last, when = new Date(l.time * 1000).toLocaleString("tr");
      h += `<div class="org-card ${l.errors && l.errors.length ? "warn" : "ok"}">${l.undo ? "Geri alındı" : "Düzenlendi"}: <b>${l.moved}</b> dosya · ${when}` +
        (l.errors && l.errors.length ? `<br><small>${l.errors.length} dosya taşınamadı: ${esc(l.errors.slice(0, 3).join(" · "))}</small>` : "") + `</div>`;
    }
    if (!st.running && !d.scanning) {
      if (d.pending_books) h += `<div class="org-card">${d.pending_books.toLocaleString("tr")} kitabın içeriği henüz okunuyor; okununca ${d.auto ? "otomatik" : "burada"} yerleştirilecek. (Gemini sonradan farklı bir kategori bulursa kitap bir kez daha taşınabilir.)</div>`;
      if (!d.count) {
        h += `<div class="org-card ok">${d.pending_books ? "İncelenmiş" : "Bütün"} kitaplar doğru klasörde. ✓</div>`;
      } else {
        h += `<div class="org-card"><b>${d.count.toLocaleString("tr")} dosya</b> yeni yerine taşınacak:<div class="org-summary">` +
          d.summary.slice(0, 24).map(([k, n]) => `<span class="chip">${esc(k)}<span class="n">${n}</span></span>`).join("") + `</div>` +
          `<div class="org-moves">` + d.moves.slice(0, 300).map((m) => `<div><span class="from" title="${esc(m.from)}">${esc(m.from)}</span><span class="arrow">${ARROW}</span><span title="${esc(m.to)}">${esc(m.to)}</span></div>`).join("") +
          (d.count > 300 ? `<div><span class="muted">… ve ${d.count - 300} dosya daha</span></div>` : "") + `</div></div>`;
      }
    }
    h += `<div class="org-actions">`;
    if (d.count && !st.running && !d.scanning) h += `<button class="btn primary" id="orgApply" ${d.writable ? "" : "disabled"}>${d.count.toLocaleString("tr")} dosyayı düzenle</button>`;
    h += `<label class="switch"><input type="checkbox" id="orgAuto" ${d.auto || (d.count && !d.last_batch) ? "checked" : ""} ${d.writable ? "" : "disabled"}>Yeni kitapları otomatik düzenle</label>`;
    if (d.can_undo && !st.running) h += `<button class="btn" id="orgUndo" style="margin-left:auto" title="Son düzenlemedeki dosyaları eski yerlerine taşır">Son düzenlemeyi geri al${d.last_batch ? ` (${d.last_batch.count})` : ""}</button>`;
    h += `</div>`;
    box.innerHTML = h;

    $("#orgApply")?.addEventListener("click", async () => {
      try {
        const r = await orgFetch("/api/organize/apply", { auto: $("#orgAuto").checked });
        toast(r.started ? "Düzenleme başladı" : "Tarama sürüyor; bitince düzenlenecek");
        orgPoll();
      } catch (e) { toast(e.message); }
    });
    $("#orgUndo")?.addEventListener("click", async () => {
      if (!confirm("Son düzenlemede taşınan dosyalar eski yerlerine dönsün mü? Otomatik düzenleme kapatılacak.")) return;
      try { await orgFetch("/api/organize/undo", {}); toast("Geri alınıyor…"); orgPoll(); } catch (e) { toast(e.message); }
    });
    $("#orgAuto")?.addEventListener("change", async (e) => {
      // Önizleme bekleyen dosya varsa, anahtar 'Düzenle' ile birlikte gönderilir
      if (d.count && !d.auto) return;
      try { await orgFetch("/api/organize/auto", { on: e.target.checked }); toast(e.target.checked ? "Otomatik düzenleme açık" : "Otomatik düzenleme kapalı"); } catch (er) { toast(er.message); }
    });
    clearTimeout(orgTimer);
    if (st.running || d.scanning) orgTimer = setTimeout(orgPoll, 1500);
  }
  let orgWasBusy = false;
  async function orgPoll() {
    try {
      const d = await orgFetch();
      const busy = d.status?.running || d.scanning;
      if (orgWasBusy && !busy) load();
      orgWasBusy = busy;
      if ($("#organize").open) orgRender(d);
      else if (busy) orgTimer = setTimeout(orgPoll, 2000);
    } catch (e) { $("#orgContent").innerHTML = `<div class="org-card warn">${esc(e.message)}</div>`; }
  }
  $("#btnOrganize").addEventListener("click", () => { $("#organize").showModal(); orgPoll(); });
  $("#organize").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });

  // ---------- toplu seçim ----------
  state.sel = new Set(); state.selecting = false;
  function setSelecting(on) {
    state.selecting = on;
    if (!on) state.sel.clear();
    $("#btnSelect").classList.toggle("active", on);
    $("#books").classList.toggle("selecting", on);
    if (!on) document.querySelectorAll("#books .sel").forEach((el) => el.classList.remove("sel"));
    updateSelBar();
  }
  function toggleSel(id, el) {
    state.sel.has(id) ? state.sel.delete(id) : state.sel.add(id);
    if (el) el.classList.toggle("sel", state.sel.has(id));
    updateSelBar();
  }
  // seçili öğe (kitap veya koleksiyon) -> dosya listesi
  function selectedFiles() {
    const files = [];
    for (const b of state.books) {
      if (state.sel.has(b.id) || (b.series_id && state.sel.has(b.series_id))) files.push(...b.formats);
    }
    return files;
  }
  function updateSelBar() {
    const bar = $("#selBar");
    bar.hidden = !state.selecting;
    if (!state.selecting) return;
    const n = state.sel.size, files = selectedFiles();
    $("#selCount").textContent = n ? `${n.toLocaleString("tr")} seçildi · ${files.length.toLocaleString("tr")} dosya · ${fmtSize(files.reduce((t, f) => t + f.size, 0))}` : "Kitaplara dokunarak seçin";
    $("#selDelete").disabled = !n;
  }
  $("#btnSelect").addEventListener("click", () => setSelecting(!state.selecting));
  $("#selCancel").addEventListener("click", () => setSelecting(false));
  $("#selAll").addEventListener("click", () => {
    const all = state.filtered.every((x) => state.sel.has(x.id));
    for (const x of state.filtered) all ? state.sel.delete(x.id) : state.sel.add(x.id);
    document.querySelectorAll("#books [data-id]").forEach((el) => el.classList.toggle("sel", state.sel.has(el.dataset.id)));
    $("#selAll").textContent = all ? "Görünenlerin tümünü seç" : "Seçimi kaldır";
    updateSelBar();
  });
  $("#selDelete").addEventListener("click", async () => {
    const files = selectedFiles(); if (!files.length) return;
    const ok = await deleteFiles(files.map((f) => f.id), `${state.sel.size} seçili kitap`, `${files.length} dosya`);
    if (ok) setSelecting(false);
  });

  // ---------- silme (iki aşamalı onay, kalıcı) ----------
  function ask({ step, title, body, ok }) {
    return new Promise((resolve) => {
      const dlg = $("#confirmDlg");
      $("#cfStep").textContent = step; $("#cfTitle").textContent = title; $("#cfBody").innerHTML = body; $("#cfOk").textContent = ok;
      const done = (v) => { dlg.close(); $("#cfOk").onclick = $("#cfCancel").onclick = null; resolve(v); };
      $("#cfOk").onclick = () => done(true); $("#cfCancel").onclick = () => done(false);
      dlg.oncancel = (e) => { e.preventDefault(); done(false); };
      dlg.showModal(); $("#cfCancel").focus();
    });
  }
  function filesFor(ids) {
    const out = [];
    for (const b of state.books) for (const f of b.formats) if (ids.includes(f.id)) out.push(f);
    return out;
  }
  async function deleteFiles(ids, title, what) {
    const files = filesFor(ids);
    const list = `<ul class="cf-list">${files.slice(0, 12).map((f) => `<li>${esc(f.path)} <span>· ${fmtSize(f.size)}</span></li>`).join("")}${files.length > 12 ? `<li>… ve ${files.length - 12} dosya daha</li>` : ""}</ul>`;
    const ok1 = await ask({ step: "1 / 2", title: `“${title}” silinsin mi?`, body: `Silinecek: <b>${esc(what)}</b>${list}`, ok: "Sil" });
    if (!ok1) return false;
    const ok2 = await ask({ step: "2 / 2", title: "Emin misiniz? Bu işlem geri alınamaz.",
      body: `<b>${files.length} dosya</b> sunucudan <b>kalıcı olarak</b> silinecek. Çöp kutusuna gitmez, geri getirilemez.${list}`, ok: "Evet, kalıcı olarak sil" });
    if (!ok2) return false;
    try {
      let deleted = 0, errors = 0;
      for (let i = 0; i < ids.length; i += 400) {  // büyük seçimler parça parça
        toast(`Siliniyor… ${Math.min(i + 400, ids.length)} / ${ids.length}`);
        const r = await fetch("/api/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: ids.slice(i, i + 400), title, confirm: "KALICI-SIL" }) });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || r.status);
        deleted += d.deleted.length; errors += (d.errors || []).length;
      }
      $("#detail").open && $("#detail").close();
      await load();
      toast(errors ? `${deleted} silindi, ${errors} silinemedi` : `${deleted} dosya kalıcı olarak silindi`);
      return true;
    } catch (e) { toast("Silinemedi: " + e.message); await load(); return false; }
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-del]"); if (!b) return;
    e.preventDefault(); e.stopPropagation();
    deleteFiles(b.dataset.del.split(","), b.dataset.title, b.dataset.what);
  });

  // ---------- ayarlar ----------
  function setStatus(msg, kind) {
    const el = $("#setStatus"); el.hidden = !msg; el.className = "org-card " + (kind || ""); el.innerHTML = msg || "";
  }
  async function openSettings() {
    $("#settings").showModal(); setStatus("");
    try {
      const d = await (await fetch("/api/settings", { cache: "no-store" })).json();
      const ai = d.ai, st = d.ai_status;
      $("#setKey").value = ""; $("#setKey").placeholder = ai.has_key ? `Kayıtlı: ${ai.key_hint}${ai.key_source === "env" ? " (compose)" : ""} — değiştirmek için yenisini yapıştırın` : "AIza…";
      $("#setLimit").value = ai.daily_limit; $("#setModel").value = ai.model;
      if (ai.has_key) setStatus(st.paused_until ? `${esc(st.error || "Duraklatıldı")} — ${new Date(st.paused_until * 1000).toLocaleString("tr")} devam edecek. Bugün: ${st.today}/${st.daily_limit}` : st.error ? esc(st.error) : `Yapay zeka açık · bugün ${st.today}/${st.daily_limit} istek`, st.error ? "warn" : "ok");
      else setStatus("Yapay zeka kapalı — anahtar girilmemiş.", "");
    } catch (e) { setStatus("Ayarlar okunamadı: " + esc(e.message), "warn"); }
  }
  async function saveSettings(body) {
    setStatus("Kaydediliyor ve deneniyor…");
    try {
      const r = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || r.status);
      if (d.test_ok === false) setStatus(esc(d.test_message), "warn");
      else { setStatus(d.test_message ? "✓ " + esc(d.test_message) + " — kitaplar arka planda incelenmeye başlayacak." : "Kaydedildi.", "ok"); $("#setKey").value = ""; }
      $("#setKey").placeholder = d.ai.has_key ? `Kayıtlı: ${d.ai.key_hint}` : "AIza…";
      poll();
    } catch (e) { setStatus("Kaydedilemedi: " + esc(e.message), "warn"); }
  }
  $("#btnSettings").addEventListener("click", openSettings);
  document.addEventListener("click", (e) => { if (e.target.closest("[data-open-settings]")) openSettings(); });
  $("#settings").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });
  $("#setSave").addEventListener("click", () => {
    const body = { gemini_model: $("#setModel").value, gemini_daily_limit: $("#setLimit").value };
    if ($("#setKey").value.trim()) body.gemini_api_key = $("#setKey").value.trim();
    saveSettings(body);
  });
  $("#setTest").addEventListener("click", async () => {
    setStatus("Deneniyor…");
    const d = await (await fetch("/api/settings/test", { method: "POST" })).json().catch(() => ({ ok: false, message: "Sunucuya ulaşılamadı" }));
    setStatus((d.ok ? "✓ " : "") + esc(d.message), d.ok ? "ok" : "warn");
  });
  $("#setClear").addEventListener("click", () => { if (confirm("Kayıtlı API anahtarı silinsin mi?")) saveSettings({ gemini_api_key: "" }); });

  load().then(() => {
    const m = location.hash.match(/kitap=([^&]+)/);
    if (m) openDetail(m[1]);
  });
})();
