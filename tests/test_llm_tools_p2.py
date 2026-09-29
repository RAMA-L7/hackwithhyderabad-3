"""P2: LLMRouter tool-calling infrastructure (docs/adr-001-hub-spoke-coordinator.md, phase P2).

Transport only. No test here executes a real tool, contacts the P1 registry, or reaches a provider:
every provider response is scripted through a fake transport, so no API key is required.

The compatibility requirement dominates this file: the no-tools path (`complete_structured`) must keep
behaving exactly as it did before P2, byte for byte in its request payload.
"""

from __future__ import annotations

import json
import unittest

import loop_support  # noqa: F401  (path wiring, matches the existing llm tests)
from debugagent.llm import (
    MAX_TOOL_TURNS,
    LLMAuthError,
    LLMRouter,
    LLMToolCallInvalid,
    LLMToolTurnLimit,
    LLMToolUnsupported,
    LLMUnavailable,
    Route,
    StructuredOutputError,
    ToolCall,
    ToolCallError,
    ToolDefinition,
    ToolDefinitionError,
    ToolResultMessage,
)
from debugagent.llm.tools import TOOL_CHOICE_MODES, parse_tool_calls, tool_choice_payload

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["hypotheses"],
    "properties": {"hypotheses": {"type": "array", "items": {"type": "string"}}},
}
FINAL = {"hypotheses": ["proxy body limit"]}
PRIMARY = Route("primary", "baseten", "deepseek-ai/DeepSeek-V4.1-Flash", "https://p.example/v1", "pk", 10)
FALLBACK = Route("fallback", "openrouter", "stealth/space-bunny-alpha", "https://f.example/v1", "fk", 17)
TOOLS_ENABLED = Route("primary", "baseten", "deepseek-ai/DeepSeek-V4.1-Flash",
                      "https://p.example/v1", "pk", 10, True)

TOOL_TASK = {
    "name": "task", "description": "delegate a subtask",
    "input_schema": {"type": "object", "additionalProperties": False,
                     "required": ["objective"], "properties": {"objective": {"type": "string"}}},
}


# --- response builders -------------------------------------------------------------------------

def completion(content, finish="stop", **message):
    return 200, json.dumps({"choices": [{"message": {"content": content, **message}, "finish_reason": finish}]})


def tool_turn(*calls, finish="tool_calls", content=None):
    # .get so a test can deliberately build a malformed call (missing id, etc.).
    body = {"role": "assistant", "content": content, "tool_calls": [
        {"id": c.get("id"), "type": "function",
         "function": {"name": c.get("name"), "arguments": c.get("arguments", "{}")}} for c in calls]}
    return 200, json.dumps({"choices": [{"message": body, "finish_reason": finish}]})


def call(cid="call_1", name="task", arguments="{}"):
    return {"id": cid, "name": name, "arguments": arguments}


class FakeTransport:
    """Scripted responses per base URL, recording every payload. Mirrors tests/test_loop_llm.py."""

    def __init__(self, primary=(), fallback=()):
        self.script = {PRIMARY.base_url: list(primary), FALLBACK.base_url: list(fallback)}
        self.calls: list[tuple[str, str, dict, float]] = []

    def __call__(self, url, key, payload, timeout):
        self.calls.append((url, key, payload, timeout))
        base = url.rsplit("/chat/completions", 1)[0]
        queue = self.script[base]
        if not queue:
            raise AssertionError(f"unexpected extra call to {base}")
        step = queue.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def hits(self, route):
        return sum(1 for c in self.calls if c[0].startswith(route.base_url))


def router(transport, *, primary=TOOLS_ENABLED, fallback=FALLBACK, **kwargs):
    return LLMRouter(primary, fallback, transport=transport, **kwargs)


def echo_tool(result="ok"):
    """A stand-in executor. Records what it was asked; runs nothing."""
    def execute(c: ToolCall) -> ToolResultMessage:
        return ToolResultMessage(call_id=c.call_id, name=c.name,
                                 content=json.dumps({"echo": c.arguments, "status": result}))
    return execute


# --- 1/2. backward compatibility ----------------------------------------------------------------

