"""Evaluador determinista del benchmark agéntico.

Recibe el dict que devuelve ``GeoAgentGraph.process()`` y la sección
``expected`` de una tarea, y produce un veredicto por-check + pass global.
Todo es código determinista (regex/conteos) — el JUICIO ya lo hizo el agente;
aquí solo verificamos hechos observables del resultado.

Diseño ENGINE-AGNOSTIC: las trayectorias se derivan tanto de
``agent_messages`` (grafo cableado) como de ``reasoning_trace`` (bucle ReAct),
mapeando herramientas → agentes. Así el mismo YAML puntúa `off` y `hybrid`
sin sesgar el comparativo (A2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Herramienta del bucle ReAct → agente equivalente del grafo cableado.
_TOOL_TO_AGENT = {
    "query_database": "gis_agent",
    "spatial_operation": "python_agent",
    "analyze_layer": "python_agent",
    "apply_symbology": "symbology_agent",
    "search_external": "data_agent",
    "select_service": "data_agent",
    "load_external": "data_agent",
}


@dataclass
class TaskVerdict:
    task_id: str
    ok: bool
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    latency_s: float | None = None
    error: str | None = None
    # F0 (gate V4): qué respondió y cómo. Sin esto, una tarea que PASA no se
    # puede revisar — y el bench de F0 mostró un conteo inventado ("2.409.199
    # hospitales") que el regex dejó pasar por venir escrito en palabras.
    answer: str | None = None
    intent: str | None = None
    sql: str | None = None
    # F3: qué herramientas llamó (y en qué orden): sin esto, «agotó 12 pasos» no se puede diagnosticar
    herramientas: list[str] = field(default_factory=list)
    # eficiencia (comparar modelos): llamadas al LLM y tokens de la pregunta, de observabilidad.registrar_llm
    llm: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.task_id,
            "ok": self.ok,
            "checks": self.checks,
            "reasons": self.reasons,
            "latency_s": self.latency_s,
            "error": self.error,
            "answer": self.answer,
            "intent": self.intent,
            "sql": self.sql,
            "herramientas": self.herramientas,
            "llm": self.llm,
        }


def _agents_seen(result: dict) -> list[str]:
    """Secuencia de agentes que actuaron, cualquiera sea el motor."""
    seen: list[str] = []
    for msg in result.get("agent_messages") or []:
        agent = (msg or {}).get("agent")
        if agent:
            seen.append(str(agent))
    for entry in result.get("reasoning_trace") or []:
        if (entry or {}).get("kind") == "tool_call":
            tool = str(entry.get("tool") or "")
            seen.append(_TOOL_TO_AGENT.get(tool, tool))
    return seen


def _results_list(result: dict) -> list:
    data = result.get("data")
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return data["results"]
    return []


def _feature_count(result: dict) -> int:
    gj = result.get("geojson") or result.get("external_geojson") or {}
    if isinstance(gj, dict):
        return len(gj.get("features") or [])
    return 0


def _viz_type(result: dict) -> str | None:
    viz = result.get("visualization")
    if isinstance(viz, dict):
        return viz.get("type")
    if isinstance(viz, str):
        return viz
    return None


_NUMERO = re.compile(r"[-−]?\d[\d.,]*\d|\d")


def _lecturas(token: str) -> set[float]:
    """Valores posibles de una cifra escrita: «2.584» es 2584 (es) o 2.584 (en); «0,641» es 0.641.

    El agente escribe en español y a veces en inglés: ante la duda valen las dos lecturas. Una
    verdad sigue siendo exigente porque la tolerancia es pequeña frente a la distancia entre
    lecturas (×1000)."""
    t = token.replace("−", "-")
    if "." in t and "," in t:
        decimal = "." if t.rfind(".") > t.rfind(",") else ","
        miles = "," if decimal == "." else "."
        candidatos = [t.replace(miles, "").replace(decimal, ".")]
    elif "." in t or "," in t:
        sep = "." if "." in t else ","
        candidatos = [t.replace(sep, "")]
        if t.count(sep) == 1:
            candidatos.append(t.replace(sep, "."))
    else:
        candidatos = [t]
    valores = set()
    for c in candidatos:
        try:
            valores.add(float(c))
        except ValueError:
            continue
    return valores


def cifras(texto: str) -> set[float]:
    """Todas las lecturas de todas las cifras de un texto."""
    return set().union(*(_lecturas(m) for m in _NUMERO.findall(texto or ""))) if texto else set()


def _coincide(valores: set[float], verdad) -> bool:
    """``verdad``: un número (tolerancia relativa 0,5 %), {valor, tol} (relativa), {rango: [a, b]} o
    {alguno: [v1, v2]} (dos lecturas igual de defendibles: p. ej. por atributo o por el límite real)."""
    if isinstance(verdad, dict) and "alguno" in verdad:
        return any(_coincide(valores, v) for v in verdad["alguno"])
    if isinstance(verdad, dict) and "rango" in verdad:
        lo, hi = (float(x) for x in verdad["rango"])
        return any(lo <= v <= hi for v in valores)
    valor = float(verdad["valor"] if isinstance(verdad, dict) else verdad)
    tol = float(verdad.get("tol", 0.005)) if isinstance(verdad, dict) else 0.005
    return any(abs(v - valor) <= abs(valor) * tol for v in valores)


def _herramientas(result: dict) -> list[str]:
    """Herramientas que llamó el bucle ReAct (incluidas las de los MCP: «servidor__tool»)."""
    return [str(e.get("tool")) for e in (result.get("decision_trace") or result.get("reasoning_trace") or [])
            if isinstance(e, dict) and e.get("kind") == "tool_call" and e.get("tool")]


def _check_result_kind(result: dict, kind: str) -> tuple[bool, str]:
    n_feats = _feature_count(result)
    viz = _viz_type(result)
    if kind == "geojson":
        return (n_feats >= 1, f"features={n_feats}")
    if kind == "table_or_chart":
        ok = viz in ("table", "chart") and len(_results_list(result)) >= 1
        return (ok, f"visualization={viz!r}, results={len(_results_list(result))}")
    if kind == "text_only":
        ok = n_feats == 0 and bool((result.get("message") or "").strip())
        return (ok, f"features={n_feats}, message={'sí' if result.get('message') else 'no'}")
    return (False, f"result_kind desconocido en el YAML: {kind!r}")


def evaluate(task: dict, result: dict, latency_s: float | None = None) -> TaskVerdict:
    """Aplica todos los checks de ``task['expected']`` sobre ``result``."""
    expected = task.get("expected") or {}
    v = TaskVerdict(task_id=str(task.get("id")), ok=True, latency_s=latency_s)

    def _record(name: str, passed: bool, why: str) -> None:
        v.checks[name] = bool(passed)
        if not passed:
            v.ok = False
            v.reasons.append(f"{name}: {why}")

    if "success" in expected:
        want = bool(expected["success"])
        got = bool(result.get("success"))
        # ``partial_ok`` relaja: un plan parcialmente exitoso cuenta como éxito.
        if not got and want and expected.get("partial_ok") and result.get("partial"):
            got = True
        _record("success", got == want, f"esperaba success={want}, fue {result.get('success')}")

    if expected.get("intent_any"):
        allowed = [str(x) for x in expected["intent_any"]]
        got_intent = result.get("intent")
        # El bucle ReAct no emite intent — si la tarea lo permite con
        # ``intent_optional_react`` y hay trace ReAct, el check pasa vacío.
        if got_intent is None and expected.get("intent_optional_react") and (
            result.get("reasoning_trace")
        ):
            _record("intent_any", True, "")
        else:
            _record(
                "intent_any", str(got_intent) in allowed,
                f"intent={got_intent!r} no está en {allowed}",
            )

    seen = _agents_seen(result)
    if expected.get("trajectory_contains"):
        for agent in expected["trajectory_contains"]:
            _record(
                f"trajectory_contains:{agent}", str(agent) in seen,
                f"{agent} no actuó (actuaron: {seen})",
            )
    if expected.get("trajectory_absent"):
        for agent in expected["trajectory_absent"]:
            _record(
                f"trajectory_absent:{agent}", str(agent) not in seen,
                f"{agent} actuó y no debía (actuaron: {seen})",
            )

    if expected.get("sql_regex"):
        sql = result.get("sql") or ""
        ok = bool(re.search(str(expected["sql_regex"]), sql, re.IGNORECASE | re.DOTALL))
        _record("sql_regex", ok, f"SQL no matchea {expected['sql_regex']!r}: {sql[:150]!r}")

    if expected.get("no_sql"):
        _record("no_sql", not result.get("sql"), f"se ejecutó SQL: {str(result.get('sql'))[:120]!r}")

    if "min_results" in expected:
        n = max(len(_results_list(result)), _feature_count(result))
        _record("min_results", n >= int(expected["min_results"]),
                f"esperaba >= {expected['min_results']}, hubo {n}")

    if "max_results" in expected:
        n = max(len(_results_list(result)), _feature_count(result))
        _record("max_results", n <= int(expected["max_results"]),
                f"esperaba <= {expected['max_results']}, hubo {n}")

    if expected.get("result_kind"):
        ok, why = _check_result_kind(result, str(expected["result_kind"]))
        _record("result_kind", ok, why)

    if expected.get("visualization_type"):
        got = _viz_type(result)
        _record("visualization_type", got == expected["visualization_type"],
                f"esperaba {expected['visualization_type']!r}, fue {got!r}")

    if expected.get("has_symbology"):
        symb = result.get("symbology")
        _record("has_symbology", bool(symb), "no se aplicó simbología")

    if expected.get("symbology_type_not"):
        symb = result.get("symbology") or {}
        got = symb.get("symbology_type") if isinstance(symb, dict) else None
        _record("symbology_type_not", got != expected["symbology_type_not"],
                f"simbología prohibida {expected['symbology_type_not']!r} fue elegida")

    if "min_services" in expected:
        n = len(result.get("found_services") or [])
        _record("min_services", n >= int(expected["min_services"]),
                f"esperaba >= {expected['min_services']} servicios, hubo {n}")

    if expected.get("answer_regex"):
        msg = result.get("message") or ""
        ok = bool(re.search(str(expected["answer_regex"]), msg, re.IGNORECASE | re.DOTALL))
        _record("answer_regex", ok, f"mensaje no matchea: {msg[:200]!r}")

    if expected.get("answer_not_regex"):
        msg = result.get("message") or ""
        ok = not re.search(str(expected["answer_not_regex"]), msg, re.IGNORECASE | re.DOTALL)
        _record("answer_not_regex", ok, f"mensaje matchea lo prohibido: {msg[:200]!r}")

    # Verdades (F0 del plan de calidad): la cifra que el usuario lee, contra la medida en el origen.
    if expected.get("answer_numbers"):
        valores = cifras(result.get("message") or "")
        for verdad in expected["answer_numbers"]:
            _record(f"answer_numbers:{verdad}", _coincide(valores, verdad),
                    f"la respuesta no dice {verdad}: {(result.get('message') or '')[:200]!r}")

    if expected.get("answer_numbers_not"):
        valores = cifras(result.get("message") or "")
        for falsa in expected["answer_numbers_not"]:
            _record(f"answer_numbers_not:{falsa}", not _coincide(valores, falsa),
                    f"la respuesta da la cifra FALSA {falsa}: {(result.get('message') or '')[:200]!r}")

    if expected.get("tool_regex"):
        usadas = _herramientas(result)
        patron = str(expected["tool_regex"])
        _record("tool_regex", any(re.search(patron, t) for t in usadas),
                f"ninguna herramienta matchea {patron!r} (usó: {usadas})")

    if expected.get("has_imagery"):
        _record("has_imagery", bool(result.get("external_imagery")), "no se entregó imagen al mapa")

    if not v.checks:
        v.ok = False
        v.reasons.append("la tarea no declara ningún check en expected")
    return v


def aggregate(verdicts: list[TaskVerdict]) -> dict:
    """Score global + desglose por categoría (las tareas llevan ``category``)."""
    n = len(verdicts)
    passed = sum(1 for x in verdicts if x.ok)
    return {
        "n_tasks": n,
        "n_passed": passed,
        "score": round(passed / n, 4) if n else 0.0,
    }
