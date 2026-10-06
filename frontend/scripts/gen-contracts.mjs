/**
 * Contrato backend → frontend (F4, S4.1).
 *
 *   npm run gen:contracts            # escribe src/contracts/{schema/*.json,generated.ts}
 *   npm run gen:contracts -- --check # falla si lo generado no coincide (CI / vitest)
 *
 * Fuente única: los JSON Schema que exporta el backend en `contracts/schema/`
 * (`python -m geo_copilot.platform.contracts.export`). De ellos salen:
 *   - los tipos TS (json-schema-to-typescript), y
 *   - la validación en tiempo de ejecución (zod `fromJSONSchema` sobre el MISMO
 *     schema copiado, ver src/contracts/index.ts) — sin un segundo modelo a mano.
 * Se copian dentro de `frontend/` porque la imagen del frontend solo lleva esa carpeta.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { compile } from 'json-schema-to-typescript'

const aqui = path.dirname(fileURLToPath(import.meta.url))
const ORIGEN = path.resolve(aqui, '..', '..', 'contracts', 'schema')
const DESTINO = path.resolve(aqui, '..', 'src', 'contracts')
const RAICES = ['query_response', 'layer_ref', 'artifact_bundle', 'map_action', 'geo_result']

const CABECERA = `
/**
 * GENERADO por scripts/gen-contracts.mjs desde contracts/schema/ — NO EDITAR.
 * Cambia el modelo pydantic, exporta (python -m geo_copilot.platform.contracts.export)
 * y regenera (npm run gen:contracts).
 */
`

/** Quita `title` salvo en las definiciones nombradas: evita alias como `Kind2 = "layer"`. */
function sinTitulos(nodo, esDefinicion = false) {
  if (Array.isArray(nodo)) return nodo.map((n) => sinTitulos(n))
  if (!nodo || typeof nodo !== 'object') return nodo
  const out = {}
  for (const [k, v] of Object.entries(nodo)) {
    if (k === 'title' && !esDefinicion) continue
    if (k === '$defs') {
      out[k] = Object.fromEntries(Object.entries(v).map(([n, d]) => [n, sinTitulos(d, true)]))
    } else if (k === 'properties') {
      // Las claves de `properties` son NOMBRES de campo: un campo que se llama
      // `title` (ChartSpec.title, TableOut.title) no es la anotación y se queda.
      out[k] = Object.fromEntries(Object.entries(v).map(([n, d]) => [n, sinTitulos(d)]))
    } else {
      out[k] = sinTitulos(v)
    }
  }
  return out
}

export async function generar() {
  const schemas = {}
  for (const nombre of RAICES) {
    const texto = fs.readFileSync(path.join(ORIGEN, `${nombre}.schema.json`), 'utf8').replace(/\r\n/g, '\n')
    schemas[nombre] = { texto, json: JSON.parse(texto) }
  }
  // Un solo documento: todas las $defs + cada raíz como definición con su título.
  const defs = {}
  for (const { json } of Object.values(schemas)) {
    Object.assign(defs, json.$defs ?? {})
    const { $defs: _d, $id: _i, ...raiz } = json
    defs[json.title] = raiz
  }
  const combinado = sinTitulos({ title: 'Contratos', type: 'object', $defs: defs }, true)
  const ts = await compile(combinado, 'Contratos', {
    bannerComment: '', additionalProperties: false, unreachableDefinitions: true,
    style: { singleQuote: true, semi: false },
  })
  const archivos = { 'generated.ts': CABECERA + ts.replace(/export interface Contratos \{\}\n?/, '') }
  for (const [nombre, { texto }] of Object.entries(schemas)) archivos[`schema/${nombre}.schema.json`] = texto
  // El ejemplo del backend del que parten los fixtures de test también va dentro de
  // frontend/: la imagen solo lleva esa carpeta y tsc compila src/ entero.
  archivos['examples/query_response.json'] = fs
    .readFileSync(path.join(ORIGEN, '..', 'examples', 'query_response.json'), 'utf8').replace(/\r\n/g, '\n')
  return archivos
}

async function main() {
  const check = process.argv.includes('--check')
  const archivos = await generar()
  const distintos = []
  for (const [rel, contenido] of Object.entries(archivos)) {
    const destino = path.join(DESTINO, rel)
    const actual = fs.existsSync(destino) ? fs.readFileSync(destino, 'utf8').replace(/\r\n/g, '\n') : null
    if (actual !== contenido) {
      distintos.push(rel)
      if (!check) {
        fs.mkdirSync(path.dirname(destino), { recursive: true })
        fs.writeFileSync(destino, contenido)
      }
    }
  }
  if (check && distintos.length) {
    console.error(`Contratos del frontend desactualizados: ${distintos.join(', ')}\n→ npm run gen:contracts`)
    process.exit(1)
  }
  if (!check) console.log(distintos.length ? `regenerados: ${distintos.join(', ')}` : 'al día')
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  await main()
}