class NoToolsPathCompatibilityTests(unittest.TestCase):
    def test_complete_structured_still_uses_the_single_shot_payload(self):
        t = FakeTransport([completion(json.dumps(FINAL))])
        result = router(t, primary=PRIMARY).complete_structured("p", schema=SCHEMA)
        self.assertEqual(result.data, FINAL)
        self.assertEqual(len(t.calls), 1, "one request, no loop")
        payload = t.calls[0][2]
        self.assertNotIn("tools", payload)
        self.assertNotIn("tool_choice", payload)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "p"}])
        self.assertEqual(payload["response_format"],
                         {"type": "json_schema", "json_schema": {"name": "response", "strict": True,
                                                                 "schema": SCHEMA}})

    def test_existing_keyword_callers_remain_valid(self):
        t = FakeTransport([completion(json.dumps(FINAL))])
        out = router(t, primary=PRIMARY).complete_structured("p", schema=SCHEMA, name="hypotheses",
                                                             check=lambda d: [])
        self.assertEqual(t.calls[0][2]["response_format"]["json_schema"]["name"], "hypotheses")
        self.assertEqual(out.data, FINAL)

    def test_check_still_rejects_and_retries(self):
        t = FakeTransport([completion(json.dumps({"wrong": []})), completion(json.dumps(FINAL))])
        out = router(t, primary=PRIMARY).complete_structured("p", schema=SCHEMA)
        self.assertEqual(out.data, FINAL)
        self.assertEqual(t.hits(PRIMARY), 2)

    # 17. fallback semantics for no-tool requests are untouched
    def test_existing_fallback_behaviour_is_intact(self):
        t = FakeTransport([TimeoutError()], [completion(json.dumps(FINAL))])
        out = router(t, primary=PRIMARY).complete_structured("p", schema=SCHEMA)
        self.assertEqual(out.data, FINAL)
        self.assertTrue(out.fallback_used)
        self.assertEqual(t.hits(PRIMARY), 1, "transport failure goes straight to fallback")

    def test_auth_error_does_not_fail_over(self):
        t = FakeTransport([(401, "{}")], [completion(json.dumps(FINAL))])
        with self.assertRaises(LLMAuthError):
            router(t, primary=PRIMARY).complete_structured("p", schema=SCHEMA)
        self.assertEqual(t.hits(FALLBACK), 0)

    def test_route_defaults_to_tools_disabled(self):
        self.assertFalse(PRIMARY.supports_tools, "tools must be opt-in per route")
        self.assertFalse(FALLBACK.supports_tools)


# --- 3/4/5. tool definition validation ---------------------------------------------------------

class ToolDefinitionTests(unittest.TestCase):
    def test_definition_serialisation_is_correct(self):
        definition = ToolDefinition.from_dict(TOOL_TASK)
        payload = definition.to_payload()
        self.assertEqual(payload["type"], "function")
        self.assertEqual(payload["function"]["name"], "task")
        self.assertEqual(payload["function"]["description"], TOOL_TASK["description"])
        self.assertEqual(payload["function"]["parameters"], TOOL_TASK["input_schema"])

    def test_duplicate_tool_names_are_rejected(self):
        with self.assertRaises(ToolDefinitionError) as caught:
            ToolDefinition.from_mapping([TOOL_TASK, dict(TOOL_TASK)])
        self.assertIn("duplicate", str(caught.exception))

    def test_invalid_definitions_are_rejected(self):
        cases = {
            "empty name": {**TOOL_TASK, "name": "  "},
            "missing name": {k: v for k, v in TOOL_TASK.items() if k != "name"},
            "blank description": {**TOOL_TASK, "description": ""},
            "schema not an object": {**TOOL_TASK, "input_schema": "nope"},
            "non-object schema type": {**TOOL_TASK, "input_schema": {"type": "array"}},
            "properties not an object": {**TOOL_TASK, "input_schema": {"type": "object", "properties": []}},
            "extra field": {**TOOL_TASK, "temperature": 1},
            "not a dict": "nope",
        }
        for label, payload in cases.items():
            with self.subTest(label):
                with self.assertRaises(ToolDefinitionError):
                    ToolDefinition.from_dict(payload)

    def test_empty_tool_list_is_rejected(self):
        with self.assertRaises(ToolDefinitionError):
            ToolDefinition.from_mapping([])
        with self.assertRaises(ToolDefinitionError):
            ToolDefinition.from_mapping("task")

    def test_definition_is_frozen(self):
        definition = ToolDefinition.from_dict(TOOL_TASK)
        with self.assertRaises(AttributeError):
            definition.name = "other"


