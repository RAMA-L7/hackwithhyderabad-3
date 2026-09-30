"""P2 tool-calling data types for the Hub-and-Spoke Coordinator (docs/adr-001-hub-spoke-coordinator.md).

P2 is transport only. This module represents tool definitions, assistant tool calls and tool results,
and serialises them into the OpenAI-compatible wire format the router sends. It **executes nothing**:
no tool is invoked, no worker is contacted, and the P1 registry is not consulted here. The caller
supplies an execution callback; what runs behind it is P4+ (Memory Specialist, Code/Log Verifier,
Patch Generator) and parallel fan-out is P5.

Provider note, deliberate and not emulated: `tools` and `response_format` are never sent together.
The repository already records that Space Bunny (OpenRouter) rejects strict routing with a 404
(`router.py:1-9`, MK0 measurement), and combining tool calling with a forced JSON schema is the same
family of provider-specific constraint. In tool mode the final answer is instead validated locally by
`router.extract_json` + `router.validate` + the caller's `check`, which is the fail-closed path the
codebase already uses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Tool-choice forms. The wire value is either one of these two strings, or the *name* of a tool to
# pin. "required" is deliberately unsupported: the Coordinator decides whether to delegate, so forcing
# a call would let a model spawn work the architecture did not ask for. Naming an absent tool fails.
TOOL_CHOICE_MODES = ("auto", "none")

# Refuse a runaway loop. Each turn is a full provider round trip plus tool execution, so the bound is
# small on purpose; a coordinator that cannot finish in four turns has lost the thread.
MAX_TOOL_TURNS = 4

# --- P3-B resource limits --------------------------------------------------------
# P2 bounded TURNS only. These bound the other three axes, and they were left unbounded knowingly
# (ADR 001, "Known limitation (P2): no resource limits on tool payloads"). The values are sized
# from measured real payloads, not guessed: the six shipped seed cases render to 796-970 characters,
# and an agent tool result is a file excerpt or a log tail, not a document.
#
# Every one of these REJECTS. None of them truncates. A truncated tool result is worse than an
# oversized one: the model cannot tell a clipped log from a complete one, and a clipped log is
# engineering evidence that looks whole. Failing closed keeps the ambiguity visible.
#
# Ownership follows the boundary each value protects:
#   - per-item sizes and the per-turn call count: the data boundary, `parse_tool_calls` and
#     `from_dict`, which are the only places that see an untrusted value before we act on it;
#   - cumulative conversation size: the loop, which is the only place history accumulates;
#   - tool-definition size: `ToolDefinition.from_dict`, before the definition is ever offered.

# One assistant turn may not request more than this many tool calls. Three workers is the whole
# planned roster, so 8 leaves headroom for a re-delegation without letting a single turn fan out
# without bound.
MAX_TOOL_CALLS_PER_TURN = 8

# Serialised `arguments` for one call. A TaskSpec is a few hundred characters; 16k is ~30x that and
# still small enough to read in a log when a delegation goes wrong.
MAX_TOOL_ARGUMENT_CHARS = 16_000

# `content` of one tool result. Sized for a file excerpt or log tail, not a whole file. This is the
# value that would otherwise be truncated to "fit"; it is refused instead.
MAX_TOOL_RESULT_CHARS = 32_000

# A tool `description` and its `input_schema`, combined, as offered to the provider.
MAX_TOOL_DEFINITION_CHARS = 8_000

# The whole accumulated message history, checked before every request. The per-item limits above
# each allow a turn to stay small, but the history GROWS across turns, so only the loop can see the
# running total. Roughly four maximum-size tool results plus overhead, which is beyond any planned
# Coordinator path and exists to stop an unbounded conversation rather than to constrain a real one.
MAX_CONVERSATION_CHARS = 256_000


class ToolDefinitionError(ValueError):
    """A tool definition is malformed. Fails closed, like SchemaError in schemas.py."""


class ToolCallError(ValueError):
    """An assistant tool call is malformed: no id, unknown tool, or unparseable arguments."""


class ToolResourceLimit(ToolCallError):
    """A tool payload exceeded a P3-B resource limit. The payload is refused, never clipped.

    Subclasses `ToolCallError` so every existing `except ToolCallError` handler keeps working: a
    limit breach is a malformed input from the caller's point of view, and no existing caller needs
    to learn a new exception type to stay correct. It is named separately so a host can tell a size
    rejection apart from a structural one.
    """


@dataclass(frozen=True)
class ToolDefinition:
    """One tool the provider may call. Three fields only: name, description, input schema."""

    name: str
    description: str
    input_schema: dict

    def to_payload(self) -> dict:
        """OpenAI-compatible function-tool payload. The router owns provider formatting."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    @classmethod
    def from_dict(cls, data: Any, label: str = "ToolDefinition") -> "ToolDefinition":
        errors: list[str] = []
        if not isinstance(data, dict):
            raise ToolDefinitionError(f"{label}: expected object, got {type(data).__name__}")
        unknown = sorted(set(data) - {"name", "description", "input_schema"})
        for key in unknown:
            errors.append(f"{label}.{key}: not allowed (fields are name, description, input_schema)")
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}.name: expected a non-empty string")
        elif not name.replace("_", "").replace("-", "").isalnum():
            errors.append(f"{label}.name: {name!r} is not a plain identifier")
        description = data.get("description")
        if not isinstance(description, str) or not description.strip():
            errors.append(f"{label}.description: expected a non-empty string")
        schema = data.get("input_schema")
        if not isinstance(schema, dict):
            errors.append(f"{label}.input_schema: expected object, got {type(schema).__name__}")
        elif schema.get("type") != "object":
            errors.append(f"{label}.input_schema: only object schemas are supported, "
                          f"got type={schema.get('type')!r}")
        elif not isinstance(schema.get("properties", {}), dict):
            errors.append(f"{label}.input_schema.properties: expected object")
        if errors:
            raise ToolDefinitionError(f"{label}: " + "; ".join(errors))
        # P3-B: bound the definition as offered, before it reaches the provider. Structural
        # validation passed above, so this is a size question only.
        offered = len(str(description)) + len(json.dumps(schema, sort_keys=True))
        if offered > MAX_TOOL_DEFINITION_CHARS:
            raise ToolDefinitionError(
                f"{label}: definition is {offered} chars, over the "
                f"{MAX_TOOL_DEFINITION_CHARS} limit (name, description and input_schema). "
                "Shorten the description; it is not truncated."
            )
        return cls(name=str(name), description=str(description), input_schema=dict(schema))

    @classmethod
    def from_mapping(cls, items: Any, label: str = "tools") -> tuple["ToolDefinition", ...]:
        """Validate a sequence of definitions and reject duplicate names."""
        if isinstance(items, (str, bytes)) or not isinstance(items, (list, tuple)):
            raise ToolDefinitionError(f"{label}: expected an array of tool definitions, "
                                      f"got {type(items).__name__}")
        if not items:
            raise ToolDefinitionError(f"{label}: must not be empty")
        definitions: list[ToolDefinition] = []
        seen: set[str] = set()
        for index, item in enumerate(items):
            definition = cls.from_dict(item, f"{label}[{index}]")
            if definition.name in seen:
                raise ToolDefinitionError(f"{label}[{index}]: duplicate tool name {definition.name!r}")
            seen.add(definition.name)
            definitions.append(definition)
        return tuple(definitions)


