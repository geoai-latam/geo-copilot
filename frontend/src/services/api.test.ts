/**
 * Tests for API service
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { queryApi, sessionApi, approvalApi, metadataApi, healthApi, discoveryApi } from './api'

describe('API Service', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  describe('queryApi', () => {
    it('should send query request', async () => {
      const mockResponse = {
        success: true,
        message: 'Query processed',
        session_id: 'test-session',
        results: { data: [] },
        visualizations: null,
      }

      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockResponse),
      } as Response)

      const result = await queryApi.process({
        query: 'Show me all buildings',
        session_id: 'test-session',
      })

      expect(fetch).toHaveBeenCalledWith(
        '/api/v1/query/',
        expect.objectContaining({
          method: 'POST',
          headers: expect.objectContaining({
            'Content-Type': 'application/json',
          }),
        })
      )
      expect(result).toEqual(mockResponse)
    })
  })

  describe('sessionApi', () => {
    it('should create a new session', async () => {
      const mockSession = {
        session_id: 'new-session-123',
        created_at: '2024-01-01T00:00:00Z',
        message_count: 0,
      }

      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockSession),
      } as Response)

      const result = await sessionApi.create()

      expect(fetch).toHaveBeenCalledWith(
        '/api/v1/session/',
        expect.objectContaining({
          method: 'POST',
        })
      )
      expect(result.session_id).toBe('new-session-123')
    })

    it('should reset a session', async () => {
      const mockSession = {
        session_id: 'session-123',
        created_at: '2024-01-01',
        updated_at: '2024-01-01',
        message_count: 0,
      }

      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockSession),
      } as Response)

      const result = await sessionApi.reset('session-123')

      expect(fetch).toHaveBeenCalledWith(
        '/api/v1/session/session-123/reset',
        expect.objectContaining({
          method: 'POST',
        })
      )
      expect(result.session_id).toBe('session-123')
    })

    it('S0.3: exists() es true si la sesión responde', async () => {
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true, status: 200,
        json: () => Promise.resolve({ session_id: 's1' }),
      } as Response)
      await expect(sessionApi.exists('s1')).resolves.toBe(true)
      expect(fetch).toHaveBeenCalledWith('/api/v1/session/s1', expect.anything())
    })

    it('S0.3: exists() es false con 404', async () => {
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: false, status: 404, statusText: 'Not Found',
        json: () => Promise.resolve({ detail: 'Session s1 not found' }),
      } as Response)
      await expect(sessionApi.exists('s1')).resolves.toBe(false)
    })

    it('S0.3: exists() propaga los errores que no son 404', async () => {
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: false, status: 500, statusText: 'Internal Server Error',
        json: () => Promise.resolve({ detail: 'boom' }),
      } as Response)
      await expect(sessionApi.exists('s1')).rejects.toThrow()
    })
  })

  describe('approvalApi', () => {
    it('should submit approval', async () => {
      const mockResult = {
        success: true,
        message: 'Approved',
      }

      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockResult),
      } as Response)

      const result = await approvalApi.submit('apr-1', {
        action: 'approve',
        session_id: 'test-session',
      })

      expect(fetch).toHaveBeenCalledWith(
        '/api/v1/approval/apr-1',
        expect.objectContaining({
          method: 'POST',
        })
      )
      expect(result.success).toBe(true)
    })
  })

  describe('metadataApi', () => {
    it('should list entities', async () => {
      const mockResponse = {
        entities: [
          { name: 'buildings', description: 'Building data' },
          { name: 'roads', description: 'Road network' },
        ],
        total: 2,
      }

      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockResponse),
      } as Response)

      const result = await metadataApi.listEntities()

      expect(fetch).toHaveBeenCalledWith(
        '/api/v1/metadata/entities',
        expect.objectContaining({
          headers: expect.objectContaining({
            'Content-Type': 'application/json',
          }),
        })
      )
      expect(result.entities).toHaveLength(2)
      expect(result.total).toBe(2)
    })
  })

  describe('healthApi', () => {
    it('should check system health', async () => {
      const mockHealth = {
        status: 'healthy',
        database: 'connected',
        llm: 'configured',
      }

      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockHealth),
      } as Response)

      const result = await healthApi.check()

      expect(fetch).toHaveBeenCalledWith('/health')
      expect(result.status).toBe('healthy')
    })
  })

  describe('Error handling', () => {
    it('should throw ApiError on non-ok response', async () => {
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
        json: () => Promise.resolve({ detail: 'Server error' }),
      } as Response)

      await expect(sessionApi.create()).rejects.toThrow()
    })

    it('should handle network errors', async () => {
      vi.mocked(fetch).mockRejectedValueOnce(new Error('Network error'))

      await expect(sessionApi.create()).rejects.toThrow('Network error')
    })

    /**
     * Auditoría 2026-09-08 §5 (5): el 413 lo emite nginx
     * (`client_max_body_size 50m`), no FastAPI, así que el cuerpo es HTML y
     * `response.json()` revienta. El mensaje quedaba en "Error: Payload Too
     * Large", que no dice qué hacer. La causa siempre es la misma: el
     * map_context lleva demasiadas capas adjuntas.
     */
    it('traduce el 413 de nginx (cuerpo HTML) a algo accionable', async () => {
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: false,
        status: 413,
        statusText: 'Payload Too Large',
        json: () => Promise.reject(new SyntaxError('Unexpected token <')),
      } as unknown as Response)

      await expect(sessionApi.create()).rejects.toThrow(/oculta o quita alguna capa/i)
    })
  })

  // MIN-DISCOVERY-HEADERS
  describe('cabeceras y propagación de sesión', () => {
    const okJson = (body: unknown) => ({
      ok: true, status: 200, json: () => Promise.resolve(body),
    } as Response)

    afterEach(() => {
      vi.unstubAllEnvs()
    })

    it('discoveryApi.load propaga session_id en el body (#35)', async () => {
      vi.mocked(fetch).mockResolvedValueOnce(okJson({ type: 'geojson', geojson: null, name: 'x' }))
      await discoveryApi.load({ id: 'item-1' } as never, { sessionId: 's1' })
      const init = vi.mocked(fetch).mock.calls[0][1] as RequestInit
      expect(JSON.parse(init.body as string).session_id).toBe('s1')
    })

    it('el frontend NUNCA envía X-API-Key (dev / proxy)', async () => {
      vi.mocked(fetch).mockResolvedValueOnce(okJson({ entities: [], total: 0 }))
      await metadataApi.listEntities()
      const headers = (vi.mocked(fetch).mock.calls[0][1] as RequestInit).headers as Record<string, string>
      expect(headers['X-API-Key']).toBeUndefined()
    })

    it('FRT-01 (BFF): aunque VITE_API_KEY esté seteada, el bundle NO adjunta X-API-Key', async () => {
      // La key la inyecta nginx server-side (docker/nginx.frontend.conf), no el
      // navegador. El bundle no debe filtrarla nunca.
      vi.resetModules()
      vi.stubEnv('VITE_API_KEY', 'secreto-123')
      const fresh = await import('./api')
      vi.mocked(fetch).mockResolvedValueOnce(okJson({ entities: [], total: 0 }))
      await fresh.metadataApi.listEntities()
      const headers = (vi.mocked(fetch).mock.calls[0][1] as RequestInit).headers as Record<string, string>
      expect(headers['X-API-Key']).toBeUndefined()
    })
  })
})
