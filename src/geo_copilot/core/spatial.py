"""SpatialReasoner (Fase 2 / F2.1) — razonamiento de CRS/unidades.

El gap que cierra: los prompts de los agentes fijaban EPSG:32618 (UTM 18N,
Colombia) para TODA operación métrica. Áreas/distancias fuera de la zona 18
(Perú, México, …) salían mal en silencio. Esto deriva la zona UTM correcta
del bbox real, decide si reproyectar según la operación, y declara
honestamente los casos no soportados (multi-zona, raster, 3D) en vez de
producir resultados mudos y erróneos.

Funciona en cualquier país: la fórmula UTM es global.
"""

from __future__ import annotations

from geo_copilot.core.formatters import crs_units_note

# Operaciones que requieren un CRS PROYECTADO (métrico) para tener sentido.
METRIC_OPERATIONS = frozenset(
    {"area", "distance", "length", "buffer", "perimeter", "centroid_metric", "nearest"}
)
# Operaciones topológicas: 4326 (grados) sirve, no necesitan reproyección.
TOPOLOGICAL_OPERATIONS = frozenset(
    {"intersects", "contains", "within", "touches", "overlaps", "topology", "count"}
)


def utm_zone(lon: float) -> int:
    """Zona UTM (1..60) para una longitud. Fórmula global."""
    z = int((lon + 180.0) // 6.0) + 1
    return max(1, min(60, z))


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG UTM para un punto (32600+zona Norte / 32700+zona Sur)."""
    zone = utm_zone(lon)
    return (32600 if lat >= 0 else 32700) + zone


def utm_epsg_for_bbox(bbox: list[float] | tuple[float, ...] | None) -> tuple[int | None, str]:
    """EPSG UTM métrico para un bbox [minx, miny, maxx, maxy] en grados.

    Devuelve ``(epsg, nota)``. ``epsg`` es ``None`` cuando el bbox CRUZA
    varias zonas UTM (o es inválido): un único CRS métrico no es unívoco →
    se declara no soportado en v1 (honestidad antes que resultado erróneo).
    """
    if not bbox or len(bbox) != 4:
        return None, "bbox ausente o inválido; no se puede derivar CRS métrico"
    minx, miny, maxx, maxy = bbox
    if not all(isinstance(v, (int, float)) for v in bbox):
        return None, "bbox con valores no numéricos"
    if abs(minx) > 180 or abs(maxx) > 180 or abs(miny) > 90 or abs(maxy) > 90:
        return None, "bbox fuera de rango lon/lat (¿no está en EPSG:4326?)"

    z_min, z_max = utm_zone(minx), utm_zone(maxx)
    if z_min != z_max:
        return None, (
            f"el bbox cruza zonas UTM {z_min}-{z_max}; una reproyección "
            f"métrica unívoca no está soportada en v1 (declarado honestamente)"
        )
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    epsg = utm_epsg(cx, cy)
    return epsg, crs_units_note(epsg)


def point_extent_km2(features: list | None) -> float | None:
    """Área aproximada (km²) del bbox de features de PUNTOS. ``None`` si no
    aplica (menos de 2 puntos, coords inválidas, área degenerada).

    Determinista (geo math) a propósito: el CÓDIGO calcula la densidad; el LLM
    la JUZGA (insights.evaluate_visualization_fit). Aprox: 1° lat ≈ 111.32 km,
    1° lon ≈ 111.32·cos(lat). Suficiente para una señal de densidad relativa.
    """
    import math

    lons: list[float] = []
    lats: list[float] = []

    def _add(c: object) -> None:
        if (isinstance(c, (list, tuple)) and len(c) >= 2
                and isinstance(c[0], (int, float)) and isinstance(c[1], (int, float))):
            lons.append(float(c[0]))
            lats.append(float(c[1]))

    for f in features or []:
        geom = (f or {}).get("geometry") or {} if isinstance(f, dict) else {}
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        if gtype == "Point":
            _add(coords)
        elif gtype == "MultiPoint" and isinstance(coords, (list, tuple)):
            for c in coords:
                _add(c)

    if len(lons) < 2:
        return None
    dlon = max(lons) - min(lons)
    dlat = max(lats) - min(lats)
    midlat = (max(lats) + min(lats)) / 2.0
    km_lat = 111.32
    km_lon = 111.32 * math.cos(math.radians(midlat))
    area = abs(dlon * km_lon) * abs(dlat * km_lat)
    return round(area, 4) if area > 0 else None


class SpatialReasoner:
    """Decide el CRS adecuado para una operación geoespacial."""

    @staticmethod
    def recommend(bbox: list[float] | None, operation: str) -> dict:
        """Recomienda el CRS objetivo para ``operation`` sobre ``bbox``.

        Returns dict:
            target_srid: int | None  — CRS a usar (None si no soportado)
            metric: bool             — si el target es métrico
            supported: bool          — False = declarar honestamente no soportado
            reason: str              — explicación para el usuario/LLM
            units_note: str          — nota de unidades del target
        """
        op = (operation or "").strip().lower()
        if op in METRIC_OPERATIONS:
            epsg, note = utm_epsg_for_bbox(bbox)
            if epsg is None:
                return {
                    "target_srid": None,
                    "metric": False,
                    "supported": False,
                    "reason": (
                        f"La operación '{op}' es métrica y requiere un CRS proyectado, "
                        f"pero {note}."
                    ),
                    "units_note": "indeterminado",
                }
            return {
                "target_srid": epsg,
                "metric": True,
                "supported": True,
                "reason": (
                    f"'{op}' es métrica → reproyectar a EPSG:{epsg} para resultados en metros."
                ),
                "units_note": note,
            }
        # Topológica (o desconocida tratada como topológica): 4326 sirve.
        return {
            "target_srid": 4326,
            "metric": False,
            "supported": True,
            "reason": (
                f"'{op or 'operación'}' es topológica/no-métrica → EPSG:4326 (grados) es suficiente."
            ),
            "units_note": crs_units_note(4326),
        }

    @staticmethod
    def format_for_prompt(bbox: list[float] | None) -> str:
        """Bloque para el prompt: el CRS métrico recomendado para ESTE bbox.

        Reemplaza el EPSG:32618 hardcodeado: el LLM ve la zona correcta para
        los datos que el usuario realmente tiene.
        """
        epsg, note = utm_epsg_for_bbox(bbox)
        if epsg is None:
            return (
                "CRS MÉTRICO: no derivable automáticamente para esta zona "
                f"({note}). Para cálculos de área/distancia, pide al usuario "
                "el CRS proyectado adecuado o declara la limitación."
            )
        return (
            f"CRS MÉTRICO RECOMENDADO para esta zona: EPSG:{epsg} ({note}). "
            f"Usa ST_Transform(geom, {epsg}) para área/distancia/buffer en metros; "
            f"NO asumas una zona UTM fija."
        )
