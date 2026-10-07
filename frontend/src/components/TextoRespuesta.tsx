/**
 * FH.7 — el texto de una respuesta con sus enlaces al mapa: pasar el ratón resalta lo
 * que señala; clic lo selecciona (autor «usuario», se deshace con Ctrl+Z) y lo encuadra.
 * Un enlace a algo que ya no está en el mapa queda como texto (con el motivo al pasar).
 */
import { Fragment, type ReactNode, useState } from 'react'
import { Download } from 'lucide-react'

import { descargarDataset } from '@/lib/exportarCapa'
import { useOperaciones } from '@/lib/operaciones'
import { elementos, extensionDe, resolverCapa, type Referencia } from '@/lib/referencias'
import { bloques, piezas } from '@/lib/textoMarkdown'
import { useLayers, useMapStore } from '@/stores/mapStore'

/** Un enlace de descarga del agente: baja el archivo con la credencial del usuario. */
function Descarga({ refe, texto, delTurno }: { refe: Referencia; texto: string; delTurno: string[] }) {
  const capas = useLayers()
  const [estado, setEstado] = useState<string | null>(null)
  const capa = refe.capa.startsWith('ds_') ? undefined : resolverCapa(refe, capas, delTurno, texto)
  const dataset = refe.capa.startsWith('ds_') ? refe.capa : capa?.datasetId
  const bajar = async () => {
    if (!dataset || !refe.descarga) return
    setEstado('Preparando…')
    try {
      const r = await descargarDataset(dataset, refe.descarga.formato, refe.descarga.crs ?? null)
      setEstado(`${r.archivo} · ${r.elementos.toLocaleString('es')} elementos`)
    } catch (e) {
      setEstado(e instanceof Error ? e.message : String(e))
    }
  }
  if (!dataset) return <span className="ref-mapa ref-rota" title="Esa capa ya no está" data-testid="ref-rota">{texto}</span>
  return (
    <>
      <button type="button" className="ref-mapa ref-descarga" data-testid="ref-descarga" onClick={() => void bajar()}
              title="Descargar el archivo">
        <Download className="w-3 h-3" /> {texto}
      </button>
      {estado && <span className="ref-descarga-estado" role="status"> {estado}</span>}
    </>
  )
}

function Enlace({ refe, texto, delTurno }: { refe: Referencia; texto: string; delTurno: string[] }) {
  if (refe.descarga) return <Descarga refe={refe} texto={texto} delTurno={delTurno} />
  return <EnlaceMapa refe={refe} texto={texto} delTurno={delTurno} />
}

function EnlaceMapa({ refe, texto, delTurno }: { refe: Referencia; texto: string; delTurno: string[] }) {
  const capas = useLayers()
  const capa = resolverCapa(refe, capas, delTurno, texto)
  const el = capa ? elementos(refe, capa) : 'vacio'
  if (!capa || el === 'vacio') {
    const motivo = !capa ? 'Esa capa ya no está en el mapa' : 'Ese elemento no está en la capa (¿filtrado o quitado?)'
    return <span className="ref-mapa ref-rota" title={motivo} data-testid="ref-rota">{texto}</span>
  }
  const resaltar = (on: boolean) => {
    if (el) useMapStore.getState().setResaltado(capa.id, on ? el : null)
  }
  const ir = () => {
    resaltar(false)
    const ops = useOperaciones.getState()
    if (!el) {
      ops.ejecutar({ op: 'zoom_to', layer_id: capa.id, args: {} } as never, 'user')
      return
    }
    ops.ejecutar({ op: 'select', layer_id: capa.id, reason: 'enlace de la respuesta',
                   args: { ...(el.ids ? { ids: el.ids } : { where: el.where }), mode: 'replace', origin: 'link' } } as never, 'user')
    const bbox = el.ids ? extensionDe(capa, el.ids) : null
    if (bbox) ops.ejecutar({ op: 'zoom_to', args: { bbox } } as never, 'user')
    else ops.ejecutar({ op: 'zoom_to', layer_id: capa.id, args: {} } as never, 'user')
  }
  return (
    <button type="button" className="ref-mapa" data-testid="ref-mapa"
            title={el ? `Ver en «${capa.name}»` : `Ver la capa «${capa.name}»`}
            onMouseEnter={() => resaltar(true)} onMouseLeave={() => resaltar(false)}
            onFocus={() => resaltar(true)} onBlur={() => resaltar(false)} onClick={ir}>
      {texto}
    </button>
  )
}

/** Una línea con su formato (negrita, código) y sus enlaces al mapa. */
function Linea({ texto, delTurno }: { texto: string; delTurno: string[] }) {
  return (
    <>
      {piezas(texto).map((p, i) => {
        let nodo: ReactNode = p.ref
          ? <Enlace refe={p.ref} texto={p.texto} delTurno={delTurno} />
          : p.texto
        if (p.codigo) nodo = <code>{nodo}</code>
        if (p.negrita) nodo = <strong>{nodo}</strong>
        return <Fragment key={i}>{nodo}</Fragment>
      })}
    </>
  )
}

/** El markdown de la respuesta (V5, auditoría F4): antes se veían los `**` y las líneas se juntaban. */
export function TextoRespuesta({ texto, delTurno = [] }: { texto: string; delTurno?: string[] }) {
  return (
    <div className="texto-respuesta">
      {bloques(texto).map((b, i) => {
        if (b.tipo === 'titulo') return <p key={i} className="texto-titulo"><strong><Linea texto={b.texto} delTurno={delTurno} /></strong></p>
        if (b.tipo === 'lista') {
          const Etiqueta = b.ordenada ? 'ol' : 'ul'
          return <Etiqueta key={i}>{b.items.map((it, j) => <li key={j}><Linea texto={it} delTurno={delTurno} /></li>)}</Etiqueta>
        }
        if (b.tipo === 'tabla') {
          return (
            <div key={i} className="texto-tabla">
              <table>
                <thead><tr>{b.cabecera.map((c, j) => <th key={j}><Linea texto={c} delTurno={delTurno} /></th>)}</tr></thead>
                <tbody>{b.filas.map((f, j) => (
                  <tr key={j}>{f.map((c, k) => <td key={k}><Linea texto={c} delTurno={delTurno} /></td>)}</tr>
                ))}</tbody>
              </table>
            </div>
          )
        }
        return (
          <p key={i}>
            {b.lineas.map((l, j) => (
              <Fragment key={j}>{j > 0 && <br />}<Linea texto={l} delTurno={delTurno} /></Fragment>
            ))}
          </p>
        )
      })}
    </div>
  )
}
