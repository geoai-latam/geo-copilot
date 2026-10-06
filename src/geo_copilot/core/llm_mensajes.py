"""Los MENSAJES del LLM: el mensaje y la respuesta comunes, y su formato para cada proveedor
(OpenAI/Azure y Anthropic).

Salió de `llm_client.py` (F4 del plan de calidad: llm_client.py tenía 770 líneas), tal cual.
"""

import json

from pydantic import BaseModel


class LLMMessage(BaseModel):
    """Mensaje para el LLM.

    Campos de tool-calling NATIVO (opcionales, backward-compatible — un mensaje
    plano sin ellos se formatea idéntico a antes):
      - ``tool_calls``: en un turno ``assistant``, las herramientas que pidió.
      - ``tool_call_id`` + ``name``: en un turno ``role="tool"``, a qué llamada
        responde (el resultado observado). Permite el round-trip nativo del
        bucle ReAct en vez del scratchpad de texto.
      - ``provider_blocks``: el contenido CRUDO del turno assistant tal como lo
        devolvió el proveedor (Anthropic: bloques ``thinking`` incluidos). Los
        modelos que razonan exigen recibirlos de vuelta sin tocar en el bucle de
        herramientas; si está, se reenvía tal cual.
    """
    role: str  # system, user, assistant, tool
    content: str = ""
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    provider_blocks: list[dict] | None = None


def format_openai_messages(messages: list["LLMMessage"]) -> list[dict]:
    """``LLMMessage`` → formato chat de OpenAI/Azure (y compatibles).

    Mensajes planos quedan IDÉNTICOS a antes. Soporta el round-trip nativo de
    tool: ``assistant`` con ``tool_calls`` y ``role="tool"`` con ``tool_call_id``.
    """
    out: list[dict] = []
    for m in messages:
        if m.role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m.tool_call_id or "",
                "content": m.content or "",
            })
            continue
        d: dict = {"role": m.role, "content": m.content or ""}
        if m.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.get("id"),
                    "type": tc.get("type", "function"),
                    "function": tc.get("function", {}),
                }
                for tc in m.tool_calls
            ]
        out.append(d)
    return out


def format_anthropic_messages(messages: list["LLMMessage"]) -> tuple[str, list[dict]]:
    """``LLMMessage`` → ``(system, messages)`` formato Anthropic.

    Mapea el round-trip de tool a bloques ``tool_use`` (assistant) / ``tool_result``
    (user). Mensajes planos quedan equivalentes a antes. Varios ``system`` se
    concatenan (antes solo sobrevivía el último) y, como Anthropic exige que la
    conversación empiece por ``user``, un historial que empieza por ``assistant``
    se abre con un turno de usuario mínimo.
    """
    system_parts: list[str] = []
    out: list[dict] = []
    for m in messages:
        if m.role == "system":
            if (m.content or "").strip():
                system_parts.append(m.content)
        elif m.role == "tool":
            out.append({"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": m.tool_call_id or "",
                "content": m.content or "",
            }]})
        elif m.role == "assistant" and m.provider_blocks:
            out.append({"role": "assistant", "content": m.provider_blocks})
        elif m.role == "assistant" and m.tool_calls:
            blocks: list[dict] = []
            if (m.content or "").strip():
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                fn = tc.get("function", {})
                try:
                    inp = json.loads(fn.get("arguments") or "{}")
                    if not isinstance(inp, dict):
                        inp = {}
                except (ValueError, TypeError):
                    inp = {}
                blocks.append({
                    "type": "tool_use", "id": tc.get("id"),
                    "name": fn.get("name"), "input": inp,
                })
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": m.role, "content": m.content or ""})
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": "(continúa la conversación)"})
    return "\n\n".join(system_parts), out


class LLMResponse(BaseModel):
    """Respuesta del LLM.

    ``stop_reason`` normalizado: ``end`` | ``tool_use`` | ``length`` (se acabó el
    tope de salida: el texto o los argumentos pueden venir truncados) |
    ``refusal`` | ``other``. ``provider_blocks``: ver ``LLMMessage``.
    """
    content: str
    model: str
    usage: dict | None = None
    tool_calls: list[dict] | None = None
    stop_reason: str | None = None
    provider_blocks: list[dict] | None = None
