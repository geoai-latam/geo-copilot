"""Validación CRÍTICA con LLM REAL del ROUTER de intención (@pytest.mark.llm).

Superficie: ``RouterAgent.process(query, context) -> AgentResponse`` cuyo
``data.intent`` clasifica la intención del usuario en uno de:
``direct_response | query_data | follow_up | search_external |
select_service | load_external | spatial_operation | apply_symbology``.

Estos tests llaman a un LLM de verdad para verificar que el PROMPT del router
provoca la clasificación correcta en un modelo real — lo que un mock no puede.
Las aserciones son sobre la DIRECCIÓN del juicio (qué intent), no sobre texto.

Para cada juicio incluimos casos CLAROS (dirección estable) + al menos un caso
DISCRIMINANTE donde las palabras clave engañarían a una heurística pero la
intención real es otra. Imprimimos el reasoning del LLM con print() y, en la
validación crítica, juzgamos si la decisión es SÓLIDA (no solo verde).

Correr:
    pytest -p no:cacheprovider -o addopts="" -m llm -s -q tests/test_llm_router.py
"""

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# Schema mínimo y realista para que el router sepa qué hay en la BD interna.
_SCHEMA = (
    "lotes(id int, nombre text, barrio text, uso_suelo text, area_m2 numeric, geom geometry); "
    "bomberos(id int, nombre text, geom geometry); "
    "barrios(id int, nombre text, geom geometry)"
)


def _router(llm):
    from geo_copilot.agents.router_agent.agent import RouterAgent
    return RouterAgent(llm_client=llm)


async def _route(llm, query, **ctx_extra):
    """Ejecuta el router y devuelve (intent, reasoning, full_data)."""
    ctx = {"schema_info": _SCHEMA}
    ctx.update(ctx_extra)
    resp = await _router(llm).process(query, ctx)
    data = resp.data or {}
    return data.get("intent"), data.get("reasoning", ""), data


def _active_layer_ctx(
    *,
    source="internal",
    name="lotes del centro",
    count=42,
    geometry="Polygon",
    fields=None,
):
    """Contexto con una capa YA CARGADA en el mapa (para Smart Router)."""
    return {
        "active_data_source": source,
        "active_source_name": name,
        "active_feature_count": count,
        "active_geometry_type": geometry,
        "active_field_names": fields or ["id", "nombre", "uso_suelo", "area_m2"],
    }


# ===========================================================================
# 1) Pregunta general / saludo / capacidades  →  direct_response
# ===========================================================================
@pytest.mark.asyncio
async def test_general_question_is_direct_response():
    llm = await get_real_llm_or_skip()
    intent, reason, _ = await _route(llm, "Hola, ¿qué puedes hacer?")
    print(f"\n[direct/capabilities] intent={intent} reason={reason}")
    assert intent == "direct_response"


@pytest.mark.asyncio
async def test_conceptual_question_is_direct_response():
    llm = await get_real_llm_or_skip()
    # Pregunta conceptual de GIS: no requiere tocar la BD ni buscar datos.
    intent, reason, _ = await _route(llm, "¿Qué es un buffer en análisis espacial?")
    print(f"\n[direct/conceptual-buffer] intent={intent} reason={reason}")
    # DISCRIMINANTE PARCIAL: aparece "buffer", que una heurística mandaría a
    # spatial_operation; pero es una pregunta teórica → direct_response.
    assert intent == "direct_response"


# ===========================================================================
# 2) "trae los lotes del centro"  →  query_data
# ===========================================================================
@pytest.mark.asyncio
async def test_fetch_internal_data_is_query_data():
    llm = await get_real_llm_or_skip()
    intent, reason, _ = await _route(llm, "trae los lotes del centro")
    print(f"\n[query_data/lotes] intent={intent} reason={reason}")
    assert intent == "query_data"


@pytest.mark.asyncio
async def test_aggregate_count_is_query_data():
    llm = await get_real_llm_or_skip()
    intent, reason, _ = await _route(llm, "¿cuántos lotes hay en el barrio centro?")
    print(f"\n[query_data/count] intent={intent} reason={reason}")
    assert intent == "query_data"


# ===========================================================================
# 3) "busca datos de bomberos"  →  search_external
# ===========================================================================
@pytest.mark.asyncio
async def test_search_external_explicit():
    llm = await get_real_llm_or_skip()
    intent, reason, _ = await _route(llm, "busca datos de bomberos en portales públicos")
    print(f"\n[search_external/explicit] intent={intent} reason={reason}")
    assert intent == "search_external"


@pytest.mark.asyncio
async def test_search_external_imagery():
    llm = await get_real_llm_or_skip()
    # El prompt instruye explícitamente: imágenes/ortofotos → search_external
    # (NUNCA "no manejo imágenes"). Validamos esa resolución creativa.
    intent, reason, _ = await _route(
        llm, "necesito ortofotos satelitales recientes de Medellín"
    )
    print(f"\n[search_external/imagery] intent={intent} reason={reason}")
    assert intent == "search_external"


