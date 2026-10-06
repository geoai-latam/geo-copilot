"""La EJECUCIÓN del SQL: el conteo previo, la ejecución (con el rol de solo lectura) y el GeoJSON
del resultado.

Salió de `GISAgent` (F4 del plan de calidad: agent.py tenía 1.101 líneas), tal cual.
"""

import json
from typing import TYPE_CHECKING, Any, cast

from geo_copilot.agents.gis_agent.sql_validator import SQLValidator, quote_qualified
from geo_copilot.core.config import settings
from geo_copilot.core.logging import get_logger
from geo_copilot.security.hitl import HITLActionType, HITLStatus

if TYPE_CHECKING:
    from geo_copilot.security.hitl import HITLManager
    from geo_copilot.semantic.layer import SemanticLayer


def _ag():
    """`agent` importa este módulo; su estado de módulo (el rol de solo lectura, que las pruebas
    reinician ahí) y sus helpers se resuelven al usarlos."""
    from geo_copilot.agents.gis_agent import agent

    return agent


logger = get_logger("geo_copilot.agents.gis_agent.agent")


def _fail(error: str, table: str | None = None, sql: str | None = None) -> dict[str, Any]:
    return {
        "success": False,
        "count": None,
        "table": table,
        "executed_sql": sql,
        "error": error,
    }


def _timeout_conteo(timeout_ms: Any) -> int:
    """El statement_timeout del conteo (ms): el pedido o el de la config, mínimo 500."""
    # Timeout: coerción defensiva.
    from geo_copilot.core.config import get_settings
    cfg = get_settings()
    default_t_ms = int(cfg.db_query_timeout * 1000)
    try:
        requested_t_ms = int(timeout_ms) if timeout_ms is not None else default_t_ms
    except (TypeError, ValueError):
        requested_t_ms = default_t_ms
    t_ms = max(500, requested_t_ms)
    return t_ms


def _con_tope(sql: str, _cap: Any) -> str:
    """El SQL con un LIMIT exterior que no se puede eludir (el tope de la config)."""
    # P0-A + R3.1: tope DURO de LIMIT en el único punto de ejecución. La
    # versión vieja usaba `\bLIMIT\s+(\d+)` que matcheaba el PRIMER LIMIT
    # aunque estuviera en una SUBQUERY (bypass: outer sin LIMIT quedaba sin
    # tope). Ahora: si el SQL termina en un LIMIT exterior, se capa ese; si
    # no (sin LIMIT, LIMIT ALL, o solo LIMITs anidados), se ENVUELVE la
    # query completa: SELECT * FROM (<sql>) AS _capped LIMIT <cap> —
    # imposible de eludir con anidamiento (WITH/CTE es válido en subquery).
    import re as _re
    if not isinstance(_cap, int) or _cap <= 0:
        _cap = 1000
    _body = sql.rstrip().rstrip(";").rstrip()
    _m = _re.search(
        r"\bLIMIT\s+(\d+)(\s+OFFSET\s+\d+)?\s*$", _body, _re.IGNORECASE
    )
    if _m:
        if int(_m.group(1)) > _cap:
            _body = (
                _body[: _m.start()]
                + f"LIMIT {_cap}"
                + (_m.group(2) or "")
            )
        return _body
    return f"SELECT * FROM (\n{_body}\n) AS _capped\nLIMIT {_cap}"


