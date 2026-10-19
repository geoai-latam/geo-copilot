/**
 * Exportar una capa a archivo, desde su fila en el panel de capas: formato, sistema de referencia
 * y alcance (toda, lo filtrado o lo seleccionado).
 */
import { useState } from 'react'
import { Download, Loader2 } from 'lucide-react'

import { type Alcance, alcancesDe, exportarCapa, FORMATOS, SISTEMAS } from '@/lib/exportarCapa'
import type { MapLayer } from '@/stores'

const NOMBRE_ALCANCE: Record<Alcance, string> = { toda: 'Toda la capa', filtro: 'Lo filtrado', seleccion: 'Lo seleccionado' }

type Estado = { tipo: 'ok' | 'error' | 'trabajando'; texto: string } | null

/** «3116» → «EPSG:3116»; vacío → null (aún no se puede descargar). */
function sistemaElegido(crs: string, otro: string): string | null {
  if (crs !== 'otro') return crs
  const t = otro.trim()
  if (!t) return null
  return /^\d+$/.test(t) ? `EPSG:${t}` : t
}

function SelectorSistema({ capa, crs, setCrs, otro, setOtro }: {
  capa: MapLayer; crs: string; setCrs: (v: string) => void; otro: string; setOtro: (v: string) => void
}) {
  return (
    <label>Sistema de referencia
      <select className="input" aria-label={`Sistema de referencia de ${capa.name}`} value={crs}
              onChange={(e) => setCrs(e.target.value)}>
        {SISTEMAS.map((s) => <option key={s.id} value={s.id}>{s.nombre} — {s.id}</option>)}
        <option value="otro">Otro (código EPSG)…</option>
      </select>
      {crs === 'otro' && (
        <input className="input" placeholder="p. ej. 3116" value={otro} aria-label="Código EPSG"
               onChange={(e) => setOtro(e.target.value)} />
      )}
    </label>
  )
}

function SelectorAlcance({ capa, alcances, alcance, setAlcance }: {
  capa: MapLayer; alcances: Alcance[]; alcance: Alcance; setAlcance: (a: Alcance) => void
}) {
  return (
    <div className="exportar-alcance" role="radiogroup" aria-label="Qué exportar">
      {alcances.map((a) => (
        <label key={a}>
          <input type="radio" name={`alcance-${capa.id}`} checked={alcance === a} onChange={() => setAlcance(a)} />
          {NOMBRE_ALCANCE[a]}
        </label>
      ))}
    </div>
  )
}

export function ExportarCapa({ capa }: { capa: MapLayer }) {
  const enServidor = !!capa.datasetId
  const [formato, setFormato] = useState<string>(enServidor ? 'gpkg' : 'geojson')
  const [crs, setCrs] = useState<string>('EPSG:4326')
  const [otro, setOtro] = useState('')
  const alcances = alcancesDe(capa)
  // por defecto lo más concreto: la selección si la hay, si no lo filtrado
  const [alcance, setAlcance] = useState<Alcance>(alcances[alcances.length - 1])
  const [estado, setEstado] = useState<Estado>(null)
  const sistema = sistemaElegido(crs, otro)
  const trabajando = estado?.tipo === 'trabajando'

  const descargar = async () => {
    setEstado({ tipo: 'trabajando', texto: 'Preparando el archivo…' })
    try {
      const r = await exportarCapa(capa, formato, formato === 'kml' ? null : sistema, alcance)
      setEstado({ tipo: 'ok', texto: `${r.archivo} · ${r.elementos.toLocaleString('es')} elementos` })
    } catch (e) {
      setEstado({ tipo: 'error', texto: e instanceof Error ? e.message : String(e) })
    }
  }

  return (
    <div className="exportar-capa" data-testid="exportar-capa">
      <label>Formato
        <select className="input" aria-label={`Formato de ${capa.name}`} value={formato}
                onChange={(e) => setFormato(e.target.value)}>
          {FORMATOS.filter((f) => enServidor || f.id === 'geojson').map((f) =>
            <option key={f.id} value={f.id}>{f.nombre}</option>)}
        </select>
      </label>
      {enServidor && formato !== 'kml' && <SelectorSistema {...{ capa, crs, setCrs, otro, setOtro }} />}
      {alcances.length > 1 && <SelectorAlcance {...{ capa, alcances, alcance, setAlcance }} />}
      <button className="gc-btn gc-btn-primary" onClick={() => void descargar()} disabled={trabajando || !sistema}>
        {trabajando ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
        Descargar
      </button>
      {estado && !trabajando && (
        <div className={`exportar-estado ${estado.tipo}`} role={estado.tipo === 'error' ? 'alert' : 'status'}>
          {estado.texto}
        </div>
      )}
    </div>
  )
}
