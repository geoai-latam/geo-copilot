"""
Cliente abstracto para interactuar con diferentes proveedores de LLM.

Qué parámetros se envían lo decide el PERFIL del modelo (``model_profile``), no el código de cada
llamada: con un modelo que razona no se manda ``temperature``, el tope de salida sube a su mínimo y,
si el proveedor rechaza un parámetro que no conocíamos, se deja de enviar y se repite una vez.
"""

import contextlib
import time
from collections.abc import AsyncGenerator, Iterator
from contextvars import ContextVar
from typing import Any, cast

from geo_copilot.core.logging import get_logger
from geo_copilot.core.model_profile import ModelProfile, inferir
from geo_copilot.platform.observabilidad import TipoFalloLLM

logger = get_logger(__name__)

# F4: los mensajes y los clientes de cada proveedor viven en sus módulos; se reexportan aquí porque
# el resto del código los importa de `llm_client`.
from geo_copilot.core.llm_mensajes import (  # noqa: F401
    LLMMessage,
    LLMResponse,
    format_anthropic_messages,
    format_openai_messages,
)
from geo_copilot.core.llm_proveedores import (  # noqa: F401
    AnthropicClient,
    AzureOpenAIClient,
    BaseLLMClient,
    OpenAIClient,
    _bloque_crudo,
    _OpenAIStyleClient,
    _uso,
)

PROVEEDORES = ("openai", "azure", "anthropic", "openai_compatible")

#: Lo que el usuario lee cuando el proveedor está al límite de su cuota (429). Antes el router
#: decía «Intenta reformular tu consulta»: falso — la consulta estaba bien (prueba de carga E7.5).
MENSAJE_SATURADO = ("El modelo de lenguaje está al límite de uso del proveedor en este momento. "
                    "Espera unos segundos y vuelve a intentarlo; tu consulta no tiene nada mal.")


#: F7 (auditoría): OpenAI responde 429 `insufficient_quota` cuando se acaba el saldo o la cuota de
#: facturación. Eso no se arregla esperando: decir «espera unos segundos» sería falso.
MENSAJE_CUOTA_AGOTADA = ("El proveedor del modelo de lenguaje rechazó la consulta porque la cuota o el "
                         "saldo de la cuenta están agotados. Esperar no lo resuelve: avisa al administrador "
                         "del servicio. Tu consulta no tiene nada mal.")


def _cadena(exc: BaseException) -> Iterator[BaseException]:
    """La excepción y las que envuelve (raise … from), sin ciclos."""
    visto: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in visto:
        visto.add(id(e))
        yield e
        e = e.__cause__ or e.__context__


def es_cuota_agotada(exc: BaseException) -> bool:
    """¿El proveedor rechazó porque la cuenta no tiene saldo/cuota (`insufficient_quota`)?"""
    for e in _cadena(exc):
        cuerpo = getattr(e, "body", None)
        error = cuerpo.get("error") if isinstance(cuerpo, dict) else None
        codigos = (getattr(e, "code", None),
                   *((cuerpo.get("code"), cuerpo.get("type")) if isinstance(cuerpo, dict) else ()),
                   *((error.get("code"), error.get("type")) if isinstance(error, dict) else ()))
        if "insufficient_quota" in codigos:
            return True
    return False


def es_saturacion(exc: BaseException) -> bool:
    """¿El proveedor rechazó por límite de tasa (429) que se libera esperando? Igual para OpenAI,
    Azure y Anthropic. La cuota/saldo agotado también llega como 429, pero no es saturación."""
    if es_cuota_agotada(exc):
        return False
    return any(type(e).__name__ == "RateLimitError" or getattr(e, "status_code", None) == 429
               for e in _cadena(exc))


def tipo_fallo_llm(exc: BaseException) -> TipoFalloLLM:
    """Hecho para la métrica de fallos: cuota_agotada, saturacion, auth (401/403) o error."""
    if es_cuota_agotada(exc):
        return "cuota_agotada"
    if es_saturacion(exc):
        return "saturacion"
    if any(getattr(e, "status_code", None) in (401, 403) for e in _cadena(exc)):
        return "auth"
    return "error"


def mensaje_fallo_llm(exc: BaseException) -> str | None:
    """Lo que el usuario debe leer cuando el fallo es del PROVEEDOR (no de su consulta); None si no."""
    if es_cuota_agotada(exc):
        return MENSAJE_CUOTA_AGOTADA
    if es_saturacion(exc):
        return MENSAJE_SATURADO
    return None


def _ahora() -> float:
    return time.monotonic()


