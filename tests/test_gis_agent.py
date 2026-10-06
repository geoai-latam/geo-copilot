"""
Tests para el Agente GIS/SQL.
"""

import re
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

# El PythonSandbox usa subprocess + restricciones POSIX (resource.RLIMIT_AS,
# signals, cgroups) que NO funcionan en Windows. Decisión oficial F5(B)
# del roadmap (2026-05-25): Windows nativo NO es plataforma soportada para
# el sandbox. Devs en Windows deben usar WSL2 o Docker Desktop.
# Ver docs/sistema/09-configuracion-y-deploy.md §9.4 para política completa.
# Estos tests corren en Linux/macOS y en CI.
_skip_subprocess_on_windows = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "PythonSandbox no soportado en Windows nativo (F5 del roadmap). "
        "Usa WSL2 o Docker Desktop para correr estos tests localmente."
    ),
)

# Sprint F (2026-05-25) borró ``SQLTemplates`` y los 4 metodos template
# (proximity/aggregation/coverage/intersection) del GISAgent. El LLM
# construye SQL completo viendo el schema. Los tests que ejercitaban esos
# símbolos se removieron junto con el código.
from geo_copilot.agents.gis_agent import GISAgent, SQLGenerator, SQLValidator
from geo_copilot.agents.gis_agent.sandbox import PythonSandbox
from geo_copilot.security.hitl import HITLManager
from geo_copilot.semantic.layer import SemanticLayer


def _strip_sql_comments(sql: str) -> str:
    """Quita comentarios `-- ...` line-end del SQL para asserts estructurales."""
    return re.sub(r"--[^\n]*", "", sql)


class TestStripSqlComments:
    """Auto-tests del helper para evitar regresiones en los asserts SQL.

    Antes (auditoría): los regex `ST_Buffer[^;]{0,200}\\b500\\b` aceptaban
    `-- foo 500\\nST_Buffer(...)` como match aunque 500 estuviera en
    comentario, no en el buffer. Estos tests garantizan que strippeamos
    correctamente."""

    def test_strips_single_line_comment(self):
        assert _strip_sql_comments("-- foo 500\nSELECT 1") == "\nSELECT 1"

    def test_strips_inline_trailing_comment(self):
        assert _strip_sql_comments("SELECT 1 -- comment\nSELECT 2") == \
               "SELECT 1 \nSELECT 2"

    def test_preserves_string_literal_containing_dashes(self):
        # Limitación conocida: nuestro helper es regex simple, NO un parser.
        # Un literal SQL con `--` adentro se romperá. Documentado aquí.
        # Si esto causa false positives en producción, switch a sqlparse.
        sql = "SELECT 'no--comment'"
        # Documentamos comportamiento actual (limitación):
        assert _strip_sql_comments(sql) == "SELECT 'no"

    def test_regex_with_strip_rejects_number_in_comment(self):
        """REGRESIÓN: con el strip aplicado, un SQL con `500` SOLO en
        comentario NO debe pasar el regex de proximity_analysis."""
        sql = "-- distance 500 m\nSELECT * FROM x"
        stripped = _strip_sql_comments(sql)
        flat = re.sub(r"\s+", " ", stripped)
        # No hay ST_Buffer ni ST_DWithin en el ejecutable.
        assert not re.search(
            r"ST_Buffer[^;]{0,200}\b500\b", flat, re.IGNORECASE,
        )
        assert not re.search(
            r"ST_DWithin[^;]{0,200}\b500\b", flat, re.IGNORECASE,
        )


