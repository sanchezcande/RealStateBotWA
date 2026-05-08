# Onboarding de Clientes: Vera Bot

## Para que sirve este doc

Guia paso a paso para dar de alta un cliente nuevo con Vera en WhatsApp, Facebook Messenger e Instagram Direct. Pensada para que cualquiera pueda seguirla sin saber nada tecnico.

---

## RESUMEN RAPIDO

```
1. Crear/configurar la Meta App
2. Configurar WhatsApp (numero, token, webhook)
3. Configurar Facebook + Instagram (si el plan lo incluye)
4. Cargar variables en Railway
5. Verificar que todo funcione
```

---

## Que necesitas ANTES de empezar

### Del cliente:
- Numero de WhatsApp del negocio
- Numero personal del agente (para notificaciones)
- Google Sheet con propiedades (o lo creas vos)
- Email de contacto
- Horario de atencion de la inmobiliaria (ej: "lunes a viernes de 9 a 18")
- Pagina de Facebook del negocio (si quiere FB/IG)
- Cuenta de Instagram del negocio conectada a la pagina de FB (si quiere IG)

### Tuyas:
- Acceso a Meta App Dashboard (`developers.facebook.com`)
- Acceso a Meta Business Suite (`business.facebook.com`)
- API Key de DeepSeek
- Acceso al proyecto en Railway

---

## PARTE 1: META APP

### 1.1 Crear o usar la app

URL: `https://developers.facebook.com/apps/`

- Si ya existe la app (ej: "RealEstate WA Bot"), usala
- Si necesitas una nueva: Crear app → tipo "Business" → agregar producto "WhatsApp"

### 1.2 App en modo LIVE

- Barra lateral → **Publicar**
- Debe decir **"Publicada"**
- Si dice "En desarrollo" → publicarla
- Para publicar necesitas: URL de politica de privacidad + email de contacto

### 1.3 Anotar App ID y App Secret

- Ir a **Configuracion de la app** → **Basica**
- Copiar el **Identificador de la app** (App ID)
- Click en **Mostrar** en "Clave secreta de la app" → copiar el **App Secret**
- Guardar ambos, los vas a necesitar

---

## PARTE 2: WHATSAPP

### 2.1 Obtener el WABA ID

- En el dashboard de Meta → **WhatsApp** → **API Setup**
- El WABA ID aparece arriba o en la URL
- Anotarlo (ej: `1921482308505030`)

### 2.2 Agregar y verificar el numero

- En **WhatsApp** → **API Setup** → agregar el numero del cliente
- Meta manda codigo por SMS o llamada
- Ingresar el codigo para verificar

### 2.3 Anotar el Phone Number ID

- Despues de verificar, Meta muestra el **Phone Number ID**
- Ejemplo: `1104919886028807`
- NO es lo mismo que el numero de telefono, es un ID interno de Meta

### 2.4 Crear System User y Token

1. Ir a **Meta Business Suite** → **Configuracion del negocio** → **Usuarios** → **Usuarios del sistema**
2. Crear un System User tipo **Admin**
3. Asignarle el WABA como activo
4. Click en **Generar token**
5. Seleccionar la app
6. Marcar estos permisos:
   - `whatsapp_business_management`
   - `whatsapp_business_messaging`
7. Generar → copiar el token
8. Este es tu `WHATSAPP_TOKEN`

> IMPORTANTE: el token debe ser permanente (no expira). Si te da opcion de duracion, elegir "Never expires".

### 2.5 Configurar Webhook de WhatsApp

**En el dashboard de Meta:**
- Ir a **WhatsApp** → **Configuracion** (o **Configuration**)
- Callback URL: `https://TUDOMINIO/webhook`
- Verify Token: un string que vos elegis (ej: `opengatehub123`)
- Click en **"Verificar y guardar"**

### 2.6 !!! PASO CRITICO: Suscribir el field "messages" !!!

> ESTO ES LO MAS IMPORTANTE DE TODO EL ONBOARDING.
> Sin esto, Meta tiene la URL del webhook pero no sabe que mandar.
> El bot NO va a recibir mensajes si falta este paso.

**Opcion A — Desde el dashboard (a veces no funciona bien):**
- En la seccion de Webhook, click en **"Administrar"** o **"Manage"**
- Marcar la casilla **"messages"**
- Guardar

**Opcion B — Por API (RECOMENDADO, siempre funciona):**

