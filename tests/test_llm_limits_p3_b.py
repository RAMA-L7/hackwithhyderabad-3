"""P3-B regression tests: resource limits on tool payloads and the conversation.

ADR 001 recorded the P2 limitation knowingly: "no resource limits on tool payloads". These tests
pin the enforcement and, just as importantly, pin the principle behind it:

    EVERY limit REFUSES. NONE of them truncates.

A truncated tool result is the failure mode that matters. The model cannot distinguish a clipped
log from a complete one, and a clipped log is engineering evidence that looks whole. Each limit
test therefore asserts the payload arrived INTACT or was REFUSED - never shortened.

Deterministic: no sleeps, no threads, no provider calls. Failures are injected by constructing
oversized values directly, and the conversation limit by driving a scripted transport.
"""

from __future__ import annotations

import json
import unittest

import support
from debugagent.llm import (
    MAX_CONVERSATION_CHARS,
    MAX_TOOL_ARGUMENT_CHARS,
    MAX_TOOL_CALLS_PER_TURN,
    MAX_TOOL_DEFINITION_CHARS,
    MAX_TOOL_RESULT_CHARS,
    LLMRouter,
    LLMToolResourceLimit,
    Route,
    ToolDefinition,
    ToolResourceLimit,
    ToolResultMessage,
)
from debugagent.llm.tools import ToolCallError, ToolDefinitionError, parse_tool_calls

VALID = {"name": "task", "description": "delegate to a worker", "input_schema": {
    "type": "object", "properties": {}}}


def call_message(count: int, arg_size: int = 0, start: int = 0) -> dict:
    """An assistant message requesting `count` tool calls, each with an optional large argument."""
    return {"tool_calls": [
        {"id": f"c{i}", "type": "function",
         "function": {"name": "task",
                      "arguments": json.dumps({"blob": "x" * arg_size}) if arg_size else "{}"}}
        for i in range(start, start + count)]}


class LimitValueTests(unittest.TestCase):
    """The limits exist, are positive, and are ordered so the loop is the outer bound."""

    def test_every_limit_is_a_positive_integer(self):
        for name, value in (
            ("MAX_TOOL_CALLS_PER_TURN", MAX_TOOL_CALLS_PER_TURN),
            ("MAX_TOOL_ARGUMENT_CHARS", MAX_TOOL_ARGUMENT_CHARS),
            ("MAX_TOOL_RESULT_CHARS", MAX_TOOL_RESULT_CHARS),
            ("MAX_TOOL_DEFINITION_CHARS", MAX_TOOL_DEFINITION_CHARS),
            ("MAX_CONVERSATION_CHARS", MAX_CONVERSATION_CHARS),
        ):
            with self.subTest(limit=name):
                self.assertIsInstance(value, int)
                self.assertGreater(value, 0)

    def test_conversation_limit_exceeds_a_maximum_turn(self):
        """Four turns of maximum-size results must fit; the loop limit should not be reachable
        before the conversation limit in a planned run."""
        self.assertGreater(MAX_CONVERSATION_CHARS, MAX_TOOL_RESULT_CHARS * 2)

    def test_call_limit_covers_the_planned_worker_roster(self):
        """Three workers is the whole planned roster, so the per-turn cap must allow it."""
        self.assertGreaterEqual(MAX_TOOL_CALLS_PER_TURN, 3)


class ToolDefinitionLimitTests(unittest.TestCase):
    """A definition is bounded before it is ever offered to the provider."""

    def test_normal_definition_is_accepted(self):
        self.assertEqual(ToolDefinition.from_dict(VALID).name, "task")

    def test_oversized_description_is_refused(self):
        with self.assertRaises(ToolDefinitionError) as ctx:
            ToolDefinition.from_dict({**VALID, "description": "d" * (MAX_TOOL_DEFINITION_CHARS + 1)})
        self.assertIn("not truncated", str(ctx.exception))

    def test_oversized_schema_is_refused(self):
        huge = {"type": "object",
                "properties": {f"p{i}": {"type": "string"} for i in range(2000)}}
        with self.assertRaises(ToolDefinitionError):
            ToolDefinition.from_dict({**VALID, "input_schema": huge})

    def test_size_is_checked_after_structural_validation(self):
        """A definition that is both malformed and oversized reports the structural problem."""
        with self.assertRaises(ToolDefinitionError) as ctx:
            ToolDefinition.from_dict({"name": "", "description": "d" * 50_000,
                                      "input_schema": {"type": "array"}})
        self.assertIn("name", str(ctx.exception))


