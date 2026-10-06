/**
 * UI-ERRORBOUNDARY — aislamiento granular por zona.
 *
 * Antes había un único ErrorBoundary en la raíz: un throw en el mapa anterior/Chart
 * blanqueaba TODA la app (contradiciendo su propio mensaje "el resto sigue
 * funcionando"). Ahora hay límites por zona. Este test prueba la propiedad que
 * los hace útiles: un throw en una zona NO afecta a la zona hermana.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ErrorBoundary } from './ErrorBoundary'

function Boom(): JSX.Element {
  throw new Error('zona rota')
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ErrorBoundary — aislamiento por zona', () => {
  it('un throw en una zona muestra su fallback y NO tumba la zona hermana', () => {
    // Silencia el console.error esperado del boundary (ruido de test).
    vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <div>
        <ErrorBoundary fallbackTitle="Mapa roto">
          <Boom />
        </ErrorBoundary>
        <ErrorBoundary fallbackTitle="Chat ok">
          <div>contenido del chat</div>
        </ErrorBoundary>
      </div>,
    )

    // La zona rota muestra su fallback específico...
    expect(screen.getByText('Mapa roto')).toBeTruthy()
    // ...y la zona hermana sigue renderizando su contenido.
    expect(screen.getByText('contenido del chat')).toBeTruthy()
  })

  it('sin error, renderiza los hijos tal cual', () => {
    render(
      <ErrorBoundary>
        <div>ok</div>
      </ErrorBoundary>,
    )
    expect(screen.getByText('ok')).toBeTruthy()
  })
})

/**
 * Auditoría 2026-09-08 §5 (4) — la pantalla que explica la rotura tiene que
 * leerse.
 *
 * Venía con las escalas grises de Tailwind, pensadas para fondo oscuro,
 * pintadas sobre el fondo papel de esta app: `text-gray-200` sobre `--bg`
 * mide 1,17:1, `text-gray-400` 2,39:1 y `text-red-300` sobre `bg-black/40`
 * 1,58:1. El AA de texto normal pide 4,5:1.
 *
 * El ratio en sí vive en `styles/index.css` (`.error-boundary`, con los
 * números medidos anotados). Lo que este test fija es que el componente
 * siga enganchado a esa hoja y no vuelva a las clases de Tailwind: jsdom no
 * carga el CSS, así que medir el color computado aquí sería medir nada.
 */
describe('ErrorBoundary — legibilidad del fallback', () => {
  const renderRoto = () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const { container } = render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    )
    return container.querySelector('[role="alert"]') as HTMLElement
  }

  it('usa los tokens de la paleta, no las escalas grises de Tailwind', () => {
    const alert = renderRoto()
    expect(alert.className).toContain('error-boundary')
    expect(alert.querySelector('.eb-hint')).not.toBeNull()
    expect(alert.querySelector('.eb-detail')).not.toBeNull()
    expect(alert.querySelector('.eb-retry')).not.toBeNull()

    // Ninguna clase de color de Tailwind puede sobrevivir aquí dentro: son
    // colores absolutos que ignoran el tema y produjeron el 1,17:1.
    const html = alert.outerHTML
    for (const clase of ['text-gray-', 'text-red-', 'bg-black/', 'bg-blue-', 'text-white']) {
      expect(html).not.toContain(clase)
    }
  })

  it('el mensaje del error sigue siendo visible en el fallback', () => {
    renderRoto()
    expect(screen.getByText('zona rota')).toBeTruthy()
  })
})