class EjecucionSQLMixin:
    """La ejecución del SQL: conteo previo, ejecución de solo lectura y el GeoJSON del resultado."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        db_pool: Any
        db_connection: Any
        semantic_layer: SemanticLayer | None
        sql_validator: SQLValidator
        hitl_manager: HITLManager
        _MAX_WHERE_LEN: int

    async def preview_count(
        self,
        *,
        entity: str,
        where_clause: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Contar filas SIN traer geometría (preflight para HITL/Planner).

        Útil para:
        - SymbologyAgent decidiendo heatmap/cluster sin tener que cargar.
        - HITL mostrando "esta query va a devolver ~150k features" antes
          de aprobar.
        - Planner decidiendo si auto-añadir LIMIT a un step de query_data.

        **Seguridad:**
        - Ejecuta en transacción ``READ ONLY`` con ``statement_timeout``.
        - ``where_clause`` se valida con ``SQLValidator`` (mismo módulo
          que rechaza el SQL del LLM): bloquea tokens destructivos,
          multi-statement, UNION injection, pg_sleep, etc.
        - Cap de longitud de ``where_clause`` para defenderse de prompt
          inflation adversaria.

        ``entity`` se resuelve en el semantic layer; ``where_clause`` va sin la palabra WHERE
        (p. ej. ``"area_m2 > 500"``); ``timeout_ms`` cae al de la config si no es un entero.
        Devuelve ``{success, count, table, executed_sql, error}``; sin entidad o sin pool,
        ``success=False`` con la razón (el caller decide).
        """
        if not self.db_pool:
            return _fail("no database pool")
        if not self.semantic_layer:
            return _fail("no semantic layer")

        if not entity or not isinstance(entity, str) or not entity.strip():
            return _fail("entity is empty or not a string")

        ent = self.semantic_layer.get_entity(entity.strip())
        if ent is None:
            return _fail(f"entity {entity!r} not found in semantic layer")

        # S5: identificadores citados — evita inyección si schema/table del
        # semantic layer contienen caracteres hostiles/espacios/mayúsculas.
        table_ref = quote_qualified(ent.schema_name, ent.table)

        invalida = self._where_invalido(where_clause, table_ref)
        if invalida is not None:
            return invalida

        sql = f"SELECT COUNT(*) AS n FROM {table_ref}"
        if where_clause and where_clause.strip():
            sql += f" WHERE {where_clause.strip()}"

        t_ms = _timeout_conteo(timeout_ms)

        return await self._contar(sql, t_ms, table_ref, entity, where_clause)

    async def _contar(self, sql: str, t_ms: int, table_ref: str, entity: str,
                      where_clause: str | None) -> dict[str, Any]:
        """El COUNT(*) en una transacción READ ONLY con su statement_timeout."""
        try:
            async with self.db_pool.acquire() as conn:
                async with conn.transaction(readonly=True):
                    await conn.execute(f"SET LOCAL statement_timeout = {t_ms}")
                    row = await conn.fetchrow(sql)
                count = int(row["n"]) if row else 0
            logger.info(
                f"[A2A] preview_count({entity!r}, where={where_clause!r}) = {count}"
            )
            return {
                "success": True,
                "count": count,
                "table": table_ref,
                "executed_sql": sql,
                "error": None,
            }
        except Exception as exc:  # capacidad A2A best-effort: pool/red/SQL del LLM; el fallo se devuelve como dato, no rompe al llamador
            err = f"{type(exc).__name__}: {exc}"
            logger.warning(f"[A2A] preview_count failed: {err}", exc_info=True)
            return _fail(err, table=table_ref, sql=sql)

    def _where_invalido(self, where_clause: Any, table_ref: str) -> dict[str, Any] | None:
        """El fallo si la cláusula WHERE del conteo no es segura (SQLValidator, longitud); None si lo es."""
        # Validar where_clause con SQLValidator — antes solo chequeábamos
        # ``;`` que permitía UNION injection, pg_sleep, etc. El validator
        # ya tiene la lista canónica de patrones prohibidos del proyecto.
        if where_clause:
            if not isinstance(where_clause, str):
                return _fail("where_clause must be a string", table=table_ref)
            wc = where_clause.strip()
            if len(wc) > self._MAX_WHERE_LEN:
                return _fail(
                    f"where_clause too long ({len(wc)} chars > {self._MAX_WHERE_LEN})",
                    table=table_ref,
                )
            # Validamos como SELECT completo para que SQLValidator
            # ejecute todos sus checks sobre el where también.
            probe = f"SELECT 1 FROM {table_ref} WHERE {wc}"
            v = self.sql_validator.validate(probe)
            if not v["is_valid"]:
                return _fail(
                    f"where_clause rechazada por SQLValidator: "
                    f"{'; '.join(v['errors'])}",
                    table=table_ref,
                )
        return None

    async def _aprobacion(self, sql: str, params: dict | None, validation: dict) -> tuple[str, dict | None]:
        """HITL del SQL: (el SQL aprobado —o el modificado—, None) o (sql, el rechazo para el usuario)."""
        if settings.hitl_enabled and self.hitl_manager:
            hitl_response = await self.hitl_manager.request_approval(
                action_type=HITLActionType.SQL_EXECUTION,
                title="Execute SQL Query",
                description="Execute validated spatial query",
                details={"sql": sql, "params": params},
                risks=validation.get("warnings", []),
                preview=sql[:500]
            )

            # Allowlist, no lista de rechazos (ver la ruta viva en
            # orchestrator/nodes/gis_agent.py). Con REJECTED + MODIFIED, un
            # EXPIRED seguía de largo hacia la ejecución.
            if hitl_response.status == HITLStatus.MODIFIED:
                sql = cast(str, hitl_response.modified_content)
            elif hitl_response.status != HITLStatus.APPROVED:
                return sql, {
                    "success": False,
                    "message": (
                        f"Query not approved ({hitl_response.status.value}): "
                        f"{hitl_response.feedback or 'sin razón especificada'}"
                    ),
                }
        return sql, None

    async def _rol_de_solo_lectura(self) -> bool:
        """True si el rol `gis_readonly` está; si no, FAIL-CLOSED (no se ejecuta SQL del LLM)."""
        # SEC-02: rol de mínimos privilegios para el SQL del LLM. Chequeado una
        # vez FUERA de la transacción (un SET ROLE a un rol inexistente aborta la
        # transacción; por eso solo lo emitimos si sabemos que está disponible).
        use_readonly_role: bool = await _ag()._gis_readonly_available(self.db_pool)
        # R0.6 (AUD-04): FAIL-CLOSED. Si el rol de mínimos privilegios no está
        # disponible, NO se ejecuta SQL del LLM. Antes se continuaba con el
        # usuario de login —superusuario en la imagen postgis— y una
        # transacción READ ONLY no contiene a un superusuario: `pg_read_file`
        # y `pg_authid` quedaban al alcance desde el chat.
        if not use_readonly_role:
            raise _ag().GisReadonlyUnavailable(
                "El rol 'gis_readonly' no está disponible en esta base de datos. "
                "Ejecuta docker/init-db/03_gis_readonly.sql (o la migración "
                "equivalente) antes de permitir SQL generado por el modelo."
            )
        return use_readonly_role

    async def execute_query(
        self,
        sql: str,
        params: dict | None = None
    ) -> dict[str, Any]:
        """
        Ejecutar una consulta SQL validada.

        Args:
            sql: Consulta SQL
            params: Parámetros de la consulta

        Returns:
            Resultados de la ejecución
        """
        # Validar antes de ejecutar
        validation = self.sql_validator.validate(sql)
        if not validation["is_valid"]:
            return {
                "success": False,
                "message": "SQL validation failed",
                "errors": validation["errors"]
            }

        # Solicitar aprobación HITL
        sql, rechazo = await self._aprobacion(sql, params, validation)
        if rechazo is not None:
            return rechazo

        # Ejecutar query
        if not self.db_connection:
            return {
                "success": True,
                "message": "Query validated (no DB connection for execution)",
                "sql": sql,
                "results": None,
                "note": "Configure db_connection to execute queries"
            }

        try:
            # C3b-1: _execute_sql devuelve (results, geojson). Antes se
            # guardaba la tupla entera en "results".
            results, geojson = await self._execute_sql(sql, params)
            return {
                "success": True,
                "message": "Query executed successfully",
                "sql": sql,
                "results": results,
                "geojson": geojson,
            }
        except Exception as e:
            logger.error(f"Query execution failed: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"Execution failed: {str(e)}",
                "sql": sql
            }

    async def _execute_sql(self, sql: str, params: dict | None = None) -> tuple[list[dict], dict | None]:
        """
        Ejecutar SQL contra la base de datos usando asyncpg.

        SEC-6b — todo SQL generado por el LLM corre dentro de una
        transacción ``READ ONLY`` con ``statement_timeout`` aplicado.
        Esto evita: (a) que una operación de escritura inadvertida
        modifique datos, (b) que una query patológica (joins masivos,
        ``pg_sleep``) agote el pool indefinidamente. El timeout viene
        de ``settings.db_query_timeout`` (segundos).
        """
        if not self.db_pool:
            logger.warning("No database pool available for SQL execution")
            return [], None

        from geo_copilot.core.config import get_settings
        timeout_ms = max(1000, int(get_settings().db_query_timeout) * 1000)

        # R3.1: la denylist del SQLValidator corre AQUÍ, en el ÚNICO punto de
        # ejecución del path vivo (antes solo protegía código legacy muerto).
        # Cinturón sobre la transacción READ ONLY: rechaza escritura/UNION-
        # injection ANTES de tocar la BD, con error honesto que el corrector
        # puede leer. La validación SEMÁNTICA sigue siendo del LLM + HITL.
        _validator = getattr(self, "sql_validator", None) or SQLValidator()
        _verdict = _validator.validate(sql)
        if not _verdict.get("is_valid", False):
            raise ValueError(
                "SQL rechazado por el validador: "
                + "; ".join(_verdict.get("errors", ["operación no permitida"]))
            )

        sql = _con_tope(sql, getattr(getattr(self, "sql_validator", None), "max_limit", 1000))

        use_readonly_role = await self._rol_de_solo_lectura()
        try:
            async with self.db_pool.acquire() as conn:
                async with conn.transaction(readonly=True):
                    # ``SET LOCAL`` aplica sólo a la transacción actual.
                    await conn.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
                    # SEC-02: bajar a `gis_readonly` (SELECT solo sobre esquemas
                    # de dominio) para que un SELECT del LLM no pueda leer tablas
                    # fuera del dominio. Revierte al cerrar la transacción.
                    if use_readonly_role:
                        await conn.execute("SET LOCAL ROLE gis_readonly")
                    rows = await conn.fetch(sql)
                # V5 FH.9: un `numeric` de Postgres llega como Decimal y se serializaba como
                # texto («0E-8», «1.00000000» en la tabla); es un número.
                from decimal import Decimal

                results = [{k: float(v) if isinstance(v, Decimal) else v for k, v in dict(row).items()}
                           for row in rows]
                geojson = self._extract_geojson(results)
                return results, geojson

        except Exception as e:
            logger.error(f"SQL execution error: {e}", exc_info=True)
            raise

    def _extract_geojson(self, results: list[dict]) -> dict | None:
        """
        Extraer GeoJSON de los resultados SQL.

        Args:
            results: Lista de diccionarios con resultados

        Returns:
            FeatureCollection GeoJSON o None
        """

        if not results:
            return None

        geom_keys = ["geometry", "geom_geojson", "geojson", "geom"]
        features = []

        for row in results:
            geom_value = None
            geom_key = None

            # Buscar columna de geometría
            for key in geom_keys:
                if key in row and row[key]:
                    geom_value = row[key]
                    geom_key = key
                    break

            # Buscar columnas que terminen en _geojson
            if not geom_value:
                for key, value in row.items():
                    if key.endswith("_geojson") and value:
                        geom_value = value
                        geom_key = key
                        break

            if geom_value:
                try:
                    geom = json.loads(geom_value) if isinstance(geom_value, str) else geom_value
                    properties = {k: v for k, v in row.items() if k != geom_key}
                    features.append({
                        "type": "Feature",
                        "geometry": geom,
                        "properties": properties
                    })
                except (json.JSONDecodeError, TypeError):
                    pass

        if features:
            # La geometría es el DATO: no se simplifica. Antes una «red de seguridad»
            # la simplificaba (~5 m) al pasar de 40 000 vértices para aligerar el mapa;
            # esa versión deformada iba al workspace y a los cálculos, y 11 de 8442
            # construcciones que tocan un buffer dejaban de tocarlo (V5 F4). Desde F2
            # las capas grandes se DIBUJAN por teselas MVT, que ya generalizan por zoom.
            return {"type": "FeatureCollection", "features": features}
        return None
