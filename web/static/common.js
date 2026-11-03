// Klien WebSocket bersama untuk semua overlay. Semua teks dari chat ditulis lewat textContent (anti-XSS).
(function () {
  const listeners = { state: [], event: [], goal: [], toast: [] };
  let state = null;
  let recvAt = 0;
  let preview = null; // pengaturan sementara dari halaman Admin (preview sebelum disimpan)
  const isAdmin = () => document.body && "admin" in document.body.dataset;

  const ALIGN = { left: ["flex-start", "left"], center: ["center", "center"], right: ["flex-end", "right"] };

  // Gaya overlay dari pengaturan -> variabel CSS (lihat common.css). Halaman Admin punya gaya sendiri.
  function applyTheme(s) {
    if (isAdmin()) return;
    const root = document.documentElement.style;
    document.body.dataset.theme = s.theme || "dark";
    root.setProperty("--accent", s.accent || "#f4a93c");
    root.setProperty("--font", s.font || "Poppins, system-ui, sans-serif");
    root.setProperty("--scale", String((s.overlay_scale || 100) / 100));
    root.setProperty("--card-alpha", `${s.card_opacity ?? 100}%`);
    root.setProperty("--radius", `${s.corner_radius ?? 20}px`);
    root.setProperty("--radius-sm", `${Math.round((s.corner_radius ?? 20) * 0.7)}px`);
    root.setProperty("--stroke", s.card_border === false ? "0px" : "2px");
    const [justify, text] = ALIGN[s.align] || ALIGN.center;
    root.setProperty("--justify", justify);
    root.setProperty("--talign", text);
    document.body.classList.toggle("no-shadow", s.text_shadow === false);
  }

  // Pengaturan efektif untuk overlay ini: gaya dasar + perubahan preview (belum disimpan)
  // + gaya khusus overlay ini (settings.overlay_styles[nama], diatur di tab Tampilan overlay).
  function withPreview(st) {
    const base = preview ? { ...st.settings, ...preview } : st.settings;
    const own = (base.overlay_styles || {})[document.body.dataset.overlay] || {};
    return { ...st, settings: { ...base, ...own } };
  }

  function publish(st) {
    state = withPreview(st);
    applyTheme(state.settings);
    listeners.state.forEach((f) => f(state));
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.type === "state") {
        recvAt = performance.now();
        publish(m.state);
      } else if (m.type === "event") listeners.event.forEach((f) => f(m));
      else if (m.type === "goal") listeners.goal.forEach((f) => f(m));
      else if (m.type === "toast") listeners.toast.forEach((f) => f(m));
    };
    ws.onclose = () => setTimeout(connect, 1500);
    ws.onerror = () => ws.close();
  }

  // Preview di Admin: overlay dimuat dalam iframe, Admin mengirim pengaturan yang belum disimpan
  // dan event contoh. Hanya diterima dari halaman Admin di alamat yang sama.
  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin || window.parent === window) return;
    const m = e.data || {};
    if (m.type === "preview-settings") {
      preview = m.settings;
      if (state) publish(state);
    } else if (m.type === "preview-event") listeners.event.forEach((f) => f({ type: "event", ...m.event }));
    else if (m.type === "preview-goal") listeners.goal.forEach((f) => f({ type: "goal", ...m.goal }));
  });

  // Ekspresi maskot dari brand board: normal, happy (member baru), sleepy (jeda), celebrate (goal).
  const FACES = ["normal", "happy", "sleepy", "celebrate"];
  const faceUrl = (f) => `/static/brand/mascot-${f}.svg`;
  FACES.forEach((f) => {
    new Image().src = faceUrl(f);
  }); // preload agar ganti ekspresi tidak berkedip

  function mascot(img) {
    let base = "normal",
      timer = null;
    const show = (f) => {
      if (!img.src.endsWith(faceUrl(f))) img.src = faceUrl(f);
    };
    return {
      // ekspresi dasar mengikuti status timer
      status(st) {
        base = st === "idle" || st === "paused" ? "sleepy" : st === "ended" ? "celebrate" : "normal";
        if (!timer) show(base);
      },
      // ekspresi sementara untuk event (happy) atau goal (celebrate)
      flash(f, ms) {
        clearTimeout(timer);
        show(f);
        img.classList.remove("bump");
        void img.offsetWidth;
        img.classList.add("bump");
        timer = setTimeout(() => {
          timer = null;
          show(base);
        }, ms);
      },
    };
  }

  window.Sub = {
    mascot,
    faceUrl,
    on(type, fn) {
      listeners[type].push(fn);
      if (type === "state" && state) fn(state);
    },
    start() {
      connect();
    },
    get state() {
      return state;
    },
    // sisa waktu saat ini (detik), diinterpolasi di sisi klien
    remaining() {
      if (!state) return 0;
      if (state.status !== "running") return state.remaining;
      return Math.max(0, state.remaining - (performance.now() - recvAt) / 1000);
    },
    hms(sec) {
      sec = Math.max(0, Math.floor(sec));
      return [Math.floor(sec / 3600), Math.floor((sec % 3600) / 60), sec % 60];
    },
    pad(n) {
      return String(n).padStart(2, "0");
    },
    num(n) {
      return new Intl.NumberFormat("id-ID").format(n);
    },
    el(tag, cls, text) {
      const e = document.createElement(tag);
      if (cls) e.className = cls;
      if (text !== undefined) e.textContent = text;
      return e;
    },
  };
})();
