"""
Cliente mínimo para tu LLM local. Asume un endpoint compatible con la API
de chat de OpenAI, que es lo que exponen tanto Ollama (`/v1/chat/completions`)
como LM Studio. Ajusta la ruta si tu setup difiere.

Sobre el costo de cada llamada: el modelo en uso (`lfm2.5-2.6b`) razona
antes de responder y no se le puede apagar el razonamiento desde la API
(probado contra LM Studio con `chat_template_kwargs.enable_thinking=false`,
con `reasoning_effort` y con `/nothink` en el system prompt: los tres
siguen gastando ~190 tokens de razonamiento). Medido, una clasificación
cuesta entre 13 y 25 segundos. Por eso `clasificar_intent` intenta primero
resolver el mensaje sin LLM y solo llama al modelo cuando el texto es
realmente ambiguo -- ver `_clasificar_sin_llm`.
"""

import json
import random
import re
from datetime import datetime

import requests
from flask import current_app

from app.textutils import normalizar

SYSTEM_PROMPT = """Eres el asistente de reservas de la LACS (Centro de Producción
Audiovisual) de Uninorte. SOLO puedes ayudar con: reservar equipos/espacios,
consultar disponibilidad, cancelar una reserva, o dar ayuda sobre cómo usar
este chat. Si el mensaje pide cualquier otra cosa, responde amablemente que
no puedes ayudar con eso y sugiere escribir "ayuda". Nunca inventes
disponibilidad: si no tienes el dato, dilo explícitamente. Sé breve y claro,
como si hablaras por WhatsApp. No uses markdown ni listas con viñetas."""


PROMPT_CHARLA = """Eres el asistente de reservas de la LACS (Centro de
Producción Audiovisual) de Uninorte, hablando por WhatsApp. El usuario te
escribió algo conversacional: un saludo, una cortesía, una despedida o una
pregunta sobre ti. Respóndele de forma natural y cálida, en 2 o 3 líneas
como máximo, y menciona de pasada, en una sola línea, que puedes reservarle
un equipo o un espacio, decirle si algo está disponible, o cancelar una
reserva suya.

Reglas: no inventes disponibilidad, precios, horarios ni datos de reservas,
porque no los tienes. No uses markdown, ni negritas, ni listas con viñetas:
es WhatsApp, escribe en prosa corta. Una sola pregunta al final, no más."""


INTENTS_VALIDOS = ("reservar", "consultar_disponibilidad", "cancelar", "ayuda", "saludo", "otro")

PROMPT_CLASIFICACION = """Eres un clasificador de intents, no un asistente
conversacional. Dado un mensaje de WhatsApp dirigido al bot de reservas de
la LACS (Centro de Producción Audiovisual) de Uninorte, clasifícalo en
EXACTAMENTE una de estas categorías y responde SOLO con la palabra de la
categoría (sin comillas, sin explicación, sin markdown, sin texto extra):

- reservar: quiere reservar/apartar un equipo o espacio, o le está
  respondiendo al bot dentro de un intento de reserva ya en curso (ej.
  eligiendo cuál activo de una lista que el bot ofreció, dando fecha/hora,
  corrigiendo un dato).
- consultar_disponibilidad: pregunta qué activos existen, cuáles están
  libres/ocupados, o pide información del catálogo sin pedir reservar
  todavía (ej. "qué cámaras tienen", "qué hay disponible", "tienen
  micrófonos").
- cancelar: quiere cancelar una reserva que ya tiene hecha.
- ayuda: pregunta qué puede hacer el bot o cómo usarlo.
- saludo: conversación social, sin ningún pedido concreto detrás: saludos,
  despedidas, agradecimientos, cortesías, o preguntas sobre el bot mismo
  (ej. "hola", "buenos días", "cómo estás", "gracias", "quién eres").
- otro: cualquier otra cosa (temas que no tienen nada que ver con la LACS,
  texto sin sentido).

Responde solo la palabra de la categoría."""


