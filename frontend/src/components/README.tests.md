# Tests de componentes React

## `DataDiscoveryPanel.test.tsx.disabled` — activación manual

Este archivo de tests está **deshabilitado por extensión** (`.disabled`)
hasta que las dependencias de testing-library estén instaladas.

**Por qué:** el archivo importa `@testing-library/react` y
`@testing-library/user-event`. Si vitest intentara parsearlo con esas
deps ausentes, la suite COMPLETA rompería. Renombrarlo a
`.tsx.disabled` lo excluye del glob `**/*.test.tsx` y mantiene la
suite verde mientras esperas a instalar.

### Cómo activarlo

```bash
cd frontend
npm install              # ya añadimos las deps en package.json
mv src/components/DataDiscoveryPanel.test.tsx.disabled \
   src/components/DataDiscoveryPanel.test.tsx
npx vitest run src/components/DataDiscoveryPanel.test.tsx
```

Si los 9 tests pasan, todo está bien. Commit el rename junto con el
`package-lock.json` actualizado.

### Por qué no auto-skip

Vitest hace `transform` de cada archivo `.test.tsx` ANTES de evaluar
condicionales runtime. Un import estático de un módulo inexistente
rompe el transform — no hay forma de "auto-skip" sin afectar el parser.
Las alternativas (require dinámico, conditional describe, etc.) o
complican el archivo o no funcionan con JSX. El rename es la solución
más limpia para una situación temporal.
