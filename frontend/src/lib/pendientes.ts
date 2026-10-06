/**
 * Ajustes manuales todavía en vuelo (p. ej. el editor de estilo, que clasifica en el servidor).
 *
 * Pendiente del acta FH (EH.5): pedir al agente MENOS de un segundo después de tocar la rampa
 * enviaba el mapa SIN el fijado (el ajuste aún viajaba) y el agente lo pisaba. Antes de tomar la
 * foto del mapa para un turno se espera a lo que esté en vuelo (con tope: nunca bloquea el chat).
 */
const enVuelo = new Set<Promise<unknown>>()

export function enVueloMientras<T>(p: Promise<T>): Promise<T> {
  enVuelo.add(p)
  const quitar = () => { enVuelo.delete(p) }
  p.then(quitar, quitar)
  return p
}

export async function esperarAjustesEnVuelo(topeMs = 5000): Promise<void> {
  if (!enVuelo.size) return
  await Promise.race([
    Promise.allSettled([...enVuelo]),
    new Promise((resolve) => setTimeout(resolve, topeMs)),
  ])
}