```bash
# Reemplazar APP_ID, APP_SECRET, TU_DOMINIO, y TU_VERIFY_TOKEN
APP_TOKEN="APP_ID|APP_SECRET"

curl -X POST "https://graph.facebook.com/v21.0/APP_ID/subscriptions" \
  -d "object=whatsapp_business_account" \
  -d "callback_url=https://TU_DOMINIO/webhook" \
  -d "verify_token=TU_VERIFY_TOKEN" \
  -d "fields=messages" \
  -d "access_token=$APP_TOKEN"
```

Tiene que devolver: `{"success": true}`

**Para confirmar que quedo bien:**

```bash
curl "https://graph.facebook.com/v21.0/APP_ID/subscriptions?access_token=$APP_TOKEN"
```

Buscar esto en la respuesta:

```json
{
  "object": "whatsapp_business_account",
  "callback_url": "https://TU_DOMINIO/webhook",
  "active": true,
  "fields": [{"name": "messages"}]
}
```

> Si `"fields"` esta vacio o no aparece `"messages"` → EL BOT NO VA A FUNCIONAR. Repetir el paso.

### 2.7 Suscribir la app al WABA

```bash
curl -X POST "https://graph.facebook.com/v21.0/WABA_ID/subscribed_apps" \
  -H "Authorization: Bearer WHATSAPP_TOKEN"
```

Tiene que devolver: `{"success": true}`

### 2.8 Registrar el numero en Cloud API

```bash
curl -X POST "https://graph.facebook.com/v21.0/PHONE_NUMBER_ID/register" \
  -H "Authorization: Bearer WHATSAPP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messaging_product": "whatsapp", "pin": "123456"}'
```

Tiene que devolver: `{"success": true}`

---

## PARTE 3: FACEBOOK MESSENGER + INSTAGRAM DIRECT

> Solo disponible en plan **pro** o **premium**. El plan **starter** ignora mensajes de FB/IG.

### 3.1 Que necesitas

- Una **Pagina de Facebook** del negocio
- Para Instagram: la cuenta de IG tiene que estar **conectada a la Pagina de Facebook** como cuenta de Instagram Business

### 3.2 Agregar productos en la Meta App

En el dashboard de Meta de la app:
1. Click en **Agregar producto**
2. Agregar **Messenger**
3. Agregar **Instagram** (si el cliente quiere IG)

### 3.3 Generar el Page Access Token

1. Ir a **Messenger** → **Configuracion** (o **Settings**)
2. En "Access Tokens", buscar la pagina del cliente
3. Click en **Generar token**
4. Copiar el token
5. Este es tu `PAGE_ACCESS_TOKEN`

> Este token sirve para Facebook Messenger Y para Instagram Direct (siempre y cuando la cuenta de IG este conectada a la pagina).

### 3.4 Configurar Webhook de Facebook/Instagram

**En el dashboard de Meta:**
- Ir a **Messenger** → **Configuracion** → seccion **Webhooks**
- Callback URL: `https://TU_DOMINIO/webhook/meta`
- Verify Token: el mismo que usaste para WhatsApp
- Click en **"Verificar y guardar"**

> ATENCION: la URL de FB/IG es `/webhook/meta`, NO `/webhook` (esa es solo para WhatsApp).

### 3.5 Suscribir webhook fields de Facebook

**Desde el dashboard:**
- En Messenger → Webhooks → click en **"Administrar"**
- Marcar: **`messages`** y **`messaging_postbacks`**
- Guardar

**O por API:**

```bash
APP_TOKEN="APP_ID|APP_SECRET"

curl -X POST "https://graph.facebook.com/v21.0/APP_ID/subscriptions" \
  -d "object=page" \
  -d "callback_url=https://TU_DOMINIO/webhook/meta" \
  -d "verify_token=TU_VERIFY_TOKEN" \
  -d "fields=messages,messaging_postbacks" \
  -d "access_token=$APP_TOKEN"
```

### 3.6 Suscribir la pagina al webhook

Esto le dice a Meta "manda los webhooks de ESTA pagina":

```bash
curl -X POST "https://graph.facebook.com/v21.0/PAGE_ID/subscribed_apps" \
  -d "subscribed_fields=messages,messaging_postbacks" \
  -d "access_token=PAGE_ACCESS_TOKEN"
```

### 3.7 Instagram (si aplica)

