"""MK4: router failover rules, fail-closed validation, MK0-derived payloads. Fake transport, no network."""

from __future__ import annotations

import json
import unittest

import loop_support  # noqa: F401  (path wiring)
from debugagent.adapters.llm.response import extract_json
from debugagent.adapters.llm.router import LLMRouter
from debugagent.adapters.llm.settings import Route, load_routes
from debugagent.domain.errors import LLMAuthError, LLMConfigError, LLMUnavailable, StructuredOutputError

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["hypotheses"],
    "properties": {"hypotheses": {"type": "array", "items": {"type": "string"}}},
}
GOOD = {"hypotheses": ["proxy body limit"]}
PRIMARY = Route("primary", "baseten", "deepseek-ai/DeepSeek-V4.1-Flash", "https://p.example/v1", "pk-secret", 10)
FALLBACK = Route("fallback", "openrouter", "stealth/space-bunny-alpha", "https://f.example/v1", "fk-secret", 17)


def completion(content, finish="stop", **message):
    return 200, json.dumps({"choices": [{"message": {"content": content, **message}, "finish_reason": finish}]})


class FakeTransport:
    """Scripted responses per base URL. An entry is (status, text) or an exception to raise."""

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


def router(transport, **kwargs):
    return LLMRouter(PRIMARY, FALLBACK, transport=transport, **kwargs)


class HappyPathTests(unittest.TestCase):
    def test_primary_serves(self):
        t = FakeTransport([completion(json.dumps(GOOD))])
        result = router(t).complete_structured("p", schema=SCHEMA)
        self.assertEqual(result.data, GOOD)
        self.assertEqual((result.provider, result.fallback_used), ("baseten", False))
        self.assertEqual(t.hits(FALLBACK), 0)

    def test_payload_carries_mk0_decisions(self):
        t = FakeTransport([completion(json.dumps(GOOD))], [completion(json.dumps(GOOD))])
        r = router(t)
        r.complete_structured("p", schema=SCHEMA, name="hypotheses")
        _, key, payload, timeout = t.calls[0]
        self.assertEqual((key, timeout), ("pk-secret", 10))
        self.assertEqual(payload["chat_template_kwargs"], {"thinking": False})
        self.assertEqual(payload["max_tokens"], 1500)
        self.assertEqual(payload["response_format"]["json_schema"]["schema"], SCHEMA)
        self.assertNotIn("provider", payload)  # never require_parameters

        LLMRouter(FALLBACK, PRIMARY, transport=t).complete_structured("p", schema=SCHEMA)
        openrouter_payload = t.calls[1][2]
        self.assertEqual(openrouter_payload["reasoning"], {"effort": "low"})
        self.assertNotIn("provider", openrouter_payload)
        self.assertNotIn("chat_template_kwargs", openrouter_payload)

    def test_api_key_never_in_repr_or_attempts(self):
        t = FakeTransport([completion(json.dumps(GOOD))])
        result = router(t).complete_structured("p", schema=SCHEMA)
        self.assertNotIn("pk-secret", repr(PRIMARY) + json.dumps(result.attempts))


class FailoverTests(unittest.TestCase):
    def assert_fails_over(self, primary_step):
        t = FakeTransport([primary_step], [completion(json.dumps(GOOD))])
        result = router(t).complete_structured("p", schema=SCHEMA)
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(t.hits(PRIMARY), 1, "transport failures go straight to fallback, no same-route retry")
        self.assertEqual([a["outcome"] for a in result.attempts], ["error", "served"])
        return result

    def test_timeout(self):
        self.assertEqual(self.assert_fails_over(TimeoutError()).attempts[0]["error_class"], "TIMEOUT")

    def test_unreachable(self):
        self.assertEqual(self.assert_fails_over(ConnectionRefusedError()).attempts[0]["error_class"], "UNREACHABLE")

    def test_rate_limited(self):
        self.assert_fails_over((429, '{"error": {"message": "slow down"}}'))

    def test_server_errors(self):
        for status in (500, 502, 503):
            with self.subTest(status=status):
                self.assert_fails_over((status, "upstream error"))

    def test_error_inside_200(self):
        self.assertEqual(self.assert_fails_over((200, '{"error": {"code": 502}}')).attempts[0]["error_class"],
                         "BAD_RESPONSE")

    def test_non_json_200(self):
        self.assert_fails_over((200, "<html>gateway</html>"))


