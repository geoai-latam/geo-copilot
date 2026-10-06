/**
 * FH.1 — el mapa compartido: UN reducer para todas las operaciones sobre las
 * capas, las haga el usuario (panel de capas, Ctrl+Z) o el agente (órdenes
 * `map_command`, capas nuevas, re-estilos). Todas quedan en un registro con su
 * autor y su inversa, y se deshacen igual.
 *
 * La inversa es POR OPERACIÓN (el valor anterior del atributo, la posición
 * anterior, la capa quitada), no una foto del mapa: deshacer una acción no pisa
 * lo que pasó después sobre otras capas.
 *
 * El registro también es lo que ve el agente: `accionesDesdeUltimoTurno()` viaja
 * en el `map_context` (quién hizo qué, y si se deshizo).
 */
import { create } from 'zustand'

import type { MapCommand } from '@/contracts'
import { useMapStore, type MapLayer } from '@/stores/mapStore'
import type { GeoJSONFeatureCollection, LayerSymbology } from '@/types'
import { combinar, cumpleTodas, idsQueCumplen, type SeleccionCapa } from '@/lib/seleccion'

export type Autor = 'user' | 'agent'

/** Operaciones del registro: las órdenes del contrato + añadir/sustituir capa. */
export type Operacion =
  | MapCommand
  | { op: 'add_layer'; layer_id: string; args?: { dibujo?: string }; reason?: string | null }
  | { op: 'replace_layer'; layer_id: string; previa: MapLayer; reason?: string | null }
  // FH.3: el nombre y los vértices viven en el workspace (los ve el agente por ahí
  // también): no se deshacen con Ctrl+Z, que solo movería el mapa y no el dataset.
  | { op: 'rename_layer'; layer_id: string; args: { nombre: string; antes: string }; reason?: string | null }
  | { op: 'edit_geometry'; layer_id: string; data: GeoJSONFeatureCollection; reason?: string | null }

export interface Entrada {
  id: number
  op: Operacion['op']
  layer_id: string | null
  layer_name: string | null
  args: Record<string, unknown>
  author: Autor
  at: Date
  reason: string | null
  /** Devuelve el mapa como estaba antes de esta operación (sobre las capas de AHORA). */
  inversa: ((layers: MapLayer[]) => MapLayer[]) | null
  /** Rehace la operación (sobre las capas de AHORA). */
  directa: ((layers: MapLayer[]) => MapLayer[]) | null
  deshecha: boolean
  deshechaEn: Date | null
}

/** Ventana en la que gestos iguales seguidos (arrastrar un slider) son UNA entrada. */
const AGRUPAR_MS = 1500

const conAtributos = (layers: MapLayer[], id: string, cambio: Partial<MapLayer>) =>
  layers.map((l) => (l.id === id ? { ...l, ...cambio } : l))

const colorDe = (s: LayerSymbology | undefined, fallback: string) =>
  s?.fill?.color || s?.stroke?.color || s?.marker?.color || fallback

function mover(layers: MapLayer[], id: string, destino: number): MapLayer[] {
  const from = layers.findIndex((l) => l.id === id)
  if (from === -1) return layers
  const to = Math.max(0, Math.min(layers.length - 1, destino))
  if (to === from) return layers
  const next = [...layers]
  const [capa] = next.splice(from, 1)
  next.splice(to, 0, capa)
  return next
}

/** Índice de destino de un `reorder` en el orden de dibujo (0 = abajo). */
function destinoDe(layers: MapLayer[], id: string, to: string, relativeTo?: string | null): number {
  const sin = layers.filter((l) => l.id !== id)
  if (to === 'top') return layers.length - 1
  if (to === 'bottom') return 0
  const ref = sin.findIndex((l) => l.id === relativeTo)
  if (ref === -1) return layers.findIndex((l) => l.id === id)
  return to === 'above' ? ref + 1 : ref
}

/** El valor de un ajuste del estilo (para saber si un fijado sigue en pie). */
function valorDe(s: LayerSymbology | undefined, campo: string): unknown {
  if (!s) return undefined
  if (campo === 'fill_color') return s.fill?.color
  return (s as unknown as Record<string, unknown>)[campo]
}

/**
 * FH.6: lo que el usuario fijó a mano. Un estilo que ya trae `pinned` (el editor) manda;
 * uno que no (el del agente) conserva los fijados cuyo valor NO cambió. Si el agente
 * cambió la rampa (porque se lo pidieron), ese fijado ya no está en pie.
 */
export function conFijados(nuevo: LayerSymbology, previo: LayerSymbology | undefined): LayerSymbology {
  if (nuevo.pinned?.length) return nuevo
  const siguen = (previo?.pinned ?? []).filter((c) => JSON.stringify(valorDe(nuevo, c)) === JSON.stringify(valorDe(previo, c)))
  return siguen.length ? { ...nuevo, pinned: siguen } : nuevo
}

