"""Validación estructural del SQL: vive en `geo_sql_guard.ast` (T5.1, compartida con el
servidor MCP de SQL). Aquí se reexporta y queda `evaluar_en_modo`, que es del núcleo.
"""

from __future__ import annotations

from typing import Any

from geo_sql_guard.ast import *  # noqa: F403
from geo_sql_guard.ast import ResultadoAST, analizar, fijar_srid_de_columnas  # noqa: F401
from geo_sql_guard.crs import check_measurable_crs  # noqa: F401

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)


def evaluar_en_modo(
    sql: str,
    modo: str,
    veredicto_actual: bool,
    tablas_permitidas: set[str] | None = None,
) -> dict[str, Any] | None:
    """Ejecutar el análisis según el modo configurado.

    Devuelve ``None`` si el modo es ``off``. En ``shadow`` sólo registra. En
    ``enforce`` devuelve el resultado para que el llamador bloquee.

    ``veredicto_actual`` es lo que dictaminó el validador de texto; se usa para
    registrar SÓLO las discrepancias, que es la información útil: dónde el AST
    rechazaría algo que hoy pasa (posible rotura) y dónde aceptaría algo que hoy
    se bloquea (posible falso positivo del validador actual).
    """
    modo = (modo or "shadow").strip().lower()
    if modo == "off":
        return None

    resultado = analizar(sql, tablas_permitidas)

    if modo == "shadow":
        if veredicto_actual and not resultado.aceptada:
            # El caso que importa medir: hoy pasa, con AST se rompería.
            logger.warning(
                "[SQL-AST][SOMBRA] RECHAZARÍA un SQL que hoy se acepta. "
                "motivos=%s funciones=%s tablas=%s sql=%r",
                resultado["motivos"], resultado["funciones"],
                resultado["tablas"], sql[:400],
            )
        elif not veredicto_actual and resultado.aceptada:
            logger.info(
                "[SQL-AST][SOMBRA] ACEPTARÍA un SQL que hoy se bloquea. sql=%r",
                sql[:400],
            )
        return None

    return dict(resultado)