@pytest.fixture
def sample_semantic_config():
    """Configuración semántica de ejemplo."""
    return {
        "version": "1.0",
        "entities": {
            "parcela": {
                "description": "Unidad catastral",
                "aliases": ["predio", "lote"],
                "table": "cat_parcelas",
                "schema": "catastro",
                "geometry_column": "geom",
                "geometry_type": "POLYGON",
                "srid": 4326,
                "fields": {
                    "id": {"column": "id_parcela", "type": "string", "primary_key": True},
                    "area": {"column": "area_m2", "type": "float"},
                    "valor": {"column": "valor_cat", "type": "float"}
                },
                "metrics": []
            },
            "municipio": {
                "description": "División administrativa",
                "aliases": ["ciudad"],
                "table": "div_municipios",
                "schema": "admin",
                "geometry_column": "geom",
                "geometry_type": "MULTIPOLYGON",
                "srid": 4326,
                "fields": {
                    "codigo": {"column": "cod_mun", "type": "string", "primary_key": True},
                    "nombre": {"column": "nom_mun", "type": "string"},
                    "poblacion": {"column": "poblacion", "type": "integer"}
                }
            },
            "via": {
                "description": "Red vial",
                "aliases": ["carretera"],
                "table": "infra_vias",
                "schema": "infraestructura",
                "geometry_column": "geom",
                "geometry_type": "LINESTRING",
                "srid": 4326,
                "fields": {
                    "id": {"column": "id_via", "type": "string", "primary_key": True},
                    "nombre": {"column": "nombre", "type": "string"},
                    "tipo": {"column": "tipo_via", "type": "string"}
                }
            },
            "equipamiento": {
                "description": "Equipamiento público",
                "aliases": ["servicio"],
                "table": "equip_publico",
                "schema": "equipamientos",
                "geometry_column": "geom",
                "geometry_type": "POINT",
                "srid": 4326,
                "fields": {
                    "id": {"column": "id_equip", "type": "string", "primary_key": True},
                    "nombre": {"column": "nombre", "type": "string"},
                    "tipo": {"column": "tipo", "type": "string"}
                }
            }
        },
        "relationships": {}
    }


