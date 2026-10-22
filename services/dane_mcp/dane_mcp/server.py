"""dane-mcp — Censo Nacional de Población y Vivienda 2018 (CNPV) integrado al Marco Geoestadístico (MGN).

«¿Cuánta gente vive en Chía?», «¿cuántas personas mayores de 60 hay en estas manzanas?», «¿qué manzanas
no tienen alcantarillado?». Tres herramientas:

- `dane_variables`: las variables censales de un nivel (manzana o municipio) con la descripción del DANE.
- `dane_municipios`: municipios por nombre o código DIVIPOLA con sus totales OFICIALES del censo y, si se
  pide, su límite (MGN 2025).
- `dane_censo_manzanas`: las manzanas de un área con las variables elegidas (censo 2018) y lo que el MGN
  2025 añade (recuento de viviendas, unidades económicas), con las sumas del área.

Las cifras son del censo de 2018 (población CENSADA), no proyecciones; lo del MGN 2025 se dice aparte.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

from geo_mcp_kit import (
    GeoMcpAuth,
    KeyRing,
    RateLimiter,
    ToolRunner,
    compact_result,
    completitud,
    feature_collection,
    geo_meta,
    geo_result,
    herramientas_en_hilo,
)
from geo_mcp_kit import area as areas
from geo_mcp_kit.red import ErrorRed, UrlNoPermitida
from geo_mcp_kit.resultados import Resultados
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ToolAnnotations

from dane_mcp import censo, fuentes

logger = logging.getLogger("dane_mcp")
SCOPES = {"dane_variables": "dane:read", "dane_municipios": "dane:read", "dane_censo_manzanas": "dane:read"}
MAX_MANZANAS = 20_000
NOTA_CENSO = ("cifras del Censo Nacional de Población y Vivienda 2018 (población CENSADA), no proyecciones "
              "a hoy")
NOTA_MANZANAS = ("la población rural DISPERSA no vive en manzanas: la suma de las manzanas de un municipio es "
                 "menor que su total (Bogotá: 7 136 912 en manzanas, 7 181 469 en el municipio). Para el total "
                 "de un municipio usa dane_municipios")
resultados = Resultados(os.environ.get("DANE_RESULTADOS_DIR") or os.path.join(tempfile.gettempdir(), "dane-resultados"))
runner = ToolRunner(timeout_s=170, service="dane-mcp",
                    expected_errors=(ErrorRed, UrlNoPermitida, censo.ErrorCenso, areas.AreaInvalida))
mcp = FastMCP(
    "dane-mcp",
    instructions=("Censo 2018 (DANE) integrado al Marco Geoestadístico: `dane_variables` para ver qué hay, "
                  "`dane_municipios` para totales oficiales de un municipio y `dane_censo_manzanas` para las "
                  "manzanas de un área con sus variables."),
    stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@mcp.tool(
    description=("Las variables del censo 2018 de un `nivel` (manzana o municipio) con la descripción del DANE: "
                 "personas, sexo, grupos de edad, nivel educativo, viviendas por tipo y ocupación, hogares, "
                 "servicios (acueducto, alcantarillado, gas, internet, energía)… Los NOMBRES cambian por nivel "
                 "(manzana TP27_PERSO, municipio STP27_PERS): usa los de este listado. `texto` filtra por "
                 "palabras de la descripción."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={}, outputs=[], cost="low"),
)
def dane_variables(nivel: str = "manzana", texto: str | None = None) -> CallToolResult:
    def listar() -> dict:
        if nivel not in ("manzana", "municipio"):
            raise censo.ErrorCenso("nivel: manzana o municipio")
        todas = fuentes.campos(nivel)
        palabras = [p for p in censo.plano(texto or "").split() if len(p) > 2]
        elegidas = [c for c in todas if not palabras
                    or any(p in censo.plano(c["descripcion"] + " " + c["campo"]) for p in palabras)]
        return geo_result([], facts={"nivel": nivel, "variables": elegidas[:120], "total": len(todas),
                                     "coinciden": len(elegidas)})

    return compact_result(runner.run(listar))


@mcp.tool(
    description=("Municipios de Colombia por `nombre` (sin tildes; varios pueden coincidir: «Chía», «Nunchía»…) "
                 "o `codigo` DIVIPOLA de 5 dígitos, con sus totales OFICIALES del censo 2018 (personas, "
                 "viviendas, hogares y las `variables` de nivel municipio que pidas). Es lo que responde «¿cuánta "
                 "gente vive en X?». `con_limite`: además su límite (MGN 2025) como capa, si hay uno solo."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={}, outputs=["feature_collection"], cost="low"),
)
def dane_municipios(nombre: str | None = None, codigo: str | None = None, variables: list[str] | None = None,
                    con_limite: bool = False) -> CallToolResult:
    def buscar() -> dict:
        hallados = censo.municipios(nombre, codigo, variables or [])
        hechos: dict[str, Any] = {"municipios": hallados[:25], "encontrados": len(hallados), "nota": NOTA_CENSO,
                                  "consulta": {"nombre": nombre, "codigo": codigo, "variables": variables or []},
                                  "completitud": completitud(min(len(hallados), 25), len(hallados),
                                                             motivo="se muestran 25" if len(hallados) > 25 else None)}
        if not con_limite or len(hallados) != 1:
            if con_limite and len(hallados) > 1:
                hechos["aviso"] = "hay varios: elige uno por su código para traer su límite"
            return geo_result([], facts=hechos)
        m = hallados[0]
        limite = fuentes.municipio_mgn(m["codigo"], *m["centroide"])
        if limite is None:
            hechos["aviso"] = "no se encontró su límite en el MGN 2025"
            return geo_result([], facts=hechos)
        limite["properties"] = {k: v for k, v in m.items() if k != "centroide"}
        fc = {"type": "FeatureCollection", "features": [limite]}
        return geo_result([feature_collection(str(m["municipio"]), fc, crs="EPSG:4326")], facts=hechos,
                          style_hint={"solo_contorno": True})

    return compact_result(runner.run(buscar))


@mcp.tool(
    description=("Las manzanas de un área con variables del censo 2018 (nombres de nivel manzana, de "
                 "dane_variables; p. ej. TP27_PERSO personas, TP34_7_EDA 60–69 años, TP19_ALC_2 sin "
                 "alcantarillado) y lo que añade el MGN 2025 (recuento de viviendas, unidades económicas), como "
                 "capa, con las SUMAS del área. `area` = una capa de la sesión (solo lo que la interseca) o "
                 f"`bbox`. Hasta {MAX_MANZANAS} manzanas. La población rural dispersa no está en manzanas: para "
                 "el total de un municipio usa dane_municipios."),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    meta=geo_meta(inputs={"area": ["geometry", "layer_ref"], "bbox": ["bbox", "layer_ref"]},
                  outputs=["feature_collection", "feature_ref"], cost="medium"),
)
def dane_censo_manzanas(variables: list[str], area: dict | None = None, bbox: list[float] | None = None,
                        max_manzanas: int = 5000) -> CallToolResult:
    def consultar() -> dict:
        geom = areas.geometria(area) if area else None
        zona = list(geom.bounds) if geom is not None else bbox
        if not zona or len(zona) != 4:
            raise censo.ErrorCenso("falta la zona: `area` (una capa de la sesión) o `bbox`")
        maximo = max(1, min(int(max_manzanas), MAX_MANZANAS))
        fc, meta = censo.manzanas(zona, variables or ["TP27_PERSO"], maximo)
        hechos: dict[str, Any] = {}
        if geom is not None:
            hechos["filtro_area"] = areas.recortar(fc, geom)
        hechos.update(censo.sumas(fc, censo.validar_variables("manzana", variables or ["TP27_PERSO"])))
        if meta["vinieron"] >= maximo:
            hechos["aviso"] = (f"se llegó al tope de {maximo} manzanas: las sumas NO son de toda el área; "
                               "acota el área o sube `max_manzanas`")
        hechos.update({"fuente": NOTA_CENSO + "; geometría y recuento de viviendas: MGN 2025", "nota": NOTA_MANZANAS})
        return resultados.capa("Manzanas censo 2018", fc, hechos)

    return compact_result(runner.run(consultar))


def build_app():
    keys = KeyRing.from_json(os.environ.get("DANE_MCP_KEYS", "[]"), known_scopes={"dane:read"}, tool_scopes=SCOPES)
    if not len(keys):
        raise RuntimeError("dane-mcp no arranca sin claves (DANE_MCP_KEYS)")
    return GeoMcpAuth(herramientas_en_hilo(mcp).streamable_http_app(), keys, RateLimiter(), service="dane-mcp",
                      routes=(resultados.ruta("dane_censo_manzanas"),))


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("DANE_MCP_PORT", "9920")))
