/**
 * Contrato backend → frontend (F4, S4.1): tipos generados + validación en la frontera.
 *
 * Los tipos salen de `generated.ts` (json-schema-to-typescript) y la validación
 * de zod `fromJSONSchema` sobre el MISMO schema: una sola fuente, el modelo
 * pydantic del backend. Si backend y frontend divergen, falla el test de
 * contrato (`contracts.test.ts`) antes de llegar a producción; y si aun así
 * llega una respuesta fuera de contrato, se rechaza aquí con el motivo en vez de
 * pintar algo a medias.
 */
import * as z from 'zod'

import queryResponseSchema from './schema/query_response.schema.json'
import type { QueryResponse } from './generated'

export type * from './generated'

/** Un artefacto de la respuesta (capa, tabla, gráfico, estadísticas, informe, servicios, orden al mapa). */
export type Artifact = QueryResponse['artifacts'][number]
/** Una orden al mapa (unión por `op`): la del agente y la de un gesto del usuario (FH.1). */
export type MapCommand = import('./generated').MapCommandOut['command']

// El schema de pydantic es JSON Schema 2020-12 con $defs: fromJSONSchema lo resuelve.
const validador = z.fromJSONSchema(queryResponseSchema as z.core.JSONSchema.JSONSchema)

export class RespuestaFueraDeContrato extends Error {
  constructor(public readonly problemas: string[]) {
    super(`Respuesta del servidor fuera de contrato: ${problemas.slice(0, 3).join('; ')}`)
    this.name = 'RespuestaFueraDeContrato'
  }
}

/** Valida una respuesta de /query contra el contrato; lanza con el motivo si no cumple. */
export function validarRespuesta(json: unknown): QueryResponse {
  const r = validador.safeParse(json)
  if (!r.success) {
    throw new RespuestaFueraDeContrato(
      r.error.issues.map((i) => `${i.path.join('.') || '(raíz)'}: ${i.message}`),
    )
  }
  // Lo VALIDADO, no el JSON de entrada: zod completa los campos con `default`
  // del schema, así que el objeto cumple de verdad el tipo que declara.
  return r.data as QueryResponse
}
