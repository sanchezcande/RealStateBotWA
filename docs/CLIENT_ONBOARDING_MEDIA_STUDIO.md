# Client Onboarding: Media Studio

## Objetivo

Esta guia sirve para dar de alta a un cliente que va a usar el generador de videos e imagenes con Google Gemini dentro de RealEstateBot.

## Que necesita el cliente

- Una cuenta de Google
- Un proyecto activo en Google AI Studio / Gemini API
- Billing habilitado en ese proyecto
- Una API key de Gemini

## Paso 1: Crear o ubicar el proyecto correcto

1. Entrar a `https://aistudio.google.com/apikey`
2. Crear un proyecto nuevo o importar uno existente
3. Confirmar que la API key quede asociada al proyecto correcto

Importante:
- El nombre del proyecto en Google no tiene que coincidir exactamente con el nombre comercial del cliente
- Lo importante es que la API key usada por la app salga de ese proyecto

## Paso 2: Habilitar billing

1. Entrar al proyecto desde AI Studio
2. Ir a la vista de API keys o billing
3. Habilitar `Set up Billing` o `Upgrade`
4. Confirmar que el proyecto pase a paid tier

Links oficiales:
- `https://ai.google.dev/gemini-api/docs/billing/`
- `https://ai.google.dev/pricing`
- `https://ai.google.dev/gemini-api/docs/rate-limits`

## Paso 3: Configurar la app

Definir la variable:

```env
GOOGLE_AI_API_KEY=tu_api_key
```

La app usa esa key para:
- generar videos con Veo
- generar imagenes con Imagen

## Paso 4: Modelo usado por defecto

El sistema ahora usa:

```text
veo-3.0-fast-generate-001
```

Motivo:
- menor costo por prueba
- menor riesgo al testear onboarding

## Paso 5: Primer test recomendado

Para validar que todo funcione sin gastar de mas:

1. Subir 2 fotos
2. Elegir formato `Vertical 9:16` si el destino es reels, stories o WhatsApp
3. Elegir `Horizontal 16:9` si el destino es web o YouTube
4. Generar un video
5. Revisar que:
   - se creen todos los clips
   - el video final se una correctamente
   - no aparezcan errores `429 RESOURCE_EXHAUSTED`

## Como funciona el video multi-foto

La app no manda todas las fotos juntas a Veo.

Hace esto:
1. Genera 1 clip por foto
2. Recorta cada clip
3. Une los clips en un solo MP4 con `ffmpeg`

Consecuencia:
- 3 fotos = 3 requests a Veo
- 5 fotos = 5 requests a Veo

## Formatos soportados

- `Vertical 9:16`
- `Horizontal 16:9`

Recomendacion:
- Vertical para Instagram Reels, TikTok, Stories y WhatsApp
- Horizontal para landing pages, YouTube y desktop

## Costos estimados

Los costos dependen de Google y pueden cambiar. Revisar siempre:

- `https://ai.google.dev/pricing`

Referencia operativa:
- Veo Fast reduce bastante el costo frente a Veo Standard
- Cada foto adicional implica otro clip y otro request

## Errores frecuentes

### 429 RESOURCE_EXHAUSTED

Significa:
- rate limit alcanzado
- cuota agotada
- trial o tier insuficiente

Que hacer:
- esperar un rato
- bajar la cantidad de fotos
- confirmar billing y tier

### Video parcial

El sistema actual no guarda videos parciales.

Si faltan clips:
- el job falla
- se informa el error
- no se entrega un video engañoso

### Fallo al unir clips

La app usa `ffmpeg` dentro del contenedor.

Si la union falla:
- revisar logs del job
- confirmar que el deploy incluya `ffmpeg`

## Checklist de entrega al cliente

- Proyecto de Gemini creado o importado
- Billing activo
- API key generada
- API key cargada en la app
- Prueba de video con 2 fotos completada
- Prueba de imagen completada
- Confirmacion del formato preferido del cliente
- Limite de gasto revisado

## Recomendacion comercial

Durante onboarding:
- arrancar con 2 fotos por video
- usar `Veo Fast`
- usar `Vertical 9:16` si el cliente publica en redes

Cuando ya este estable:
- subir a 3 o mas fotos
- ajustar prompts por tipo de propiedad