# --------------------------------------------------------------------------
# Vocabulario. Tres capas distintas, a propósito -- no las mezcles:
#
#   1. MARCADORES_*    señales inequívocas. Si aparecen, el mensaje se
#                      clasifica SIN llamar al LLM (`_clasificar_sin_llm`).
#   2. PALABRAS_*      vocabulario más amplio y menos preciso, para el
#                      heurístico de respaldo cuando el LLM no responde.
#   3. SENALES_DOMINIO "esto habla del mundo de la LACS". No clasifica nada
#                      por sí solo: solo decide si una respuesta de acción
#                      del LLM se toma en serio (`_validar_intent_de_accion`).
#
# Todo se compara contra texto ya pasado por `textutils.normalizar`
# (minúsculas y sin tildes), así que va escrito sin acentos: "manana",
# "camara", "microfono".
# --------------------------------------------------------------------------

MARCADORES_CANCELAR = ("cancel", "anul")
MARCADORES_RESERVAR = ("reserv", "apart", "separ", "agend", "alquil", "prestamo", "prestame", "rentar")
MARCADORES_DISPONIBILIDAD = (
    "disponib", "esta libre", "estan libres", "hay libre", "que hay libre",
    "que hay disponible", "cuales hay", "que tienen", "tienen algun",
    "tienen alguna", "queda algun", "queda alguna", "esta ocupad",
    "estan ocupad",
)
MARCADORES_AYUDA = (
    "ayuda", "help", "que puedes hacer", "que sabes hacer", "para que sirves",
    "como funciona", "como te uso", "como se usa", "que opciones", "comandos",
    "instrucciones",
)

# Formas de desistir que NO usan la palabra "cancelar". Son la trampa de la
# ruta rápida: "ya no voy a necesitar la reserva de mañana" contiene
# "reserv", así que el marcador de reservar lo mandaría al wizard de
# reservas justo cuando el usuario quiere lo contrario (medido: el LLM sí
# responde "cancelar" a esa frase). Cuando aparece una de estas, la ruta
# rápida se abstiene y deja decidir al modelo.
MARCADORES_DESISTIR = (
    "ya no", "no voy a", "no podre", "no puedo ir", "no necesito",
    "no la necesito", "no lo necesito", "dar de baja", "echar para atras",
    "desisto", "me arrepenti", "ya no quiero", "borra la reserva",
    "elimina la reserva", "quitar la reserva",
)

# Vocabulario del heurístico de respaldo. Más amplio que los marcadores:
# incluye verbos genéricos como "necesito" o "quiero", que por sí solos no
# bastan para saltarse el LLM, pero sí sirven cuando el LLM no responde y
# no hay una segunda opinión que corrija.
PALABRAS_CANCELAR = MARCADORES_CANCELAR
PALABRAS_RESERVAR = MARCADORES_RESERVAR + ("necesito", "quiero", "quisiera", "me gustaria")
PALABRAS_DISPONIBILIDAD = MARCADORES_DISPONIBILIDAD + ("libre", "horario", "ocupad")
PALABRAS_AYUDA = MARCADORES_AYUDA

# Cosas que solo se dicen si el mensaje tiene algo que ver con la LACS:
# verbos de reserva, vocabulario de equipos y espacios, y referencias
# temporales. Se complementa en runtime con los nombres reales del catálogo
# que `webhook.py` le pasa a `clasificar_intent` (ver `terminos_catalogo`).
SENALES_DOMINIO = (
    # acción
    "reserv", "apart", "separ", "agend", "alquil", "prestamo", "presta",
    "renta", "cancel", "anul", "disponib", "libre", "ocupad", "necesito",
    "quiero", "quisiera", "me gustaria", "usar", "ocupar", "sacar", "pedir",
    "solicit", "confirmar",
    # equipos y espacios
    "camara", "lente", "tripode", "luz", "luces", "microfono", "audio",
    "sonido", "grabar", "grabacion", "filmar", "video", "foto", "dron",
    "celular", "estudio", "sala", "salon", "espacio", "equipo", "activo",
    "cabina", "edicion", "croma", "memoria", "bateria", "cargador", "cable",
    "monitor", "proyector", "computador", "portatil", "laptop", "tarjeta",
    "estabilizador", "gimbal", "slider", "reflector",
    # tiempo
    "hoy", "manana", "lunes", "martes", "miercoles", "jueves", "viernes",
    "sabado", "domingo", "semana", "hora", "horario", "fecha", "dias",
    "mediodia", "tarde", "noche", "reserva", "cita", "turno",
)

