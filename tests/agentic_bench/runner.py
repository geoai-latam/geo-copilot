"""Runner del benchmark agéntico (A1) — standalone, sin pytest.

Ejecuta las tareas de ``tasks.yaml`` contra el grafo REAL (LLM real + BD real
+ sandbox si la plataforma lo permite) y emite un score JSON versionable.

Uso (dentro del contenedor app, recomendado — sandbox POSIX + BD interna):
    docker cp tests/agentic_bench geo_copilot_app:/app/agentic_bench
    docker exec geo_copilot_app python /app/agentic_bench/runner.py \
        --policy off --out /app/agentic_bench/results

Uso (host, subset sin sandbox):
    python tests/agentic_bench/runner.py --category simple,aggregation

Flags:
    --policy off|hybrid|always|current   política ReAct (via env REACT_POLICY);
                                         'current' no toca el entorno.
    --only id1,id2      correr solo esas tareas.
    --category a,b      correr solo esas categorías.
    --out DIR           directorio de resultados (default: bench_results/).
    --timeout N         segundos por tarea (default 300).
    --label TEXT        etiqueta libre incluida en el JSON (p.ej. 'fase4-pre').

El score principal EXCLUYE las tareas `flaky` (red externa); `score_strict`
las incluye. HITL queda desactivado (el grafo se construye sin hitl_manager).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))          # evaluator / synthetic como módulos planos
_SRC = _HERE.parent.parent / "src"
if _SRC.is_dir():                        # en el repo; en el contenedor ya está instalado
    sys.path.insert(0, str(_SRC))

from evaluator import TaskVerdict, _herramientas, aggregate, evaluate
from synthetic import build_layer


def _apply_policy(policy: str) -> None:
    """Fija la política ReAct via entorno ANTES de importar settings."""
    # El bench es DESATENDIDO: sin HITL. GeoAgentGraph crea un HITLManager por
    # defecto aunque no se le pase — apagarlo por settings es lo único fiable
    # (si no, cada SQL espera HITL_TIMEOUT=300s una aprobación que nunca llega).
    os.environ["HITL_ENABLED"] = "false"
    if policy == "current":
        return
    os.environ["REACT_POLICY"] = policy
    # Compat con el flag booleano viejo: 'always' lo enciende, el resto lo apaga.
    os.environ["REACT_MODE"] = "true" if policy == "always" else "false"


async def _build_graph():
    """Construye el grafo como lo hace la API (dependencies.py) pero SIN HITL."""
    import asyncpg

    from geo_copilot.core.config import get_settings
    from geo_copilot.core.llm_client import LLMClient
    from geo_copilot.orchestrator.graph import GeoAgentGraph
    from geo_copilot.semantic.layer import SemanticLayer

    settings = get_settings()

    db_pool = None
    try:
        db_pool = await asyncpg.create_pool(
            settings.database_url.get_secret_value(),
            min_size=1, max_size=4,
            command_timeout=settings.db_query_timeout,
        )
    except Exception as exc:  # noqa: BLE001 — un crash del grafo ES un hallazgo
        print(f"[bench] AVISO: sin BD ({exc}) — las tareas de BD fallarán", file=sys.stderr)

    llm = LLMClient.from_settings(settings)

    semantic = None
    try:
        semantic = SemanticLayer(settings.semantic_layer_path)
        if db_pool is not None:
            await semantic.hydrate_from_database(db_pool)
    except Exception as exc:  # noqa: BLE001 — un crash del grafo ES un hallazgo
        print(f"[bench] AVISO: semantic layer no cargó ({exc})", file=sys.stderr)

    graph = GeoAgentGraph(
        llm_client=llm, db_pool=db_pool, semantic_layer=semantic,
        # SIN hitl_manager: los nodos saltan la aprobación (bench desatendido).
    )
    return graph, db_pool


async def _build_app_graph():
    """El grafo de la APP, con todo lo que la app conecta: MCP (hub con pinning), workspace,
    geocodificador… Las verdades preguntan por esos servicios; el grafo de ``_build_graph`` no los
    tiene. HITL apagado por entorno (``_apply_policy``), igual que el bench."""
    from geo_copilot.api.dependencies import get_app_state

    estado = get_app_state()
    await estado.initialize()
    return estado.agent_graph, estado


_CONSUMO: dict = {}


def _contar_llm() -> None:
    """Cuenta llamadas y tokens del LLM por pregunta, interceptando lo que el cliente ya informa a
    observabilidad (sin tocar el producto). Para comparar la EFICIENCIA de dos modelos."""
    from geo_copilot.platform import observabilidad

    original = observabilidad.registrar_llm

    def registrar(modelo, segundos, uso):
        _CONSUMO["llamadas"] = _CONSUMO.get("llamadas", 0) + 1
        _CONSUMO["segundos_llm"] = round(_CONSUMO.get("segundos_llm", 0.0) + segundos, 2)
        for clave in ("prompt_tokens", "completion_tokens"):
            _CONSUMO[clave] = _CONSUMO.get(clave, 0) + int((uso or {}).get(clave) or 0)
        _CONSUMO["modelo"] = modelo
        return original(modelo, segundos, uso)

    observabilidad.registrar_llm = registrar


def _process_kwargs(task: dict) -> dict:
    """Traduce ``setup`` del YAML a los kwargs de ``graph.process()``."""
    setup = task.get("setup") or {}
    kwargs: dict = {}
    layer = setup.get("previous_geojson")
    if layer:
        kwargs["previous_geojson"] = build_layer(str(layer))
    for key in (
        "conversation_history", "previous_sql", "previous_results",
        "active_data_source", "active_source_name", "map_context",
        "found_services", "session_region",
    ):
        if key in setup:
            kwargs[key] = setup[key]
    return kwargs


async def _run_task(graph, task: dict, timeout: float, session_id: str = "") -> TaskVerdict:
    query = str(task["query"])
    _CONSUMO.clear()
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(
            # session_id vacío: sin websockets ni HITL (bench desatendido). En modo --app cada
            # corrida lleva su sesión: el workspace y las capas de un MCP cuelgan de ella.
            graph.process(query=query, session_id=session_id, **_process_kwargs(task)),
            timeout=timeout,
        )
    except TimeoutError:
        return TaskVerdict(
            task_id=str(task.get("id")), ok=False,
            latency_s=round(time.monotonic() - t0, 1),
            error=f"timeout tras {timeout:.0f}s",
            reasons=[f"timeout tras {timeout:.0f}s"],
        )
    except Exception as exc:  # noqa: BLE001 — un crash del grafo ES un hallazgo
        return TaskVerdict(
            task_id=str(task.get("id")), ok=False,
            latency_s=round(time.monotonic() - t0, 1),
            error=f"excepción del grafo: {exc}",
            reasons=[f"excepción del grafo: {exc}"],
        )
    latency = round(time.monotonic() - t0, 1)
    result = result if isinstance(result, dict) else {}
    verdict = evaluate(task, result, latency_s=latency)
    verdict.answer = str(result.get("message") or "")[:600] or None
    verdict.intent = result.get("intent")
    verdict.sql = (result.get("sql") or None) and str(result["sql"])[:600]
    verdict.herramientas = _herramientas(result)
    verdict.llm = dict(_CONSUMO)
    return verdict


def _git_commit() -> str | None:
    try:
        import subprocess
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(_HERE),
        )
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _fraccion(texto: str) -> float:
    """El umbral es una fracción 0-1: `85` (en vez de 0.85) haría fallar toda corrida."""
    valor = float(texto)
    if not 0.0 <= valor <= 1.0:
        raise argparse.ArgumentTypeError(f"el umbral es una fracción entre 0 y 1, no {texto}")
    return valor


def _bajo_umbral(summary: dict, umbral: float | None) -> str | None:
    """Motivo por el que la corrida NO supera el umbral, o None si lo supera (o no hay umbral)."""
    if umbral is None:
        return None
    # F7 (auditoría): si todas las tareas son `flaky`, el score principal sale de 0 tareas (0.0);
    # se decía «por debajo del umbral» cuando en realidad no se midió nada.
    if not summary.get("n_tasks"):
        return "NINGUNA TAREA NO-FLAKY: el score principal no mide nada (revisa los filtros)"
    if summary["score"] < umbral:
        return f"POR DEBAJO DEL UMBRAL: {summary['score']:.2%} < {umbral:.0%}"
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark agéntico GEO_COPILOT")
    parser.add_argument("--policy", default="current",
                        choices=["current", "off", "hybrid", "always"])
    parser.add_argument("--tasks", default=str(_HERE / "tasks.yaml"))
    parser.add_argument("--only", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--label", default="")
    # F7 (S7.4): corrida nocturna contra el despliegue — por debajo, código de salida 1
    parser.add_argument("--umbral", type=_fraccion, default=None,
                        help="score mínimo (0-1); por debajo el proceso sale con código 1")
    parser.add_argument("--verbose", action="store_true",
                        help="logging INFO a stderr (diagnóstico)")
    parser.add_argument("--app", action="store_true",
                        help="grafo de la app con sus MCP (necesario para verdades.yaml)")
    parser.add_argument("--repeticiones", type=int, default=1,
                        help="corre cada tarea N veces: el LLM no es determinista, se mide una TASA")
    args = parser.parse_args()

    if args.verbose:
        import logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(levelname)s %(name)s: %(message)s",
            stream=sys.stderr,
        )

    _apply_policy(args.policy)
    _contar_llm()

    import yaml
    tasks: list[dict] = yaml.safe_load(Path(args.tasks).read_text(encoding="utf-8"))

    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        tasks = [t for t in tasks if str(t.get("id")) in wanted]
    if args.category:
        cats = {x.strip() for x in args.category.split(",") if x.strip()}
        tasks = [t for t in tasks if str(t.get("category")) in cats]
    if not tasks:
        print("[bench] no hay tareas que correr con esos filtros", file=sys.stderr)
        return 2

    if args.app:
        graph, app_state = await _build_app_graph()
        db_pool = None
    else:
        (graph, db_pool), app_state = await _build_graph(), None
    from geo_copilot.core.config import get_settings
    settings = get_settings()

    print(f"[bench] {len(tasks)} tareas ×{args.repeticiones} · policy={args.policy} · "
          f"modelo={settings.llm_provider}/{settings.llm_model}{' · app+MCP' if args.app else ''}")

    verdicts: list[TaskVerdict] = []
    by_category: dict[str, list[TaskVerdict]] = {}
    by_task: dict[str, list[TaskVerdict]] = {}
    for i, task in enumerate(tasks, 1):
        tid, cat = str(task.get("id")), str(task.get("category"))
        for rep in range(args.repeticiones):
            print(f"[bench] ({i}/{len(tasks)}) {tid} #{rep + 1} …", flush=True)
            sesion = f"bench-{uuid.uuid4().hex[:10]}" if args.app else ""
            verdict = await _run_task(graph, task, args.timeout, session_id=sesion)
            verdicts.append(verdict)
            by_category.setdefault(cat, []).append(verdict)
            by_task.setdefault(tid, []).append(verdict)
            mark = "✓" if verdict.ok else "✗"
            why = "" if verdict.ok else f"  ← {'; '.join(verdict.reasons)[:160]}"
            print(f"[bench]   {mark} {tid} ({verdict.latency_s}s){why}", flush=True)

    if db_pool is not None:
        await db_pool.close()
    if app_state is not None and hasattr(app_state, "shutdown"):
        await app_state.shutdown()

    flaky_ids = {str(t.get("id")) for t in tasks if t.get("flaky")}
    core = [v for v in verdicts if v.task_id not in flaky_ids]
    summary = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "label": args.label or None,
        "policy": args.policy,
        "model": f"{settings.llm_provider}/{settings.llm_model}",
        "git_commit": _git_commit(),
        "score": aggregate(core)["score"],
        "score_strict": aggregate(verdicts)["score"],
        # aggregate() ya devuelve n_tasks/n_passed (antes se prefijaba otra vez: n_n_tasks)
        **{k: vv for k, vv in aggregate(core).items() if k != "score"},
        "by_category": {
            cat: aggregate(vs) for cat, vs in sorted(by_category.items())
        },
        "repeticiones": args.repeticiones,
        # tasa por tarea: con N corridas, una tarea al 2/3 es inestable, no «verde» ni «roja»
        "by_task": {tid: aggregate(vs) for tid, vs in by_task.items()},
        "tasks": [v.to_dict() for v in verdicts],
    }

    out_dir = Path(args.out) if args.out else (_HERE.parent.parent / "bench_results")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ")
    out_path = out_dir / f"{stamp}_{args.policy}{('_' + args.label) if args.label else ''}.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[bench] SCORE {summary['score']:.2%} "
          f"(strict {summary['score_strict']:.2%}) → {out_path}")
    for cat, agg in summary["by_category"].items():
        print(f"[bench]   {cat:<14} {agg['n_passed']}/{agg['n_tasks']}")
    if args.repeticiones > 1:
        for tid, agg in summary["by_task"].items():
            if agg["n_passed"] < agg["n_tasks"]:
                print(f"[bench]   inestable o roja: {tid} {agg['n_passed']}/{agg['n_tasks']}")
    motivo = _bajo_umbral(summary, args.umbral)
    if motivo:
        print(f"[bench] {motivo}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
