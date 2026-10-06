"""
API REST y WebSocket para GEO_COPILOT.

Proporciona endpoints para:
- Procesamiento de consultas en lenguaje natural
- Aprobación HITL de SQL/código
- Streaming de respuestas vía WebSocket
- Gestión de sesiones
"""

from geo_copilot.api.app import create_app

__all__ = ["create_app"]
