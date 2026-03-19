/* ── PropBot Landing Page JS ──────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", function () {

  /* ── Intersection Observer for .anim-card ─────────────────────── */
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) e.target.classList.add("visible");
      });
    },
    { threshold: 0.12 }
  );
  document.querySelectorAll(".anim-card").forEach((el) => observer.observe(el));

  /* ── Chat demo ─────────────────────────────────────────────────── */
  const conversations = {
    whatsapp: [
      { type: "user", text: "Hola! Busco un departamento en alquiler en el centro, 2 ambientes, con balcón." },
      { type: "bot", text: "Hola! Con quién hablo? Encontré 3 propiedades que coinciden con lo que buscás en Centro. Te paso la primera:" },
      { type: "card", icon: "fa-building", titulo: "Depto 2 amb con balcón", precio: "USD 550/mes", tags: ["2 amb", "Balcón", "Piso 5"], direccion: "Rivadavia 2340, Centro" },
      { type: "bot", text: "Tiene balcón al frente, piso 5 con mucha luz. Los horarios de visita son martes a jueves 10-13 y 15-18. Te interesa agendar?" },
      { type: "user", text: "Sí, me interesa! Soy Martín. Tiene cochera?" },
      { type: "bot", text: "Dale Martín, sí tiene cochera incluida en el precio. Te mando las fotos: drive.google.com/… Qué día te viene bien para la visita?" },
    ],
    messenger: [
      { type: "user", text: "Buenas, busco casa en venta con pileta y quincho, presupuesto hasta USD 250.000." },
      { type: "bot", text: "Buenas! Con quién tengo el gusto? Tengo opciones que te van a gustar. Mirá esta:" },
      { type: "card", icon: "fa-home", titulo: "Casa con pileta y quincho", precio: "USD 220.000", tags: ["5 amb", "Pileta", "Quincho", "350 m²"], direccion: "Bello Horizonte" },
      { type: "bot", text: "Tiene pileta, quincho, jardín, 4 dormitorios y 3 baños. Acepta crédito hipotecario. Te paso las fotos y más detalles?" },
      { type: "user", text: "Perfecto, sí mándame todo." },
      { type: "bot", text: "Ahí van las fotos: drive.google.com/…\nDirección: Estados Unidos 5314. Para visitar se puede miércoles 10-13 o viernes 10-13, cuál te queda mejor?" },
    ],
    instagram: [
      { type: "user", text: "Hola! Vi el post del chalet en barrio privado. Cuánto sale?" },
      { type: "bot", text: "Hola! El chalet en Las Golondrinas está a USD 310.000. Es a estrenar, te cuento:" },
      { type: "card", icon: "fa-key", titulo: "Chalet en barrio privado", precio: "USD 310.000", tags: ["5 amb", "500 m²", "Seguridad 24hs", "A estrenar"], direccion: "Ecuador 9999 · Las Golondrinas" },
      { type: "bot", text: "Tiene 4 dormitorios, 3 baños, pileta, quincho, jardín y cochera doble. Barrio privado con seguridad 24hs. Querés que un asesor te contacte?" },
      { type: "user", text: "Sí por favor, soy de otra provincia y no puedo ir pronto." },
      { type: "bot", text: "Sin problema, te conecto con el asesor para una videollamada. Pasame tu número y te escribimos por WhatsApp para coordinar." },
    ],
  };

  const platformColors = {
    whatsapp: { color: "#25D366", dark: "#128C7E" },
    messenger: { color: "#0084FF", dark: "#0068CC" },
    instagram: { color: "#E1306C", dark: "#C13584" },
  };

  let activeTab = "whatsapp";
  let chatStep = 0;
  let chatTimer = null;

  function getTimeStr() {
    return new Date().toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit" });
  }

  function renderPropertyCard(msg) {
    return `<div class="chat-bubble-in pb-bubble bot" style="animation-delay:0s">
      <div style="width:32px;flex-shrink:0;margin-right:8px"></div>
      <div class="pb-prop-card">
        <div class="pb-prop-img"><i class="fa-solid ${msg.icon}" style="font-size:2.5rem"></i></div>
        <div class="pb-prop-title">${msg.titulo}</div>
        <div class="pb-prop-price">${msg.precio}</div>
        <div class="pb-prop-tags">${msg.tags.map(t => `<span class="pb-prop-tag">${t}</span>`).join("")}</div>
        <div class="pb-prop-loc"><i class="fa-solid fa-map-marker-alt" style="color:#D97706"></i> ${msg.direccion}</div>
      </div>
    </div>`;
  }

  function renderBubble(msg) {
    if (msg.type === "card") return renderPropertyCard(msg);
    const isUser = msg.type === "user";
    const p = platformColors[activeTab];
    let html = `<div class="chat-bubble-in pb-bubble ${isUser ? "user" : "bot"}" style="animation-delay:0s">`;
    if (!isUser) {
      html += `<div class="pb-bubble-avatar" style="padding:0;overflow:hidden"><img src="/static/img/Avatar.png" alt="Vera" style="width:100%;height:100%;object-fit:cover;border-radius:50%"></div>`;
    }
    html += `<div class="pb-bubble-content">${msg.text.replace(/\n/g, "<br>")}<span class="pb-bubble-time">${getTimeStr()}</span></div></div>`;
    return html;
  }

  function renderTyping() {
    const p = platformColors[activeTab];
    return `<div class="pb-typing">
      <div style="width:32px;height:32px;border-radius:50%;overflow:hidden;flex-shrink:0">
        <img src="/static/img/Avatar.png" alt="Vera" style="width:100%;height:100%;object-fit:cover;border-radius:50%">
      </div>
      <div class="pb-typing-dots">●●●</div>
    </div>`;
  }

  function advanceChat() {
    const conv = conversations[activeTab];
    const container = document.getElementById("chat-messages");
    if (!container) return;

    if (chatStep < conv.length) {
      let html = "";
      for (let i = 0; i < chatStep; i++) {
        html += renderBubble(conv[i]);
      }
      if (chatStep < conv.length) {
        html += renderTyping();
      }
      container.innerHTML = html;
      container.scrollTop = container.scrollHeight;
      chatStep++;
      chatTimer = setTimeout(advanceChat, 900);
    } else {
      // Show all messages, no typing
      let html = "";
      for (let i = 0; i < conv.length; i++) {
        html += renderBubble(conv[i]);
      }
      container.innerHTML = html;
      container.scrollTop = container.scrollHeight;
    }
  }

  function switchTab(tab) {
    activeTab = tab;
    chatStep = 0;
    clearTimeout(chatTimer);

    // Update tab styles
    document.querySelectorAll(".pb-tab").forEach((btn) => {
      const t = btn.dataset.tab;
      const p = platformColors[t];
      if (t === tab) {
        btn.classList.add("active");
        btn.style.borderColor = p.color;
        btn.style.background = p.color;
        btn.style.color = "#fff";
        btn.style.boxShadow = `0 6px 20px ${p.color}40`;
      } else {
        btn.classList.remove("active");
        btn.style.borderColor = "#E5E7EB";
        btn.style.background = "#fff";
        btn.style.color = "#6B7280";
        btn.style.boxShadow = "none";
      }
    });

    // Update chat header
    const p = platformColors[tab];
    const header = document.getElementById("chat-header");
    if (header) header.style.background = `linear-gradient(135deg,${p.dark},${p.color})`;

    // Update platform icon in header
    const iconMap = { whatsapp: "fa-whatsapp", messenger: "fa-facebook-messenger", instagram: "fa-instagram" };
    const platformIcon = document.getElementById("chat-platform-icon");
    if (platformIcon) platformIcon.className = `fa-brands ${iconMap[tab]}`;

    // Clear and restart chat
    const container = document.getElementById("chat-messages");
    if (container) container.innerHTML = "";
    chatTimer = setTimeout(advanceChat, 500);
  }

  // Init tabs
  document.querySelectorAll(".pb-tab").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  // Start initial chat
  switchTab("whatsapp");

  /* ── Features toggle re-observe ───────────────────────────────── */
  var featuresExtra = document.getElementById("features-extra");
  if (featuresExtra) {
    var mo = new MutationObserver(function () {
      featuresExtra.querySelectorAll(".anim-card:not(.visible)").forEach(function (el) {
        observer.observe(el);
      });
    });
    mo.observe(featuresExtra, { attributes: true, attributeFilter: ["class"] });
  }

  /* ── FAQ accordion ─────────────────────────────────────────────── */
  document.querySelectorAll(".pb-faq-item").forEach((item) => {
    item.addEventListener("click", () => {
      const wasActive = item.classList.contains("active");
      document.querySelectorAll(".pb-faq-item").forEach((i) => i.classList.remove("active"));
      if (!wasActive) item.classList.add("active");
    });
  });

  /* ── Smooth scroll for nav links ──────────────────────────────── */
  document.querySelectorAll('.pb-header-nav a, a[href^="#"]').forEach(function (link) {
    link.addEventListener("click", function (e) {
      var href = this.getAttribute("href");
      if (href && href.startsWith("#")) {
        var target = document.querySelector(href);
        if (target) {
          e.preventDefault();
          target.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      }
    });
  });

  /* ── Sticky bar on scroll ──────────────────────────────────────── */
  const sticky = document.querySelector(".pb-sticky");
  const waFloat = document.querySelector(".pb-wa-float");
  const scrollTop = document.querySelector(".pb-scroll-top");

  window.addEventListener("scroll", () => {
    const show = window.scrollY > 700;
    if (sticky) sticky.classList.toggle("show", show);
    if (waFloat) waFloat.classList.toggle("sticky-active", show);
    if (scrollTop) scrollTop.classList.toggle("show", show);
    if (scrollTop) scrollTop.classList.toggle("sticky-active", show);
  });

  if (scrollTop) {
    scrollTop.addEventListener("click", () => {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  /* ── Video Demo (Ken Burns) ────────────────────────────────────── */
  const SLIDES = [
    { label: "Living comedor", photo: "https://images.unsplash.com/photo-1493809842364-78817add7ffb?w=700&q=85&fit=crop", detail: "Amplios ambientes con luz natural", kb: { ss: 1.15, es: 1.0, sx: -3, sy: -2, ex: 3, ey: 2 } },
    { label: "Cocina equipada", photo: "https://images.unsplash.com/photo-1556909114-f6e7ad7d3136?w=700&q=85&fit=crop", detail: "Mesada de granito · Alacenas nuevas", kb: { ss: 1.0, es: 1.18, sx: 4, sy: 0, ex: -2, ey: -3 } },
    { label: "Dormitorio principal", photo: "https://images.unsplash.com/photo-1540518614846-7eded433c457?w=700&q=85&fit=crop", detail: "Suite con walk-in closet", kb: { ss: 1.2, es: 1.05, sx: 2, sy: 3, ex: -4, ey: -1 } },
    { label: "Jardín y pileta", photo: "https://images.unsplash.com/photo-1564013799919-ab600027ffc6?w=700&q=85&fit=crop", detail: "Pileta + quincho · 350 m² totales", kb: { ss: 1.05, es: 1.2, sx: -4, sy: 1, ex: 2, ey: -3 } },
    { label: "Fachada", photo: "https://images.unsplash.com/photo-1570129477492-45c003edd2be?w=700&q=85&fit=crop", detail: "Frente reciclado · Cochera doble", kb: { ss: 1.1, es: 1.0, sx: 0, sy: -4, ex: 0, ey: 2 } },
  ];
  const SLIDE_DURATION = 3200;

  let videoPlaying = false;
  let videoDone = false;
  let currentSlide = 0;
  let videoProgress = 0;
  let slideInterval = null;
  let progressInterval = null;

  const phoneEl = document.getElementById("video-phone");
  const progressEl = document.getElementById("video-progress-bar");
  const progressWrap = document.getElementById("video-progress");
  const playBtn = document.getElementById("video-play-btn");

  function renderVideoSlide() {
    if (!phoneEl) return;
    const s = SLIDES[currentSlide];
    const kb = s.kb;
    phoneEl.innerHTML = `
      <div class="pb-video-notch"></div>
      <div class="kb-image" style="position:absolute;inset:-10%;background-image:url(${s.photo});background-size:cover;background-position:center;--kb-start-scale:${kb.ss};--kb-end-scale:${kb.es};--kb-start-x:${kb.sx}%;--kb-start-y:${kb.sy}%;--kb-end-x:${kb.ex}%;--kb-end-y:${kb.ey}%;--kb-duration:${SLIDE_DURATION}ms"></div>
      ${videoPlaying ? `<div class="pb-video-scanline" style="animation:scanline ${SLIDE_DURATION}ms linear infinite"></div>` : ""}
      <div class="pb-video-overlay"></div>
      <div class="pb-video-watermark">PropBot</div>
      <div class="text-overlay" style="position:absolute;bottom:0;left:0;right:0;padding:1.2rem 1rem 1.5rem;z-index:15">
        <div style="font-size:.72rem;color:rgba(255,255,255,.7);margin-bottom:3px;text-transform:uppercase;letter-spacing:.08em">${currentSlide + 1} / ${SLIDES.length}</div>
        <div style="font-size:1rem;font-weight:800;color:#fff;margin-bottom:2px">${s.label}</div>
        <div style="font-size:.8rem;color:rgba(255,255,255,.8)">${s.detail}</div>
        ${currentSlide === 0 ? '<div style="margin-top:8px;font-size:1.1rem;font-weight:900;color:#D97706">Casa con pileta — USD 220.000</div>' : ""}
      </div>`;
  }

  function renderVideoDone() {
    if (!phoneEl) return;
    phoneEl.innerHTML = `
      <div class="pb-video-notch"></div>
      <div style="position:absolute;inset:0;background:rgba(0,0,0,.75);display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:20;gap:8px">
        <div style="font-size:2.5rem">✅</div>
        <div style="color:#fff;font-weight:700;font-size:.95rem">Video listo!</div>
        <div style="color:rgba(255,255,255,.65);font-size:.78rem">Listo para publicar</div>
        <button onclick="resetVideo()" style="margin-top:8px;background:#D97706;color:#fff;border:none;border-radius:999px;padding:6px 18px;font-size:.8rem;font-weight:700;cursor:pointer">Ver de nuevo</button>
      </div>`;
  }

  function renderVideoIdle() {
    if (!phoneEl) return;
    let thumbs = SLIDES.map((s) => `<div style="width:28px;height:28px;border-radius:6px;background-image:url(${s.photo});background-size:cover;background-position:center;border:1px solid rgba(255,255,255,.15)"></div>`).join("");
    phoneEl.innerHTML = `
      <div class="pb-video-notch"></div>
      <div style="position:absolute;inset:0;background:linear-gradient(155deg,#1a2a1a 0%,#0d1f0d 100%);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px">
        <div style="font-size:3.5rem">🏠</div>
        <div style="color:#fff;font-weight:700;font-size:1rem;text-align:center;padding:0 1rem">Casa con pileta y quincho</div>
        <div style="color:rgba(255,255,255,.55);font-size:.8rem">5 fotos · generando video...</div>
        <div style="display:flex;gap:4px;margin-top:4px">${thumbs}</div>
      </div>`;
  }

  function startVideo() {
    videoPlaying = true;
    videoDone = false;
    currentSlide = 0;
    videoProgress = 0;
    if (playBtn) playBtn.style.display = "none";
    if (progressWrap) progressWrap.style.display = "block";

    renderVideoSlide();

    slideInterval = setInterval(() => {
      currentSlide++;
      if (currentSlide >= SLIDES.length) {
        clearInterval(slideInterval);
        clearInterval(progressInterval);
        videoPlaying = false;
        videoDone = true;
        videoProgress = 100;
        if (progressEl) progressEl.style.width = "100%";
        renderVideoDone();
        return;
      }
      renderVideoSlide();
    }, SLIDE_DURATION);

    const totalDuration = SLIDES.length * SLIDE_DURATION;
    const tick = 80;
    let elapsed = 0;
    progressInterval = setInterval(() => {
      elapsed += tick;
      videoProgress = Math.min((elapsed / totalDuration) * 100, 99);
      if (progressEl) progressEl.style.width = videoProgress + "%";
    }, tick);
  }

  window.resetVideo = function () {
    clearInterval(slideInterval);
    clearInterval(progressInterval);
    videoPlaying = false;
    videoDone = false;
    currentSlide = 0;
    videoProgress = 0;
    if (playBtn) playBtn.style.display = "flex";
    if (progressWrap) progressWrap.style.display = "none";
    renderVideoIdle();
  };

  if (playBtn) {
    playBtn.addEventListener("click", startVideo);
  }

  // Init video idle state
  renderVideoIdle();
  if (progressWrap) progressWrap.style.display = "none";

  /* ── Calendly ──────────────────────────────────────────────────── */
  const calendlyContainer = document.getElementById("calendly-widget");
  const CALENDLY_URL = "https://calendly.com/sanchezgcandelaria/15min?hide_event_type_details=1&hide_gdpr_banner=1&text_color=0f172a&primary_color=d97706";

  function initCalendly() {
    if (window.Calendly && calendlyContainer) {
      window.Calendly.initInlineWidget({
        url: CALENDLY_URL,
        parentElement: calendlyContainer,
      });
      calendlyContainer.style.minWidth = "100%";
      calendlyContainer.style.height = "630px";
      // Remove loader
      const loader = document.getElementById("calendly-loader");
      if (loader) loader.style.display = "none";
    }
  }

  // Calendly script loaded callback
  window.onCalendlyLoad = function () {
    initCalendly();
  };

  // If already loaded
  if (window.Calendly) initCalendly();
});
