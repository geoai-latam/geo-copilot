"""Dependencias entre pasos de un plan (F3.1) — tracking tipo DAG.

El gap que cierra: el ejecutor multi-paso era estrictamente lineal y un fallo
en CUALQUIER paso abortaba TODO el plan. Con dependencias explícitas:

  - un paso solo se ejecuta si sus dependencias produjeron output;
  - un fallo SALTA (honestamente) solo los pasos que dependen de él
    (transitivamente), no los independientes;
  - ramas independientes sobreviven al fallo de una hermana.

Todo se deriva de ``step_results`` (ya es un canal del GraphState) — no hace
falta estado nuevo que LangGraph pudiera descartar.

Default conservador: si un paso NO declara ``depends_on``, se asume que depende
del paso inmediatamente anterior (cadena lineal). Así, sin info de dependencias,
el comportamiento es el de antes (un fallo propaga hacia abajo) pero con reporte
honesto de "saltado" en vez de un abort abrupto. El planner puede declarar
``depends_on: []`` para marcar un paso como independiente.
"""

from __future__ import annotations

from typing import Any


def _get(step: Any, key: str, default=None):
    """Lee un atributo de un step que puede ser dict o dataclass PlanStep."""
    if isinstance(step, dict):
        return step.get(key, default)
    return getattr(step, key, default)


def step_id_of(step: Any, idx: int) -> str:
    return _get(step, "step_id") or f"step_{idx + 1}"


def step_dependencies(step: Any, idx: int, plan: list) -> list[str]:
    """IDs de los pasos de los que ``step`` depende.

    - ``depends_on`` explícito (lista) → se usa tal cual (incluye ``[]`` =
      independiente).
    - ``depends_on`` ausente/None → default lineal: el paso anterior (o ``[]``
      si es el primero).
    """
    deps = _get(step, "depends_on")
    if deps is None:
        if idx <= 0:
            return []
        return [step_id_of(plan[idx - 1], idx - 1)]
    # Normalizar a lista de strings no vacíos.
    return [str(d) for d in deps if d]


def satisfied_step_ids(step_results: list[dict] | None) -> set[str]:
    """IDs de pasos cuyo output SÍ está disponible (terminaron con éxito).

    Una dependencia está SATISFECHA solo si su paso quedó registrado con
    ``success=True``. Todo lo demás (falló, se saltó, aún no corrió, no existe)
    cuenta como NO satisfecho — ver ``blocked_dependencies``.
    """
    return {
        r["step_id"]
        for r in step_results or []
        if r.get("success") and r.get("step_id")
    }


def unavailable_step_ids(step_results: list[dict] | None) -> set[str]:
    """IDs de pasos cuyo output NO está disponible (fallaron o se saltaron).

    ``pending_selection`` se excluye: pausa el plan, no es un fallo de output.
    (Se conserva para telemetría/introspección; el gating real usa
    ``satisfied_step_ids`` vía ``blocked_dependencies``.)
    """
    out: set[str] = set()
    for r in step_results or []:
        if not r.get("success") and not r.get("pending_selection"):
            sid = r.get("step_id")
            if sid:
                out.add(sid)
    return out


def blocked_dependencies(
    step: Any, idx: int, plan: list, step_results: list[dict] | None
) -> list[str]:
    """Dependencias de ``step`` que NO están satisfechas (vacío = ejecutable).

    Una dependencia bloquea si NO hay un paso con ese ``step_id`` registrado con
    ``success=True``. Esto cubre, con una sola regla, TODOS los casos de
    ``depends_on`` malformado que de otro modo correrían sobre datos obsoletos
    (revisión adversarial F3.1):
      - ID inexistente → nunca se satisface → bloqueado (se salta).
      - referencia hacia ADELANTE (a un paso posterior) → aún no corrió cuando se
        evalúa → bloqueado (el bucle es en-orden; no reordenamos).
      - auto-dependencia / ciclo → ninguno se satisface → ambos se saltan (sin
        deadlock: ``current_step_index`` igual avanza).
      - dependencia fallida/saltada → ``success=False`` → bloqueado (transitivo).
    El planner debe declarar dependencias solo a pasos ANTERIORES; un forward/
    ciclo degrada honestamente a "saltado", no a un resultado silencioso erróneo.
    """
    satisfied = satisfied_step_ids(step_results)
    return [d for d in step_dependencies(step, idx, plan) if d not in satisfied]
