/**
 * Error boundary - FE-5.
 *
 * Wraps the React tree so an exception in any child (el mapa anterior, Chart,
 * any panel) does not blank the whole app. Without this, a single throw
 * leaves the user staring at a white screen with no recourse.
 *
 * A11Y (auditoría 2026-09-08 §5.4): la pantalla de fallback venía con
 * `text-gray-200` / `text-gray-400` / `text-red-300` sobre `bg-black/40`, que
 * son colores del tema oscuro genérico de Tailwind pintados sobre el fondo
 * papel de esta app: 1,17:1, 2,39:1 y 1,58:1 medidos. El mensaje que explica
 * la rotura era justo lo que no se veía. Ahora usa los tokens de la paleta
 * (`.error-boundary` en styles/index.css), que cumplen AA (≥4,5:1) en los dos
 * temas — el peor par medido es 6,24:1.
 */

import { Component, ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** Optional message rendered when an error is caught. */
  fallbackTitle?: string
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }): void {
     
    console.error('[ErrorBoundary] caught:', error, info)
  }

  handleReset = (): void => {
    this.setState({ error: null })
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div
          role="alert"
          className="error-boundary flex h-full w-full flex-col items-center justify-center p-8 text-center"
        >
          <h2 className="mb-2 text-lg font-semibold">
            {this.props.fallbackTitle ?? 'Algo se rompió en la interfaz'}
          </h2>
          <p className="eb-hint mb-4 max-w-md text-sm">
            La sección no pudo renderizarse. El resto de la aplicación sigue
            funcionando. Puedes reintentar o recargar la página.
          </p>
          <pre className="eb-detail mb-4 max-w-xl whitespace-pre-wrap break-words p-3 text-left text-xs">
            {this.state.error.message}
          </pre>
          <button
            type="button"
            onClick={this.handleReset}
            className="eb-retry px-4 py-2 text-sm font-medium"
          >
            Reintentar
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
