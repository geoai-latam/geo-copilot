"""SEC-ERROR-LEAK — el responder no filtra internals crudos al cliente.

El responder mostraba state['error'] crudo con HTTP 200, saltándose el saneo
que aplican los except: podía filtrar nombres de tablas/columnas/tipos PostGIS
o tracebacks al chat. sanitize_error_message() genericar los mensajes con
marcadores de internals y deja pasar los mensajes honestos ya redactados.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from geo_copilot.core.error_sanitizer import (
    ERROR_MESSAGES,
    sanitize_error,
    sanitize_error_message,
)

_DB = ERROR_MESSAGES["database"]
_TABLA = ERROR_MESSAGES["table_not_found"]
_COLUMNA = ERROR_MESSAGES["column_not_found"]


@pytest.fixture
def prod_settings(monkeypatch):
    """Fuerza debug=False (prod): la garantía de seguridad es que NO se filtren
    internals al cliente. En debug (dev local) el crudo sí se anexa, por diseño."""
    monkeypatch.setattr(
        "geo_copilot.core.config.get_settings",
        lambda: SimpleNamespace(debug=False),
    )


class TestSanitizeErrorMessage:
    def test_mensajes_honestos_pasan_tal_cual(self, prod_settings):
        for honest in (
            "No hay datos cargados para procesar. Primero consulta la base de datos.",
            "Consulta rechazada: el usuario pidió no ejecutar.",
            "El cómputo geométrico en memoria requiere un entorno Linux/Docker.",
            "Operación completada pero no quedaron features en el resultado.",
        ):
            assert sanitize_error_message(honest) == honest

    def test_error_crudo_postgis_se_genericar(self, prod_settings):
        # Fuga clásica: el nombre de la tabla en un error crudo de PostgreSQL.
        leak = 'relation "usuarios_privados" does not exist'
        out = sanitize_error_message(leak)
        assert out == _TABLA
        assert "usuarios_privados" not in out

    def test_columna_y_traceback_se_genericar(self, prod_settings):
        assert sanitize_error_message('column "salario_secreto" does not exist') == _COLUMNA
        tb = 'Traceback (most recent call last):\n  File "/app/geo_copilot/x.py", line 5'
        out = sanitize_error_message(tb)
        assert out == ERROR_MESSAGES["generic"]
        assert "/app/" not in out and ".py" not in out

    def test_debug_anexa_crudo_para_dev(self, monkeypatch):
        # En debug (dev local) sí se muestra el crudo para diagnóstico — es el
        # contrato explícito, igual que sanitize_error. Prod DEBE correr debug=False.
        monkeypatch.setattr(
            "geo_copilot.core.config.get_settings",
            lambda: SimpleNamespace(debug=True),
        )
        out = sanitize_error_message('relation "t" does not exist')
        assert out.startswith(_TABLA) and "debug:" in out

    def test_none_no_revienta(self, prod_settings):
        assert sanitize_error_message(None) == ""


class TestPlannerSanitizaExcepcion:
    def test_sanitize_error_no_expone_str_exc(self):
        # El planner ahora usa sanitize_error(exc); str(exc) con una ruta no debe
        # aparecer en el mensaje al cliente.
        exc = ImportError("No module named 'foo'; ruta C:/Users/secreto/proj/x.py")
        msg = sanitize_error(exc, context="planner.build_plan")
        assert "secreto" not in msg
        assert "x.py" not in msg


# ── S0.4: allowlist y los tres canales ──────────────────────────────────────
def _asyncpg_errors() -> list[BaseException]:
    """Excepciones REALES de asyncpg, con el texto que PostgreSQL produce."""
    import asyncpg

    e1 = asyncpg.exceptions.UndefinedColumnError('column "salario_secreto" does not exist')
    e2 = asyncpg.exceptions.UndefinedTableError('relation "pg_shadow_copia" does not exist')
    e3 = asyncpg.exceptions.PostgresSyntaxError('syntax error at or near "FROMM"')
    return [e1, e2, e3]


_SECRETOS = ("salario_secreto", "pg_shadow_copia", "FROMM", "SQLSTATE", "DETAIL", "HINT")


class TestAllowlist:
    def test_solo_frases_seguras_pasan(self, prod_settings):
        from geo_copilot.core.error_sanitizer import is_user_safe

        assert is_user_safe("No hay datos cargados.")
        assert is_user_safe("Consulta rechazada: DELETE no permitido")
        # Lo que la denylist no preveía también se bloquea: la forma lo delata.
        assert not is_user_safe('valor inesperado en {"tabla": "nomina"}')
        assert not is_user_safe("SELECT * FROM nomina WHERE id = 1")
        assert not is_user_safe("línea 1\nlínea 2")
        assert not is_user_safe("x" * 301)

    def test_error_sin_marcadores_conocidos_tambien_se_bloquea(self, prod_settings):
        # Con la denylist anterior esto pasaba: ninguna de las 13 subcadenas.
        out = sanitize_error_message('KeyError: "nomina_empleados"')
        assert "nomina_empleados" not in out
        assert out in ERROR_MESSAGES.values()

    @pytest.mark.parametrize(
        ("exc", "esperado"),
        list(zip(_asyncpg_errors(), ("column_not_found", "table_not_found", "database"), strict=True)),
        ids=lambda v: type(v).__name__ if isinstance(v, BaseException) else v,
    )
    def test_errores_reales_de_asyncpg(self, prod_settings, exc, esperado):
        from geo_copilot.core.error_sanitizer import describe_error

        for out in (sanitize_error_message(str(exc)), describe_error(str(exc))):
            assert out == ERROR_MESSAGES[esperado]
            assert not any(s in out for s in _SECRETOS)


class TestTresCanales:
    """Los tres canales que la auditoría encontró devolviendo el error crudo."""

    def test_traza_de_razonamiento(self, prod_settings):
        from geo_copilot.orchestrator.graph import _build_reasoning_trace

        state = {
            "intent": "query_data",
            "messages": [
                {"agent": "gis_agent", "success": False,
                 "content": 'Error: column "salario_secreto" does not exist'},
                {"agent": "responder", "success": True,
                 "content": "Encontré 12 predios en la zona."},
                {"agent": "data_agent", "success": True,
                 "content": 'cargado {"url": "http://10.0.0.5/x"}'},
            ],
        }
        trace = _build_reasoning_trace(state)
        by_step = {t["agent"]: t for t in trace}
        assert by_step["gis_agent"]["detail"] == _COLUMNA
        assert by_step["responder"]["detail"] == "Encontré 12 predios en la zona."
        # Un paso exitoso con contenido no seguro se muestra sin resumen.
        assert by_step["data_agent"]["detail"] == ""
        assert "salario_secreto" not in str(trace)

    @pytest.mark.asyncio
    async def test_reintento_por_websocket(self, monkeypatch):
        # Sin prod_settings: describe_error no depende de debug, y la fixture
        # sustituye get_settings antes de que se importe la app.

        from unittest.mock import AsyncMock

        from geo_copilot.api import websocket as ws

        sent = []
        monkeypatch.setattr(ws.connection_manager, "is_connected", AsyncMock(return_value=True))
        monkeypatch.setattr(
            ws.connection_manager, "send_message",
            AsyncMock(side_effect=lambda sid, msg: sent.append(msg) or True),
        )
        await ws.send_retry_started(
            "s1", "gis_agent", 1, 3,
            'column "salario_secreto" does not exist', "corrigiendo SQL",
        )
        assert sent[0].data["error"] == _COLUMNA
        assert "salario_secreto" not in str(sent[0].data)

    def test_correction_info_de_la_respuesta_rest(self, prod_settings):
        import inspect

        from geo_copilot.api.routes import turno_salida

        # El canal REST arma `original_error` con describe_error; se verifica
        # sobre el código porque la ruta completa requiere el grafo real.
        # (F4: la salida del turno salió de query.py a turno_salida.py)
        src = inspect.getsource(turno_salida)
        bloque = src[src.index('"original_error"'):][:200]
        assert "describe_error(" in bloque


def test_rechazo_por_catalogo_se_lee_como_tabla_inexistente(prod_settings):
    """S0.2: con `enforce`, la tabla fuera del catálogo se bloquea ANTES de la BD.
    El usuario debe leer lo mismo que si PostgreSQL dijera que no existe, no la
    jerga del validador ("allowlist", "estructura no admitida")."""
    crudo = (
        "SQL rechazado por el validador: Estructura no admitida: "
        "tabla no disponible en el catálogo: public.hospitales"
    )
    assert sanitize_error_message(crudo) == ERROR_MESSAGES["table_not_found"]


def test_el_corrector_clasifica_el_rechazo_como_tabla_inexistente():
    from geo_copilot.agents.gis_agent.sql_corrector import SQLCorrector

    a = SQLCorrector().analyze_error(
        "SQL rechazado por el validador: Estructura no admitida: "
        "tabla no disponible en el catálogo: public.hospitales"
    )
    assert a["type"] == "table_not_found"
