/**
 * FH.7 — «cómo se hizo» una capa: la operación que la produjo (SQL, código, herramienta o
 * servicio MCP con sus argumentos, versión de la fuente) y, hacia atrás, la de los datasets
 * de los que sale. Sale de la procedencia del LayerRef (backend); una capa que no está en
 * el workspace (raster de un servicio) muestra la procedencia que trajo.
 */
import { useEffect, useState } from 'react'

import { workspaceApi, type PasoProcedencia } from '@/services/api'
import type { MapLayer } from '@/stores/mapStore'
import { useSessionStore } from '@/stores/sessionStore'
import { argumentos, nombreCapacidad } from './ComoSeHizo.helpers'

const fecha = (iso?: string) => (iso ? new Date(iso).toLocaleString() : '')

export function ComoSeHizo({ capa }: { capa: MapLayer }) {
  const sessionId = useSessionStore((s) => s.sessionId)
  const [pasos, setPasos] = useState<PasoProcedencia[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!capa.datasetId || !sessionId) return
    let vivo = true
    workspaceApi.procedencia(sessionId, capa.datasetId)
      .then((r) => { if (vivo) setPasos(r.pasos) })
      .catch((e) => { if (vivo) setError(e instanceof Error ? e.message : String(e)) })
    return () => { vivo = false }
  }, [capa.datasetId, sessionId])

  if (!capa.datasetId) {
    return (
      <div className="como-se-hizo" data-testid="como-se-hizo">
        {capa.origen
          ? <Paso titulo={nombreCapacidad(capa.origen.capability)} args={argumentos(capa.origen.arguments, [])} />
          : <p className="como-nada">Esta capa no trae registro de cómo se obtuvo.</p>}
      </div>
    )
  }
  if (error) return <div className="como-se-hizo dibujo-error" role="alert">No se pudo leer la procedencia: {error}</div>
  if (!pasos) return <div className="como-se-hizo" aria-busy>Leyendo la procedencia…</div>
  return (
    <div className="como-se-hizo" data-testid="como-se-hizo">
      {pasos.map((p, i) => (p.disponible && p.provenance ? (
        <Paso key={p.dataset_id} titulo={`${i === 0 ? '' : 'de '}${nombreCapacidad(p.provenance.capability)}`}
              subtitulo={`${p.nombre ?? p.dataset_id} · ${fecha(p.provenance.produced_at)}`}
              args={argumentos(p.provenance.arguments, pasos)} sql={p.provenance.sql} code={p.provenance.code}
              fuente={p.provenance.source_version}
              ediciones={(p.provenance.edits ?? []).map((e) => `${nombreCapacidad(e.capability)}: ${
                argumentos(e.arguments, pasos).map(([k, v]) => `${k} ${v}`).join(', ')} (${fecha(e.produced_at)})`)} />
      ) : (
        <p key={p.dataset_id} className="como-nada">de {p.dataset_id}: ya no está en el workspace (venció o se borró).</p>
      )))}
    </div>
  )
}

function Paso({ titulo, subtitulo, args, sql, code, fuente, ediciones }: {
  titulo: string; subtitulo?: string; args: [string, string][]; sql?: string | null; code?: string | null
  fuente?: string | null; ediciones?: string[]
}) {
  return (
    <section className="como-paso">
      <div className="como-titulo">{titulo}</div>
      {subtitulo && <div className="como-sub">{subtitulo}</div>}
      {args.length > 0 && (
        <dl className="como-args">
          {args.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
        </dl>
      )}
      {fuente && <div className="como-sub">fuente: {fuente}</div>}
      {sql && <details open><summary>SQL</summary><pre className="como-codigo">{sql}</pre></details>}
      {code && <details><summary>Código</summary><pre className="como-codigo">{code}</pre></details>}
      {ediciones?.length ? <ul className="como-ediciones">{ediciones.map((e) => <li key={e}>después: {e}</li>)}</ul> : null}
    </section>
  )
}