/**
 * El reducer: aplica una orden a las capas. Puro (no toca stores). Devuelve las
 * capas nuevas y la inversa para deshacer. `zoom_to` no cambia capas.
 */
export function reducir( // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  layers: MapLayer[], cmd: Operacion,
): { layers: MapLayer[]; inversa: Entrada['inversa'] } {
  const id = cmd.layer_id ?? ''
  const antes = layers.find((l) => l.id === id)
  const restaurar = (campos: (keyof MapLayer)[]) => (ls: MapLayer[]) =>
    antes ? conAtributos(ls, id, Object.fromEntries(campos.map((c) => [c, antes[c]]))) : ls

  switch (cmd.op) {
    case 'set_visibility':
      if (!antes) return { layers, inversa: null }
      return { layers: conAtributos(layers, id, { visible: cmd.args.visible }), inversa: restaurar(['visible']) }
    case 'set_opacity':
      if (!antes) return { layers, inversa: null }
      return { layers: conAtributos(layers, id, { opacity: cmd.args.opacity }), inversa: restaurar(['opacity']) }
    case 'set_label':
      if (!antes) return { layers, inversa: null }
      return { layers: conAtributos(layers, id, { labelField: cmd.args.field ?? null }), inversa: restaurar(['labelField']) }
    case 'set_style': {
      if (!antes) return { layers, inversa: null }
      const symbology = conFijados(cmd.args.style as unknown as LayerSymbology, antes.symbology)
      return {
        layers: conAtributos(layers, id, { symbology, color: colorDe(symbology, antes.color) }),
        inversa: restaurar(['symbology', 'color']),
      }
    }
    case 'reorder': {
      const desde = layers.findIndex((l) => l.id === id)
      if (desde === -1) return { layers, inversa: null }
      const nuevas = mover(layers, id, destinoDe(layers, id, cmd.args.to, cmd.args.relative_to))
      return { layers: nuevas, inversa: (ls) => mover(ls, id, desde) }
    }
    case 'remove_layer': {
      const desde = layers.findIndex((l) => l.id === id)
      if (desde === -1 || !antes) return { layers, inversa: null }
      return {
        layers: layers.filter((l) => l.id !== id),
        inversa: (ls) => (ls.some((l) => l.id === id) ? ls : [...ls.slice(0, desde), antes, ...ls.slice(desde)]),
      }
    }
    case 'add_layer':
      // La capa ya la añadió el store (con su id); deshacer = quitarla.
      return { layers, inversa: (ls) => ls.filter((l) => l.id !== id) }
    case 'replace_layer': {
      // Re-estilo EN SU SITIO de datos nuevos (`replaces`): la capa nueva ocupa el
      // lugar de `previa`; deshacer vuelve a poner `previa` ahí.
      const previa = cmd.previa
      return {
        layers,
        inversa: (ls) => ls.map((l) => (l.id === id ? previa : l)),
      }
    }
    case 'select': {
      // FH.2: la selección es un atributo de la capa; deshacer devuelve la anterior.
      if (!antes) return { layers, inversa: null }
      const a = cmd.args
      let sel: SeleccionCapa | null
      const enMemoria = antes.kind === 'vector-geojson'
      if (a.where && !enMemoria) {
        // Teselas: la condición se resalta con un filtro; el recuento lo da quien selecciona.
        sel = { where: a.where, count: a.count ?? 0, origin: a.origin }
      } else {
        const nuevos = a.where ? idsQueCumplen(antes, a.where) : (a.ids ?? [])
        const ids = combinar(antes.seleccion?.ids, nuevos, a.mode)
        sel = ids.length ? { ids, count: ids.length, origin: a.origin } : null
      }
      return { layers: conAtributos(layers, id, { seleccion: sel }), inversa: restaurar(['seleccion']) }
    }
    case 'set_filter': {
      // FH.5: la capa pasa a ser su subconjunto (y la selección, lo que quede visible).
      if (!antes) return { layers, inversa: null }
      const where = cmd.args.where?.length ? cmd.args.where : null
      const cuenta = where && antes.kind === 'vector-geojson'
        ? (antes.data.features ?? []).filter((f) => cumpleTodas(where, (f.properties ?? {}) as Record<string, unknown>)).length
        : (where ? cmd.args.count ?? null : null)
      return {
        layers: conAtributos(layers, id, { filtro: where, filtroCount: cuenta }),
        inversa: restaurar(['filtro', 'filtroCount']),
      }
    }
    case 'clear_selection': {
      const afectadas = layers.filter((l) => l.seleccion && (!cmd.layer_id || l.id === cmd.layer_id))
      if (!afectadas.length) return { layers, inversa: null }
      const previas = new Map(afectadas.map((l) => [l.id, l.seleccion]))
      return {
        layers: layers.map((l) => (previas.has(l.id) ? { ...l, seleccion: null } : l)),
        inversa: (ls) => ls.map((l) => (previas.has(l.id) ? { ...l, seleccion: previas.get(l.id) } : l)),
      }
    }
    case 'rename_layer':
      return { layers: conAtributos(layers, id, { name: cmd.args.nombre }), inversa: null }
    case 'edit_geometry':
      return { layers: conAtributos(layers, id, { data: cmd.data }), inversa: null }
    case 'zoom_to':
    default:
      return { layers, inversa: null }
  }
}