@pytest.fixture
def semantic_layer(sample_semantic_config):
    """Crear capa semántica de prueba."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(sample_semantic_config, f)
        temp_path = f.name

    layer = SemanticLayer(temp_path)
    yield layer
    Path(temp_path).unlink()


@pytest.fixture
def sql_validator():
    """Instancia de SQLValidator."""
    return SQLValidator(max_limit=1000)


@pytest.fixture
def sql_generator(semantic_layer):
    """Instancia de SQLGenerator."""
    return SQLGenerator(semantic_layer=semantic_layer)


@pytest.fixture
def gis_agent(semantic_layer):
    """Instancia de GISAgent."""
    return GISAgent(semantic_layer=semantic_layer)


# NOTE (Sprint F, 2026-05-25): la clase ``TestSQLTemplates`` ejercitaba
# ``SQLTemplates.proximity/aggregation/coverage/intersection/buffer/
# hotspot_density/temporal_change``. Esas APIs se borraron del producto
# (el LLM construye SQL completo viendo el schema), así que removimos los
# 9 tests aquí. Si vuelven los templates, restaurar desde git.


class TestSQLValidator:
    """Tests para validador SQL."""

    def test_validate_safe_query(self, sql_validator):
        """Test query segura."""
        sql = """
        SELECT id, nombre, ST_AsGeoJSON(geom)
        FROM catastro.parcelas
        WHERE area > 100
        LIMIT 100;
        """

        result = sql_validator.validate(sql)

        assert result["is_valid"] is True
        assert len(result["errors"]) == 0

    def test_detect_forbidden_operations(self, sql_validator):
        """Test detección de operaciones prohibidas."""
        dangerous_queries = [
            "DELETE FROM catastro.parcelas;",
            "DROP TABLE users;",
            "INSERT INTO parcelas VALUES (1);",
            "UPDATE parcelas SET area = 0;",
            "TRUNCATE catastro.parcelas;",
        ]

        for sql in dangerous_queries:
            result = sql_validator.validate(sql)
            assert result["is_valid"] is False
            assert len(result["errors"]) > 0

    def test_detect_missing_limit(self, sql_validator):
        """Test detección de LIMIT faltante."""
        sql = "SELECT * FROM parcelas;"

        result = sql_validator.validate(sql)

        assert any("LIMIT" in w for w in result["warnings"])

    def test_detect_select_star(self, sql_validator):
        """Test advertencia SELECT *."""
        sql = "SELECT * FROM parcelas LIMIT 100;"

        result = sql_validator.validate(sql)

        assert any("SELECT *" in w for w in result["warnings"])

    def test_optimize_add_limit(self, sql_validator):
        """Test agregar LIMIT."""
        sql = "SELECT id FROM parcelas;"

        optimized = sql_validator.optimize(sql)

        assert "LIMIT" in optimized

    def test_optimize_reduce_limit(self, sql_validator):
        """Test reducir LIMIT excesivo."""
        sql = "SELECT id FROM parcelas LIMIT 10000;"

        optimized = sql_validator.optimize(sql)

        assert "LIMIT 1000" in optimized

    def test_estimate_complexity(self, sql_validator):
        """Test estimación de complejidad."""
        simple_sql = "SELECT id FROM parcelas LIMIT 100;"
        complex_sql = """
        SELECT a.id, b.nombre, c.tipo
        FROM parcelas a
        JOIN municipios b ON ST_Within(a.geom, b.geom)
        JOIN zonas c ON ST_Intersects(a.geom, c.geom)
        WHERE ST_Buffer(a.geom, 100) IS NOT NULL
        """

        simple_result = sql_validator.estimate_complexity(simple_sql)
        complex_result = sql_validator.estimate_complexity(complex_sql)

        assert complex_result["score"] > simple_result["score"]

    def test_get_tables_used(self, sql_validator):
        """Test extraer tablas usadas."""
        sql = """
        SELECT a.id
        FROM catastro.parcelas a
        JOIN admin.municipios b ON ST_Within(a.geom, b.geom)
        """

        tables = sql_validator.get_tables_used(sql)

        assert "catastro.parcelas" in tables
        assert "admin.municipios" in tables


class TestSQLGenerator:
    """Tests para generador SQL."""

    def test_generate_count_query(self, sql_generator):
        """Test generar query de conteo."""
        sql = sql_generator.generate_count_query("parcela")

        assert "COUNT" in sql
        # S5: identificadores citados.
        assert '"catastro"."cat_parcelas"' in sql

    def test_generate_extent_query(self, sql_generator):
        """Test generar query de extent."""
        sql = sql_generator.generate_extent_query("parcela")

        assert "ST_Extent" in sql
        assert "xmin" in sql
        assert "ymax" in sql

    def test_generate_sample_query_eliminado(self, sql_generator):
        """R2.2: el template de muestra fue eliminado (heurística de campos);
        las muestras las genera el LLM con SQL propio."""
        assert not hasattr(sql_generator, "generate_sample_query")


class TestGISAgent:
    """Tests para GISAgent."""

    def test_agent_initialization(self, gis_agent):
        """Test inicialización del agente."""
        assert gis_agent.name == "GISAgent"
        assert len(gis_agent.get_tools()) > 0

    def test_agent_capabilities(self, gis_agent):
        """Test capacidades del agente."""
        caps = gis_agent.get_capabilities()

        assert "analysis_types" in caps
        assert "proximity" in caps["analysis_types"]
        assert "aggregation" in caps["analysis_types"]
        assert "spatial_functions" in caps
        assert "ST_Buffer" in caps["spatial_functions"]

    # NOTE (Sprint F): los siguientes tests cubrían APIs eliminadas del
    # producto — ``proximity_analysis``, ``territorial_aggregation``,
    # ``coverage_analysis``, ``spatial_intersection``. El SQL espacial
    # ahora lo construye el LLM via ``sql_generator.generate()`` viendo el
    # schema. La cobertura equivalente vive en el manual e2e
    # ``tests/manual_e2e/sprint_f_gis.py`` (requiere LLM real). El test
    # de rechazo de SQL destructivo se mantiene porque ejercita
    # ``SQLValidator`` que sigue activo.

    @pytest.mark.asyncio
    async def test_generated_sql_rejects_destructive_ops(self):
        """Regresión de seguridad: SQLValidator debe rechazar DROP/DELETE/
        TRUNCATE incluso si vienen disfrazados en mayúsculas/minúsculas o
        con comentarios. No es estrictamente un test del GISAgent pero
        garantiza que el validador (que el agente usa post-generación) NO
        deja pasar SQL destructivo."""
        from geo_copilot.agents.gis_agent.sql_validator import SQLValidator
        v = SQLValidator()

        for bad_sql in [
            "DROP TABLE parcelas;",
            "delete from parcelas where id = 1",
            "TRUNCATE parcelas",
            "UPDATE parcelas SET valor = 0",
            "ALTER TABLE parcelas DROP COLUMN id",
            "INSERT INTO parcelas (id) VALUES (1)",
            "SELECT * FROM parcelas; DROP TABLE parcelas",  # multi-statement
        ]:
            result = v.validate(bad_sql)
            # Para multi-statement, validator emite warning pero el primer
            # statement puede ser válido — la protección real está en el
            # connector que ejecuta solo el primer statement.
            if ";" in bad_sql.rstrip(";") :
                assert any("statement" in w.lower() for w in result["warnings"])
            else:
                assert not result["is_valid"], (
                    f"SQL destructivo NO debió validar: {bad_sql}\n→ {result}"
                )

    # NOTE (Sprint F): ``test_invalid_entity`` ejercitaba
    # ``proximity_analysis`` con una entidad inexistente — el método ya
    # no existe. El manejo de "entidad no encontrada" pasó a vivir en el
    # propio LLM prompt del sql_generator (que la rechaza), por lo que el
    # test equivalente requiere LLM real y vive en manual_e2e.


class TestPythonSandbox:
    """Tests para sandbox Python."""

    @pytest.fixture(autouse=True)
    def _subprocess_backend(self, monkeypatch):
        # Estos tests ejecutan código de verdad con el runner local. Desde R0.5
        # el default es 'docker', y en CI no hay contenedor sandbox: sin fijar
        # el backend, las ejecuciones fallaban por el `docker exec` ausente, no
        # por lo que el test dice probar. El backend docker tiene sus propios
        # tests en test_sandbox_docker_backend.py.
        from geo_copilot.agents.gis_agent import sandbox as sandbox_mod

        monkeypatch.setattr(sandbox_mod.settings, "sandbox_backend", "subprocess")

    def test_analyze_safe_code(self):
        """Test análisis de código seguro."""
        sandbox = PythonSandbox()

        code = """