# "3pm", "15:30", "a las 4 pm", "12/10" -- una hora o una fecha también es
# señal de que el mensaje habla del dominio, aunque no nombre ningún equipo.
_REGEX_TIEMPO = re.compile(r"\b\d{1,2}\s*(?::\d{2}|[ap]\.?\s?m\.?\b|h\b)|\b\d{1,2}\s*/\s*\d{1,2}")

# Conversación social. `_es_charla_social` la usa restándola del mensaje:
# si al quitar todo esto no queda nada más, el mensaje era puro saludo.
FRASES_SOCIALES = (
    "buenos dias", "buenas tardes", "buenas noches", "buen dia",
    "como estas", "como esta", "como vas", "como va", "que mas",
    "que tal", "que hubo", "muchas gracias", "mil gracias", "hasta luego",
    "nos vemos", "feliz dia", "un gusto", "mucho gusto", "quien eres",
    "quien sos", "como te llamas", "todo bien", "estas ahi", "sigues ahi",
    "que haces", "buenas buenas",
)
PALABRAS_SOCIALES = frozenset({
    "hola", "holi", "holaa", "ola", "buenas", "hey", "ey", "saludos",
    "gracias", "chao", "adios", "bye", "ok", "oka", "okey", "listo",
    "dale", "perfecto", "genial", "excelente", "quiubo", "porfa",
})

# Palabras que pueden quedar sueltas alrededor de un saludo sin cambiar que
# el mensaje siga siendo puro saludo ("hola, ¿todo bien por ahí?").
_RELLENO_SOCIAL = frozenset({
    "y", "tu", "usted", "por", "ahi", "aca", "aqui", "todo", "muy", "mucho",
    "a", "de", "el", "la", "los", "las", "un", "una", "que", "tal", "bot",
    "buen", "buenos", "dia", "dias", "favor", "hoy", "pues", "eh", "bien",
})

# Saludos pelados: no piden interpretación ninguna, se contestan sin LLM.
SALUDOS_PELADOS = frozenset({
    "hola", "holi", "holaa", "ola", "buenas", "buenos dias", "buen dia",
    "buenas tardes", "buenas noches", "hey", "ey", "saludos", "hola buenas",
    "hola buenos dias", "hola buenas tardes", "hola buenas noches",
    "que mas", "quiubo", "que hubo", "buenas buenas", "hola hola",
})

# Solo estas tres categorías tienen consecuencias reales si se clasifican
# mal (arrancan un wizard de reserva/cancelación, o le afirman al usuario
# el estado de un activo) -- "ayuda", "saludo" y "otro" son inofensivas de
# sobra: lo peor que hacen es responder de más o abrir el menú de fallback
# (ver `menu_service.py`). Por eso solo estas se validan.
_INTENTS_DE_ACCION = ("reservar", "consultar_disponibilidad", "cancelar")

# Cómo escribe el modelo cada categoría cuando no responde la palabra
# exacta (visto en la práctica: "consultar disponibilidad" con espacio en
# vez de guion bajo, "saludar" en vez de "saludo").
_ALIAS_INTENT = {
    "consultar disponibilidad": "consultar_disponibilidad",
    "consulta_disponibilidad": "consultar_disponibilidad",
    "disponibilidad": "consultar_disponibilidad",
    "saludar": "saludo",
    "charla": "saludo",
    "conversacion": "saludo",
    "social": "saludo",
    "cancelacion": "cancelar",
    "reserva": "reservar",
}

# De más larga a más corta: al buscar la categoría dentro de una frase,
# "consultar_disponibilidad" tiene que ganarle a "disponibilidad".
_CLAVES_INTENT = tuple(
    sorted(
        [(i, i) for i in INTENTS_VALIDOS] + list(_ALIAS_INTENT.items()),
        key=lambda par: len(par[0]),
        reverse=True,
    )
)


def _texto_plano(texto_usuario: str) -> str:
    """Normaliza (minúsculas, sin tildes) y deja solo letras, dígitos y
    espacios, para que la puntuación y los emoji no impidan reconocer un
    saludo: "¡Hola!! 👋" -> "hola"."""
    limpio = re.sub(r"[^a-z0-9 ]+", " ", normalizar(texto_usuario))
    return " ".join(limpio.split())


