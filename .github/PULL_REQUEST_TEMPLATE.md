## Qué cambia

<!-- Una o dos frases. Qué hace este PR, en presente. -->

## Por qué

<!--
La fricción o el fallo que lo motivó. Si cierra un issue, enlázalo: "Cierra #12".
-->

## Tipo de cambio

- [ ] `feat` — funcionalidad nueva
- [ ] `fix` — corrección
- [ ] `sec` — seguridad
- [ ] `refactor` — sin cambio de comportamiento
- [ ] `test` — solo tests
- [ ] `docs` — solo documentación

## Tests

- [ ] `ruff check src/ tests/ services/` pasa
- [ ] `python -m pytest -q` pasa
- [ ] `cd frontend && npm test -- --run` pasa (si tocaste el frontend)
- [ ] `cd frontend && npm run build` pasa (si tocaste el frontend)
- [ ] Añadí tests que fallan sin este cambio y pasan con él
- [ ] Corrí los marcados `@pytest.mark.integration` o los E2E, que el CI no corre

**Cómo lo probaste**, con el comando exacto:

```bash
```

## Documentación

- [ ] Actualicé `docs/sistema/` si cambié comportamiento (es la fuente de verdad documental)
- [ ] Actualicé el `README.md` si cambié la instalación o el uso
- [ ] No hacía falta tocar documentación, y explico abajo por qué

## Comprobaciones del proyecto

- [ ] No metí heurísticas, plantillas SQL ni listas de palabras clave: la decisión semántica sigue
      siendo del LLM, validada contra el esquema
- [ ] Si falla, falla honesto: no hay fallback que adivine un CRS, una capa o un resultado
- [ ] Si toqué un gate (aprobación, scopes, dominios), sigue siendo allowlist y falla cerrado
- [ ] Si añadí una tool al `imagery-mcp`, la registré en `TOOL_SCOPES`
      (`services/imagery_mcp/imagery_mcp/auth.py`)
- [ ] No hay secretos, `.env`, dumps de datos ni archivos pesados en el diff

## Notas para quien revise

<!-- Lo que no se ve en el diff: decisiones que dudaste, deuda que dejas, qué mirar primero. -->
