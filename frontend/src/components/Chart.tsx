import {
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  ZAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import type { Visualization } from '@/types'
import { datosParaGrafico } from './chart.helpers'

interface ChartProps {
  visualization: Visualization
}

// Colores para las gráficas
const COLORS = [
  '#10b981', // green-500
  '#3b82f6', // blue-500
  '#f59e0b', // amber-500
  '#ef4444', // red-500
  '#8b5cf6', // violet-500
  '#ec4899', // pink-500
  '#06b6d4', // cyan-500
  '#f97316', // orange-500
]

export function Chart({ visualization }: ChartProps) {
  const { config, data } = visualization

  const chartType = config?.chart_type as string || 'bar'
  const chartData = (data as Record<string, unknown>[]) || []

  // Antes había detectXKey/detectYKey que adivinaban los ejes buscando
  // nombres de columna ('cantidad', 'total', 'nombre', 'categoria'...).
  // Para data real esos heurísticos invertían ejes y el usuario veía
  // gráficos engañosos sin saberlo. Ahora exigimos x_key/y_key explícitos
  // del backend (InsightsAgent debe poblarlos).
  const xKey = config?.x_key as string | undefined
  const yKey = config?.y_key as string | undefined

  if (!chartData || chartData.length === 0) {
    return (
      <div className="h-48 flex items-center justify-center text-gray-500">
        No hay datos para visualizar
      </div>
    )
  }

  if (!xKey || !yKey) {
    return (
      <div className="h-48 flex items-center justify-center text-amber-400 text-sm px-4 text-center">
        ⚠️ El backend no especificó qué columnas usar como ejes (x_key/y_key).
        No se renderiza el gráfico para evitar invertir los ejes sin avisar.
      </div>
    )
  }

  const formattedData = datosParaGrafico(chartData, xKey, yKey, chartType)

  // Renderizar según tipo de gráfico
  if (chartType === 'pie') {
    return (
      <ResponsiveContainer width="100%" height={250}>
        <PieChart>
          <Pie
            data={formattedData}
            dataKey={yKey}
            nameKey={xKey}
            cx="50%"
            cy="50%"
            outerRadius={80}
            label={({ name, percent }) => `${name}: ${(percent * 100).toFixed(0)}%`}
          >
            {formattedData.map((_, index) => (
              <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              backgroundColor: '#1f2937',
              border: '1px solid #374151',
              borderRadius: '0.5rem'
            }}
            labelStyle={{ color: '#9ca3af' }}
          />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    )
  }

  if (chartType === 'line') {
    return (
      <ResponsiveContainer width="100%" height={250}>
        <LineChart data={formattedData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis
            dataKey={xKey}
            tick={{ fill: '#9ca3af', fontSize: 12 }}
            axisLine={{ stroke: '#4b5563' }}
          />
          <YAxis
            tick={{ fill: '#9ca3af', fontSize: 12 }}
            axisLine={{ stroke: '#4b5563' }}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: '#1f2937',
              border: '1px solid #374151',
              borderRadius: '0.5rem'
            }}
            labelStyle={{ color: '#9ca3af' }}
          />
          <Legend />
          <Line
            type="monotone"
            dataKey={yKey}
            stroke="#10b981"
            strokeWidth={2}
            dot={{ fill: '#10b981', strokeWidth: 2 }}
          />
        </LineChart>
      </ResponsiveContainer>
    )
  }

  // Dispersión: dos variables continuas (p. ej. correlación área vs población).
  // Antes caía al BarChart y pintaba un muro de barras sobre un eje categórico.
  if (chartType === 'scatter') {
    return (
      <ResponsiveContainer width="100%" height={250}>
        <ScatterChart margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis
            type="number"
            dataKey={xKey}
            name={xKey}
            tick={{ fill: '#9ca3af', fontSize: 12 }}
            axisLine={{ stroke: '#4b5563' }}
          />
          <YAxis
            type="number"
            dataKey={yKey}
            name={yKey}
            tick={{ fill: '#9ca3af', fontSize: 12 }}
            axisLine={{ stroke: '#4b5563' }}
          />
          <ZAxis range={[50, 50]} />
          <Tooltip
            cursor={{ strokeDasharray: '3 3' }}
            contentStyle={{
              backgroundColor: '#1f2937',
              border: '1px solid #374151',
              borderRadius: '0.5rem',
            }}
            labelStyle={{ color: '#9ca3af' }}
          />
          <Legend />
          <Scatter data={formattedData} fill="#10b981" />
        </ScatterChart>
      </ResponsiveContainer>
    )
  }

  // Default: Bar chart
  return (
    <ResponsiveContainer width="100%" height={250}>
      <BarChart data={formattedData}>
        <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
        <XAxis
          dataKey={xKey}
          tick={{ fill: '#9ca3af', fontSize: 12 }}
          axisLine={{ stroke: '#4b5563' }}
          interval={0}
          angle={-45}
          textAnchor="end"
          height={60}
        />
        <YAxis
          tick={{ fill: '#9ca3af', fontSize: 12 }}
          axisLine={{ stroke: '#4b5563' }}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: '#1f2937',
            border: '1px solid #374151',
            borderRadius: '0.5rem'
          }}
          labelStyle={{ color: '#9ca3af' }}
        />
        <Legend />
        <Bar
          dataKey={yKey}
          fill="#10b981"
          radius={[4, 4, 0, 0]}
        />
      </BarChart>
    </ResponsiveContainer>
  )
}