def _es_charla_social(texto_usuario: str) -> bool:
    """True si el mensaje es SOLO conversación social: saludo, cortesía,
    despedida o pregunta sobre el bot, sin ningún pedido concreto.

    Se implementa restando: se le quitan al texto las frases y palabras
    sociales conocidas y, si lo que queda es vacío o puro relleno, era un
    saludo suelto. Restar en vez de buscar coincidencias evita el falso
    positivo obvio -- "hola, necesito una cámara" contiene "hola" pero no
    es un saludo: ahí queda residuo ("necesito camara") que delata el
    pedido. Las frases de varias palabras se quitan por substring; las de
    una sola, comparando token a token, para que "también" no cuente como
    "bien" ni "reyes" como "ey"."""
    plano = _texto_plano(texto_usuario)
    if not plano or len(plano.split()) > 8:
        return False
    resto = plano
    for frase in sorted(FRASES_SOCIALES, key=len, reverse=True):
        resto = resto.replace(frase, " ")
    return all(
        palabra in PALABRAS_SOCIALES or palabra in _RELLENO_SOCIAL
        for palabra in resto.split()
    )


def _tiene_senal_de_dominio(texto_usuario: str, terminos_catalogo: list[str] | None) -> bool:
    """True si el mensaje menciona algo del mundo de la LACS: un verbo de
    reserva, un tipo de equipo o espacio, una referencia temporal, o un
    nombre/categoría real del catálogo. Es deliberadamente generoso: su
    trabajo no es clasificar, sino separar "esto va del bot" de "esto no
    tiene nada que ver" antes de dejar que el LLM arranque un wizard."""
    texto_norm = normalizar(texto_usuario)
    if any(senal in texto_norm for senal in SENALES_DOMINIO):
        return True
    if _REGEX_TIEMPO.search(texto_norm):
        return True
    for termino in terminos_catalogo or ():
        # Un nombre de catálogo largo ("Cámara Sony FX30") casi nunca se
        # escribe completo, así que basta con que alguna de sus palabras
        # significativas aparezca. Las de 3 letras o menos se descartan:
        # no discriminan nada ("de", "kit", "pro").
        for palabra in normalizar(termino).split():
            if len(palabra) >= 4 and palabra in texto_norm:
                return True
    return False


def _clasificar_sin_llm(texto_usuario: str) -> str | None:
    """Intento de clasificación instantánea, sin red. Devuelve None cuando
    el mensaje no trae ninguna señal inequívoca -- ahí sí vale la pena
    pagar los 13-25 segundos que cuesta preguntarle al modelo.

    Con `lfm2.5-2.6b` esto no es solo una optimización de latencia: los
    mensajes que caen aquí son justamente los que cualquier modelo acierta,
    así que no se pierde precisión, y a cambio el bot contesta al instante
    en los dos casos más frecuentes (alguien que escribe "quiero reservar
    una cámara", y alguien que escribe "hola").

    Orden deliberado: "cancel" antes que "reserv" porque "cancelar mi
    reserva" contiene la subcadena "reserv"; los marcadores de acción antes
    que el saludo porque "hola, quiero reservar una cámara" es un pedido,
    no un saludo."""
    texto_norm = normalizar(texto_usuario)

    if any(p in texto_norm for p in MARCADORES_CANCELAR):
        return "cancelar"
    # Desistir sin decir "cancelar" no es inequívoco: que aparezca la
    # palabra "reserva" en "ya no voy a necesitar la reserva de mañana" no
    # significa que quiera reservar. Se abstiene y decide el LLM.
    if any(p in texto_norm for p in MARCADORES_DESISTIR):
        return None
    if any(p in texto_norm for p in MARCADORES_AYUDA):
        return "ayuda"
    if any(p in texto_norm for p in MARCADORES_RESERVAR):
        return "reservar"
    if any(p in texto_norm for p in MARCADORES_DISPONIBILIDAD):
        return "consultar_disponibilidad"
    if _es_charla_social(texto_usuario):
        return "saludo"
    return None


