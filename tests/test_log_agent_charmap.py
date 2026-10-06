"""Regresión LIVE.1: log_agent reventaba con UnicodeEncodeError (cp1252).

`core.colors.log_agent` imprimía SIEMPRE un rótulo con el emoji 🤖,
ignorando `settings.debug_console_output`. En Windows el print a stdout
(cp1252) lanzaba UnicodeEncodeError dentro del flujo de /query, y el
grafo lo devolvía como respuesta del asistente.
"""

import io

import pytest

from geo_copilot.core import colors
from geo_copilot.core.config import get_settings


def test_log_agent_silent_by_default(monkeypatch, capsys):
    monkeypatch.setattr(get_settings(), "debug_console_output", False)
    colors.log_agent("RouterAgent", "Analizando", "detalle")
    out = capsys.readouterr().out
    assert out == ""  # no imprime nada (respeta el setting)


def test_log_agent_prints_when_enabled(monkeypatch, capsys):
    monkeypatch.setattr(get_settings(), "debug_console_output", True)
    colors.log_agent("RouterAgent", "Analizando", "detalle")
    out = capsys.readouterr().out
    assert "RouterAgent" in out


def test_log_agent_never_raises_on_unencodable(monkeypatch):
    """Aunque la consola no pueda encodear el emoji, no debe propagar."""
    monkeypatch.setattr(get_settings(), "debug_console_output", True)

    class _Cp1252Stdout(io.StringIO):
        def write(self, s):
            # Simula una consola cp1252: el emoji no es representable.
            s.encode("cp1252")  # lanza UnicodeEncodeError con 🤖
            return super().write(s)

    monkeypatch.setattr("sys.stdout", _Cp1252Stdout())
    # No debe lanzar.
    colors.log_agent("GISAgent", "Generando SQL")
