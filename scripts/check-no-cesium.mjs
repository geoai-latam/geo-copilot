#!/usr/bin/env node
/**
 * FND-NOCESIUM-GATE — gate de "cero rastro de Cesium".
 *
 * Escanea frontend/src (+ package.json y vite.config.ts) buscando cualquier
 * referencia a `cesium` (case-insensitive, incluidos comentarios, deps, CSS
 * .cesium-*, VITE_CESIUM_TOKEN, vi.mock('cesium')).
 *
 * Dos modos:
 *   (default)   Compara contra scripts/cesium-baseline.json. PASA si todas las
 *               referencias están en el baseline; FALLA si aparece Cesium en un
 *               archivo NUEVO (evita creep durante la migración a MapLibre).
 *   --strict    Estado final (DoD de MAP-CAMERA-RETIRE): FALLA si queda CUALQUIER
 *               referencia. El objetivo es baseline vacío y este modo en verde.
 *
 * Uso:  node scripts/check-no-cesium.mjs [--strict]
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, relative, sep } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = join(HERE, '..')
const FRONTEND = join(REPO_ROOT, 'frontend')

const SCAN_DIRS = [join(FRONTEND, 'src')]
const SCAN_FILES = [join(FRONTEND, 'package.json'), join(FRONTEND, 'vite.config.ts')]
const SKIP_DIRS = new Set(['node_modules', 'dist', '.git', 'coverage'])
const TEXT_EXT = /\.(tsx?|jsx?|mjs|cjs|json|css|scss|html|md|txt)$/i
const CESIUM = /cesium/i

const strict = process.argv.includes('--strict')

function walk(dir) {
  let out = []
  let entries
  try { entries = readdirSync(dir) } catch { return out }
  for (const name of entries) {
    if (SKIP_DIRS.has(name)) continue
    const full = join(dir, name)
    const st = statSync(full)
    if (st.isDirectory()) out = out.concat(walk(full))
    else out.push(full)
  }
  return out
}

const files = [...SCAN_DIRS.flatMap(walk), ...SCAN_FILES]
const matches = []
for (const file of files) {
  if (!TEXT_EXT.test(file)) continue
  let content
  try { content = readFileSync(file, 'utf8') } catch { continue }
  if (CESIUM.test(content)) matches.push(relative(REPO_ROOT, file).split(sep).join('/'))
}
matches.sort()

let baseline = []
try {
  baseline = JSON.parse(readFileSync(join(HERE, 'cesium-baseline.json'), 'utf8')).allowed || []
} catch { baseline = [] }

if (strict) {
  if (matches.length === 0) {
    console.log('✅ [no-cesium --strict] cero rastro de Cesium.')
    process.exit(0)
  }
  console.error(`❌ [no-cesium --strict] ${matches.length} archivo(s) aún referencian Cesium:`)
  for (const m of matches) console.error('   ' + m)
  console.error('   → objetivo de MAP-CAMERA-RETIRE: 0.')
  process.exit(1)
}

const baseSet = new Set(baseline)
const creep = matches.filter((m) => !baseSet.has(m))
const stale = baseline.filter((b) => !matches.includes(b))

if (creep.length > 0) {
  console.error(`❌ [no-cesium] ${creep.length} archivo(s) NUEVOS con Cesium fuera del baseline.`)
  console.error('   Estamos migrando a MapLibre: NO agregues más Cesium.')
  for (const m of creep) console.error('   ' + m)
  process.exit(1)
}

console.log(`✅ [no-cesium] sin creep: ${matches.length} referencias, todas en el baseline (objetivo final: 0).`)
if (stale.length > 0) {
  console.log(`ℹ️  ${stale.length} entrada(s) del baseline ya no matchean — pódalas al migrar:`)
  for (const s of stale) console.log('   ' + s)
}
process.exit(0)