def _extraer_intent_de_texto(contenido: str) -> str | None:
    """Saca la categoría de lo que respondió el LLM. Normalmente el modelo
    contesta una sola palabra limpia, pero a veces la envuelve en una frase.

    Cuando hay varias categorías mencionadas se toma la ÚLTIMA, no la
    primera: si el modelo razona en voz alta, la conclusión va al final
    ("no es cancelar, es reservar"). La versión anterior recorría
    `INTENTS_VALIDOS` en orden fijo, así que "reservar" ganaba siempre por
    ser el primer elemento de la tupla."""
    limpio = normalizar(contenido).strip().strip(" .,;:!?\"'`*_-\n")
    if limpio in INTENTS_VALIDOS:
        return limpio
    if limpio in _ALIAS_INTENT:
        return _ALIAS_INTENT[limpio]

    mejor_pos, mejor_intent = -1, None
    for clave, intent in _CLAVES_INTENT:
        pos = limpio.rfind(clave)
        if pos > mejor_pos:
            mejor_pos, mejor_intent = pos, intent
    return mejor_intent


def _validar_intent_de_accion(
    intent: str, texto_usuario: str, terminos_catalogo: list[str] | None
) -> str:
    """Red de seguridad sobre la respuesta del LLM, mucho más holgada que
    la que había antes.

    Historia: con `liquid/lfm2.5-1.2b` el modelo tenía un sesgo medido
    hacia "reservar"/"cancelar" ante cualquier mensaje ambiguo, así que se
    exigía que el texto del usuario contuviera literalmente una palabra
    clave de esa misma categoría; si no, se descartaba la respuesta del
    LLM. Eso era rígido a propósito, pero le quitaba al chat toda la
    holgura: "tienen algo para grabar el jueves" no contiene ninguna
    palabra clave de "reservar" y terminaba en el menú de fallback aunque
    el modelo lo hubiera entendido perfecto.

    Con `lfm2.5-2.6b` ese sesgo ya no aparece (verificado: "buenos dias" ->
    "otro", donde el 1.2b decía "reservar"), así que ahora se le cree al
    modelo salvo que el mensaje no tenga NINGUNA relación con el dominio.
    Ya no se exige la palabra clave exacta de la categoría, solo que el
    mensaje hable de la LACS: un verbo de reserva, un equipo, un espacio,
    una fecha, o un nombre del catálogo. Si no hay nada de eso, el mensaje
    no puede haber sido una petición de reserva por más que el LLM lo diga,
    y se enruta a "saludo" (si era conversación) o a "otro" (y de ahí al
    menú de `menu_service.py`)."""
    if intent not in _INTENTS_DE_ACCION:
        return intent
    if _tiene_senal_de_dominio(texto_usuario, terminos_catalogo):
        return intent
    if _es_charla_social(texto_usuario):
        return "saludo"
    return "otro"


