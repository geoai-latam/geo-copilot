"""Tipos base de los contratos: versión, CRS y extensión.

Regla dura del plan (§3.4): **el CRS se declara, nunca se supone**. Por eso `Crs`
no tiene default en ningún modelo que lo use — igual que el shapefile sin `.prj`
que F0 dejó de etiquetar 4326 a ciegas.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

#: Versión del contrato (semver). Sube el MAJOR cuando un consumidor que sólo
#: conoce la versión anterior pueda interpretar mal un documento nuevo.
CONTRACT_VERSION = "1.8.0"  # FH.10: vistas, comparar (cortina), tiempo; fecha de los rasters

#: Tope de features que pueden viajar INLINE (GeoJSON en el cuerpo). Por encima,
#: el productor devuelve una referencia (`feature_ref`) y el núcleo la ingiere.
MAX_INLINE_FEATURES = 10_000

#: `AUTORIDAD:CÓDIGO` — EPSG:4326, EPSG:9377, OGC:CRS84, ESRI:102100…
Crs = Annotated[
    str,
    StringConstraints(pattern=r"^(EPSG|OGC|ESRI|IAU_2015):[A-Za-z0-9_.]+$"),
]


def _bbox_valida(b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = b
    if minx > maxx or miny > maxy:
        raise ValueError(f"bbox invertida: {b} (se espera minx, miny, maxx, maxy)")
    return b


#: (minx, miny, maxx, maxy) en el CRS que declare el modelo que la contiene.
BBox = Annotated[tuple[float, float, float, float], AfterValidator(_bbox_valida)]


class Strict(BaseModel):
    """Base de todos los contratos: campos desconocidos son un error.

    Un contrato que acepta campos de más acaba siendo un `dict[str, Any]` con
    otro nombre — que es justo lo que F1 viene a quitar.
    """

    # json_schema_serialization_defaults_required: en el schema de salida, un campo
    # con default que SIEMPRE viaja (p. ej. el discriminador `kind`) es obligatorio.
    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_serialization_defaults_required=True)