# ===========================================================================
# 4) "ponlo en rojo" con capa activa  →  apply_symbology
# ===========================================================================
@pytest.mark.asyncio
async def test_apply_symbology_color_with_active_layer():
    llm = await get_real_llm_or_skip()
    intent, reason, _ = await _route(
        llm, "ponlos en rojo", **_active_layer_ctx()
    )
    print(f"\n[apply_symbology/red] intent={intent} reason={reason}")
    assert intent == "apply_symbology"


@pytest.mark.asyncio
async def test_apply_symbology_choropleth_with_active_layer():
    llm = await get_real_llm_or_skip()
    intent, reason, _ = await _route(
        llm,
        "coloréalos por uso_suelo",
        **_active_layer_ctx(fields=["id", "nombre", "uso_suelo", "area_m2"]),
    )
    print(f"\n[apply_symbology/choropleth] intent={intent} reason={reason}")
    assert intent == "apply_symbology"


# ===========================================================================
# 5) "buffer de 500m" con capa activa  →  spatial_operation
# ===========================================================================
@pytest.mark.asyncio
async def test_spatial_operation_buffer_with_active_layer():
    llm = await get_real_llm_or_skip()
    intent, reason, data = await _route(
        llm, "hazles un buffer de 500m", **_active_layer_ctx()
    )
    print(f"\n[spatial_operation/buffer] intent={intent} reason={reason} "
          f"ops={data.get('additional_operations')}")
    assert intent == "spatial_operation"


@pytest.mark.asyncio
async def test_spatial_operation_centroid_with_active_layer():
    llm = await get_real_llm_or_skip()
    intent, reason, _ = await _route(
        llm, "dame el centroide de cada uno", **_active_layer_ctx()
    )
    print(f"\n[spatial_operation/centroid] intent={intent} reason={reason}")
    assert intent == "spatial_operation"


# ===========================================================================
# DISCRIMINANTES — keywords engañarían a una heurística; el LLM debe acertar
# ===========================================================================
# BUG DE PROMPT ARREGLADO: la regla spatial_operation ahora tiene una
# PRECONDICIÓN DURA (CAPA ACTIVA Features>0) y distingue tabla-en-schema de
# capa-cargada. El LLM ya elige query_data correctamente. (Era xfail strict.)
@pytest.mark.asyncio
async def test_discriminant_buffer_keyword_without_layer_is_not_spatial_op():
    """DISCRIMINANTE: 'buffer de 500m' SIN capa cargada.

    Una heurística por keyword ('buffer','metros') dispararía spatial_operation.
    Pero spatial_operation requiere una CAPA YA CARGADA; sin datos no hay nada
    sobre qué operar. El prompt dice: si no hay capa, elige search_external o
    query_data. El usuario nombra 'bomberos' (entidad), así que la dirección
    correcta es traer/buscar esos datos primero, NO spatial_operation.

    xfail estricto: documenta el bug de prompt observado (el LLM elige
    spatial_operation alucinando datos cargados). Si el prompt se corrige y
    el modelo pasa a query_data/search_external, este xfail empezará a
    XPASS y delatará que el bug ya no aplica — momento de quitar el marcador.
    """
    llm = await get_real_llm_or_skip()
    # Sin contexto de capa activa (active_data_source ausente → "none").
    intent, reason, _ = await _route(
        llm, "hazle un buffer de 500 metros a los bomberos"
    )
    print(f"\n[DISCRIM buffer-no-layer] intent={intent} reason={reason}")
    # Lo crítico: NO debe ser spatial_operation (no hay capa). Debe resolver
    # consiguiendo los datos: query_data (bomberos está en el schema interno)
    # o search_external. Multi-step (buscar+buffer) también es aceptable.
    assert intent != "spatial_operation"
    assert intent in {"query_data", "search_external"}


@pytest.mark.asyncio
async def test_discriminant_search_keyword_but_layer_loaded_is_symbology():
    """DISCRIMINANTE: el usuario dice 'busca' pero ya tiene la capa cargada.

    'busca' es la keyword canónica de search_external. PERO el prompt tiene la
    regla Smart Router: con una capa cargada, un ajuste sobre ESA capa NO es
    nueva búsqueda. 'búscame los que sean comerciales y píntalos de azul' opera
    sobre 'uso_suelo' que YA está en los campos cargados → es un re-estilo /
    filtro visual de la capa activa, no una búsqueda externa nueva.
    """
    llm = await get_real_llm_or_skip()
    intent, reason, _ = await _route(
        llm,
        "de estos, píntame de azul los comerciales",
        **_active_layer_ctx(fields=["id", "nombre", "uso_suelo", "area_m2"]),
    )
    print(f"\n[DISCRIM search-kw-but-loaded] intent={intent} reason={reason}")
    # NO debe ser search_external/query_data: los datos ya están cargados y el
    # campo uso_suelo ya está disponible. Es ajuste visual → apply_symbology.
    assert intent not in {"search_external", "query_data"}
    assert intent == "apply_symbology"


