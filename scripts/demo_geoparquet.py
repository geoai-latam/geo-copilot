"""Datos de demostración para archivos-mcp (T5.6): lotes del catastro de Chapinero y Teusaquillo
exportados a GeoParquet con DuckDB (no se versionan: salen de la BD interna del despliegue).

    python scripts/demo_geoparquet.py            # -> data/archivos/predios_chapinero.parquet

Lee la BD con el contenedor `geo_copilot_db` (usuario y BD de su propio entorno).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import duckdb

BBOX = (-74.075, 4.625, -74.045, 4.665)   # Chapinero–Teusaquillo
SALIDA = Path(__file__).resolve().parents[1] / "data" / "archivos" / "predios_chapinero.parquet"


def main() -> None:
    def env(nombre: str) -> str:
        return subprocess.run(["docker", "exec", "geo_copilot_db", "printenv", nombre], check=True,
                              capture_output=True, text=True).stdout.strip()

    x0, y0, x1, y1 = BBOX
    sql = ("COPY (SELECT json_build_object('lotcodigo', lotcodigo, 'manzcodigo', manzcodigo, 'lotupredia', "
           "lotupredia, 'area_m2', round(ST_Area(shape::geography)::numeric, 2), 'geom', ST_AsText(shape)) "
           f"FROM catastro.lotes WHERE shape && ST_MakeEnvelope({x0}, {y0}, {x1}, {y1}, 4326)) TO STDOUT")
    salida = subprocess.run(["docker", "exec", "geo_copilot_db", "psql", "-U", env("POSTGRES_USER"), "-d",
                             env("POSTGRES_DB"), "-tAc", sql], check=True, capture_output=True, text=True).stdout
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as tmp:
        tmp.write(salida)
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    con.execute(f"COPY (SELECT lotcodigo, manzcodigo, lotupredia, area_m2, ST_GeomFromText(geom) AS geom "
                f"FROM read_json_auto('{tmp.name}')) TO '{SALIDA.as_posix()}' (FORMAT PARQUET)")
    n = con.execute(f"SELECT count(*) FROM '{SALIDA.as_posix()}'").fetchone()[0]
    os.unlink(tmp.name)
    print(f"{n} lotes -> {SALIDA} ({SALIDA.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
