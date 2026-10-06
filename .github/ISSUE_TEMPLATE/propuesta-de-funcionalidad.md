---
name: Propuesta de funcionalidad
about: Una capacidad nueva o un cambio de comportamiento
title: "[FEAT] "
labels: enhancement
assignees: ''
---

## Qué problema resuelve

<!--
Empieza por la fricción, no por la solución. Quién la sufre, en qué trabajo concreto.
"Un analista catastral que hoy exporta a Excel para calcular áreas" dice más que
"falta un módulo de estadísticas".
-->

## Cómo te imaginas que funciona

<!-- Si es una consulta en lenguaje natural, escríbela tal cual la dirías. -->

```
```

Y qué debería devolver: ¿capa en el mapa, tabla, gráfico, narrativa?

## Alternativas que ya miraste

<!-- ¿Se puede hacer hoy dando un rodeo? ¿Por qué no alcanza? -->

## Qué parte del sistema tocaría

<!-- Márcalo si lo sabes; si no, déjalo en blanco. La guía está en docs/sistema/. -->

- [ ] Un agente (Router / Data / GIS / Python / Symbology / Insights)
- [ ] Orquestador (LangGraph, ReAct, planner)
- [ ] `imagery-mcp` (Sentinel-2, teselas)
- [ ] API o WebSocket
- [ ] Frontend / MapLibre
- [ ] Despliegue (Docker, configuración)
- [ ] Documentación

## Datos o fuentes que necesita

<!--
Si depende de una fuente externa: cuál, quién la publica, con qué licencia y en qué EPSG.
Sin eso no se puede evaluar si es viable.
-->

## Encaje con el proyecto

- [ ] La decisión semántica la sigue tomando el LLM (nada de heurísticas ni plantillas fijas)
- [ ] Si falla, falla honesto: no devuelve un resultado adivinado
- [ ] No requiere exponer el stack a internet
