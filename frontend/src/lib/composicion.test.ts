import { describe, expect, it } from 'vitest'

import { longitudRedonda, metrosPorPixel, tamanoPagina, textoDistancia } from './composicion'
import { pdfConImagen } from './pdfMinimo'

describe('composición de impresión', () => {
  it('la barra de escala usa longitudes redondas', () => {
    expect(longitudRedonda(7300)).toBe(5000)
    expect(longitudRedonda(1999)).toBe(1000)
    expect(longitudRedonda(260)).toBe(200)
    expect(longitudRedonda(1)).toBe(1)
  })

  it('las distancias se leen en m o km', () => {
    expect(textoDistancia(500)).toBe('500 m')
    expect(textoDistancia(5000)).toBe('5 km')
  })

  it('metros por píxel: el ecuador a zoom 0 y la mitad a 60°', () => {
    expect(metrosPorPixel(0, 0)).toBeCloseTo(78271.5, 0)
    expect(metrosPorPixel(60, 0)).toBeCloseTo(78271.5 / 2, 0)
  })

  it('páginas horizontales a 150 ppp', () => {
    expect(tamanoPagina('a4')).toEqual({ ancho: 1754, alto: 1240 })
    expect(tamanoPagina('carta')).toEqual({ ancho: 1650, alto: 1275 })
  })
})

describe('PDF de una página con una imagen', () => {
  it('trae cabecera, la imagen JPEG, tabla xref con offsets reales y fin', async () => {
    const jpeg = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 0xff, 0xd9])
    const blob = pdfConImagen(jpeg, 10, 5, 'a4')
    expect(blob.type).toBe('application/pdf')
    const bytes = new Uint8Array(await blob.arrayBuffer())
    const texto = new TextDecoder('latin1').decode(bytes)
    expect(texto.startsWith('%PDF-1.4')).toBe(true)
    expect(texto).toContain('/Width 10 /Height 5')
    expect(texto).toContain('/MediaBox [0 0 841.89 595.28]')
    expect(texto.trimEnd().endsWith('%%EOF')).toBe(true)
    // cada offset de la xref apunta a «n 0 obj»
    const xref = texto.slice(texto.indexOf('xref'))
    const offsets = [...xref.matchAll(/(\d{10}) 00000 n/g)].map((m) => Number(m[1]))
    expect(offsets).toHaveLength(5)
    offsets.forEach((o, i) => expect(texto.slice(o, o + 7)).toBe(`${i + 1} 0 obj`))
    const inicio = Number(/startxref\n(\d+)/.exec(texto)?.[1])
    expect(texto.slice(inicio, inicio + 4)).toBe('xref')
  })
})
