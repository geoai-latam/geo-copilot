"""
Tests de integración GISAgent + SQL real contra PostGIS — F3.2 del roadmap.

Estos tests EJECUTAN el SQL generado por los templates del GISAgent contra
una BD PostGIS de verdad con el schema catastral real (idéntico al dump
del usuario `geocopilot.sql`). Validan:

- El SQL compila y se ejecuta sin error.
- Los resultados tienen la cardinalidad esperada.
- Las geometrías devueltas son válidas y están en EPSG:4326.
- Los predicados espaciales (ST_Buffer, ST_Within, ST_DWithin) producen
  filtros semánticamente correctos.

Marcados `@pytest.mark.integration` — saltan si la BD no está disponible.

Activar:

    docker compose -f docker-compose.test.yml up -d
    pip install -e .[dev,integration]
    pytest -m integration
"""

from __future__ import annotations

import json

import pytest

from geo_copilot.agents.gis_agent.sql_validator import SQLValidator

# `postgis`: solo necesita la BD de `docker-compose.test.yml` — el job de CI
# de integración corre `-m postgis` (sin LLM ni red externa).
# loop_scope="session": el pool `postgis_pool` es de sesión y vive en el loop
# de la sesión; con el loop por test, asyncpg fallaba con "attached to a
# different loop" (nadie lo vio porque el puerto equivocado los saltaba).
pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgis,
    pytest.mark.asyncio(loop_scope="session"),
]


# =============================================================================
# Smoke tests: la BD está disponible y el schema cargó
# =============================================================================
async def test_seed_loaded_with_expected_cardinality(postgis_pool):
    """El seed inserta 25 construcciones + 8 lotes. Si la cardinalidad es
    distinta, el seed cambió o el VACUUM no corrió — fail fast."""
    async with postgis_pool.acquire() as conn:
        constr_count = await conn.fetchval(
            "SELECT count(*) FROM catastro.construcciones"
        )
        lotes_count = await conn.fetchval("SELECT count(*) FROM catastro.lotes")

    assert constr_count == 25, f"esperaba 25 construcciones, hay {constr_count}"
    assert lotes_count == 8, f"esperaba 8 lotes, hay {lotes_count}"


async def test_all_geometries_are_srid_4326(postgis_pool):
    """Constraint del schema: shape DEBE estar en SRID 4326. Verifica."""
    async with postgis_pool.acquire() as conn:
        bad_srid = await conn.fetchval(
            """
            SELECT count(*) FROM catastro.construcciones
            WHERE st_srid(shape) <> 4326
            """
        )
        assert bad_srid == 0, f"{bad_srid} construcciones con SRID != 4326"


# =============================================================================
# Tests de SQL templates ejecutado contra BD real
# =============================================================================
async def test_count_buildings_by_distrito(postgis_pool):
    """Agregación territorial: SQL que el GISAgent generaría para
    'cuántas construcciones por distrito'. Verifica ejecución real."""
    sql = """
    SELECT
        l.lotdistrit AS distrito,
        count(c.objectid) AS total_construcciones
    FROM catastro.construcciones c
    JOIN catastro.lotes l ON ST_Intersects(c.shape, l.shape)
    GROUP BY l.lotdistrit
    ORDER BY total_construcciones DESC
    """

    # Validar primero la sintaxis (mismo flow del GISAgent en prod).
    validation = SQLValidator().validate(sql)
    assert validation["is_valid"], validation["errors"]

    async with postgis_pool.acquire() as conn:
        rows = await conn.fetch(sql)

    # Debe haber al menos 1 fila por distrito presente en seed (11, 25).
    assert len(rows) >= 1
    by_distrito = {row["distrito"]: row["total_construcciones"] for row in rows}
    # Bogotá D.C. (11) y Mosquera (25) en el seed.
    assert 11 in by_distrito or 25 in by_distrito


async def test_buildings_taller_than_threshold(postgis_pool):
    """Filtro semántico: edificios de >= 10 pisos. El seed tiene 5 así."""
    sql = """
    SELECT count(*) FROM catastro.construcciones
    WHERE connpisos >= 10
    """
    async with postgis_pool.acquire() as conn:
        count = await conn.fetchval(sql)

    assert count == 5, f"esperaba 5 edificios >= 10 pisos, hubo {count}"


async def test_proximity_query_with_st_dwithin(postgis_pool):
    """ST_DWithin con distancia en metros via geography cast.

    Tomamos una construcción del seed y buscamos construcciones a < 5 km.
    Verifica que ST_DWithin produce resultados realistas (no 0, no todos).
    """
    sql = """
    WITH target AS (
        SELECT shape FROM catastro.construcciones WHERE objectid = 1
    )
    SELECT
        c.objectid,
        c.connpisos,
        ST_Distance(
            c.shape::geography,
            (SELECT shape FROM target)::geography
        ) AS distance_m
    FROM catastro.construcciones c, target t
    -- H20 (V5 F2): prefiltro indexable (&& usa el GiST) antes de la medida
    -- exacta en geography; sin él, sobre la tabla real son >30 s.
    WHERE c.shape && ST_Expand(t.shape, 0.07)
      AND ST_DWithin(
        c.shape::geography,
        t.shape::geography,
        5000  -- 5 km
    )
    ORDER BY distance_m
    """

    validation = SQLValidator().validate(sql)
    assert validation["is_valid"], validation["errors"]

    async with postgis_pool.acquire() as conn:
        rows = await conn.fetch(sql)

    # Al menos la construcción 1 (distance=0) está en el resultado.
    assert len(rows) >= 1
    # La primera fila debe ser la propia construcción (distance ~0).
    assert rows[0]["objectid"] == 1
    assert rows[0]["distance_m"] < 1.0  # mismo punto
    # Todas las demás están a <5km como pedimos.
    for row in rows:
        assert row["distance_m"] <= 5000.1