def clasificar_intent(texto_usuario: str, terminos_catalogo: list[str] | None = None) -> str:
    """Clasifica el intent del mensaje. Tres capas, en orden:

    1. `_clasificar_sin_llm`: si el mensaje trae una señal inequívoca, se
       responde al instante y no se llama al modelo.
    2. El LLM local (mismo endpoint que `extraer_datos_reserva`), validado
       después por `_validar_intent_de_accion`.
    3. `_clasificar_intent_heuristico`, si el LLM no responde, tarda
       demasiado o dice algo irreconocible -- mejor una clasificación
       aproximada que dejar el mensaje bloqueado por la whitelist.

    `terminos_catalogo` son nombres y subcategorías reales de activos (se
    los pasa `webhook.py` desde `recursos_service.listar_terminos_catalogo`);
    sirven como señal de dominio para reconocer un pedido que nombra un
    equipo sin usar ningún verbo de reserva ("¿y el trípode Manfrotto?").
    Es opcional: sin ellos el guard sigue funcionando con el vocabulario
    estático, solo un poco más estrecho."""
    rapido = _clasificar_sin_llm(texto_usuario)
    if rapido is not None:
        return rapido

    base_url = current_app.config["LLM_BASE_URL"].rstrip("/")
    modelo = current_app.config["LLM_MODEL"]
    url = f"{base_url}/v1/chat/completions"

    payload = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": PROMPT_CLASIFICACION},
            {"role": "user", "content": texto_usuario},
        ],
        "temperature": 0,
        # Tope deliberadamente bajo, y por un motivo distinto al de
        # `extraer_datos_reserva` (que necesita 2000). Aquí la respuesta
        # útil es UNA palabra: todo el presupuesto se va en razonar, y lo
        # medido es que ~190 tokens bastan en un mensaje normal y ~490 en
        # uno difícil. Cuando el modelo se enreda de verdad se dispara
        # (952 tokens y 61 segundos con "esto no tiene sentido") y encima
        # concluye mal. Cortar en 800 acota la espera y convierte ese caso
        # en `finish_reason == "length"`, que abajo se trata como "el LLM
        # no supo" y cae al heurístico -- que además acierta.
        "max_tokens": 800,
    }

    try:
        # 45s, no los 150 de `extraer_datos_reserva`. Medido con
        # `lfm2.5-2.6b`: toda clasificación que el modelo acierta llegó en
        # 30s o menos; pasado eso lo que hay es un modelo enredado que
        # además concluye mal. Cortar y responder con el heurístico es
        # mejor respuesta y mucho menos espera. Si el LLM está caído del
        # todo, esto también acota cuánto tarda el bot en darse cuenta.
        resp = requests.post(url, json=payload, headers=_headers(), timeout=45)
        resp.raise_for_status()
        eleccion = resp.json()["choices"][0]
        mensaje = eleccion["message"]
        contenido = (mensaje.get("content") or mensaje.get("reasoning_content") or "").strip()
        truncado = eleccion.get("finish_reason") == "length"
    except (requests.RequestException, KeyError, IndexError) as exc:
        current_app.logger.error("Error consultando el LLM para clasificar intent: %s", exc)
        return _clasificar_intent_heuristico(texto_usuario)

    if truncado:
        current_app.logger.warning(
            "El LLM agotó el presupuesto de tokens razonando sobre %r; se usa el heurístico",
            texto_usuario,
        )
        return _clasificar_intent_heuristico(texto_usuario)

    intent = _extraer_intent_de_texto(contenido)
    if intent is None:
        current_app.logger.warning("El LLM devolvió un intent no reconocido: %r", contenido)
        return _clasificar_intent_heuristico(texto_usuario)

    # El modelo a veces se rinde con "otro" en mensajes que sí eran
    # conversación ("cómo estás", "quién eres"): esos merecen una respuesta
    # natural, no el menú de fallback -- que es justo lo que se espera
    # cuando alguien intenta hablar normalmente.
    if intent == "otro" and _es_charla_social(texto_usuario):
        return "saludo"

    return _validar_intent_de_accion(intent, texto_usuario, terminos_catalogo)


def _clasificar_intent_heuristico(texto_usuario: str) -> str:
    """Heurística de respaldo por palabras clave -- se usa solo si el LLM
    no está disponible o responde algo irreconocible. Usa el vocabulario
    ampliado (`PALABRAS_*`), no los marcadores estrictos: aquí no hay una
    segunda opinión que corrija, así que conviene ser generoso.

    "cancel" se revisa antes que "reserv" a propósito: "cancelar mi
    reserva" contiene la subcadena "reserv" (de "reserva"), así que si el
    orden fuera al revés, cancelar algo caería mal clasificado como
    "reservar". Disponibilidad va antes que reservar porque "quiero saber
    si hay una cámara libre" contiene "quiero", que aquí cuenta como
    palabra de reserva."""
    texto = normalizar(texto_usuario)
    if any(p in texto for p in PALABRAS_CANCELAR):
        return "cancelar"
    if any(p in texto for p in PALABRAS_AYUDA):
        return "ayuda"
    if any(p in texto for p in PALABRAS_DISPONIBILIDAD):
        return "consultar_disponibilidad"
    if any(p in texto for p in PALABRAS_RESERVAR):
        return "reservar"
    if _es_charla_social(texto_usuario):
        return "saludo"
    return "otro"


def _headers() -> dict:
    api_key = current_app.config.get("LLM_API_KEY")
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


_REGEX_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def _limpiar_para_whatsapp(texto: str) -> str:
    """WhatsApp no renderiza markdown: usa un solo asterisco para negrita,
    así que `**así**` se ve literalmente con los asteriscos dobles (y el
    modelo los escribe igual aunque el prompt le pida que no). También se
    quita cualquier bloque `<think>` que se haya colado en `content` en vez
    de quedarse en `reasoning_content`."""
    texto = _REGEX_THINK.sub("", texto)
    texto = re.sub(r"\*\*(.+?)\*\*", r"*\1*", texto, flags=re.DOTALL)
    texto = re.sub(r"^\s{0,3}#{1,6}\s*", "", texto, flags=re.MULTILINE)
    return texto.strip()


MENSAJE_ERROR_LLM = (
    "Tuve un problema procesando tu mensaje. Por favor intenta de "
    "nuevo en un momento, o escribe 'ayuda'."
)


