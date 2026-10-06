/**
 * maplibre-gl 6 es solo ESM y busca su worker junto a su propio archivo (`import.meta.url`).
 * Tras el bundle ese archivo ya no existe (en dev tampoco: el optimizador de deps de vite no lo
 * copia), y sin worker no se procesan las geometrías: el mapa no pinta ni se puede hacer clic.
 * `?worker&url` hace que vite compile el worker como un chunk propio (con su `maplibre-gl-shared`)
 * y nos dé su URL. Se importa una vez, antes de crear un mapa.
 */
import { setWorkerUrl } from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'

setWorkerUrl(workerUrl)
