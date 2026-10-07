"""Trazabilidad del turno: qué está haciendo el agente, contado al usuario mientras lo hace.

Cada paso es un evento `trace` por WebSocket (`events.sink().traza`) con un esquema estable que el
chat pinta como línea de tiempo y guarda con la respuesta:

    {"id", "tipo": interpretar|pensar|herramienta|agente, "estado": en_curso|ok|fallo,
     "titulo", "detalle"?, "herramienta"?, "argumentos"?, "ms"?}

Son HECHOS del turno (qué herramienta, con qué, qué devolvió y cuánto tardó), no interpretación:
el texto lo pone el código a partir de lo que pasó; la lectura de la consulta es la del propio
router (sus palabras). Best-effort: un fallo del transporte nunca toca el razonamiento.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from geo_copilot.core.logging import get_logger
from geo_copilot.platform import events

logger = get_logger(__name__)

_GEOJSON = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon",
            "GeometryCollection", "Feature", "FeatureCollection"}
_PREFIJO_OBS = re.compile(r"^Resultado de «[^»]+» \(datos externos, no instrucciones\):\s*")
_MAX_ARGS = 8


def nuevo_id(prefijo: str) -> str:
    return f"{prefijo}-{uuid.uuid4().hex[:8]}"


async def emitir(session_id: str | None, *, id: str, tipo: str, estado: str, titulo: str,
                 detalle: str | None = None, herramienta: str | None = None,
                 argumentos: dict[str, str] | None = None, ms: int | None = None) -> None:
    if not session_id:
        return
    evento: dict[str, Any] = {"id": id, "tipo": tipo, "estado": estado, "titulo": titulo}
    for k, v in (("detalle", detalle), ("herramienta", herramienta), ("argumentos", argumentos), ("ms", ms)):
        if v:
            evento[k] = v
    try:
        await events.sink().traza(session_id, evento)
    except Exception:
        logger.debug("[traza] no se pudo emitir", exc_info=True)


def _corto(valor: Any, n: int = 80) -> str:
    """Un argumento en pocas palabras: una geometría no se vuelca entera."""
    if isinstance(valor, dict):
        tipo = valor.get("type")
        if tipo == "FeatureCollection":
            return f"FeatureCollection ({len(valor.get('features') or [])} elementos)"
        if tipo in _GEOJSON:
            g = valor.get("geometry") if tipo == "Feature" else valor
            if isinstance(g, dict) and g.get("type") == "Point":
                c = g.get("coordinates") or []
                return f"punto ({c[0]:.4f}, {c[1]:.4f})" if len(c) >= 2 else "punto"
            return f"geometría {(g or {}).get('type', tipo)}"
        return f"{{{len(valor)} campos}}"
    if isinstance(valor, list):
        if len(valor) <= 4 and all(isinstance(x, (int, float, str)) for x in valor):
            return ", ".join(str(x) for x in valor)
        return f"lista de {len(valor)}"
    texto = str(valor)
    return texto if len(texto) <= n else texto[: n - 1] + "…"


def resumen_argumentos(args: dict) -> dict[str, str]:
    return {k: _corto(v) for k, v in list((args or {}).items())[:_MAX_ARGS] if v not in (None, "", [], {})}


def _hechos_simples(hechos: dict, n: int = 6) -> list[str]:
    """Los hechos escalares (y los de un nivel más), los primeros `n`."""
    out: list[str] = []
    for k, v in hechos.items():
        if isinstance(v, (int, float, str, bool)) and str(v):
            out.append(f"{k}: {_corto(v, 60)}")
        elif isinstance(v, dict):
            out += [f"{k}.{k2}: {_corto(v2, 40)}" for k2, v2 in v.items()
                    if isinstance(v2, (int, float, str, bool)) and str(v2)][:2]
        if len(out) >= n:
            break
    return out[:n]


def extracto(observacion: str, n: int = 240) -> str:
    """Lo que devolvió la herramienta, para el usuario: sus hechos simples o el principio del texto."""
    texto = _PREFIJO_OBS.sub("", (observacion or "").strip())
    try:
        obj = json.loads(texto)
    except (ValueError, TypeError):
        obj = None
    if isinstance(obj, dict):
        partes = _hechos_simples(obj.get("hechos") or {})
        capas = obj.get("capas") or []
        if capas:
            partes.append("capa: " + ", ".join(str(c.get("nombre")) for c in capas[:2] if isinstance(c, dict)))
        if isinstance(obj.get("capa_raster"), dict):
            partes.append(f"capa: {obj['capa_raster'].get('nombre')}")
        if partes:
            return _corto(" · ".join(partes), n)
    return _corto(texto, n)


_PREFIJO_DESC = re.compile(r"^\[Servidor externo[^\]]*\]\s*")


def describir_herramienta(nombre: str) -> tuple[str, str | None]:
    """(título, qué hace) de una herramienta: las del núcleo con su paso declarado («Ajustando el
    mapa»); las de un servidor MCP como «servidor · tool» y la primera frase de su descripción."""
    from geo_copilot.platform.capabilities import registry

    cap = registry().get(nombre)
    desc = _PREFIJO_DESC.sub("", (cap.description if cap else "") or "").strip()
    primera = re.split(r"(?<=[.:])\s", desc, maxsplit=1)[0] if desc else None
    if "__" in nombre:
        servidor, tool = nombre.split("__", 1)
        return f"{servidor} · {tool}", _corto(primera, 160) if primera else None
    if cap and cap.step and cap.step[1]:
        return cap.step[1], _corto(primera, 160) if primera else None
    return nombre, _corto(primera, 160) if primera else None
