"""Validación CRÍTICA con LLM REAL del juicio de SIMBOLOGÍA (@pytest.mark.llm).

Superficie: ``SymbologyAgent.analyze_data(geojson, query)`` → el LLM diseña la
simbología eligiendo ``symbology_type`` + ``classification_field`` +
``label_field`` + ``classification_method`` viendo el schema (campos, tipos,
stats, samples) y la query del usuario.

Lo que validamos NO es texto exacto sino la DIRECCIÓN/estructura del juicio:
- polígono con campo numérico "poblacion" + "muestra poblacion" → graduated_colors
  con classification_field=poblacion.
- puntos con categórico "tipo" + "colorea por tipo" → unique_values con
  classification_field=tipo.
- DISCRIMINANTE: un geojson con VARIOS campos donde una heurística ingenua
  (elegir el primer campo, o el primero numérico/categórico) escogería el
  equivocado, pero el LLM debe elegir EXACTAMENTE el que pide la query.

Construimos ``SymbologyAgent`` con ``agent_hub=None`` para aislar el juicio del
LLM: sin hub, ``_validate_design_via_a2a`` devuelve el design sin tocar, así
asertamos sobre lo que el LLM decidió, no sobre un override A2A.

Imprimimos el reasoning real del modelo en cada test y JUZGAMOS si la decisión
es sólida — no nos conformamos con verde.
"""

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm


def _agent(llm):
    """SymbologyAgent con el LLM real y SIN hub A2A (aísla el juicio del LLM)."""
    from geo_copilot.agents.symbology_agent.agent import SymbologyAgent

    return SymbologyAgent(llm_client=llm, agent_hub=None)


def _polygon_feature(props: dict, x: float = 0.0) -> dict:
    """Polígono pequeño (cuadrado) con propiedades dadas."""
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [x, 0.0], [x, 1.0], [x + 1.0, 1.0], [x + 1.0, 0.0], [x, 0.0],
            ]],
        },
        "properties": props,
    }


def _point_feature(props: dict, x: float = 0.0) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [x, 0.0]},
        "properties": props,
    }


