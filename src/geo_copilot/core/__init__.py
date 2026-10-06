"""
Módulo core con configuración, logging y cliente LLM.
"""

from geo_copilot.core.config import settings
from geo_copilot.core.llm_client import LLMClient
from geo_copilot.core.logging import get_logger, setup_logging

__all__ = ["settings", "setup_logging", "get_logger", "LLMClient"]
