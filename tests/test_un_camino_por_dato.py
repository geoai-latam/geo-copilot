"""F2 del plan de calidad: UN camino por dato.

El catastro (BD interna) se publicaba también como fuente «catastro» del MCP `sql`. El agente tenía
dos caminos al mismo dato: gastaba pasos eligiendo, y en la batería de verdades llegó a responder
con la BD interna diciendo que era el archivo. La BD interna va por `query_database`; el MCP `sql`
publica solo bases que el núcleo NO ve.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
COMPOSE = RAIZ / "docker" / "docker-compose.yml"


def _entorno(servicio: str) -> dict[str, str]:
    env = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"][servicio]["environment"]
    if isinstance(env, dict):
        return {k: str(v) for k, v in env.items()}
    return dict(linea.split("=", 1) for linea in env)


def test_el_mcp_sql_no_publica_la_bd_interna():
    env = _entorno("postgis-mcp")
    fuentes = json.loads(env["SQL_SOURCES"])
    for fuente in fuentes:
        dsn = env.get(fuente["dsn_env"], "")
        # la BD interna del compose es el servicio `postgis`, base geo_copilot
        assert "@postgis:" not in dsn and "/geo_copilot" not in dsn, (
            f"la fuente «{fuente['id']}» apunta a la BD interna: ese dato ya tiene su camino "
            "(query_database); dos caminos al mismo dato confunden al agente")
    assert "catastro" not in {f["id"] for f in fuentes}
