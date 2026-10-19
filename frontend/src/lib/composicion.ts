/**
 * Composición de impresión: el mapa tal como se ve, con título, leyenda, norte, escala (gráfica y
 * numérica), fuentes, sistema de referencia y fecha, en una página A4 o Carta horizontal a
 * 150 ppp. Sale como PNG o como PDF (lib/pdfMinimo).
 *
 * El lienzo de WebGL se copia justo tras pintarlo: fuera de ese instante el búfer ya se borró
 * (sin `preserveDrawingBuffer`, que penaliza todo el pintado).
 */
import type * as maplibregl from 'maplibre-gl'

import { type Leyenda, leyendaDe } from '@/lib/leyenda'
import { type Pagina, PAGINAS, pdfConImagen } from '@/lib/pdfMinimo'
import type { MapLayer } from '@/stores'

const PPP = 150
const MARGEN = 40
const COLUMNA = 380
const CABECERA = 120
const TINTA = '#1f2a24'
const GRIS = '#6b716a'

export interface OpcionesComposicion {
  titulo: string
  subtitulo?: string
  autor?: string
  pagina: Pagina
}

/** Tamaño de la página en píxeles a 150 ppp. */
export function tamanoPagina(pagina: Pagina): { ancho: number; alto: number } {
  const p = PAGINAS[pagina]
  return { ancho: Math.round((p.ancho / 72) * PPP), alto: Math.round((p.alto / 72) * PPP) }
}

/** Una longitud «redonda» (1, 2 o 5 × 10ⁿ) que no pase de `maximo` metros. */
export function longitudRedonda(maximo: number): number {
  const exp = 10 ** Math.floor(Math.log10(maximo))
  for (const m of [5, 2, 1]) if (m * exp <= maximo) return m * exp
  return exp
}

export function textoDistancia(m: number): string {
  return m >= 1000 ? `${(m / 1000).toLocaleString('es')} km` : `${m.toLocaleString('es')} m`
}

/** Metros por píxel CSS del mapa en su centro (teselas de 512 de MapLibre). */
export function metrosPorPixel(lat: number, zoom: number): number {
  return (40075016.686 * Math.cos((lat * Math.PI) / 180)) / (512 * 2 ** zoom)
}

/** Copia el lienzo del mapa recién pintado: `redraw()` pinta en el acto y la copia se hace en la
 * misma tarea, antes de que el navegador presente el fotograma y borre el búfer. Así no depende
 * del siguiente `requestAnimationFrame`, que no llega si la pestaña no está a la vista. */
export function capturarMapa(map: maplibregl.Map): HTMLCanvasElement {
  map.redraw()
  const src = map.getCanvas()
  const copia = document.createElement('canvas')
  copia.width = src.width
  copia.height = src.height
  copia.getContext('2d')?.drawImage(src, 0, 0)
  return copia
}

/** Las fuentes de los datos: el atribuido de cada fuente del mapa, sin HTML. */
export function fuentesDe(map: maplibregl.Map): string[] {
  const out = new Set<string>()
  const fuentes = map.getStyle()?.sources ?? {}
  for (const s of Object.values(fuentes)) {
    const a = (s as { attribution?: string }).attribution
    if (a) out.add(a.replace(/<[^>]+>/g, '').replace(/&copy;/g, '©').trim())
  }
  return [...out].filter(Boolean)
}

function envolver(ctx: CanvasRenderingContext2D, texto: string, ancho: number): string[] {
  const lineas: string[] = []
  let actual = ''
  for (const palabra of texto.split(/\s+/)) {
    const prueba = actual ? `${actual} ${palabra}` : palabra
    if (ctx.measureText(prueba).width > ancho && actual) {
      lineas.push(actual)
      actual = palabra
    } else actual = prueba
  }
  if (actual) lineas.push(actual)
  return lineas
}