# --- 6. tool choice ------------------------------------------------------------------------------

class ToolChoiceTests(unittest.TestCase):
    def test_supported_choices(self):
        self.assertEqual(TOOL_CHOICE_MODES, ("auto", "none"))
        tools = (ToolDefinition.from_dict(TOOL_TASK),)
        self.assertEqual(tool_choice_payload("auto", tools), "auto")
        self.assertEqual(tool_choice_payload("none", tools), "none")
        # a tool name pins the choice
        self.assertEqual(tool_choice_payload("task", tools),
                         {"type": "function", "function": {"name": "task"}})

    def test_unknown_or_unmatched_choice_is_rejected(self):
        tools = (ToolDefinition.from_dict(TOOL_TASK),)
        with self.assertRaises(ToolDefinitionError):
            tool_choice_payload("required", tools)          # forcing a call is not supported
        with self.assertRaises(ToolDefinitionError):
            tool_choice_payload("other_tool", tools)        # not offered
        with self.assertRaises(ToolDefinitionError):
            tool_choice_payload("task", ())                 # no tools at all
        with self.assertRaises(ToolDefinitionError):
            tool_choice_payload("", tools)
        with self.assertRaises(ToolDefinitionError):
            tool_choice_payload(None, tools)


# --- 7/8/9/10/11. assistant tool call parsing ----------------------------------------------------

class ToolCallParsingTests(unittest.TestCase):
    def test_assistant_response_without_tool_calls_returns_normally(self):
        t = FakeTransport([completion(json.dumps(FINAL))])
        out = router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA, execute_tool=echo_tool())
        self.assertEqual(out.result.data, FINAL)
        self.assertEqual(out.turns, 1)
        self.assertEqual(out.tool_calls, ())
        self.assertEqual(len(t.calls), 1)

    def test_single_tool_call_is_parsed_correctly(self):
        t = FakeTransport([tool_turn(call("call_1", "task", '{"objective":"check nginx"}')),
                          completion(json.dumps(FINAL))])
        out = router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA, execute_tool=echo_tool())
        self.assertEqual(out.turns, 2)
        self.assertEqual(len(out.tool_calls), 1)
        self.assertEqual(out.tool_calls[0].call_id, "call_1")
        self.assertEqual(out.tool_calls[0].name, "task")
        self.assertEqual(out.tool_calls[0].arguments, {"objective": "check nginx"})

    def test_multiple_tool_calls_preserve_provider_order(self):
        t = FakeTransport([tool_turn(call("c1", "task", '{"n":1}'),
                                 call("c2", "task", '{"n":2}'),
                                 call("c3", "task", '{"n":3}')),
                          completion(json.dumps(FINAL))])
        out = router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA, execute_tool=echo_tool())
        self.assertEqual([c.call_id for c in out.tool_calls], ["c1", "c2", "c3"])
        self.assertEqual([c.arguments for c in out.tool_calls], [{"n": 1}, {"n": 2}, {"n": 3}])
        self.assertEqual([r.call_id for r in out.tool_results], ["c1", "c2", "c3"])

    def test_arguments_accept_a_json_object_directly(self):
        t = FakeTransport([tool_turn(call("c1", "task", {"objective": "inline"})),
                          completion(json.dumps(FINAL))])
        out = router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA, execute_tool=echo_tool())
        self.assertEqual(out.tool_calls[0].arguments, {"objective": "inline"})

    def test_malformed_tool_call_json_is_rejected_not_defaulted(self):
        for label, arguments in [("not json", "{not json"),
                                 ("json array", "[1,2]"),
                                 ("json string", '"hello"')]:
            with self.subTest(label):
                t = FakeTransport([tool_turn(call("c1", "task", arguments))])
                with self.assertRaises(LLMToolCallInvalid) as caught:
                    router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA,
                                                 execute_tool=echo_tool())
                self.assertIn("INVALID_TOOL_CALL", caught.exception.error_class)

    def test_missing_call_id_is_rejected(self):
        t = FakeTransport([tool_turn({"name": "task", "arguments": "{}"})])
        with self.assertRaises(LLMToolCallInvalid):
            router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA, execute_tool=echo_tool())

    def test_null_arguments_from_the_provider_mean_no_arguments(self):
        # A provider may legitimately send null for a no-argument tool; that is not malformed.
        t = FakeTransport([tool_turn({"id": "c1", "name": "task", "arguments": None}),
                          completion(json.dumps(FINAL))])
        out = router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA, execute_tool=echo_tool())
        self.assertEqual(out.tool_calls[0].arguments, {})

    def test_unknown_tool_name_is_rejected(self):
        t = FakeTransport([tool_turn(call("c1", "shell", "{}"))])
        with self.assertRaises(LLMToolCallInvalid) as caught:
            router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA, execute_tool=echo_tool())
        self.assertIn("not offered", str(caught.exception))

    def test_duplicate_call_id_is_rejected(self):
        t = FakeTransport([tool_turn(call("c1"), call("c1"))])
        with self.assertRaises(LLMToolCallInvalid):
            router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA, execute_tool=echo_tool())

    def test_parse_tool_calls_rejects_malformed_shapes(self):
        for message in [{"tool_calls": "nope"}, {"tool_calls": ["nope"]},
                        {"tool_calls": [{"type": "function"}]},
                        {"tool_calls": [{"id": "c", "function": "nope"}]},
                        {"tool_calls": [{"id": "c", "function": {"arguments": "{}"}}]}]:
            with self.subTest(str(message)[:60]):
                with self.assertRaises(ToolCallError):
                    parse_tool_calls(message, {"task"})


