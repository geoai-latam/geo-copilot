/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// Destino del backend para el proxy de dev. Configurable por env para
// poder convivir con otros servicios en :8000 (default sin cambios).
//   VITE_PROXY_TARGET=http://localhost:8001 npm run dev
const PROXY_TARGET = process.env.VITE_PROXY_TARGET || 'http://localhost:8000'
const WS_TARGET = process.env.VITE_WS_TARGET || PROXY_TARGET.replace(/^http/, 'ws')
// Solo desarrollo: apuntar el proxy al nginx local (certificado autofirmado), p. ej. para probar
// varias réplicas detrás del balanceador (F7, E7.2). Nunca afecta al build.
const PROXY_SECURE = process.env.VITE_PROXY_INSECURE !== '1'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  // El worker de maplibre-gl 6 es un módulo ES que importa `maplibre-gl-shared` (ver lib/maplibreWorker.ts).
  worker: { format: 'es' },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: PROXY_TARGET,
        changeOrigin: true,
        secure: PROXY_SECURE,
      },
      '/health': {
        target: PROXY_TARGET,
        changeOrigin: true,
        secure: PROXY_SECURE,
      },
      '/ws': {
        target: WS_TARGET,
        ws: true,
        secure: PROXY_SECURE,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    coverage: {
      reporter: ['text', 'json', 'html'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/test/**', 'src/**/*.d.ts'],
      // Gates de cobertura, ~1 pt de margen sobre lo medido. Vitest 5 (2026-10-05) mide con el
      // remapeo por AST de v8, no comparable con el de vitest 1: las líneas pasan de 15,98 % a
      // 51,91 % y las ramas de 78,82 % a 43,76 % con los MISMOS tests (antes contaba mal las
      // ramas de código no ejecutado). Medido: statements 49,69 %, branches 43,76 %,
      // functions 44,38 %, lines 51,91 %. Subir según se añadan tests, nunca bajar.
      thresholds: {
        lines: 50,
        branches: 42,
        functions: 43,
        statements: 48,
      },
    },
  },
})