/** Dibuja una leyenda a partir de `y`; devuelve la `y` siguiente (o null si ya no cabe). */
function dibujarLeyenda(ctx: CanvasRenderingContext2D, ley: Leyenda, x: number, y: number, ancho: number,
                        limite: number): number | null {
  ctx.fillStyle = TINTA
  ctx.font = 'bold 17px system-ui, sans-serif'
  for (const l of envolver(ctx, ley.titulo, ancho)) {
    if (y + 20 > limite) return null
    ctx.fillText(l, x, y + 16)
    y += 22
  }
  ctx.font = '15px system-ui, sans-serif'
  for (const f of ley.filas ?? []) {
    if (y + 22 > limite) return null
    ctx.fillStyle = f.color
    ctx.fillRect(x, y + 3, 18, 14)
    ctx.strokeStyle = '#0004'
    ctx.strokeRect(x, y + 3, 18, 14)
    ctx.fillStyle = TINTA
    const etiqueta = f.count != null ? `${f.label} (${f.count.toLocaleString('es')})` : f.label
    ctx.fillText(envolver(ctx, etiqueta, ancho - 28)[0] ?? '', x + 28, y + 15)
    y += 22
  }
  if (ley.rampa) {
    if (y + 44 > limite) return null
    const g = ctx.createLinearGradient(x, 0, x + ancho, 0)
    ley.rampa.colores.forEach((c, i, a) => g.addColorStop(a.length > 1 ? i / (a.length - 1) : 0, c))
    ctx.fillStyle = g
    ctx.fillRect(x, y + 2, ancho, 14)
    ctx.fillStyle = GRIS
    ctx.font = '13px system-ui, sans-serif'
    if (ley.rampa.min != null) ctx.fillText(String(ley.rampa.min), x, y + 32)
    if (ley.rampa.max != null) {
      const t = String(ley.rampa.max)
      ctx.fillText(t, x + ancho - ctx.measureText(t).width, y + 32)
    }
    y += 40
  }
  if (ley.nota) {
    ctx.fillStyle = GRIS
    ctx.font = 'italic 13px system-ui, sans-serif'
    for (const l of envolver(ctx, ley.nota, ancho)) {
      if (y + 18 > limite) return null
      ctx.fillText(l, x, y + 13)
      y += 17
    }
  }
  return y + 12
}

function dibujarNorte(ctx: CanvasRenderingContext2D, cx: number, cy: number, rumbo: number) {
  ctx.save()
  ctx.translate(cx, cy)
  ctx.rotate((-rumbo * Math.PI) / 180)
  ctx.beginPath()
  ctx.moveTo(0, -30)
  ctx.lineTo(14, 18)
  ctx.lineTo(0, 8)
  ctx.closePath()
  ctx.fillStyle = TINTA
  ctx.fill()
  ctx.beginPath()
  ctx.moveTo(0, -30)
  ctx.lineTo(-14, 18)
  ctx.lineTo(0, 8)
  ctx.closePath()
  ctx.strokeStyle = TINTA
  ctx.lineWidth = 2
  ctx.stroke()
  ctx.fillStyle = TINTA
  ctx.font = 'bold 18px system-ui, sans-serif'
  ctx.textAlign = 'center'
  ctx.fillText('N', 0, -36)
  ctx.restore()
}

/** Escala gráfica de `metrosPorPx` (de la página) bajo el mapa; devuelve el texto 1:N. */
function dibujarEscala(ctx: CanvasRenderingContext2D, x: number, y: number, metrosPorPx: number, anchoMax: number): string {
  const metros = longitudRedonda(metrosPorPx * anchoMax)
  const px = metros / metrosPorPx
  ctx.fillStyle = TINTA
  ctx.fillRect(x, y, px / 2, 8)
  ctx.strokeStyle = TINTA
  ctx.lineWidth = 1.5
  ctx.strokeRect(x, y, px, 8)
  ctx.font = '14px system-ui, sans-serif'
  ctx.fillText('0', x - 4, y + 26)
  const t = textoDistancia(metros)
  ctx.fillText(t, x + px - ctx.measureText(t).width / 2, y + 26)
  const denominador = Math.round(metrosPorPx / (0.0254 / PPP))
  const redondo = 10 ** Math.max(0, Math.floor(Math.log10(denominador)) - 1)
  return `1:${(Math.round(denominador / redondo) * redondo).toLocaleString('es')}`
}