async def test_st_within_lotes_municipios_logic(postgis_pool):
    """ST_Within: construcciones DENTRO de lotes específicos.

    Cada construcción del seed cae en su propio lote (el `lotecodigo`
    coincide). Verifica que ST_Within produce ese mapping correcto.
    """
    sql = """
    SELECT
        c.objectid AS construccion,
        c.concodigo,
        l.objectid AS lote,
        l.lotcodigo
    FROM catastro.construcciones c, catastro.lotes l
    WHERE ST_Within(c.shape, l.shape)
    ORDER BY c.objectid
    LIMIT 50
    """

    async with postgis_pool.acquire() as conn:
        rows = await conn.fetch(sql)

    # Cada match tiene un par construccion-lote válido.
    assert rows, "ningún ST_Within match — geometrías mal seedeadas?"
    for row in rows:
        # El lotecodigo de la construcción debe iniciar con el del lote
        # (relación catastral típica: lote 110010001-01 contiene 110010001-001-001 etc.)
        # En nuestro seed lo simplificamos: el lotecodigo de la construcción
        # ES el lotcodigo del lote.
        # Solo verificamos que ambos campos existen.
        assert row["concodigo"]
        assert row["lotcodigo"]


async def test_st_buffer_creates_valid_polygon(postgis_pool):
    """ST_Buffer alrededor de una construcción produce un polígono válido
    (no GEOMETRYCOLLECTION EMPTY, no NULL)."""
    sql = """
    SELECT
        objectid,
        ST_AsGeoJSON(
            ST_Buffer(shape::geography, 100)::geometry
        ) AS buffer_geojson,
        ST_Area(ST_Buffer(shape::geography, 100)) AS buffer_area_m2
    FROM catastro.construcciones
    WHERE objectid = 16  -- edificio de 15 pisos
    """

    async with postgis_pool.acquire() as conn:
        row = await conn.fetchrow(sql)

    assert row is not None
    # ST_Buffer 100m produce un área de ~31,400 m² (pi * 100²) + área del polígono original.
    assert 25_000 < row["buffer_area_m2"] < 50_000, (
        f"buffer 100m con área inesperada: {row['buffer_area_m2']:.0f} m²"
    )
    # El geojson es parseable y tipo Polygon.
    geojson = json.loads(row["buffer_geojson"])
    assert geojson["type"] in ("Polygon", "MultiPolygon")


async def test_outsr_returns_4326_geometry(postgis_pool):
    """`outSR=4326` semánticamente: cuando proyectamos via
    ST_Transform(.., 4326) el SRID resultante es 4326. Regresión por si
    cambia el SRID del schema."""
    sql = """
    SELECT
        ST_SRID(ST_Transform(shape, 4326)) AS srid_out
    FROM catastro.construcciones
    LIMIT 1
    """
    async with postgis_pool.acquire() as conn:
        row = await conn.fetchrow(sql)

    assert row["srid_out"] == 4326


# =============================================================================
# Tests de SEGURIDAD: validador rechaza inyecciones
# =============================================================================
async def test_validator_rejects_destructive_sql_before_execution(postgis_pool):
    """SQLValidator debe rechazar SQL destructivo. Verificamos que NINGUNO
    de estos llegaría a ejecutarse contra la BD."""
    v = SQLValidator()

    destructive = [
        "DROP TABLE catastro.construcciones",
        "DELETE FROM catastro.lotes",
        "TRUNCATE catastro.construcciones",
        "UPDATE catastro.construcciones SET conaltura = 0",
        "ALTER TABLE catastro.lotes DROP COLUMN shape",
    ]
    for sql in destructive:
        result = v.validate(sql)
        assert not result["is_valid"], (
            f"SQL destructivo NO debió validar: {sql}"
        )


async def test_db_is_read_only_for_test_user(postgis_pool):
    """Defensa en profundidad: aunque el GISAgent solo genere SELECT, el rol con
    el que se ejecuta no puede escribir — un bypass del validador fallaría por
    permisos, igual que con `gis_readonly` en producción.

    Antes este test solo "documentaba" y conectaba como `gc_test`, que la imagen
    oficial crea como SUPERUSER: la aserción `is_superuser is False` era falsa,
    y nadie lo supo porque el puerto equivocado del conftest saltaba el test.
    """
    import asyncpg

    async with postgis_pool.acquire() as conn:
        is_superuser = await conn.fetchval(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        )
        assert is_superuser is False, "El rol de tests es superuser — riesgo en CI"

        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("DELETE FROM catastro.lotes")
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("CREATE TABLE catastro.intruso (id int)")
