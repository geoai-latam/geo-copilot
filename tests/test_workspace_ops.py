"""S2.5 — capacidades espaciales deterministas: corrección geométrica contra PostGIS REAL.

Cada cifra se contrasta con una referencia independiente (shapely en UTM, o
conteos construidos a mano), no con la propia implementación.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime

import pytest
from pyproj import Transformer
from shapely.geometry import LineString, Point, box, mapping, shape
from shapely.ops import transform

from geo_copilot.platform.contracts import Provenance
from geo_copilot.platform.workspace import DatasetStore, WorkspaceError, ops

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgis,
    pytest.mark.asyncio(loop_scope="session"),
]

PROC = Provenance(capability="test", produced_at=datetime.now(UTC))
# Bogotá está en la zona UTM 18N.
A_UTM = Transformer.from_crs(4326, 32618, always_xy=True).transform
A_GEO = Transformer.from_crs(32618, 4326, always_xy=True).transform
X0, Y0 = A_UTM(-74.08, 4.60)


def _ws() -> str:
    return f"sesion-{uuid.uuid4()}"


def _geo(g):
    """Geometría construida en metros (UTM 18N) → EPSG:4326."""
    return mapping(transform(A_GEO, g))


def _fc(geoms_props):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": _geo(g), "properties": p} for g, p in geoms_props
    ]}


async def _cargar(store, ws, nombre, geoms_props):
    return await store.ingest_features(ws, nombre, _fc(geoms_props), crs="EPSG:4326", provenance=PROC)


def _cuadro(dx, dy, lado):
    return box(X0 + dx, Y0 + dy, X0 + dx + lado, Y0 + dy + lado)


# --- medir ---------------------------------------------------------------


async def test_area_de_un_km2(workspace_pool):
    store, ws = DatasetStore(workspace_pool), _ws()
    ref = await _cargar(store, ws, "km2", [(_cuadro(0, 0, 1000), {"n": 1})])
    h = (await ops.medir(store, ws, ref.id)).hechos
    # UTM distorsiona ~0.04 % a 4.6°N cerca del meridiano central: 0.5 % sobra.
    assert h["area_total_m2"] == pytest.approx(1_000_000, rel=5e-3)
    assert h["area_total_ha"] == pytest.approx(100, rel=5e-3)


async def test_area_total_no_cuenta_dos_veces_el_solape(workspace_pool):
    store, ws = DatasetStore(workspace_pool), _ws()
    ref = await _cargar(store, ws, "solapados", [
        (_cuadro(0, 0, 1000), {"n": 1}), (_cuadro(500, 0, 1000), {"n": 2}),
    ])
    h = (await ops.medir(store, ws, ref.id)).hechos
    assert h["area_total_m2"] == pytest.approx(1_500_000, rel=5e-3)
    assert h["suma_areas_individuales_m2"] == pytest.approx(2_000_000, rel=5e-3)


async def test_longitud_de_una_via(workspace_pool):
    store, ws = DatasetStore(workspace_pool), _ws()
    via = LineString([(X0, Y0), (X0 + 3000, Y0), (X0 + 3000, Y0 + 4000)])
    ref = await _cargar(store, ws, "vía", [(via, {"nombre": "Av"})])
    h = (await ops.medir(store, ws, ref.id)).hechos
    assert h["longitud_total_m"] == pytest.approx(7000, rel=5e-3)


# --- buffer (E2.6) ----------------------------------------------------------


async def test_buffer_de_500m_a_una_via_y_su_area(workspace_pool):
    store, ws = DatasetStore(workspace_pool), _ws()
    via = LineString([(X0, Y0), (X0 + 2000, Y0)])
    ref = await _cargar(store, ws, "Vías", [(via, {"nombre": "Calle 1"})])
    res = await ops.buffer(store, ws, ref.id, 500)
    # referencia: el mismo buffer con shapely en metros (misma aproximación de arcos)
    esperado = via.buffer(500).area
    assert res.hechos["area_total_m2"] == pytest.approx(esperado, rel=1e-2)
    # y la fórmula analítica 2rL + πr² (la de shapely la aproxima por polígono)
    assert res.hechos["area_total_m2"] == pytest.approx(2 * 500 * 2000 + math.pi * 500**2, rel=1e-2)
    assert res.ref is not None and res.ref.feature_count == 1
    assert [f.name for f in res.ref.fields] == ["nombre"]  # conserva atributos
    assert res.ref.provenance.capability == "core.buffer"


async def test_el_buffer_llega_al_radio_pedido_y_no_se_queda_corto(workspace_pool):
    """V5 F5: con 8 segmentos por cuadrante el círculo de 200 m quedaba ~1 m por dentro y un lote a
    199,5 m caía fuera; con 64, lo que está a < 200 m queda dentro (error ~1,5 cm)."""
    store, ws = DatasetStore(workspace_pool), _ws()
    sede = Point(X0, Y0)
    ref = await _cargar(store, ws, "sede", [(sede, {"n": 1})])
    res = await ops.buffer(store, ws, ref.id, 200)
    fc = await store.to_geojson(ws, res.ref.id)
    circulo = shape(fc["features"][0]["geometry"])
    # puntos a 199,5 m en varias direcciones (incluidas las de peor cuerda de un polígono de 32 lados)
    for grados in (0, 5.625, 11.25, 45, 95.625):
        a = math.radians(grados)
        dentro = Point(X0 + 199.5 * math.cos(a), Y0 + 199.5 * math.sin(a))
        assert circulo.intersects(Point(*A_GEO(dentro.x, dentro.y))), \
            f"un punto a 199,5 m (rumbo {grados}°) quedó fuera del buffer de 200 m"


async def test_buffer_disuelto_une_los_solapes(workspace_pool):
    store, ws = DatasetStore(workspace_pool), _ws()
    p1, p2 = Point(X0, Y0), Point(X0 + 100, Y0)
    ref = await _cargar(store, ws, "puntos", [(p1, {"n": 1}), (p2, {"n": 2})])
    separado = await ops.buffer(store, ws, ref.id, 100)
    disuelto = await ops.buffer(store, ws, ref.id, 100, disolver=True)
    esperado = p1.buffer(100).union(p2.buffer(100)).area
    assert disuelto.ref.feature_count == 1 and separado.ref.feature_count == 2
    assert disuelto.hechos["area_total_m2"] == pytest.approx(esperado, rel=1e-2)
    assert separado.hechos["suma_areas_individuales_m2"] == pytest.approx(
        2 * math.pi * 100**2, rel=1e-2,
    )


@pytest.mark.parametrize("metros", [0, -5, 200_000])
async def test_buffer_con_distancia_absurda_se_rechaza(workspace_pool, metros):
    store, ws = DatasetStore(workspace_pool), _ws()
    ref = await _cargar(store, ws, "p", [(Point(X0, Y0), {"n": 1})])
    with pytest.raises(ops.OperacionInvalida):
        await ops.buffer(store, ws, ref.id, metros)


# --- overlay -----------------------------------------------------------------


async def test_interseccion_y_diferencia(workspace_pool):
    store, ws = DatasetStore(workspace_pool), _ws()
    a = await _cargar(store, ws, "A", [(_cuadro(0, 0, 1000), {"zona": "a"})])
    b = await _cargar(store, ws, "B", [(_cuadro(500, 0, 1000), {"zona": "b"})])
    inter = await ops.overlay(store, ws, a.id, b.id, modo="intersection")
    assert inter.hechos["area_total_m2"] == pytest.approx(500_000, rel=5e-3)
    assert {f.name for f in inter.ref.fields} == {"a_zona", "b_zona"}
    dif = await ops.overlay(store, ws, a.id, b.id, modo="difference")
    assert dif.hechos["area_total_m2"] == pytest.approx(500_000, rel=5e-3)


async def test_diferencia_sin_solape_conserva_la_geometria(workspace_pool):
    store, ws = DatasetStore(workspace_pool), _ws()
    a = await _cargar(store, ws, "A", [(_cuadro(0, 0, 1000), {"zona": "a"})])
    b = await _cargar(store, ws, "B", [(_cuadro(5000, 0, 1000), {"zona": "b"})])
    dif = await ops.overlay(store, ws, a.id, b.id, modo="difference")
    assert dif.hechos["area_total_m2"] == pytest.approx(1_000_000, rel=5e-3)


# --- unión espacial y agregación ----------------------------------------------


def _manzanas_y_puntos():
    manzanas = [(_cuadro(i * 1000, 0, 1000), {"manzana": f"M{i}"}) for i in range(3)]
    # 3 puntos en M0, 1 en M1, 0 en M2, 1 fuera de todo
    puntos = [
        (Point(X0 + 100, Y0 + 100), {"valor": 10}),
        (Point(X0 + 200, Y0 + 500), {"valor": 20}),
        (Point(X0 + 900, Y0 + 900), {"valor": 30}),
        (Point(X0 + 1500, Y0 + 500), {"valor": 5}),
        (Point(X0 + 9000, Y0 + 500), {"valor": 99}),
    ]
    return manzanas, puntos


async def test_union_espacial_within(workspace_pool):
    store, ws = DatasetStore(workspace_pool), _ws()
    m, p = _manzanas_y_puntos()
    zonas, pts = await _cargar(store, ws, "Manzanas", m), await _cargar(store, ws, "Puntos", p)
    res = await ops.union_espacial(store, ws, pts.id, zonas.id, predicado="within")
    assert res.hechos["parejas"] == 4 and res.hechos["elementos_de_a_con_pareja"] == 4
    assert res.hechos["elementos_de_a"] == 5
    filas = await store.filas(ws, (
        f'SELECT a_valor, b_manzana FROM "{res.ref.storage.schema_name}"."{res.ref.storage.table}" '
        "ORDER BY a_valor"
    ))
    assert [(f["a_valor"], f["b_manzana"]) for f in filas] == [
        (5, "M1"), (10, "M0"), (20, "M0"), (30, "M0"),
    ]


async def test_union_espacial_dwithin_en_metros(workspace_pool):
    store, ws = DatasetStore(workspace_pool), _ws()
    a = await _cargar(store, ws, "A", [(Point(X0, Y0), {"n": 1})])
    b = await _cargar(store, ws, "B", [
        (Point(X0 + 90, Y0), {"d": 90}), (Point(X0 + 110, Y0), {"d": 110}),
    ])
    res = await ops.union_espacial(store, ws, a.id, b.id, predicado="dwithin", metros=100)
    assert res.hechos["parejas"] == 1


async def test_agregacion_conteo_y_suma_por_zona(workspace_pool):
    store, ws = DatasetStore(workspace_pool), _ws()
    m, p = _manzanas_y_puntos()
    zonas, pts = await _cargar(store, ws, "Manzanas", m), await _cargar(store, ws, "Puntos", p)
    conteo = await ops.agregar_por_zonas(store, ws, zonas.id, pts.id)
    t = f'"{conteo.ref.storage.schema_name}"."{conteo.ref.storage.table}"'
    filas = await store.filas(ws, f"SELECT manzana, conteo FROM {t} ORDER BY manzana")
    assert [(f["manzana"], f["conteo"]) for f in filas] == [("M0", 3), ("M1", 1), ("M2", 0)]
    assert conteo.hechos["zonas_con_datos"] == 2 and conteo.hechos["total"] == 4

    suma = await ops.agregar_por_zonas(store, ws, zonas.id, pts.id, estadistico="sum", campo="valor")
    t = f'"{suma.ref.storage.schema_name}"."{suma.ref.storage.table}"'
    filas = await store.filas(ws, f"SELECT manzana, sum_valor FROM {t} ORDER BY manzana")
    assert [(f["manzana"], f["sum_valor"]) for f in filas] == [("M0", 60), ("M1", 5), ("M2", None)]


async def test_agregacion_con_campo_inexistente_o_no_numerico(workspace_pool):
    store, ws = DatasetStore(workspace_pool), _ws()
    m, p = _manzanas_y_puntos()
    zonas, pts = await _cargar(store, ws, "Manzanas", m), await _cargar(store, ws, "Puntos", p)
    with pytest.raises(ops.OperacionInvalida, match="no tiene el campo"):
        await ops.agregar_por_zonas(store, ws, zonas.id, pts.id, estadistico="sum", campo="precio")
    with pytest.raises(ops.OperacionInvalida, match="no es numérico"):
        await ops.agregar_por_zonas(store, ws, pts.id, zonas.id, estadistico="sum", campo="manzana")


# --- autocorrelación -----------------------------------------------------------


def _rejilla_con_cluster(n=10):
    """Rejilla n×n: la mitad oeste con valores altos, la este bajos (+ ruido fijo)."""
    celdas = []
    for i in range(n):
        for j in range(n):
            alto = i < n // 2
            v = (100 if alto else 10) + ((i * 7 + j * 3) % 5)
            celdas.append((_cuadro(i * 100, j * 100, 100), {"valor": v, "celda": f"{i}-{j}"}))
    return celdas


async def test_lisa_encuentra_el_cluster_alto_y_el_bajo(workspace_pool):
    store, ws = DatasetStore(workspace_pool), _ws()
    ref = await _cargar(store, ws, "Rejilla", _rejilla_con_cluster())
    res = await ops.autocorrelacion(store, ws, ref.id, "valor", metodo="lisa")
    h = res.hechos
    assert h["moran_i"] > 0.7 and h["moran_p"] < 0.01 and h["pesos"] == "queen"
    assert h["clusters"]["HH"] > 20 and h["clusters"]["LL"] > 20
    assert h["clusters"]["HL"] == 0 and h["clusters"]["LH"] == 0
    t = f'"{res.ref.storage.schema_name}"."{res.ref.storage.table}"'
    # una celda del corazón oeste es HH; una del corazón este, LL
    filas = {f["celda"]: f["lisa_clase"] for f in await store.filas(ws, f"SELECT celda, lisa_clase FROM {t}")}
    assert filas["2-5"] == "HH" and filas["7-5"] == "LL"
    # determinista: mismo resultado otra vez
    otra = await ops.autocorrelacion(store, ws, ref.id, "valor", metodo="lisa")
    assert otra.hechos == h


async def test_gi_estrella_marca_puntos_calientes_y_frios(workspace_pool):
    store, ws = DatasetStore(workspace_pool), _ws()
    ref = await _cargar(store, ws, "Rejilla", _rejilla_con_cluster())
    res = await ops.autocorrelacion(store, ws, ref.id, "valor", metodo="gi")
    assert res.hechos["puntos"]["caliente"] > 20 and res.hechos["puntos"]["frio"] > 20
    assert {"gi_z", "gi_p", "gi_clase"} <= {f.name for f in res.ref.fields}


async def test_autocorrelacion_rechaza_campos_constantes_y_pocos_datos(workspace_pool):
    store, ws = DatasetStore(workspace_pool), _ws()
    pocos = await _cargar(store, ws, "pocos", _rejilla_con_cluster(3))
    with pytest.raises(ops.OperacionInvalida, match="al menos 10"):
        await ops.autocorrelacion(store, ws, pocos.id, "valor")
    const = await _cargar(store, ws, "const", [
        (_cuadro(i * 100, 0, 100), {"valor": 7}) for i in range(12)
    ])
    with pytest.raises(ops.OperacionInvalida, match="constante"):
        await ops.autocorrelacion(store, ws, const.id, "valor")


# --- aislamiento ------------------------------------------------------------------


async def test_una_sesion_no_opera_sobre_los_datasets_de_otra(workspace_pool):
    store, ws_a, ws_b = DatasetStore(workspace_pool), _ws(), _ws()
    ref = await _cargar(store, ws_a, "de A", [(_cuadro(0, 0, 100), {"n": 1})])
    with pytest.raises(WorkspaceError, match="no existe en esta sesión"):
        await ops.buffer(store, ws_b, ref.id, 10)
    with pytest.raises(WorkspaceError, match="no existe en esta sesión"):
        await ops.medir(store, ws_b, ref.id)
