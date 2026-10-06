"""El ESQUEMA de la base (completo y resumido), el SQL generado desde la pregunta y sus riesgos.

Salió de `GISAgent` (F4 del plan de calidad: agent.py tenía 1.101 líneas), tal cual.
"""

import json
from typing import TYPE_CHECKING, Any

from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.prompts import cargar_prompt


def _ag():
    """`agent` importa este módulo; su estado de módulo (el rol de solo lectura, que las pruebas
    reinician ahí) y sus helpers se resuelven al usarlos."""
    from geo_copilot.agents.gis_agent import agent

    return agent

if TYPE_CHECKING:
    from geo_copilot.core.llm_client import LLMClient

logger = get_logger("geo_copilot.agents.gis_agent.agent")


def _contexto_conversacion(conversation_history: list[dict] | None, previous_sql: str | None,
                           previous_results: list[dict] | None) -> str:
    """La conversación reciente y la consulta anterior (para preguntas de seguimiento)."""
    # Construir contexto de conversación
    context_parts = []
    if conversation_history:
        recent = conversation_history[-4:]
        context_parts.append("CONVERSACIÓN RECIENTE:")
        for m in recent:
            role = "Usuario" if m.get('role') == 'user' else "Sistema"
            context_parts.append(f"  {role}: {m.get('content', '')[:200]}")

    if previous_sql:
        context_parts.append(f"\nSQL DE LA CONSULTA ANTERIOR:\n{previous_sql}")

        # Incluir muestra de resultados anteriores si existen
        if previous_results and len(previous_results) > 0:
            # Mostrar solo los primeros registros y columnas relevantes
            sample = previous_results[:3]
            sample_str = json.dumps(sample, default=str, ensure_ascii=False)[:500]
            context_parts.append(f"\nRESULTADOS ANTERIORES ({len(previous_results)} registros):\n{sample_str}")

        context_parts.append("""
⚠️ REGLAS CRÍTICAS PARA PREGUNTAS DE SEGUIMIENTO:
1. Si el usuario pregunta sobre atributos del resultado anterior ("cuantos pisos tiene", "cual es el area", "que tipo es"):
   - REUTILIZA el SQL anterior agregando/modificando el SELECT para incluir el atributo pedido
   - Mantén EXACTAMENTE el mismo WHERE/filtro
   - Ejemplo: Si el SQL anterior era "SELECT ... FROM tabla WHERE condicion LIMIT 1"
     Y pregunta "cuantos pisos tiene" → "SELECT "NUMERO_PISOS" FROM tabla WHERE condicion LIMIT 1"

2. Si hace referencia a 'esos', 'esa información', 'esos predios', 'los anteriores':
   - REUTILIZA el WHERE/filtro del SQL anterior

3. Si pide estadísticas de los datos anteriores:
   - USA el mismo WHERE pero agrega GROUP BY""")

    return "\n".join(context_parts) if context_parts else ""


async def _fijar_srid(conn: Any) -> None:
    """El SRID de las columnas de geometría para el validador AST (si es uno solo, 4326)."""
    # H14: el validador AST necesita el SRID de las columnas para
    # detectar predicados con SRID mezclado. Solo si es uno solo en
    # toda la BD y coincide con el del workspace (4326); si no, se
    # comparan únicamente SRIDs explícitos.
    from geo_copilot.agents.gis_agent.sql_ast_validator import fijar_srid_de_columnas

    srids = {
        r["srid"] for r in await conn.fetch(
            "SELECT DISTINCT srid FROM geometry_columns "
            "WHERE f_table_schema NOT IN ('pg_catalog', 'information_schema', 'topology') "
            "AND f_table_schema NOT LIKE 'ws\\_%'"
        )
    }
    fijar_srid_de_columnas(4326 if srids == {4326} else None)


async def _hechos_de_columnas(conn: Any) -> dict[tuple[str, str, str], str]:
    """Por columna: sus pocos valores (pg_stats) y su comentario en la BD."""
    # F5 (V5): «lotes del catastro de Bogotá» → el generador filtró `lotdistrit = 11001`
    # (lo leyó como el código DANE de Bogotá; sus valores son 0 y 1) y la respuesta fue 0
    # lotes donde había 3. Los HECHOS de la columna (qué valores toma, si son pocos, y su
    # comentario en la BD) van con ella; las estadísticas del planificador no leen la tabla.
    hechos_col: dict[tuple[str, str, str], str] = {}
    try:
        for r in await conn.fetch(
            "SELECT schemaname, tablename, attname, n_distinct, most_common_vals::text AS vals "
            "FROM pg_stats WHERE schemaname NOT IN ('pg_catalog', 'information_schema', 'topology') "
            "AND schemaname NOT LIKE 'ws\\_%' AND schemaname <> 'ws_meta' "
            "AND n_distinct > 0 AND n_distinct <= 10"
        ):
            vals = (r["vals"] or "").strip("{}")
            if vals:
                hechos_col[(r["schemaname"], r["tablename"], r["attname"])] = f"valores: {vals[:80]}"
        for r in await conn.fetch(
            "SELECT n.nspname, c.relname, a.attname, col_description(c.oid, a.attnum) AS com "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE a.attnum > 0 AND col_description(c.oid, a.attnum) IS NOT NULL "
            "AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'topology') AND n.nspname NOT LIKE 'ws\\_%'"
        ):
            clave = (r["nspname"], r["relname"], r["attname"])
            previo = hechos_col.get(clave)
            hechos_col[clave] = f"{r['com'][:120]}" + (f"; {previo}" if previo else "")
    except Exception:  # hechos opcionales: sin ellos el esquema sigue siendo válido
        logger.warning("[GISAgent] sin estadísticas/comentarios de columnas", exc_info=True)
    return hechos_col


