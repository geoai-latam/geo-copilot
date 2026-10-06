"""
Multi-Step Planning: PlannerAgent y PlanExecutor para consultas complejas.

ORC-5 (2026-05-30):
- ``PlannerAgent`` genera el plan (intacto).
- ``PlanExecutor`` quedó como capa de compatibilidad — el bucle de
  ejecución vive ahora dentro del grafo compilado de LangGraph
  (``orchestrator/nodes/step_router.py`` y ``step_finalizer.py``).
"""

from typing import Any, cast

from geo_copilot.agents.base import AgentResponse, BaseAgent
from geo_copilot.core.config import get_settings
from geo_copilot.core.llm_client import LLMClient, LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.core.utils import parse_json_from_llm
from geo_copilot.prompts import cargar_prompt

logger = get_logger(__name__)

# F4: los modelos del plan viven en plan_modelos; se reexportan porque se importan de aquí.
from geo_copilot.orchestrator.plan_modelos import (  # noqa: F401
    VALID_ACTION_TYPES,
    ExecutionPlan,
    PlanStep,
    StepResult,
    StepStatus,
)

# =============================================================================
# Estructuras de datos
# =============================================================================


# =============================================================================
# PlannerAgent: Genera planes de ejecución
# =============================================================================

class PlannerAgent(BaseAgent):
    """
    Agente que analiza consultas complejas y genera planes de ejecución.

    Detecta cuando una consulta requiere múltiples pasos (ej: "busca bomberos
    y hazles buffer 500m") y genera un plan estructurado para ejecutarlos
    secuencialmente.

    NOTA: La detección de complejidad es realizada por el LLM, no por
    palabras clave, para mayor robustez y flexibilidad.
    """

    def __init__(self, llm_client: LLMClient | None = None):
        super().__init__(
            name="PlannerAgent",
            description="Genera planes de ejecución para consultas complejas"
        )
        settings = get_settings()
        self.llm_client = llm_client or LLMClient.from_settings(settings)
        self.settings = settings

    def get_capabilities(self) -> dict:
        """Retornar capacidades del agente."""
        return {
            "actions": ["analyze_complexity", "generate_plan"],
            "supported_operations": [
                "search_external",
                "load_external",
                "select_service",
                "spatial_operation",
                "query_data",
            ],
        }

    async def is_complex_query(self, query: str) -> bool:
        """
        Detecta si una query necesita múltiples pasos usando el LLM.

        Args:
            query: Consulta del usuario

        Returns:
            True si la query requiere planificación multi-paso
        """
        prompt = f"""Analiza si esta consulta requiere MÚLTIPLES OPERACIONES SECUENCIALES.

CONSULTA: "{query}"

Una consulta es multi-paso si necesita ejecutar operaciones DISTINTAS en SECUENCIA.

Ejemplos multi-paso (true):
- "Busca bomberos y hazles buffer de 500m" (buscar + operación espacial)
- "Carga hospitales y luego calcula el área" (cargar + operación espacial)
- "Busca colegios, carga el primero y filtra por zona norte" (buscar + seleccionar + filtrar)

Ejemplos NO multi-paso (false):
- "Busca bomberos" (una sola operación)
- "Hazle buffer a los datos" (una sola operación)
- "Muestra los parques" (una sola operación)

Responde SOLO con JSON: {{"is_multi_step": true}} o {{"is_multi_step": false}}"""

        try:
            response = await self.llm_client.chat([
                LLMMessage(role="user", content=prompt)
            ])
            result = parse_json_from_llm(response.content, default={})
            return cast(bool, result.get("is_multi_step", False))
        except Exception as e:  # llamada LLM; ante fallo se trata como consulta de un paso
            logger.warning(f"[PlannerAgent] Error detecting complexity: {e}", exc_info=True)
            return False

    async def process(
        self,
        query: str,
        context: dict | None = None
    ) -> AgentResponse:
        """
        Procesar una consulta y generar un plan si es compleja.

        Args:
            query: Consulta del usuario
            context: Contexto con información adicional

        Returns:
            AgentResponse con el plan generado
        """
        context = context or {}

        # NOTA: La verificación de complejidad ya la hizo el RouterAgent,
        # así que no es necesario verificarla aquí. Si llegamos a este punto,
        # ya sabemos que es una query compleja que necesita planificación.

        try:
            plan = await self.generate_plan(query, context)
            return AgentResponse(
                success=True,
                message=f"Plan generado con {len(plan.steps)} pasos",
                data={
                    "is_multi_step": plan.is_multi_step,
                    "reasoning": plan.reasoning,
                    "steps": [self._step_to_dict(s) for s in plan.steps],
                }
            )
        except Exception as e:  # noqa: BLE001 — frontera del agente (LLM); sanitize_error registra el traceback
            logger.error(f"[PlannerAgent] Error generating plan: {e}")
            # R3.5: sin str(e) crudo hacia el cliente (fugaba paths/tablas
            # saltándose SEC-ERROR-LEAK). sanitize_error loguea el detalle y
            # devuelve un mensaje seguro.
            from geo_copilot.core.error_sanitizer import sanitize_error
            return AgentResponse(
                success=False,
                message=sanitize_error(
                    e,
                    context="planner.generate_plan",
                    user_message="No pude generar un plan para esta consulta.",
                ),
                data={"is_multi_step": False, "steps": []}
            )

    @staticmethod
    def _validate_plan_payload(result: Any) -> str | None:
        """Valida el payload del LLM ANTES de construir el plan (R2.1/R2.5).

        Devuelve la descripción del problema (para re-preguntar al LLM) o
        ``None`` si el plan es válido. No corrige ni adivina: la corrección es
        del LLM.
        """
        if not isinstance(result, dict):
            return "la respuesta no es un objeto JSON parseable"
        steps = result.get("steps")
        if not isinstance(steps, list) or not steps:
            return "el plan no contiene ningún paso en 'steps'"
        for i, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                return f"el paso {i} no es un objeto"
            action = step.get("action_type")
            if action not in VALID_ACTION_TYPES:
                return (
                    f"el paso {i} tiene action_type inválido {action!r}; "
                    f"usa uno de: {', '.join(sorted(VALID_ACTION_TYPES))}"
                )
            if not (step.get("query_fragment") or step.get("description")):
                return f"el paso {i} no tiene query_fragment ni description"
        return None

    async def generate_plan(self, query: str, context: dict) -> ExecutionPlan:
        """
        Generar un plan de ejecución para una consulta compleja.

        Usa el LLM para analizar la consulta y descomponerla en pasos
        ejecutables secuencialmente.

        Args:
            query: Consulta del usuario
            context: Contexto adicional

        Returns:
            ExecutionPlan con los pasos a ejecutar
        """
        system_prompt = self._build_planner_prompt(context)
        user_prompt = f'CONSULTA DEL USUARIO: "{query}"'
        result = await self._pedir_plan(system_prompt, user_prompt)
        steps = self._pasos(result)

        return ExecutionPlan(
            is_multi_step=len(steps) > 1,
            reasoning=result.get("reasoning", "Plan generado automáticamente"),
            steps=steps,
            original_query=query,
        )

    async def _pedir_plan(self, system_prompt: str, user_prompt: str) -> dict:
        """El plan del LLM con su contenido validado (re-pregunta UNA vez; si no, ValueError)."""
        # R2.1: sin fallback adivinador. A3: el plan llega como una LLAMADA a la
        # función `create_plan` con schema (structured outputs) — la FORMA la
        # garantiza structured_call (con su propio re-intento); el CONTENIDO
        # (steps>=1, action_type del vocabulario) se valida aquí y, si es
        # inválido, se RE-PREGUNTA UNA vez adjuntando el problema; si vuelve a
        # fallar, el planner falla HONESTO (ValueError → success=False).
        from geo_copilot.core.structured_output import structured_call
        plan_parameters = _esquema_del_plan()
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
        result: dict | None = None
        problem: str | None = None
        for attempt in (1, 2):
            candidate = await structured_call(
                self.llm_client, messages,
                name="create_plan",
                description="Registra el plan de ejecución multi-paso para la consulta.",
                parameters=plan_parameters,
            )
            problem = self._validate_plan_payload(candidate)
            max_pasos = getattr(self.settings, "max_plan_steps", 5)
            if problem is None and len(candidate.get("steps") or []) > max_pasos:
                # Antes se truncaba en silencio (solo el log lo decía): los últimos pasos del plan
                # —a menudo el que responde— desaparecían. Es un límite: se le dice al LLM.
                problem = (f"el plan tiene {len(candidate['steps'])} pasos y el máximo es {max_pasos}: "
                           "combina pasos o deja fuera lo secundario (dilo en `reasoning`)")
            if problem is None:
                result = candidate
                break
            logger.warning(
                f"[PlannerAgent] Plan inválido (intento {attempt}): {problem} — "
                + ("re-preguntando al LLM" if attempt == 1 else "fallo honesto")
            )
            messages = messages[:2] + [
                LLMMessage(
                    role="user",
                    content=(
                        f"Tu plan anterior NO es válido: {problem}. "
                        "Llama a `create_plan` con el plan corregido: al menos un "
                        "paso y cada paso con un action_type del vocabulario."
                    ),
                ),
            ]
        if result is None:
            raise ValueError(
                f"El planificador no produjo un plan válido tras reintentar: {problem}"
            )
        return result

    def _pasos(self, result: dict) -> list[PlanStep]:
        """Los pasos del plan (ya validado: steps>=1 y action_type dentro del vocabulario)."""
        steps = []
        for i, step_data in enumerate(result.get("steps", [])):
            raw_deps = step_data.get("depends_on")
            depends_on = (
                [str(d) for d in raw_deps if d] if isinstance(raw_deps, list) else None
            )
            step = PlanStep(
                step_id=step_data.get("step_id", f"step_{i+1}"),
                description=step_data.get("description", ""),
                query_fragment=step_data.get("query_fragment", ""),
                action_type=step_data.get("action_type"),  # validado arriba
                depends_on=depends_on,  # F3.1: None = default lineal
                service_number=_entero(step_data.get("service_number")),
            )
            steps.append(step)
            logger.info(f"[PlannerAgent] Step {step.step_id}: action_type={step.action_type}")

        # F3.1: step_id únicos. Un id duplicado haría ambigua la resolución de
        # ``depends_on`` (¿cuál de los dos?). Renombramos los repetidos para que
        # el gating por dependencias sea inequívoco.
        seen_ids: set[str] = set()
        for n, st in enumerate(steps):
            if st.step_id in seen_ids:
                new_id = f"{st.step_id}_{n + 1}"
                logger.warning(
                    f"[PlannerAgent] step_id duplicado '{st.step_id}' → '{new_id}'"
                )
                st.step_id = new_id
            seen_ids.add(st.step_id)

        # Validar y limitar pasos
        max_steps = getattr(self.settings, 'max_plan_steps', 5)
        if len(steps) > max_steps:
            logger.warning(f"[PlannerAgent] Plan truncated from {len(steps)} to {max_steps} steps")
            steps = steps[:max_steps]
            # Tras truncar, una depends_on a un paso eliminado queda huérfana;
            # blocked_dependencies (semántica "satisfecha") la trata como no
            # satisfecha → ese paso se salta honestamente en vez de correr mal.
        return steps

    def _build_planner_prompt(self, context: dict) -> str:
        """Construir prompt del sistema para el planificador."""
        has_external_data = context.get("has_external_data", False)
        external_source = context.get("external_source_name") or ""
        found_services = context.get("found_services") or []
        schema_info = context.get("schema_info") or ""

        # Construir contexto de servicios encontrados
        services_context = ""
        if found_services:
            services_list = "\n".join([f"  {i+1}. {s.get('name', 'Sin nombre')}" for i, s in enumerate(found_services[:5])])
            services_context = f"\n\nSERVICIOS EXTERNOS DISPONIBLES (de búsqueda previa):\n{services_list}"

        # Contexto de datos ya cargados — explícito y enfático para
        # evitar que el LLM genere search_external redundante cuando los
        # datos ya están en memoria.
        data_context = ""
        if has_external_data:
            data_context = (
                f"\n\n⚠️ DATOS YA CARGADOS EN MEMORIA: '{external_source}'\n"
                f"NO generes pasos de search_external ni select_service para "
                f"este dataset. Aplica las operaciones DIRECTAMENTE sobre los datos "
                f"cargados, eligiendo bien el action_type:\n"
                f"  - color/estilo/clasificación/MAPA DE CALOR (heatmap)/CLUSTER → action_type=symbology\n"
                f"  - buffer/centroide/área/intersección/unión → action_type=spatial_operation"
            )

        # CAPAS EN EL MAPA (todas las cargadas ahora, de BD o REST). El planner
        # las usa para NO re-consultar una entidad que YA está cargada.
        from geo_copilot.core.formatters import format_map_context
        map_block = format_map_context(context.get("map_context"))
        loaded_context = f"\n\n{map_block}" if map_block else ""

        return cargar_prompt("planificador").format(
            schema_info=schema_info if schema_info else "Schema no disponible",
            data_context=data_context,
            services_context=services_context,
            loaded_context=loaded_context,
        )

    def _step_to_dict(self, step: PlanStep) -> dict:
        """Convertir PlanStep a diccionario."""
        return {
            "step_id": step.step_id,
            "description": step.description,
            "query_fragment": step.query_fragment,
            "action_type": step.action_type,
            "depends_on": step.depends_on,  # F3.1
            "service_number": step.service_number,
        }


