"""MK3: deterministic normalizer. Nothing invented, nothing overwritten, conflicts fail closed."""

from __future__ import annotations

import unittest

import loop_support  # noqa: F401  (path wiring)
from debugagent.domain.errors import InputError, NormalizationError
from debugagent.domain.models import DebugInput
from debugagent.services.normalization_service import TAXONOMY_KEYS, NormalizationService

normalize = NormalizationService().normalize

ACT2 = DebugInput(
    description=("media-uploader resets connections on uploads over 2 MB behind nginx.\n"
                 "service=media-uploader proxy=nginx-1.25\n"
                 "uploads under 2 MB succeed\n"
                 "status: 499 in the access log, size=2.5MB"),
    measurements=["20/20 uploads of 2.5MB fail with ECONNRESET", "uploads under 2 MB succeed"],
    environment_hints={"runtime": "node20"},
)


class NormalizeTests(unittest.TestCase):
    def test_act2_shape(self):
        case = normalize(ACT2)
        self.assertEqual(case.problem_signature, "media-uploader resets connections on uploads over 2 MB behind nginx")
        self.assertEqual(case.environment, {"service": "media-uploader", "runtime": "node20",
                                            "proxy": "nginx-1.25", "region": None})
        self.assertEqual(case.symptoms, ["uploads under 2 MB succeed", "status: 499 in the access log, size=2.5MB",
                                         "20/20 uploads of 2.5MB fail with ECONNRESET"])
        self.assertEqual(case.raw_description, ACT2.description)
        self.assertEqual(case.source_case_ids, [])

    def test_nothing_invented(self):
        case = normalize(DebugInput("payments-api returns 502 for large bodies"))
        self.assertEqual(case.environment, {key: None for key in TAXONOMY_KEYS})
        self.assertEqual(case.symptoms, [])

    def test_non_taxonomy_pairs_stay_symptoms(self):
        case = normalize(DebugInput("upload fails\nsize=2MB status: 502"))
        self.assertEqual(case.symptoms, ["size=2MB status: 502"])
        self.assertIsNone(case.environment["service"])

    def test_key_inside_a_word_is_not_environment(self):
        case = normalize(DebugInput("x\nweb-service=foo my.region=bar"))
        self.assertIsNone(case.environment["service"])
        self.assertIsNone(case.environment["region"])

    def test_values_verbatim_keys_lowercased(self):
        case = normalize(DebugInput("x\nService: Photo-API, Region=eu-west-1."))
        self.assertEqual((case.environment["service"], case.environment["region"]), ("Photo-API", "eu-west-1"))

    def test_env_pairs_in_measurements_are_read(self):  # manual run 2026-09-28: they were silently dropped
        case = normalize(DebugInput("invoice-api times out on long PDFs", ["service=invoice-api runtime=python3.11"]))
        self.assertEqual((case.environment["service"], case.environment["runtime"]), ("invoice-api", "python3.11"))
        self.assertEqual(case.symptoms, [])

    def test_measurement_conflicting_with_description_fails_closed(self):
        with self.assertRaises(NormalizationError):
            normalize(DebugInput("x service=a", ["service=b"]))

    def test_extra_hint_keys_kept(self):
        self.assertEqual(normalize(DebugInput("x", environment_hints={"tls": "1.3"})).environment["tls"], "1.3")

    def test_matching_hint_is_not_a_conflict(self):
        case = normalize(DebugInput("x service=a", environment_hints={"service": "a"}))
        self.assertEqual(case.environment["service"], "a")

    def test_hint_conflicting_with_text_fails_closed(self):
        with self.assertRaises(NormalizationError) as ctx:
            normalize(DebugInput("x service=a", environment_hints={"service": "b"}))
        self.assertIn("description says 'a' but the hint says 'b'", ctx.exception.errors[0])

    def test_text_conflicting_with_itself_fails_closed(self):
        with self.assertRaises(NormalizationError):
            normalize(DebugInput("service=a then later service=b"))

    def test_invalid_hint_key_rejected(self):
        with self.assertRaises(NormalizationError):
            normalize(DebugInput("x", environment_hints={"Bad Key!": "v"}))

    def test_blank_description(self):
        for text in ("", "   \n\t"):
            with self.subTest(text=repr(text)), self.assertRaises(InputError):
                normalize(DebugInput(text))

    def test_deterministic(self):
        self.assertEqual(normalize(ACT2).to_dict(), normalize(ACT2).to_dict())


if __name__ == "__main__":
    unittest.main()
