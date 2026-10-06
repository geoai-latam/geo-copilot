/* Configuración de ESLint del frontend (flat config, ESLint 10).
 *
 * Migrada de `.eslintrc.cjs` (ESLint 8) con las MISMAS reglas y alcances. Antes `--ext ts,tsx`
 * limitaba el lint a TypeScript; aquí lo hace `files`, y los `.js`/`.mjs`/`.cjs` (configs de
 * herramientas y scripts) quedan fuera como antes.
 *
 * El typecheck (`tsc` con `strict` y `noUnusedLocals`) ya cubre tipos y variables muertas; aquí
 * van las reglas que el compilador no ve: hooks de React y sus dependencias, y el contrato de
 * fast-refresh.
 */
import js from '@eslint/js'
import { defineConfig } from 'eslint/config'
import reactHooks from 'eslint-plugin-react-hooks'
import { reactRefresh } from 'eslint-plugin-react-refresh'
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default defineConfig(
  {
    ignores: [
      'dist',
      'coverage',
      'playwright-report',
      'test-results',
      '**/*.js',
      '**/*.mjs',
      '**/*.cjs',
      '*.config.ts',
    ],
  },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, tseslint.configs.recommended, reactRefresh.configs.recommended()],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: { ...globals.browser, ...globals.node },
    },
    linterOptions: { reportUnusedDisableDirectives: 'error' },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      // Las dos reglas de `plugin:react-hooks/recommended` de la v4. La v7 añade a su
      // `recommended` las reglas del React Compiler (purity, refs, set-state-in-effect…): son
      // otra decisión, no parte de subir ESLint.
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      // Estaba activa en la configuración que se perdió: hay `eslint-disable-next-line no-console`
      // en el código pidiéndola. `warn` y `error` sí pasan: son los canales que un navegador
      // debe poder mostrar.
      'no-console': ['error', { allow: ['warn', 'error'] }],
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // `catch (e) { /* da igual */ }` es un patrón deliberado en varios sitios del cliente (el
      // render por capa se aísla a propósito), así que el aviso se limita a lo que de verdad
      // esconde un bug: una variable sin usar que NO sea un argumento ignorado.
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', caughtErrors: 'none' }],
      // F1 del plan de calidad (docs/PLAN_CALIDAD_ORQUESTADOR_2026-10-04.md): TRINQUETE. Clean
      // Code: archivos de 500 líneas como máximo; funciones simples. Lo que ya los pasaba lleva su
      // `eslint-disable` (deuda a la vista); algo NUEVO que los pase rompe el lint, y al arreglar
      // uno viejo `--report-unused-disable-directives` obliga a quitar su disable. Solo puede
      // bajar. (Sin max-lines-per-function: un componente React con su JSX es UNA función.)
      'max-lines': ['error', { max: 500 }],
      complexity: ['error', 15],
    },
  },
  {
    // Tests y specs de Playwright: `any` es legítimo para fabricar mensajes degenerados del
    // WebSocket y espiar internos, que es justo lo que esos tests existen para cubrir. `console`
    // es su forma de dejar rastro.
    files: ['**/*.test.ts', '**/*.test.tsx', 'e2e/**/*.ts', 'e2e-integration/**/*.ts', 'src/test/**'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
      'no-console': 'off',
      // el trinquete de tamaño es para el código del producto: una suite larga no es deuda
      'max-lines': 'off',
      complexity: 'off',
    },
  },
  {
    // generado desde el esquema del backend (`npm run contracts`): no se edita a mano
    files: ['src/contracts/generated.ts'],
    // (y sus `interface X {}` salen tal cual del esquema: un objeto sin campos fijos)
    rules: { 'max-lines': 'off', '@typescript-eslint/no-empty-object-type': 'off' },
  },
  {
    // El logger ES el sitio donde vive `console`. Prohibírselo obligaría a ponerle un disable a
    // cada línea de su propia razón de existir.
    files: ['src/utils/logger.ts'],
    rules: { 'no-console': 'off' },
  },
)
