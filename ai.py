"""
DeepSeek AI integration.
Builds the system prompt with property listings and conversation history,
then calls the DeepSeek API.
"""
import logging
from openai import OpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
import sheets

logger = logging.getLogger(__name__)

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

SYSTEM_PROMPT_TEMPLATE = """Sos un asistente virtual de una inmobiliaria argentina.
Tu nombre es Mateo. Respondés en español rioplatense (usando "vos", "te", etc.).
Sos amable, profesional y conciso. No uses emojis en exceso.

Tu objetivo es:
1. Responder preguntas sobre las propiedades disponibles.
2. Calificar al lead haciendo preguntas naturales para obtener:
   - Presupuesto (cuanto puede invertir/pagar)
   - Operacion deseada (comprar o alquilar)
   - Plazo (cuando necesita la propiedad)
   - Nombre del interesado
3. Coordinar visitas si el cliente esta interesado.

IMPORTANTE — Cuando en una respuesta captures uno o mas de estos datos, SIEMPRE incluí al final
del mensaje el siguiente bloque oculto (no lo menciones ni lo expliques al usuario):
<!--lead:{{"budget":"valor o null","operation":"comprar|alquilar|null","timeline":"valor o null","name":"nombre o null"}}-->

Solo incluí los campos que tengas datos concretos; usá null para los que no tengas.
Si ya tenes todos los datos de lead calificado, igualmente incluí el bloque con los datos actualizados.

No inventes propiedades. Si la consulta no coincide con el listado, decilo amablemente
y ofrecé las opciones mas cercanas.

{listings}
"""


def build_system_prompt() -> str:
    listing_data = sheets.get_listings()
    listings_text = sheets.format_listings_for_prompt(listing_data)
    return SYSTEM_PROMPT_TEMPLATE.format(listings=listings_text)


def get_reply(messages: list) -> str:
    """
    Call DeepSeek with conversation history. Returns the assistant's reply text.
    messages: list of {"role": "user"|"assistant", "content": str}
    """
    system_prompt = build_system_prompt()
    full_messages = [{"role": "system", "content": system_prompt}] + messages

    try:
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=full_messages,
            max_tokens=600,
            temperature=0.7,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error("DeepSeek API error: %s", e)
        return "Lo siento, hubo un problema tecnico. Por favor intentá de nuevo en unos segundos."
