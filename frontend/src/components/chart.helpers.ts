/**
 * Los valores de Y (medidas) pasan a número si llegan como texto. La X es una
 * CATEGORÍA y se deja tal cual — salvo en dispersión, donde también es medida:
 * convertirla borraba los ceros de los códigos (lote «004503009001» → 4503009001,
 * V5 F4).
 */
export function datosParaGrafico(
  filas: Record<string, unknown>[], xKey: string, yKey: string, chartType: string,
): Record<string, unknown>[] {
  const numericas = chartType === 'scatter' ? [xKey, yKey] : [yKey]
  return filas.map((fila) => {
    const out = { ...fila }
    for (const k of numericas) {
      const v = out[k]
      if (typeof v === 'string' && v.trim() !== '' && !isNaN(Number(v))) out[k] = Number(v)
    }
    return out
  })
}