@pytest.mark.asyncio
async def test_discriminant_red_color_without_layer_is_not_symbology():
    """DISCRIMINANTE: 'en rojo' pero NO hay ninguna capa cargada.

    'rojo/color' son keywords de apply_symbology. Pero apply_symbology exige una
    capa YA CARGADA. Sin capa, 'muéstrame los lotes comerciales en rojo' es ante
    todo una petición de DATOS (los lotes comerciales de la BD); el color es un
    detalle de presentación que vendrá después. La dirección correcta es
    query_data (lotes está en el schema), NO apply_symbology sobre la nada.
    """
    llm = await get_real_llm_or_skip()
    intent, reason, _ = await _route(
        llm, "muéstrame los lotes comerciales en rojo"
    )
    print(f"\n[DISCRIM red-no-layer] intent={intent} reason={reason}")
    assert intent != "apply_symbology"
    assert intent in {"query_data", "search_external"}


@pytest.mark.asyncio
async def test_discriminant_select_service_vs_query_count():
    """DISCRIMINANTE: un número desnudo.

    Sin lista de servicios encontrados, un '3' suelto no es select_service (no
    hay nada que seleccionar). Aquí damos contexto de servicios encontrados y
    un mensaje '3' → debe ser select_service. (Contraparte del caso ambiguo:
    valida que el router usa el CONTEXTO de servicios, no solo el texto.)
    """
    llm = await get_real_llm_or_skip()
    found = [
        {"name": "Estaciones de Bomberos - IDIGER", "url": "https://x/FeatureServer/0"},
        {"name": "Bomberos Bogotá - MapServer", "url": "https://y/MapServer/2"},
        {"name": "Cuerpo de Bomberos Voluntarios", "url": "https://z/FeatureServer/1"},
    ]
    intent, reason, data = await _route(
        llm, "carga el 2", found_services=found
    )
    print(f"\n[DISCRIM select-service] intent={intent} reason={reason} "
          f"num={data.get('selected_service_number')}")
    assert intent == "select_service"


@pytest.mark.asyncio
async def test_discriminant_load_external_url_not_search():
    """DISCRIMINANTE: el usuario pega una URL pidiendo 'busca/carga'.

    'busca/carga' empuja a search_external, pero la presencia de una URL http(s)
    completa manda: load_external. El prompt lo dice explícitamente.
    """
    llm = await get_real_llm_or_skip()
    intent, reason, data = await _route(
        llm,
        "carga esto https://services.arcgis.com/abc/FeatureServer/0",
    )
    print(f"\n[DISCRIM load-external-url] intent={intent} reason={reason} "
          f"url={data.get('external_url')}")
    assert intent == "load_external"


# ===========================================================================
# 5) ANÁLISIS/ESTADÍSTICA/ML sobre una capa cargada  →  analyze
#     (FIX-ROUTER-ANALYZE — el motor analítico del sandbox)
# ===========================================================================
@pytest.mark.asyncio
async def test_analyze_clustering_with_active_layer():
    """'agrupa en clusters CON ESTADÍSTICA' sobre capa cargada → analyze.

    Distinto de apply_symbology (agrupar para VER colores) y de spatial_operation
    (geometría). Es análisis: corre en el sandbox y devuelve tabla/estadística.
    """
    llm = await get_real_llm_or_skip()
    intent, reason, _ = await _route(
        llm,
        "detecta grupos/clusters en estos lotes y dame el conteo por cluster",
        **_active_layer_ctx(geometry="Point"),
    )
    print(f"\n[analyze/clustering] intent={intent} reason={reason}")
    assert intent == "analyze"


@pytest.mark.asyncio
async def test_analyze_correlation_with_active_layer():
    """'correlación entre dos variables' sobre capa cargada → analyze."""
    llm = await get_real_llm_or_skip()
    intent, reason, _ = await _route(
        llm,
        "¿hay correlación entre el área y el uso de suelo de estos lotes?",
        **_active_layer_ctx(),
    )
    print(f"\n[analyze/correlation] intent={intent} reason={reason}")
    assert intent == "analyze"


@pytest.mark.asyncio
async def test_discriminant_simple_count_is_query_data_not_analyze():
    """DISCRIMINANTE: un conteo SIMPLE que la BD resuelve con SQL es query_data,
    NO analyze — aunque haya capa cargada. 'analyze' es análisis estadístico/ML."""
    llm = await get_real_llm_or_skip()
    intent, reason, _ = await _route(
        llm, "¿cuántos lotes hay en total?", **_active_layer_ctx()
    )
    print(f"\n[DISCRIM count-not-analyze] intent={intent} reason={reason}")
    assert intent in ("query_data", "follow_up")  # datos ya cargados → follow_up también válido


@pytest.mark.asyncio
async def test_discriminant_visual_cluster_is_symbology_not_analyze():
    """DISCRIMINANTE (el más fino): 'agrupa en clusters para VER en el mapa' es
    apply_symbology (cluster VISUAL), no analyze (cluster ANALÍTICO)."""
    llm = await get_real_llm_or_skip()
    intent, reason, _ = await _route(
        llm,
        "muestra estos puntos agrupados en clusters de colores en el mapa",
        **_active_layer_ctx(geometry="Point"),
    )
    print(f"\n[DISCRIM visual-cluster-symbology] intent={intent} reason={reason}")
    assert intent == "apply_symbology"
