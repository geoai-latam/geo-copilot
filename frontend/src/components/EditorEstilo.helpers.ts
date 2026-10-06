/** FH.6 — lógica del editor de estilo (sin JSX: se prueba sin montar nada). */
import type { DisenoEstilo } from '@/services/api'
import type { MapLayer } from '@/stores/mapStore'

export const TIPOS: { valor: string; nombre: string; soloPuntos?: boolean; necesitaCampo?: boolean }[] = [
  { valor: 'single_symbol', nombre: 'Un color' },
  { valor: 'unique_values', nombre: 'Categorías', necesitaCampo: true },
  { valor: 'graduated_colors', nombre: 'Graduado', necesitaCampo: true },
  { valor: 'graduated_symbols', nombre: 'Tamaños', necesitaCampo: true, soloPuntos: true },
  { valor: 'heatmap', nombre: 'Calor' },
  { valor: 'cluster', nombre: 'Cluster', soloPuntos: true },
]

export const METODOS: { valor: string; nombre: string }[] = [
  { valor: 'natural_breaks', nombre: 'Jenks' },
  { valor: 'quantile', nombre: 'Cuantiles' },
  { valor: 'equal_interval', nombre: 'Iguales' },
  { valor: 'std_deviation', nombre: 'Desv. estándar' },
]

const GRADUADOS = new Set(['graduated_colors', 'graduated_symbols'])

/** El diseño actual de la capa (lo que el editor muestra). */
export function disenoDe(l: MapLayer): DisenoEstilo { // eslint-disable-line complexity -- deuda congelada (F1): ESLint 10 suma `?.` y defaults; partir, no subir
  const s = l.symbology
  return {
    symbology_type: s?.symbology_type ?? 'single_symbol',
    classification_field: s?.classification_field ?? null,
    classification_method: s?.classification_method ?? null,
    num_classes: s?.num_classes ?? (s?.class_breaks?.length || null),
    color_scheme: s?.color_scheme ?? null,
    fill_color: s?.fill?.color ?? l.color ?? null,
  }
}

/**
 * El diseño tras cambiar UN ajuste, y lo que queda fijado: lo que el usuario toca a
 * mano se fija (el agente lo verá como hecho); lo que ya estaba fijado sigue.
 */
export function cambiar(
  actual: DisenoEstilo, fijados: string[], campo: keyof DisenoEstilo, valor: DisenoEstilo[keyof DisenoEstilo],
): { diseno: DisenoEstilo; pinned: string[] } {
  const diseno = { ...actual, [campo]: valor } as DisenoEstilo
  if (campo === 'symbology_type') {
    if (!GRADUADOS.has(String(valor))) diseno.classification_method = null
    if (GRADUADOS.has(String(valor)) && !diseno.classification_method) diseno.classification_method = 'natural_breaks'
    if (GRADUADOS.has(String(valor)) && !diseno.num_classes) diseno.num_classes = 5
  }
  const pinned = fijados.includes(campo) ? fijados : [...fijados, campo]
  return { diseno, pinned }
}

/** ¿Se puede aplicar ya? (un tipo que clasifica necesita campo) */
export function completo(d: DisenoEstilo): boolean {
  const t = TIPOS.find((x) => x.valor === d.symbology_type)
  return !t?.necesitaCampo || !!d.classification_field
}

export const NOMBRE_FIJADO: Record<string, string> = {
  symbology_type: 'tipo', classification_field: 'campo', classification_method: 'método',
  num_classes: 'clases', color_scheme: 'rampa', fill_color: 'color',
}
