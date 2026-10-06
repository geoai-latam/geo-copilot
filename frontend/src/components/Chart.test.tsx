/**
 * Chart.tsx — FIX-CHART-SCATTER.
 *
 * Antes, chart_type='scatter' (el caso insignia de correlación que emite el
 * backend analítico) caía al BarChart por defecto y pintaba un muro de barras
 * sobre un eje categórico. Aquí verificamos que ahora enruta al ScatterChart.
 *
 * Se mockea recharts: en jsdom el ResponsiveContainer tiene tamaño 0 y no
 * dibuja SVG real, así que probamos el RAMAJE (qué componente se elige), que
 * es exactamente el bug. El render pixel-real se valida en el E2E de navegador.
 */
import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import type { Visualization } from '@/types'

vi.mock('recharts', () => {
  const make = (name: string) =>
    ({ children }: { children?: React.ReactNode }) => (
      <div data-testid={name}>{children}</div>
    )
  return {
    ResponsiveContainer: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
    BarChart: make('BarChart'), Bar: make('Bar'),
    LineChart: make('LineChart'), Line: make('Line'),
    PieChart: make('PieChart'), Pie: make('Pie'), Cell: make('Cell'),
    ScatterChart: make('ScatterChart'), Scatter: make('Scatter'),
    XAxis: make('XAxis'), YAxis: make('YAxis'), ZAxis: make('ZAxis'),
    CartesianGrid: make('CartesianGrid'), Tooltip: make('Tooltip'), Legend: make('Legend'),
  }
})

// Import DESPUÉS del mock (vi.mock se iza, pero el import del SUT debe resolver el mock).
const { Chart } = await import('./Chart')
const { datosParaGrafico } = await import('./chart.helpers')

const viz = (chart_type: string): Visualization => ({
  type: 'chart',
  config: { chart_type: chart_type as never, x_key: 'area', y_key: 'poblacion' },
  data: [
    { area: 100, poblacion: 2000 },
    { area: 300, poblacion: 5000 },
  ],
})

describe('Chart — enrutamiento por chart_type', () => {
  it('scatter → ScatterChart (NO BarChart)', () => {
    const { queryByTestId } = render(<Chart visualization={viz('scatter')} />)
    expect(queryByTestId('ScatterChart')).toBeTruthy()
    expect(queryByTestId('Scatter')).toBeTruthy()
    expect(queryByTestId('BarChart')).toBeNull()
  })

  it('bar → BarChart (sin regresión)', () => {
    const { queryByTestId } = render(<Chart visualization={viz('bar')} />)
    expect(queryByTestId('BarChart')).toBeTruthy()
    expect(queryByTestId('ScatterChart')).toBeNull()
  })

  it('line → LineChart (sin regresión)', () => {
    const { queryByTestId } = render(<Chart visualization={viz('line')} />)
    expect(queryByTestId('LineChart')).toBeTruthy()
  })

  it('sin x_key/y_key no renderiza gráfico (aviso, no ejes invertidos)', () => {
    const bad: Visualization = { type: 'chart', config: { chart_type: 'scatter' }, data: [{ a: 1 }] }
    const { queryByTestId } = render(<Chart visualization={bad} />)
    expect(queryByTestId('ScatterChart')).toBeNull()
  })
})

describe('datosParaGrafico — la categoría no se convierte en número', () => {
  const filas = [{ id: '004503009001', area: '2165.01' }, { id: '004503009026', area: 1805.04 }]

  it('barras: el código del eje X conserva sus ceros; el área pasa a número', () => {
    const out = datosParaGrafico(filas, 'id', 'area', 'bar')
    expect(out.map((f) => f.id)).toEqual(['004503009001', '004503009026'])
    expect(out.map((f) => f.area)).toEqual([2165.01, 1805.04])
  })

  it('dispersión: los dos ejes son medidas', () => {
    const out = datosParaGrafico([{ x: '3', y: '4' }], 'x', 'y', 'scatter')
    expect(out[0]).toEqual({ x: 3, y: 4 })
  })

  it('un texto vacío no se vuelve 0', () => {
    expect(datosParaGrafico([{ c: 'a', v: '' }], 'c', 'v', 'bar')[0].v).toBe('')
  })
})