/** La página entera en un canvas. */
export async function componer(map: maplibregl.Map, capas: MapLayer[], op: OpcionesComposicion): Promise<HTMLCanvasElement> {
  const { ancho, alto } = tamanoPagina(op.pagina)
  const pagina = document.createElement('canvas')
  pagina.width = ancho
  pagina.height = alto
  const ctx = pagina.getContext('2d')
  if (!ctx) throw new Error('El navegador no permite componer la imagen.')
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, ancho, alto)

  // cabecera
  ctx.fillStyle = TINTA
  ctx.font = 'bold 34px system-ui, sans-serif'
  ctx.fillText(envolver(ctx, op.titulo || 'Mapa', ancho - 2 * MARGEN)[0] ?? '', MARGEN, MARGEN + 34)
  if (op.subtitulo) {
    ctx.fillStyle = GRIS
    ctx.font = '19px system-ui, sans-serif'
    ctx.fillText(envolver(ctx, op.subtitulo, ancho - 2 * MARGEN)[0] ?? '', MARGEN, MARGEN + 66)
  }

  // el mapa, entero dentro de su marco (centrado a lo ancho)
  const marco = { x: MARGEN, y: CABECERA, w: ancho - 2 * MARGEN - COLUMNA - 24, h: alto - CABECERA - MARGEN - 30 }
  const img = capturarMapa(map)
  const k = Math.min(marco.w / img.width, marco.h / img.height)
  const w = img.width * k
  const h = img.height * k
  const ox = marco.x + (marco.w - w) / 2
  const oy = marco.y   // arriba, bajo el título: el blanco que sobre queda al pie
  ctx.drawImage(img, ox, oy, w, h)
  ctx.strokeStyle = TINTA
  ctx.lineWidth = 1.5
  ctx.strokeRect(ox, oy, w, h)

  // escala (los píxeles del lienzo son físicos: devicePixelRatio)
  const cssPorPx = map.getCanvas().clientWidth / img.width
  const metrosPorPx = (metrosPorPixel(map.getCenter().lat, map.getZoom()) * cssPorPx) / k
  const numerica = dibujarEscala(ctx, ox, oy + h + 12, metrosPorPx, Math.min(260, w / 3))
  ctx.fillStyle = TINTA
  ctx.font = '14px system-ui, sans-serif'
  ctx.fillText(`Escala aprox. ${numerica} (en ${PAGINAS[op.pagina].nombre})`, ox + Math.min(260, w / 3) + 40, oy + h + 26)

  // columna: norte, leyenda, pie
  const cx = ancho - MARGEN - COLUMNA
  dibujarNorte(ctx, cx + COLUMNA - 30, CABECERA + 50, map.getBearing())
  let y = CABECERA
  ctx.fillStyle = TINTA
  ctx.font = 'bold 20px system-ui, sans-serif'
  ctx.fillText('Leyenda', cx, y + 20)
  y += 40
  const pie = alto - MARGEN - 150
  const leyendas = [...capas].reverse().map(leyendaDe).filter((l): l is Leyenda => !!l)
  let fuera = 0
  for (const ley of leyendas) {
    const sig = dibujarLeyenda(ctx, ley, cx, y, COLUMNA - 70, pie)
    if (sig == null) fuera += 1
    else y = sig
  }
  if (fuera) {
    ctx.fillStyle = GRIS
    ctx.font = 'italic 14px system-ui, sans-serif'
    ctx.fillText(`… y ${fuera} capa${fuera > 1 ? 's' : ''} más`, cx, Math.min(y, pie) + 14)
  }

  ctx.fillStyle = GRIS
  ctx.font = '13px system-ui, sans-serif'
  const lineas = [
    ...(op.autor ? [`Elaboró: ${op.autor}`] : []),
    `Fecha: ${new Date().toLocaleDateString('es', { year: 'numeric', month: 'long', day: 'numeric' })}`,
    'Proyección del mapa: Web Mercator (EPSG:3857)',
    ...fuentesDe(map).map((f) => `Fuente: ${f}`),
    'Hecho con Geo Copilot',
  ]
  let yp = pie + 20
  for (const l of lineas) {
    for (const t of envolver(ctx, l, COLUMNA)) {
      if (yp > alto - MARGEN) break
      ctx.fillText(t, cx, yp)
      yp += 17
    }
  }
  return pagina
}

/** Descarga la composición como PNG o PDF. */
export async function exportarComposicion(map: maplibregl.Map, capas: MapLayer[], op: OpcionesComposicion,
                                          formato: 'png' | 'pdf'): Promise<string> {
  const lienzo = await componer(map, capas, op)
  const base = (op.titulo || 'mapa').normalize('NFKD').replace(/[̀-ͯ]/g, '')
    .replace(/[^\w]+/g, '_').replace(/^_|_$/g, '').toLowerCase() || 'mapa'
  const blob = formato === 'png'
    ? await new Promise<Blob>((ok, mal) => lienzo.toBlob((b) => (b ? ok(b) : mal(new Error('sin imagen'))), 'image/png'))
    : await new Promise<Blob>((ok, mal) => lienzo.toBlob(async (b) => {
      if (!b) return mal(new Error('sin imagen'))
      ok(pdfConImagen(new Uint8Array(await b.arrayBuffer()), lienzo.width, lienzo.height, op.pagina))
    }, 'image/jpeg', 0.92))
  const archivo = `${base}.${formato}`
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = archivo
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  return archivo
}
