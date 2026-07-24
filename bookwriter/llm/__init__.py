"""Accesso all'LLM locale (LM Studio, API OpenAI-compatibile)."""

from bookwriter.llm.client import LLMError, LMStudioClient, ModelInfo

__all__ = ["LLMError", "LMStudioClient", "ModelInfo"]
