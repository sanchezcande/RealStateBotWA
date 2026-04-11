# Client Onboarding: WhatsApp Bot (Vera)

## Objetivo

Guia completa para dar de alta un cliente con el bot de WhatsApp. Incluye configuracion de Meta, credenciales, webhook, y verificacion.

---

## Datos que necesitas del cliente

- Numero de WhatsApp del negocio (el que va a usar el bot)
- Numero personal del agente inmobiliario (para notificaciones / NOTIFY_NUMBER)
- Google Sheet ID con las propiedades (o se crea uno nuevo)
- Email de contacto

## Datos que VOS generas/configuras

- Meta App (o usar la existente: RealEstate WA Bot)
- WABA (WhatsApp Business Account)
- System User Token
- Phone Number ID
- Verify Token
- DeepSeek API Key

---

## Paso 1: Meta App Dashboard

### 1.1 Crear o usar la app

URL: `https://developers.facebook.com/apps/`

Si es cliente nuevo en una app compartida, usar la app existente. Si necesita app separada:
1. Crear app tipo "Business"
2. Agregar producto "WhatsApp"

### 1.2 Verificar que la app este en modo LIVE

- Ir a la barra lateral → **Publicar**
- Debe decir **"Publicada"**
- Si dice "En desarrollo", publicarla (requiere URL de politica de privacidad y email de contacto)

### 1.3 Configuracion basica (Configuracion de la app → Basica)

Completar:
- Nombre visible
- Correo electronico de contacto
- URL de la politica de privacidad: `https://propbot.cc/privacy` (o la del cliente)
- Categoria: "Bots de Messenger para empresas"

---

## Paso 2: WhatsApp Business Account (WABA)

### 2.1 Obtener el WABA ID

- Ir a **WhatsApp** → **API Setup** en el dashboard de Meta
- El WABA ID aparece arriba o en la URL
- Anotarlo (ej: `1921482308505030`)

### 2.2 Registrar el numero de telefono

- En **WhatsApp** → **API Setup** → agregar el numero del cliente
- Meta envia un codigo de verificacion por SMS o llamada
- Completar la verificacion

### 2.3 Obtener el Phone Number ID

- Una vez verificado, Meta muestra el **Phone Number ID** (ej: `1104919886028807`)
- Anotarlo para las variables de entorno

---

## Paso 3: System User y Token

### 3.1 Crear System User

- Ir a **Meta Business Suite** → **Configuracion del negocio** → **Usuarios** → **Usuarios del sistema**
- Crear un System User (tipo Admin)
- Asignarle permisos sobre el WABA

### 3.2 Generar Token

- Click en "Generar token" en el System User
- Seleccionar la app (RealEstate WA Bot)
- Permisos necesarios:
  - `whatsapp_business_management`
  - `whatsapp_business_messaging`
- El token NO debe expirar (token permanente)
- Guardarlo como `WHATSAPP_TOKEN`

---

## Paso 4: Configurar Webhook

### 4.1 URL y Verify Token

- Ir a **WhatsApp** → **Configuracion** (o **Configuration**) en el dashboard de Meta
- Callback URL: `https://propbot.cc/webhook`
- Verify Token: el valor de la variable `VERIFY_TOKEN` (ej: `opengatehub123`)
- Click en "Verificar y guardar"

### 4.2 CRITICO: Suscribir webhook fields

Esto se puede hacer de 2 formas:

**Opcion A — Desde el dashboard:**
- En la misma seccion de Webhook, click en **"Administrar"** (o **"Manage"**)
- Marcar la casilla **"messages"** ✅
- Guardar

**Opcion B — Por API (mas confiable):**

```bash
APP_TOKEN="APP_ID|APP_SECRET"
curl -X POST "https://graph.facebook.com/v21.0/APP_ID/subscriptions" \
  -d "object=whatsapp_business_account" \
  -d "callback_url=https://propbot.cc/webhook" \
  -d "verify_token=TU_VERIFY_TOKEN" \
  -d "fields=messages" \
  -d "access_token=$APP_TOKEN"
```

Debe devolver `{"success": true}`

**Para verificar que quedo bien:**

```bash
curl "https://graph.facebook.com/v21.0/APP_ID/subscriptions?access_token=$APP_TOKEN"
```

Debe mostrar:
```json
{
  "object": "whatsapp_business_account",
  "callback_url": "https://propbot.cc/webhook",
  "active": true,
  "fields": [{"name": "messages"}]
}
```

> SI NO APARECE `"fields"` CON `"messages"`, EL BOT NO VA A RECIBIR MENSAJES. Este fue el error mas comun hasta ahora.

### 4.3 Suscribir la app al WABA

Tambien por API:

```bash
curl -X POST "https://graph.facebook.com/v21.0/WABA_ID/subscribed_apps" \
  -H "Authorization: Bearer WHATSAPP_TOKEN"
```

Debe devolver `{"success": true}`

### 4.4 Registrar el numero en Cloud API

```bash
curl -X POST "https://graph.facebook.com/v21.0/PHONE_NUMBER_ID/register" \
  -H "Authorization: Bearer WHATSAPP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messaging_product": "whatsapp", "pin": "123456"}'
```