class NoFailoverTests(unittest.TestCase):
    def test_auth_and_config_classes_never_fail_over(self):
        cases = {400: LLMConfigError, 401: LLMAuthError, 402: LLMConfigError, 403: LLMAuthError,
                 404: LLMConfigError, 422: LLMConfigError}
        for status, error in cases.items():
            with self.subTest(status=status):
                t = FakeTransport([(status, '{"error": {"message": "no"}}')], [completion(json.dumps(GOOD))])
                with self.assertRaises(error):
                    router(t).complete_structured("p", schema=SCHEMA)
                self.assertEqual(t.hits(FALLBACK), 0)

    def test_auth_error_on_fallback_is_raised_not_hidden(self):
        t = FakeTransport([TimeoutError()], [(401, "{}")])
        with self.assertRaises(LLMAuthError):
            router(t).complete_structured("p", schema=SCHEMA)

    def test_both_routes_down(self):
        t = FakeTransport([TimeoutError()], [(503, "down")])
        with self.assertRaises(LLMUnavailable) as ctx:
            router(t).complete_structured("p", schema=SCHEMA)
        self.assertEqual(len(ctx.exception.attempts), 2)


class InvalidOutputTests(unittest.TestCase):
    def test_retry_once_then_fail_over(self):
        t = FakeTransport([completion("not json"), completion('{"hypotheses": [1]}')],
                          [completion(json.dumps(GOOD))])
        result = router(t).complete_structured("p", schema=SCHEMA)
        self.assertTrue(result.fallback_used)
        self.assertEqual(t.hits(PRIMARY), 2)
        self.assertEqual([a["error_class"] for a in result.attempts], ["INVALID_OUTPUT", "INVALID_OUTPUT", None])

    def test_retry_can_recover_on_primary(self):
        t = FakeTransport([completion(None, finish="length"), completion(json.dumps(GOOD))])
        result = router(t).complete_structured("p", schema=SCHEMA)
        self.assertFalse(result.fallback_used)

    def test_invalid_everywhere_raises_never_returns_text(self):
        t = FakeTransport([completion("{}"), completion("{}")], [completion("prose answer"), completion("[1]")])
        with self.assertRaises(StructuredOutputError) as ctx:
            router(t).complete_structured("p", schema=SCHEMA)
        self.assertEqual(len(ctx.exception.attempts), 4)

    def test_caller_check_counts_as_invalid(self):
        bad = {"hypotheses": ["cites seed-999"]}
        t = FakeTransport([completion(json.dumps(bad)), completion(json.dumps(bad))], [completion(json.dumps(GOOD))])
        check = lambda data: ["invented id seed-999"] if "cites seed-999" in data["hypotheses"] else []  # noqa: E731
        result = router(t).complete_structured("p", schema=SCHEMA, check=check)
        self.assertEqual(result.data, GOOD)
        self.assertIn("invented id", result.attempts[0]["detail"])

    def test_fallback_also_retries_invalid_output_once(self):  # flow C, 2026-09-28: Space Bunny added `rank`
        bad = {"hypotheses": ["x"], "rank": 1}
        t = FakeTransport([TimeoutError()], [completion(json.dumps(bad)), completion(json.dumps(GOOD))])
        result = router(t).complete_structured("p", schema=SCHEMA)
        self.assertTrue(result.fallback_used)
        self.assertEqual([a["error_class"] for a in result.attempts], ["TIMEOUT", "INVALID_OUTPUT", None])

    def test_retries_configurable_to_zero(self):
        t = FakeTransport([completion("nope")], [completion(json.dumps(GOOD))])
        self.assertTrue(router(t, retries_on_invalid=0).complete_structured("p", schema=SCHEMA).fallback_used)