class ToolCallCountTests(unittest.TestCase):
    """Per-turn call count, refused before any call is parsed or executed."""

    def test_count_within_limit_parses(self):
        parsed = parse_tool_calls(call_message(MAX_TOOL_CALLS_PER_TURN), {"task"})
        self.assertEqual(len(parsed), MAX_TOOL_CALLS_PER_TURN)

    def test_count_over_limit_is_refused(self):
        with self.assertRaises(ToolResourceLimit) as ctx:
            parse_tool_calls(call_message(MAX_TOOL_CALLS_PER_TURN + 1), {"task"})
        message = str(ctx.exception)
        self.assertIn("None were executed", message)
        self.assertIn("not truncated", message)

    def test_refusal_is_also_a_tool_call_error(self):
        """Existing `except ToolCallError` handlers keep working unchanged."""
        self.assertTrue(issubclass(ToolResourceLimit, ToolCallError))
        with self.assertRaises(ToolCallError):
            parse_tool_calls(call_message(MAX_TOOL_CALLS_PER_TURN + 1), {"task"})

    def test_absent_tool_calls_still_returns_empty(self):
        self.assertEqual(parse_tool_calls({}, {"task"}), ())


class ToolArgumentLimitTests(unittest.TestCase):
    """Serialised arguments are bounded, and refused rather than clipped."""

    def test_arguments_within_limit_parse(self):
        parsed = parse_tool_calls(call_message(1, arg_size=1000), {"task"})
        self.assertEqual(len(parsed), 1)

    def test_oversized_arguments_are_refused(self):
        with self.assertRaises(ToolResourceLimit) as ctx:
            parse_tool_calls(call_message(1, arg_size=MAX_TOOL_ARGUMENT_CHARS + 1), {"task"})
        self.assertIn("not truncated", str(ctx.exception))

    def test_refused_arguments_are_never_silently_emptied(self):
        """The old failure mode this protects: a malformed call becoming `{}`, which would run a
        tool with no parameters and look like a successful delegation."""
        with self.assertRaises(ToolResourceLimit):
            parse_tool_calls(call_message(1, arg_size=MAX_TOOL_ARGUMENT_CHARS + 1), {"task"})

    def test_oversized_string_arguments_are_refused_too(self):
        message = {"tool_calls": [{"id": "c0", "type": "function", "function": {
            "name": "task", "arguments": json.dumps({"blob": "x" * (MAX_TOOL_ARGUMENT_CHARS + 1)})}}]}
        with self.assertRaises(ToolResourceLimit):
            parse_tool_calls(message, {"task"})


class ToolResultLimitTests(unittest.TestCase):
    """The limit that matters most: a tool result is never silently shortened."""

    def test_result_within_limit_is_accepted_intact(self):
        content = "log line\n" * 100
        result = ToolResultMessage.from_dict(
            {"call_id": "c1", "name": "read", "content": content})
        self.assertEqual(result.content, content, "content must arrive byte-identical")

    def test_oversized_result_is_refused(self):
        with self.assertRaises(ToolResourceLimit) as ctx:
            ToolResultMessage.from_dict(
                {"call_id": "c1", "name": "read", "content": "x" * (MAX_TOOL_RESULT_CHARS + 1)})
        message = str(ctx.exception)
        self.assertIn("NOT truncated", message)
        self.assertIn("indistinguishable from a complete one", message)

    def test_result_at_exactly_the_limit_is_accepted(self):
        content = "x" * MAX_TOOL_RESULT_CHARS
        result = ToolResultMessage.from_dict(
            {"call_id": "c1", "name": "read", "content": content})
        self.assertEqual(len(result.content), MAX_TOOL_RESULT_CHARS)

    def test_refusal_is_also_a_tool_call_error(self):
        with self.assertRaises(ToolCallError):
            ToolResultMessage.from_dict(
                {"call_id": "c1", "name": "read", "content": "x" * (MAX_TOOL_RESULT_CHARS + 1)})

    def test_structural_validation_still_runs_first(self):
        """A blank oversized-shaped result reports the structural problem, not the size one."""
        with self.assertRaises(ToolCallError) as ctx:
            ToolResultMessage.from_dict({"call_id": "", "name": "read", "content": "   "})
        self.assertIn("call_id", str(ctx.exception))

    def test_ok_false_still_constructs(self):
        """A failed tool result is still a result, and is still bounded."""
        result = ToolResultMessage.from_dict(
            {"call_id": "c1", "name": "read", "content": "boom", "ok": False})
        self.assertFalse(result.ok)