@dataclass(frozen=True)
class ToolCall:
    """One assistant-requested tool call, parsed and validated. Nothing is executed."""

    call_id: str
    name: str
    arguments: dict

    def assistant_message(self) -> dict:
        """The assistant turn that requested the call, for the next request's message history."""
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": self.call_id,
                "type": "function",
                "function": {"name": self.name, "arguments": json.dumps(self.arguments, sort_keys=True)},
            }],
        }


@dataclass(frozen=True)
class ToolResultMessage:
    """One tool outcome, in the shape the provider expects back. Transport only."""

    call_id: str
    name: str
    content: str
    ok: bool = True

    def to_message(self) -> dict:
        return {"role": "tool", "tool_call_id": self.call_id, "name": self.name, "content": self.content}

    @classmethod
    def from_dict(cls, data: Any, label: str = "ToolResultMessage") -> "ToolResultMessage":
        errors: list[str] = []
        if not isinstance(data, dict):
            raise ToolCallError(f"{label}: expected object, got {type(data).__name__}")
        unknown = sorted(set(data) - {"call_id", "name", "content", "ok"})
        for key in unknown:
            errors.append(f"{label}.{key}: not allowed")
        call_id = data.get("call_id")
        if not isinstance(call_id, str) or not call_id.strip():
            errors.append(f"{label}.call_id: expected a non-empty string")
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}.name: expected a non-empty string")
        content = data.get("content")
        if not isinstance(content, str) or not content.strip():
            errors.append(f"{label}.content: expected a non-empty string")
        ok = data.get("ok", True)
        if not isinstance(ok, bool):
            errors.append(f"{label}.ok: expected boolean")
        if errors:
            raise ToolCallError(f"{label}: " + "; ".join(errors))
        # P3-B: this is the limit that matters most. A clipped tool result is engineering evidence
        # that looks complete - a truncated log reads as a whole log. Refuse instead, and say so,
        # so the host can narrow the query.
        if len(content) > MAX_TOOL_RESULT_CHARS:
            raise ToolResourceLimit(
                f"{label}.content: {len(content)} chars, over the {MAX_TOOL_RESULT_CHARS} limit. "
                "Tool results are NOT truncated, because a clipped result is indistinguishable "
                "from a complete one. Narrow the query or page the output."
            )
        return cls(call_id=str(call_id), name=str(name), content=str(content), ok=bool(ok))


