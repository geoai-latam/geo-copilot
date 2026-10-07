import { describe, expect, it } from 'vitest'
import proj4 from 'proj4'

import { componer, plantillaCog, rampa, rejilla, type CogSpec } from './cogNavegador'

const C1 = { escala: 0.0001, offset: -0.1 }
const pix = (out: Uint8ClampedArray, k = 0) => Array.from(out.slice(k * 4, k * 4 + 4))

describe('cogNavegador: lo mismo que el servidor, en el navegador', () => {
  it('rgb: reflectancia con su rango por canal; el nodata queda transparente', () => {
    const cog: CogSpec = { version: 1, tipo: 'rgb', nodata: 0, rangos: [[0, 0.1], [0, 0.2], [0, 0.4]],
                           bandas: [{ url: 'r', ...C1 }, { url: 'g', ...C1 }, { url: 'b', ...C1 }] }
    const out = componer(cog, (k) => (k === 0 ? [2000, 2000, 2000] : [0, 0, 0]), null)    // 0,1 de reflectancia
    expect(pix(out, 0)).toEqual([255, 128, 64, 255])
    expect(pix(out, 1)[3]).toBe(0)
  })

  it('índice: (a−b)/(a+b) en su rampa y la máscara de nubes de la SCL', () => {
    const cog: CogSpec = { version: 1, tipo: 'indice', rangos: [[-1, 1]], colores: ['#000000', '#ffffff'],
                           bandas: [{ url: 'nir', ...C1 }, { url: 'red', ...C1 }], mascara: { url: 'scl', excluir: [9] } }
    // nir 0,3 y red 0,1 → NDVI 0,5 → 75 % de la rampa
    const out = componer(cog, () => [4000, 2000], (k) => (k === 1 ? 9 : 4))
    expect(pix(out, 0)).toEqual([191, 191, 191, 255])
    expect(pix(out, 1)[3]).toBe(0)                                     // nube alta: fuera
  })

  it('clases: la paleta de la SCL; rgb8 (el TCI) tal cual', () => {
    const scl = componer({ version: 1, tipo: 'clases', bandas: [{ url: 's' }], clases: { 6: '#0000ff' } }, () => [6], null)
    expect(pix(scl)).toEqual([0, 0, 255, 255])
    const tci = componer({ version: 1, tipo: 'rgb8', bandas: [{ url: 't' }] }, () => [10, 20, 30], null)
    expect(pix(tci)).toEqual([10, 20, 30, 255])
  })

  it('rampa interpolada entre sus paradas', () => {
    expect(rampa([[0, 0, 0], [100, 200, 50]], 0.5)).toEqual([50, 100, 25])
    expect(rampa([[0, 0, 0], [100, 200, 50]], 7)).toEqual([100, 200, 50])
  })

  it('la malla de 17×17 reproyecta igual que proj4 punto a punto (error < 1 m)', () => {
    const utm = '+proj=utm +zone=18 +datum=WGS84 +units=m +no_defs'
    const z = 12, x = 1205, y = 1977                     // cerca de Bogotá
    const { xs, ys, caja } = rejilla(z, x, y, utm)
    const tam = (2 * Math.PI * 6378137) / 2 ** z
    const k = 100 * 256 + 37
    const [ex, ey] = proj4('EPSG:3857', utm).forward([-Math.PI * 6378137 + x * tam + (37.5 / 256) * tam,
                                                      Math.PI * 6378137 - y * tam - (100.5 / 256) * tam])
    expect(Math.abs(xs[k] - ex)).toBeLessThan(1)
    expect(Math.abs(ys[k] - ey)).toBeLessThan(1)
    expect(caja[0]).toBeLessThan(caja[2])
  })

  it('cambiar el contraste cambia la plantilla (MapLibre vuelve a pedir las teselas)', () => {
    const base: CogSpec = { version: 1, tipo: 'banda', bandas: [{ url: 'n' }], rangos: [[0, 0.4]] }
    const a = plantillaCog('capa', base, '/respaldo/{z}/{x}/{y}.png')
    const b = plantillaCog('capa', { ...base, rangos: [[0, 0.2]] }, '/respaldo/{z}/{x}/{y}.png')
    expect(a).toMatch(/^s2cog:\/\/capa-[a-z0-9]+\/\{z\}\/\{x\}\/\{y\}$/)
    expect(a).not.toBe(b)
  })
})
