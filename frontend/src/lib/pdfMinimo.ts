/**
 * Un PDF de UNA página con una imagen JPEG a página completa (lo que necesita la composición de
 * impresión), sin dependencias: cabecera, catálogo, página, la imagen como XObject DCTDecode y la
 * tabla de referencias cruzadas.
 */

/** Tamaños de página en puntos PDF (1/72 pulgada), en horizontal. */
export const PAGINAS = {
  a4: { ancho: 841.89, alto: 595.28, nombre: 'A4' },
  carta: { ancho: 792, alto: 612, nombre: 'Carta' },
} as const
export type Pagina = keyof typeof PAGINAS

export function pdfConImagen(jpeg: Uint8Array, anchoPx: number, altoPx: number, pagina: Pagina): Blob {
  const { ancho, alto } = PAGINAS[pagina]
  const enc = new TextEncoder()
  const partes: Uint8Array[] = []
  const offsets: number[] = []
  let largo = 0
  const poner = (p: string | Uint8Array) => {
    const b = typeof p === 'string' ? enc.encode(p) : p
    partes.push(b)
    largo += b.length
  }
  const objeto = (n: number, cuerpo: string | Uint8Array[], ) => {
    offsets[n] = largo
    poner(`${n} 0 obj\n`)
    if (typeof cuerpo === 'string') poner(cuerpo)
    else cuerpo.forEach(poner)
    poner('\nendobj\n')
  }
  const contenido = `q ${ancho} 0 0 ${alto} 0 0 cm /Im0 Do Q`

  poner('%PDF-1.4\n%\xE2\xE3\xCF\xD3\n')
  objeto(1, '<< /Type /Catalog /Pages 2 0 R >>')
  objeto(2, '<< /Type /Pages /Kids [3 0 R] /Count 1 >>')
  objeto(3, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${ancho} ${alto}] `
    + '/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>')
  objeto(4, [enc.encode(`<< /Type /XObject /Subtype /Image /Width ${anchoPx} /Height ${altoPx} `
    + `/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.length} >>\nstream\n`),
  jpeg, enc.encode('\nendstream')])
  objeto(5, `<< /Length ${contenido.length} >>\nstream\n${contenido}\nendstream`)
  const xref = largo
  poner(`xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map((o) => `${String(o).padStart(10, '0')} 00000 n \n`).join('')}`)
  poner(`trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`)
  return new Blob(partes as BlobPart[], { type: 'application/pdf' })
}