async def _dormir(segundos: float) -> None:
    """La espera entre reintentos. F7 (auditoría): punto propio (como `_ahora`) para simular el reloj
    SOLO aquí; parchear `asyncio.sleep` alcanza a cualquier bucle del proceso (un uvicorn en otro
    hilo de los tests avanzaba el reloj falso y llenaba sus esperas)."""
    import asyncio

    await asyncio.sleep(segundos)


#: Instante (reloj monotónico) en que vence el turno en curso; None si nadie lo fijó.
_plazo_turno: ContextVar[float | None] = ContextVar("geo_plazo_turno", default=None)
#: Lo que se reserva al final del turno para cerrar con el mensaje de saturación.
MARGEN_PLAZO_S = 5.0


@contextlib.contextmanager
def plazo_turno(segundos: float | None) -> Iterator[None]:
    """F7 (auditoría): el turno vence en `segundos` (el `wait_for` que lo envuelve). Quien crea la
    tarea del turno lo fija ANTES de crearla (la tarea copia el contexto); desde ahí ninguna llamada
    al LLM de ese turno DECIDE esperar otro 429 más allá del plazo, así el usuario lee
    MENSAJE_SATURADO y no un 504 genérico. Solo acota esa decisión: una llamada en curso que tarda
    sin 429 (``llm_request_timeout`` más los reintentos del SDK) no se corta aquí. Sin plazo fijado
    (None) cada llamada solo tiene su ``llm_espera_saturacion_s``."""
    ficha = _plazo_turno.set(_ahora() + segundos if segundos else None)
    try:
        yield
    finally:
        _plazo_turno.reset(ficha)


def _segundos_pedidos(exc: BaseException) -> float | None:
    """El Retry-After del proveedor (en ms o s), si lo mandó."""
    cabeceras = getattr(getattr(exc, "response", None), "headers", None) or {}
    for clave, factor in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        try:
            valor = float(cast(Any, cabeceras.get(clave)))
        except (TypeError, ValueError):
            continue
        if valor > 0:
            return valor * factor
    return None


