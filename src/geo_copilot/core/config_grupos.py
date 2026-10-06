"""Los GRUPOS de campos de la configuración que `Settings` hereda.

Salieron de `Settings` (F4 del plan de calidad: config.py tenía 615 líneas), tal cual: pydantic
junta los campos de las bases, así que cada variable de entorno se lee igual.
"""


from pydantic import Field
from pydantic_settings import BaseSettings


class ConfigAutonomia(BaseSettings):
    """La AUTONOMÍA del agente: autocorrección, planes multipaso, HITL, ReAct, reflexiones
    y sus tiempos."""

    # =================================================================
    # AUTONOMÍA: Self-correction y Multi-step Planning
    # =================================================================
    autonomous_mode: bool = Field(
        default=True,
        description="Habilitar modo autónomo con auto-corrección. Si es False, usa flujo original sin reintentos."
    )
    max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Máximo de reintentos por agente en modo autónomo (0 = sin reintentos)"
    )
    enable_planning: bool = Field(
        default=True,
        description="Habilitar planificación multi-paso (Fase 2). Permite descomponer consultas complejas."
    )
    max_plan_steps: int = Field(
        default=5,
        ge=2,
        le=10,
        description="Máximo de pasos permitidos en un plan de ejecución"
    )
    # Persistencia durable del estado del agente (EntityMemory/AgentMetrics).
    # None = solo en memoria (se pierde al reiniciar). Si se da un dir, el
    # aprendizaje (resoluciones de entidad + métricas cost-aware) sobrevive
    # reinicios. v1 single-process (JSON atómico).
    agent_state_dir: str | None = Field(
        default=None,
        description="Dir para persistir EntityMemory/AgentMetrics (None = solo memoria)."
    )
    # Fase 4 (F4.2): mecanismo de HITL. 'blocking' (producción) = el actual,
    # asyncio.Event + request_approval; 'interrupt' (EXPERIMENTAL/spike) =
    # LangGraph interrupt()+checkpointer. Default 'blocking' → guardrail intacto.
    # El cutover completo está DIFERIDO (ver orchestrator/hitl_interrupt.py).
    hitl_mode: str = Field(
        default="blocking",
        description="Mecanismo HITL: 'blocking' (producción) | 'interrupt' (LangGraph)."
    )
    # Checkpointer para hitl_mode='interrupt'. 'memory' = MemorySaver (in-process,
    # resume mismo proceso); 'postgres' = AsyncPostgresSaver durable (requiere
    # langgraph-checkpoint-postgres + hitl_checkpointer_dsn). Cae a 'memory' si
    # postgres no está disponible.
    hitl_checkpointer: str = Field(
        default="memory",
        description="Checkpointer del HITL interrupt: 'memory' | 'postgres'."
    )
    hitl_checkpointer_dsn: str | None = Field(
        default=None,
        description="DSN Postgres para el checkpointer durable (si hitl_checkpointer='postgres')."
    )
    # Fase 4 (ReAct): si True, el entry usa el bucle agent_loop (capability
    # listing + tool-calling) en vez del routing por intent-enum. Default False
    # → comportamiento existente intacto. Experimental (F4.5).
    react_mode: bool = Field(
        default=False,
        description="Usar el bucle ReAct (agent_loop) como entry en vez del router por intent."
    )
    # A2 (Fase 4 remediación): política ReAct por consulta. 'off' = grafo
    # cableado siempre; 'hybrid' = el router decide — consultas COMPLEJAS
    # (multi-operación) van al bucle ReAct, las simples al grafo cableado
    # (más barato y predecible); 'always' = todo al bucle. ``react_mode=True``
    # (flag viejo) equivale a 'always' y tiene precedencia (compat).
    #
    # DEFAULT 'hybrid' (decisión 2026-07-19, condicionada al benchmark A1):
    # off 93.33% == hybrid 93.33% (32 tareas, LLM real), latencia mediana
    # 6.8s vs 6.4s. ROLLBACK: exportar REACT_POLICY=off (sin redeploy de
    # código); en HITL 'interrupt' el hybrid se desactiva solo.
    react_policy: str = Field(
        default="hybrid",
        description="Política ReAct: 'off' | 'hybrid' (complejas → bucle) | 'always'."
    )
    # FH.8: tras cada respuesta, 2–3 siguientes pasos que propone el LLM (una llamada más,
    # corta, con tope de 8 s). SUGERENCIAS_HABILITADAS=false la quita (costo/latencia).
    sugerencias_habilitadas: bool = Field(
        default=True, description="Sugerencias de siguiente paso tras cada respuesta (una llamada LLM más)."
    )
    # Fase 4 (ReAct): tope de iteraciones del bucle agent_loop. Circuit-breaker
    # (F4.3) — evita que un LLM en bucle gaste herramientas/tokens sin fin.
    react_max_tool_calls: int = Field(
        # 8 → 12 (V5 de los MCP): con el geocodificador, todo pedido «de un lugar» gasta 2 llamadas
        # (buscar + límite) antes de empezar; con un par de reintentos se cortaba a mitad de la cadena.
        default=12,
        ge=1,
        # 30 impedía incluso configurar más a un modelo capaz de encadenar un análisis largo
        le=200,
        description="Máximo de tool-calls por turno en el bucle ReAct (circuit-breaker)"
    )
    react_token_budget: int = Field(
        default=0,
        ge=0,
        description="Presupuesto de tokens por turno ReAct (0 = sin límite de tokens)"
    )
    # Fase 5 (F5): nº máx de reflexiones composicionales antes de aceptar la
    # respuesta. Cuando el agente va a `answer`, un juez verifica que cubra la
    # consulta; si no, lo empuja a seguir (acotado). 0 = desactivado.
    react_max_reflections: int = Field(
        default=1,
        ge=0,
        le=3,
        description="Máx. de re-trabajos por validación composicional del bucle ReAct (0 = off)"
    )
    plan_timeout: int = Field(
        default=300,
        ge=60,
        le=600,
        description="Timeout total para ejecución de un plan multi-paso (en segundos)"
    )
    # Timeouts para autonomía
    total_execution_timeout: int = Field(
        default=120,
        ge=30,
        le=600,
        description="Timeout total para todo el proceso en segundos"
    )