async def _comentarios_de_tablas(conn: Any) -> dict[str, str]:
    """El comentario de cada tabla (su alcance, p. ej. «todo es de Bogotá»)."""
    # y el comentario de cada TABLA (su alcance: p. ej. «todo es de Bogotá»)
    com_tabla: dict[str, str] = {}
    try:
        for r in await conn.fetch(
            "SELECT n.nspname || '.' || c.relname AS t, obj_description(c.oid, 'pg_class') AS com "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('r', 'v', 'm') AND obj_description(c.oid, 'pg_class') IS NOT NULL "
            "AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'topology') "
            "AND n.nspname NOT LIKE 'ws\\_%'"
        ):
            com_tabla[r["t"]] = r["com"][:240]
    except Exception:  # hecho opcional
        logger.warning("[GISAgent] sin comentarios de tablas", exc_info=True)
    return com_tabla


def _formatear_esquema(rows: list, hechos_col: dict, com_tabla: dict) -> str:
    """El esquema como texto para el LLM: TODAS las tablas y columnas, con sus hechos."""
    # Organizar por tabla
    schema: dict[str, list[dict]] = {}
    for row in rows:
        table_key = f"{row['table_schema']}.{row['table_name']}"
        if table_key not in schema:
            schema[table_key] = []
        schema[table_key].append({
            "column": row["column_name"],
            "type": row["udt_name"],
            "geom_type": row["geometry_type"],
            "hecho": hechos_col.get((row["table_schema"], row["table_name"], row["column_name"])),
        })

    # Formatear como texto - TODAS las tablas y TODAS las columnas
    lines = []
    for table, cols in schema.items():
        # Separar columna de geometría de las demás
        geom_col = next((c for c in cols if c["geom_type"]), None)
        other_cols = [c for c in cols if not c["geom_type"]]

        # Formatear columnas con sus tipos
        col_info = [f'"{c["column"]}" ({c["type"]}' + (f'; {c["hecho"]}' if c.get("hecho") else "") + ")"
                    for c in other_cols]

        # Marcar claramente la columna de geometría
        if geom_col:
            geom_str = f' | GEOMETRIA: "{geom_col["column"]}" ({geom_col["geom_type"]})'
        else:
            geom_str = " | SIN GEOMETRIA"

        lines.append(f"TABLA {table}:{geom_str}")
        if com_tabla.get(table):
            lines.append(f"  Descripción: {com_tabla[table]}")
        lines.append(f"  Columnas: {', '.join(col_info)}")
        lines.append("")  # Línea en blanco entre tablas

    return "\n".join(lines)


