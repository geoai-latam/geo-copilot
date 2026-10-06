/**
 * Helpers puros del panel HITL.
 *
 * Auditoría 2026-09-08 §5 (hallazgo 1): el panel pintaba SIEMPRE las tres
 * celdas del grid de impacto —filas / costo / tiempo— leyendo
 * `approval.impact_estimate`, un campo con CERO productores en el backend
 * (`security/hitl.py:340-354` emite approval_id, content_type, content,
 * content_sha256, preview, warnings, risk_level, title, description,
 * action_type y created_at — nada más). Resultado: tres guiones fijos que
 * parecían un cálculo que salió vacío en vez de un dato que nadie manda.
 *
 * Regla: una celda solo existe si el backend mandó su campo. Si no mandó
 * ninguno, no hay grid — hay una frase que dice que no llegó estimación.
 * En una UI de seguridad el hueco declarado es información; el guion
 * ambiguo y el default presentado como medición, no.
 */
import type { ImpactEstimate } from '@/types'

export interface ImpactCell {
  key: 'rows' | 'cost' | 'time'
  label: string
  value: string
  /** Resalta la celda de costo cuando la acción es de alto riesgo. */
  warn: boolean
}

const RISK_LABEL: Record<string, string> = {
  low: 'bajo',
  medium: 'medio',
  high: 'alto',
}

function formatTime(ms: number): string {
  return ms < 1000 ? `~ ${ms} ms` : `~ ${(ms / 1000).toFixed(1)} s`
}

/**
 * Construye SOLO las celdas para las que el backend envió dato.
 *
 * @param impact  `approval.impact_estimate` tal cual llega (puede faltar).
 * @param highRisk `risk_level === 'high'` o `impact.risk === 'high'`.
 * @returns celdas en orden de lectura; array vacío si no llegó nada.
 */
export function buildImpactCells(
  impact: ImpactEstimate | null | undefined,
  highRisk: boolean,
): ImpactCell[] {
  if (!impact) return []
  const cells: ImpactCell[] = []

  if (impact.rows != null) {
    cells.push({
      key: 'rows',
      label: 'Filas estimadas',
      value: `~ ${impact.rows.toLocaleString('es')}`,
      warn: false,
    })
  }

  // El costo del planner es el dato preferido; `risk` es el respaldo
  // cualitativo que el backend sí podría mandar sin correr un EXPLAIN.
  if (impact.cost != null) {
    cells.push({
      key: 'cost',
      label: 'Costo (planner)',
      value: Math.round(impact.cost).toLocaleString('es'),
      warn: highRisk,
    })
  } else if (impact.risk != null) {
    cells.push({
      key: 'cost',
      label: 'Riesgo estimado',
      value: RISK_LABEL[impact.risk] ?? impact.risk,
      warn: highRisk,
    })
  }

  if (impact.time_ms != null) {
    cells.push({
      key: 'time',
      label: 'Tiempo estimado',
      value: formatTime(impact.time_ms),
      warn: false,
    })
  }

  return cells
}
