"""ScriptedLLM (Fase 4 / F4.T1) — doble de test para bucles no-deterministas.

ESTRATEGIA DE TEST PARA EL BUCLE ReAct (prerrequisito de F4.4):

El bucle ReAct (decide herramienta → observa resultado → vuelve a decidir) es
guiado por un LLM, así que su comportamiento es NO determinista: el mismo input
puede producir distintas secuencias de tool-calls. No se puede testear con un LLM
real (frágil, lento, caro, no reproducible). La estrategia:

  1. **Doble de LLM guionizado** (``ScriptedLLM``): devuelve una SECUENCIA fija de
     respuestas (algunas con ``tool_calls``, una final). Así el test fija el
     "plan" del agente y verifica que el bucle lo EJECUTA correctamente
     (despacha la herramienta, alimenta el resultado, corta al final).
  2. **Aserciones sobre la ESTRUCTURA, no el texto**: se verifica la secuencia de
     decisiones (qué herramientas, en qué orden, con qué resultado), los
     invariantes (termina en final/error; circuit-break al tope), y los efectos
     en el estado — NO strings exactos del LLM (que el doble controla, pero el
     real no).
  3. **Casos límite deterministas**: script que nunca termina → debe cortar por
     el circuit-breaker (F4.3); herramienta inexistente → error manejado;
     argumentos JSON inválidos → no crashea.

``ScriptedLLM`` implementa la interfaz ``chat()`` de ``BaseLLMClient`` y registra
cada llamada (``messages``/``tools``) para aserciones.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from geo_copilot.core.llm_client import LLMMessage, LLMResponse

_TOOLCALL_MODEL = "scripted-llm"


def tool_call_response(
    tool: str, args: dict | None = None, *, call_id: str = "call_1", content: str = ""
) -> LLMResponse:
    """Respuesta que pide invocar ``tool`` con ``args`` (formato OpenAI)."""
    return LLMResponse(
        content=content,
        model=_TOOLCALL_MODEL,
        tool_calls=[{
            "id": call_id,
            "function": {"name": tool, "arguments": json.dumps(args or {})},
        }],
    )


def final_response(text: str) -> LLMResponse:
    """Respuesta final (sin tool_calls): termina el bucle."""
    return LLMResponse(content=text, model=_TOOLCALL_MODEL)


class ScriptedLLM:
    """LLM falso que reproduce una secuencia fija de respuestas.

    Cada ``chat()`` consume la siguiente entrada del guion. Si el guion se agota
    devuelve por defecto una respuesta final vacía (para no colgar un bucle mal
    escrito; los tests pueden afirmar ``exhausted``).
    """

    def __init__(self, script: list[LLMResponse] | None = None):
        self._script: list[LLMResponse] = list(script or [])
        self._idx = 0
        self.calls: list[dict] = []  # historial de (messages, tools) por llamada

    @property
    def exhausted(self) -> bool:
        return self._idx >= len(self._script)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        self.calls.append({
            "messages": list(messages), "tools": tools, "tool_choice": tool_choice,
        })
        if self.exhausted:
            return LLMResponse(content="", model=_TOOLCALL_MODEL)
        resp = self._script[self._idx]
        self._idx += 1
        return resp

    async def stream(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        resp = await self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        yield resp.content


# ---------------------------------------------------------------------------
# Helpers de aserción sobre la traza de decisiones (F4.T3)
# ---------------------------------------------------------------------------
def decision_kinds(trace: list[dict]) -> list[Any]:
    """Secuencia de ``kind`` de una lista de decisiones (DecisionTrace.to_list)."""
    return [d.get("kind") for d in trace]


def tools_called(trace: list[dict]) -> list[Any]:
    """Herramientas invocadas, en orden (entradas kind=tool_call)."""
    return [d.get("tool") for d in trace if d.get("kind") == "tool_call"]


def ends_terminal(trace: list[dict]) -> bool:
    """Invariante: la traza termina en una decisión terminal (final/error)."""
    return bool(trace) and trace[-1].get("kind") in ("final", "error")
