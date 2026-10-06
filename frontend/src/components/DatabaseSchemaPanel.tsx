/**
 * Panel del drawer "BD" — muestra el schema REAL de la BD conectada.
 *
 * NO hay hardcoding: consume `GET /api/v1/metadata/tables` que devuelve
 * el resultado del introspector (information_schema + geometry_columns
 * + pg_catalog). Renderiza un árbol schemas → tablas → columnas con
 * iconos por tipo (geometría / texto / numérico / fecha).
 */

import { useEffect, useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  Hash,
  Type,
  Calendar,
  Map as MapIcon,
  Key,
  AlertCircle,
  Database,
  RefreshCw,
  Layers as LayersIcon,
} from 'lucide-react'

import { metadataApi } from '@/services/api'
import { useMapStore } from '@/stores/mapStore'
import { capaDelUsuario } from '@/lib/operaciones'
import { logger } from '@/utils/logger'

/** Mapea el geometry_type de PostGIS al tipo que usan las capas teseladas. */
function tileGeomKind(gt: string | null): 'polygon' | 'line' | 'point' {
  const t = (gt || '').toLowerCase()
  if (t.includes('point')) return 'point'
  if (t.includes('line')) return 'line'
  return 'polygon'
}

interface Column {
  name: string
  type: string
  nullable: boolean
  primary_key: boolean
  is_geometry: boolean
  geometry_type: string | null
  srid: number | null
}

interface Table {
  name: string
  qualified_name: string
  estimated_rows: number | null
  geometry_column: string | null
  geometry_type: string | null
  srid: number | null
  columns: Column[]
}

interface Schema {
  name: string
  tables: Table[]
}

interface TablesResponse {
  schemas: Schema[]
  total_tables: number
  total_schemas: number
  connected: boolean
}

function columnIcon(col: Column) {
  if (col.is_geometry) return <MapIcon size={11} className="text-emerald-500" />
  if (col.primary_key) return <Key size={11} className="text-amber-500" />
  if (/int|float|numeric|double|real/i.test(col.type)) {
    return <Hash size={11} className="text-blue-500" />
  }
  if (/date|time/i.test(col.type)) {
    return <Calendar size={11} className="text-purple-500" />
  }
  return <Type size={11} className="text-gray-400" />
}

function formatRowCount(n: number | null): string {
  if (n == null || n < 0) return ''
  if (n < 1000) return `${n}`
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`
  return `${(n / 1_000_000).toFixed(2)}M`
}

export function DatabaseSchemaPanel() {
  const [data, setData] = useState<TablesResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [openSchemas, setOpenSchemas] = useState<Set<string>>(new Set())
  const [openTables, setOpenTables] = useState<Set<string>>(new Set())
  const addTableTiles = useMapStore((s) => s.addTableTiles)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await metadataApi.listTables()
      setData(response)
      // Auto-abrir el primer schema si solo hay uno o pocos
      if (response.schemas.length === 1) {
        setOpenSchemas(new Set([response.schemas[0].name]))
      }
    } catch (e) {
      logger.error('[DatabaseSchemaPanel] failed', e)
      setError(e instanceof Error ? e.message : 'Error desconocido')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const toggleSchema = (name: string) => {
    setOpenSchemas((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const toggleTable = (qname: string) => {
    setOpenTables((prev) => {
      const next = new Set(prev)
      if (next.has(qname)) next.delete(qname)
      else next.add(qname)
      return next
    })
  }

  if (loading && !data) {
    return (
      <div className="db-panel">
        <div className="db-panel-loading">
          <RefreshCw className="animate-spin" size={14} />
          <span>Cargando schema de la BD…</span>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="db-panel">
        <div className="db-panel-error">
          <AlertCircle size={14} />
          <span>{error}</span>
          <button className="disc-btn ghost" onClick={load}>
            Reintentar
          </button>
        </div>
      </div>
    )
  }

  if (!data || !data.connected) {
    return (
      <div className="db-panel">
        <div className="db-panel-empty">
          <Database size={20} className="text-gray-400" />
          <p>No hay conexión a la base de datos.</p>
          <p className="text-xs text-gray-500">
            Verifica que el contenedor `geo_copilot_db` esté corriendo.
          </p>
        </div>
      </div>
    )
  }

  if (data.total_tables === 0) {
    return (
      <div className="db-panel">
        <div className="db-panel-empty">
          <Database size={20} className="text-gray-400" />
          <p>La BD está conectada pero no tiene tablas accesibles.</p>
          <p className="text-xs text-gray-500">
            ¿Quizás el usuario `{import.meta.env.VITE_DB_USER || 'geo_user'}` no
            tiene permisos GRANT sobre los schemas del catastro?
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="db-panel">
      <div className="db-panel-head">
        <span className="db-panel-summary">
          {data.total_schemas} schema{data.total_schemas === 1 ? '' : 's'} ·{' '}
          {data.total_tables} tabla{data.total_tables === 1 ? '' : 's'}
        </span>
        <button
          className="disc-icon-btn"
          onClick={load}
          title="Recargar schema"
        >
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="db-panel-tree">
        {data.schemas.map((schema) => {
          const isOpen = openSchemas.has(schema.name)
          return (
            <div key={schema.name} className="db-schema">
              <button
                className="db-schema-row"
                onClick={() => toggleSchema(schema.name)}
              >
                {isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                <span className="db-schema-name">{schema.name}</span>
                <span className="db-schema-count">
                  {schema.tables.length} tabla{schema.tables.length === 1 ? '' : 's'}
                </span>
              </button>

              {isOpen && (
                <div className="db-tables">
                  {schema.tables.map((table) => {
                    const tableOpen = openTables.has(table.qualified_name)
                    const rowText = formatRowCount(table.estimated_rows)
                    return (
                      <div key={table.qualified_name} className="db-table">
                        <button
                          className="db-table-row"
                          onClick={() => toggleTable(table.qualified_name)}
                        >
                          {tableOpen ? (
                            <ChevronDown size={11} />
                          ) : (
                            <ChevronRight size={11} />
                          )}
                          {table.geometry_column ? (
                            <MapIcon size={11} className="text-emerald-500" />
                          ) : (
                            <Database size={11} className="text-gray-400" />
                          )}
                          <span className="db-table-name">{table.name}</span>
                          {table.geometry_type && (
                            <span
                              className="db-table-geom-badge"
                              title={`SRID ${table.srid}`}
                            >
                              {table.geometry_type}
                            </span>
                          )}
                          {rowText && (
                            <span className="db-table-rows">{rowText}</span>
                          )}
                        </button>
                        {table.geometry_column && (
                          <button
                            className="db-table-tile"
                            title="Teselar en el mapa (vector tiles MVT — no trae todo el GeoJSON)"
                            onClick={() =>
                              capaDelUsuario(addTableTiles(
                                schema.name,
                                table.name,
                                tileGeomKind(table.geometry_type),
                                table.name,
                              ))
                            }
                          >
                            <LayersIcon size={10} aria-hidden /> Teselar
                          </button>
                        )}

                        {tableOpen && (
                          <ul className="db-columns">
                            {table.columns.map((col) => (
                              <li key={col.name} className="db-column">
                                {columnIcon(col)}
                                <span className="db-column-name">
                                  {col.name}
                                </span>
                                <span className="db-column-type">
                                  {col.is_geometry
                                    ? `${col.geometry_type || 'geometry'} · ${col.srid || '?'}`
                                    : col.type}
                                </span>
                                {!col.nullable && !col.primary_key && (
                                  <span className="db-column-flag">NOT NULL</span>
                                )}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