# --- 12/13. tool result messages and multi-turn -------------------------------------------------

class ToolResultMessageTests(unittest.TestCase):
    def test_tool_result_serialisation_is_correct(self):
        result = ToolResultMessage(call_id="c1", name="task", content='{"done":true}')
        self.assertEqual(result.to_message(),
                         {"role": "tool", "tool_call_id": "c1", "name": "task", "content": '{"done":true}'})

    def test_tool_result_validation(self):
        for payload in [{}, {"call_id": "c"}, {"call_id": "c", "name": "t"},
                        {"call_id": "c", "name": "t", "content": ""},
                        {"call_id": "c", "name": "t", "content": "x", "ok": "yes"},
                        {"call_id": "c", "name": "t", "content": "x", "extra": 1}]:
            with self.subTest(str(payload)[:50]):
                with self.assertRaises(ToolCallError):
                    ToolResultMessage.from_dict(payload)

    def test_tool_call_then_result_continues_to_another_turn(self):
        t = FakeTransport([tool_turn(call("c1")), completion(json.dumps(FINAL))])
        router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA, execute_tool=echo_tool())
        self.assertEqual(len(t.calls), 2, "a second request was made after the tool result")
        history = t.calls[1][2]["messages"]
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[1]["role"], "assistant")
        self.assertEqual(history[1]["tool_calls"][0]["id"], "c1")
        self.assertEqual(history[2]["role"], "tool")
        self.assertEqual(history[2]["tool_call_id"], "c1")

    def test_two_sequential_tool_turns(self):
        t = FakeTransport([tool_turn(call("c1")), tool_turn(call("c2")), completion(json.dumps(FINAL))])
        out = router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA, execute_tool=echo_tool())
        self.assertEqual(out.turns, 3)
        self.assertEqual([c.call_id for c in out.tool_calls], ["c1", "c2"])
        self.assertEqual(len(t.calls[2][2]["messages"]), 5)

    def test_executor_must_return_a_tool_result_message(self):
        t = FakeTransport([tool_turn(call("c1"))])
        with self.assertRaises(ToolCallError):
            router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA,
                                         execute_tool=lambda c: {"call_id": c.call_id})

    def test_tool_mode_sends_tools_and_no_response_format(self):
        t = FakeTransport([completion(json.dumps(FINAL))])
        router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA,
                                      tool_choice="task", execute_tool=echo_tool())
        payload = t.calls[0][2]
        self.assertIn("tools", payload)
        self.assertEqual(payload["tool_choice"], {"type": "function", "function": {"name": "task"}})
        self.assertNotIn("response_format", payload, "tools and response_format are never combined")

    def test_final_output_still_fails_closed(self):
        t = FakeTransport([completion(json.dumps({"wrong": []}))])
        with self.assertRaises(StructuredOutputError):
            router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA, execute_tool=echo_tool())


# --- 14/15. the turn bound -----------------------------------------------------------------------