---

## Paso 5: Variables de entorno en Railway

Variables obligatorias:

```env
WHATSAPP_TOKEN=token_del_system_user
PHONE_NUMBER_ID=id_del_numero
VERIFY_TOKEN=token_de_verificacion_del_webhook
NOTIFY_NUMBER=numero_personal_del_agente
DEEPSEEK_API_KEY=clave_de_deepseek
```

Variables opcionales pero recomendadas:

```env
GOOGLE_SHEET_ID=id_de_la_sheet_con_propiedades
GOOGLE_CREDENTIALS_JSON=credenciales_de_service_account
FOLLOWUP_ENABLED=true
FOLLOWUP_DAYS=3
DASHBOARD_PLAN=starter
DASHBOARD_TOKEN=token_para_el_dashboard
DASHBOARD_ADMIN_PASSWORD=password_del_dashboard
DASHBOARD_SECRET_KEY=clave_secreta_random
VISIT_MODE=notify
```

Variables que NO deben estar en produccion:

```env
SEED_DEMO_DATA=false   # o mejor: NO incluirla
```

---

## Paso 6: Verificacion post-deploy

### 6.1 Health check basico

```bash
curl https://propbot.cc/health
# Debe devolver: {"status":"ok","checks":{"api":"ok","database":"ok"}}
```

### 6.2 Verificar config de WhatsApp

```bash
curl https://propbot.cc/health/whatsapp
```

Verificar que muestre:
- `phone.status`: `CONNECTED`
- `phone.account_mode`: `LIVE`
- `phone.platform_type`: `CLOUD_API`
- `token_debug.data.is_valid`: `true`
- `waba_subs.data`: debe contener la app suscrita
- `phone.webhook_configuration.application`: la URL del webhook

### 6.3 Verificar webhook fields (con App Secret)

```bash
APP_TOKEN="APP_ID|APP_SECRET"
curl "https://graph.facebook.com/v21.0/APP_ID/subscriptions?access_token=$APP_TOKEN"
```

Confirmar que `whatsapp_business_account` tenga `fields: [{"name": "messages"}]`

### 6.4 Verificar que llegan webhooks

```bash
curl https://propbot.cc/health/webhook-log
```

Mandar un mensaje al numero del bot y volver a consultar. Debe aparecer el payload.

### 6.5 Test de envio

Mandar un mensaje desde un celular al numero del bot. Debe:
1. Aparecer `POST /webhook` en los logs HTTP de Railway
2. Aparecer en `/health/webhook-log`
3. El bot debe responder

---

## Credenciales que necesitas tener a mano

| Credencial | Donde se obtiene | Variable de entorno |
|---|---|---|
| WhatsApp Token | Meta Business Suite → System User | `WHATSAPP_TOKEN` |
| Phone Number ID | Meta App Dashboard → WhatsApp → API Setup | `PHONE_NUMBER_ID` |
| Verify Token | Lo elegis vos (string cualquiera) | `VERIFY_TOKEN` |
| App ID | Meta App Dashboard → Configuracion → Basica | (para API calls) |
| App Secret | Meta App Dashboard → Configuracion → Basica | (para verificar webhook fields) |
| WABA ID | Meta App Dashboard → WhatsApp | (para suscribir app) |
| DeepSeek API Key | platform.deepseek.com | `DEEPSEEK_API_KEY` |
| Google Sheet ID | URL de la sheet (entre /d/ y /edit) | `GOOGLE_SHEET_ID` |
| Google Credentials | Google Cloud Console → Service Account | `GOOGLE_CREDENTIALS_JSON` |

---

## Troubleshooting

### El bot no responde mensajes

1. Verificar `/health` → debe ser `ok`
2. Verificar `/health/webhook-log` → si esta vacio, Meta no manda webhooks
3. Si no llegan webhooks:
   - Verificar webhook fields con App Secret (paso 6.3)
   - Si `whatsapp_business_account` no tiene field `messages` → suscribirlo (paso 4.2)
   - Verificar que la app este suscrita al WABA (paso 4.3)
   - Verificar que el numero este registrado en Cloud API (paso 4.4)
   - Verificar que la app este en modo **Publicada** (Live)
4. Si llegan webhooks pero no responde:
   - Revisar logs de Railway (application logs, no solo HTTP)
   - Verificar `WHATSAPP_TOKEN` y `PHONE_NUMBER_ID`
   - Verificar `/health/deepseek`

### Mensajes a numeros falsos (mock data)

- Nunca dejar `SEED_DEMO_DATA=true` en produccion
- Si se seedearon datos falsos, la app los purga automaticamente al iniciar
- Los numeros demo (54110000xxxx) estan bloqueados para envio

### Token expirado o invalido

- Verificar con `/health/whatsapp` → `token_debug.data.is_valid`
- Si es `false`, generar un nuevo token en Meta Business Suite → System User
- Actualizar `WHATSAPP_TOKEN` en Railway

### Numero no conectado

- Verificar con `/health/whatsapp` → `phone.status`
- Si no es `CONNECTED`, re-registrar el numero (paso 4.4)
