"""LLM layer (Mukul, C1). The only place provider-specific code may live."""

from debugagent.llm.router import (
    LLMAuthError,
    LLMConfigError,
    LLMError,
    LLMRouter,
    LLMToolCallInvalid,
    LLMToolTurnLimit,
    LLMToolUnsupported,
    LLMUnavailable,
    Route,
    StructuredOutputError,
    StructuredResult,
    ToolLoopResult,
)
from debugagent.llm.tools import (
    MAX_TOOL_TURNS,
    TOOL_CHOICE_MODES,
    ToolCall,
    ToolCallError,
    ToolDefinition,
    ToolDefinitionError,
    ToolResultMessage,
)

__all__ = [
    "LLMAuthError",
    "LLMConfigError",
    "LLMError",
    "LLMRouter",
    "LLMToolCallInvalid",
    "LLMToolTurnLimit",
    "LLMToolUnsupported",
    "LLMUnavailable",
    "MAX_TOOL_TURNS",
    "Route",
    "StructuredOutputError",
    "StructuredResult",
    "TOOL_CHOICE_MODES",
    "ToolCall",
    "ToolCallError",
    "ToolDefinition",
    "ToolDefinitionError",
    "ToolLoopResult",
    "ToolResultMessage",
]