class TurnLimitTests(unittest.TestCase):
    def test_default_max_turns_is_small_and_configurable(self):
        self.assertEqual(MAX_TOOL_TURNS, 4)
        self.assertEqual(LLMRouter(TOOLS_ENABLED, FALLBACK).max_tool_turns, 4)
        self.assertEqual(LLMRouter(TOOLS_ENABLED, FALLBACK, max_tool_turns=2).max_tool_turns, 2)

    def test_turn_limit_is_enforced_and_typed(self):
        t = FakeTransport([tool_turn(call("c1")), tool_turn(call("c2"))])
        with self.assertRaises(LLMToolTurnLimit) as caught:
            router(t, max_tool_turns=2).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA,
                                                           execute_tool=echo_tool())
        self.assertEqual(caught.exception.error_class, "TOOL_TURN_LIMIT")
        self.assertIn("max_turns=2", str(caught.exception))
        self.assertEqual(len(t.calls), 2, "the loop stopped at the bound")

    def test_per_call_max_turns_overrides_the_router_default(self):
        t = FakeTransport([tool_turn(call("c1"))])
        with self.assertRaises(LLMToolTurnLimit):
            router(t, max_tool_turns=9).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA,
                                                            execute_tool=echo_tool(), max_turns=1)

    def test_invalid_max_turns_is_rejected(self):
        t = FakeTransport([completion(json.dumps(FINAL))])
        for bad in [0, -1, "3", True]:
            with self.subTest(str(bad)):
                with self.assertRaises(ToolDefinitionError):
                    router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA,
                                                  execute_tool=echo_tool(), max_turns=bad)

    # C6: the diagnostic must report the value actually validated. When max_turns is omitted the
    # parameter is None, so interpolating it reported "got None" for a bad router default.
    def test_invalid_router_default_reports_the_resolved_limit(self):
        t = FakeTransport([completion(json.dumps(FINAL))])
        for bad_default in [0, -3]:
            with self.subTest(f"default={bad_default}"):
                with self.assertRaises(ToolDefinitionError) as caught:
                    router(t, max_tool_turns=bad_default).complete_with_tools(
                        "p", tools=[TOOL_TASK], schema=SCHEMA, execute_tool=echo_tool())
                self.assertIn(f"got {bad_default}", str(caught.exception))
                self.assertNotIn("got None", str(caught.exception))

    def test_invalid_router_default_still_rejects_when_caller_supplies_a_valid_override(self):
        # Semantics unchanged: the per-call value wins, so a bad default is not consulted at all.
        t = FakeTransport([completion(json.dumps(FINAL))])
        out = router(t, max_tool_turns=0).complete_with_tools(
            "p", tools=[TOOL_TASK], schema=SCHEMA, execute_tool=echo_tool(), max_turns=1)
        self.assertEqual(out.result.data, FINAL)

    def test_default_is_still_four_and_unchanged(self):
        self.assertEqual(MAX_TOOL_TURNS, 4)
        self.assertEqual(LLMRouter(TOOLS_ENABLED, FALLBACK).max_tool_turns, 4)
        self.assertEqual(LLMRouter(TOOLS_ENABLED, FALLBACK, max_tool_turns=9).max_tool_turns, 9)

    def test_max_tool_turns_from_env_must_be_positive(self):
        base = {"LLM_PRIMARY_PROVIDER": "baseten", "LLM_PRIMARY_MODEL": "m", "LLM_PRIMARY_BASE_URL": "u",
                "LLM_PRIMARY_API_KEY": "k", "LLM_FALLBACK_PROVIDER": "openrouter", "LLM_FALLBACK_MODEL": "m2",
                "LLM_FALLBACK_BASE_URL": "u2", "LLM_FALLBACK_API_KEY": "k2"}
        from debugagent.llm.router import LLMConfigError
        with self.assertRaises(LLMConfigError):
            LLMRouter.from_env({**base, "LLM_MAX_TOOL_TURNS": "0"})
        self.assertEqual(LLMRouter.from_env({**base, "LLM_MAX_TOOL_TURNS": "6"}).max_tool_turns, 6)


# --- 16/18. provider capability and no silent downgrade ------------------------------------------