/** Argumentos compactos para el registro (lo que ve el agente). Un estilo, resumido. */
/** Un estilo en una línea («graduated_colors por lotupredia, 6 clases, Purples»). */
function resumenEstilo(s: LayerSymbology | undefined): string {
  if (!s) return 'sin estilo'
  return [
    s.symbology_type + (s.classification_field ? ` por ${s.classification_field}` : ''),
    s.class_breaks?.length ? `${s.class_breaks.length} clases` : null,
    s.color_scheme ?? null,
  ].filter(Boolean).join(', ')
}

function argsParaRegistro(cmd: Operacion, despues?: MapLayer, antes?: MapLayer): Record<string, unknown> { // eslint-disable-line complexity -- deuda congelada (F1): ESLint 10 suma `?.` y defaults; partir, no subir
  // EH.6 (V5): un re-estilo (`set_style`, o `replace_layer` con datos nuevos) que no dice QUÉ
  // estilo puso y cuál quitó deja al agente, tras un Ctrl+Z, adivinando qué se deshizo (y mal).
  if (cmd.op === 'replace_layer') {
    return despues?.symbology || cmd.previa.symbology
      ? { estilo: resumenEstilo(despues?.symbology), antes: resumenEstilo(cmd.previa.symbology) }
      : {}
  }
  if (cmd.op === 'set_style') {
    return { estilo: resumenEstilo(despues?.symbology), antes: resumenEstilo(antes?.symbology) }
  }
  if (cmd.op === 'add_layer') return { ...(cmd.args ?? {}) }
  if (cmd.op === 'edit_geometry') return {}
  if (cmd.op === 'set_filter') {
    return { where: (cmd.args.where ?? []).map((c) => `${c.field} ${c.op} ${Array.isArray(c.value) ? c.value.join('|') : c.value}`).join(' y ') || null }
  }
  if (cmd.op === 'select') {
    const a = cmd.args
    return { origin: a.origin, mode: a.mode, ...(a.where ? { where: `${a.where.field} ${a.where.op} ${a.where.value}` } : {}),
             ...(a.ids ? { ids: a.ids.length } : {}) }
  }
  return { ...(cmd.args as Record<string, unknown>) }
}

interface OperacionesState {
  registro: Entrada[]
  /** Ids de entradas deshechas que se pueden rehacer (la última, arriba). */
  rehacer: number[]
  /** Cuándo se envió la última consulta: lo posterior es «desde tu última respuesta». */
  marcaTurno: Date | null
  ejecutar: (cmd: Operacion, author: Autor) => Entrada | null
  deshacer: () => Entrada | null
  rehacerUltima: () => Entrada | null
  marcarTurno: () => void
  limpiar: () => void
}

let secuencia = 0

