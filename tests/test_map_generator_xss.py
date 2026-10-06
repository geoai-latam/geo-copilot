"""Regresión C3d-4 / TST-03: XSS en el popup Leaflet, el título y — el vector
real — los VALORES de properties embebidos en el <script> del HTML."""

from geo_copilot.agents.insights_agent.map_generator import MapGenerator


def _html_with_property(value: str) -> str:
    """HTML de un point_map con una feature cuya property lleva `value`."""
    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
                "properties": {"evil": value},
            }
        ],
    }
    return MapGenerator().render_to_html(
        {"type": "point_map", "title": "t", "data": data}
    )


def test_popup_uses_textcontent_not_concat():
    html = MapGenerator().render_to_html({"type": "point_map", "title": "t"})
    # El popup ya NO concatena valores crudos en innerHTML.
    assert "popup += '<strong>'" not in html
    # Usa DOM seguro (textContent / createTextNode).
    assert "textContent" in html
    assert "createTextNode" in html


def test_title_is_escaped():
    evil = '<script>alert(1)</script>'
    html = MapGenerator().render_to_html({"type": "point_map", "title": evil})
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_property_value_cannot_break_out_of_script():
    """TST-03 (vector REAL): una property que intenta cerrar el <script> con
    `</script>` para inyectar markup NO debe romper el tag ni dejar el markup
    ejecutable en el HTML. Antes: el `json.dumps` crudo dentro del <script>
    dejaba `</script><img onerror=...>` literal → el parser HTML lo ejecutaba."""
    html = _html_with_property("</script><img src=x onerror=alert(1)>")

    # No debe aparecer el cierre de script seguido del markup (breakout).
    assert "</script><img" not in html
    # Ni el <img> ejecutable sin escapar.
    assert "<img src=x onerror=alert(1)>" not in html
    # El dato SÍ sobrevive dentro del JS, escapado a \\u003c (JSON válido).
    assert "u003c/script" in html.replace("\\", "")


def test_property_value_with_script_payload_neutralized():
    """Paridad con test_title_is_escaped, pero para un VALOR de property: un
    `<script>alert(1)</script>` en un atributo no queda como markup vivo."""
    html = _html_with_property("<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in html


def test_benign_property_with_spaces_preserved():
    """El escape de embedding NO debe corromper valores normales con espacios
    (regresión: un replace ingenuo de separadores podría tocar los espacios)."""
    html = _html_with_property("hola mundo con espacios")
    assert "hola mundo con espacios" in html