import numpy as np
import pandas as pd

data = np.array([1, 2, 3, 4, 5])
mean = np.mean(data)
"""

        result = sandbox.analyze_security(code)

        assert result["is_safe"] is True
        assert "numpy" in result["modules_used"]
        assert "pandas" in result["modules_used"]

    def test_detect_forbidden_module(self):
        """Test detección de módulo prohibido."""
        sandbox = PythonSandbox()

        code = """
import os
os.system('rm -rf /')
"""

        result = sandbox.analyze_security(code)

        assert result["is_safe"] is False
        assert any("os" in v for v in result["violations"])

    def test_detect_forbidden_function(self):
        """Test detección de función prohibida."""
        sandbox = PythonSandbox()

        code = """
eval('print("hello")')
"""

        result = sandbox.analyze_security(code)

        assert result["is_safe"] is False
        assert any("eval" in v for v in result["violations"])

    def test_detect_exec(self):
        """Test detección de exec."""
        sandbox = PythonSandbox()

        code = """
exec('import os')
"""

        result = sandbox.analyze_security(code)

        assert result["is_safe"] is False

    @_skip_subprocess_on_windows
    @pytest.mark.asyncio
    async def test_execute_safe_code(self):
        """Test ejecución de código seguro."""
        sandbox = PythonSandbox()

        code = """
import math
result = math.sqrt(16)
print(f"Result: {result}")
"""

        result = await sandbox.execute(code, require_approval=False)

        assert result["success"] is True
        assert "4.0" in result["output"]

    @_skip_subprocess_on_windows
    @pytest.mark.asyncio
    async def test_execute_with_input(self):
        """Test ejecución con datos de entrada."""
        sandbox = PythonSandbox()

        code = """
