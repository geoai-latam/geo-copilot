"""
Prompt del PythonAgent.

El SYSTEM_PROMPT enseña al LLM a generar código Python para DOS familias de
tareas sobre un GeoDataFrame `gdf` en memoria:

  1. Transformaciones geométricas (buffer, centroide, área, clip, dissolve…)
     → producen una capa para el mapa (`result` = GeoDataFrame).
  2. Análisis y estadística (agregaciones, correlaciones, distribuciones,
     clustering, vecino más cercano, regresión…) → producen `table`/`stats`/
     `chart`, no necesariamente geometría.

El sandbox expone numpy/pandas/scipy/scikit-learn/statsmodels además de
geopandas/shapely, así que el agente puede hacer análisis de verdad, no solo
geometría. Antes el prompt solo enseñaba geometría y exigía un GeoDataFrame —
por eso el sandbox estaba subutilizado.
"""
from geo_copilot.prompts import cargar_prompt

SYSTEM_PROMPT = cargar_prompt("python_motor")
