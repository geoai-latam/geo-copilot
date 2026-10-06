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
            return {"error": (
                f"La operación superó el tiempo máximo de {limit:.0f} s. "
                "Prueba con un área más pequeña o un rango más corto."
            )}
        except self.expected_errors as exc:
            return {"error": str(exc)}
        except Exception as exc:
            externo = fallo_de_proveedor(exc, self.service)
            if externo is not None:
                logger.warning("tool %s: %s", getattr(fn, "__name__", fn), externo)
                return {"error": externo}
            logger.exception("fallo no tipado en tool")
            return {"error": f"Fallo interno del servicio {self.service}: {type(exc).__name__}"}


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