def _fc(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _design(analysis: dict) -> dict:
    return analysis.get("design", {}) or {}


# ===========================================================================
# CASO CLARO 1 — polígonos con "poblacion" numérico + "muestra población"
#                → coropleto (graduated_colors) sobre poblacion.
# ===========================================================================
@pytest.mark.asyncio
async def test_llm_population_polygons_to_graduated_colors():
    llm = await get_real_llm_or_skip()
    agent = _agent(llm)
    feats = [
        _polygon_feature({"nombre": "Suba", "poblacion": 1_200_000}, x=0),
        _polygon_feature({"nombre": "Kennedy", "poblacion": 980_000}, x=2),
        _polygon_feature({"nombre": "Chapinero", "poblacion": 170_000}, x=4),
        _polygon_feature({"nombre": "Teusaquillo", "poblacion": 140_000}, x=6),
        _polygon_feature({"nombre": "La Candelaria", "poblacion": 24_000}, x=8),
    ]
    analysis = await agent.analyze_data(_fc(feats), query="muestra la población por localidad")
    d = _design(analysis)
    print(f"\n[POP polygons] type={d.get('symbology_type')} field={d.get('classification_field')} "
          f"method={d.get('classification_method')} scheme={d.get('color_scheme')} "
          f"label={d.get('label_field')}\n  reason={d.get('reasoning')}")

    # Población numérica continua sobre polígonos → coropleto graduado.
    assert d.get("symbology_type") == "graduated_colors"
    assert d.get("classification_field") == "poblacion"
    # label debe ser el nombre legible, NO la población (no se etiqueta por el valor clasificado).
    assert d.get("label_field") in ("nombre", None)


# ===========================================================================
# CASO CLARO 2 — puntos con "tipo" categórico + "colorea por tipo"
#                → unique_values sobre tipo.
# ===========================================================================
@pytest.mark.asyncio
async def test_llm_categorical_points_to_unique_values():
    llm = await get_real_llm_or_skip()
    agent = _agent(llm)
    tipos = ["restaurante", "farmacia", "restaurante", "banco", "farmacia",
             "restaurante", "banco", "farmacia"]
    feats = [
        _point_feature({"nombre": f"POI {i}", "tipo": t}, x=float(i))
        for i, t in enumerate(tipos)
    ]
    analysis = await agent.analyze_data(_fc(feats), query="colorea los puntos por tipo de establecimiento")
    d = _design(analysis)
    print(f"\n[CAT points] type={d.get('symbology_type')} field={d.get('classification_field')} "
          f"scheme={d.get('color_scheme')} label={d.get('label_field')}\n  reason={d.get('reasoning')}")

    # Categórico sin orden con pocas categorías → unique_values por tipo.
    assert d.get("symbology_type") == "unique_values"
    assert d.get("classification_field") == "tipo"


# ===========================================================================
# CASO CLARO 3 — "mapa de calor de delitos" + muchos puntos (1500) sin
#                atributo cuantitativo → heatmap, classification_field=null.
#
# Usamos 1500 puntos (>1000) para que AMBAS condiciones del prompt apliquen
# y el caso sea ESTABLE. La variante con pocos puntos (donde el prompt es
# ambiguo) se documenta abajo como HALLAZGO en
# test_llm_heatmap_explicit_intent_ignored_below_1000.
# ===========================================================================
@pytest.mark.asyncio
async def test_llm_heatmap_intent_to_heatmap():
    llm = await get_real_llm_or_skip()
    agent = _agent(llm)
    feats = [
        _point_feature({"id": f"caso-{i}"}, x=float(i) * 0.001)
        for i in range(1500)
    ]
    analysis = await agent.analyze_data(_fc(feats), query="muéstrame un mapa de calor de la densidad de delitos")
    d = _design(analysis)
    print(f"\n[HEATMAP n=1500] type={d.get('symbology_type')} field={d.get('classification_field')} "
          f"scheme={d.get('color_scheme')}\n  reason={d.get('reasoning')}")

    # Intent explícito de densidad + >1000 puntos → heatmap; no clasifica por campo.
    assert d.get("symbology_type") == "heatmap"
    assert d.get("classification_field") is None
    # "id" es identificador único — NUNCA debe ser classification_field.
    assert d.get("classification_field") != "id"


# ===========================================================================
# HALLAZGO (bug de prompt) — intent EXPLÍCITO de heatmap ignorado con pocos pts.
#
# El prompt (_DESIGN_SYMBOLOGY_SYSTEM) lista DOS condiciones para heatmap como
# bullets (OR implícito):
#   * El usuario menciona explícitamente "densidad"/"calor"/"mapa de calor".
#   * Hay >1000 puntos Point/MultiPoint sin atributo cuantitativo claro.
# La primera condición DEBERÍA bastar por sí sola: si el usuario PIDE un mapa
# de calor, esa es su intención declarada. Pero el modelo trata el ">1000" como
# un UMBRAL DURO universal: con 30 puntos (y a veces con 200) rechaza el heatmap
# pese al intent explícito ("Aunque el usuario pide un mapa de calor...
# insuficientes para un heatmap efectivo que requiere >1000 puntos") y degrada a
# single_symbol. Probado reproducible: n=30 → single_symbol estable;
# n=200 → INESTABLE (a veces single_symbol, a veces heatmap entre runs);
# n=1500 → heatmap estable. Es decir, con pocos puntos el intent explícito del
# usuario se descarta sin avisar, y en la zona media el juicio es no-determinista.
#
# Juicio: el cartógrafo real tiene razón en que 30 puntos no dan kernel density
# útil — PERO el prompt no le dijo eso; le dijo que el intent explícito basta.
# La redacción ambigua hace que el modelo invente un gate y descarte el intent
# del usuario sin avisarlo como tal. Es un bug de PROMPT, no del modelo: el
# prompt debería (a) decir que el intent explícito manda y degradar a puntos
# SOLO si la densidad espacial es insuficiente, dejándolo al juez A2A de
# densidad (que ya existe: evaluate_visualization_fit con extent_km2), o
# (b) declarar explícitamente el mínimo de puntos para heatmap (eliminando la
# ambigüedad que produce el no-determinismo en la zona media).
#
# BUG DE PROMPT ARREGLADO: el bloque heatmap ahora dice que el INTENT EXPLÍCITO
# MANDA (heatmap aunque haya pocos puntos; la densidad la juzga el A2A/insights).
# El LLM ya honra el intent con n=30. (Era xfail.)
# ===========================================================================
@pytest.mark.asyncio
async def test_llm_heatmap_explicit_intent_honored_few_points():
    llm = await get_real_llm_or_skip()
    agent = _agent(llm)
    # 30 puntos densos, intent EXPLÍCITO de densidad/calor (extremo estable).
    feats = [
        _point_feature({"id": f"caso-{i}"}, x=float(i) * 0.001)
        for i in range(30)
    ]
    analysis = await agent.analyze_data(
        _fc(feats), query="muéstrame un mapa de calor de la densidad de delitos"
    )
    d = _design(analysis)
    print(f"\n[HEATMAP n=30 (HALLAZGO)] type={d.get('symbology_type')} "
          f"field={d.get('classification_field')}\n  reason={d.get('reasoning')}")

    # El intent explícito manda → heatmap (aunque sean pocos puntos; la densidad
    # real la juzga el insights/A2A con el extent).
    assert d.get("symbology_type") == "heatmap"


# ===========================================================================
# DISCRIMINANTE — varios campos numéricos. La query pide UNO específico
# ("densidad"), pero el PRIMER campo del schema y el de mayor varianza son
# OTROS ("poblacion", "area_km2"). Una heurística ingenua (primer numérico /
# mayor std / primer campo) elegiría mal; el LLM debe leer la query y elegir
# 'densidad_hab_km2'.
# ===========================================================================
@pytest.mark.asyncio
async def test_llm_discriminant_picks_field_from_query_not_first():
    llm = await get_real_llm_or_skip()
    agent = _agent(llm)
    # poblacion: mayor magnitud/varianza (señuelo para "mayor std").
    # area_km2: primer campo alfabético-ish / numérico (señuelo "primero").
    # densidad_hab_km2: lo que pide la query, magnitud intermedia.
    rows = [
        {"nombre": "A", "area_km2": 100.0, "poblacion": 1_200_000, "densidad_hab_km2": 12000.0},
        {"nombre": "B", "area_km2": 320.0, "poblacion": 980_000, "densidad_hab_km2": 3062.0},
        {"nombre": "C", "area_km2": 55.0, "poblacion": 170_000, "densidad_hab_km2": 3090.0},
        {"nombre": "D", "area_km2": 410.0, "poblacion": 140_000, "densidad_hab_km2": 341.0},
        {"nombre": "E", "area_km2": 7.0, "poblacion": 24_000, "densidad_hab_km2": 3428.0},
    ]
    feats = [_polygon_feature(r, x=float(i) * 2) for i, r in enumerate(rows)]
    analysis = await agent.analyze_data(
        _fc(feats),
        query="colorea las localidades según su densidad poblacional (habitantes por km2)",
    )
    d = _design(analysis)
    print(f"\n[DISCRIMINANT field] type={d.get('symbology_type')} field={d.get('classification_field')} "
          f"method={d.get('classification_method')} scheme={d.get('color_scheme')} "
          f"label={d.get('label_field')}\n  reason={d.get('reasoning')}")

    # La query pide DENSIDAD explícitamente → debe clasificar por ese campo,
    # no por poblacion (mayor varianza) ni area_km2 (otro numérico).
    assert d.get("symbology_type") == "graduated_colors"
    assert d.get("classification_field") == "densidad_hab_km2"
    assert d.get("label_field") in ("nombre", None)


# ===========================================================================
# DISCRIMINANTE 2 — el campo a clasificar es CATEGÓRICO pero NO el primero,
# y hay un identificador único como señuelo. Query: "color por estrato".
# El schema tiene 'codigo_predial' (identificador, primer campo) y 'estrato'.
# Una heurística "primer string" tomaría codigo_predial (catástrofe: un color
# por feature). El LLM debe elegir 'estrato'.
# ===========================================================================
@pytest.mark.asyncio
async def test_llm_discriminant_categorical_not_identifier():
    llm = await get_real_llm_or_skip()
    agent = _agent(llm)
    estratos = ["3", "5", "2", "3", "6", "2", "4", "3"]
    feats = [
        _polygon_feature(
            {"codigo_predial": f"25754-{1000 + i}-AB", "estrato": e, "barrio": "Centro"},
            x=float(i) * 2,
        )
        for i, e in enumerate(estratos)
    ]
    analysis = await agent.analyze_data(
        _fc(feats), query="píntame los predios con un color distinto por estrato socioeconómico"
    )
    d = _design(analysis)
    print(f"\n[DISCRIMINANT categorical] type={d.get('symbology_type')} "
          f"field={d.get('classification_field')} label={d.get('label_field')}\n  reason={d.get('reasoning')}")

    # color por estrato → unique_values sobre 'estrato'.
    assert d.get("classification_field") == "estrato"
    # codigo_predial es un identificador único: jamás debe ser el campo de clasificación.
    assert d.get("classification_field") != "codigo_predial"
    # ni el label debe ser el id largo (señuelo): debería preferir barrio o estrato, o null.
    assert d.get("label_field") != "codigo_predial"


# ===========================================================================
# F1 (regresión del núcleo, E0.1): "colorea los lotes de verde" sobre lotes con
# un campo categórico (lotdispers = N/D). Pedir UN color no es pedir una
# clasificación: el LLM a veces respondía unique_values por lotdispers en tonos
# verdes (2 de 5 corridas reales) y el usuario veía dos verdes sin saber por qué.
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize("pedido", [
    "colorea los lotes de verde",
    "ponlos en rojo",
    "píntalos de azul",
])
async def test_llm_un_color_pedido_es_single_symbol(pedido):
    llm = await get_real_llm_or_skip()
    agent = _agent(llm)
    feats = [
        _polygon_feature({"lotcodigo": f"004512{i:04d}", "lotdispers": "N" if i % 5 else "D",
                          "manzcodigo": "004512"}, x=float(i) * 2)
        for i in range(20)
    ]
    analysis = await agent.analyze_data(_fc(feats), query=pedido)
    d = _design(analysis)
    print(f"\n[{pedido}] type={d.get('symbology_type')} field={d.get('classification_field')} "
          f"scheme={d.get('color_scheme')}\n  reason={d.get('reasoning')}")
    assert d.get("symbology_type") == "single_symbol"
