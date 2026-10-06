"""Re-aprobar herramientas MCP desde la terminal del despliegue (F5 del plan de calidad).

Cuando la descripción o el esquema de una herramienta cambian, el pinning la deshabilita hasta que
un administrador la re-apruebe (protección contra un «rug pull»). Hasta ahora eso solo se hacía en el
panel de Conexiones, a mano: en la revisión MCP del 2026-10-04 hubo que hacerlo 3 veces tras cada
despliegue. Este comando hace lo mismo que el botón del panel (fija la huella actual y lo deja en la
auditoría) y la app, que revalida el catálogo cada `MCP_REFRESH_S`, la vuelve a habilitar sola.

    python -m geo_copilot.platform.mcp.aprobar                          # lista las pendientes
    python -m geo_copilot.platform.mcp.aprobar --servidor sql --tool sql_query --por ana
    python -m geo_copilot.platform.mcp.aprobar --pendientes --por ana --si

Sin `--si` solo MUESTRA lo que aprobaría y la descripción que el LLM va a leer: aprobar a ciegas es
justo lo que el pinning existe para impedir. Solo para los servidores de la PLATAFORMA (el YAML); las
conexiones de una organización se re-aprueban en su panel.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from geo_copilot.platform.mcp.hub import EstadoTool, McpHub, RedisPinStore


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m geo_copilot.platform.mcp.aprobar",
                                description="Re-aprobar herramientas MCP deshabilitadas por el pinning.")
    p.add_argument("--servidor", help="id del servidor (config/mcp_servers*.yaml)")
    p.add_argument("--tool", help="nombre de la herramienta en ese servidor")
    p.add_argument("--pendientes", action="store_true", help="todas las deshabilitadas por el pinning")
    p.add_argument("--por", help="quién aprueba (queda en la auditoría); obligatorio para aprobar")
    p.add_argument("--si", action="store_true", help="aprobar de verdad (sin esto, solo muestra)")
    args = p.parse_args(argv)
    if bool(args.servidor) != bool(args.tool):
        p.error("--servidor y --tool van juntos")
    if (args.servidor or args.pendientes) and args.si and not args.por:
        p.error("--por es obligatorio para aprobar: la auditoría dice quién lo hizo")
    return args


async def _hub(settings: Any) -> McpHub:
    """El mismo hub que arma la app (mismo YAML, mismos pins en Redis), con el catálogo al día."""
    from geo_copilot.platform.mcp import config as mcp_config

    if str(getattr(settings, "session_backend", "")).lower() != "redis":
        raise SystemExit("Los pins viven en la MEMORIA de la app (SESSION_BACKEND no es redis): este "
                         "comando no los alcanza. Re-aprueba en el panel de Conexiones.")
    hub = McpHub(mcp_config.cargar(settings.mcp_servers_path), pins=RedisPinStore(str(settings.redis_url)))
    await hub.refrescar()
    return hub


def _elegidas(hub: McpHub, args: argparse.Namespace) -> list[EstadoTool]:
    pendientes = [e for e in hub.tools.values() if not e.habilitada]
    if args.pendientes:
        return pendientes
    if args.servidor:
        return [e for e in hub.tools.values() if (e.servidor, e.tool) == (args.servidor, args.tool)]
    return pendientes


def _mostrar(e: EstadoTool) -> None:
    estado = "HABILITADA" if e.habilitada else f"PENDIENTE: {e.motivo}"
    print(f"\n{e.servidor}:{e.tool}  [{e.riesgo}]  {estado}")
    print(f"  descripción que leerá el LLM: {e.descripcion or '(vacía)'}")
    print(f"  argumentos: {', '.join(sorted(e.esquema.get('properties') or {})) or '(ninguno)'}")


async def _auditar(settings: Any, e: EstadoTool, por: str) -> None:
    """Deja constancia en la auditoría de la plataforma (la misma acción que el panel)."""
    import asyncpg

    from geo_copilot.platform import auditoria
    from geo_copilot.platform.identidad.principal import Principal

    pool = await asyncpg.create_pool(settings.database_url.get_secret_value(), min_size=1, max_size=1)
    try:
        auditoria.instalar(auditoria.AuditoriaEnPostgres(pool))
        quien = Principal(sub=por, org_id=settings.org_plataforma or "plataforma", nombre=por, via="cli")
        await auditoria.registrar("tool.reaprobar", f"mcp.{e.servidor}.{e.tool}", "ok", principal=quien,
                                  detalle={"via": "cli", "huella": e.huella[:16]})
    finally:
        await pool.close()


async def ejecutar(argv: list[str] | None = None) -> int:
    from geo_copilot.core.config import get_settings

    args = _argumentos(argv)
    settings = get_settings()
    hub = await _hub(settings)
    elegidas = _elegidas(hub, args)
    if not elegidas:
        print("Nada que aprobar: ninguna herramienta pendiente" if not args.servidor
              else f"«{args.servidor}:{args.tool}» no existe en los servidores de la plataforma")
        return 0 if not args.servidor else 1
    for e in elegidas:
        _mostrar(e)
    if not (args.si and (args.servidor or args.pendientes)):
        print("\nNo se aprobó nada. Revisa las descripciones y repite con --por <quién> --si.")
        return 0
    for e in elegidas:
        await hub.aprobar(e.servidor, e.tool, por=args.por)
        await _auditar(settings, e, args.por)
        print(f"aprobada {e.servidor}:{e.tool} (la app la habilita en su próxima revalidación, "
              f"≤ {settings.mcp_refresh_s:.0f} s)")
    print(json.dumps({"aprobadas": [f"{e.servidor}:{e.tool}" for e in elegidas]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(ejecutar()))