Si la cuenta de IG esta conectada a la pagina de FB, los mensajes de IG llegan por el mismo webhook (`/webhook/meta`). El bot los detecta automaticamente por el campo `"object": "instagram"`.

No hace falta configurar un webhook separado para IG, pero si asegurarte de que:
- La cuenta de IG sea **Business** o **Creator**
- Este **conectada** a la pagina de Facebook
- En el dashboard de Meta, en **Instagram** → **Configuracion**, la pagina este asociada

---

## PARTE 4: VARIABLES DE ENTORNO EN RAILWAY

### Obligatorias (WhatsApp):

```env
WHATSAPP_TOKEN=token_del_system_user
PHONE_NUMBER_ID=id_del_numero_de_telefono
VERIFY_TOKEN=el_verify_token_del_webhook
NOTIFY_NUMBER=numero_del_agente_para_notificaciones
DEEPSEEK_API_KEY=clave_de_deepseek
```

### Para Facebook/Instagram (si el plan lo incluye):

```env
PAGE_ACCESS_TOKEN=token_de_la_pagina_de_facebook
DASHBOARD_PLAN=pro          # o "premium" — "starter" no incluye FB/IG
```

### Opcionales pero recomendadas:

```env
GOOGLE_SHEET_ID=id_de_la_sheet_con_propiedades
GOOGLE_CREDENTIALS_JSON=credenciales_de_service_account_en_json
FOLLOWUP_ENABLED=true
FOLLOWUP_DAYS=3
DASHBOARD_TOKEN=token_para_acceder_al_dashboard
DASHBOARD_ADMIN_PASSWORD=password_del_dashboard
DASHBOARD_SECRET_KEY=una_clave_secreta_random
VISIT_MODE=notify
OFFICE_HOURS=lunes a viernes de 9 a 18
BASE_URL=https://TU_DOMINIO
```

### Variables que NUNCA van en produccion:

```env
SEED_DEMO_DATA    # NO incluirla, o poner false
```

---

## PARTE 5: VERIFICACION (hacer esto SIEMPRE)

Despues de cargar todo, correr estos checks uno por uno.

### 5.1 Health check basico

```bash
curl https://TU_DOMINIO/health
```
Esperado: `{"status":"ok","checks":{"api":"ok","database":"ok"}}`

### 5.2 Config de WhatsApp

```bash
curl https://TU_DOMINIO/health/whatsapp
```

Verificar:
- `phone.status` = `CONNECTED`
- `phone.account_mode` = `LIVE`
- `phone.platform_type` = `CLOUD_API`
- `token_debug.data.is_valid` = `true`
- `waba_subs.data` = debe mostrar la app suscrita

### 5.3 Webhook fields (EL MAS IMPORTANTE)

```bash
APP_TOKEN="APP_ID|APP_SECRET"
curl "https://graph.facebook.com/v21.0/APP_ID/subscriptions?access_token=$APP_TOKEN"
```

Verificar que aparezca:
- `whatsapp_business_account` con `fields: [{"name": "messages"}]` → para WhatsApp
- `page` con `fields: [{"name": "messages"}]` → para Facebook/Instagram

> Si alguno no tiene fields → suscribirlos (volver al paso 2.6 o 3.5)

### 5.4 Test de WhatsApp

1. Mandar un mensaje desde un celular al numero del bot
2. Revisar: `curl https://TU_DOMINIO/health/webhook-log`
3. Debe aparecer el payload del mensaje
4. El bot debe responder

### 5.5 Test de Facebook Messenger (si aplica)

1. Ir a la pagina de Facebook del negocio
2. Mandar un mensaje por Messenger
3. Revisar: `curl https://TU_DOMINIO/health/webhook-log`
4. El bot debe responder

### 5.6 Test de Instagram Direct (si aplica)

1. Mandar un DM a la cuenta de Instagram del negocio
2. Revisar: `curl https://TU_DOMINIO/health/webhook-log`
3. El bot debe responder

---

## TABLA DE CREDENCIALES

