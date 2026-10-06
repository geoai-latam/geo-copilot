"""
Utilidades compartidas del sistema GEO_COPILOT.
"""


import json
import re

# Modelos de razonamiento abiertos (Qwen3, DeepSeek-R1…) escriben su pensamiento en el texto,
# a menudo con llaves: el recorte «primer { … último }» cogía ese pensamiento.
_PENSAMIENTO = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def parse_json_from_llm(content: str, default: dict | None = None) -> dict:
    """
    Parsear JSON de una respuesta LLM.

    Extrae el primer objeto JSON válido encontrado en el contenido,
    útil cuando el LLM responde con texto adicional alrededor del JSON.

    Args:
        content: Texto de respuesta del LLM que puede contener JSON
        default: Valor por defecto si no se puede parsear (default: {})

    Returns:
        Diccionario parseado o valor por defecto

    Example:
        >>> parse_json_from_llm('Here is the result: {"key": "value"} done')
        {'key': 'value'}
    """
    if default is None:
        default = {}

    content = _PENSAMIENTO.sub("", content or "")
    try:
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            datos: dict = json.loads(content[start:end])
            return datos
    except json.JSONDecodeError:
        pass
    # Prosa con llaves alrededor del objeto: el primer objeto JSON que decodifique entero.
    decoder = json.JSONDecoder()
    for i, ch in enumerate(content):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(content, i)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj

    return default


def clean_code_from_markdown(code: str, language: str = "python") -> str:
    """
    Limpiar código que viene envuelto en bloques de markdown.

    Remueve los marcadores ```python o ```sql del inicio y ``` del final
    que los LLMs suelen agregar al generar código.

    Args:
        code: Código que puede tener marcadores markdown
        language: Lenguaje esperado ("python", "sql", etc.)

    Returns:
        Código limpio sin marcadores markdown

    Example:
        >>> clean_code_from_markdown("```python\\nprint('hello')\\n```")
        "print('hello')"
    """
    if not code:
        return code

    cleaned = code.strip()

    # Remover marcador de inicio con lenguaje específico
    lang_marker = f"```{language}"
    if cleaned.startswith(lang_marker):
        cleaned = cleaned[len(lang_marker):]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]

    # Remover marcador de fin
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    return cleaned.strip()
