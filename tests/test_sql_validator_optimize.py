"""DAT (auditoría E2E) — SQLValidator.optimize() no debe malformar SQL con
funciones anidadas al inyectar ST_Simplify.

El bug: el patrón `ST_AsGeoJSON\\(\\s*([^)]+)\\s*\\)` corta en el PRIMER ')', así
que para `ST_AsGeoJSON(ST_Transform(geom, 4326))` capturaba `ST_Transform(geom,
4326` (incompleto) y la sustitución metía la tolerancia como 3er arg de
ST_Transform, dejando ST_Simplify con un solo argumento → SQL inválido.
"""

from __future__ import annotations

from geo_copilot.agents.gis_agent.sql_validator import SQLValidator


def _opt(sql: str) -> str:
    return SQLValidator(max_limit=100000).optimize(sql)


def test_nested_st_transform_no_se_malforma():
    sql = "SELECT ST_AsGeoJSON(ST_Transform(geom, 4326)) AS g FROM t LIMIT 10"
    out = _opt(sql)
    # No debe meter la tolerancia dentro de ST_Transform (3er arg inválido) ni
    # dejar un ST_Simplify roto. Con función anidada, se deja tal cual.
    assert "ST_Transform(geom, 4326, 0.0001)" not in out
    assert "ST_AsGeoJSON(ST_Transform(geom, 4326))" in out


def test_columna_simple_si_se_envuelve():
    sql = "SELECT ST_AsGeoJSON(geom) AS g FROM t LIMIT 10"
    out = _opt(sql)
    assert "ST_AsGeoJSON(ST_Simplify(geom, 0.0001))" in out


def test_ya_simplificado_no_se_toca():
    sql = "SELECT ST_AsGeoJSON(ST_Simplify(geom, 0.001)) AS g FROM t LIMIT 10"
    out = _opt(sql)
    # No debe doble-envolver.
    assert out.count("ST_Simplify") == 1