# =============================================================================
# PlanExecutor: ORC-5 (2026-05-30) — solo expone helpers de normalización
# y notificación. El bucle de ejecución vive ahora dentro del grafo
# compilado de LangGraph (``step_router`` → agentes → ``step_finalizer``
# en ``orchestrator/nodes/``). Antes esta clase corría un for-loop Python
# que invocaba a los nodos directamente, duplicando el routing del grafo
# principal. Conservamos la clase con superficie mínima para no romper
# el constructor de ``GeoAgentGraph`` (instancia compatibilidad) y para
# tests que aún ejercitan ``_normalize_plan``.
# =============================================================================

def _esquema_del_plan() -> dict:
    """El schema de la función `create_plan` (la FORMA del plan; el contenido se valida aparte)."""
    return {
        "type": "object",
        "properties": {
            "reasoning": {
                "type": "string",
                "description": "Por qué el plan tiene estos pasos, en este orden.",
            },
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_id": {"type": "string"},
                        "description": {"type": "string"},
                        "query_fragment": {
                            "type": "string",
                            "description": "Instrucción autónoma para el agente que ejecuta el paso.",
                        },
                        "action_type": {
                            "type": "string",
                            "enum": sorted(VALID_ACTION_TYPES),
                        },
                        "depends_on": {
                            "type": ["array", "null"],
                            "items": {"type": "string"},
                            "description": "step_ids de los que depende (null = el anterior).",
                        },
                        "service_number": {
                            "type": ["integer", "null"],
                            "description": "Solo select_service: el número (1..N) del servicio a cargar, "
                                           "de la lista de servicios ya mostrada.",
                        },
                    },
                    "required": ["description", "query_fragment", "action_type"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["steps", "reasoning"],
        "additionalProperties": False,
    }


def _entero(valor: Any) -> int | None:
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else None


class PlanExecutor:
    """Capa de compatibilidad post-ORC-5 — el routing y el bucle viven
    dentro del grafo compilado, este wrapper solo tiene utilidades sueltas.
    """

    def __init__(self, graph: Any = None):
        self.graph = graph
        self.settings = get_settings()
        self.max_retries = getattr(self.settings, 'max_retries', 2)

    @staticmethod
    def _normalize_plan(plan: ExecutionPlan | list[dict]) -> list[PlanStep]:
        """Convertir un plan (ExecutionPlan o lista de dicts) a lista de PlanStep.

        Útil para tests y para callers que construyan el plan manualmente.
        El grafo compilado consume directo la lista de dicts del state.
        """
        if isinstance(plan, ExecutionPlan):
            return plan.steps

        steps: list[PlanStep] = []
        for i, step_data in enumerate(plan):
            if isinstance(step_data, PlanStep):
                steps.append(step_data)
            else:
                _raw_deps = step_data.get("depends_on")
                steps.append(PlanStep(
                    step_id=step_data.get("step_id", f"step_{i + 1}"),
                    description=step_data.get("description", ""),
                    query_fragment=step_data.get("query_fragment", ""),
                    action_type=step_data.get("action_type", "general"),
                    depends_on=(
                        [str(d) for d in _raw_deps if d]
                        if isinstance(_raw_deps, list) else None
                    ),
                    service_number=_entero(step_data.get("service_number")),
                ))
        return steps


# El bucle Python original (``execute_plan``, ``_execute_step``,
# ``_execute_through_graph``, ``_merge_step_output``, ``_compile_final_state``
# + notificaciones por step) se eliminó en ORC-5. Su funcionalidad equivalente
# pasó a ``orchestrator/nodes/step_router.py`` y ``step_finalizer.py``, que
# corren como nodos nativos de LangGraph dentro del grafo compilado.
#
# Lo que se ganó:
#   - Un solo motor de routing (el del grafo compilado).
#   - Telemetría WS uniforme (mismos eventos para single-step y multi-step).
#   - Refactorizar el flujo de un agente actualiza ambos modos a la vez.
#   - 350+ LOC borradas de este módulo.
