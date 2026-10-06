"""Los CLIENTES de cada proveedor (OpenAI, Azure OpenAI, Anthropic) sobre una base común que adapta
el perfil del modelo si el proveedor rechaza un parámetro.

Salió de `llm_client.py` (F4 del plan de calidad: llm_client.py tenía 770 líneas), tal cual.
"""

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any, cast

from geo_copilot.core.llm_mensajes import (
    LLMMessage,
    LLMResponse,
    format_anthropic_messages,
    format_openai_messages,
)
from geo_copilot.core.logging import get_logger
from geo_copilot.core.model_profile import ModelProfile, adaptar, inferir, salida

logger = get_logger("geo_copilot.core.llm_client")

_STOP_OPENAI = {"stop": "end", "tool_calls": "tool_use", "function_call": "tool_use",
                "length": "length", "content_filter": "refusal"}
_STOP_ANTHROPIC = {"end_turn": "end", "stop_sequence": "end", "tool_use": "tool_use",
                   "max_tokens": "length", "refusal": "refusal", "pause_turn": "other"}


class BaseLLMClient(ABC):
    """Cliente base abstracto para LLMs."""

    profile: ModelProfile = ModelProfile()

    @abstractmethod
    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        """Enviar mensajes al LLM y obtener respuesta.

        ``tool_choice`` (A3, structured outputs): None → 'auto' cuando hay
        tools (comportamiento clásico); un dict formato OpenAI
        ``{"type": "function", "function": {"name": ...}}`` FUERZA esa
        función — los backends lo traducen a su dialecto (o, si el modelo no
        admite forzar, se degrada a 'auto' y ``structured_call`` lo pide por
        instrucción).
        """
        pass

    @abstractmethod
    def stream(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.1,
        max_tokens: int = 4096
    ) -> AsyncGenerator[str, None]:
        """Stream de respuesta del LLM (un generador asíncrono en cada proveedor)."""
        raise NotImplementedError

    async def _con_adaptacion(self, llamar):
        """Llama; si el proveedor rechaza un parámetro que el perfil puede apagar, repite UNA vez."""
        try:
            return await llamar()
        except Exception as exc:
            nuevo = adaptar(self.profile, exc)
            if nuevo is None:
                raise
            self.profile = nuevo
            return await llamar()


def _uso(usage: Any, entrada: str, salida_: str) -> dict | None:
    """Tokens usados; un servidor compatible puede no devolver ``usage`` (antes: AttributeError)."""
    if usage is None:
        return None
    pin = getattr(usage, entrada, None) or 0
    pout = getattr(usage, salida_, None) or 0
    return {"prompt_tokens": pin, "completion_tokens": pout, "total_tokens": pin + pout}


