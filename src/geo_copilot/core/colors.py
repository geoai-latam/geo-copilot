"""
Colores ANSI para logging en consola.

Módulo centralizado para evitar duplicación de la clase Colors
en múltiples archivos del proyecto.
"""


class Colors:
    """Colores ANSI para output en terminal."""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'


def log_agent(agent_name: str, action: str, details: str = "", success: bool = True):
    """
    Helper para logging decorativo de agentes (output de consola).

    Solo imprime cuando ``settings.debug_console_output`` está activo. Antes
    imprimía SIEMPRE, ignorando ese setting; en Windows el emoji 🤖 reventaba
    con ``UnicodeEncodeError`` (codec cp1252) dentro del flujo de ``/query``,
    y el error se devolvía como respuesta del asistente. El ``print`` va
    envuelto en try/except como red final: el output decorativo nunca debe
    tumbar el flujo de ejecución.
    """
    try:
        from geo_copilot.core.config import settings
        if not settings.debug_console_output:
            return
    except Exception:  # noqa: BLE001 — ante cualquier duda, no imprimir.
        return

    color = Colors.GREEN if success else Colors.RED
    status = "✓" if success else "✗"
    try:
        print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*60}{Colors.END}")
        print(f"{Colors.BOLD}🤖 [{agent_name}]{Colors.END} {color}{status} {action}{Colors.END}")
        if details:
            print(f"   {Colors.YELLOW}→ {details}{Colors.END}")
        print(f"{Colors.CYAN}{'='*60}{Colors.END}")
    except Exception:  # noqa: BLE001 — p.ej. UnicodeEncodeError en consolas cp1252.
        pass
