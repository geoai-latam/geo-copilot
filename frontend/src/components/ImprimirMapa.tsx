/**
 * «Imprimir»: la composición del mapa (título, leyenda, norte, escala, fuentes, fecha) en PNG o
 * PDF, A4 o Carta. Un botón en la cabecera del lienzo que abre un panel corto.
 */
import { useState } from 'react'
import { Loader2, Printer } from 'lucide-react'

import { exportarComposicion } from '@/lib/composicion'
import { mapaActivo } from '@/lib/mapaActivo'
import type { Pagina } from '@/lib/pdfMinimo'
import { useMapStore } from '@/stores'

function Campo({ etiqueta, valor, poner, ph }: { etiqueta: string; valor: string; poner: (v: string) => void; ph?: string }) {
  return (
    <label>{etiqueta}
      <input className="input" value={valor} placeholder={ph} aria-label={etiqueta} onChange={(e) => poner(e.target.value)} />
    </label>
  )
}

export function ImprimirMapa() {
  const [abierto, setAbierto] = useState(false)
  const [titulo, setTitulo] = useState('')
  const [subtitulo, setSubtitulo] = useState('')
  const [autor, setAutor] = useState('')
  const [pagina, setPagina] = useState<Pagina>('carta')
  const [estado, setEstado] = useState<string | null>(null)
  const [trabajando, setTrabajando] = useState(false)

  const generar = async (formato: 'png' | 'pdf') => {
    const map = mapaActivo()
    if (!map) return setEstado('No hay mapa que imprimir.')
    setTrabajando(true)
    setEstado(null)
    try {
      const capas = useMapStore.getState().layers
      const archivo = await exportarComposicion(map, capas, { titulo, subtitulo, autor, pagina }, formato)
      setEstado(`Listo: ${archivo}`)
    } catch (e) {
      setEstado(`No se pudo componer: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setTrabajando(false)
    }
  }

  return (
    <div className="imprimir-mapa">
      <button className={`gc-btn gc-btn-ghost${abierto ? ' is-active' : ''}`} title="Imprimir el mapa (PNG / PDF)"
              aria-label="Imprimir el mapa" aria-expanded={abierto} onClick={() => setAbierto(!abierto)}>
        <Printer className="w-3.5 h-3.5" />
      </button>
      {abierto && (
        <div className="imprimir-panel" role="dialog" aria-label="Imprimir el mapa" data-testid="imprimir-panel">
          <Campo etiqueta="Título" valor={titulo} poner={setTitulo} ph="p. ej. Pendientes de los cerros orientales" />
          <Campo etiqueta="Subtítulo" valor={subtitulo} poner={setSubtitulo} ph="opcional" />
          <Campo etiqueta="Elaboró" valor={autor} poner={setAutor} ph="persona o entidad (opcional)" />
          <label>Página
            <select className="input" aria-label="Tamaño de página" value={pagina}
                    onChange={(e) => setPagina(e.target.value as Pagina)}>
              <option value="carta">Carta, horizontal</option>
              <option value="a4">A4, horizontal</option>
            </select>
          </label>
          <div className="imprimir-acciones">
            <button className="gc-btn gc-btn-primary" disabled={trabajando} onClick={() => void generar('pdf')}>
              {trabajando && <Loader2 className="w-3.5 h-3.5 animate-spin" />} PDF
            </button>
            <button className="gc-btn" disabled={trabajando} onClick={() => void generar('png')}>PNG</button>
          </div>
          {estado && <div className="imprimir-estado" role="status">{estado}</div>}
        </div>
      )}
    </div>
  )
}