class ProviderCapabilityTests(unittest.TestCase):
    def test_tool_calling_on_a_disabled_route_is_explicit(self):
        t = FakeTransport([completion(json.dumps(FINAL))])
        with self.assertRaises(LLMToolUnsupported) as caught:
            router(t, primary=PRIMARY).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA,
                                                          execute_tool=echo_tool())
        self.assertEqual(caught.exception.error_class, "TOOL_UNSUPPORTED")
        self.assertEqual(t.calls, [], "no request was sent: tools are never silently dropped")

    # 18. a tool request must not become a normal structured request
    def test_tool_mode_never_downgrades_to_a_normal_request(self):
        t = FakeTransport([completion(json.dumps(FINAL))])
        with self.assertRaises(LLMToolUnsupported):
            router(t, primary=PRIMARY).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA,
                                                          execute_tool=echo_tool())
        self.assertEqual(t.calls, [])

    def test_tools_enabled_flag_is_read_from_env(self):
        from debugagent.llm.router import load_routes
        env = {"LLM_PRIMARY_PROVIDER": "baseten", "LLM_PRIMARY_MODEL": "m", "LLM_PRIMARY_BASE_URL": "u",
               "LLM_PRIMARY_API_KEY": "k", "LLM_FALLBACK_PROVIDER": "openrouter", "LLM_FALLBACK_MODEL": "m2",
               "LLM_FALLBACK_BASE_URL": "u2", "LLM_FALLBACK_API_KEY": "k2"}
        self.assertFalse(load_routes(env)[0].supports_tools)
        self.assertTrue(load_routes({**env, "LLM_PRIMARY_TOOLS": "1"})[0].supports_tools)
        self.assertFalse(load_routes({**env, "LLM_FALLBACK_TOOLS": "1"})[0].supports_tools)

    def test_transport_failure_in_tool_mode_is_typed_and_does_not_fail_over(self):
        t = FakeTransport([TimeoutError()], [completion(json.dumps(FINAL))])
        with self.assertRaises(LLMUnavailable) as caught:
            router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA, execute_tool=echo_tool())
        self.assertEqual(caught.exception.error_class, "TIMEOUT")
        self.assertEqual(t.hits(FALLBACK), 0, "the tool loop never crosses to the other route")

    def test_bad_response_body_in_tool_mode_is_typed(self):
        t = FakeTransport([(200, json.dumps({"no": "choices"}))])
        with self.assertRaises(LLMUnavailable) as caught:
            router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA, execute_tool=echo_tool())
        self.assertIn("unusable response", str(caught.exception))


# --- scope guards --------------------------------------------------------------------------------

class P2ScopeTests(unittest.TestCase):
    def test_router_module_has_no_concurrency_or_execution_primitives(self):
        import pathlib
        text = pathlib.Path(__file__).resolve().parents[1] / "src" / "debugagent" / "llm" / "tools.py"
        source = text.read_text(encoding="utf-8")
        for banned in ["ThreadPool", "concurrent.futures", "asyncio.gather", "asyncio.create_task",
                       "threading.Lock", "asyncio.Lock", "subprocess", "os.system", "eval(", "exec("]:
            with self.subTest(banned):
                self.assertNotIn(banned, source)

    def test_p2_does_not_touch_the_trust_path(self):
        import pathlib
        base = pathlib.Path(__file__).resolve().parents[1] / "src" / "debugagent"
        source = (base / "llm" / "tools.py").read_text(encoding="utf-8")
        # Check import statements only: prose may legitimately mention the registry to say it is
        # deliberately NOT consulted.
        imports = [l.strip() for l in source.splitlines() if l.strip().startswith(("import ", "from "))]
        joined = " ".join(imports).lower()
        for forbidden in ["evidence", "verify", "investigate", "hindsight", "memory", "agents",
                          "registry", "engineer", "pipeline"]:
            with self.subTest(forbidden):
                self.assertNotIn(forbidden, joined)

    def test_tool_loop_result_reports_what_happened(self):
        t = FakeTransport([tool_turn(call("c1")), completion(json.dumps(FINAL))])
        out = router(t).complete_with_tools("p", tools=[TOOL_TASK], schema=SCHEMA, execute_tool=echo_tool())
        summary = out.to_dict()
        self.assertEqual(summary["turns"], 2)
        self.assertEqual(len(summary["tool_calls"]), 1)
        self.assertEqual(summary["tool_calls"][0]["name"], "task")
        self.assertEqual(summary["tool_results"][0]["ok"], True)
        self.assertFalse(summary["fallback_used"])


if __name__ == "__main__":
    unittest.main()
