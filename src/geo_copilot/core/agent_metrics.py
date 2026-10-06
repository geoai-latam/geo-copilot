"""AgentMetrics (Fase 6 / F6) — aprendizaje de largo plazo, cost-aware.

El bucle ReAct (F4.4) elige herramientas, pero hasta ahora cada turno arrancaba
SIN memoria de cuánto costó ni qué tan fiable fue cada herramienta en turnos
anteriores. F6 registra métricas por turno/herramienta y las realimenta como un
HINT de costo/fiabilidad en el prompt, para que el agente aprenda —dentro de la
sesión/proceso— a preferir lo barato y confiable y a ser cauto con lo que viene
fallando ("query_database falló 3/4 veces; considera una alternativa").

Es un mecanismo de aprendizaje LIGERO y honesto:
  - registra hechos (tokens, éxito, herramientas, reflexiones) — no inventa;
  - el hint solo aparece con suficiente historia (``min_turns``) — no ruido
    prematuro;
  - in-proceso por instancia del grafo, con tope (como [[EntityMemory]]); la
    persistencia durable (archivo/BD) es un follow-up.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class _ToolStat:
    calls: int = 0
    successes: int = 0

    @property
    def success_rate(self) -> float:
        return self.successes / self.calls if self.calls else 0.0


@dataclass
class AgentMetrics:
    """Métricas acumuladas de los turnos del agente (cost-aware)."""

    max_turns: int = 500
    persist_path: str | None = None
    _tools: dict[str, _ToolStat] = field(default_factory=dict)
    _turns: deque = field(default_factory=lambda: deque(maxlen=500))

    def __post_init__(self) -> None:
        if self._turns.maxlen != self.max_turns:
            self._turns = deque(self._turns, maxlen=max(1, self.max_turns))
        if self.persist_path:
            self._load()

    # -- persistencia durable (F49) — aprendizaje real entre reinicios ------
    def _load(self) -> None:
        from geo_copilot.core.json_store import load_json
        if not self.persist_path:  # solo se llama con ruta
            return
        raw = load_json(self.persist_path)
        for name, st in (raw.get("tools") or {}).items():
            self._tools[name] = _ToolStat(
                calls=int(st.get("calls", 0)), successes=int(st.get("successes", 0)))
        for t in (raw.get("turns") or []):
            if isinstance(t, dict):
                self._turns.append(t)

    def _save(self) -> None:
        if not self.persist_path:
            return
        from geo_copilot.core.json_store import save_json
        save_json(self.persist_path, {
            "tools": {n: {"calls": st.calls, "successes": st.successes}
                      for n, st in self._tools.items()},
            "turns": list(self._turns),
        })

    # -- registro -----------------------------------------------------------
    def record_tool(self, name: str, success: bool) -> None:
        if not name:
            return
        st = self._tools.setdefault(name, _ToolStat())
        st.calls += 1
        if success:
            st.successes += 1
        self._save()

    def record_turn(
        self, *, success: bool, tokens: int = 0, n_tools: int = 0, reflections: int = 0
    ) -> None:
        self._turns.append({
            "success": bool(success),
            "tokens": int(tokens or 0),
            "n_tools": int(n_tools or 0),
            "reflections": int(reflections or 0),
        })
        self._save()

    # -- agregados ----------------------------------------------------------
    def tool_stats(self) -> dict[str, dict]:
        return {
            n: {"calls": st.calls, "successes": st.successes,
                "success_rate": round(st.success_rate, 3)}
            for n, st in self._tools.items()
        }

    def session_summary(self) -> dict:
        turns = list(self._turns)
        n = len(turns)
        if not n:
            return {"turns": 0, "total_tokens": 0, "avg_tokens": 0, "success_rate": 0.0}
        total_tokens = sum(t["tokens"] for t in turns)
        successes = sum(1 for t in turns if t["success"])
        return {
            "turns": n,
            "total_tokens": total_tokens,
            "avg_tokens": round(total_tokens / n),
            "success_rate": round(successes / n, 3),
        }

    def cost_hint(self, *, min_turns: int = 2, min_tool_calls: int = 2,
                  unreliable_below: float = 0.6) -> str:
        """Hint corto de costo/fiabilidad para el prompt. ``""`` si hay poca
        historia (no mete ruido prematuro)."""
        s = self.session_summary()
        if s["turns"] < min_turns:
            return ""
        parts = [
            f"HISTÓRICO DE SESIÓN: {s['turns']} turnos, ~{s['avg_tokens']} tokens/turno "
            f"(prefiere caminos eficientes)."
        ]
        unreliable = [
            f"{n} ({st.success_rate:.0%} éxito en {st.calls})"
            for n, st in self._tools.items()
            if st.calls >= min_tool_calls and st.success_rate < unreliable_below
        ]
        if unreliable:
            parts.append(
                "Herramientas poco fiables hasta ahora: " + ", ".join(unreliable)
                + ". Considera una alternativa o sé cauto."
            )
        return " ".join(parts)