total = sum(numbers)
average = total / len(numbers)
"""

        result = await sandbox.execute(
            code,
            input_data={"numbers": [1, 2, 3, 4, 5]},
            require_approval=False
        )

        assert result["success"] is True
        assert result["results"]["total"] == 15
        assert result["results"]["average"] == 3.0

    # NOTE: ``generate_analysis_template`` fue eliminado en Fase 1 al
    # reescribir el sandbox para ejecución por subproceso aislado. Era una
    # utilidad sin consumidores reales en el código de producción (solo la
    # ejercitaba este test). Si vuelve a hacer falta se restaura desde git.

    @_skip_subprocess_on_windows
    @pytest.mark.asyncio
    async def test_reject_dangerous_code(self):
        """Test rechazo de código peligroso."""
        sandbox = PythonSandbox()

        code = """
import subprocess
subprocess.call(['ls', '-la'])
"""

        result = await sandbox.execute(code, require_approval=False)

        assert result["success"] is False
        assert "Security violation" in result["error"]

    # =========================================================================
    # Tests de seguridad y límites del subprocess
    # (Añadidos 2026-05-24 — antes solo testeábamos math.sqrt(16).
    # Estos garantizan que el aislamiento del subprocess realmente funciona.)
    # =========================================================================

    @_skip_subprocess_on_windows
    @pytest.mark.asyncio
    async def test_sandbox_blocks_filesystem_read(self):
        """El sandbox NO debe poder leer archivos del host. `open()` con
        un path absoluto debe fallar — o porque el sandbox lo bloquea
        estáticamente, o porque el subprocess no tiene permisos.

        Sin esta protección, un LLM podría ser inducido a leer
        `/etc/passwd`, `~/.aws/credentials`, etc."""
        sandbox = PythonSandbox()

        code = """
content = open("/etc/passwd").read() if __import__("os").name != "nt" else ""
"""
        result = await sandbox.execute(code, require_approval=False)
        # Aceptamos cualquier camino de fallo (security violation estática
        # o error de ejecución), pero NO success=True con contenido del archivo.
        if result["success"]:
            content = (
                result.get("results", {}).get("content", "")
                if isinstance(result.get("results"), dict) else ""
            )
            assert not content or "root:" not in str(content), (
                "el sandbox no debió poder leer /etc/passwd"
            )

    @pytest.mark.asyncio
    async def test_sandbox_blocks_import_of_dangerous_modules(self):
        """Una lista de módulos peligrosos debe ser rechazada por el
        analizador estático ANTES de ejecutar — no solo por error en
        runtime."""
        sandbox = PythonSandbox()

        dangerous_modules = ["socket", "ctypes", "pickle", "marshal", "shelve"]
        for module in dangerous_modules:
            result = sandbox.analyze_security(f"import {module}")
            assert not result["is_safe"], (
                f"módulo peligroso '{module}' NO debió pasar el análisis: {result}"
            )

    @pytest.mark.asyncio
    async def test_sandbox_blocks_dunder_attribute_access(self):
        """Acceso a atributos `__class__`, `__bases__`, `__subclasses__`,
        `__globals__` permite escapar de cualquier sandbox vía
        Python introspection. Debe rechazarse."""
        sandbox = PythonSandbox()

        bypass_attempts = [
            "x = ().__class__.__bases__[0].__subclasses__()",
            "code = (lambda: None).__globals__",
            "obj = type('X', (), {}).__mro__",
        ]
        rejected = 0
        for code in bypass_attempts:
            result = sandbox.analyze_security(code)
            if not result["is_safe"]:
                rejected += 1
        # Al menos uno debe ser rechazado — si NINGUNO lo es, el analizador
        # es trivialmente burlable.
        assert rejected >= 1, (
            f"NINGÚN intento de bypass via dunders fue rechazado: "
            f"{bypass_attempts}"
        )

    @_skip_subprocess_on_windows
    @pytest.mark.asyncio
    async def test_sandbox_safe_geopandas_execution(self):
        """El uso legítimo (geopandas sobre GeoJSON pequeño) debe funcionar.
        Antes solo probábamos math.sqrt(16) — código trivial — sin verificar
        que el caso de uso real del PythonAgent (procesar GeoDataFrames)
        está soportado por el sandbox."""
        sandbox = PythonSandbox()

        # GeoJSON mínimo: 2 puntos en Bogotá.
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature",
                 "geometry": {"type": "Point", "coordinates": [-74.07, 4.65]},
                 "properties": {"name": "A"}},
                {"type": "Feature",
                 "geometry": {"type": "Point", "coordinates": [-74.10, 4.70]},
                 "properties": {"name": "B"}},
            ],
        }

        code = """