def _completar(system_prompt: str, texto_usuario: str, respaldo: str) -> str:
    """Una vuelta de chat contra el LLM local, con el mismo manejo de
    errores para todos los llamadores. `max_tokens` y `timeout` están
    dimensionados para un modelo que razona: `lfm2.5-2.6b` gasta ~140
    tokens razonando antes de escribir la respuesta, y una respuesta
    conversacional corta tardó entre 10 y 20 segundos medida contra LM
    Studio -- los 30s de timeout y los 300 tokens de antes se quedaban
    cortos y cortaban la respuesta a la mitad."""
    base_url = current_app.config["LLM_BASE_URL"].rstrip("/")
    modelo = current_app.config["LLM_MODEL"]
    url = f"{base_url}/v1/chat/completions"

    payload = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": texto_usuario},
        ],
        "temperature": 0.3,
        "max_tokens": 800,
    }

    try:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=120)
        resp.raise_for_status()
        mensaje = resp.json()["choices"][0]["message"]
        contenido = _limpiar_para_whatsapp(mensaje.get("content") or "")
    except (requests.RequestException, KeyError, IndexError) as exc:
        current_app.logger.error("Error consultando el LLM local: %s", exc)
        return respaldo

    # Si el modelo gastó todo el presupuesto razonando, "content" llega
    # vacío: mejor el texto de respaldo que mandarle un mensaje en blanco
    # al usuario.
    return contenido or respaldo


def generar_respuesta(texto_usuario: str) -> str:
    """Respuesta libre del LLM, acotada por SYSTEM_PROMPT. Solo se usa para
    el intent 'ayuda' (ver `routes/webhook.py`): lo que responde
    reservar/consultar/cancelar sale de datos reales, nunca del modelo."""
    return _completar(SYSTEM_PROMPT, texto_usuario, MENSAJE_ERROR_LLM)


RESPUESTAS_SALUDO = (
    "¡Hola! 👋 Soy el asistente de reservas de la LACS. Puedo apartarte un "
    "equipo o un espacio, decirte si algo está libre, o cancelar una "
    "reserva tuya. ¿Qué necesitas?",
    "¡Hola! Aquí estoy 🙂 Te ayudo a reservar equipos o espacios de la LACS, "
    "a consultar si algo está disponible, o a cancelar una reserva. "
    "Cuéntame qué necesitas.",
    "¡Hey! 👋 Soy el bot de reservas de la LACS. Dime qué equipo o espacio te "
    "interesa y lo miramos; también puedo contarte qué hay libre, o "
    "cancelar una reserva tuya.",
)


def responder_charla(texto_usuario: str) -> str:
    """Respuesta al intent 'saludo': conversación social, sin un pedido
    concreto detrás.

    Un saludo pelado ("hola", "buenos días") no tiene nada que interpretar,
    así que se responde con un texto fijo -- instantáneo, y con este modelo
    eso importa: una respuesta generada cuesta entre 10 y 20 segundos, que
    es una eternidad para contestar "hola" por WhatsApp. Se rota entre
    varias redacciones para que no suene a máquina cuando alguien saluda
    dos días seguidos.

    Lo demás que sea social pero no un saludo pelado ("cómo estás", "quién
    eres", "gracias por todo") sí pasa por el LLM: ahí la naturalidad vale
    el tiempo, y si el modelo falla se cae al mismo texto fijo."""
    if _texto_plano(texto_usuario) in SALUDOS_PELADOS:
        return random.choice(RESPUESTAS_SALUDO)
    return _completar(PROMPT_CHARLA, texto_usuario, random.choice(RESPUESTAS_SALUDO))


_DIAS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

