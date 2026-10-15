# 22 · Rutas e isócronas (Valhalla) — `rutas-mcp`

Preguntas que no se responden con una línea recta: «¿cuánto se tarda de A a B?», «¿qué zona queda a 15
minutos del hospital?», «¿cuál de estos colegios está más cerca en tiempo?». `rutas-mcp`
(`services/rutas_mcp/`, puerto 9910) rutea sobre la red vial de OpenStreetMap con **Valhalla**.

## Herramientas

| Herramienta | Qué hace | Sale |
|---|---|---|
| `rutas_ruta(origen \| origen_lon+origen_lat, destino \| …, paradas?, modo?)` | Ruta con distancia, tiempo e indicaciones (en español) | capa de línea |
| `rutas_isocronas(minutos, centro \| centro_lon+centro_lat, modo?)` | Zona alcanzable en N minutos (hasta 4 tiempos, máx. 120), con su área geodésica | capa de polígonos |
| `rutas_matriz(origenes \| origenes_lonlat, destinos \| destinos_lonlat, modo?)` | Tiempos y distancias de cada origen a cada destino (hasta 50×50) y el destino más cercano en tiempo por origen | tabla |

Modos: `auto`, `a_pie`, `bicicleta`, `camion`, `moto`. Cada extremo se da como **referencia** (`punto`
marcado, una capa de un elemento, la selección) o con **números** (p. ej. el centro de `lugares_buscar`).
De un polígono se usa un punto interior y se dice. Los tiempos son estimaciones del motor **sin tráfico en
vivo**.

## El motor

- **Autoalojado** (producción): servicio `valhalla` del compose, perfil `rutas`
  (`ghcr.io/valhalla/valhalla-scripted:3.9.1`). La primera vez descarga el extracto de Colombia de Geofabrik
  y construye las teselas (varios GB de RAM y tiempo de CPU); después arranca desde el volumen
  `valhalla_tiles`. Con él: `VALHALLA_URL=http://valhalla:8002`.
- **Desarrollo**: sin el autoalojado, `VALHALLA_URL` apunta por defecto a la instancia pública de FOSSGIS
  (misma versión 3.9.1). Su política de uso pide poco tráfico: **no** para producción.

## Comportamientos medidos (2026-10-15)

- Plaza de Bolívar → Chía en carro: 33,09 km; OSRM (otro motor, mismos datos OSM) 33,94 km. Los TIEMPOS
  difieren mucho (72 contra 40 min): cada motor modela las velocidades a su manera; por eso se dicen como
  estimación.
- Isócronas consistentes con rutas reales: lo de dentro de la de 15 min se alcanza en ~15 min; lo de
  fuera, no (`tests/test_rutas_mcp_vivo.py`).
- Rutas imposibles (p. ej. > 1 500 km o un punto en el mar): el motivo del motor en palabras.

V5 (`e2e-integration/rutas-mcp.spec.ts`): ruta entre lugares nombrados (vía `lugares-mcp`) 5/6 — el fallo
fue del enrutador del núcleo (respondió sin herramientas) —; isócrona a pie 3/3.

## Probar

- Unidad: `pytest tests/test_rutas_mcp.py`.
- En vivo: `pytest -m integration tests/test_rutas_mcp_vivo.py`.
