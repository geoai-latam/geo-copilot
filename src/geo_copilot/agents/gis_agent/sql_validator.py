"""
Validador de SQL para seguridad y optimización.

Verifica que las consultas SQL sean seguras y sugiere optimizaciones.
"""

import re
from collections.abc import Callable
from typing import Any

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)

# F4: el análisis (optimizar, complejidad, tablas, explicación) vive en su módulo (mixin).
from geo_copilot.agents.gis_agent.sql_analisis import SQLAnalisisMixin


def quote_ident(ident: str) -> str:
    """Cita un identificador Postgres de forma segura (S5).

    Envuelve en comillas dobles y escapa cualquier comilla doble interna.
    Evita inyección vía nombres de schema/tabla/columna del semantic layer
    que contengan caracteres hostiles, espacios o mayúsculas.

    Raises:
        ValueError: si el identificador es vacío o no es str.
    """
    if not isinstance(ident, str) or not ident.strip():
        raise ValueError("identificador vacío o no-string")
    return '"' + ident.replace('"', '""') + '"'


def quote_qualified(schema: str, table: str) -> str:
    """Cita una referencia ``schema.table`` (S5)."""
    return f"{quote_ident(schema)}.{quote_ident(table)}"


def _legible(patron: str) -> str:
    """El motivo en palabras (V5 FH.9: el corrector recibía la regex del UNION y repetía el
    UNION ALL tres veces). Qué hacer en su lugar lo decide el LLM."""
    if "UNION" in patron:
        return "UNION / UNION ALL entre SELECT (no se permite combinar consultas así)"
    limpio = re.sub(r"\\[bs][*+]?|[()?:\\]", " ", patron)
    return " ".join(limpio.split()) or patron