PROMPT_EXTRACCION = """Eres un extractor de datos, no un asistente conversacional.
Dado un mensaje de un usuario que quiere reservar un activo de la LACS
(Centro de Producción Audiovisual), devuelve SOLO un objeto JSON (sin
markdown, sin explicación, sin texto antes o después) con estas claves:

- "recurso": el nombre EXACTO (copiado tal cual) de uno de estos activos,
  solo si el mensaje lo menciona con claridad; si no, null.
  Activos disponibles: {nombres_recursos}
- "inicio": fecha y hora de inicio solicitada, formato "YYYY-MM-DD HH:MM"
  (24 horas), o null si el mensaje no da información suficiente para
  calcularla.
- "fin": fecha y hora de fin, mismo formato. Si el usuario da una
  duración en vez de hora de fin (ej. "por 2 horas"), calcúlala sumando
  esa duración al inicio. Si no hay ninguna pista de duración ni de fin,
  asume 1 hora de duración. null si no se pudo calcular "inicio".

Hoy es {dia_semana} {fecha}, son las {hora} (hora de Colombia, esa es tu
única zona horaria de referencia). Usa esto para resolver fechas
relativas ("hoy", "mañana", "el viernes", "en 2 horas", etc.) -- si el
usuario nombra un día de la semana, asume el próximo que ocurra a partir
de hoy (inclusive si es hoy mismo y la hora todavía no pasó).

Responde solo el JSON."""


def extraer_datos_reserva(
    texto_usuario: str, nombres_recursos: list[str], ahora_local: datetime
) -> dict | None:
    """Le pide al LLM que extraiga {recurso, inicio, fin} del mensaje del
    usuario como JSON estructurado. El LLM SOLO interpreta lenguaje
    natural aquí -- nunca decide si hay disponibilidad ni escribe en la
    base de datos, eso lo valida recursos_service con los datos reales
    (ver docs/ARQUITECTURA.md). Devuelve None si el LLM no respondió con
    JSON parseable; el llamador debe tratarlo como "no se pudo extraer
    nada" y pedirle al usuario que reformule, no como error fatal."""
    base_url = current_app.config["LLM_BASE_URL"].rstrip("/")
    modelo = current_app.config["LLM_MODEL"]
    url = f"{base_url}/v1/chat/completions"

    system_prompt = PROMPT_EXTRACCION.format(
        nombres_recursos=", ".join(nombres_recursos) if nombres_recursos else "(ninguno cargado)",
        dia_semana=_DIAS_ES[ahora_local.weekday()],
        fecha=ahora_local.strftime("%d/%m/%Y"),
        hora=ahora_local.strftime("%H:%M"),
    )

    payload = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": texto_usuario},
        ],
        "temperature": 0,
        # Algunos modelos locales (ej. lfm2.5, otros "razonadores") gastan
        # buena parte del presupuesto de tokens en un campo separado de
        # cadena de pensamiento ("reasoning_content") antes de escribir la
        # respuesta final -- con un max_tokens bajo se cortan a mitad de
        # razonar y "content" llega vacío o incompleto. Medido en la
        # práctica con lfm2.5-2.6b: un mensaje simple ya gastó 523 tokens
        # solo en razonar antes de escribir un JSON de 3 campos, así que
        # 700 se quedaba corto -- 2000 da margen real.
        "max_tokens": 2000,
    }

    try:
        # El modelo local en uso razona antes de responder y puede tardar
        # bastante más que una respuesta conversacional corta (se han visto
        # 40-50s con max_tokens=2000) -- timeout generoso para no cortarlo
        # a mitad de camino, ya que eso es indistinguible de "no hay red".
        resp = requests.post(url, json=payload, headers=_headers(), timeout=150)
        resp.raise_for_status()
        mensaje = resp.json()["choices"][0]["message"]
        # Fallback: si el modelo dejó "content" vacío pero sí razonó, el
        # JSON a veces queda dentro de "reasoning_content" en vez de
        # "content" -- lo intentamos igual antes de rendirnos.
        contenido = (mensaje.get("content") or mensaje.get("reasoning_content") or "").strip()
    except (requests.RequestException, KeyError, IndexError) as exc:
        current_app.logger.error("Error consultando el LLM para extracción de reserva: %s", exc)
        return None

    inicio_json = contenido.find("{")
    fin_json = contenido.rfind("}")
    if inicio_json == -1 or fin_json == -1:
        current_app.logger.warning("El LLM no devolvió JSON en extracción de reserva: %r", contenido)
        return None

    try:
        datos = json.loads(contenido[inicio_json : fin_json + 1])
    except json.JSONDecodeError:
        current_app.logger.warning("JSON inválido del LLM en extracción de reserva: %r", contenido)
        return None

    if not isinstance(datos, dict):
        return None

    return {
        "recurso": datos.get("recurso") or None,
        "inicio": datos.get("inicio") or None,
        "fin": datos.get("fin") or None,
    }
