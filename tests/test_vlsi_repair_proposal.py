"""VLSI-1D.4a: authoring a semantic SDC repair proposal.

Runs against the REAL parser, analyzer and contract validation. The fake model in `loop_support` applies
the real JSON-schema validation and the real caller `check`, so a schema violation here is a violation
the production router would also see and retry.

The suite is organised around one claim:

    **a proposal may not contain anything the renderer would have to trust**

which shows up mostly as refusals. The important negative controls are the last two classes:

- a model that names a clock nobody reported is RETRIED, not accepted;
- a model that omits a period gets a VALID `SdcRepair` here, and it is `repair.py` that refuses it later.
  Collapsing those two layers would hide which one said no.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

from debugagent.domains.vlsi.repair import SdcRepair, render_sdc_repair
from debugagent.domains.vlsi.repair_proposal import (
    PROPOSAL_REFUSAL_REASONS,
    REPAIR_PROPOSAL_SCHEMA,
    ProposalRefusal,
    RepairProposalSet,
    build_repair_prompt,
    clock_check,
    generate_repair_proposals,
)
from debugagent.domains.vlsi.sdc_analyzer import analyze_sdc_constraints
from debugagent.domains.vlsi.sdc_parser import parse_sdc

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_support import FakeLLM  # noqa: E402

REF = "constraints/top.sdc"
CLOCK = "create_clock -name core_clk -period 10 [get_ports clk]\n"
DELAY = "set_input_delay -clock pll_clk 0.4 [get_ports d]\n"

MISSING_SOURCE = CLOCK + DELAY
DUPLICATE_SOURCE = CLOCK + CLOCK
CONFLICT_SOURCE = CLOCK + "create_clock -name core_clk -period 12 [get_ports clk]\n"


def analyse(text: str, ref: str = REF):
    return analyze_sdc_constraints(parse_sdc(text, ref=ref))


def proposal(**overrides) -> dict:
    fields = {"target": REF, "operation": "define_clock", "clock": "pll_clk"}
    fields.update(overrides)
    return fields


def wrap(*items) -> dict:
    return {"repair_proposals": list(items)}


class TheSchemaIsConstrained(unittest.TestCase):
    def test_the_schema_forbids_extra_properties(self):
        self.assertFalse(REPAIR_PROPOSAL_SCHEMA["additionalProperties"],
                         "the envelope must be closed too, or a model could add sibling commentary")
        item = REPAIR_PROPOSAL_SCHEMA["properties"]["repair_proposals"]["items"]
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(set(item["properties"]),
                         {"target", "operation", "clock", "period", "unit", "objects"})

    def test_a_sibling_key_at_the_top_level_is_rejected(self):
        from debugagent.llm.router import StructuredOutputError

        with self.assertRaises(StructuredOutputError):
            generate_repair_proposals(analyse(MISSING_SOURCE),
                                      FakeLLM({"repair_proposals": [], "notes": "trust me"}))

    def test_the_schema_constrains_operation_to_an_enum(self):
        item = REPAIR_PROPOSAL_SCHEMA["properties"]["repair_proposals"]["items"]
        self.assertEqual(item["properties"]["operation"]["enum"], ["define_clock",
                                                                  "remove_duplicate_clock"])

    def test_the_schema_requires_a_target_an_operation_and_a_clock(self):
        item = REPAIR_PROPOSAL_SCHEMA["properties"]["repair_proposals"]["items"]
        self.assertEqual(set(item["required"]), {"target", "operation", "clock"})

    def test_values_the_model_may_not_know_stay_optional(self):
        """Making period/objects required would force a value into every response.

        The omission has to be expressible, because that omission is the safe path.
        """
        item = REPAIR_PROPOSAL_SCHEMA["properties"]["repair_proposals"]["items"]
        self.assertNotIn("period", item["required"])
        self.assertNotIn("objects", item["required"])

    def test_the_schema_carries_no_field_for_text(self):
        item = REPAIR_PROPOSAL_SCHEMA["properties"]["repair_proposals"]["items"]
        for forbidden in ("text", "tcl", "content", "command", "line", "diff", "sdc"):
            self.assertNotIn(forbidden, item["properties"])


class ValidProposalsBecomeRepairs(unittest.TestCase):
    def test_a_well_specified_proposal_becomes_a_repair(self):
        baseline = analyse(MISSING_SOURCE)
        llm = FakeLLM(wrap(proposal(period="10", objects=["pll"])))
        result = generate_repair_proposals(baseline, llm)
        self.assertEqual(len(result.repairs), 1)
        repair = result.repairs[0]
        self.assertEqual(repair.operation, "define_clock")
        self.assertEqual(repair.clock, "pll_clk")
        self.assertEqual(repair.period, "10")
        self.assertEqual(repair.objects, ("pll",))
        self.assertEqual(result.refusals, ())
        self.assertFalse(result.empty)

    def test_a_duplicate_removal_proposal_is_accepted(self):
        result = generate_repair_proposals(
            analyse(DUPLICATE_SOURCE),
            FakeLLM(wrap(proposal(operation="remove_duplicate_clock", clock="core_clk"))))
        self.assertEqual([r.operation for r in result.repairs], ["remove_duplicate_clock"])

    def test_provenance_is_carried_from_the_model(self):
        result = generate_repair_proposals(analyse(MISSING_SOURCE),
                                           FakeLLM(wrap(proposal(period="10", objects=["pll"]))))
        self.assertEqual(result.provider, "fake")
        self.assertEqual(result.model, "fake-model")
        self.assertFalse(result.fallback_used)

    def test_the_result_serialises(self):
        result = generate_repair_proposals(analyse(MISSING_SOURCE),
                                           FakeLLM(wrap(proposal(period="10", objects=["pll"]))))
        record = result.to_dict()
        self.assertEqual(len(record["repairs"]), 1)
        self.assertEqual(record["repairs"][0]["clock"], "pll_clk")
        self.assertEqual(record["refusals"], [])

    def test_no_proposals_is_a_valid_empty_answer_not_a_failure(self):
        result = generate_repair_proposals(analyse(CLOCK), FakeLLM(wrap()))
        self.assertTrue(result.empty)
        self.assertEqual(result.refusals, ())
        self.assertEqual(result.repairs, ())


class HallucinatedClocksAreRetried(unittest.TestCase):
    """The `citation_check` equivalent: only a reported clock may be proposed."""

    def test_a_clock_nobody_reported_is_refused_and_retried(self):
        baseline = analyse(MISSING_SOURCE)
        llm = FakeLLM(wrap(proposal(clock="imaginary_clk", period="10", objects=["p"])),
                      wrap(proposal(period="10", objects=["pll"])))
        result = generate_repair_proposals(baseline, llm)
        self.assertEqual([r.clock for r in result.repairs], ["pll_clk"])
        self.assertEqual(len(result.attempts), 2)
        self.assertIn("imaginary_clk", result.attempts[0]["detail"])

    def test_a_clock_from_a_different_finding_is_refused(self):
        """`core_clk` is reported as DUPLICATE, so proposing to DEFINE it is not a clock-name error -
        but a define_clock on it has no missing_constraint behind it, and the renderer refuses it."""
        baseline = analyse(DUPLICATE_SOURCE)
        llm = FakeLLM(wrap(proposal(clock="core_clk", period="10", objects=["p"])))
        result = generate_repair_proposals(baseline, llm)
        self.assertEqual([r.clock for r in result.repairs], ["core_clk"])
        rendered = render_sdc_repair(result.repairs[0], baseline, DUPLICATE_SOURCE)
        self.assertEqual(rendered.reason, "no_supporting_finding")

    def test_persistent_hallucination_raises_rather_than_returning_nothing(self):
        """A model that will not cite a reported clock must fail loudly, not look like it found no repair."""
        from debugagent.llm.router import StructuredOutputError

        llm = FakeLLM(wrap(proposal(clock="nope", period="1", objects=["p"])))
        with self.assertRaises(StructuredOutputError):
            generate_repair_proposals(analyse(MISSING_SOURCE), llm)

    def test_the_check_names_the_clocks_that_were_reported(self):
        problems = clock_check(analyse(MISSING_SOURCE))(wrap(proposal(clock="nope")))
        self.assertEqual(len(problems), 1)
        self.assertIn("pll_clk", problems[0])

    def test_the_check_passes_a_reported_clock(self):
        self.assertEqual(clock_check(analyse(MISSING_SOURCE))(
            wrap(proposal(period="10", objects=["pll"]))), [])


class ProposalsAreValidatedNotTrusted(unittest.TestCase):
    def test_an_unknown_field_in_a_proposal_never_reaches_the_contract(self):
        """The schema is the FIRST gate: an extra property is rejected before anything reads it.

        The router retries such an output, so the model gets told and can answer correctly. That is a
        better outcome than accepting the entry and dropping `tcl` silently.
        """
        from debugagent.llm.router import StructuredOutputError

        baseline = analyse(MISSING_SOURCE)
        raw = proposal(period="10", objects=["pll"])
        raw["tcl"] = "create_clock -name pll_clk -period 999"
        with self.assertRaises(StructuredOutputError) as caught:
            generate_repair_proposals(baseline, FakeLLM(wrap(raw)))
        detail = "; ".join(str(attempt.get("detail", "")) for attempt in caught.exception.attempts)
        self.assertIn("tcl", detail,
                      "the violation must be reported to the model, not merely counted")

    def test_a_blank_object_is_the_reachable_contract_failure(self):
        """`invalid_repair` has exactly one honest route in.

        A blank object passes the JSON schema (the items declare no `minLength`), passes the clock check,
        and is rejected by `SdcRepair`'s own contract - which rejects rather than dropping it, because a
        silently dropped object would render a constraint missing a port the engineer believed was there.
        """
        baseline = analyse(MISSING_SOURCE)
        result = generate_repair_proposals(
            baseline, FakeLLM(wrap(proposal(period="10", objects=["pll", ""]))))
        self.assertEqual(result.repairs, ())
        self.assertEqual([f.reason for f in result.refusals], ["invalid_repair"])
        self.assertIn("objects[1]", result.refusals[0].detail)

    def test_a_clock_nobody_reported_never_reaches_the_contract_either(self):
        from debugagent.llm.router import StructuredOutputError

        with self.assertRaises(StructuredOutputError):
            generate_repair_proposals(analyse(MISSING_SOURCE),
                                      FakeLLM(wrap(proposal(clock=" pll_clk", period="10"))))

    def test_a_duplicate_proposal_is_refused_and_the_first_is_kept(self):
        baseline = analyse(MISSING_SOURCE)
        llm = FakeLLM(wrap(proposal(period="10", objects=["pll"]),
                           proposal(period="999", objects=["other"])))
        result = generate_repair_proposals(baseline, llm)
        self.assertEqual([r.period for r in result.repairs], ["10"])
        self.assertEqual([f.reason for f in result.refusals], ["duplicate_proposal"])

    def test_two_different_operations_on_one_clock_both_survive(self):
        baseline = analyse(MISSING_SOURCE)
        llm = FakeLLM(wrap(proposal(period="10", objects=["pll"]),
                           proposal(operation="remove_duplicate_clock", clock="pll_clk")))
        result = generate_repair_proposals(baseline, llm)
        self.assertEqual([r.operation for r in result.repairs],
                         ["define_clock", "remove_duplicate_clock"])
        self.assertEqual(result.refusals, ())

    def test_an_incomplete_analysis_refuses_without_calling_the_model(self):
        llm = FakeLLM(wrap(proposal(period="10", objects=["pll"])))
        baseline = analyse("set_multicycle_path -setup 2 -from [get_clocks c]\n")
        self.assertFalse(baseline.complete)
        result = generate_repair_proposals(baseline, llm)
        self.assertEqual(result.repairs, ())
        self.assertEqual([f.reason for f in result.refusals], ["incomplete_evidence"])
        self.assertEqual(llm.prompts, [], "the model must not be asked to repair a phantom condition")

    def test_every_declared_refusal_reason_is_reachable(self):
        """A reason in `PROPOSAL_REFUSAL_REASONS` that nothing produces is documentation."""
        reached = set()

        incomplete = generate_repair_proposals(
            analyse("set_false_path -from [get_clocks a] -to [get_clocks b]\n"), FakeLLM())
        reached.add(incomplete.refusals[0].reason)

        invalid = generate_repair_proposals(
            analyse(MISSING_SOURCE), FakeLLM(wrap(proposal(period="10", objects=["pll", ""]))))
        reached.add(invalid.refusals[0].reason)

        duplicate = generate_repair_proposals(
            analyse(MISSING_SOURCE),
            FakeLLM(wrap(proposal(period="10", objects=["pll"]), proposal(period="20"))))
        reached.add(duplicate.refusals[0].reason)

        self.assertEqual(reached, set(PROPOSAL_REFUSAL_REASONS),
                         f"declared but unreachable: {sorted(set(PROPOSAL_REFUSAL_REASONS) - reached)}")


class OmissionIsNotFabrication(unittest.TestCase):
    """The core safety property, and the reason the schema leaves values optional."""

    def test_a_proposal_without_a_period_is_a_valid_repair_here(self):
        result = generate_repair_proposals(analyse(MISSING_SOURCE), FakeLLM(wrap(proposal())))
        self.assertEqual(len(result.repairs), 1)
        self.assertEqual(result.repairs[0].period, "")
        self.assertEqual(result.refusals, ())

    def test_and_it_is_the_RENDERER_that_refuses_it(self):
        """The layers stay distinct: this module accepts the intent, `repair.py` judges it.

        Collapsing them would report a refusal as a malformed proposal, sending an engineer to look at
        the model's output when the actual answer is that the evidence does not support the repair.
        """
        result = generate_repair_proposals(analyse(MISSING_SOURCE), FakeLLM(wrap(proposal())))
        rendered = render_sdc_repair(result.repairs[0], analyse(MISSING_SOURCE), MISSING_SOURCE)
        self.assertEqual(rendered.reason, "missing_required_value")
        self.assertNotIn("invalid_repair", [f.reason for f in result.refusals])

    def test_a_specified_period_reaches_the_renderer_and_renders(self):
        result = generate_repair_proposals(
            analyse(MISSING_SOURCE), FakeLLM(wrap(proposal(period="10", objects=["pll"]))))
        rendered = render_sdc_repair(result.repairs[0], analyse(MISSING_SOURCE), MISSING_SOURCE)
        self.assertIn("pll_clk -period 10", rendered.proposed)

    def test_a_conflicting_clock_is_offered_no_repair_by_the_renderer(self):
        result = generate_repair_proposals(
            analyse(CONFLICT_SOURCE),
            FakeLLM(wrap(proposal(operation="remove_duplicate_clock", clock="core_clk"))))
        rendered = render_sdc_repair(result.repairs[0], analyse(CONFLICT_SOURCE), CONFLICT_SOURCE)
        self.assertEqual(rendered.reason, "conflicting_finding")


class ThePromptShowsOnlyEvidence(unittest.TestCase):
    @property
    def _prompt(self) -> str:
        return build_repair_prompt(analyse(MISSING_SOURCE))

    def test_every_reported_finding_appears_with_its_identity_and_facts(self):
        for fragment in ("missing_constraint", "missing_constraint:clock=pll_clk",
                         "clock=pll_clk", "constraints/top.sdc"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self._prompt)

    def test_the_prompt_carries_no_recalled_case_or_symptom(self):
        for forbidden in ("PAST CASES", "case_id", "supporting_case_ids", "relevance_class"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, self._prompt)

    def test_the_prompt_tells_the_model_to_omit_rather_than_invent(self):
        """The instruction must be stated as a rule, not merely implied.

        Asserted as the exact sentence, because a weaker check ("does the word omit appear") passes even
        if the rule is inverted and the model is told to supply a period it cannot justify - which is the
        single most damaging change this prompt could suffer.
        """
        self.assertIn("Supply period and objects ONLY if the findings state them", self._prompt)
        self.assertIn("OMIT the field", self._prompt)
        self.assertIn("fabricated", self._prompt.lower())
        self.assertNotIn("sensible default", self._prompt.lower())

    def test_the_prompt_forbids_writing_sdc(self):
        lowered = self._prompt.lower()
        self.assertIn("never write sdc", lowered)
        self.assertIn("never invent a clock", lowered)

    def test_the_prompt_refuses_conflicts_explicitly(self):
        self.assertIn("conflicting_constraint` has NO repair", self._prompt)

    def test_a_clean_analysis_produces_an_empty_evidence_section(self):
        prompt = build_repair_prompt(analyse(CLOCK))
        self.assertIn("none reported", prompt)


class ThisModuleIsInert(unittest.TestCase):
    """No wiring, no write path, no pipeline coupling."""

    @property
    def _source(self) -> str:
        return Path(sys.modules[generate_repair_proposals.__module__].__file__).read_text(
            encoding="utf-8")

    def _code(self) -> str:
        """The module with every docstring removed.

        Prose is excluded deliberately: this module's rules text contains "NEVER write SDC" and its
        docstring says "nothing is written" precisely because it does neither. Scanning raw text would
        report the documentation of the boundary as a breach of it.
        """
        tree = ast.parse(self._source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = node.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
        return ast.unparse(tree)

    def test_it_imports_no_pipeline_no_llm_and_no_writer(self):
        imported = set()
        for node in ast.walk(ast.parse(self._source)):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        for forbidden in ("debugagent.pipeline", "debugagent.llm", "debugagent.agents",
                          "debugagent.memory", "debugagent.application", "os", "pathlib",
                          "subprocess", "shutil", "json"):
            with self.subTest(module=forbidden):
                self.assertFalse([n for n in imported if n.split(".")[0] == forbidden.split(".")[0]],
                                 f"must not import {forbidden}")

    def test_it_references_no_writer_and_no_application(self):
        code = self._code()
        for forbidden in ("RepositoryWriter", "RepairApproval", "content_hash", "WebEngineer",
                          "investigate", "run_worker_stage"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, code)

    def test_it_never_renders_and_never_writes(self):
        """1D.4a produces intent only. Rendering is `repair.py`; writing does not exist yet.

        Checked over the AST rather than by substring, because the RULES text legitimately contains the
        words "write" and "patch" - it is PROMPT DATA telling the model not to. Scanning text would flag
        the instruction as the violation; what matters is that no code path performs either act.
        """
        tree = ast.parse(self._source)
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        for forbidden in ("render_sdc_repair", "write", "apply", "commit", "replace", "open",
                          "write_text", "rename", "unlink"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, attributes)
                self.assertNotIn(forbidden, called)
        self.assertNotIn("render_sdc_repair", self._code())

    def test_the_rules_text_forbids_writing_sdc_while_the_code_cannot(self):
        """The prohibition is stated in the prompt; the capability is absent from the code.

        Two different things, deliberately: the prompt tells a cooperating model what not to emit, and the
        type system plus this check mean a non-cooperating one still cannot get text through.
        """
        self.assertIn("NEVER write SDC", self._source)
        self.assertNotIn("tcl", str(REPAIR_PROPOSAL_SCHEMA).lower())

    def test_the_hypothesis_contract_is_untouched(self):
        """The whole reason this is a separate call: `RESPONSE_SCHEMA` gained no `repair_proposals`."""
        from debugagent.pipeline.hypothesize import RESPONSE_SCHEMA

        self.assertNotIn("repair_proposals", RESPONSE_SCHEMA)
        self.assertFalse(RESPONSE_SCHEMA["properties"]["hypotheses"]["items"]
                         ["additionalProperties"])

    def test_the_refusal_carries_no_usable_repair_fields(self):
        refusal = ProposalRefusal(reason="invalid_repair", detail="x")
        self.assertFalse(hasattr(refusal, "proposed"))
        self.assertEqual(set(refusal.to_dict()), {"reason", "detail", "index", "clock"})

    def test_the_result_is_frozen(self):
        result = RepairProposalSet()
        with self.assertRaises(AttributeError):
            result.repairs = (SdcRepair(operation="define_clock", clock="c"),)


if __name__ == "__main__":
    unittest.main()