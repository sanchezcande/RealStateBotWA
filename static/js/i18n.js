/* ===== Internationalization (ES / EN) ===== */

const I18N = {
  es: {
    // -- Sidebar / Nav --
    dashboard: "Dashboard",
    conversations: "Conversaciones",
    leads: "Leads",
    visits: "Agenda",
    media_studio: "Media Studio",
    logout: "Cerrar sesion",

    // -- Dashboard index --
    last_n_days: "Últimos {days} días",
    n_days: "{n} días",
    vs_prev_period: "vs. período anterior",
    of_total: "del total",
    conversations_kpi: "Conversaciones",
    qualified_leads: "Leads calificados",
    scheduled_visits: "Citas agendadas",
    escalated_human: "Escaladas a humano",
    export_csv: "Exportar CSV",
    print_pdf: "Imprimir / PDF",
    new_conv_per_day: "Conversaciones nuevas por día",
    no_data_yet: "Sin datos todavía",
    peak_hours: "Horarios pico",
    operation_type: "Tipo de operación",
    most_requested_props: "Propiedades más solicitadas",
    no_visits_yet: "Sin citas registradas todavía",
    query_resolution: "Resolución de consultas",
    contact_channels: "Canales de contacto",
    lead_quality: "Calidad de leads",
    visit_singular: "cita",
    visit_plural: "citas",
    conv_chart_label: "Conversaciones",

    // -- Data labels (from backend) --
    "Venta": "Venta",
    "Alquiler": "Alquiler",
    "Resueltas por bot": "Resueltas por bot",
    "Escaladas a humano": "Escaladas a humano",
    "Leads calificados": "Leads calificados",
    "Interaccion sin calificar": "Interaccion sin calificar",
    "Sin interaccion": "Sin interaccion",

    // -- Conversations page --
    all_channels: "Todos los canales",
    all: "Todos",
    leads_only: "Solo leads",
    with_visit: "Con cita",
    search_placeholder: "Buscar por nombre, telefono o mensaje...",
    no_conversations: "Sin conversaciones",
    lead: "Lead",
    select_conversation: "Selecciona una conversacion",
    qualified_lead: "Lead calificado",
    operation_label: "Operacion:",
    type_label: "Tipo:",
    budget_label: "Presupuesto:",
    timeline_label: "Timeline:",
    previous: "Anterior",
    next: "Siguiente",
    type_reply: "Escribi tu mensaje...",
    bot_paused: "Bot desactivado",
    bot_active: "Bot activado",
    agent_label: "Agente",

    // -- Leads page --
    all_operations: "Todas las operaciones",
    rental: "Alquiler",
    purchase: "Compra",
    most_recent: "Mas reciente",
    oldest: "Mas antiguo",
    name: "Nombre",
    phone: "Telefono",
    operation: "Operacion",
    type: "Tipo",
    budget: "Presupuesto",
    timeline: "Timeline",
    channel: "Canal",
    msgs: "Msgs",
    visits_th: "Agenda",
    last_contact: "Ultimo contacto",
    no_leads: "Sin leads registrados",

    // -- Visits page --
    calendar: "Calendario",
    list: "Lista",
    confirmed_f: "Confirmadas",
    cancelled_f: "Canceladas",
    date: "Fecha",
    time: "Hora",
    property: "Propiedad",
    client: "Cliente",
    address: "Direccion",
    status: "Estado",
    confirmed: "Confirmada",
    cancelled: "Cancelada",
    completed: "Completada",
    no_visits_registered: "Sin citas registradas",
    mon: "Lun", tue: "Mar", wed: "Mie", thu: "Jue",
    fri: "Vie", sat: "Sab", sun: "Dom",
    months: ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
             "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"],

    // -- Media Studio --
    media_subtitle: "Genera videos y fotos con IA para tus propiedades",
    videos_this_month: "Videos este mes:",
    remaining: "restantes",
    buy_videos: "+ Comprar videos",
    buy_additional: "Comprar videos adicionales",
    each_video_costs: "Cada video adicional cuesta",
    added_to_limit: "Se agregan al limite de este mes.",
    quantity: "Cantidad:",
    total: "Total:",
    confirm_purchase: "Confirmar compra",
    cancel: "Cancelar",
    generate_video: "Generar Video",
    generate_image: "Generar Imagen",
    history: "Historial",
    drag_photos: "Arrastra fotos aca o hace click para subir",
    file_types: "JPG, PNG o WebP (max 10 MB cada una)",
    uploaded_photos: "Fotos subidas",
    selected: "seleccionadas",
    deselect_all: "Deseleccionar todas",
    select_all: "Seleccionar todas",
    delete: "Eliminar",
    generate_video_tour: "Generar Video Tour",
    video_smooth_desc: "Se generara un video con transiciones suaves entre las",
    photos_selected: "fotos seleccionadas.",
    single_photo_desc: "Con 1 sola foto se genera una animacion cinematica de ~8 segundos.",
    multi_photo_desc: "Se genera un clip de ~5 seg por cada foto ({count} fotos = ~{secs} seg total) y se unen en un video.",
    multi_photo_capped: "Se seleccionan 5 fotos representativas de las {count} elegidas para generar el video (max 5 clips).",
    property_name_opt: "Nombre de la propiedad (opcional)",
    custom_prompt_opt: "Prompt personalizado (opcional) — ej: 'Tour elegante por departamento moderno en Palermo, luz natural'",
    video_format: "Formato de video",
    video_format_help: "Vertical funciona mejor para reels, stories y WhatsApp. Horizontal sirve para web o YouTube.",
    format_vertical: "Vertical 9:16",
    format_horizontal: "Horizontal 16:9",
    generating: "Generando...",
    no_videos_available: "Sin videos disponibles —",
    buy_more: "comprar mas",
    video_generated: "Video generado!",
    download_video: "Descargar video",
    generate_ai_image: "Generar Imagen con IA",
    ai_image_desc: "Genera imagenes para listings usando Imagen 3. Ideal para crear renders, home staging virtual o imagenes de marketing.",
    image_prompt_placeholder: "Descripcion de la imagen — ej: 'Living moderno con vista al rio, decoracion minimalista, luz del atardecer, foto profesional de arquitectura'",
    image_generated: "Imagen generada!",
    download_image: "Descargar imagen",
    no_generations: "Sin generaciones todavia",
    video_tour: "Video Tour",
    image: "Imagen",
    no_name: "Sin nombre",
    queued: "En cola",
    processing: "Procesando",
    completed_status: "Completado",
    error: "Error",
    download: "Descargar",
    delete_photo_confirm: "Eliminar esta foto?",
    sending: "Enviando...",
    write_description: "Escribi una descripcion",
    network_error: "Error de red",
    videos_added: "Se agregaron {count} video(s). Ahora tenes {remaining} disponibles.",
    video_word: "video",
    videos_word: "videos",

    // -- Login --
    login_title: "Iniciar sesion",
    login_subtitle: "Ingresa para acceder al dashboard",
    password: "Contrasena",
    password_placeholder: "Tu contrasena",
    or: "o",
    access_token: "Token de acceso",
    token_placeholder: "Token del dashboard",
    sign_in: "Ingresar",
    invalid_credentials: "Credenciales incorrectas",

    // -- Upgrade --
    dashboard_unavailable: "Dashboard no disponible",
    upgrade_msg: "El plan <strong>Starter</strong> no incluye acceso al dashboard.<br>Comunicate con tu asesor para conocer los planes Pro y Premium.",

    // -- Time ago --
    time_now: "ahora",
    time_min: "min",
    time_h: "h",
    time_d: "d",
  },

  en: {
    // -- Sidebar / Nav --
    dashboard: "Dashboard",
    conversations: "Conversations",
    leads: "Leads",
    visits: "Schedule",
    media_studio: "Media Studio",
    logout: "Log out",

    // -- Dashboard index --
    last_n_days: "Last {days} days",
    n_days: "{n} days",
    vs_prev_period: "vs. previous period",
    of_total: "of total",
    conversations_kpi: "Conversations",
    qualified_leads: "Qualified leads",
    scheduled_visits: "Scheduled appointments",
    escalated_human: "Escalated to human",
    export_csv: "Export CSV",
    print_pdf: "Print / PDF",
    new_conv_per_day: "New conversations per day",
    no_data_yet: "No data yet",
    peak_hours: "Peak hours",
    operation_type: "Operation type",
    most_requested_props: "Most requested properties",
    no_visits_yet: "No appointments recorded yet",
    query_resolution: "Query resolution",
    contact_channels: "Contact channels",
    lead_quality: "Lead quality",
    visit_singular: "appointment",
    visit_plural: "appointments",
    conv_chart_label: "Conversations",

    // -- Data labels (from backend) --
    "Venta": "Sale",
    "Alquiler": "Rental",
    "Resueltas por bot": "Resolved by bot",
    "Escaladas a humano": "Escalated to human",
    "Leads calificados": "Qualified leads",
    "Interaccion sin calificar": "Unqualified interaction",
    "Sin interaccion": "No interaction",

    // -- Conversations page --
    all_channels: "All channels",
    all: "All",
    leads_only: "Leads only",
    with_visit: "With appointment",
    search_placeholder: "Search by name, phone or message...",
    no_conversations: "No conversations",
    lead: "Lead",
    select_conversation: "Select a conversation",
    qualified_lead: "Qualified lead",
    operation_label: "Operation:",
    type_label: "Type:",
    budget_label: "Budget:",
    timeline_label: "Timeline:",
    previous: "Previous",
    next: "Next",
    type_reply: "Type your message...",
    bot_paused: "Bot disabled",
    bot_active: "Bot enabled",
    agent_label: "Agent",

    // -- Leads page --
    all_operations: "All operations",
    rental: "Rental",
    purchase: "Purchase",
    most_recent: "Most recent",
    oldest: "Oldest",
    name: "Name",
    phone: "Phone",
    operation: "Operation",
    type: "Type",
    budget: "Budget",
    timeline: "Timeline",
    channel: "Channel",
    msgs: "Msgs",
    visits_th: "Schedule",
    last_contact: "Last contact",
    no_leads: "No leads registered",

    // -- Visits page --
    calendar: "Calendar",
    list: "List",
    confirmed_f: "Confirmed",
    cancelled_f: "Cancelled",
    date: "Date",
    time: "Time",
    property: "Property",
    client: "Client",
    address: "Address",
    status: "Status",
    confirmed: "Confirmed",
    cancelled: "Cancelled",
    completed: "Completed",
    no_visits_registered: "No appointments registered",
    mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu",
    fri: "Fri", sat: "Sat", sun: "Sun",
    months: ["January","February","March","April","May","June",
             "July","August","September","October","November","December"],

    // -- Media Studio --
    media_subtitle: "Generate AI videos and photos for your properties",
    videos_this_month: "Videos this month:",
    remaining: "remaining",
    buy_videos: "+ Buy videos",
    buy_additional: "Buy additional videos",
    each_video_costs: "Each additional video costs",
    added_to_limit: "Added to this month's limit.",
    quantity: "Quantity:",
    total: "Total:",
    confirm_purchase: "Confirm purchase",
    cancel: "Cancel",
    generate_video: "Generate Video",
    generate_image: "Generate Image",
    history: "History",
    drag_photos: "Drag photos here or click to upload",
    file_types: "JPG, PNG or WebP (max 10 MB each)",
    uploaded_photos: "Uploaded photos",
    selected: "selected",
    deselect_all: "Deselect all",
    select_all: "Select all",
    delete: "Delete",
    generate_video_tour: "Generate Video Tour",
    video_smooth_desc: "A video with smooth transitions between the",
    photos_selected: "selected photos will be generated.",
    single_photo_desc: "With 1 photo, a cinematic animation of ~8 seconds is generated.",
    multi_photo_desc: "A ~5 sec clip is generated per photo ({count} photos = ~{secs} sec total) and joined into one video.",
    multi_photo_capped: "5 representative photos are selected from the {count} chosen to generate the video (max 5 clips).",
    property_name_opt: "Property name (optional)",
    custom_prompt_opt: "Custom prompt (optional) — e.g.: 'Elegant tour of modern apartment in Palermo, natural light'",
    video_format: "Video format",
    video_format_help: "Vertical works better for reels, stories, and WhatsApp. Horizontal is better for web or YouTube.",
    format_vertical: "Vertical 9:16",
    format_horizontal: "Horizontal 16:9",
    generating: "Generating...",
    no_videos_available: "No videos available —",
    buy_more: "buy more",
    video_generated: "Video generated!",
    download_video: "Download video",
    generate_ai_image: "Generate Image with AI",
    ai_image_desc: "Generate images for listings using Imagen 3. Ideal for renders, virtual home staging or marketing images.",
    image_prompt_placeholder: "Image description — e.g.: 'Modern living room with river view, minimalist decor, sunset light, professional architecture photo'",
    image_generated: "Image generated!",
    download_image: "Download image",
    no_generations: "No generations yet",
    video_tour: "Video Tour",
    image: "Image",
    no_name: "No name",
    queued: "Queued",
    processing: "Processing",
    completed_status: "Completed",
    error: "Error",
    download: "Download",
    delete_photo_confirm: "Delete this photo?",
    sending: "Sending...",
    write_description: "Write a description",
    network_error: "Network error",
    videos_added: "Added {count} video(s). You now have {remaining} available.",
    video_word: "video",
    videos_word: "videos",

    // -- Login --
    login_title: "Log in",
    login_subtitle: "Sign in to access the dashboard",
    password: "Password",
    password_placeholder: "Your password",
    or: "or",
    access_token: "Access token",
    token_placeholder: "Dashboard token",
    sign_in: "Sign in",
    invalid_credentials: "Invalid credentials",

    // -- Upgrade --
    dashboard_unavailable: "Dashboard unavailable",
    upgrade_msg: "The <strong>Starter</strong> plan does not include dashboard access.<br>Contact your advisor to learn about the Pro and Premium plans.",

    // -- Time ago --
    time_now: "now",
    time_min: "min",
    time_h: "h",
    time_d: "d",
  },
};

