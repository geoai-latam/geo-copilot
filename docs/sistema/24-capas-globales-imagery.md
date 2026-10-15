# 24 · Capas globales en imagery-mcp: cobertura, población y radar

Tres herramientas nuevas de `imagery-mcp` (`services/imagery_mcp/imagery_mcp/tools_globales.py`), para preguntas
que la óptica de Sentinel-2/Landsat no responde sola.

| Herramienta | Fuente | Qué da |
|---|---|---|
| `imagery_land_cover(aoi_geojson)` | ESA WorldCover v200 (2021, 10 m) | km² y % por clase (árboles, pastizal, cultivos, construido, agua…) hasta 2°×2° |
| `imagery_population(aoi_geojson, year)` | WorldPop R2024B (100 m, «constrained», 2015–2030) | personas estimadas en el área (centro de píxel dentro) |
| `imagery_sar(aoi_geojson, date_from?, date_to?, water_threshold_db?)` | Sentinel-1 RTC (Planetary Computer) | VV/VH en dB de la escena más reciente y % de superficie «lisa» (agua abierta probable) |

## Comportamientos medidos (2026-10-15)

- WorldCover: COG públicos de la ESA por celdas de 3°, el servidor atiende rangos: se lee solo la ventana.
- WorldPop: su servidor **ignora `Range`** (responde 200 con el archivo entero): se descarga una vez por año
  y país (Colombia 2025: 64 MB, ~90 s) a la caché del contenedor (`IMAGERY_CACHE_DIR`). Por eso la política
  del hub para imagery sube a 180 s.
- WorldPop es un **modelo**, no un censo: Chapinero 2018 = 164 059 (el censo del DANE dice 150 699); después
  de 2020 son proyecciones. Las herramientas lo dicen.
- El radar ve a través de las nubes. El umbral de agua es un **criterio** (−15 a −20 dB en la literatura).

## Verdades (`tests/test_imagery_globales_vivo.py`)

- Radar contra óptico, dos sensores distintos: Embalse de Tominé 29,4 % «liso» (S1) contra 29,0 % de agua
  (WorldCover); centro de Bogotá ~0 % los dos, mayoría «Construido» (83,5 %).
- WorldPop: la suma de la herramienta = un cálculo independiente píxel a píxel (Chapinero 2018: 164 059).
- Chía (límite OSM): Árboles 38,12 %, Pastizal 33,19 %, Construido 21,07 %; WorldPop 2025: 174 695.

V5 (`e2e-integration/imagery-globales.spec.ts`, LLM real): cobertura 3/3, radar 3/3, población 2/3.

## Lo que destapó la V5 en el núcleo

- El agente pasaba la ref de `lugares_buscar` («R10687625») como zona: el rechazo ahora dice que es un LUGAR y
  que hay que traer su límite con `lugares_limite`.
- La cobertura de `viewport` (2 629 km²) se presentó como la de Chía (80 km²): la observación ahora dice qué
  zona fue cada referencia («`aoi_geojson` = la zona VISIBLE del mapa…»).
- Pendiente (propuesta): que el hub resuelva solo una ref de lugar trayendo su límite (declarado por el servidor
  de lugares en su configuración), para no depender de que el agente encadene `lugares_limite`.