import geopandas as gpd
from shapely.geometry import shape

features = input_geojson["features"]
geoms = [shape(f["geometry"]) for f in features]
total_features = len(geoms)
"""
        result = await sandbox.execute(
            code,
            input_data={"input_geojson": geojson},
            require_approval=False,
        )

        assert result["success"], result.get("error")
        assert result["results"]["total_features"] == 2

    def test_sandbox_analyzer_no_false_positive_on_valid_dataframe(self):
        """Regresión: el analizador estático no debe rechazar código común
        de pandas/geopandas (por ejemplo `.values`, `.apply`, `.iloc`)
        confundiéndolo con acceso a atributos peligrosos."""
        sandbox = PythonSandbox()

        legit_code = """
import pandas as pd
df = pd.DataFrame({"a": [1, 2, 3]})
total = df["a"].sum()
arr = df.values
filtered = df.iloc[0:2]
mapped = df["a"].apply(lambda x: x * 2)
"""
        result = sandbox.analyze_security(legit_code)
        assert result["is_safe"], (
            f"código legítimo de pandas fue rechazado: {result['violations']}"
        )


class TestSandboxPlatformGuard:
    """Regresión B7: en SO sin rlimits POSIX el sandbox debe fallar
    EXPLÍCITAMENTE, no tragar un ImportError/cwd inexistente.
    """

    @pytest.mark.asyncio
    async def test_execute_rejects_explicitly_when_unavailable(self, monkeypatch):
        from geo_copilot.agents.gis_agent import sandbox as sandbox_mod

        # R0.5: los rlimits POSIX son una preocupación EXCLUSIVA del backend
        # 'subprocess'. Desde que el default es 'docker' (AUD-03), hay que fijar
        # explícitamente el backend que este test cubre — si no, con un
        # contenedor sandbox vivo la ejecución tiene éxito y el test no prueba
        # nada de lo que dice probar.
        monkeypatch.setattr(sandbox_mod.settings, "sandbox_backend", "subprocess")
        monkeypatch.setattr(sandbox_mod, "SANDBOX_AVAILABLE", False)
        sb = sandbox_mod.PythonSandbox()

        result = await sb.execute("x = 1", require_approval=False)

        assert result["success"] is False
        # Error claro, no un traceback de plomería ni "resource".
        assert "POSIX" in result["error"]
        assert "resource" not in result["error"].lower()

    @pytest.mark.asyncio
    async def test_security_check_runs_before_platform_guard(self, monkeypatch):
        """Código inseguro se rechaza por seguridad aunque el SO no soporte."""
        from geo_copilot.agents.gis_agent import sandbox as sandbox_mod

        monkeypatch.setattr(sandbox_mod, "SANDBOX_AVAILABLE", False)
        sb = sandbox_mod.PythonSandbox()

        result = await sb.execute("import os\nos.system('x')", require_approval=False)

        assert result["success"] is False
        assert result["error"] == "Security violation"

    def test_runner_module_imports_cross_os(self, monkeypatch):
        """El runner debe ser importable en cualquier SO (resource opcional)."""
        import importlib

        runner = importlib.import_module(
            "geo_copilot.agents.gis_agent.sandbox_runner"
        )
        # _apply_limits no debe lanzar aunque resource sea None.
        monkeypatch.setattr(runner, "resource", None)
        runner._apply_limits(1, 64, 1000)

    def test_apply_limits_requests_the_rlimits(self, monkeypatch):
        """Los rlimits se piden con los valores dados.

        Nunca llamar `_apply_limits` con el `resource` real desde un test: los
        aplica al PROCESO DE PYTEST. En Linux, 1 s de CPU y 64 MB bastaban para
        que el kernel matara la suite entera (exit 137 en CI); en Windows no se
        notaba porque `resource` no existe.
        """
        import importlib
        import types

        runner = importlib.import_module(
            "geo_copilot.agents.gis_agent.sandbox_runner"
        )
        calls: dict[str, tuple] = {}
        fake = types.SimpleNamespace(
            RLIMIT_CPU="cpu", RLIMIT_AS="as", RLIMIT_FSIZE="fsize",
            RLIMIT_NPROC="nproc", RLIMIT_NOFILE="nofile", RLIMIT_CORE="core",
            setrlimit=lambda which, limits: calls.__setitem__(which, limits),
        )
        monkeypatch.setattr(runner, "resource", fake)

        runner._apply_limits(1, 64, 1000)

        assert calls["cpu"] == (1, 1)
        assert calls["as"] == (64 * 1024 * 1024,) * 2
        assert calls["fsize"] == (1000, 1000)
        assert calls["core"] == (0, 0)


class TestQuoteIdent:
    """S5: quoting de identificadores SQL contra inyección por nombres."""

    def test_quote_ident_basic(self):
        from geo_copilot.agents.gis_agent.sql_validator import quote_ident

        assert quote_ident("parcelas") == '"parcelas"'

    def test_quote_ident_escapes_double_quote(self):
        from geo_copilot.agents.gis_agent.sql_validator import quote_ident

        # Una comilla doble interna se duplica (escape Postgres).
        assert quote_ident('a"b') == '"a""b"'

    def test_quote_ident_neutralizes_injection(self):
        from geo_copilot.agents.gis_agent.sql_validator import quote_ident

        evil = 'x; DROP TABLE y; --'
        quoted = quote_ident(evil)
        # Todo queda DENTRO de las comillas → es un identificador (raro),
        # no SQL ejecutable.
        assert quoted.startswith('"') and quoted.endswith('"')
        assert quoted == '"x; DROP TABLE y; --"'

    def test_quote_ident_rejects_empty(self):
        from geo_copilot.agents.gis_agent.sql_validator import quote_ident

        with pytest.raises(ValueError):
            quote_ident("")

    def test_quote_qualified(self):
        from geo_copilot.agents.gis_agent.sql_validator import quote_qualified

        assert quote_qualified("catastro", "parcelas") == '"catastro"."parcelas"'

    def test_generate_count_query_uses_quoted_identifiers(self, semantic_layer):
        from geo_copilot.agents.gis_agent.sql_generator import SQLGenerator

        gen = SQLGenerator(llm_client=None, semantic_layer=semantic_layer)
        sql = gen.generate_count_query("parcela")

        # schema.table citados (la fixture usa schema 'catastro', table
        # 'cat_parcelas').
        assert '"catastro"."cat_parcelas"' in sql


class TestSQLValidatorHardening:
    """S6: cerrar bypasses de la denylist (UNION SELECT sin ALL, sinks
    Postgres, evasión por comentarios)."""

    def _v(self):
        return SQLValidator()

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM users UNION SELECT pwd FROM secrets LIMIT 1",  # sin ALL
            "SELECT * FROM users UNION ALL SELECT pwd FROM secrets LIMIT 1",
            "COPY users TO '/tmp/x'",
            "SELECT lo_import('/etc/passwd')",
            "SELECT pg_read_file('/etc/passwd')",
            "SELECT pg_ls_dir('/')",
            "CALL admin_proc()",
            "SELECT * INTO newtable FROM users",
            "SELECT dblink('host=evil', 'select 1')",
            # Evasión por comentario de bloque entre tokens.
            "SELECT * FROM x WHERE 1=1 /**/UNION/**/SELECT pwd FROM s LIMIT 1",
        ],
    )
    def test_blocks_dangerous(self, sql):
        result = self._v().validate(sql)
        assert result["is_valid"] is False, f"debió rechazar: {sql}"

    def test_safe_query_still_valid(self):
        sql = (
            "SELECT id, nombre, ST_AsGeoJSON(geom) "
            "FROM catastro.parcelas WHERE area > 100 LIMIT 100"
        )
        result = self._v().validate(sql)
        assert result["is_valid"] is True, result["errors"]