class ParserTests(unittest.TestCase):
    """Response shapes observed in MK0 (S5)."""

    def body(self, content, finish="stop", **message):
        return json.loads(completion(content, finish, **message)[1])

    def test_clean_and_fenced(self):
        self.assertEqual(extract_json(self.body(json.dumps(GOOD)))[0], GOOD)
        self.assertEqual(extract_json(self.body("```json\n" + json.dumps(GOOD) + "\n```"))[0], GOOD)

    def test_content_list_is_joined(self):
        parts = [{"type": "text", "text": '{"hypotheses": '}, {"type": "text", "text": '["x"]}'}]
        self.assertEqual(extract_json(self.body(parts))[0], {"hypotheses": ["x"]})

    def test_reasoning_fields_ignored(self):
        body = self.body(None, reasoning_content='{"hypotheses": ["from reasoning"]}')
        self.assertEqual(extract_json(body), (None, "empty content"))

    def test_whitespace_padding_is_empty(self):  # Space Bunny padded truncated output with blank lines
        self.assertEqual(extract_json(self.body("\n    \n\n   "))[1], "empty content")

    def test_truncation_is_invalid_even_if_parseable(self):
        self.assertEqual(extract_json(self.body(json.dumps(GOOD), finish="length"))[1], "truncated at max_tokens")

    def test_bare_array_wrapped_only_when_schema_has_one_array_key(self):
        body = self.body('["proxy body limit"]')
        self.assertEqual(extract_json(body, SCHEMA)[0], {"hypotheses": ["proxy body limit"]})
        two_keys = {"type": "object", "required": ["a", "b"], "properties": {"a": {"type": "array"}, "b": {"type": "array"}}}
        self.assertEqual(extract_json(body, two_keys)[1], "JSON root is not an object")

    def test_wrapped_array_is_still_validated(self):
        t = FakeTransport([completion("[1, 2]"), completion("[3]")], [completion(json.dumps(GOOD))])
        result = router(t).complete_structured("p", schema=SCHEMA)
        self.assertTrue(result.fallback_used, "wrapped content that fails the schema is still invalid")

    def test_prose_and_non_object_rejected(self):
        self.assertEqual(extract_json(self.body('Here: {"a": 1}'))[1], "content is not JSON")
        self.assertEqual(extract_json(self.body("[1, 2]"))[1], "JSON root is not an object")
        self.assertEqual(extract_json({"choices": []})[1], "no choices")


class ConfigTests(unittest.TestCase):
    ENV = {
        "LLM_PRIMARY_PROVIDER": "baseten", "LLM_PRIMARY_MODEL": "m1", "LLM_PRIMARY_BASE_URL": "https://p/v1/",
        "LLM_PRIMARY_API_KEY": "k1", "LLM_FALLBACK_PROVIDER": "openrouter", "LLM_FALLBACK_MODEL": "m2",
        "LLM_FALLBACK_BASE_URL": "https://f/v1", "LLM_FALLBACK_API_KEY": "k2",
        "LLM_TIMEOUT_S": "30", "LLM_PRIMARY_TIMEOUT_S": "10",
    }

    def test_routes_from_env(self):
        primary, fallback = load_routes(self.ENV)
        self.assertEqual((primary.provider, primary.base_url, primary.timeout_s), ("baseten", "https://p/v1", 10))
        self.assertEqual(fallback.timeout_s, 30)  # falls back to LLM_TIMEOUT_S

    def test_missing_values_listed(self):
        env = dict(self.ENV, LLM_FALLBACK_API_KEY="", LLM_PRIMARY_MODEL="")
        with self.assertRaises(LLMConfigError) as ctx:
            load_routes(env)
        self.assertIn("LLM_PRIMARY_MODEL", str(ctx.exception))
        self.assertIn("LLM_FALLBACK_API_KEY", str(ctx.exception))
        self.assertNotIn("k1", str(ctx.exception))

    def test_unknown_provider_rejected(self):
        with self.assertRaises(LLMConfigError):
            load_routes(dict(self.ENV, LLM_PRIMARY_PROVIDER="openai"))

    def test_bad_numbers_rejected(self):
        with self.assertRaises(LLMConfigError):
            load_routes(dict(self.ENV, LLM_PRIMARY_TIMEOUT_S="ten"))
        with self.assertRaises(LLMConfigError):
            LLMRouter.from_env(dict(self.ENV, LLM_MAX_RETRIES_PRIMARY="-1"))

    def test_from_env_reads_retries(self):
        self.assertEqual(LLMRouter.from_env(dict(self.ENV, LLM_MAX_RETRIES_PRIMARY="0")).retries_on_invalid, 0)


if __name__ == "__main__":
    unittest.main()
