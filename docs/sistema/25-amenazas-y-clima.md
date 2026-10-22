# 25 · Amenazas y clima — `amenazas-mcp`

Sismos, incendios activos, caudales de ríos y clima de fuentes públicas **sin clave**
(`services/amenazas_mcp/`, puerto 9930). Las capas OFICIALES de amenaza de Colombia (SGC: sísmica NSR-10,
volcánica, movimientos en masa) están en `ogc-mcp` (doc 20).

| Herramienta | Fuente | Da |
|---|---|---|
| `amenazas_sismos(area \| bbox, desde?, hasta?, magnitud_min?)` | USGS FDSN | capa de sismos, los 5 mayores y el conteo del catálogo con los mismos filtros |
| `amenazas_incendios(area \| bbox, periodo, sensor)` | NASA FIRMS (CSV regionales públicos: 24 h, 48 h, 7 d) | capa de focos de calor con fecha, confianza y FRP |
| `amenazas_caudal(punto \| lon+lat, dias_pasados?, dias_pronostico?)` | GloFAS por la Flood API de Open-Meteo | serie diaria de la celda del punto y caudal medio de las vecinas |
| `clima_serie(desde, hasta, punto \| lon+lat, fuente)` | ERA5 (Open-Meteo; pronóstico ≤16 días) o NASA POWER | serie diaria, totales del periodo, resumen mensual y la elevación de la malla |

Las tools con zona devuelven el rectángulo consultado (`zona_consultada_bbox`) y dicen que `viewport` es solo lo
visible: un país o departamento va por su límite.

## Comportamientos medidos (2026-10-15)

- **GloFAS es una malla de 0,05°**: en Honda la celda del punto da 1,5 m³/s (un afluente) y a ~5 km el
  Magdalena ~2 500. La herramienta da las vecinas por caudal para que el agente no confunda el río.
- **Cada fuente de clima modela el punto a su elevación**: Bogotá (2 600 m) = 2 594 m en ERA5 y 1 790 m en
  POWER → enero 2023: 11,9 °C contra 17,5 °C. Se dice en los hechos.
- **FIRMS**: los CSV regionales no piden clave; solo cubren los últimos 7 días (más atrás pide `MAP_KEY`).
- Sismos M≥4 en el rectángulo de Colombia, oct 2025–oct 2026: 191 (= `/count` directo de la USGS).

## Verdades (`tests/test_amenazas_mcp_vivo.py`)

`/count` directo de la USGS; recuento propio del CSV de FIRMS; el Magdalena > 500 m³/s en las vecinas; cada
fuente de clima informa su elevación y la más alta da menos temperatura.

## Probar

- Unidad: `pytest tests/test_amenazas_mcp.py`.
- En vivo: `pytest -m integration tests/test_amenazas_mcp_vivo.py`.
- V5: `e2e-integration/amenazas-mcp.spec.ts`.
