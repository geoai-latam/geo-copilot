/**
 * SEC-XSS-POPUP — FeaturePopup sanea el HTML de `description` de servicios
 * ArcGIS externos con DOMPurify.
 *
 * El saneo previo (regex que solo quitaba <script>) dejaba pasar <img onerror>,
 * <svg onload>, javascript:. Un servicio malicioso cargado vía Discovery podía
 * ejecutar JS y robar tokens. Aquí inyectamos esos vectores y verificamos que
 * el HTML renderizado NO los contiene, pero SÍ conserva el HTML de presentación.
 */
import { describe, it, expect, afterEach } from 'vitest'
import { render } from '@testing-library/react'
import { FeaturePopup } from './FeaturePopup'
import { useMapStore } from '@/stores'

function showDescription(description: string) {
  // properties vacío → el componente entra a la rama de `description` (HTML).
  useMapStore.setState({
    selectedFeature: {
      properties: {},
      source: 'imagery',
      description,
      screenX: 20,
      screenY: 20,
    },
  } as never)
}

afterEach(() => {
  useMapStore.setState({ selectedFeature: null } as never)
})

describe('FeaturePopup — saneo XSS del description (DOMPurify)', () => {
  it('neutraliza <img onerror>, <svg onload>, javascript: y <script>', () => {
    showDescription(
      '<img src=x onerror="window.__xss=1">' +
      '<svg onload="window.__xss=2"></svg>' +
      '<a href="javascript:alert(1)">click</a>' +
      '<script>window.__xss=3</script>',
    )
    const { container } = render(<FeaturePopup />)
    const html = (container.querySelector('.feature-popup-description')?.innerHTML ?? '').toLowerCase()
    expect(html).not.toContain('onerror')
    expect(html).not.toContain('onload')
    expect(html).not.toContain('javascript:')
    expect(html).not.toContain('<script')
  })

  it('conserva el HTML de presentación seguro (tablas, negritas, enlaces http)', () => {
    showDescription('<b>Municipio</b>: <a href="https://ejemplo.co">ver</a><table><tr><td>1</td></tr></table>')
    const { container } = render(<FeaturePopup />)
    const el = container.querySelector('.feature-popup-description')
    expect(el).not.toBeNull()
    const html = (el?.innerHTML ?? '').toLowerCase()
    expect(html).toContain('<b>')
    expect(html).toContain('municipio')
    expect(html).toContain('href="https://ejemplo.co"')
    expect(html).toContain('<table')
  })
})
