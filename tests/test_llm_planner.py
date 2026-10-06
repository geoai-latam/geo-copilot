"""Validación CRÍTICA con LLM REAL del PLANNER (@pytest.mark.llm).

SUPERFICIE: ``PlannerAgent.generate_plan`` (descomposición multi-paso +
``depends_on``) y ``PlannerAgent.is_complex_query``.

Estos tests llaman a un LLM de verdad para verificar que el PROMPT del
planificador provoca la decisión correcta en un modelo real:

  - Una cadena ("busca X, hazle buffer, píntalo") se descompone en pasos
    ENCADENADOS (cada transformación depende del paso que produce sus datos).
  - Una query simple ("busca bomberos") NO es multi-paso.
  - DISCRIMINANTE: "carga bomberos en rojo Y hospitales en azul" son DOS
    ramas INDEPENDIENTES — los dos ``search_external`` deben tener
    ``depends_on == []`` (raíces de rama), no una cadena lineal donde el
    segundo search "depende" del color del primero. Una heurística que asume
    cadena lineal (depende-del-anterior) lo haría MAL aquí.

Las aserciones son sobre la DIRECCIÓN/ESTRUCTURA del juicio (no texto exacto):
cantidad de pasos, qué pasos son raíces de dependencia, y el grafo de
dependencias resultante. Imprimimos el reasoning real del modelo para validarlo
críticamente.

Correr:
    pytest -p no:cacheprovider -o addopts="" -m llm -s -q tests/test_llm_planner.py
"""

import pytest

from geo_copilot.orchestrator.planner import PlannerAgent, PlanStep
from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm


# ===========================================================================
# Helpers de estructura del plan
# ===========================================================================
def _print_plan(tag: str, plan):
    print(f"\n[PLANNER {tag}] is_multi_step={plan.is_multi_step} "
          f"reasoning={plan.reasoning!r}")
    for s in plan.steps:
        print(f"   - {s.step_id}: action_type={s.action_type!r} "
              f"depends_on={s.depends_on!r} | {s.query_fragment!r}")


def _effective_deps(step: PlanStep, prev_id: str | None) -> list[str]:
    """Dependencias EFECTIVAS según la semántica del planner.

    ``None`` (omitido) = cadena lineal → depende del paso anterior.
    ``[]`` = raíz de rama (independiente).
    Lista no vacía = dependencias declaradas explícitas.
    """
    if step.depends_on is None:
        return [prev_id] if prev_id else []
    return list(step.depends_on)


def _is_root(step: PlanStep, prev_id: str | None) -> bool:
    """¿Es este paso una RAÍZ de dependencia (no usa output de uno anterior)?"""
    return len(_effective_deps(step, prev_id)) == 0


def _search_like(step: PlanStep) -> bool:
    """¿El paso OBTIENE datos (raíz natural de un pipeline)?"""
    return step.action_type in ("search_external", "select_service", "query_database")


# ===========================================================================
# CASO CLARO 1 — cadena de 3 pasos encadenados (buscar → buffer → color)
# ===========================================================================
@pytest.mark.asyncio
async def test_planner_three_step_chain_has_linear_dependencies():
    llm = await get_real_llm_or_skip()
    plan = await PlannerAgent(llm_client=llm).generate_plan(
        "busca bomberos y hazles buffer 500m y ponlos en rojo", context={})
    _print_plan("3-step-chain", plan)

    assert plan.is_multi_step is True
    # 3 operaciones distintas: obtener datos, buffer, simbología.
    assert len(plan.steps) == 3, f"esperaba 3 pasos, hubo {len(plan.steps)}"

    s1, s2, s3 = plan.steps
    # Paso 1 obtiene datos (bomberos NO está en la BD → search_external).
    assert _search_like(s1)
    assert s1.action_type == "search_external"
    # Buffer = operación espacial; color = simbología.
    assert s2.action_type == "spatial_operation"
    assert s3.action_type == "symbology"

    # ESTRUCTURA DE DEPENDENCIAS: el primer paso es raíz; buffer depende del
    # search; color depende del buffer (cadena real, no ramas).
    assert _is_root(s1, None), "el search inicial debe ser raíz (sin deps)"
    assert s1.step_id in _effective_deps(s2, s1.step_id), \
        "el buffer debe depender del paso que trae los datos"
    assert s2.step_id in _effective_deps(s3, s2.step_id), \
        "la simbología debe depender del buffer (cadena)"