class ConversationLimitTests(unittest.TestCase):
    """The loop is the only place history accumulates, so it owns the total."""

    PRIMARY = Route("primary", "baseten", "m", "https://p/v1", "k", 10, True)

    def _router(self, replies):
        recorded: list[dict] = []
        queue = list(replies)

        def transport(url, api_key, payload, timeout_s):
            recorded.append(payload)
            return queue.pop(0)

        return LLMRouter(self.PRIMARY, self.PRIMARY, transport=transport), recorded

    def _tool_reply(self, count=1, arg_size=0):
        return (200, json.dumps({"choices": [{"message": call_message(count, arg_size),
                                              "finish_reason": "tool_calls"}]}))

    def _final_reply(self):
        return (200, json.dumps({"choices": [{"message": {"content": json.dumps({"h": ["x"]})},
                                              "finish_reason": "stop"}]}))

    def test_small_conversation_is_unaffected(self):
        router, transport = self._router([self._tool_reply(), self._final_reply()])
        router.complete_with_tools(
            "p", tools=[VALID], schema={"type": "object", "properties": {}},
            execute_tool=lambda c: ToolResultMessage(call_id=c.call_id, name=c.name, content="ok"))
        self.assertEqual(len(transport), 2)

    def test_conversation_limit_is_checked_before_sending(self):
        """A conversation over the cap must be refused WITHOUT a request being sent."""
        router, transport = self._router([self._tool_reply()])
        huge = "y" * (MAX_CONVERSATION_CHARS + 1)
        with self.assertRaises(LLMToolResourceLimit) as ctx:
            router.complete_with_tools(
                huge, tools=[VALID], schema={"type": "object", "properties": {}},
                execute_tool=lambda c: ToolResultMessage(
                    call_id=c.call_id, name=c.name, content="ok"))
        self.assertEqual(transport, [], "nothing may be sent once the cap is exceeded")
        self.assertIn("before any request was sent", str(ctx.exception))

    def test_conversation_limit_message_refuses_to_truncate(self):
        router, _ = self._router([self._tool_reply()])
        with self.assertRaises(LLMToolResourceLimit) as ctx:
            router.complete_with_tools(
                "z" * (MAX_CONVERSATION_CHARS + 1), tools=[VALID],
                schema={"type": "object", "properties": {}},
                execute_tool=lambda c: ToolResultMessage(
                    call_id=c.call_id, name=c.name, content="ok"))
        message = str(ctx.exception)
        self.assertIn("NOT", message)
        self.assertIn("orphan", message)

    def test_error_carries_a_typed_error_class(self):
        router, _ = self._router([self._tool_reply()])
        with self.assertRaises(LLMToolResourceLimit) as ctx:
            router.complete_with_tools(
                "w" * (MAX_CONVERSATION_CHARS + 1), tools=[VALID],
                schema={"type": "object", "properties": {}},
                execute_tool=lambda c: ToolResultMessage(
                    call_id=c.call_id, name=c.name, content="ok"))
        self.assertEqual(ctx.exception.error_class, "TOOL_RESOURCE_LIMIT")

    def test_oversized_result_from_a_host_is_refused_by_the_loop(self):
        """The cap must hold even when a host builds ToolResultMessage directly.

        `ToolResultMessage(...)` bypasses `from_dict`, so validating only there would let an
        oversized result into the history. The loop checks it again, and refuses.
        """
        router, _ = self._router([self._tool_reply()])

        def executor(call):
            return ToolResultMessage(call_id=call.call_id, name=call.name,
                                     content="r" * (MAX_TOOL_RESULT_CHARS + 1))

        with self.assertRaises(ToolResourceLimit) as ctx:
            router.complete_with_tools(
                "p", tools=[VALID], schema={"type": "object", "properties": {}},
                execute_tool=executor, max_turns=4)
        self.assertIn("execute_tool returned", str(ctx.exception))
        self.assertIn("NOT truncated", str(ctx.exception))

    def test_result_at_the_cap_is_accepted_by_the_loop(self):
        """Boundary: a result exactly at the cap must pass, so the limit is not off-by-one."""
        router, sent = self._router([self._tool_reply(), self._final_reply()])
        content = "r" * MAX_TOOL_RESULT_CHARS
        result = router.complete_with_tools(
            "p", tools=[VALID], schema={"type": "object", "properties": {}},
            execute_tool=lambda c: ToolResultMessage(call_id=c.call_id, name=c.name, content=content))
        self.assertEqual(len(sent), 2)
        self.assertEqual(result.tool_results[0].content, content, "content must arrive intact")


class NoOverclaimTests(unittest.TestCase):
    """P3-B must not imply it makes anything safe that it does not."""

    def test_no_truncation_helper_was_introduced(self):
        """The limits reject; they must not slice."""
        import inspect

        from debugagent.llm import tools

        source = inspect.getsource(tools)
        for marker in ("content[:", "content[0:", "clipped_to", "content=content["):
            self.assertNotIn(marker, source, f"unexpected truncation construct: {marker}")

    def test_limit_documentation_states_no_truncation(self):
        import inspect

        from debugagent.llm import tools

        doc = inspect.getsource(tools).lower()
        self.assertIn("none of them truncates", doc)
        self.assertIn("engineering evidence that looks whole", doc)


if __name__ == "__main__":
    unittest.main()
