# PropBot - Guía de Marca / Brand Guidelines

---

## 1. Logo

### Versiones disponibles

| Archivo | Resolución | Uso recomendado |
|---------|-----------|-----------------|
| `propbot-logo-1024.png` | 1024x1024 | Impresiones, presentaciones, material gráfico |
| `propbot-logo-512.png` | 512x512 | Perfil de Meta, WhatsApp Business, redes sociales |
| `propbot-logo-180.png` | 180x180 | Favicon, thumbnails, íconos pequeños |
| `propbot-icon.svg` | Vectorial | Escalado ilimitado, uso editorial |

### Concepto
Casa + burbuja de chat integradas. Representa la unión entre el mundo inmobiliario y la comunicación automatizada con IA. El diseño es limpio, geométrico y moderno, con profundidad sutil (sombras suaves).

### Reglas de uso
- **Zona de seguridad:** Mantener un margen mínimo equivalente al 15% del ancho del logo a cada lado.
- **Tamaño mínimo:** 32x32px (digital), 10mm (impreso).
- **NO** distorsionar, rotar, agregar bordes, cambiar colores, ni aplicar efectos sobre el logo.
- Sobre fondos oscuros: usar el logo tal cual (ya tiene contraste propio).
- Sobre fondos claros: usar el logo tal cual (el gradiente naranja resalta).

### Wordmark (texto)
- **"Prop"** en blanco `#FFFFFF` (sobre fondo oscuro) o azul oscuro `#0B1A3E` (sobre fondo claro)
- **"Bot"** en amarillo dorado `#FCD34D` (sobre fondo oscuro) o naranja `#D97706` (sobre fondo claro)
- Sin espacio entre "Prop" y "Bot"

---

## 2. Paleta de Colores

### Primarios (Marca)

| Nombre | Hex | RGB | Uso |
|--------|-----|-----|-----|
| **Amber Oscuro** | `#92400E` | 146, 64, 14 | Inicio del gradiente, acentos profundos |
| **Naranja PropBot** | `#D97706` | 217, 119, 6 | **Color principal de marca**, CTAs, acentos |
| **Naranja Claro** | `#F59E0B` | 245, 158, 11 | Final del gradiente, botones hover |
| **Dorado** | `#FCD34D` | 252, 211, 77 | Texto "Bot", badges premium, highlights |

### Neutros / Fondos

| Nombre | Hex | RGB | Uso |
|--------|-----|-----|-----|
| **Azul Noche** | `#0B1A3E` | 11, 26, 62 | Fondo principal (hero, sidebar, headers) |
| **Azul Medio** | `#0D2252` | 13, 34, 82 | Gradiente de fondos oscuros |
| **Blanco** | `#FFFFFF` | 255, 255, 255 | Fondos claros, texto sobre oscuro |
| **Gris Texto** | `#2B2B2B` | 43, 43, 43 | Texto body sobre fondo claro |

### Plataformas

| Plataforma | Color | Hex |
|-----------|-------|-----|
| WhatsApp | Verde | `#25D366` |
| WhatsApp Dark | Verde oscuro | `#128C7E` |
| Messenger | Azul | `#0084FF` |
| Instagram | Rosa | `#E1306C` |

### Gradientes

| Nombre | CSS | Uso |
|--------|-----|-----|
| **Logo / Ícono** | `linear-gradient(145deg, #92400E, #D97706)` | Logo, íconos de marca |
| **CTA Button** | `linear-gradient(135deg, #D97706, #F59E0B)` | Botones principales |
| **Background Dark** | `linear-gradient(180deg, #0B1A3E, #0D2252, #0B1A3E)` | Fondos de secciones |

---

## 3. Tipografía

### Font principal
**Inter** — Google Fonts
`https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900`

| Peso | Uso |
|------|-----|
| 400 Regular | Texto body, párrafos |
| 500 Medium | Labels, captions |
| 600 Semibold | Subtítulos, nav links |
| 700 Bold | Títulos de sección, CTAs |
| 800 Extra Bold | Headings principales |
| 900 Black | Logo wordmark "PropBot" |

