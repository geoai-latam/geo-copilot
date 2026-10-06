"""Los MODELOS del plan multipaso: los tipos de acción válidos, el estado de un paso, el paso, su
resultado y el plan de ejecución.

Salió de `planner.py` (F4 del plan de calidad: planner.py tenía 558 líneas), tal cual.
"""

from dataclasses import dataclass, field
from enum import Enum

# R2.5/R1.1: vocabulario canónico de action_type. Única fuente de verdad para
# validar los planes del LLM (generate_plan re-pregunta si un paso trae un tipo
# fuera de esta lista). Camino HEREDADO desde S1.3: con react_policy=hybrid
# (default) lo multi-paso va a ReAct, que toma sus herramientas del registro de
# capacidades; este vocabulario apunta a nodos del grafo y solo se usa con
# react_policy=off o HITL interrupt. Las capacidades nuevas (MCP) NO se añaden
# aquí (ver docs/TAREAS_PLATAFORMA_GEO_MCP.md, T1.7).
# Debe mantenerse en sync con _ACTION_TO_INTENT
# (nodes/step_router.py) — hay un test de sincronía que lo garantiza.
VALID_ACTION_TYPES = frozenset({
    "query_database",
    "search_external",
    "select_service",
    "spatial_operation",
    "analyze",
    "symbology",
    "apply_symbology",
    # A5: el plan puede PREGUNTAR al usuario cuando un paso exige información
    # que no está en la consulta. Es TERMINAL: pausa el plan con la pregunta.
    "ask_user",
})


class StepStatus(str, Enum):
    """Estado de un paso de ejecución."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    """
    Representa un paso individual en un plan de ejecución.

    El LLM genera cada paso con su tipo de acción explícito, evitando
    la necesidad de detectar el tipo con palabras clave.

    Attributes:
        step_id: Identificador único del paso (ej: "step_1")
        description: Descripción legible del paso
        query_fragment: Instrucción que se ejecutará
        action_type: Tipo de acción a ejecutar (determinado por LLM)
            - "query_database": Consulta a BD interna (PostgreSQL/PostGIS)
            - "search_external": Buscar servicios en fuentes externas
            - "select_service": Seleccionar/cargar servicio de lista previa
            - "spatial_operation": Operación espacial (buffer, centroide, área, etc.)
            - "analyze": Análisis/estadística/ML sobre la capa (sandbox Python)
            - "symbology": Aplicar simbología (colores, estilos)
    """
    step_id: str
    description: str
    query_fragment: str = ""
    # R2.5: sin default adivinador. None = el LLM no lo emitió → el plan se
    # considera inválido en generate_plan (re-pregunta) y, si llegara a
    # ejecución, step_router lo marca como paso fallido honesto (antes el
    # default 'general' caía en silencio al adivinador query_data).
    action_type: str | None = None
    # F3.1: IDs de pasos de los que éste depende. None = default lineal (depende
    # del paso anterior). [] = independiente (sobrevive al fallo de otra rama).
    depends_on: list[str] | None = None
    #: Solo `select_service`: el número (1..N) del servicio de la lista mostrada, decidido por el
    #: LLM. Antes se sacaba del texto con una regex («carga el de 2024» → servicio 2024).
    service_number: int | None = None


@dataclass
class StepResult:
    """
    Resultado de la ejecución de un paso.

    Attributes:
        step_id: ID del paso ejecutado
        success: Si la ejecución fue exitosa
        output: Datos de salida del paso
        error: Mensaje de error si falló
        retry_count: Número de reintentos realizados
        skipped: Si el paso fue saltado por condición
        skip_message: Mensaje si fue saltado
    """
    step_id: str
    success: bool
    output: dict = field(default_factory=dict)
    error: str | None = None
    retry_count: int = 0
    skipped: bool = False
    skip_message: str | None = None


@dataclass
class ExecutionPlan:
    """
    Plan completo de ejecución multi-paso.

    Attributes:
        is_multi_step: Si el plan tiene múltiples pasos
        reasoning: Razonamiento del por qué se creó el plan
        steps: Lista de pasos a ejecutar
        original_query: Query original del usuario
    """
    is_multi_step: bool
    reasoning: str
    steps: list[PlanStep]
    original_query: str = ""
