"""Capacidades espaciales DETERMINISTAS sobre el workspace (S2.5).

Buffer métrico, medición, overlay, unión espacial, agregación por zonas y
autocorrelación espacial (LISA, Gi*) calculadas por código propio, no por código
que escribe el LLM: el resultado es reproducible y verificable. El LLM decide
QUÉ operación y con qué parámetros; la geometría la resuelve PostGIS.

Reglas:
- Solo datasets de ESTA sesión: cada id se resuelve con `store.get(ws, id)`,
  que filtra por workspace. Un id de otra sesión no existe aquí.
- Medidas métricas con `geography` (elipsoide WGS84): metros y m² correctos en
  cualquier latitud, sin elegir zona UTM. Los datasets del workspace están en
  EPSG:4326 por contrato.
- Cada operación que produce geometría crea un dataset NUEVO (con procedencia)
  y devuelve cifras (`hechos`) para que el LLM narre con números reales.
- Identificadores: solo los que emite el store (tablas `d_<hex>`, campos ya
  saneados), siempre citados. Los valores (distancias) van como parámetros.

La reproyección no es una operación aquí: el workspace guarda todo en 4326 y
las medidas usan `geography`. Entregar en otro CRS es de la fase de exportación.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from geo_copilot.platform.contracts import LayerRef, Provenance, WorkspaceTable
from geo_copilot.platform.workspace.store import DatasetStore, WorkspaceError, _qi

Predicado = Literal["intersects", "within", "contains", "dwithin"]
Estadistico = Literal["count", "sum", "mean", "min", "max"]

#: Tope de elementos para la autocorrelación (se calcula en memoria de la app).
MAX_AUTOCORRELACION = 200_000
_PERMUTACIONES = 999
_SEMILLA = 12345  # determinista: misma entrada → mismos p-valores


@dataclass
class Resultado:
    ref: LayerRef | None
    hechos: dict[str, Any] = field(default_factory=dict)


class OperacionInvalida(WorkspaceError):
    """Parámetros que no tienen sentido para la operación (no es un fallo del sistema)."""


def _prov(capacidad: str, **args: Any) -> Provenance:
    return Provenance(
        capability=f"core.{capacidad}", produced_at=datetime.now(UTC),
        arguments={k: v for k, v in args.items() if v is not None},
    )


async def _capa(store: DatasetStore, ws: str, dataset_id: str, *, geom: bool = True) -> LayerRef:
    ref = await store.get(ws, dataset_id)
    if ref is None or not isinstance(ref.storage, WorkspaceTable):
        raise WorkspaceError(f"el dataset {dataset_id} no existe en esta sesión")
    if geom and not ref.storage.geometry_column:
        raise OperacionInvalida(f"«{ref.name}» no tiene geometría")
    return ref


def _tabla(ref: LayerRef) -> str:
    assert isinstance(ref.storage, WorkspaceTable)
    return f"{_qi(ref.storage.schema_name)}.{_qi(ref.storage.table)}"


def _props(ref: LayerRef, alias: str, prefijo: str = "") -> list[str]:
    """`alias.campo AS prefijo_campo` para cada propiedad (sin fid ni geom)."""
    return [
        f"{alias}.{_qi(f.name)} AS {_qi((prefijo + f.name)[:63])}" for f in ref.fields
    ]


def _poligonal(ref: LayerRef) -> bool:
    return bool(ref.geometry_type and "Polygon" in ref.geometry_type)


def _num(v: Any) -> float | None:
    return None if v is None else round(float(v), 2)


# ---------------------------------------------------------------------------
# Medición
# ---------------------------------------------------------------------------


async def medir(store: DatasetStore, ws: str, dataset_id: str) -> Resultado:
    """Conteo, área total (m², ha) y longitud total (m, km) de un dataset.

    El área total cuenta una sola vez lo que se solapa (unión), y además se da
    la suma simple por si el usuario pregunta por la suma de áreas individuales.
    """
    ref = await _capa(store, ws, dataset_id)
    t = _tabla(ref)
    h = await store.hechos(ws, (
        "SELECT count(*) AS n, "
        "sum(ST_Area(geom::geography)) AS area_suma_m2, "
        "ST_Area(ST_Union(geom)::geography) AS area_union_m2, "
        "sum(ST_Length(geom::geography)) AS longitud_m "
        f"FROM {t} WHERE geom IS NOT NULL"
    ))
    area = _num(h.get("area_union_m2"))
    hechos = {
        "dataset": ref.name, "elementos": int(h.get("n") or 0),
        "geometria": ref.geometry_type,
    }
    if _poligonal(ref):
        hechos |= {
            "area_total_m2": area, "area_total_ha": None if area is None else round(area / 10_000, 4),
            "suma_areas_individuales_m2": _num(h.get("area_suma_m2")),
        }
    if ref.geometry_type and "LineString" in ref.geometry_type:
        lon = _num(h.get("longitud_m"))
        hechos |= {"longitud_total_m": lon, "longitud_total_km": None if lon is None else round(lon / 1000, 4)}
    return Resultado(None, hechos)


# ---------------------------------------------------------------------------
# Buffer métrico
# ---------------------------------------------------------------------------


async def agregar_medida(store: DatasetStore, ws: str, dataset_id: str, medida: str) -> Resultado:
    """El área / longitud / perímetro de cada elemento como un campo del MISMO dataset."""
    ref = await store.agregar_medida(ws, dataset_id, medida)
    campo = store.MEDIDAS[medida][0]
    t = _tabla(ref)
    h = await store.hechos(ws, (
        f"SELECT count(*) AS n, min({_qi(campo)}) AS min, max({_qi(campo)}) AS max, "
        f"sum({_qi(campo)}) AS suma FROM {t} WHERE geom IS NOT NULL"
    ))
    return Resultado(ref, {
        "operacion": f"campo {campo} añadido a cada elemento (geodésico, exacto)",
        "dataset": ref.name, "campo": campo, "elementos": int(h.get("n") or 0),
        "min": _num(h.get("min")), "max": _num(h.get("max")), "suma": _num(h.get("suma")),
        "misma_capa": True,
    })


async def buffer(
    store: DatasetStore, ws: str, dataset_id: str, metros: float, *,
    disolver: bool = False, nombre: str | None = None,
) -> Resultado:
    if not (0 < float(metros) <= 100_000):
        raise OperacionInvalida("la distancia del buffer debe estar entre 0 y 100 km")
    ref = await _capa(store, ws, dataset_id)
    t = _tabla(ref)
    # V5 F5: con los 8 segmentos por cuadrante por defecto, el círculo de 200 m queda hasta ~1 m
    # por DENTRO (un lote a 199,5 m quedaba fuera); con 64 el error es ~1,5 cm.
    zona = "ST_Buffer(s.geom::geography, $1, 'quad_segs=64')::geometry"
    if disolver:
        sql = f"SELECT ST_Multi(ST_Union({zona})) AS geom FROM {t} s WHERE s.geom IS NOT NULL"
    else:
        cols = ", ".join([*_props(ref, "s"), f"{zona} AS geom"])
        sql = f"SELECT {cols} FROM {t} s WHERE s.geom IS NOT NULL ORDER BY s.fid"
    nuevo = await store.crear_desde_sql(
        ws, nombre or f"Buffer {metros:g} m de {ref.name}", sql, (float(metros),),
        provenance=_prov("buffer", dataset=dataset_id, metros=metros, disolver=disolver),
    )
    medida = await medir(store, ws, nuevo.id)
    return Resultado(nuevo, {"operacion": "buffer", "metros": float(metros), "disuelto": disolver,
                             "entrada": ref.name, **medida.hechos})


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------


async def overlay(
    store: DatasetStore, ws: str, a_id: str, b_id: str, *,
    modo: Literal["intersection", "difference"] = "intersection", nombre: str | None = None,
) -> Resultado:
    a, b = await _capa(store, ws, a_id), await _capa(store, ws, b_id)
    ta, tb = _tabla(a), _tabla(b)
    if modo == "intersection":
        geom = "ST_Intersection(a.geom, b.geom)"
        if _poligonal(a) and _poligonal(b):
            geom = f"ST_CollectionExtract({geom}, 3)"  # solo la parte de área
        cols = ", ".join([*_props(a, "a", "a_"), *_props(b, "b", "b_"), f"{geom} AS geom"])
        sql = (
            f"SELECT * FROM (SELECT {cols} FROM {ta} a JOIN {tb} b "
            f"ON ST_Intersects(a.geom, b.geom)) x WHERE NOT ST_IsEmpty(x.geom)"
        )
        titulo = f"{a.name} ∩ {b.name}"
    elif modo == "difference":
        cols = ", ".join([
            *_props(a, "a"),
            "coalesce(ST_Difference(a.geom, (SELECT ST_Union(b.geom) FROM "
            f"{tb} b WHERE ST_Intersects(a.geom, b.geom))), a.geom) AS geom",
        ])
        sql = f"SELECT * FROM (SELECT {cols} FROM {ta} a) x WHERE NOT ST_IsEmpty(x.geom)"
        titulo = f"{a.name} menos {b.name}"
    else:
        raise OperacionInvalida(f"modo de overlay no soportado: {modo!r}")
    nuevo = await store.crear_desde_sql(
        ws, nombre or titulo, sql,
        provenance=_prov("overlay", a=a_id, b=b_id, modo=modo),
    )
    medida = await medir(store, ws, nuevo.id)
    return Resultado(nuevo, {"operacion": f"overlay:{modo}", "a": a.name, "b": b.name, **medida.hechos})


# ---------------------------------------------------------------------------
# Unión espacial
# ---------------------------------------------------------------------------


async def union_espacial(
    store: DatasetStore, ws: str, a_id: str, b_id: str, *,
    predicado: Predicado = "intersects", metros: float | None = None, nombre: str | None = None,
) -> Resultado:
    """Cada elemento de A con los atributos de los B que cumplen el predicado.

    Una fila por pareja (A, B); los A sin pareja no aparecen (unión interna).
    `dwithin` usa metros sobre geography.
    """
    a, b = await _capa(store, ws, a_id), await _capa(store, ws, b_id)
    params: tuple[Any, ...] = ()
    if predicado == "intersects":
        cond = "ST_Intersects(a.geom, b.geom)"
    elif predicado == "within":
        cond = "ST_Within(a.geom, b.geom)"
    elif predicado == "contains":
        cond = "ST_Contains(a.geom, b.geom)"
    elif predicado == "dwithin":
        if metros is None or not (0 < float(metros) <= 100_000):
            raise OperacionInvalida("`dwithin` necesita una distancia entre 0 y 100 km")
        cond = "ST_DWithin(a.geom::geography, b.geom::geography, $1)"
        params = (float(metros),)
    else:
        raise OperacionInvalida(f"predicado no soportado: {predicado!r}")
    cols = ", ".join(["a.fid AS a_fid", *_props(a, "a", "a_"), *_props(b, "b", "b_"), "a.geom AS geom"])
    sql = f"SELECT {cols} FROM {_tabla(a)} a JOIN {_tabla(b)} b ON {cond}"
    nuevo = await store.crear_desde_sql(
        ws, nombre or f"{a.name} × {b.name} ({predicado})", sql, params,
        provenance=_prov("spatial_join", a=a_id, b=b_id, predicado=predicado, metros=metros),
    )
    distintos = await store.hechos(
        ws, f"SELECT count(DISTINCT a_fid) AS n FROM {_tabla(nuevo)}",
    )
    return Resultado(nuevo, {
        "operacion": f"union_espacial:{predicado}", "a": a.name, "b": b.name,
        "parejas": nuevo.feature_count, "elementos_de_a_con_pareja": int(distintos.get("n") or 0),
        "elementos_de_a": a.feature_count,
    })


# ---------------------------------------------------------------------------
# Agregación por zonas
# ---------------------------------------------------------------------------


async def agregar_por_zonas(
    store: DatasetStore, ws: str, zonas_id: str, datos_id: str, *,
    estadistico: Estadistico = "count", campo: str | None = None, nombre: str | None = None,
) -> Resultado:
    """Para cada zona: cuántos elementos de `datos` la intersecan, o su suma/media/… de `campo`.

    Las zonas sin datos quedan con 0 (conteo) o nulo (resto): se ven en el mapa.
    """
    z, d = await _capa(store, ws, zonas_id), await _capa(store, ws, datos_id)
    if estadistico == "count":
        agg, col = "count(d.fid)", "conteo"
    else:
        tipos = {f.name: f.type for f in d.fields}
        if not campo or campo not in tipos:
            raise OperacionInvalida(
                f"«{d.name}» no tiene el campo {campo!r}; campos: {', '.join(tipos) or 'ninguno'}"
            )
        if tipos[campo] not in ("integer", "number"):
            raise OperacionInvalida(f"el campo {campo!r} no es numérico ({tipos[campo]})")
        if estadistico not in ("sum", "mean", "min", "max"):
            raise OperacionInvalida(f"estadístico no soportado: {estadistico!r}")
        fn = {"sum": "sum", "mean": "avg", "min": "min", "max": "max"}[estadistico]
        agg, col = f"{fn}(d.{_qi(campo)})::double precision", f"{estadistico}_{campo}"[:63]
    props = _props(z, "z")
    grupo = ", ".join(["z.fid", *(f"z.{_qi(f.name)}" for f in z.fields), "z.geom"])
    cols = ", ".join([*props, f"{agg} AS {_qi(col)}", "z.geom AS geom"])
    sql = (
        f"SELECT {cols} FROM {_tabla(z)} z LEFT JOIN {_tabla(d)} d "
        f"ON ST_Intersects(z.geom, d.geom) GROUP BY {grupo} ORDER BY z.fid"
    )
    nuevo = await store.crear_desde_sql(
        ws, nombre or f"{d.name} por {z.name}", sql,
        provenance=_prov("aggregate", zonas=zonas_id, datos=datos_id, estadistico=estadistico, campo=campo),
    )
    con_datos = f"{_qi(col)} > 0" if estadistico == "count" else f"{_qi(col)} IS NOT NULL"
    h = await store.hechos(ws, (
        f"SELECT count(*) AS zonas, count(*) FILTER (WHERE {con_datos}) AS con_datos, "
        f"max({_qi(col)})::double precision AS maximo, sum({_qi(col)})::double precision AS total "
        f"FROM {_tabla(nuevo)}"
    ))
    return Resultado(nuevo, {
        "operacion": "agregacion", "zonas": z.name, "datos": d.name, "columna": col,
        "zonas_total": int(h.get("zonas") or 0), "zonas_con_datos": int(h.get("con_datos") or 0),
        "maximo": _num(h.get("maximo")), "total": _num(h.get("total")),
    })


# ---------------------------------------------------------------------------
# Autocorrelación espacial (LISA, Getis-Ord Gi*)
# ---------------------------------------------------------------------------


async def autocorrelacion(
    store: DatasetStore, ws: str, dataset_id: str, campo: str, *,
    metodo: Literal["lisa", "gi"] = "lisa", nombre: str | None = None,
) -> Resultado:
    """Moran global + LISA local, o Getis-Ord Gi*, sobre un campo numérico.

    Pesos: contigüidad Queen para polígonos (con vecinos por KNN para las islas)
    y 8 vecinos más cercanos para puntos/líneas; estandarizados por fila. p por
    999 permutaciones con semilla fija. Significancia: p < 0.05.
    """
    ref = await _capa(store, ws, dataset_id)
    tipos = {f.name: f.type for f in ref.fields}
    if campo not in tipos or tipos[campo] not in ("integer", "number"):
        raise OperacionInvalida(
            f"«{ref.name}» no tiene un campo numérico {campo!r}; campos: {', '.join(tipos)}"
        )
    if (ref.feature_count or 0) < 10:
        raise OperacionInvalida("se necesitan al menos 10 elementos para una autocorrelación")
    if (ref.feature_count or 0) > MAX_AUTOCORRELACION:
        raise OperacionInvalida(f"más de {MAX_AUTOCORRELACION} elementos: agrega por zonas primero")

    filas = await store.filas(ws, (
        f"SELECT fid, {_qi(campo)}::double precision AS v, ST_AsBinary(geom) AS g "
        f"FROM {_tabla(ref)} WHERE geom IS NOT NULL AND {_qi(campo)} IS NOT NULL ORDER BY fid"
    ))
    if len(filas) < 10:
        raise OperacionInvalida("menos de 10 elementos con valor y geometría")

    import asyncio

    calc = await asyncio.to_thread(_calcular_autocorrelacion, filas, _poligonal(ref), metodo)

    valores = ", ".join(f"({fid}, {q!r}, {p}, {s!r})" for fid, q, p, s in calc["por_fid"])
    extra = (
        "l.cuadrante AS lisa_cuadrante, l.p AS lisa_p, l.clase AS lisa_clase"
        if metodo == "lisa" else "l.cuadrante::double precision AS gi_z, l.p AS gi_p, l.clase AS gi_clase"
    )
    cols = ", ".join([*_props(ref, "t"), extra, "t.geom AS geom"])
    sql = (
        f"SELECT {cols} FROM {_tabla(ref)} t JOIN (VALUES {valores}) "
        f"AS l(fid, cuadrante, p, clase) ON l.fid = t.fid ORDER BY t.fid"
    )
    nuevo = await store.crear_desde_sql(
        ws, nombre or f"{'LISA' if metodo == 'lisa' else 'Gi*'} de {campo} en {ref.name}", sql,
        provenance=_prov("autocorrelation", dataset=dataset_id, campo=campo, metodo=metodo),
    )
    return Resultado(nuevo, {
        "operacion": f"autocorrelacion:{metodo}", "dataset": ref.name, "campo": campo,
        "elementos": len(filas), **calc["resumen"],
    })



def _calcular_autocorrelacion(filas: list[dict], poligonal: bool, metodo: str) -> dict[str, Any]:
    import geopandas as gpd
    import numpy as np
    import shapely
    from esda.getisord import G_Local
    from esda.moran import Moran, Moran_Local
    from libpysal import weights
    from libpysal.weights.util import attach_islands

    gdf = gpd.GeoDataFrame(
        {"fid": [f["fid"] for f in filas], "v": [f["v"] for f in filas]},
        geometry=shapely.from_wkb([bytes(f["g"]) for f in filas]), crs="EPSG:4326",
    )
    y = gdf["v"].to_numpy(dtype=float)
    if np.nanstd(y) == 0:
        raise OperacionInvalida("el campo es constante: no hay variación que analizar")

    if poligonal:
        w = weights.Queen.from_dataframe(gdf, use_index=False, silence_warnings=True)
        if w.islands:
            # Islas (sin vecino que comparta borde): se les da su vecino más
            # cercano para que no queden fuera del análisis.
            knn = weights.KNN.from_dataframe(gdf.set_geometry(gdf.representative_point()), k=1)
            w = attach_islands(w, knn)
    else:
        k = min(8, len(gdf) - 1)
        w = weights.KNN.from_dataframe(gdf.set_geometry(gdf.geometry.representative_point()), k=k)
    w.transform = "r"

    np.random.seed(_SEMILLA)  # Moran global no acepta semilla: p_sim reproducible
    glob = Moran(y, w, permutations=_PERMUTACIONES)
    resumen: dict[str, Any] = {
        "moran_i": round(float(glob.I), 4), "moran_p": round(float(glob.p_sim), 4),
        "pesos": "queen" if poligonal else f"knn{w.max_neighbors}",
    }
    por_fid: list[tuple[int, Any, float, str]] = []
    if metodo == "lisa":
        loc = Moran_Local(y, w, permutations=_PERMUTACIONES, seed=_SEMILLA)
        nombres = {1: "HH", 2: "LH", 3: "LL", 4: "HL"}
        for fid, q, p in zip(gdf["fid"], loc.q, loc.p_sim, strict=True):
            clase = nombres[int(q)] if p < 0.05 else "ns"
            por_fid.append((int(fid), nombres[int(q)], round(float(p), 4), clase))
        conteo = {c: sum(1 for *_, x in por_fid if x == c) for c in ("HH", "LL", "HL", "LH", "ns")}
        resumen |= {"clusters": conteo}
    else:
        gi = G_Local(y, w, star=True, permutations=_PERMUTACIONES, seed=_SEMILLA)
        for fid, z, p in zip(gdf["fid"], gi.Zs, gi.p_sim, strict=True):
            clase = ("caliente" if z > 0 else "frio") if p < 0.05 else "ns"
            por_fid.append((int(fid), round(float(z), 4), round(float(p), 4), clase))
        conteo = {c: sum(1 for *_, x in por_fid if x == c) for c in ("caliente", "frio", "ns")}
        resumen |= {"puntos": conteo}
    return {"por_fid": por_fid, "resumen": resumen}