### Jerarquía de tamaños

| Elemento | Tamaño | Peso |
|----------|--------|------|
| Logo text | 1.25rem (20px) | 900 |
| Hero título | clamp(1.7rem, 3vw, 2.2rem) | 800 |
| Sección título | clamp(1.7rem, 3vw, 2.2rem) | 800 |
| Body | 1rem (16px) | 400 |
| Small / caption | 0.78-0.9rem | 500-600 |

### Letter spacing
- Logo: `-0.02em` (tracking apretado)
- Badges: `0.08em` (tracking abierto, uppercase)

---

## 4. Estilo Visual

### Personalidad de marca
- **Profesional pero accesible** — no corporativo frío, sino cálido y confiable
- **Tecnológico pero simple** — IA sin jerga técnica, automatización sin complejidad
- **Latinoamericano** — tono cercano, voseo/tuteo, lenguaje directo

### Bordes y radios

| Elemento | Border radius |
|----------|--------------|
| Logo ícono | 10-13px (~23-30%) |
| Botones CTA | 999px (pill) |
| Cards | 14-16px |
| Inputs | 12px |
| Badges | 99px (pill) |
| Modales | 20-24px |

### Sombras

| Tipo | CSS |
|------|-----|
| Logo | `0 4px 18px rgba(217,119,6, 0.5)` |
| CTA hover | `0 4px 14px rgba(217,119,6, 0.35)` |
| Card default | `0 2px 12px rgba(0,0,0, 0.08)` |
| Card hover | `0 16px 40px rgba(0,0,0, 0.13)` |
| Pricing hover | `0 20px 50px rgba(217,119,6, 0.25)` |

### Animaciones
- Transiciones suaves: `0.15s - 0.3s ease`
- Entrada de elementos: `fadeInUp 0.6s ease`
- Hover en cards: `translateY(-6px)` con sombra expandida
- Texto shimmer en hero: gradiente animado 3s
- No usar animaciones agresivas o que distraigan

---

## 5. Tono y Voz

### Principios
1. **Directo** — ir al grano, sin rodeos
2. **Confiable** — datos concretos, sin promesas infladas
3. **Cercano** — hablar como un colega del rubro, no como una corporación
4. **Práctico** — enfocarse en beneficios reales, no features técnicas

### Ejemplos

| Sí | No |
|----|-----|
| "Respondé consultas 24/7 sin levantar el teléfono" | "Solución omnicanal de customer engagement con AI" |
| "Conectás tu planilla y listo" | "Integración seamless con múltiples data sources" |
| "Tu bot lee tu stock y contesta solo" | "Motor de NLP con procesamiento de datos en tiempo real" |

### Palabras clave de marca
Automatización, inmobiliaria, WhatsApp, bot, consultas, propiedades, 24/7, IA, sin código, Google Sheets

---

## 6. Aplicaciones

### Foto de perfil (Meta / WhatsApp Business)
- Usar `propbot-logo-512.png`
- El logo se ve bien en recorte circular (el ícono queda centrado)

### Redes sociales
- Posts: fondo `#0B1A3E` con logo y texto en colores de marca
- Stories: gradiente naranja como acento, texto Inter Bold blanco

### Email
- Header: logo 180px + wordmark sobre fondo oscuro
- CTA buttons: gradiente naranja, texto blanco, border-radius pill

### Presentaciones / Decks
- Fondo principal: blanco o `#0B1A3E`
- Acentos: naranja `#D97706`
- Títulos: Inter 800, color azul noche o blanco según fondo

---

## 7. Assets

Todos los archivos están en `/static/img/`:

```
static/img/
├── propbot-logo-1024.png   ← Alta resolución
├── propbot-logo-512.png    ← Redes sociales / perfil
├── propbot-logo-180.png    ← Favicon / web
├── propbot-icon.svg        ← Vectorial (legacy)
└── propbot-logo-full.svg   ← Logo + wordmark vectorial (legacy)
```