class SQLValidator(SQLAnalisisMixin):
    """
    Valida consultas SQL para seguridad y rendimiento.

    Verificaciones:
    - Operaciones prohibidas (INSERT, UPDATE, DELETE, DROP, etc.)
    - Inyección SQL
    - Límites de resultados
    - Optimizaciones espaciales
    """

    # Patrones de operaciones prohibidas
    FORBIDDEN_PATTERNS = [
        r"\bINSERT\b",
        r"\bUPDATE\b",
        r"\bDELETE\b",
        r"\bDROP\b",
        r"\bTRUNCATE\b",
        r"\bALTER\b",
        r"\bCREATE\b",
        r"\bGRANT\b",
        r"\bREVOKE\b",
        r"\bEXECUTE\b",
        r"\bEXEC\b",
        r";\s*--",  # Comentario después de punto y coma
        # S6: UNION injection — antes solo se bloqueaba la variante con ALL,
        # dejando ``UNION SELECT`` como bypass trivial.
        r"UNION\s+(?:ALL\s+)?SELECT",
        r"INTO\s+OUTFILE",
        r"INTO\s+DUMPFILE",
        # S6: ``SELECT ... INTO tabla`` crea tablas en Postgres.
        r"\bINTO\b",
        r"LOAD_FILE",
        r"pg_sleep",
        r"benchmark\s*\(",
        # S6: sinks específicos de Postgres ausentes antes.
        r"\bCOPY\b",            # COPY ... TO/FROM (lectura/escritura de ficheros)
        r"\bCALL\b",            # invocación de procedimientos
        r"\bdblink\b",          # conexiones salientes
        r"\blo_import\b",       # large object import desde fichero
        r"\blo_export\b",
        r"\bpg_read_file\b",
        r"\bpg_read_binary_file\b",
        r"\bpg_ls_dir\b",
        r"\bpg_stat_file\b",
    ]

    # Patrones de advertencia (solo patrones regex válidos sin look-behind variable)
    WARNING_PATTERNS = [
        (r"SELECT\s+\*", "Usar SELECT * puede retornar demasiados campos"),
        (r"ST_Distance\s*\([^)]+\)\s*<", "Usar ST_DWithin en lugar de ST_Distance < X para usar índices"),
    ]


    def __init__(
        self,
        max_limit: int = 1000,
        allowed_tables: Callable[[], set[str]] | None = None,
    ):
        """
        Inicializar validador.

        Args:
            max_limit: Límite máximo de resultados permitido
            allowed_tables: S0.2. Función que devuelve las tablas legibles
                (`esquema.tabla` en minúsculas). Se consulta en cada validación
                porque el semantic layer se hidrata DESPUÉS de construir el
                agente. ``None``: sin allowlist de tablas.
        """
        self.max_limit = max_limit
        self.allowed_tables = allowed_tables

    @staticmethod
    def _strip_comments(sql: str) -> str:  # noqa: C901, PLR0912, PLR0915
        """Quita comentarios SQL para que la denylist no se evada con ellos.

        S6: ``DEL/**/ETE`` o ``UNION/**/SELECT`` burlaban los patrones
        ``\\bWORD\\b``. Removemos comentarios de bloque ``/* */`` y de línea
        ``-- ...`` antes de escanear operaciones prohibidas.

        R0.7a (auditoría 2026-07-26, AUD-04): esto se hacía con dos regex
        CIEGAS A LOS LITERALES DE CADENA::

            re.sub(r"/\\*.*?\\*/", " ", sql)      # borraba desde un '/*' entre comillas
            re.sub(r"--[^\\n]*", " ", sql)        # borraba desde un '--' entre comillas

        En PostgreSQL un ``--`` o ``/*`` DENTRO de comillas simples no abre
        comentario, así que todo lo que iba detrás quedaba invisible para la
        denylist pero **sí se ejecutaba**. Verificado ejecutando el validador
        real: ``SELECT '/*' AS a, pg_read_file('/etc/passwd') AS b`` daba
        ``is_valid=True``, igual que un ``UNION SELECT rolpassword FROM
        pg_authid`` precedido de ``'--'``.

        Ahora se recorre la cadena carácter a carácter respetando literales
        (``'...'`` con ``''`` escapado), identificadores entrecomillados
        (``"..."``) y dollar-quoting (``$tag$...$tag$``). Sólo se eliminan los
        comentarios que aparecen FUERA de ellos. Los literales se conservan
        intactos para no alterar lo que la denylist ve.

        NOTA DE ALCANCE: esto cierra el bypass verificado, pero NO convierte a
        una denylist sobre texto en un control robusto. El fix de fondo es
        validar el AST (``sqlglot``/``pglast``) — ver la tarea R0.7.
        """
        salida: list[str] = []
        i, n = 0, len(sql)
        while i < n:
            c = sql[i]

            # --- literal de cadena: '...' (con '' como comilla escapada) -----
            if c == "'":
                salida.append(c)
                i += 1
                while i < n:
                    salida.append(sql[i])
                    if sql[i] == "'":
                        if i + 1 < n and sql[i + 1] == "'":   # '' escapada
                            salida.append(sql[i + 1])
                            i += 2
                            continue
                        i += 1
                        break
                    i += 1
                continue

            # --- identificador entrecomillado: "..." ------------------------
            if c == '"':
                salida.append(c)
                i += 1
                while i < n:
                    salida.append(sql[i])
                    if sql[i] == '"':
                        if i + 1 < n and sql[i + 1] == '"':
                            salida.append(sql[i + 1])
                            i += 2
                            continue
                        i += 1
                        break
                    i += 1
                continue

            # --- dollar-quoting: $tag$ ... $tag$ ----------------------------
            if c == "$":
                m = re.match(r"\$[A-Za-z_]\w*\$|\$\$", sql[i:])
                if m:
                    tag = m.group(0)
                    fin = sql.find(tag, i + len(tag))
                    fin = n if fin == -1 else fin + len(tag)
                    salida.append(sql[i:fin])
                    i = fin
                    continue

            # --- comentario de línea ----------------------------------------
            if c == "-" and i + 1 < n and sql[i + 1] == "-":
                salto = sql.find("\n", i)
                i = n if salto == -1 else salto
                salida.append(" ")
                continue

            # --- comentario de bloque (anidable en PostgreSQL) --------------
            if c == "/" and i + 1 < n and sql[i + 1] == "*":
                profundidad, i = 1, i + 2
                while i < n and profundidad:
                    if sql.startswith("/*", i):
                        profundidad += 1
                        i += 2
                    elif sql.startswith("*/", i):
                        profundidad -= 1
                        i += 2
                    else:
                        i += 1
                salida.append(" ")
                continue

            salida.append(c)
            i += 1

        return "".join(salida)

    def validate(self, sql: str) -> dict[str, Any]:
        """
        Validar una consulta SQL.

        Args:
            sql: Consulta SQL a validar

        Returns:
            Diccionario con resultado de validación
        """
        result: dict[str, Any] = {
            "is_valid": True,
            "errors": [],
            "warnings": [],
            "suggestions": [],
            "security_score": 100
        }

        sql_upper = sql.upper()
        # S6: escanear operaciones prohibidas sobre el SQL sin comentarios
        # para que ``UNION/**/SELECT`` no evada los patrones.
        scan_upper = self._strip_comments(sql).upper()

        # Verificar operaciones prohibidas
        for pattern in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, scan_upper, re.IGNORECASE):
                result["is_valid"] = False
                result["errors"].append(f"Operación prohibida detectada: {_legible(pattern)}")
                result["security_score"] -= 50

        # Verificar patrones de advertencia
        for pattern, message in self.WARNING_PATTERNS:
            if re.search(pattern, sql, re.IGNORECASE | re.DOTALL):
                result["warnings"].append(message)
                result["security_score"] -= 5

        # Verificar LIMIT
        limit_match = re.search(r"LIMIT\s+(\d+)", sql_upper)
        if limit_match:
            limit_value = int(limit_match.group(1))
            if limit_value > self.max_limit:
                result["warnings"].append(
                    f"LIMIT {limit_value} excede el máximo recomendado ({self.max_limit})"
                )
                result["security_score"] -= 10
        else:
            result["warnings"].append("No se encontró LIMIT en la query")
            result["security_score"] -= 10

        # Verificar uso de índices espaciales
        has_spatial = any(f in sql_upper for f in ["ST_", "GEOM", "GEOMETRY"])
        if has_spatial:
            uses_indexed = any(f.upper() in sql_upper for f in self.INDEXED_FUNCTIONS)
            if not uses_indexed:
                result["suggestions"].append(
                    "Considerar usar funciones espaciales indexadas: " +
                    ", ".join(self.INDEXED_FUNCTIONS[:5])
                )

        # Verificar parámetros no sanitizados
        if re.search(r"'\s*\+\s*", sql) or re.search(r"\+\s*'", sql):
            result["is_valid"] = False
            result["errors"].append("Posible concatenación de strings no segura")
            result["security_score"] -= 30

        # Verificar múltiples statements
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        if len(statements) > 1:
            result["warnings"].append(
                f"Múltiples statements detectados ({len(statements)}). "
                "Solo se ejecutará el primero."
            )

        # Asegurar score mínimo de 0
        result["security_score"] = max(0, result["security_score"])

        # R0.7 (auditoría 2026-07-26, AUD-04): segunda opinión ESTRUCTURAL.
        # Todo lo de arriba decide mirando el SQL como texto, y eso es frágil
        # por construcción: el mismo SQL admite infinitos disfraces textuales.
        # `sql_ast_validator` lo parsea y valida su forma (un solo statement de
        # lectura, funciones dentro de una allowlist, ningún nodo de escritura).
        #
        # En `shadow` —el default— NO altera el veredicto: sólo registra las
        # discrepancias para dimensionar qué se rompería al activarlo. En
        # `enforce` sí bloquea.
        result["ast"] = self._validar_estructura(sql, result["is_valid"], self._tablas())
        if result["ast"] and not result["ast"].get("aceptada"):
            result["is_valid"] = False
            result["errors"].extend(
                f"Estructura no admitida: {m}" for m in result["ast"]["motivos"]
            )

        return result

    def _tablas(self) -> set[str] | None:
        if self.allowed_tables is None:
            return None
        try:
            return {t.lower() for t in self.allowed_tables()}
        except Exception as exc:  # noqa: BLE001 — sin catálogo, falla cerrado
            logger.warning("S0.2: no se pudo obtener la allowlist de tablas: %s", exc)
            return set()

    @staticmethod
    def _validar_estructura(
        sql: str, veredicto_texto: bool, tablas_permitidas: set[str] | None = None,
    ) -> dict[str, Any] | None:
        """Puente al validador estructural, tolerante a fallos.

        Si algo va mal aquí (parser roto, dependencia ausente) la petición no se
        rompe, pero el resultado depende del modo:
          - `shadow`: se registra y manda el veredicto del validador de texto.
          - `enforce`: se RECHAZA. Desde que `enforce` es el default, la allowlist
            de tablas solo existe en este camino; saltárselo por un fallo haría
            que un import roto abriera todas las tablas de `public`.
        """
        modo = "enforce"  # si ni siquiera se puede leer la config, falla cerrado
        try:
            from geo_copilot.core.config import get_settings

            modo = getattr(get_settings(), "sql_ast_validation", "enforce")
            from geo_copilot.agents.gis_agent import sql_ast_validator

            return sql_ast_validator.evaluar_en_modo(
                sql, modo, veredicto_texto, tablas_permitidas,
            )
        except Exception as exc:  # import/config rotos no tumban la petición; en `enforce` se rechaza (abajo), en `shadow` manda el texto
            logger.warning(
                "R0.7: el validador estructural no pudo ejecutarse (modo=%s): %s",
                modo, exc, exc_info=True,
            )
            if str(modo).strip().lower() == "enforce":
                return {
                    "aceptada": False,
                    "motivos": ["validador estructural no disponible"],
                    "funciones": [], "tablas": [], "n_statements": 0,
                }
            return None
