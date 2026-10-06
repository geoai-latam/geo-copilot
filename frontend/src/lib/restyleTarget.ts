/**
 * FRT-04 — elegir la capa a re-estilar.
 *
 * El backend indica `target_layer_id` (la capa activa que el mapa reportó en
 * map_context). Se re-estila ESA capa por id. Sólo si el backend no indicó
 * objetivo (o el id ya no existe entre las capas cargadas) se cae a "la última
 * añadida" — la heurística de posición previa, que re-estilaba la capa
 * equivocada cuando la última no era la que el usuario nombró.
 */
export function pickRestyleTarget<T extends { id: string }>(
  layers: T[],
  targetLayerId: string | null | undefined,
): T | undefined {
  const byId = targetLayerId
    ? layers.find((l) => l.id === targetLayerId)
    : undefined
  return byId ?? (layers.length ? layers[layers.length - 1] : undefined)
}