| Credencial | Donde se saca | Variable de entorno | Para que |
|---|---|---|---|
| App ID | Meta App Dashboard → Basica | (API calls) | Suscribir webhooks |
| App Secret | Meta App Dashboard → Basica → Mostrar | (API calls) | Suscribir webhooks |
| WABA ID | Meta → WhatsApp → API Setup | (API calls) | Suscribir app al WABA |
| Phone Number ID | Meta → WhatsApp → API Setup | `PHONE_NUMBER_ID` | Enviar mensajes WA |
| WhatsApp Token | Meta Business Suite → System User | `WHATSAPP_TOKEN` | Auth con WhatsApp API |
| Verify Token | Lo elegis vos | `VERIFY_TOKEN` | Verificar webhook URL |
| Page Access Token | Meta → Messenger → Settings | `PAGE_ACCESS_TOKEN` | Enviar msgs FB/IG |
| DeepSeek API Key | platform.deepseek.com | `DEEPSEEK_API_KEY` | IA del bot |
| Google Sheet ID | URL del sheet (entre /d/ y /edit) | `GOOGLE_SHEET_ID` | Propiedades |
| Google Credentials | Google Cloud → Service Account | `GOOGLE_CREDENTIALS_JSON` | Leer la sheet |

---

## PLANES

| Feature | Starter | Pro | Premium |
|---|---|---|---|
| WhatsApp | Si | Si | Si |
| Facebook Messenger | No | Si | Si |
| Instagram Direct | No | Si | Si |
| Dashboard analytics | Basico | Completo | Completo |
| Media Studio (videos) | No | No | Si |

Variable: `DASHBOARD_PLAN=starter` / `pro` / `premium`

---

## TROUBLESHOOTING

### El bot no responde mensajes de WhatsApp

```
1. curl /health → debe ser "ok"
2. curl /health/webhook-log → si esta vacio, Meta no manda webhooks
3. Si no llegan webhooks:
   a. Verificar webhook fields con App Secret (paso 5.3)
   b. Si NO tiene field "messages" → suscribirlo (paso 2.6 opcion B)
   c. Verificar app suscrita al WABA (paso 2.7)
   d. Verificar numero registrado en Cloud API (paso 2.8)
   e. Verificar app en modo "Publicada" (Live)
4. Si llegan webhooks pero no responde:
   a. Revisar logs de Railway (application logs)
   b. Verificar WHATSAPP_TOKEN y PHONE_NUMBER_ID
   c. curl /health/deepseek
```

### El bot no responde en Facebook/Instagram

```
1. Verificar DASHBOARD_PLAN no sea "starter"
2. Verificar PAGE_ACCESS_TOKEN este cargado en Railway
3. Verificar webhook fields de "page" tengan "messages" (paso 5.3)
4. Verificar la pagina este suscrita al webhook (paso 3.6)
5. Para IG: verificar cuenta conectada a pagina FB
```

### Mensajes a numeros falsos (mock data)

- NUNCA dejar `SEED_DEMO_DATA=true` en produccion
- La app purga datos falsos automaticamente al iniciar
- Numeros demo (54110000xxxx) estan bloqueados para envio

### Token expirado

- Verificar con `/health/whatsapp` → `token_debug.data.is_valid`
- Si es `false` → generar nuevo token en Meta Business Suite → System User
- Actualizar `WHATSAPP_TOKEN` en Railway y hacer redeploy

### Numero no conectado

- Verificar con `/health/whatsapp` → `phone.status`
- Si no es `CONNECTED` → re-registrar (paso 2.8)

---

## CHECKLIST FINAL DE ENTREGA

- [ ] App de Meta creada y publicada (Live)
- [ ] Numero de WhatsApp verificado y registrado
- [ ] System User con token permanente
- [ ] Webhook de WhatsApp configurado con field "messages"
- [ ] App suscrita al WABA
- [ ] Variables de entorno cargadas en Railway
- [ ] `/health` devuelve ok
- [ ] `/health/whatsapp` muestra CONNECTED + token valido
- [ ] Webhook fields verificados con App Secret
- [ ] Test de mensaje de WhatsApp exitoso
- [ ] (Si pro/premium) PAGE_ACCESS_TOKEN cargado
- [ ] (Si pro/premium) Webhook de FB/IG en `/webhook/meta`
- [ ] (Si pro/premium) Test de Facebook Messenger exitoso
- [ ] (Si pro/premium) Test de Instagram Direct exitoso
- [ ] Google Sheet conectada con propiedades cargadas
- [ ] `OFFICE_HOURS` configurado con el horario real de la inmobiliaria
- [ ] Follow-up habilitado y configurado
- [ ] SEED_DEMO_DATA NO esta en las variables
