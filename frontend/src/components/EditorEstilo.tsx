/**
 * FH.6 — editor manual de simbología sobre el MISMO StyleSpec que produce el agente.
 *
 * Cada cambio se calcula en el backend con el código del agente (cortes y colores) y
 * se aplica con `set_style` (autor «usuario», Ctrl+Z). Lo que el usuario toca queda
 * FIJADO: el agente lo ve como hecho en el turno siguiente y lo conserva salvo que se
 * le pida cambiarlo. Los fijados se pueden soltar (✕).
 */
import { useEffect, useState } from 'react'
import { Pin, X } from 'lucide-react'

import { useOperaciones } from '@/lib/operaciones'
import { enVueloMientras } from '@/lib/pendientes'
import { workspaceApi, type DisenoEstilo } from '@/services/api'
import type { MapLayer } from '@/stores/mapStore'
import { useSessionStore } from '@/stores/sessionStore'
import { camposDeCapa } from './FiltroCapa.helpers'
import { cambiar, completo, disenoDe, METODOS, NOMBRE_FIJADO, TIPOS } from './EditorEstilo.helpers'

let cacheRampas: Promise<Record<string, string[]>> | null = null

export function EditorEstilo({ capa }: { capa: MapLayer }) { // eslint-disable-line complexity -- deuda congelada (F1); partir, no subir
  const sessionId = useSessionStore((s) => s.sessionId)
  const [rampas, setRampas] = useState<Record<string, string[]>>({})
  const [borrador, setBorrador] = useState<DisenoEstilo>(() => disenoDe(capa))
  const [error, setError] = useState<string | null>(null)
  const [aplicando, setAplicando] = useState(false)
  const fijados = capa.symbology?.pinned ?? []
  const campos = camposDeCapa(capa)
  const esPunto = /point/i.test(String(capa.tiles?.geometryType ?? capa.data?.features?.[0]?.geometry?.type ?? ''))

  useEffect(() => {
    cacheRampas ??= workspaceApi.rampas()
    cacheRampas.then(setRampas).catch(() => { cacheRampas = null })
  }, [])
  // lo que cambie el agente (o Ctrl+Z) se ve aquí
  useEffect(() => setBorrador(disenoDe(capa)), [capa.symbology]) // eslint-disable-line react-hooks/exhaustive-deps

  const aplicar = async (diseno: DisenoEstilo, pinned: string[]) => {
    setBorrador(diseno)
    if (!completo(diseno) || !sessionId) return
    setAplicando(true)
    try {
      // en vuelo hasta que el estilo esté en el mapa: un turno enviado ahora lo espera (EH.5)
      await enVueloMientras((async () => {
        const { style } = await workspaceApi.estilo(sessionId, {
          diseno, pinned, titulo: capa.name,
          ...(capa.datasetId ? { dataset_id: capa.datasetId } : { geojson: capa.data }),
        })
        useOperaciones.getState().ejecutar(
          { op: 'set_style', layer_id: capa.id, args: { style: style as never }, reason: 'ajuste manual' }, 'user')
      })())
      setError(null)
    } catch (e) {
      setError(`No se pudo aplicar: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setAplicando(false)
    }
  }
  const cambio = (campo: keyof DisenoEstilo, valor: DisenoEstilo[keyof DisenoEstilo]) => {
    const r = cambiar(borrador, fijados, campo, valor)
    void aplicar(r.diseno, r.pinned)
  }
  const soltar = (campo: string) => void aplicar(disenoDe(capa), fijados.filter((c) => c !== campo))

  const tipo = borrador.symbology_type
  const graduado = tipo === 'graduated_colors' || tipo === 'graduated_symbols'
  const conCampo = TIPOS.find((t) => t.valor === tipo)?.necesitaCampo || tipo === 'heatmap'

  return (
    <div className="editor-estilo" data-testid="editor-estilo" aria-busy={aplicando}>
      <label>Tipo
        <select aria-label="Tipo de simbología" value={tipo} onChange={(e) => cambio('symbology_type', e.target.value)}>
          {TIPOS.filter((t) => !t.soloPuntos || esPunto).map((t) => <option key={t.valor} value={t.valor}>{t.nombre}</option>)}
        </select>
      </label>
      {conCampo && (
        <label>Campo
          <select aria-label="Campo de la simbología" value={borrador.classification_field ?? ''}
                  onChange={(e) => cambio('classification_field', e.target.value || null)}>
            <option value="">—</option>
            {campos.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
      )}
      {graduado && (
        <>
          <label>Método
            <select aria-label="Método de clasificación" value={borrador.classification_method ?? 'natural_breaks'}
                    onChange={(e) => cambio('classification_method', e.target.value)}>
              {METODOS.map((m) => <option key={m.valor} value={m.valor}>{m.nombre}</option>)}
            </select>
          </label>
          <label>Clases
            <input aria-label="Número de clases" type="number" min={2} max={12} value={borrador.num_classes ?? 5}
                   onChange={(e) => cambio('num_classes', Math.max(2, Math.min(12, Number(e.target.value) || 5)))} />
          </label>
        </>
      )}
      {tipo !== 'single_symbol' && tipo !== 'cluster' && (
        <label>Rampa
          <select aria-label="Rampa de color" value={borrador.color_scheme ?? ''}
                  onChange={(e) => cambio('color_scheme', e.target.value)}>
            <option value="" disabled>—</option>
            {Object.keys(rampas).map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          {borrador.color_scheme && rampas[borrador.color_scheme] && (
            <span className="editor-estilo-rampa" aria-hidden
                  style={{ background: `linear-gradient(to right, ${rampas[borrador.color_scheme].join(', ')})` }} />
          )}
        </label>
      )}
      {(tipo === 'single_symbol' || tipo === 'cluster') && (
        <label>Color
          <input aria-label="Color de la capa" type="color" value={borrador.fill_color ?? '#3b82f6'}
                 onChange={(e) => cambio('fill_color', e.target.value)} />
        </label>
      )}
      {fijados.length > 0 && (
        <div className="editor-estilo-fijados" data-testid="estilo-fijados">
          {fijados.map((c) => (
            <span key={c} className="filtro-chip" title="Lo fijaste a mano: el agente lo conserva salvo que le pidas cambiarlo">
              <Pin className="w-3 h-3" /> {NOMBRE_FIJADO[c] ?? c}
              <button type="button" className="icon-btn" aria-label={`Soltar ${NOMBRE_FIJADO[c] ?? c}`} onClick={() => soltar(c)}>
                <X className="w-3 h-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      {error && <div className="dibujo-error" role="alert">{error}</div>}
    </div>
  )
}
