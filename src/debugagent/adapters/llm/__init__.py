"""LLM adapter (Mukul, C1): the only place provider-specific code may live."""

from debugagent.adapters.llm.router import LLMRouter
from debugagent.adapters.llm.settings import Route, load_routes

__all__ = ["LLMRouter", "Route", "load_routes"]
