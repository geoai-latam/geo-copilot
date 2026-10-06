"""Selección de herramientas a escala (S3.7 / T3.9).

Con muchos servidores enchufados, listar todas las tools en cada turno degrada
al LLM (más tokens, más confusión entre tools parecidas). Por encima de un
umbral, el LLM ve las capacidades del núcleo + el resumen por servidor + la
herramienta `find_tools`, y ÉL decide qué buscar; esta búsqueda solo ORDENA
candidatas por su descripción. Las encontradas quedan activas el resto del turno.

v1 léxica (BM25 sobre nombre + descripción, con prefijos para la morfología
del español); la interfaz `buscar()` permite cambiarla por embeddings sin tocar
el bucle.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from typing import Any

from geo_copilot.platform.capabilities import Capability, ToolOutcome, registry

#: Clave del estado de trabajo con las tools MCP activadas en este turno.
ACTIVADAS = "herramientas_activadas"

_VACIAS = frozenset(
    "de la el los las un una unos unas y o en con por para del al que se su sus es son "
    "the of and or to in on for with a an is are by from as this that".split()
)


def _tokens(texto: str) -> list[str]:
    sin_tildes = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    crudos = re.split(r"[^a-z0-9]+", sin_tildes.replace("_", " "))
    # prefijo de 5: «vegetación»/«vegetal», «imagen»/«imágenes» caen juntas
    return [t[:5] for t in crudos if len(t) >= 3 and t not in _VACIAS]


def es_mcp(cap: Capability) -> bool:
    return cap.provider.startswith("mcp:")


def buscar(consulta: str, candidatas: Iterable[Capability], *, limite: int = 6) -> list[Capability]:
    """Las `limite` tools cuyo nombre + descripción mejor casan con la consulta (BM25)."""
    docs = [(c, _tokens(f"{c.tool_name} {c.description}")) for c in candidatas]
    q = _tokens(consulta)
    if not docs or not q:
        return []
    n = len(docs)
    media = sum(len(t) for _, t in docs) / n
    df: Counter[str] = Counter()
    for _, toks in docs:
        df.update(set(toks))
    puntuadas = []
    for cap, toks in docs:
        tf = Counter(toks)
        s = 0.0
        for t in q:
            if not tf[t]:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(toks) / media))
        if s > 0:
            puntuadas.append((s, cap.tool_name, cap))
    puntuadas.sort(key=lambda x: (-x[0], x[1]))
    return [c for _, _, c in puntuadas[:limite]]


def hay_demasiadas(graph: Any, umbral: int) -> bool:
    # solo se evalúan las MCP: registry().available() pediría también la
    # disponibilidad de find_tools, que es esta misma función (recursión)
    return sum(1 for c in registry().all() if es_mcp(c) and c.available(graph)) > umbral


def seleccion(graph: Any, activadas: Iterable[str], umbral: int) -> list[Capability]:
    """Las capacidades que el LLM ve en este paso del bucle.

    Hasta `umbral` tools MCP: todas (como siempre). Por encima: núcleo +
    `find_tools` + las que el LLM ya activó en este turno.
    """
    caps = registry().available(graph)
    if sum(1 for c in caps if es_mcp(c)) <= umbral:
        return [c for c in caps if c.tool_name != "find_tools"]
    activas = set(activadas)
    return [c for c in caps if not es_mcp(c) or c.tool_name in activas]


#: Tope (caracteres) del catálogo de nombres en el prompt. Nombres, no esquemas: 60 tools ≈ 900
#: caracteres; por encima del tope se dice cuántas faltan y se buscan con `query`.
CATALOGO_MAX = 6000


def catalogo(graph: Any, activadas: Iterable[str], umbral: int) -> str:
    """Los NOMBRES de las tools MCP que no se ven en este paso, por servidor (hecho: qué existe).

    Sin esto, por encima del umbral el LLM no sabía qué había («rutas, clima…» genérico por
    servidor) y, con el lugar nombrado, pedía al usuario que lo marcara en el mapa en vez de buscar
    (test_llm_mcp_escala: 6/15)."""
    caps = registry().available(graph)
    if sum(1 for c in caps if es_mcp(c)) <= umbral:
        return ""
    activas = set(activadas)
    por_servidor: dict[str, list[str]] = {}
    for c in caps:
        if es_mcp(c) and c.tool_name not in activas:
            por_servidor.setdefault(c.provider.removeprefix("mcp:"), []).append(c.tool_name)
    lineas: list[str] = []
    usado = faltan = 0
    for srv, nombres in sorted(por_servidor.items()):
        linea = f"  - {srv}: {', '.join(sorted(nombres))}"
        if usado + len(linea) > CATALOGO_MAX:
            faltan += len(nombres)
            continue
        lineas.append(linea)
        usado += len(linea)
    extra = f"\n  (+{faltan} más: búscalas con find_tools `query`)" if faltan else ""
    return ("CATÁLOGO DE LOS SERVICIOS CONECTADOS (aún no activas: actívalas con find_tools `names` o "
            "búscalas con `query`):\n" + "\n".join(lineas) + extra)


async def ejecutar_find_tools(graph: Any, working: dict, args: dict) -> ToolOutcome:
    consulta = str(args.get("query") or "").strip()
    nombres = [str(n).strip() for n in (args.get("names") or []) if str(n).strip()]
    if not consulta and not nombres:
        return ToolOutcome("find_tools necesita `names` (del catálogo) o `query` (qué quieres hacer).",
                           success=False)
    limite = max(1, min(int(args.get("limit") or 6), 10))
    mcp = [c for c in registry().available(graph) if es_mcp(c)]
    por_nombre = {c.tool_name: c for c in mcp}
    halladas = [por_nombre[n] for n in nombres if n in por_nombre]
    desconocidas = [n for n in nombres if n not in por_nombre]
    if consulta:
        halladas += [c for c in buscar(consulta, mcp, limite=limite) if c not in halladas]
    if not halladas:
        return ToolOutcome(f"Ninguna herramienta de los servicios conectados casa con «{consulta or nombres}». "
                           "Prueba con otras palabras o responde con lo que tienes.", success=True)
    ya = list(working.get(ACTIVADAS) or [])
    nuevas = ya + [c.tool_name for c in halladas if c.tool_name not in ya]
    lineas = "\n".join(f"- {c.tool_name}: {c.blurb}" for c in halladas)
    if desconocidas:
        lineas += f"\n(no existen: {', '.join(desconocidas)})"
    # Hecho: servidores que se solapan. Con 6 servidores con la misma «clima», el LLM llamaba a las
    # 6 con la misma pregunta (test_llm_mcp_escala) y agotaba el presupuesto del turno.
    iguales: dict[str, list[str]] = {}
    for c in halladas:
        iguales.setdefault(" ".join(c.blurb.split()).lower(), []).append(c.tool_name)
    for grupo in (g for g in iguales.values() if len(g) > 1):
        lineas += (f"\n(equivalentes, misma descripción en servidores distintos: {', '.join(grupo)} — con "
                   "una basta; otra solo si la primera falla)")
    # gpt-5.4 (2026-10-05): tras activar «elevación» respondía la altura de Monserrate de memoria («suele
    # citarse 3.150 m»). Que existan es el hecho; lo que miden ya no se responde sin llamarlas.
    return ToolOutcome(f"Herramientas activadas para este turno (ya puedes llamarlas):\n{lineas}\n"
                       "Lo que una de ellas mide o consulta se responde LLAMÁNDOLA, no de memoria. Un lugar "
                       "NOMBRADO («el centro de Bogotá», «el cerro de Monserrate») ya es el dónde: si la "
                       "herramienta pide coordenadas, geocodifícalo (activa el geocodificador con find_tools si "
                       "no está) en vez de pedirle al usuario un punto exacto.",
                       success=True, delta={ACTIVADAS: nuevas})
