/**
 * El markdown que escribe el agente, en bloques y piezas para pintarlo con elementos de React.
 *
 * El LLM responde en markdown (negritas, listas, tablas): sin esto el chat mostraba los `**`
 * literales y juntaba las líneas en un solo párrafo (V5, auditoría F4). Solo lo que se usa en una
 * respuesta: títulos, listas, tablas, párrafos, **negrita** y `código`. Nada de HTML: el texto
 * nunca se inyecta como marcado (los enlaces al mapa `[[layer:…]]` siguen siendo de `referencias`).
 */
import { trocear, type Referencia } from './referencias'

export type Bloque =
  | { tipo: 'parrafo'; lineas: string[] }
  | { tipo: 'titulo'; nivel: number; texto: string }
  | { tipo: 'lista'; ordenada: boolean; items: string[] }
  | { tipo: 'tabla'; cabecera: string[]; filas: string[][] }

const TITULO = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/
const VINETA = /^\s*[-*•]\s+(.*)$/
const NUMERO = /^\s*\d+[.)]\s+(.*)$/
const FILA = /^\s*\|.*\|\s*$/
const SEPARADOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/

/** Un bloque leído desde la línea `i` y la línea donde sigue el texto. */
type Lectura = { bloque: Bloque; sigue: number }

function celdas(linea: string): string[] {
  return linea.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())
}

function empiezaTabla(lineas: string[], i: number): boolean {
  return FILA.test(lineas[i]) && SEPARADOR.test(lineas[i + 1] ?? '')
}

function leerTabla(lineas: string[], i: number): Lectura {
  const cabecera = celdas(lineas[i])
  const filas: string[][] = []
  let j = i + 2
  while (j < lineas.length && FILA.test(lineas[j])) filas.push(celdas(lineas[j++]))
  return { bloque: { tipo: 'tabla', cabecera, filas }, sigue: j }
}

function leerLista(lineas: string[], i: number, ordenada: boolean): Lectura {
  const patron = ordenada ? NUMERO : VINETA
  const items: string[] = []
  let j = i
  for (; j < lineas.length && lineas[j].trim(); j++) {
    const m = patron.exec(lineas[j])
    if (m) items.push(m[1])
    else if (items.length && /^\s{2,}\S/.test(lineas[j])) items[items.length - 1] += ` ${lineas[j].trim()}`
    else break
  }
  return { bloque: { tipo: 'lista', ordenada, items }, sigue: j }
}

function abreOtroBloque(lineas: string[], j: number): boolean {
  const l = lineas[j]
  return TITULO.test(l) || VINETA.test(l) || NUMERO.test(l) || empiezaTabla(lineas, j)
}

function leerParrafo(lineas: string[], i: number): Lectura {
  const parrafo = [lineas[i]]
  let j = i + 1
  while (j < lineas.length && lineas[j].trim() && !abreOtroBloque(lineas, j)) parrafo.push(lineas[j++])
  return { bloque: { tipo: 'parrafo', lineas: parrafo }, sigue: j }
}

function leerBloque(lineas: string[], i: number): Lectura {
  const t = TITULO.exec(lineas[i])
  if (t) return { bloque: { tipo: 'titulo', nivel: t[1].length, texto: t[2] }, sigue: i + 1 }
  if (empiezaTabla(lineas, i)) return leerTabla(lineas, i)
  if (VINETA.test(lineas[i])) return leerLista(lineas, i, false)
  if (NUMERO.test(lineas[i])) return leerLista(lineas, i, true)
  return leerParrafo(lineas, i)
}

/** El texto en bloques: una línea en blanco separa párrafos; listas, tablas y títulos van aparte. */
export function bloques(texto: string): Bloque[] {
  const lineas = texto.replace(/\r\n?/g, '\n').split('\n')
  const out: Bloque[] = []
  let i = 0
  while (i < lineas.length) {
    if (!lineas[i].trim()) { i++; continue }
    const { bloque, sigue } = leerBloque(lineas, i)
    out.push(bloque)
    i = sigue
  }
  return out
}

export interface Pieza {
  texto: string
  ref?: Referencia
  negrita: boolean
  codigo: boolean
}

type Marca = '**' | '`'
const MARCA = /(\*\*|`)/

/** Cuántas marcas de cada tipo hay fuera de los enlaces (la última impar no abre nada). */
function contarMarcas(trozos: ReturnType<typeof trocear>): Record<Marca, number> {
  const total: Record<Marca, number> = { '**': 0, '`': 0 }
  for (const t of trozos) {
    if (t.ref) continue
    for (const p of t.texto.split(MARCA)) if (p === '**' || p === '`') total[p]++
  }
  return total
}

/**
 * Una línea en piezas con su formato. `**` y `` ` `` abren y cierran; uno sin pareja se queda
 * como texto. Los enlaces al mapa toman el formato que los rodea (`**[[layer:…|lotes]]**`).
 */
export function piezas(linea: string): Pieza[] {
  const trozos = trocear(linea)
  const total = contarMarcas(trozos)
  const vistas: Record<Marca, number> = { '**': 0, '`': 0 }
  const estado = { negrita: false, codigo: false }
  const out: Pieza[] = []
  const agregar = (texto: string, ref?: Referencia) => {
    if (!texto && !ref) return
    const previa = out[out.length - 1]
    if (!ref && previa && !previa.ref && previa.negrita === estado.negrita && previa.codigo === estado.codigo) {
      previa.texto += texto
    } else {
      out.push({ texto, ...(ref ? { ref } : {}), ...estado })
    }
  }
  // ¿la marca cambia el formato? (dentro de `código`, un ** es texto)
  const conmuta = (m: Marca): boolean => {
    const clave = m === '**' ? 'negrita' : 'codigo'
    if (m === '**' && estado.codigo) return false
    vistas[m]++
    if (!estado[clave] && vistas[m] > total[m] - (total[m] % 2)) return false
    estado[clave] = !estado[clave]
    return true
  }
  for (const t of trozos) {
    if (t.ref) { agregar(t.texto, t.ref); continue }
    for (const p of t.texto.split(MARCA)) {
      if ((p === '**' || p === '`') && conmuta(p)) continue
      agregar(p)
    }
  }
  return out
}