export const useOperaciones = create<OperacionesState>((set, get) => ({
  registro: [],
  rehacer: [],
  marcaTurno: null,

  ejecutar: (cmd, author) => { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
    const map = useMapStore.getState()
    const capaAntes = map.layers.find((l) => l.id === cmd.layer_id)
    const { layers, inversa } = reducir(map.layers, cmd)
    if (layers !== map.layers) useMapStore.setState({ layers })
    if (cmd.op === 'zoom_to') {
      if (cmd.layer_id) map.flyToLayer(cmd.layer_id)
      else if (cmd.args.bbox) map.pedirEncuadre(cmd.args.bbox as [number, number, number, number])
    }
    const ahora = new Date()
    const reg = get().registro
    const ultima = reg[reg.length - 1]
    // Arrastrar un slider = muchos set_opacity seguidos: una sola entrada, que
    // conserva la inversa de la PRIMERA (deshacer vuelve al valor de antes de arrastrar).
    if (ultima && !ultima.deshecha && ultima.op === cmd.op && ultima.layer_id === (cmd.layer_id ?? null)
        && ultima.author === author && !['reorder', 'zoom_to', 'select', 'clear_selection'].includes(cmd.op)
        && ahora.getTime() - ultima.at.getTime() < AGRUPAR_MS) {
      const args = argsParaRegistro(cmd, layers.find((l) => l.id === cmd.layer_id), capaAntes)
      // la inversa es la de la PRIMERA: el «antes» también
      if ('antes' in ultima.args && 'antes' in args) args.antes = ultima.args.antes
      const fusion = { ...ultima, args, at: ahora, directa: (ls: MapLayer[]) => reducir(ls, cmd).layers }
      set({ registro: [...reg.slice(0, -1), fusion], rehacer: [] })
      return fusion
    }
    const entrada: Entrada = {
      id: ++secuencia,
      op: cmd.op,
      layer_id: cmd.layer_id ?? null,
      layer_name: capaAntes?.name ?? layers.find((l) => l.id === cmd.layer_id)?.name ?? null,
      args: argsParaRegistro(cmd, layers.find((l) => l.id === cmd.layer_id), capaAntes),
      author,
      at: ahora,
      reason: ('reason' in cmd ? cmd.reason : null) ?? null,
      inversa,
      directa: ['add_layer', 'replace_layer', 'rename_layer', 'edit_geometry'].includes(cmd.op)
        ? null : (ls) => reducir(ls, cmd).layers,
      deshecha: false,
      deshechaEn: null,
    }
    set({ registro: [...reg, entrada], rehacer: [] })
    return entrada
  },

  deshacer: () => {
    const reg = get().registro
    const idx = [...reg].reverse().findIndex((e) => !e.deshecha && e.inversa)
    if (idx === -1) return null
    const i = reg.length - 1 - idx
    const e = reg[i]
    const antes = useMapStore.getState().layers
    useMapStore.setState({ layers: e.inversa!(antes) })
    // E2E FH.13: rehacer un reemplazo (o un añadir) no hacía nada (`directa` nula). Deshacer y
    // rehacer son LIFO (cualquier operación nueva vacía la pila de rehacer), así que rehacerla es
    // exactamente volver al mapa de justo antes de deshacerla.
    const deshecha = { ...e, deshecha: true, deshechaEn: new Date(), ...(e.directa ? {} : { directa: () => antes }) }
    set({ registro: reg.map((x, j) => (j === i ? deshecha : x)), rehacer: [...get().rehacer, e.id] })
    return deshecha
  },

  rehacerUltima: () => {
    const pila = get().rehacer
    const id = pila[pila.length - 1]
    const reg = get().registro
    const i = reg.findIndex((e) => e.id === id)
    if (i === -1 || !reg[i].directa) {
      set({ rehacer: pila.slice(0, -1) })
      return null
    }
    const e = reg[i]
    useMapStore.setState({ layers: e.directa!(useMapStore.getState().layers) })
    const rehecha = { ...e, deshecha: false, deshechaEn: null, at: new Date() }
    set({ registro: reg.map((x, j) => (j === i ? rehecha : x)), rehacer: pila.slice(0, -1) })
    return rehecha
  },

  marcarTurno: () => set({ marcaTurno: new Date() }),
  limpiar: () => set({ registro: [], rehacer: [], marcaTurno: null }),
}))

/**
 * Lo que el agente debe saber del mapa desde su última respuesta: operaciones
 * nuevas y deshacer de operaciones anteriores (su simbología deshecha con Ctrl+Z).
 */
export function accionesDesdeUltimoTurno(max = 30) {
  const { registro, marcaTurno } = useOperaciones.getState()
  const desde = marcaTurno?.getTime() ?? 0
  return registro
    .filter((e) => e.at.getTime() > desde || (e.deshechaEn && e.deshechaEn.getTime() > desde))
    .slice(-max)
    .map((e) => ({
      op: e.op === 'replace_layer' ? 'set_style' : e.op,
      layer_id: e.layer_id,
      layer_name: e.layer_name,
      args: e.args,
      at: e.at.toISOString(),
      author: e.author,
      undone: e.deshecha,
    }))
}

/** Una capa que el USUARIO trajo desde un panel (Discovery, Herramientas, BD): al registro. */
export function capaDelUsuario(id: string | null | undefined, args?: { dibujo?: string }): string | null | undefined {
  if (id) useOperaciones.getState().ejecutar({ op: 'add_layer', layer_id: id, ...(args ? { args } : {}) }, 'user')
  return id
}

/** «Limpiar» el mapa: una operación por capa, así se puede deshacer y el agente lo ve. */
export function quitarTodas(author: Autor) {
  for (const l of [...useMapStore.getState().layers]) {
    useOperaciones.getState().ejecutar({ op: 'remove_layer', layer_id: l.id, args: {}, reason: null }, author)
  }
}

// Oráculo para E2E y depuración (solo DEV/E2E, como `__mlmap`): el registro del mapa.
if (typeof window !== 'undefined' && (import.meta.env.DEV || import.meta.env.VITE_E2E === 'true')) {
  (window as unknown as { __operaciones?: typeof useOperaciones }).__operaciones = useOperaciones
}