/* Detect language: localStorage > browser > default 'es' */
let _currentLang = localStorage.getItem("dash_lang")
  || (navigator.language.startsWith("en") ? "en" : "es");

function getLang() { return _currentLang; }

function setLang(lang) {
  _currentLang = lang;
  localStorage.setItem("dash_lang", lang);
  applyTranslations();
}

function toggleLang() {
  setLang(_currentLang === "es" ? "en" : "es");
}

/**
 * Translate a key, with optional parameter substitution.
 * t("last_n_days", {days: 30}) → "Last 30 days"
 */
function t(key, params) {
  let s = (I18N[_currentLang] && I18N[_currentLang][key])
       || (I18N.es && I18N.es[key])
       || key;
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      s = s.replaceAll("{" + k + "}", v);
    }
  }
  return s;
}

/** Translate a backend data label (pass-through if no mapping). */
function tData(label) {
  return (I18N[_currentLang] && I18N[_currentLang][label]) || label;
}

/** Apply translations to all elements with data-i18n attribute. */
function applyTranslations() {
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const key = el.getAttribute("data-i18n");
    const paramsAttr = el.getAttribute("data-i18n-p");
    const params = paramsAttr ? JSON.parse(paramsAttr) : null;
    const mode = el.getAttribute("data-i18n-mode");
    if (mode === "html") {
      el.innerHTML = t(key, params);
    } else {
      el.textContent = t(key, params);
    }
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
    el.placeholder = t(el.getAttribute("data-i18n-placeholder"));
  });
  // Update lang toggle button
  const btn = document.getElementById("langToggle");
  if (btn) btn.textContent = _currentLang === "es" ? "EN" : "ES";
  // Update html lang
  document.documentElement.lang = _currentLang === "en" ? "en" : "es";
}

/* Auto-apply on DOM ready */
document.addEventListener("DOMContentLoaded", applyTranslations);