class EsquemaMixin:
    """El esquema de la base (completo y resumido), el SQL desde la pregunta y sus riesgos."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        llm_client: LLMClient
        db_pool: Any

    async def generate_sql_from_query(
        self,
        query: str,
        schema_info: str,
        previous_sql: str | None = None,
        previous_results: list[dict] | None = None,
        conversation_history: list[dict] | None = None
    ) -> str | None:
        """
        Generar SQL desde consulta natural usando schema de BD directo.

        Args:
            query: Consulta en lenguaje natural
            schema_info: Schema de la BD en formato texto
            previous_sql: SQL de consulta anterior (para follow-ups)
            previous_results: Resultados de la consulta anterior
            conversation_history: Historial de conversación

        Returns:
            SQL generado o None si falla
        """
        context_info = _contexto_conversacion(conversation_history, previous_sql, previous_results)

        # H19 (V5 F2): el tope sale de la config (SQL_RESULT_LIMIT); estaba
        # escrito "1000" aquí y en el validador aunque el setting existía.
        _tope = _ag()._tope_sql()
        prompt = cargar_prompt("sql_generacion").format(
            schema_info=schema_info,
            contexto_conversacion=f"CONTEXTO DE LA CONVERSACIÓN:{chr(10)}{context_info}" if context_info else "",
            _tope=_tope,
            query=query,
        )

        try:
            response = await self.llm_client.chat([
                LLMMessage(role="user", content=prompt)
            ])

            sql = response.content.strip()
            # Limpiar markdown si existe
            if sql.startswith("```"):
                sql = sql.split("\n", 1)[1] if "\n" in sql else sql
                sql = sql.rsplit("```", 1)[0]

            return sql.strip()

        except Exception as e:
            logger.error(f"Error generating SQL: {e}", exc_info=True)
            return None

    async def get_db_schema(self) -> str:
        """
        Obtener schema detallado de la BD para generación de SQL.

        Returns:
            Schema formateado como texto
        """
        if not self.db_pool:
            return ""

        try:
            async with self.db_pool.acquire() as conn:
                query = """
                    SELECT c.table_schema, c.table_name, c.column_name, c.udt_name,
                        CASE WHEN c.udt_name = 'geometry' THEN
                            (SELECT type FROM geometry_columns gc WHERE gc.f_table_schema = c.table_schema
                             AND gc.f_table_name = c.table_name AND gc.f_geometry_column = c.column_name)
                        ELSE NULL END as geometry_type
                    FROM information_schema.columns c
                    WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema', 'topology')
                      -- S2.2: los workspaces de las sesiones NO son esquema de dominio;
                      -- cada sesión recibe los suyos aparte (nodo SQL). Sin esto, con un
                      -- rol más amplio el prompt listaría los de todas las sesiones.
                      AND c.table_schema NOT LIKE 'ws\\_%' AND c.table_schema <> 'ws_meta'
                    ORDER BY c.table_schema, c.table_name, c.ordinal_position
                """
                rows = await conn.fetch(query)

                await _fijar_srid(conn)

                hechos_col = await _hechos_de_columnas(conn)
                com_tabla = await _comentarios_de_tablas(conn)

                return _formatear_esquema(rows, hechos_col, com_tabla)

        except Exception as e:
            logger.error(f"Error getting schema: {e}", exc_info=True)
            return ""

    async def get_db_schema_summary(self) -> str:
        """
        Obtener resumen del schema para el router.

        Returns:
            Resumen de tablas geoespaciales
        """
        if not self.db_pool:
            return ""

        try:
            async with self.db_pool.acquire() as conn:
                # Antes `LIMIT 20` sin aviso: en una BD con más tablas el router creía que la
                # entidad no existía y se iba a buscar fuera. Nombre + tipo es barato: todas hasta
                # un tope generoso, y si hay más se DICE cuántas faltan.
                query = """
                    SELECT f_table_schema, f_table_name, type, srid,
                           count(*) OVER () AS total
                    FROM geometry_columns
                    WHERE f_table_schema NOT IN ('pg_catalog', 'information_schema', 'topology')
                      -- los workspaces de sesión (ws_<hex>) no son datos de la BD: en la BD real
                      -- eran 380 de 382 tablas, y el `LIMIT 20` de antes mostraba al router 18
                      -- tablas anónimas de OTRAS sesiones en vez de las del catastro
                      AND f_table_schema NOT LIKE 'ws\\_%'
                    ORDER BY f_table_schema, f_table_name
                    LIMIT $1
                """
                rows = await conn.fetch(query, _ag()._TABLAS_EN_RESUMEN)

                if not rows:
                    return "No se encontraron tablas geoespaciales"

                lines = ["Tablas geoespaciales disponibles:"]
                for row in rows:
                    table_name = f"{row['f_table_schema']}.{row['f_table_name']}"
                    geom_type = row['type'] or 'GEOMETRY'
                    lines.append(f"- {table_name} ({geom_type})")
                faltan = int(rows[0]["total"]) - len(rows)
                if faltan > 0:
                    lines.append(f"(+{faltan} tablas geoespaciales más no listadas: que una entidad no "
                                 "esté aquí NO significa que no exista en la BD)")

                return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error getting schema summary: {e}", exc_info=True)
            return ""

    def identify_sql_risks(self, sql: str) -> list[str]:
        """
        Identificar riesgos potenciales en una consulta SQL.

        Args:
            sql: Consulta SQL a analizar

        Returns:
            Lista de riesgos identificados
        """
        risks = []
        sql_upper = sql.upper()

        if "DELETE" in sql_upper or "DROP" in sql_upper:
            risks.append("Contiene operaciones destructivas (DELETE/DROP)")
        if "UPDATE" in sql_upper or "INSERT" in sql_upper:
            risks.append("Contiene operaciones de escritura (UPDATE/INSERT)")
        if "LIMIT" not in sql_upper:
            risks.append("Sin cláusula LIMIT - puede retornar muchos registros")
        if sql_upper.count("JOIN") > 3:
            risks.append("Múltiples JOINs pueden afectar rendimiento")

        return risks