# ===========================================================================
# CASO CLARO 2 — query simple: NO multi-paso / 1 paso
# ===========================================================================
@pytest.mark.asyncio
async def test_planner_simple_query_is_not_complex():
    llm = await get_real_llm_or_skip()
    agent = PlannerAgent(llm_client=llm)

    complex_flag = await agent.is_complex_query("busca bomberos")
    print(f"\n[PLANNER simple/is_complex] is_complex_query={complex_flag}")
    # Una sola operación → NO multi-paso.
    assert complex_flag is False

    plan = await agent.generate_plan("busca bomberos", context={})
    _print_plan("simple", plan)
    # Aunque el planner no decide complejidad, una query atómica → 1 paso.
    assert len(plan.steps) == 1
    assert plan.is_multi_step is False


@pytest.mark.asyncio
async def test_is_complex_query_detects_multistep():
    """Dirección del juicio de complejidad en casos claros (ambos lados)."""
    llm = await get_real_llm_or_skip()
    agent = PlannerAgent(llm_client=llm)

    multi = await agent.is_complex_query(
        "carga hospitales y luego calcula el área de cobertura")
    simple = await agent.is_complex_query("muéstrame los parques")
    print(f"\n[PLANNER is_complex] multi(carga+área)={multi}  "
          f"simple(parques)={simple}")
    assert multi is True   # cargar + operación espacial = secuencia
    assert simple is False  # una sola operación


# ===========================================================================
# DISCRIMINANTE — dos ramas INDEPENDIENTES, no una cadena lineal
# ===========================================================================
@pytest.mark.asyncio
async def test_planner_two_independent_branches_discriminant():
    """DISCRIMINANTE (validación crítica del depends_on).

    "carga bomberos en rojo Y hospitales en azul" = DOS pipelines paralelos:
        (search bomberos → color rojo)   y   (search hospitales → color azul).

    Una heurística "cada paso depende del anterior" produciría una CADENA
    lineal donde el search de hospitales colgaría del color de bomberos —
    erróneo: las ramas son independientes. El planner debe marcar AMBOS
    search como raíces (depends_on == []), de modo que si una rama falla la
    otra sobrevive.

    Aserción clave: el segundo bloque de "obtener datos" NO debe depender
    (ni transitivamente) del primer bloque.
    """
    llm = await get_real_llm_or_skip()
    plan = await PlannerAgent(llm_client=llm).generate_plan(
        "carga bomberos en rojo y hospitales en azul", context={})
    _print_plan("two-branches", plan)

    assert plan.is_multi_step is True
    # Esperamos 4 pasos: 2 búsquedas + 2 simbologías (al menos 2 búsquedas).
    search_steps = [s for s in plan.steps if _search_like(s)]
    assert len(search_steps) >= 2, (
        "deben existir dos pasos de obtención de datos (bomberos y hospitales), "
        f"hubo {len(search_steps)}: {[s.action_type for s in plan.steps]}"
    )

    # CLAVE DEL DISCRIMINANTE: cada paso de obtención de datos es RAÍZ — no
    # cuelga del paso anterior (que sería el color de la otra rama). Con la
    # semántica del planner, eso significa depends_on == [] EXPLÍCITO (no None,
    # porque None heredaría al anterior y crearía la cadena lineal incorrecta).
    prev_id = None
    roots = []
    for s in plan.steps:
        if _search_like(s):
            assert s.depends_on == [], (
                f"el paso de datos {s.step_id} ({s.action_type}) debería ser una "
                f"raíz independiente (depends_on == []), pero fue {s.depends_on!r}. "
                f"Si fuese None heredaría al paso anterior → cadena lineal errónea."
            )
            roots.append(s.step_id)
        prev_id = s.step_id
    assert len(roots) >= 2, "deben existir >= 2 raíces independientes (dos ramas)"

    # Verificación de aislamiento de ramas: ninguna simbología debe depender,
    # transitivamente, de AMBOS searches (eso fusionaría las ramas).
    by_id = {s.step_id: s for s in plan.steps}

    def transitive_deps(step_id: str, prev_map: dict) -> set[str]:
        seen: set[str] = set()
        stack = list(_effective_deps(by_id[step_id], prev_map.get(step_id)))
        while stack:
            d = stack.pop()
            if d in seen or d not in by_id:
                continue
            seen.add(d)
            stack.extend(_effective_deps(by_id[d], prev_map.get(d)))
        return seen

    prev_map: dict[str, str | None] = {}
    prev = None
    for s in plan.steps:
        prev_map[s.step_id] = prev
        prev = s.step_id

    for s in plan.steps:
        if s.action_type == "symbology":
            tdeps = transitive_deps(s.step_id, prev_map)
            searches_in = [r for r in roots if r in tdeps]
            assert len(searches_in) <= 1, (
                f"la simbología {s.step_id} depende transitivamente de varias "
                f"búsquedas {searches_in} → las ramas se fusionaron (incorrecto)."
            )
