import { useState, useMemo } from 'react'
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  MapPin,
  Hash,
  Type,
  Calendar,
  ToggleLeft,
} from 'lucide-react'

interface GeoFeature {
  id?: string | number
  type?: string
  properties?: Record<string, unknown>
  geometry?: unknown
}

interface GeoDataTableProps {
  features: GeoFeature[]
  pageSize?: number
  maxHeight?: string
}

// Detectar tipo de dato para icono
function getTypeIcon(value: unknown) {
  if (value === null || value === undefined) return null
  if (typeof value === 'number') return <Hash className="w-3 h-3 text-cyan-400/60" />
  if (typeof value === 'boolean') return <ToggleLeft className="w-3 h-3 text-amber-400/60" />
  if (typeof value === 'string') {
    if (/^\d{4}-\d{2}-\d{2}/.test(value)) return <Calendar className="w-3 h-3 text-purple-400/60" />
    return <Type className="w-3 h-3 text-emerald-400/60" />
  }
  return null
}

// Formatear valor para mostrar
function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'boolean') return value ? 'Si' : 'No'
  if (typeof value === 'number') {
    if (Number.isInteger(value)) return value.toLocaleString()
    return value.toLocaleString(undefined, { maximumFractionDigits: 4 })
  }
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return '[Objeto]'
    }
  }
  return String(value)
}

// Formatear nombre de columna
function formatColumnName(name: string): string {
  return name
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .split(' ')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ')
}

export function GeoDataTable({ features, pageSize = 10, maxHeight = '300px' }: GeoDataTableProps) {
  const [currentPage, setCurrentPage] = useState(0)
  const [sortColumn, setSortColumn] = useState<string | null>(null)
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc')

  // Extraer propiedades planas de las features
  const { rows, columns } = useMemo(() => {
    const allKeys = new Set<string>()
    const extractedRows: Record<string, unknown>[] = []

    features.forEach((feature, index) => {
      const row: Record<string, unknown> = {
        _idx: index + 1,
      }

      // Si tiene properties, extraerlas
      if (feature.properties && typeof feature.properties === 'object') {
        Object.entries(feature.properties).forEach(([key, value]) => {
          // Ignorar campos de geometria
          if (key.toLowerCase() === 'geometry' || key.toLowerCase() === 'geom') return
          allKeys.add(key)
          row[key] = value
        })
      }
      // Si no tiene properties, usar los campos directamente
      else {
        Object.entries(feature).forEach(([key, value]) => {
          if (key === 'type' || key === 'geometry' || key === 'geom') return
          if (key === 'properties' && typeof value === 'object') return
          allKeys.add(key)
          row[key] = value
        })
      }

      extractedRows.push(row)
    })

    // Ordenar columnas: _idx primero, luego alfabeticamente
    const sortedColumns = ['_idx', ...Array.from(allKeys).sort()]

    return { rows: extractedRows, columns: sortedColumns }
  }, [features])

  // Ordenar datos
  const sortedRows = useMemo(() => {
    if (!sortColumn) return rows

    return [...rows].sort((a, b) => {
      const aVal = a[sortColumn]
      const bVal = b[sortColumn]

      if (aVal === bVal) return 0
      if (aVal === null || aVal === undefined) return 1
      if (bVal === null || bVal === undefined) return -1

      const comparison = typeof aVal === 'number' && typeof bVal === 'number'
        ? aVal - bVal
        : String(aVal).localeCompare(String(bVal))

      return sortDirection === 'asc' ? comparison : -comparison
    })
  }, [rows, sortColumn, sortDirection])

  // Paginar
  const totalPages = Math.ceil(sortedRows.length / pageSize)
  const paginatedRows = sortedRows.slice(
    currentPage * pageSize,
    (currentPage + 1) * pageSize
  )

  const handleSort = (column: string) => {
    if (sortColumn === column) {
      setSortDirection((prev) => (prev === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortColumn(column)
      setSortDirection('asc')
    }
  }

  if (features.length === 0) {
    return (
      <div className="geo-table-empty">
        <MapPin className="w-8 h-8 mx-auto mb-2 opacity-30" />
        <p>No hay datos para mostrar</p>
      </div>
    )
  }

  return (
    <div className="geo-table-container">
      {/* Table wrapper with scroll */}
      <div className="geo-table-wrapper" style={{ maxHeight }}>
        <table className="geo-table">
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  key={column}
                  onClick={() => handleSort(column)}
                  className={column === '_idx' ? 'geo-table-th-idx' : 'geo-table-th'}
                >
                  <div className="geo-table-th-content">
                    <span>{column === '_idx' ? '#' : formatColumnName(column)}</span>
                    <span className="geo-table-sort-icon">
                      {sortColumn === column ? (
                        sortDirection === 'asc' ? (
                          <ArrowUp className="w-3 h-3" />
                        ) : (
                          <ArrowDown className="w-3 h-3" />
                        )
                      ) : (
                        <ArrowUpDown className="w-3 h-3 opacity-30" />
                      )}
                    </span>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {paginatedRows.map((row, rowIndex) => (
              <tr key={rowIndex} className="geo-table-row">
                {columns.map((column) => (
                  <td
                    key={column}
                    className={column === '_idx' ? 'geo-table-td-idx' : 'geo-table-td'}
                    title={formatValue(row[column])}
                  >
                    {column === '_idx' ? (
                      <span className="geo-table-idx">{row[column] as number}</span>
                    ) : (
                      <div className="geo-table-cell">
                        {getTypeIcon(row[column])}
                        <span className="geo-table-value">{formatValue(row[column])}</span>
                      </div>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="geo-table-pagination">
          <span className="geo-table-pagination-info">
            {currentPage * pageSize + 1}-{Math.min((currentPage + 1) * pageSize, sortedRows.length)} de {sortedRows.length}
          </span>

          <div className="geo-table-pagination-controls">
            <button
              onClick={() => setCurrentPage(0)}
              disabled={currentPage === 0}
              className="geo-table-pagination-btn"
              title="Primera pagina"
            >
              <ChevronsLeft className="w-4 h-4" />
            </button>
            <button
              onClick={() => setCurrentPage((prev) => Math.max(0, prev - 1))}
              disabled={currentPage === 0}
              className="geo-table-pagination-btn"
              title="Pagina anterior"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>

            <span className="geo-table-pagination-page">
              {currentPage + 1} / {totalPages}
            </span>

            <button
              onClick={() => setCurrentPage((prev) => Math.min(totalPages - 1, prev + 1))}
              disabled={currentPage === totalPages - 1}
              className="geo-table-pagination-btn"
              title="Pagina siguiente"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
            <button
              onClick={() => setCurrentPage(totalPages - 1)}
              disabled={currentPage === totalPages - 1}
              className="geo-table-pagination-btn"
              title="Ultima pagina"
            >
              <ChevronsRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
