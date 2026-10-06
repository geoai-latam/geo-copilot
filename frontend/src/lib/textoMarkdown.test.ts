import { describe, expect, it } from 'vitest'

import { bloques, piezas } from './textoMarkdown'

describe('bloques', () => {
  it('separa párrafos, listas, títulos y tablas (respuesta real de gpt-5.4)', () => {
    const texto = [
      '### Resumen',
      'La manzana tiene **30 lotes**.',
      'Fuente: BD interna.',
      '',
      '- **res**: 2 predios',
      '- **com**: 1 predio',
      '',
      '| uso | conteo |',
      '|---|---:|',
      '| res | 2 |',
      '| com | 1 |',
      '',
      '1. primero',
      '2. segundo',
    ].join('\n')
    expect(bloques(texto)).toEqual([
      { tipo: 'titulo', nivel: 3, texto: 'Resumen' },
      { tipo: 'parrafo', lineas: ['La manzana tiene **30 lotes**.', 'Fuente: BD interna.'] },
      { tipo: 'lista', ordenada: false, items: ['**res**: 2 predios', '**com**: 1 predio'] },
      { tipo: 'tabla', cabecera: ['uso', 'conteo'], filas: [['res', '2'], ['com', '1']] },
      { tipo: 'lista', ordenada: true, items: ['primero', 'segundo'] },
    ])
  })

  it('una línea con barras pero sin separador no es una tabla', () => {
    expect(bloques('| hola |')).toEqual([{ tipo: 'parrafo', lineas: ['| hola |'] }])
  })

  it('un producto con asterisco no es una lista', () => {
    expect(bloques('3 * 4 = 12')).toEqual([{ tipo: 'parrafo', lineas: ['3 * 4 = 12'] }])
  })
})

describe('piezas', () => {
  it('negrita y código', () => {
    expect(piezas('el campo `area_m2` en **4 clases**')).toEqual([
      { texto: 'el campo ', negrita: false, codigo: false },
      { texto: 'area_m2', negrita: false, codigo: true },
      { texto: ' en ', negrita: false, codigo: false },
      { texto: '4 clases', negrita: true, codigo: false },
    ])
  })

  it('un enlace al mapa dentro de la negrita la conserva', () => {
    const p = piezas('**[[layer:activa|Lotes]]** listos')
    expect(p[0]).toMatchObject({ texto: 'Lotes', negrita: true, ref: { capa: 'activa' } })
    expect(p[1]).toEqual({ texto: ' listos', negrita: false, codigo: false })
  })

  it('una marca sin pareja se queda como texto', () => {
    expect(piezas('**30 lotes** y 2 ** sueltos')).toEqual([
      { texto: '30 lotes', negrita: true, codigo: false },
      { texto: ' y 2 ** sueltos', negrita: false, codigo: false },
    ])
  })

  it('el texto nunca se interpreta como HTML', () => {
    expect(piezas('<img src=x onerror=alert(1)>')).toEqual([
      { texto: '<img src=x onerror=alert(1)>', negrita: false, codigo: false },
    ])
  })
})