def parse_tool_calls(message: dict, known: set[str], label: str = "tool_calls") -> tuple[ToolCall, ...]:
    """Parse an assistant message's `tool_calls`, preserving provider order.

    A malformed call is a typed failure, never an empty `arguments` object: silently substituting
    `{}` would run a tool with no parameters and look like a successful delegation.
    """
    raw = message.get("tool_calls")
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, list):
        raise ToolCallError(f"{label}: expected array, got {type(raw).__name__}")
    # P3-B: count first, so an oversized turn is refused before any of it is parsed or executed.
    if len(raw) > MAX_TOOL_CALLS_PER_TURN:
        raise ToolResourceLimit(
            f"{label}: {len(raw)} tool calls in one turn, over the "
            f"{MAX_TOOL_CALLS_PER_TURN} limit. None were executed. Delegate in smaller turns; the "
            "turn is not truncated."
        )
    calls: list[ToolCall] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        where = f"{label}[{index}]"
        if not isinstance(item, dict):
            raise ToolCallError(f"{where}: expected object, got {type(item).__name__}")
        call_id = item.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            raise ToolCallError(f"{where}.id: expected a non-empty string")
        if call_id in seen:
            raise ToolCallError(f"{where}.id: duplicate tool_call id {call_id!r}")
        seen.add(call_id)
        function = item.get("function")
        if not isinstance(function, dict):
            raise ToolCallError(f"{where}.function: expected object, got {type(function).__name__}")
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ToolCallError(f"{where}.function.name: expected a non-empty string")
        if name not in known:
            raise ToolCallError(f"{where}.function.name: {name!r} was not offered in this request")
        raw_arguments = function.get("arguments")
        if raw_arguments is None:
            raw_arguments = "{}"
        if isinstance(raw_arguments, dict):
            arguments = dict(raw_arguments)
        elif isinstance(raw_arguments, str):
            try:
                parsed = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise ToolCallError(f"{where}.function.arguments: not valid JSON: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ToolCallError(f"{where}.function.arguments: expected a JSON object, "
                                    f"got {type(parsed).__name__}")
            arguments = parsed
        else:
            raise ToolCallError(f"{where}.function.arguments: expected JSON string, "
                                f"got {type(raw_arguments).__name__}")
        # P3-B: bound the serialised arguments. Refused, not clipped: silently dropping keys would
        # hand the tool a different request than the model made.
        serialised = json.dumps(arguments, sort_keys=True)
        if len(serialised) > MAX_TOOL_ARGUMENT_CHARS:
            raise ToolResourceLimit(
                f"{where}.function.arguments: {len(serialised)} chars, over the "
                f"{MAX_TOOL_ARGUMENT_CHARS} limit. The arguments are not truncated; send smaller "
                "inputs or a file path instead of inlined content."
            )
        calls.append(ToolCall(call_id=call_id, name=name, arguments=arguments))
    return tuple(calls)


def tool_choice_payload(tool_choice: str, tools: tuple["ToolDefinition", ...]) -> Any:
    """Serialise tool_choice. "auto"/"none" pass through; any other value pins a tool by name.

    Only a tool that was actually offered in this request may be pinned, so a typo becomes a typed
    error instead of a request the provider silently ignores.
    """
    if not isinstance(tool_choice, str) or not tool_choice.strip():
        raise ToolDefinitionError(f"tool_choice: expected a non-empty string, got {tool_choice!r}")
    if tool_choice in TOOL_CHOICE_MODES:
        return tool_choice
    if not tools:
        raise ToolDefinitionError(f"tool_choice: {tool_choice!r} requires at least one tool")
    if tool_choice not in {t.name for t in tools}:
        raise ToolDefinitionError(
            f"tool_choice: {tool_choice!r} is neither {list(TOOL_CHOICE_MODES)} nor the name of an "
            f"offered tool {[t.name for t in tools]}")
    return {"type": "function", "function": {"name": tool_choice}}
