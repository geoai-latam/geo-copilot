"""Registro de capacidades (S1.3 del plan de plataforma).

Una CAPACIDAD es algo que el agente puede hacer: consultar la BD, aplicar
simbología, calcular un NDVI… Da igual si la implementa código propio o un
servidor MCP remoto: se registra igual, y de este registro salen —en vez de
listas a mano repartidas por el repo— los schemas de herramientas que ve el LLM,
la lista de herramientas del prompt ReAct, el filtro por disponibilidad, el paso
que se anuncia al chip de agentes y el despacho.

Antes, añadir una herramienta tocaba ~13 sitios en ~10 archivos (plan §1.3, B1).
Con el registro, una capacidad es una entrada.

Este módulo es genérico: no importa el orquestador. Las capacidades `core.*` se
registran desde `geo_copilot.orchestrator.capabilities_core`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

#: Herramienta terminal: cierra el bucle ReAct con la respuesta al usuario. No
#: es una capacidad (no hace nada): es la forma de terminar.
ANSWER_TOOL = "answer"

_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": ANSWER_TOOL,
        "description": (
            "Termina el turno respondiendo al usuario en lenguaje natural. Úsala cuando "
            "ya tienes lo necesario, para una pregunta general/saludo, o para reportar "
            "honestamente que algo no se pudo hacer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": (
                    "Respuesta final para el usuario, con los ENLACES AL MAPA de lo que nombres: "
                    "una capa → [[layer:<capa>|texto]]; un elemento concreto (un lote por su "
                    "código…) → [[layer:<capa>?<campo>=<valor>|texto]] o [[layer:<capa>#<id>|texto]]."
                )},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
}
_ANSWER_BLURB = "TERMINA respondiendo al usuario en lenguaje natural."


@dataclass
class ToolOutcome:
    """Resultado de ejecutar una capacidad dentro del bucle."""

    observation: str
    success: bool
    delta: dict = field(default_factory=dict)
    is_final: bool = False          # True solo para la herramienta ``answer``
    final_text: str | None = None   # respuesta al usuario (si is_final)
    facts: dict | None = None       # hechos estructurados (para una UI que no pasa por el LLM)


#: `executor(graph, estado_de_trabajo, args) -> ToolOutcome`
Executor = Callable[[Any, dict, dict[str, Any]], Awaitable[ToolOutcome]]


def _siempre(_graph: Any) -> bool:
    return True


@dataclass(frozen=True)
class Capability:
    #: Identificador global: "core.query_database", "mcp.imagery.imagery_ndvi"…
    id: str
    #: Nombre con el que el LLM la invoca (único en el registro).
    tool_name: str
    #: Descripción formal (va en el schema de la herramienta).
    description: str
    #: JSON Schema de los argumentos (`{"type": "object", ...}`).
    parameters: dict[str, Any]
    executor: Executor
    #: Línea corta para la lista de herramientas del prompt ReAct.
    blurb: str
    #: ¿Está disponible ahora? (p. ej. imagery solo con servicio conectado).
    available: Callable[[Any], bool] = _siempre
    provider: str = "core"
    risk: Literal["read", "compute", "write", "external_egress"] = "read"
    cost: Literal["low", "medium", "high"] = "low"
    #: Paso que se anuncia al chip de agentes (T1.4): (agente, descripción).
    step: tuple[str, str] = ("agent_loop", "")
    #: FH.8: argumentos que reciben algo del mapa, con lo que aceptan y (opcional) los tipos de
    #: geometría que tienen sentido: {"aoi": {"accepts": ["geometry"], "geometry_types": [...]}}.
    #: `dataset` = una capa del workspace (o `seleccion` / `activa`). El menú contextual sale de aquí.
    geo_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: F6 (S6.2): de qué organización es (una conexión dada de alta por ella). None = de la
    #: plataforma, para todas. Una capacidad de la organización A no existe para B.
    org_id: str | None = None

    def tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _org_de_la_peticion() -> str | None:
    from geo_copilot.platform.identidad.principal import principal_actual

    p = principal_actual()
    return p.org_id if p is not None else None


class CapabilityRegistry:
    """Las capacidades, por ámbito: las de la plataforma (org None) y las de cada organización.

    Lo que se lee (`get`, `all`, `names`, `available`, esquemas y prompt) es lo VISIBLE para la
    organización de la petición en curso: las de la plataforma más las suyas. Sin petición
    (procesos del sistema), solo las de la plataforma.
    """

    def __init__(self) -> None:
        self._caps: dict[tuple[str, str], Capability] = {}

    def register(self, cap: Capability, *, replace: bool = False) -> Capability:
        if cap.tool_name == ANSWER_TOOL:
            raise ValueError(f"'{ANSWER_TOOL}' está reservado para cerrar el bucle")
        clave = (cap.org_id or "", cap.tool_name)
        if clave in self._caps and not replace:
            raise ValueError(f"ya hay una capacidad con tool_name {cap.tool_name!r}")
        if cap.org_id and ("", cap.tool_name) in self._caps:
            # una organización no puede tapar una herramienta de la plataforma con la suya
            raise ValueError(f"{cap.tool_name!r} ya es una herramienta de la plataforma")
        if cap.parameters.get("type") != "object":
            raise ValueError(f"{cap.id}: `parameters` debe ser un JSON Schema de objeto")
        self._caps[clave] = cap
        return cap

    def unregister(self, tool_name: str, *, org_id: str | None = None) -> None:
        self._caps.pop((org_id or "", tool_name), None)

    def _visibles(self) -> list[Capability]:
        org = _org_de_la_peticion()
        return [c for (o, _), c in self._caps.items() if not o or o == org]

    def get(self, tool_name: str | None) -> Capability | None:
        nombre = tool_name or ""
        org = _org_de_la_peticion()
        return (self._caps.get((org, nombre)) if org else None) or self._caps.get(("", nombre))

    def all(self) -> list[Capability]:
        return self._visibles()

    def names(self) -> frozenset[str]:
        return frozenset(c.tool_name for c in self._visibles()) | {ANSWER_TOOL}

    def available(self, graph: Any) -> list[Capability]:
        return [c for c in self._visibles() if c.available(graph)]

    def tool_schemas(self, graph: Any = None) -> list[dict[str, Any]]:
        """Schemas para `tools=` del LLM. Con `graph`, solo las disponibles."""
        caps = self.all() if graph is None else self.available(graph)
        return [c.tool_schema() for c in caps] + [_answer_schema()]

    def prompt_block(self, graph: Any = None) -> str:
        """Lista de herramientas para el system prompt ReAct."""
        caps = self.all() if graph is None else self.available(graph)
        lineas = [f"- {c.tool_name}: {c.blurb}" for c in caps]
        lineas.append(f"- {ANSWER_TOOL}: {_ANSWER_BLURB}")
        return "\n".join(lineas)


def _answer_schema() -> dict[str, Any]:
    import copy

    return copy.deepcopy(_ANSWER_SCHEMA)


_REGISTRO = CapabilityRegistry()


def registry() -> CapabilityRegistry:
    """El registro del proceso."""
    return _REGISTRO


# ---------------------------------------------------------------------------
# F6 — ejecutar una capacidad: permisos por rol y auditoría, en UN solo sitio
# ---------------------------------------------------------------------------

#: Rol mínimo para cada nivel de riesgo (S6.3). Un visor ve, consulta y trae datos; calcular
#: (buffer, análisis…) o escribir en un sistema externo es de analista (y escribir, además,
#: pasa por la aprobación HITL). Un riesgo desconocido exige administración.
ROL_POR_RIESGO: dict[str, str] = {"read": "viewer", "external_egress": "viewer",
                                  "compute": "analyst", "write": "analyst"}


def rol_minimo(cap: Capability) -> str:
    return ROL_POR_RIESGO.get(cap.risk, "admin")


async def ejecutar(cap: Capability, graph: Any, working: dict, args: dict[str, Any], *,
                   origen: str = "agente") -> ToolOutcome:
    """Ejecuta `cap` como el usuario de la petición: comprueba su rol y deja constancia.

    Por aquí pasan el bucle del agente (dispatch_tool) y las ejecuciones desde el mapa y el
    panel de conexiones (/acciones/…/run, /connections/…/run). Sin principal (procesos del
    propio sistema, tests del núcleo) no hay nada que comprobar.
    """
    from geo_copilot.platform import auditoria
    from geo_copilot.platform.identidad.principal import principal_actual

    principal = principal_actual()
    sid = working.get("session_id")
    minimo = rol_minimo(cap)
    base = {"origen": origen, "riesgo": cap.risk, "argumentos": args}
    if principal is not None and not principal.puede(minimo):
        await auditoria.registrar("capacidad.ejecutar", cap.id, "denegado", session_id=sid,
                                  detalle={**base, "rol": principal.rol, "rol_requerido": minimo})
        return ToolOutcome(
            observation=(f"DENEGADO por permisos: «{cap.tool_name}» es una operación de riesgo «{cap.risk}», "
                         f"que requiere el rol «{minimo}»; este usuario tiene el rol «{principal.rol}». No se "
                         "ejecutó. Para este usuario están denegadas todas las herramientas de ese riesgo; "
                         "un administrador de su organización puede cambiarle el rol."),
            success=False,
            facts={"denegado": True, "riesgo": cap.risk, "rol": principal.rol, "rol_requerido": minimo},
        )
    from geo_copilot.platform.observabilidad import medir_herramienta

    # F7 (S7.3): cada herramienta es un span de la traza del turno y una muestra de latencia
    with medir_herramienta(cap.tool_name) as medida:
        try:
            out = await cap.executor(graph, working, args)
        except Exception as exc:
            await auditoria.registrar("capacidad.ejecutar", cap.id, "error", session_id=sid,
                                      detalle={**base, "error": type(exc).__name__})
            raise
        medida["ok"] = out.success
    await auditoria.registrar("capacidad.ejecutar", cap.id, "ok" if out.success else "error", session_id=sid,
                              detalle={**base, **({} if out.success else {"observacion": out.observation})})
    return out