class LLMClient:
    """
    Factory para crear el cliente LLM apropiado según configuración.

    ``openai_compatible`` (alias ``local``): cualquier servidor con la API de chat de OpenAI en
    ``base_url`` (vLLM, Ollama, LM Studio, un gateway corporativo).
    """

    def __init__(
        self,
        provider: str = "openai",
        api_key: str = "",
        model: str = "gpt-4.1-mini",
        endpoint: str = "",
        api_version: str = "2024-12-01-preview",
        base_url: str = "",
        profile_override: str | None = None,
    ):
        self.provider = "openai_compatible" if provider == "local" else provider
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
        self.api_version = api_version
        self.base_url = base_url
        self._profile = inferir(self.provider, model, profile_override)
        self._client: BaseLLMClient | None = None
        from geo_copilot.platform import observabilidad

        observabilidad.preparar_series_llm(model)

    @property
    def profile(self) -> ModelProfile:
        return self._client.profile if self._client is not None else self._profile

    def _get_client(self) -> BaseLLMClient:
        if self._client is None:
            if self.provider == "openai":
                self._client = OpenAIClient(self.api_key, self.model, base_url=self.base_url or None,
                                            profile=self._profile)
            elif self.provider == "openai_compatible":
                if not self.base_url:
                    raise ValueError("LLM_PROVIDER=openai_compatible necesita LLM_BASE_URL")
                self._client = OpenAIClient(self.api_key, self.model, base_url=self.base_url, profile=self._profile)
            elif self.provider == "anthropic":
                self._client = AnthropicClient(self.api_key, self.model, profile=self._profile)
            elif self.provider == "azure":
                self._client = AzureOpenAIClient(
                    self.api_key,
                    self.endpoint,
                    self.model,
                    self.api_version,
                    profile=self._profile,
                )
            else:
                raise ValueError(f"Provider no soportado: {self.provider} (válidos: {', '.join(PROVEEDORES)})")
        return self._client

    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        """Enviar chat al LLM."""
        from geo_copilot.platform import observabilidad

        inicio = time.perf_counter()
        try:
            # F7 (S7.3): cada llamada al LLM es un span (modelo, tokens) y alimenta el coste por modelo
            with observabilidad.span("llm.chat", **{"gen_ai.system": self.provider,
                                                    "gen_ai.request.model": self.model,
                                                    "geo.herramientas": len(tools or [])}) as s:
                respuesta = await self._con_paciencia(
                    lambda: self._get_client().chat(messages, tools, temperature, max_tokens,
                                                    tool_choice=tool_choice),
                    self.model,
                )
                uso = respuesta.usage or {}
                s.set_attribute("gen_ai.usage.input_tokens", int(uso.get("prompt_tokens") or 0))
                s.set_attribute("gen_ai.usage.output_tokens", int(uso.get("completion_tokens") or 0))
        except Exception as exc:
            # F7 (auditoría): la caída del proveedor también es una señal (antes solo se medía el éxito)
            observabilidad.registrar_llm_fallo(self.model, tipo_fallo_llm(exc))
            raise
        observabilidad.registrar_llm(self.model, time.perf_counter() - inicio, respuesta.usage)
        return cast(LLMResponse, respuesta)

    @staticmethod
    async def _con_paciencia(llamar, modelo: str = ""):
        """F7 (E7.5): ante un 429 de CUOTA, esperar a que la ventana del proveedor se libere.

        Los reintentos del SDK esperan lo que pide el proveedor (Azure: 1–3 s) y se rinden; con la
        cuota por minuto agotada por otros usuarios, eso tumbaba el 20 % de las consultas con 5
        usuarios. Aquí se sigue esperando (lo que pida el proveedor, o 2→4→8… s con jitter) hasta
        ``llm_espera_saturacion_s``; agotado el presupuesto, el error sube como siempre.

        F7 (auditoría): el presupuesto se mide con el RELOJ desde la primera llamada (antes solo se
        sumaban los sueños propios y los reintentos del SDK dentro de cada intento lo triplicaban) y,
        si el turno fijó su plazo (``plazo_turno``), no se pasa de él menos ``MARGEN_PLAZO_S``. Es un
        presupuesto APROXIMADO: decide si se reintenta, no corta un intento en curso. El primero (con
        los reintentos del SDK dentro, que siguen Retry-After de hasta 60 s) no está acotado, y el
        siguiente se estima tan largo como el último.
        """
        import random

        from geo_copilot.core.config import get_settings
        from geo_copilot.platform import observabilidad

        inicio = _ahora()
        limite = inicio + get_settings().llm_espera_saturacion_s
        plazo = _plazo_turno.get()
        if plazo is not None:
            limite = min(limite, plazo - MARGEN_PLAZO_S)
        intento = 0
        dormido = 0.0
        while True:
            antes = _ahora()
            try:
                return await llamar()
            except Exception as exc:  # solo se retiene la saturación; lo demás sube tal cual
                if not es_saturacion(exc):
                    raise
                intento += 1
                ahora = _ahora()
                pedida = _segundos_pedidos(exc)
                # Jitter también sobre lo pedido, siempre hacia arriba (el proveedor pide un mínimo): si
                # no, todas las consultas rechazadas en la misma ventana vuelven en el mismo instante.
                espera = (pedida * random.uniform(1.0, 1.25) if pedida is not None
                          else min(2 ** intento, 20) * random.uniform(0.7, 1.3))
                # El próximo intento tardará lo que tardó este (con los reintentos del SDK dentro). Lo
                # transcurrido nunca es menos que lo dormido: el bucle termina aunque el reloj no avance.
                transcurrido = max(ahora - inicio, dormido)
                if inicio + transcurrido + espera + (ahora - antes) > limite:
                    raise
                observabilidad.registrar_llm_reintento(modelo)
                logger.warning(f"[llm] proveedor saturado (429); reintento {intento} en {espera:.1f} s")
                await _dormir(espera)
                dormido += espera

    async def stream(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.1,
        max_tokens: int = 4096
    ) -> AsyncGenerator[str, None]:
        """Stream de respuesta."""
        async for chunk in self._get_client().stream(messages, temperature, max_tokens):
            yield chunk

    @classmethod
    def from_settings(cls, settings) -> "LLMClient":
        """Crear cliente desde configuración.

        Las API keys ahora viven como ``SecretStr``; las desempaquetamos
        sólo aquí, justo antes de instanciar el SDK. Coincide con el
        default de ``openai_api_version`` (config.py) para no tener dos
        fuentes de verdad (B6).
        """
        api_key = ""
        endpoint = ""
        provider = settings.llm_provider

        if provider == "openai":
            api_key = settings.openai_api_key.get_secret_value()
        elif provider == "anthropic":
            api_key = settings.anthropic_api_key.get_secret_value()
        elif provider == "azure":
            api_key = settings.azure_openai_api_key.get_secret_value()
            endpoint = settings.azure_openai_endpoint
        elif provider in ("openai_compatible", "local"):
            api_key = settings.llm_api_key.get_secret_value()

        return cls(
            provider=provider,
            api_key=api_key,
            model=settings.llm_model,
            endpoint=endpoint,
            api_version=settings.openai_api_version,
            base_url=settings.llm_base_url,
            profile_override=settings.llm_model_profile or None,
        )