class _OpenAIStyleClient(BaseLLMClient):
    """OpenAI, Azure OpenAI y cualquier servidor compatible con la API de chat de OpenAI
    (vLLM, Ollama, LM Studio, gateways): mismo protocolo, distinto constructor del SDK."""

    model: str
    _client: Any = None

    async def _get_client(self):  # pragma: no cover — cada subclase construye su SDK
        raise NotImplementedError

    def _kwargs(self, messages, tools, temperature, max_tokens, tool_choice) -> dict:
        p = self.profile
        kwargs: dict = {"model": self.model, "messages": format_openai_messages(messages),
                        p.max_tokens_param: salida(p, max_tokens)}
        if p.supports_temperature and temperature is not None:
            kwargs["temperature"] = temperature
        if tools:
            kwargs["tools"] = tools
            forzado = isinstance(tool_choice, dict)
            kwargs["tool_choice"] = tool_choice if (tool_choice and (not forzado or p.forced_tool_choice)) else "auto"
            if p.parallel_tool_control:
                # El bucle ejecuta UNA herramienta por paso: que el modelo no reparta el plan en varias.
                kwargs["parallel_tool_calls"] = False
        return kwargs

    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        client = await self._get_client()

        async def llamar():
            return await client.chat.completions.create(
                **self._kwargs(messages, tools, temperature, max_tokens, tool_choice))

        try:
            response = await self._con_adaptacion(llamar)
        except Exception as e:
            logger.error(f"[llm:{self.model}] API error: {type(e).__name__}: {e}")
            raise

        choice = response.choices[0]
        tool_calls = None
        if choice.message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                }
                for tc in choice.message.tool_calls
            ]

        return LLMResponse(
            content=choice.message.content or "",
            model=getattr(response, "model", None) or self.model,
            usage=_uso(getattr(response, "usage", None), "prompt_tokens", "completion_tokens"),
            tool_calls=tool_calls,
            stop_reason=_STOP_OPENAI.get(getattr(choice, "finish_reason", None) or "", "other"),
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.1,
        max_tokens: int = 4096
    ) -> AsyncGenerator[str, None]:
        client = await self._get_client()
        stream = await client.chat.completions.create(
            **self._kwargs(messages, None, temperature, max_tokens, None), stream=True)

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class OpenAIClient(_OpenAIStyleClient):
    """Cliente para OpenAI y servidores compatibles (``base_url``)."""

    def __init__(self, api_key: str, model: str = "gpt-4.1-mini", base_url: str | None = None,
                 profile: ModelProfile | None = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url or None
        self.profile = profile or inferir("openai", model)
        self._client = None

    async def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            from geo_copilot.core.config import get_settings
            s = get_settings()
            self._client = AsyncOpenAI(
                # Un servidor local no pide clave, pero el SDK exige una no vacía.
                api_key=self.api_key or ("no-key" if self.base_url else ""),
                base_url=self.base_url,
                timeout=s.llm_request_timeout,
                max_retries=s.llm_max_retries,
            )
        return self._client


class AzureOpenAIClient(_OpenAIStyleClient):
    """Cliente para Azure OpenAI. OJO: ``model`` es el nombre del DEPLOYMENT; qué modelo hay
    detrás no se deduce de él → declárese con ``LLM_MODEL_PROFILE`` si razona."""

    def __init__(
        self,
        api_key: str,
        endpoint: str,
        model: str = "gpt-4.1-mini",
        api_version: str = "2024-12-01-preview",
        profile: ModelProfile | None = None,
    ):
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.api_version = api_version
        self.profile = profile or inferir("azure", model)
        self._client = None

    async def _get_client(self):
        if self._client is None:
            from openai import AsyncAzureOpenAI

            from geo_copilot.core.config import get_settings
            s = get_settings()
            logger.debug(f"[Azure] Creating client: endpoint={self.endpoint}, model={self.model}, api_version={self.api_version}")
            self._client = AsyncAzureOpenAI(
                api_key=self.api_key,
                api_version=self.api_version,
                azure_endpoint=self.endpoint,
                timeout=s.llm_request_timeout,
                max_retries=s.llm_max_retries,
            )
        return self._client


def _bloque_crudo(block: Any) -> dict:
    if hasattr(block, "model_dump"):
        return cast(dict, block.model_dump(exclude_none=True))
    return dict(block) if isinstance(block, dict) else {"type": getattr(block, "type", "text"),
                                                         "text": getattr(block, "text", "")}


class AnthropicClient(BaseLLMClient):
    """Cliente para Anthropic Claude."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-5", profile: ModelProfile | None = None):
        self.api_key = api_key
        self.model = model
        self.profile = profile or inferir("anthropic", model)
        self._client = None

    async def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic

            from geo_copilot.core.config import get_settings
            s = get_settings()
            self._client = AsyncAnthropic(
                api_key=self.api_key,
                timeout=s.llm_request_timeout,
                max_retries=s.llm_max_retries,
            )
        return self._client

    def _kwargs(self, messages, tools, temperature, max_tokens, tool_choice) -> dict:
        p = self.profile
        # Separar system message + mapear round-trip de tool a bloques Anthropic.
        system_msg, chat_messages = format_anthropic_messages(messages)
        kwargs: dict = {
            "model": self.model,
            "max_tokens": salida(p, max_tokens),
            "messages": chat_messages,
        }
        # B6: antes se omitía → Anthropic ignoraba la temperatura. Los Claude que razonan la rechazan.
        if p.supports_temperature and temperature is not None:
            kwargs["temperature"] = temperature
        if system_msg:
            kwargs["system"] = system_msg

        if tools:
            # Convertir formato OpenAI a Anthropic
            anthropic_tools = []
            for tool in tools:
                if tool.get("type") == "function":
                    func = tool["function"]
                    anthropic_tools.append({
                        "name": func["name"],
                        "description": func.get("description", ""),
                        "input_schema": func.get("parameters", {})
                    })
            kwargs["tools"] = anthropic_tools
            choice: dict = {"type": "auto"}
            # A3: traducir el forzado de función al dialecto Anthropic (si el modelo lo admite).
            if isinstance(tool_choice, dict) and tool_choice.get("type") == "function" and p.forced_tool_choice:
                forced = (tool_choice.get("function") or {}).get("name")
                if forced:
                    choice = {"type": "tool", "name": forced}
            if p.parallel_tool_control:
                choice["disable_parallel_tool_use"] = True
            kwargs["tool_choice"] = choice
        return kwargs

    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        client = await self._get_client()

        async def llamar():
            return await client.messages.create(**self._kwargs(messages, tools, temperature, max_tokens, tool_choice))

        response = await self._con_adaptacion(llamar)

        content = ""
        tool_calls = None
        razona = False

        for block in response.content:
            if block.type == "text":
                # B6: concatenar (``+=``). Con múltiples bloques de texto
                # (común al intercalar tool_use) el ``=`` previo perdía
                # todo menos el último bloque.
                content += block.text
            elif block.type == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append({
                    "id": block.id,
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input)
                    }
                })
            elif block.type in ("thinking", "redacted_thinking"):
                razona = True

        return LLMResponse(
            content=content,
            model=self.model,
            usage=_uso(getattr(response, "usage", None), "input_tokens", "output_tokens"),
            tool_calls=tool_calls,
            stop_reason=_STOP_ANTHROPIC.get(getattr(response, "stop_reason", None) or "", "other"),
            # Solo hace falta devolverlos cuando hubo pensamiento (bucle de herramientas).
            provider_blocks=[_bloque_crudo(b) for b in response.content] if razona else None,
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.1,
        max_tokens: int = 4096
    ) -> AsyncGenerator[str, None]:
        client = await self._get_client()
        kwargs = self._kwargs(messages, None, temperature, max_tokens, None)
        async with client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
