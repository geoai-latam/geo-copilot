

def test_jenks_con_menos_valores_que_clases_da_una_clase_por_valor():
    """V5 (imagery): NDVI de 2 lotes (0.38 y 0.68) en «5 clases» salía en UNA clase: mismo color."""
    from geo_copilot.agents.symbology_agent.agent import SymbologyAgent
    from geo_copilot.agents.symbology_agent.styles import ColorScheme

    a = SymbologyAgent.__new__(SymbologyAgent)
    bordes = a._edges_jenks([0.38, 0.68], 5)
    assert bordes == [0.38, 0.53, 0.68]
    clases = a._edges_to_breaks([0.38, 0.68], bordes, ColorScheme.BLUES)
    assert [c.count for c in clases] == [1, 1] and clases[0].color != clases[1].color
    assert a._edges_jenks([3.0, 3.0, 3.0], 4) == [3.0, 3.0]
    assert a._edges_jenks([1, 2, 2, 3], 5) == [1, 1.5, 2.5, 3]
