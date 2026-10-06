/**
 * Tests for logger utility
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

describe('logger', () => {
  beforeEach(() => {
    vi.spyOn(console, 'log').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'debug').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('in development mode', () => {
    it('should call console methods when DEV is true', async () => {
      // Re-import to get fresh module with current env
      vi.stubEnv('DEV', true)
      const { logger } = await import('./logger')

      logger.log('test log')
      logger.warn('test warn')
      logger.error('test error')
      logger.debug('test debug')

      // In test environment, DEV is true by default
      expect(console.log).toHaveBeenCalledWith('test log')
      expect(console.warn).toHaveBeenCalledWith('test warn')
      expect(console.error).toHaveBeenCalledWith('test error')
      expect(console.debug).toHaveBeenCalledWith('test debug')
    })
  })
})
