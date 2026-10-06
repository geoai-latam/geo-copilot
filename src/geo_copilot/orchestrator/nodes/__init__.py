"""
Nodos del grafo de orquestación — extraídos de ``graph.py`` en Fase 6 #6.

Cada nodo vive en su propio módulo. La firma es uniforme::

    async def run(graph: "GeoAgentGraph", state: GraphState) -> dict

Donde:

* ``graph`` es la instancia de ``GeoAgentGraph`` (los nodos siguen
  necesitando acceso a los agentes y al ``hitl_manager`` registrados
  ahí).
* ``state`` es el estado actual del grafo (``GraphState``).
* El retorno es un ``Partial state update`` (igual contrato que tenían
  los métodos ``_*_node`` originales).

``graph.py`` mantiene los métodos ``_*_node`` como thin delegators
(2 líneas) para no romper ``_build_graph`` ni el resto de la clase.
Migración por pasos para que cada extracción se pueda validar contra
los tests de integración (test_orchestrator_integration.py).

Estado de la migración (commit a commit en CHANGELOG.md):

* ``symbology`` — extraído.
* ``insights``  — extraído.
* ``responder`` — extraído.
* ``router``    — extraído.
* ``data``, ``gis``, ``python`` — pendientes (>250 LOC cada uno, con
  retry/HITL embebido). Se migran junto con la unificación del retry
  inline de Fase 6 #4 parte 2.
"""