class ConfigPresentacion(BaseSettings):
    """La PRESENTACIÓN: colores del tema, palabras clave de detección (errores de auth,
    fechas, geometría, nombres e ids), simbología por defecto y CRS de salida."""

    # Theme colors (para reportes y visualización)
    theme_primary_color: str = Field(default="#667eea", description="Color primario del tema")
    theme_secondary_color: str = Field(default="#764ba2", description="Color secundario del tema")
    theme_success_color: str = Field(default="#48bb78", description="Color de éxito")
    theme_warning_color: str = Field(default="#ed8936", description="Color de advertencia")
    theme_error_color: str = Field(default="#f56565", description="Color de error")

    # =================================================================
    # DETECTION KEYWORDS (configurable para diferentes idiomas/contextos)
    # =================================================================
    auth_error_codes: list[int] = Field(
        default=[499, 498, 403, 401, 400],
        description="Códigos HTTP que indican error de autenticación"
    )
    auth_error_keywords: list[str] = Field(
        default=["token", "access", "denied", "unauthorized", "authentication", "permission"],
        description="Keywords que indican error de autenticación en mensajes"
    )
    date_field_keywords: list[str] = Field(
        default=["date", "fecha", "time", "periodo", "year", "año", "mes", "month", "dia", "day"],
        description="Keywords para detectar campos de fecha en datos"
    )
    geometry_field_names: list[str] = Field(
        default=["geometry", "geom", "geom_geojson", "shape", "the_geom"],
        description="Nombres de campos de geometría a excluir de visualización"
    )

    # =================================================================
    # SYMBOLOGY DEFAULTS (personalizables por deployment)
    # =================================================================
    default_fill_opacity: float = Field(default=0.6, ge=0, le=1, description="Opacidad de relleno por defecto")
    default_stroke_width: float = Field(default=2, ge=0, description="Ancho de línea por defecto")
    default_marker_size: float = Field(default=8, ge=1, description="Tamaño de marcador por defecto")
    default_font_size: int = Field(default=12, ge=6, le=36, description="Tamaño de fuente por defecto")
    default_num_classes: int = Field(default=5, ge=2, le=12, description="Número de clases para clasificación")

    # Field detection keywords (para semantic layer y análisis)
    name_field_keywords: list[str] = Field(
        default=["nombre", "name", "nom_mun", "nom", "descripcion", "description", "titulo", "title"],
        description="Keywords para detectar campos de nombre"
    )
    id_field_keywords: list[str] = Field(
        default=["id", "objectid", "gid", "fid", "codigo", "code"],
        description="Keywords para detectar campos de ID"
    )

    # Output CRS
    default_output_crs: str = Field(
        default="EPSG:4326",
        description="Sistema de coordenadas por defecto para salida"
    )
