"""Ejecución de tools con tiempo máximo y errores honestos (extraído de imagery-mcp).

Las tools de un servidor geo suelen ser IO bloqueante (GDAL, httpx, drivers).
Un hilo no se puede matar: al vencer, el cliente recibe un error honesto y el
hilo termina en segundo plano; el pool acotado hace de contrapresión.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

logger = logging.getLogger("geo_mcp_kit")

#: Códigos estables de error (contrato `rt/1` de Terranova, spec 01 §6.3): quien consume el
#: servidor decide por el código, no leyendo el mensaje.
CODIGOS = frozenset({
    "parametro_invalido", "zona_demasiado_grande", "no_autenticado", "sin_permiso",
    "licencia_no_habilitada", "no_existe", "saldo_agotado", "limite_tasa", "fuente_no_disponible",
    "fuente_respuesta_invalida", "fuente_lenta", "error_interno",
})
CLAVES_ERROR = frozenset({"error", "codigo", "reintentar_en_s"})


def error(mensaje: str, codigo: str, reintentar_en_s: int | None = None) -> dict:
    """`{"error", "codigo"[, "reintentar_en_s"]}`: el resultado de una tool que no pudo."""
    if codigo not in CODIGOS:
        raise ValueError(f"código de error desconocido: {codigo}")
    return {"error": mensaje, "codigo": codigo, **({"reintentar_en_s": reintentar_en_s} if reintentar_en_s else {})}


def es_error(sc: Any) -> bool:
    """Un resultado de error: `{"error": …}` y, como mucho, `codigo` y `reintentar_en_s`."""
    return isinstance(sc, dict) and "error" in sc and set(sc) <= CLAVES_ERROR


class ToolRunner:
    def __init__(
        self,
        *,
        timeout_s: float,
        service: str,
        expected_errors: tuple[type[Exception], ...] = (),
        max_workers: int = 4,
    ) -> None:
        self.timeout_s = timeout_s
        self.service = service
        self.expected_errors = expected_errors
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"{service}-tool")

    def run(self, fn: Callable[..., dict], *args: Any, timeout_s: float | None = None, **kwargs: Any) -> dict:
        """Resultado de `fn`, o `{"error": ...}` con un mensaje apto para el LLM/usuario."""
        import contextvars

        from geo_mcp_kit.observabilidad import span

        limit = self.timeout_s if timeout_s is None else timeout_s

        def _con_span() -> dict:
            with span(f"tool {getattr(fn, '__name__', 'tool')}", **{"geo.servicio": self.service}):
                return fn(*args, **kwargs)

        try:
            # el hilo hereda el contexto (la traza en curso): su span cuelga de la petición MCP
            return self._pool.submit(contextvars.copy_context().run, _con_span).result(timeout=limit)
        except FutureTimeoutError:
            logger.warning("tool %s superó %.0f s", getattr(fn, "__name__", fn), limit)
            return error(
                f"La operación superó el tiempo máximo de {limit:.0f} s. "
                "Prueba con un área más pequeña o un rango más corto.",
                "fuente_lenta",
            )
        except self.expected_errors as exc:
            # la excepción puede declarar su código (`codigo = "fuente_no_disponible"`); si no, es de
            # la petición: el mensaje dice cómo corregirla
            return error(str(exc), getattr(exc, "codigo", None) or "parametro_invalido",
                         getattr(exc, "reintentar_en_s", None))
        except Exception as exc:
            externo = fallo_de_proveedor(exc, self.service)
            if externo is not None:
                logger.warning("tool %s: %s", getattr(fn, "__name__", fn), externo)
                return error(externo, *_codigo_de_proveedor(exc))
            logger.exception("fallo no tipado en tool")
            return error(f"Fallo interno del servicio {self.service}: {type(exc).__name__}", "error_interno")


def _codigo_de_proveedor(exc: BaseException) -> tuple[str, int | None]:
    """Caído, saturado o sin red: `fuente_no_disponible` (reintentar sirve). Otro 4xx: la fuente
    no aceptó lo que se le pidió o cambió (`fuente_respuesta_invalida`)."""
    codigo = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(codigo, int) and 400 <= codigo < 500 and codigo != 429:
        return "fuente_respuesta_invalida", None
    return "fuente_no_disponible", 5


def fallo_de_proveedor(exc: BaseException, service: str) -> str | None:
    """Mensaje honesto si el fallo es de un servicio EXTERNO (respuesta HTTP de error o red caída).

    F7 (T7.6): un 504 del token SAS de Planetary Computer salía como «Fallo interno del servicio
    imagery: HTTPStatusError» — el usuario leía un fallo nuestro y no sabía que reintentar sirve.
    Sin importar httpx (el kit no depende de él): se reconoce por su forma.
    """
    respuesta = getattr(exc, "response", None)
    codigo = getattr(respuesta, "status_code", None)
    peticion = getattr(exc, "request", None) if respuesta is None else getattr(respuesta, "request", None)
    host = getattr(getattr(peticion, "url", None), "host", None) or "un proveedor externo"
    if isinstance(codigo, int) and codigo >= 400:
        pasajero = codigo >= 500 or codigo == 429
        return (f"El proveedor externo {host} respondió {codigo}: no es un fallo de {service}. "
                + ("Suele ser pasajero: reintenta en unos segundos." if pasajero
                   else "Revisa la petición o el acceso a ese proveedor."))
    if type(exc).__module__.split(".")[0] == "httpx" and peticion is not None:
        return (f"No se pudo contactar con {host} ({type(exc).__name__}): no es un fallo de {service}. "
                "Suele ser pasajero: reintenta en unos segundos.")
    return None


def herramientas_en_hilo(mcp: Any) -> Any:
    """Las tools síncronas del servidor corren en un hilo, no en el bucle de eventos.

    FastMCP llama una tool `def` dentro del bucle: mientras espera a la fuente (`runner.run`
    bloquea hasta el resultado), el servidor no atiende nada más, ni otras llamadas ni el
    arranque de sesiones. Una fuente lenta tumbaba el servicio entero (Terranova F4.2: la ficha
    de un lugar pide 8 capas a la vez a arcgis-mcp). Se llama una vez, después de registrar las
    tools; las funciones del módulo siguen siendo síncronas (las pruebas las llaman directo).
    """
    import asyncio
    import functools

    for tool in mcp._tool_manager.list_tools():
        if tool.is_async:
            continue

        def envolver(fn: Callable[..., Any]) -> Callable[..., Any]:
            @functools.wraps(fn)
            async def en_hilo(*args: Any, **kwargs: Any) -> Any:
                # `to_thread` copia el contexto: la traza en curso sigue en el hilo
                return await asyncio.to_thread(fn, *args, **kwargs)

            return en_hilo

        tool.fn = envolver(tool.fn)
        tool.is_async = True
    return mcp
