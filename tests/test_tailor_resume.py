import json
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.tailor_resume import (
    MAX_RESPONSE_TOKENS,
    MODEL_RESPONSE_SCHEMA_V2,
    build_prompt,
    build_request_body,
    request_openrouter,
)
from scripts.resume_grounding import (
    MAX_BULLET_CHARS,
    MAX_HEADLINE_CHARS,
    MAX_SKILL_GROUP_CHARS,
    MAX_SUMMARY_BLOCKS,
    MAX_SUMMARY_CHARS,
    MAX_TOTAL_BULLETS,
    MAX_TOTAL_CANDIDATE_CHARS,
    GroundingError,
    load_manifest,
    parse_and_validate_response,
    parse_source,
    plain_markdown,
    render_report,
    render_resume,
    validate_manifest,
    validate_rendered_markdown,
    validate_rendered_report,
    write_manifest,
)


SYNTHETIC_SOURCE = """# Example Candidate

Quality Engineering | Test Automation
Remote | Brazil
[candidate@example.test](mailto:candidate@example.test) | linkedin.com/in/example | github.com/example

## Professional Summary

QA engineer with experience in automation.

Experienced in reliable software delivery.

## Technical Skills

**Programming Languages:** Python, Java

**Tools:** Git, LambdaTest

**Spoken Languages:** English (professional)

## Professional Experience

### QA Engineer | Acme
Jan 2024 - Feb 2024 | Brazil

- Built API automation with Python.

- Used LambdaTest during cross-platform delivery.

### Beta
São Paulo | Remote

**Senior QA Engineer** | Jan 2023 - Dec 2023

- Planned test strategies for web applications.

## Education

### Systems Degree | Example University
Jan 2020 - Dec 2022 | Completed
"""


MANY_SKILLS_SOURCE = SYNTHETIC_SOURCE.replace(
    "**Tools:** Git, LambdaTest",
    "\n".join(f"**Category {index:02d}:** Value {index:02d}" for index in range(1, 21)),
)


TOTAL_BULLETS_SOURCE = SYNTHETIC_SOURCE.replace(
    "- Used LambdaTest during cross-platform delivery.",
    "- Used LambdaTest during cross-platform delivery.\n\n- Maintained release checks.\n\n- Reviewed test results.\n\n- Documented defects.",
).replace(
    "- Planned test strategies for web applications.",
    "- Planned test strategies for web applications.\n\n- Built regression suites.\n\n- Reviewed release evidence.\n\n- Coordinated defect triage.",
).replace(
    "\n## Education",
    """

### Gamma
Campinas | Remote

**QA Engineer** | Jan 2022 - Dec 2022

- Built test plans.

- Automated regression checks.

- Reviewed defects.

- Supported releases.

### Delta
Jundiaí | Remote

**QA Engineer** | Jan 2021 - Dec 2021

- Built test plans.

- Automated regression checks.

- Reviewed defects.

- Supported releases.

### Epsilon
Remote | Brazil

**QA Engineer** | Jan 2020 - Dec 2020

- Built test plans.

- Automated regression checks.

- Reviewed defects.

- Supported releases.

## Education""",
)


def make_source() -> object:
    return parse_source(SYNTHETIC_SOURCE, "en-US")


def make_response(source, *, requirements=None, selected=None, classifications=None):
    selectable = [fragment.id for fragment in source.fragments.values() if fragment.selectable]
    selected = selected or selectable
    requirements = requirements or [
        {"id": "req-1", "text": "Python automation", "priority": "required"},
        {"id": "req-2", "text": "Swagger documentation", "priority": "preferred"},
    ]
    bullet_ids = [item for item in selected if source.fragments[item].kind == "bullet"]
    evidence = bullet_ids[:1]
    if classifications is None:
        classifications = [
            {"requirement_id": "req-1", "status": "strong", "evidence_ids": evidence},
            {"requirement_id": "req-2", "status": "gap", "evidence_ids": []},
        ]
    return {
        "schema_version": 1,
        "target_company": "Example Systems",
        "target_role": "QA Automation Engineer",
        "selected_fragment_ids": selected,
        "vacancy_requirements": requirements,
        "requirement_classifications": classifications,
        "interview_topics": [{"requirement_id": "req-2"}],
    }


class SourceParserTests(unittest.TestCase):
    def test_synthetic_ci_and_t_shape_and_future_entries_parse(self):
        source = make_source()
        self.assertEqual([item.name for item in source.employers], ["Acme", "Beta"])
        self.assertEqual(len(source.employers[1].header_groups), 2)
        self.assertEqual(len(source.education), 1)
        self.assertEqual(source.spoken_language_id, "skills.spoken-languages")

    def test_real_master_resumes_parse(self):
        for language, path in (("pt-BR", "RESUME_pt-BR.md"), ("en-US", "RESUME_en-US.md")):
            with self.subTest(language=language):
                with open(path, encoding="utf-8") as handle:
                    source = parse_source(handle.read(), language)
                self.assertEqual(len(source.employers), 5)
                self.assertEqual(len(source.education), 2)
                self.assertTrue(all(employer.bullet_ids for employer in source.employers))

    def test_duplicate_skill_fragment_id_is_rejected(self):
        duplicate = SYNTHETIC_SOURCE.replace("**Tools:** Git, LambdaTest", "**Tools:** Git, LambdaTest\n\n**Tools:** Postman")
        with self.assertRaisesRegex(GroundingError, "fragment ID collision"):
            parse_source(duplicate, "en-US")

    def test_missing_heading_and_malformed_bullet_are_rejected(self):
        missing = SYNTHETIC_SOURCE.replace("## Education", "## Training")
        with self.assertRaisesRegex(GroundingError, "required language-specific sections"):
            parse_source(missing, "en-US")
        malformed = SYNTHETIC_SOURCE.replace("- Built API automation with Python.", "Built API automation with Python.")
        with self.assertRaisesRegex(GroundingError, "malformed experience bullet"):
            parse_source(malformed, "en-US")

    def test_more_than_four_bullets_for_one_employer_is_rejected(self):
        expanded = SYNTHETIC_SOURCE.replace(
            "- Used LambdaTest during cross-platform delivery.",
            "- Used LambdaTest during cross-platform delivery.\n\n- Maintained release checks.\n\n- Reviewed test results.\n\n- Documented defects.",
        )
        source = parse_source(expanded, "en-US")
        selected = [fragment.id for fragment in source.fragments.values() if fragment.selectable]
        response = make_response(source, selected=selected)
        with self.assertRaisesRegex(GroundingError, "more than 4 bullets"):
            parse_and_validate_response(json.dumps(response), source)


class ResponseAndRenderingTests(unittest.TestCase):
    def test_valid_selection_renders_exact_source_blocks(self):
        source = make_source()
        response = make_response(source)
        selection = parse_and_validate_response(json.dumps(response), source)
        resume = render_resume(source, selection)
        report = render_report(source, selection)
        validate_rendered_markdown(source, selection, resume)
        validate_rendered_report(source, selection, report)
        self.assertIn("Used LambdaTest during cross-platform delivery.", resume)
        self.assertNotIn("device farm", resume.lower())
        self.assertNotIn("continuous", report.lower())
        self.assertNotIn("probable", report.lower())
        self.assertIn("Swagger documentation", report)
        self.assertIn("No supporting candidate fragment", report)
        self.assertNotIn("Example Systems", resume)
        self.assertNotIn("QA Automation Engineer", resume)

    def test_both_languages_render_from_exact_source(self):
        for language, path in (("pt-BR", "RESUME_pt-BR.md"), ("en-US", "RESUME_en-US.md")):
            with self.subTest(language=language):
                source = parse_source(Path(path).read_text(encoding="utf-8"), language)
                selected = [source.summary_ids[0], source.spoken_language_id]
                selected.extend(item for item in source.skill_ids if item != source.spoken_language_id)
                selected.extend(employer.bullet_ids[0] for employer in source.employers)
                response = make_response(source, selected=selected)
                response["requirement_classifications"][0]["evidence_ids"] = [source.employers[0].bullet_ids[0]]
                selection = parse_and_validate_response(json.dumps(response), source)
                validate_rendered_markdown(source, selection, render_resume(source, selection))

    def test_unknown_duplicate_and_missing_employer_selection_fail(self):
        source = make_source()
        unknown = make_response(source)
        unknown["selected_fragment_ids"] = list(unknown["selected_fragment_ids"]) + ["experience.unknown.bullet.1"]
        with self.assertRaisesRegex(GroundingError, "unknown selected fragment"):
            parse_and_validate_response(json.dumps(unknown), source)

        selected = make_response(source)["selected_fragment_ids"]
        duplicate = make_response(source, selected=selected + [selected[0]])
        with self.assertRaisesRegex(GroundingError, "duplicate selected fragment"):
            parse_and_validate_response(json.dumps(duplicate), source)

        missing = [item for item in selected if item not in source.employers[1].bullet_ids]
        with self.assertRaisesRegex(GroundingError, "selection omits employer"):
            parse_and_validate_response(json.dumps(make_response(source, selected=missing)), source)

    def test_duplicate_selection_fails_independently(self):
        source = make_source()
        selected = make_response(source)["selected_fragment_ids"]
        excessive = make_response(source, selected=selected + [selected[0]])
        with self.assertRaisesRegex(GroundingError, "duplicate selected fragment"):
            parse_and_validate_response(json.dumps(excessive), source)

    def test_selection_count_limit_fails_for_unique_fragments(self):
        source = parse_source(MANY_SKILLS_SOURCE, "en-US")
        selected = [item.id for item in source.fragments.values() if item.selectable]
        self.assertGreater(len(selected), 25)
        response = make_response(source, selected=selected)
        with self.assertRaisesRegex(GroundingError, "selected fragment count"):
            parse_and_validate_response(json.dumps(response), source)

    def test_total_bullet_limit_fails_independently(self):
        source = parse_source(TOTAL_BULLETS_SOURCE, "en-US")
        selected = [
            item.id
            for item in source.fragments.values()
            if item.selectable and item.id != "experience.acme.bullet.1"
        ]
        self.assertEqual(len(selected), 25)
        response = make_response(source, selected=selected)
        with self.assertRaisesRegex(GroundingError, "more than 16 bullets"):
            parse_and_validate_response(json.dumps(response), source)

    def test_omitted_selectable_evidence_is_reconciled(self):
        source = make_source()
        selected = [item for item in make_response(source)["selected_fragment_ids"] if item != "skills.tools"]
        response = make_response(
            source,
            selected=selected,
            classifications=[
                {"requirement_id": "req-1", "status": "strong", "evidence_ids": ["skills.tools"]},
                {"requirement_id": "req-2", "status": "partial", "evidence_ids": ["skills.tools"]},
            ],
        )
        self.assertNotIn("skills.tools", response["selected_fragment_ids"])
        selection = parse_and_validate_response(json.dumps(response), source)
        self.assertEqual(selection.selected_fragment_ids, tuple(selected) + ("skills.tools",))
        self.assertEqual(selection.strong_matches[0].evidence_ids, ("skills.tools",))
        self.assertEqual(selection.partial_matches[0].evidence_ids, ("skills.tools",))

    def test_reconciled_evidence_exceeding_bullet_limits_fails(self):
        source = parse_source(TOTAL_BULLETS_SOURCE, "en-US")
        base_skills = ["skills.programming-languages", "skills.tools", "skills.spoken-languages"]
        per_employer = [
            "summary.1", "summary.2", *base_skills,
            "experience.acme.bullet.1", "experience.acme.bullet.2", "experience.acme.bullet.3", "experience.acme.bullet.4",
            "experience.beta.bullet.1", "experience.beta.bullet.2", "experience.beta.bullet.3", "experience.beta.bullet.4",
            "experience.gamma.bullet.1", "experience.delta.bullet.1", "experience.epsilon.bullet.1",
        ]
        with self.assertRaisesRegex(GroundingError, "more than 4 bullets"):
            parse_and_validate_response(
                json.dumps(make_response(source, selected=per_employer, classifications=[
                    {"requirement_id": "req-1", "status": "strong", "evidence_ids": ["experience.acme.bullet.5"]},
                    {"requirement_id": "req-2", "status": "gap", "evidence_ids": []},
                ])),
                source,
            )

        total = [
            "summary.1", "summary.2", *base_skills,
            "experience.acme.bullet.1", "experience.acme.bullet.2", "experience.acme.bullet.3", "experience.acme.bullet.4",
            "experience.beta.bullet.1", "experience.beta.bullet.2", "experience.beta.bullet.3", "experience.beta.bullet.4",
            "experience.gamma.bullet.1", "experience.gamma.bullet.2", "experience.gamma.bullet.3", "experience.gamma.bullet.4",
            "experience.delta.bullet.1", "experience.delta.bullet.2", "experience.delta.bullet.3",
            "experience.epsilon.bullet.1",
        ]
        with self.assertRaisesRegex(GroundingError, "more than 16 bullets"):
            parse_and_validate_response(
                json.dumps(make_response(source, selected=total, classifications=[
                    {"requirement_id": "req-1", "status": "strong", "evidence_ids": ["experience.delta.bullet.4"]},
                    {"requirement_id": "req-2", "status": "gap", "evidence_ids": []},
                ])),
                source,
            )

    def test_reconciled_evidence_exceeding_fragment_count_fails(self):
        source = parse_source(MANY_SKILLS_SOURCE, "en-US")
        selected = [
            "summary.1", "summary.2",
            "skills.programming-languages", "skills.spoken-languages",
            *(f"skills.category-{index:02d}" for index in range(1, 19)),
            "experience.acme.bullet.1", "experience.acme.bullet.2", "experience.beta.bullet.1",
        ]
        self.assertEqual(len(selected), 25)
        response = make_response(
            source,
            selected=selected,
            classifications=[
                {"requirement_id": "req-1", "status": "strong", "evidence_ids": ["skills.category-19"]},
                {"requirement_id": "req-2", "status": "gap", "evidence_ids": []},
            ],
        )
        with self.assertRaisesRegex(GroundingError, "selected fragment count"):
            parse_and_validate_response(json.dumps(response), source)

    def test_reconciled_summary_limit_is_rejected(self):
        expanded = SYNTHETIC_SOURCE.replace(
            "Experienced in reliable software delivery.",
            "Experienced in reliable software delivery.\n\nThird summary paragraph.",
        )
        source = parse_source(expanded, "en-US")
        self.assertEqual(len(source.summary_ids), 3)
        selected = [item for item in make_response(source)["selected_fragment_ids"] if item != "summary.3"]
        response = make_response(
            source,
            selected=selected,
            classifications=[
                {"requirement_id": "req-1", "status": "strong", "evidence_ids": ["summary.3"]},
                {"requirement_id": "req-2", "status": "gap", "evidence_ids": []},
            ],
        )
        with self.assertRaisesRegex(GroundingError, "one or two summary paragraphs"):
            parse_and_validate_response(json.dumps(response), source)

    def test_skill_selection_limits_are_enforced(self):
        source = make_source()
        without_spoken = [item for item in make_response(source)["selected_fragment_ids"] if item != "skills.spoken-languages"]
        with self.assertRaisesRegex(GroundingError, "spoken languages and another skill category"):
            parse_and_validate_response(json.dumps(make_response(source, selected=without_spoken)), source)
        single_skill = ["summary.1", "skills.programming-languages", "experience.acme.bullet.1", "experience.beta.bullet.1"]
        with self.assertRaisesRegex(GroundingError, "spoken languages and another skill category"):
            parse_and_validate_response(json.dumps(make_response(source, selected=single_skill)), source)

    def test_mandatory_structural_fragment_is_valid_evidence(self):
        source = make_source()
        response = make_response(
            source,
            requirements=[{"id": "req-1", "text": "Based in Brazil", "priority": "required"}],
            classifications=[
                {"requirement_id": "req-1", "status": "strong", "evidence_ids": ["contact.location"]},
            ],
        )
        response["interview_topics"] = []
        self.assertNotIn("contact.location", response["selected_fragment_ids"])
        selection = parse_and_validate_response(json.dumps(response), source)
        self.assertEqual(selection.strong_matches[0].evidence_ids, ("contact.location",))
        report = render_report(source, selection)
        self.assertIn("Remote | Brazil", report)

    def test_any_known_mandatory_structural_fragment_is_valid_evidence(self):
        source = make_source()
        mandatory = [fragment.id for fragment in source.fragments.values() if fragment.mandatory]
        self.assertIn("identity.name", mandatory)
        self.assertIn("contact.links", mandatory)
        for evidence_id in mandatory:
            with self.subTest(evidence_id=evidence_id):
                self.assertNotIn(evidence_id, [fragment.id for fragment in source.fragments.values() if fragment.selectable])
                response = make_response(
                    source,
                    requirements=[{"id": "req-1", "text": "Vacancy requirement", "priority": "required"}],
                    classifications=[
                        {"requirement_id": "req-1", "status": "partial", "evidence_ids": [evidence_id]},
                    ],
                )
                response["interview_topics"] = []
                selection = parse_and_validate_response(json.dumps(response), source)
                self.assertEqual(selection.partial_matches[0].evidence_ids, (evidence_id,))

    def test_unknown_evidence_id_is_rejected(self):
        source = make_source()
        response = make_response(
            source,
            classifications=[
                {"requirement_id": "req-1", "status": "strong", "evidence_ids": ["experience.missing.bullet.1"]},
                {"requirement_id": "req-2", "status": "gap", "evidence_ids": []},
            ],
        )
        with self.assertRaisesRegex(GroundingError, "unknown evidence ID"):
            parse_and_validate_response(json.dumps(response), source)

    def test_duplicate_evidence_ids_are_rejected(self):
        source = make_source()
        response = make_response(
            source,
            classifications=[
                {"requirement_id": "req-1", "status": "strong", "evidence_ids": ["skills.tools", "skills.tools"]},
                {"requirement_id": "req-2", "status": "gap", "evidence_ids": []},
            ],
        )
        with self.assertRaisesRegex(GroundingError, "invalid or duplicate evidence ID"):
            parse_and_validate_response(json.dumps(response), source)

    def test_gap_with_mandatory_structural_evidence_is_rejected(self):
        source = make_source()
        response = make_response(source, classifications=[
            {"requirement_id": "req-1", "status": "strong", "evidence_ids": ["experience.acme.bullet.1"]},
            {"requirement_id": "req-2", "status": "gap", "evidence_ids": ["contact.location"]},
        ])
        with self.assertRaisesRegex(GroundingError, "gap classification cannot contain evidence"):
            parse_and_validate_response(json.dumps(response), source)

    def test_prompt_distinguishes_selectable_and_mandatory_fragments(self):
        from scripts.tailor_resume import build_prompt

        prompt = build_prompt(make_source(), "https://example.test/job")
        self.assertIn("SELECTABLE SOURCE FRAGMENTS", prompt)
        self.assertIn("MANDATORY STRUCTURAL SOURCE FRAGMENTS", prompt)
        self.assertIn("must\nnever be added to selected_fragment_ids", prompt)
        self.assertIn("known mandatory structural fragment ID", prompt)
        self.assertIn("Before finalizing the response, verify that every", prompt)
        self.assertIn("selectable evidence ID used in a strong or partial classification", prompt)
        self.assertIn("contract violation", prompt)

    def test_prompt_is_coverage_first_and_states_configured_budgets(self):
        from scripts.tailor_resume import build_prompt

        prompt = build_prompt(make_source(), "https://example.test/job")
        self.assertIn("Optimize for evidence coverage instead of the smallest valid selection", prompt)
        self.assertIn("selectable fragment that materially supports at least one vacancy requirement", prompt)
        self.assertIn("target approximately twelve", prompt)
        self.assertIn("to sixteen relevant experience bullets", prompt)
        self.assertIn("Never pad the resume with weak or unrelated fragments", prompt)
        self.assertIn("Select the one or two summary fragments most relevant to the vacancy", prompt)
        self.assertIn("other skill category that materially matches the vacancy", prompt)
        self.assertIn("four bullets per employer", prompt)
        self.assertIn("sixteen bullets", prompt)
        self.assertIn("twenty-five selectable fragments total", prompt)
        self.assertIn("include every selected", prompt)
        self.assertIn("not only its strongest evidence", prompt)
        self.assertNotIn("necessary evidence", prompt)

    def test_mixed_json_and_unknown_free_form_fields_fail(self):
        source = make_source()
        response = make_response(source)
        with self.assertRaisesRegex(GroundingError, "surrounding prose"):
            parse_and_validate_response("Here is the JSON:\n" + json.dumps(response), source)
        response["resume_markdown"] = "- unsupported candidate claim"
        with self.assertRaisesRegex(GroundingError, "unknown:"):
            parse_and_validate_response(json.dumps(response), source)

    def test_one_pure_json_object_and_one_fenced_json_object_pass(self):
        source = make_source()
        response = json.dumps(make_response(source))
        parse_and_validate_response(response, source)
        parse_and_validate_response(f"```json\n{response}\n```", source)

    def test_trailing_prose_multiple_fences_malformed_json_and_array_fail(self):
        source = make_source()
        response = json.dumps(make_response(source))
        invalid_responses = (
            f"```json\n{response}\n```\nTrailing prose",
            f"```json\n{response}\n```\n```json\n{response}\n```",
            response[:-1],
            "[]",
        )
        for invalid in invalid_responses:
            with self.subTest(invalid=invalid[:20]):
                with self.assertRaises(GroundingError):
                    parse_and_validate_response(invalid, source)

    def test_structured_request_payload_is_closed_and_keeps_web_tools(self):
        payload = build_request_body("example/model", "prompt", use_web=True)
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        json_schema = payload["response_format"]["json_schema"]
        self.assertTrue(json_schema["strict"])
        schema = json_schema["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(schema["properties"]["selected_fragment_ids"]["maxItems"], 25)
        self.assertEqual(schema["properties"]["vacancy_requirements"]["maxItems"], 20)
        self.assertEqual(payload["max_tokens"], MAX_RESPONSE_TOKENS)
        self.assertEqual(payload["max_tokens"], 12000)
        self.assertFalse(schema["properties"]["requirement_classifications"]["items"]["additionalProperties"])
        self.assertEqual(
            schema["properties"]["requirement_classifications"]["items"]["properties"]["status"]["enum"],
            ["strong", "partial", "gap"],
        )
        self.assertEqual(payload["provider"], {"require_parameters": True})
        self.assertEqual({tool["type"] for tool in payload["tools"]}, {"openrouter:web_fetch", "openrouter:web_search"})
        self.assertNotIn("stream", payload)

    def test_response_healing_plugin_applies_only_to_anthropic_model(self):
        anthropic = build_request_body("~anthropic/claude-sonnet-latest", "prompt", use_web=True)
        self.assertEqual(anthropic["plugins"], [{"id": "response-healing"}])
        for model in (
            "~google/gemini-flash-latest",
            "~openai/gpt-mini-latest",
            "deepseek/deepseek-chat",
            "openrouter/auto",
        ):
            with self.subTest(model=model):
                self.assertNotIn("plugins", build_request_body(model, "prompt", use_web=True))

    def test_anthropic_payload_keeps_strict_guards_and_web_tools(self):
        payload = build_request_body("~anthropic/claude-sonnet-latest", "prompt", use_web=True)
        self.assertEqual(payload["max_tokens"], 12000)
        self.assertEqual(payload["provider"], {"require_parameters": True})
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        schema = payload["response_format"]["json_schema"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual({tool["type"] for tool in payload["tools"]}, {"openrouter:web_fetch", "openrouter:web_search"})
        self.assertNotIn("stream", payload)

    def test_responses_invalid_after_normalization_are_still_rejected(self):
        source = make_source()
        response = json.dumps(make_response(source))
        with self.assertRaisesRegex(GroundingError, "surrounding prose"):
            parse_and_validate_response(f"{response}\nAdditional prose", source)
        healed_but_unknown = make_response(source)
        healed_but_unknown["resume_markdown"] = "- invented claim"
        with self.assertRaisesRegex(GroundingError, "unknown:"):
            parse_and_validate_response(json.dumps(healed_but_unknown), source)
        healed_but_unknown_id = make_response(source)
        healed_but_unknown_id["selected_fragment_ids"] = list(healed_but_unknown_id["selected_fragment_ids"]) + ["experience.missing.bullet.1"]
        with self.assertRaisesRegex(GroundingError, "unknown selected fragment"):
            parse_and_validate_response(json.dumps(healed_but_unknown_id), source)

    def test_single_classification_list_accepts_strong_partial_and_gap(self):
        source = make_source()
        response = make_response(
            source,
            requirements=[
                {"id": "req-1", "text": "Python automation", "priority": "required"},
                {"id": "req-2", "text": "Swagger documentation", "priority": "preferred"},
                {"id": "req-3", "text": "Accessibility audits", "priority": "context"},
            ],
            classifications=[
                {"requirement_id": "req-1", "status": "strong", "evidence_ids": ["experience.acme.bullet.1"]},
                {"requirement_id": "req-2", "status": "partial", "evidence_ids": ["skills.tools"]},
                {"requirement_id": "req-3", "status": "gap", "evidence_ids": []},
            ],
        )
        selection = parse_and_validate_response(json.dumps(response), source)
        self.assertEqual([item.requirement_id for item in selection.strong_matches], ["req-1"])
        self.assertEqual([item.requirement_id for item in selection.partial_matches], ["req-2"])
        self.assertEqual(list(selection.gaps), ["req-3"])
        manifest = selection.to_manifest()
        self.assertEqual(manifest["classifications"]["req-3"], {"status": "gap", "evidence_ids": []})
        report = render_report(source, selection)
        for heading in ("## Strong matches", "## Partial matches", "## Gaps"):
            self.assertIn(heading, report)

    def test_duplicate_requirement_classification_is_rejected(self):
        source = make_source()
        response = make_response(source)
        response["requirement_classifications"].append(
            {"requirement_id": "req-1", "status": "gap", "evidence_ids": []}
        )
        with self.assertRaisesRegex(GroundingError, "duplicate requirement classification"):
            parse_and_validate_response(json.dumps(response), source)

    def test_missing_requirement_classification_is_rejected(self):
        source = make_source()
        response = make_response(source)
        response["requirement_classifications"] = [response["requirement_classifications"][0]]
        with self.assertRaisesRegex(GroundingError, "must be classified exactly once"):
            parse_and_validate_response(json.dumps(response), source)

    def test_unknown_requirement_classification_is_rejected(self):
        source = make_source()
        response = make_response(source)
        response["requirement_classifications"].append(
            {"requirement_id": "req-9", "status": "gap", "evidence_ids": []}
        )
        with self.assertRaisesRegex(GroundingError, "unknown requirement"):
            parse_and_validate_response(json.dumps(response), source)

    def test_gap_with_evidence_is_rejected(self):
        source = make_source()
        response = make_response(source, classifications=[
            {"requirement_id": "req-1", "status": "strong", "evidence_ids": ["skills.tools"]},
            {"requirement_id": "req-2", "status": "gap", "evidence_ids": ["skills.tools"]},
        ])
        with self.assertRaisesRegex(GroundingError, "gap classification cannot contain evidence"):
            parse_and_validate_response(json.dumps(response), source)

    def test_strong_or_partial_without_evidence_is_rejected(self):
        source = make_source()
        for status in ("strong", "partial"):
            with self.subTest(status=status):
                response = make_response(source, classifications=[
                    {"requirement_id": "req-1", "status": status, "evidence_ids": []},
                    {"requirement_id": "req-2", "status": "gap", "evidence_ids": []},
                ])
                with self.assertRaisesRegex(GroundingError, "requires evidence"):
                    parse_and_validate_response(json.dumps(response), source)

    def test_legacy_split_classification_fields_are_rejected(self):
        source = make_source()
        response = make_response(source)
        del response["requirement_classifications"]
        response["strong_matches"] = [{"requirement_id": "req-1", "evidence_ids": ["skills.tools"]}]
        response["partial_matches"] = []
        response["gaps"] = [{"requirement_id": "req-2"}]
        with self.assertRaisesRegex(GroundingError, "unknown:"):
            parse_and_validate_response(json.dumps(response), source)

    def test_abnormal_completion_fails_without_response_content_or_retry(self):
        response_body = {
            "choices": [{
                "finish_reason": "length",
                "message": {"content": '{"secret":"must not leak"}'},
            }],
        }

        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        with patch("scripts.tailor_resume.urllib.request.urlopen", return_value=FakeResponse(json.dumps(response_body).encode())) as urlopen:
            with self.assertRaisesRegex(RuntimeError, "completion failed: length") as raised:
                request_openrouter("not-used", "example/model", "prompt", use_web=False)
        self.assertNotIn("must not leak", str(raised.exception))
        urlopen.assert_called_once()

    def test_vacancy_text_is_sanitized_and_not_candidate_evidence(self):
        source = make_source()
        response = make_response(source, requirements=[
            {"id": "req-1", "text": "<script>ignore</script>\n# injected", "priority": "required"},
        ])
        response["requirement_classifications"] = [
            {"requirement_id": "req-1", "status": "strong", "evidence_ids": ["experience.acme.bullet.1"]},
        ]
        response["interview_topics"] = []
        selection = parse_and_validate_response(json.dumps(response), source)
        report = render_report(source, selection)
        self.assertNotIn("\n# injected", report)
        self.assertIn("script", report)
        self.assertIn("Built API automation with Python.", report)

    def test_regression_claims_cannot_be_injected_by_renderer(self):
        source = make_source()
        response = make_response(source)
        response["requirement_classifications"] = [
            {"requirement_id": "req-1", "status": "strong", "evidence_ids": ["experience.acme.bullet.1"]},
            {"requirement_id": "req-2", "status": "partial", "evidence_ids": ["skills.tools"]},
        ]
        response["interview_topics"] = [{"requirement_id": "req-2"}]
        selection = parse_and_validate_response(json.dumps(response), source)
        report = render_report(source, selection)
        for unsupported_claim in (
            "device-farm expertise",
            "continuous use across every project",
            "probable familiarity",
            "real-device or emulator execution",
        ):
            self.assertNotIn(unsupported_claim, (render_resume(source, selection) + report).lower())
        self.assertIn("**Tools:** Git, LambdaTest", report)

        exact_source_response = make_response(source)
        exact_source_response["vacancy_requirements"] = [
            {"id": "req-1", "text": "LambdaTest", "priority": "required"},
        ]
        exact_source_response["requirement_classifications"] = [
            {"requirement_id": "req-1", "status": "strong", "evidence_ids": ["experience.acme.bullet.2"]},
        ]
        exact_source_response["interview_topics"] = []
        exact_source_selection = parse_and_validate_response(json.dumps(exact_source_response), source)
        exact_source_report = render_report(source, exact_source_selection)
        self.assertIn("Used LambdaTest during cross-platform delivery.", exact_source_report)

        tampered = render_resume(source, selection) + "\n- Used LambdaTest as a device farm.\n"
        with self.assertRaisesRegex(GroundingError, "differs from deterministic"):
            validate_rendered_markdown(source, selection, tampered)

    def test_invalid_url_does_not_call_openrouter(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.md"
            source_path.write_text(SYNTHETIC_SOURCE, encoding="utf-8")
            argv = [
                "tailor_resume.py",
                "--source",
                str(source_path),
                "--language",
                "en-US",
                "--model",
                "example/model",
            ]
            with patch.object(sys, "argv", argv), patch.dict(os.environ, {"JOB_URL": "not-a-url", "OPENROUTER_API_KEY": "not-used"}, clear=False), patch("scripts.tailor_resume.request_openrouter") as request:
                from scripts.tailor_resume import main

                with self.assertRaisesRegex(ValueError, "valid HTTPS URL"):
                    main()
                request.assert_not_called()


def generated_response():
    return {
        "schema_version": 2,
        "target_company": "Example Systems",
        "target_role": "QA Automation Engineer",
        "vacancy_requirements": [
            {"id": "req-python", "text": "Python automation", "priority": "required"},
            {"id": "req-swagger", "text": "Swagger documentation", "priority": "preferred"},
        ],
        "requirement_classifications": [
            {
                "requirement_id": "req-python",
                "status": "strong",
                "evidence_ids": ["experience.acme.bullet.1", "skills.programming-languages"],
            },
            {"requirement_id": "req-swagger", "status": "gap", "evidence_ids": []},
        ],
        "headline": {
            "text": "Quality Engineering | Python and API Test Automation",
            "source_fragment_ids": ["headline", "skills.programming-languages", "experience.acme.bullet.1"],
            "requirement_ids": ["req-python"],
        },
        "summary": [
            {
                "text": "QA engineer with experience in Python automation and reliable software delivery.",
                "source_fragment_ids": ["summary.1", "summary.2", "skills.programming-languages"],
                "requirement_ids": ["req-python"],
            }
        ],
        "skill_groups": [
            {
                "source_fragment_id": "skills.programming-languages",
                "items": ["Python", "Java"],
                "requirement_ids": ["req-python"],
            },
            {"source_fragment_id": "skills.tools", "items": ["Git", "LambdaTest"]},
            {"source_fragment_id": "skills.spoken-languages", "items": ["English (professional)"]},
        ],
        "experience": [
            {
                "employer": "Acme",
                "bullets": [
                    {
                        "text": "Automated API tests with Python.",
                        "source_fragment_ids": ["experience.acme.bullet.1"],
                        "requirement_ids": ["req-python"],
                    }
                ],
            },
            {
                "employer": "Beta",
                "bullets": [
                    {
                        "text": "Planned test strategies for web applications.",
                        "source_fragment_ids": ["experience.beta.bullet.1"],
                    }
                ],
            },
        ],
        "interview_topics": [{"requirement_id": "req-swagger"}],
    }


def starta_generated_response():
    def bullet(text, citations, requirements=None):
        block = {"text": text, "source_fragment_ids": citations}
        if requirements:
            block["requirement_ids"] = requirements
        return block

    return {
        "schema_version": 2,
        "target_company": "Starta",
        "target_role": "Analista de Qualidade Sênior",
        "vacancy_requirements": [
            {"id": "req-java-automation", "text": "Automação de testes com Java", "priority": "required"},
            {"id": "req-selenium", "text": "Selenium", "priority": "required"},
            {"id": "req-functional-nonfunctional", "text": "Testes funcionais e não funcionais", "priority": "required"},
            {"id": "req-performance", "text": "Testes de performance, carga e estresse", "priority": "required"},
            {"id": "req-jenkins-ci", "text": "Jenkins e integração contínua", "priority": "required"},
            {"id": "req-databases", "text": "Bancos de dados", "priority": "preferred"},
            {"id": "req-api-integration", "text": "Testes de API e integração", "priority": "required"},
            {"id": "req-tdd", "text": "TDD", "priority": "required"},
            {"id": "req-sonar", "text": "Sonar", "priority": "preferred"},
        ],
        "requirement_classifications": [
            {
                "requirement_id": "req-java-automation",
                "status": "strong",
                "evidence_ids": ["experience.trustly.bullet.2", "skills.linguagens-de-programacao"],
            },
            {
                "requirement_id": "req-selenium",
                "status": "strong",
                "evidence_ids": ["skills.automacao-de-testes", "experience.ab-inbev.bullet.2"],
            },
            {
                "requirement_id": "req-functional-nonfunctional",
                "status": "strong",
                "evidence_ids": ["skills.testes", "experience.e-mix.bullet.1"],
            },
            {
                "requirement_id": "req-performance",
                "status": "strong",
                "evidence_ids": ["experience.e-mix.bullet.3", "skills.ferramentas"],
            },
            {
                "requirement_id": "req-jenkins-ci",
                "status": "strong",
                "evidence_ids": ["experience.ci-t.bullet.5", "skills.entrega-e-observabilidade"],
            },
            {
                "requirement_id": "req-databases",
                "status": "strong",
                "evidence_ids": ["experience.dngx.bullet.2", "experience.ci-t.bullet.4"],
            },
            {
                "requirement_id": "req-api-integration",
                "status": "strong",
                "evidence_ids": [
                    "experience.ab-inbev.bullet.3",
                    "experience.ci-t.bullet.2",
                    "experience.e-mix.bullet.2",
                ],
            },
            {"requirement_id": "req-tdd", "status": "gap", "evidence_ids": []},
            {"requirement_id": "req-sonar", "status": "gap", "evidence_ids": []},
        ],
        "headline": {
            "text": "Engenharia de Qualidade | Automação de Testes | Java e Selenium",
            "source_fragment_ids": [
                "headline",
                "summary.1",
                "skills.automacao-de-testes",
                "skills.linguagens-de-programacao",
            ],
            "requirement_ids": ["req-java-automation", "req-selenium"],
        },
        "summary": [
            {
                "text": (
                    "Profissional de engenharia de software com mais de 7 anos de experiência combinada em "
                    "desenvolvimento e qualidade, com automação de testes web, mobile, APIs e acessibilidade."
                ),
                "source_fragment_ids": ["summary.1", "skills.testes"],
                "requirement_ids": ["req-functional-nonfunctional"],
            },
            {
                "text": (
                    "Atuação ao longo do ciclo de desenvolvimento com Java, Python e TypeScript, CI/CD e "
                    "observabilidade para antecipar riscos e aumentar a confiabilidade das entregas."
                ),
                "source_fragment_ids": [
                    "summary.2",
                    "skills.linguagens-de-programacao",
                    "skills.entrega-e-observabilidade",
                ],
                "requirement_ids": ["req-java-automation", "req-jenkins-ci"],
            },
        ],
        "skill_groups": [
            {
                "source_fragment_id": "skills.automacao-de-testes",
                "items": ["Selenium", "Pytest", "Robot Framework", "REST Assured"],
                "requirement_ids": ["req-selenium"],
            },
            {
                "source_fragment_id": "skills.linguagens-de-programacao",
                "items": ["Java", "Python", "SQL"],
            },
            {
                "source_fragment_id": "skills.testes",
                "items": [
                    "Testes de API",
                    "Testes de Integração",
                    "Testes de Performance",
                    "Testes Web",
                    "Testes Mobile",
                ],
                "requirement_ids": ["req-functional-nonfunctional"],
            },
            {
                "source_fragment_id": "skills.ferramentas",
                "items": ["JMeter", "Postman", "Charles Proxy"],
            },
            {
                "source_fragment_id": "skills.entrega-e-observabilidade",
                "items": ["Jenkins", "Azure DevOps", "GitHub Actions"],
            },
            {
                "source_fragment_id": "skills.idiomas",
                "items": ["Português (nativo)", "Inglês (proficiência profissional)"],
            },
        ],
        "experience": [
            {
                "employer": "Trustly",
                "bullets": [
                    bullet(
                        "Execução de estratégias de testes baseadas em risco para jornadas críticas de pagamentos.",
                        ["experience.trustly.bullet.1"],
                    ),
                    bullet(
                        "Desenvolvimento de automações E2E com Java, Selenide e Cucumber para fluxos de frontend.",
                        ["experience.trustly.bullet.2"],
                        ["req-java-automation"],
                    ),
                ],
            },
            {
                "employer": "AB InBev",
                "bullets": [
                    bullet(
                        "Automação de testes E2E com Selenium, Python e Pytest para aplicações mobile.",
                        [
                            "experience.ab-inbev.bullet.2",
                            "experience.ab-inbev.bullet.1",
                            "skills.testes",
                            "skills.automacao-de-testes",
                        ],
                        ["req-selenium", "req-functional-nonfunctional"],
                    ),
                    bullet(
                        "Validação de APIs e investigação de integrações com Charles Proxy e Postman.",
                        ["experience.ab-inbev.bullet.3"],
                        ["req-api-integration"],
                    ),
                    bullet(
                        "Planejamento de estratégias de qualidade para jornadas de comércio web e mobile.",
                        ["experience.ab-inbev.bullet.1"],
                    ),
                ],
            },
            {
                "employer": "CI&T",
                "bullets": [
                    bullet(
                        "Automação de cenários E2E, API e integração com Selenium, Pytest e REST Assured.",
                        ["experience.ci-t.bullet.2"],
                        ["req-api-integration"],
                    ),
                    bullet(
                        "Validação de dados com SQL Server e investigação de incidentes no ELK Stack.",
                        ["experience.ci-t.bullet.4"],
                        ["req-databases"],
                    ),
                    bullet(
                        "Integração contínua de testes com Jenkins e Azure DevOps.",
                        ["experience.ci-t.bullet.5"],
                        ["req-jenkins-ci"],
                    ),
                ],
            },
            {
                "employer": "e.Mix",
                "bullets": [
                    bullet(
                        "Planejamento e execução de testes funcionais e não funcionais para aplicações web e APIs REST.",
                        ["experience.e-mix.bullet.1"],
                        ["req-functional-nonfunctional"],
                    ),
                    bullet(
                        "Automação de cenários de API e E2E com Postman, Newman e Robot Framework.",
                        ["experience.e-mix.bullet.2"],
                        ["req-api-integration"],
                    ),
                    bullet(
                        "Execução de testes de performance, carga e estresse com JMeter em pipelines Azure DevOps.",
                        ["experience.e-mix.bullet.3"],
                        ["req-performance"],
                    ),
                ],
            },
            {
                "employer": "DNGX",
                "bullets": [
                    bullet(
                        "Desenvolvimento de aplicações web e mobile com GeneXus e customização de interfaces.",
                        ["experience.dngx.bullet.1"],
                    ),
                    bullet(
                        "Modelagem de soluções com SQL Server, PostgreSQL e MySQL.",
                        ["experience.dngx.bullet.2"],
                        ["req-databases"],
                    ),
                ],
            },
        ],
        "interview_topics": [{"requirement_id": "req-tdd"}, {"requirement_id": "req-sonar"}],
    }


def make_pt_source() -> object:
    return parse_source(Path("RESUME_pt-BR.md").read_text(encoding="utf-8"), "pt-BR")


class GeneratedV2Tests(unittest.TestCase):
    def _parse(self, response, source=None):
        source = source if source is not None else make_source()
        return source, parse_and_validate_response(json.dumps(response), source)

    def test_v2_adapted_generation_renders_with_provenance_and_warnings(self):
        source, generation = self._parse(generated_response())
        resume = render_resume(source, generation)
        report = render_report(source, generation)
        validate_rendered_markdown(source, generation, resume)
        validate_rendered_report(source, generation, report)
        self.assertIn("Quality Engineering | Python and API Test Automation", resume)
        self.assertIn("QA engineer with experience in Python automation and reliable software delivery.", resume)
        self.assertIn("**Programming Languages:** Python, Java", resume)
        self.assertIn("- Automated API tests with Python.", resume)
        self.assertNotIn("Built API automation with Python.", resume)
        self.assertIn("# Example Candidate", resume)
        self.assertIn("[candidate@example.test](mailto:candidate@example.test)", resume)
        self.assertIn("### QA Engineer | Acme", resume)
        self.assertIn("Jan 2024 - Feb 2024 | Brazil", resume)
        self.assertIn("### Systems Degree | Example University", resume)
        self.assertIn("## Adapted candidate-facing content", report)
        self.assertIn("## Advisory warnings", report)
        self.assertIn("`experience.acme.bullet.1`", report)
        codes = {warning.code for warning in generation.warnings}
        self.assertIn("UNSUPPORTED_CONTENT_WORD", codes)
        self.assertIn("PARAPHRASE_REVIEW", codes)
        self.assertIn("LOW_BULLET_COVERAGE", codes)
        with self.assertRaisesRegex(GroundingError, "differs from deterministic"):
            validate_rendered_markdown(source, generation, resume + "\n- invented claim\n")
        with self.assertRaisesRegex(GroundingError, "differs from deterministic"):
            validate_rendered_report(source, generation, report + "\nCandidate probably knows Swagger.\n")

    def test_v2_headline_is_optional_and_anchors_are_required(self):
        response = generated_response()
        del response["headline"]
        source, generation = self._parse(response)
        self.assertIsNone(generation.headline)
        self.assertIn("Quality Engineering | Test Automation", render_resume(source, generation))

        response = generated_response()
        response["headline"]["source_fragment_ids"] = ["skills.programming-languages"]
        with self.assertRaisesRegex(GroundingError, "must cite the master resume headline fragment"):
            self._parse(response)

        response = generated_response()
        response["summary"][0]["source_fragment_ids"] = ["experience.beta.bullet.1"]
        with self.assertRaisesRegex(GroundingError, "master summary fragment"):
            self._parse(response)

        response = generated_response()
        response["summary"] = []
        with self.assertRaisesRegex(GroundingError, "summary must contain"):
            self._parse(response)

    def test_v2_skill_groups_reorder_subset_and_strict_item_echo(self):
        response = generated_response()
        response["skill_groups"].reverse()
        tools = next(group for group in response["skill_groups"] if group["source_fragment_id"] == "skills.tools")
        tools["items"] = ["LambdaTest", "Git"]
        source, generation = self._parse(response)
        self.assertEqual(generation.skill_groups[-1].label, "Programming Languages")
        resume = render_resume(source, generation)
        self.assertIn("**Tools:** LambdaTest, Git", resume)

        punctuation = generated_response()
        punctuation["skill_groups"][0]["items"] = ["Python  ", "Java"]
        source, generation = self._parse(punctuation)
        resume = render_resume(source, generation)
        self.assertIn("**Programming Languages:** Python, Java", resume)
        self.assertNotIn("Python  ,", resume)

        lowered = generated_response()
        lowered["skill_groups"][0]["items"] = ["python", "Java"]
        with self.assertRaisesRegex(GroundingError, "not an exact source item"):
            self._parse(lowered)

        accented = generated_response()
        accented["skill_groups"][0]["items"] = ["Pythón", "Java"]
        with self.assertRaisesRegex(GroundingError, "not an exact source item"):
            self._parse(accented)

        duplicated = generated_response()
        duplicated["skill_groups"][0]["items"] = ["Python", "Python "]
        with self.assertRaisesRegex(GroundingError, "duplicate skill items"):
            self._parse(duplicated)

        emitted_kind = generated_response()
        emitted_kind["skill_groups"][0]["kind"] = "skill"
        with self.assertRaisesRegex(GroundingError, "unknown: kind"):
            self._parse(emitted_kind)

        emitted_label = generated_response()
        emitted_label["skill_groups"][0]["label"] = "Programming"
        with self.assertRaisesRegex(GroundingError, "unknown: label"):
            self._parse(emitted_label)

        non_skill = generated_response()
        non_skill["skill_groups"][0]["source_fragment_id"] = "summary.1"
        with self.assertRaisesRegex(GroundingError, "not a skill category"):
            self._parse(non_skill)

        missing_spoken = generated_response()
        missing_spoken["skill_groups"] = missing_spoken["skill_groups"][:2]
        with self.assertRaisesRegex(GroundingError, "spoken-language category"):
            self._parse(missing_spoken)

        too_many = generated_response()
        too_many["skill_groups"] = too_many["skill_groups"] + [too_many["skill_groups"][0]]
        with self.assertRaisesRegex(GroundingError, "skill groups must contain 2-3 items"):
            self._parse(too_many)

    def test_v2_employer_evidence_mismatch_fails(self):
        response = generated_response()
        response["experience"][0]["bullets"][0]["source_fragment_ids"] = ["experience.beta.bullet.1"]
        with self.assertRaisesRegex(GroundingError, "owned by Beta, not Acme"):
            self._parse(response)

        response = generated_response()
        response["experience"][1]["employer"] = "Acme"
        with self.assertRaisesRegex(GroundingError, "duplicate experience entry"):
            self._parse(response)

        response = generated_response()
        response["experience"] = response["experience"][:1]
        with self.assertRaisesRegex(GroundingError, "experience omits employers: Beta"):
            self._parse(response)

        response = generated_response()
        response["experience"][0]["bullets"][0]["source_fragment_ids"] = ["summary.1"]
        with self.assertRaisesRegex(GroundingError, "same-employer bullets or skill fragments"):
            self._parse(response)

    def test_v2_unsupported_mobile_word_requires_grounding(self):
        source = make_pt_source()
        response = starta_generated_response()
        bullet = response["experience"][1]["bullets"][0]
        bullet["text"] = "Automação E2E para aplicações mobile."
        bullet["source_fragment_ids"] = [
            "experience.ab-inbev.bullet.2",
            "skills.automacao-de-testes",
        ]
        source, generation = self._parse(response, source)
        warnings = [warning for warning in generation.warnings if "mobile" in warning.message]
        self.assertTrue(warnings, [warning.message for warning in generation.warnings])
        self.assertTrue(all(warning.code == "UNSUPPORTED_CONTENT_WORD" for warning in warnings))

        response = starta_generated_response()
        bullet = response["experience"][1]["bullets"][0]
        bullet["text"] = "Automação E2E para aplicações mobile."
        bullet["source_fragment_ids"] = [
            "experience.ab-inbev.bullet.2",
            "experience.ab-inbev.bullet.1",
            "skills.automacao-de-testes",
        ]
        source, generation = self._parse(response, source)
        self.assertFalse([warning for warning in generation.warnings if "mobile" in warning.message])

        phrased = starta_generated_response()
        phrase_bullet = phrased["experience"][1]["bullets"][0]
        phrase_bullet["text"] = "Execução de Testes Mobile sem suporte declarado."
        phrase_bullet["source_fragment_ids"] = ["experience.ab-inbev.bullet.2"]
        with self.assertRaisesRegex(GroundingError, "skill_label 'Testes' without citing supporting evidence"):
            self._parse(phrased, source)

    def test_v2_unsupported_tool_metric_title_date_and_certification_fail(self):
        response = generated_response()
        response["headline"]["text"] = "Quality Engineering | Modern Kubernetes Test Automation"
        with self.assertRaisesRegex(GroundingError, "Kubernetes"):
            self._parse(response)

        response = generated_response()
        response["experience"][1]["bullets"][0]["text"] = "Planned test strategies for web applications with 40% fewer defects."
        with self.assertRaisesRegex(GroundingError, "metric or date '40"):
            self._parse(response)

        response = generated_response()
        response["experience"][1]["bullets"][0]["text"] = "Acted as Head of Quality."
        with self.assertRaisesRegex(GroundingError, "Head"):
            self._parse(response)

        response = generated_response()
        response["experience"][1]["bullets"][0]["text"] = "Planned test strategies since 2019."
        with self.assertRaisesRegex(GroundingError, "metric or date '2019'"):
            self._parse(response)

        response = generated_response()
        response["experience"][1]["bullets"][0]["text"] = "Holds certification in Quality Engineering."
        with self.assertRaisesRegex(GroundingError, "certification claim"):
            self._parse(response)

        response = generated_response()
        response["experience"][1]["bullets"][0]["text"] = "Maintained AWS Certified Developer credentials."
        with self.assertRaisesRegex(GroundingError, "AWS"):
            self._parse(response)

    def test_v2_missing_provenance_unknown_and_duplicate_ids_fail(self):
        response = generated_response()
        del response["experience"][0]["bullets"][0]["source_fragment_ids"]
        with self.assertRaisesRegex(GroundingError, "missing: source_fragment_ids"):
            self._parse(response)

        response = generated_response()
        response["experience"][0]["bullets"][0]["source_fragment_ids"] = []
        with self.assertRaisesRegex(GroundingError, "at least 1 items"):
            self._parse(response)

        response = generated_response()
        response["experience"][0]["bullets"][0]["source_fragment_ids"] = ["experience.missing.bullet.1"]
        with self.assertRaisesRegex(GroundingError, "unknown source fragment ID"):
            self._parse(response)

        response = generated_response()
        response["experience"][0]["bullets"][0]["source_fragment_ids"] = [
            "experience.acme.bullet.1",
            "experience.acme.bullet.1",
        ]
        with self.assertRaisesRegex(GroundingError, "duplicate values"):
            self._parse(response)

        response = generated_response()
        response["experience"][0]["bullets"][0]["requirement_ids"] = ["req-missing"]
        with self.assertRaisesRegex(GroundingError, "unknown requirement"):
            self._parse(response)

    def test_v2_gap_requirement_terms_and_links_fail_closed(self):
        response = generated_response()
        response["experience"][1]["bullets"][0]["text"] = "Planned test strategies for Swagger documentation."
        with self.assertRaisesRegex(GroundingError, "UNSUPPORTED_REQUIREMENT"):
            self._parse(response)

        response = generated_response()
        response["experience"][1]["bullets"][0]["requirement_ids"] = ["req-swagger"]
        with self.assertRaisesRegex(GroundingError, "still classified as a gap"):
            self._parse(response)

        response = generated_response()
        response["vacancy_requirements"][1] = {"id": "req-git", "text": "Git", "priority": "preferred"}
        response["requirement_classifications"][1] = {
            "requirement_id": "req-git",
            "status": "gap",
            "evidence_ids": [],
        }
        response["interview_topics"] = [{"requirement_id": "req-git"}]
        response["experience"][0]["bullets"][0]["text"] = "Used Git for version control."
        response["experience"][0]["bullets"][0]["source_fragment_ids"] = [
            "experience.acme.bullet.1",
            "skills.tools",
        ]
        with self.assertRaisesRegex(GroundingError, "CLASSIFICATION_CONFLICT"):
            self._parse(response)

        response = generated_response()
        response["vacancy_requirements"][1] = {"id": "req-git", "text": "Git", "priority": "preferred"}
        response["requirement_classifications"][1] = {
            "requirement_id": "req-git",
            "status": "partial",
            "evidence_ids": ["skills.tools"],
        }
        response["interview_topics"] = []
        response["experience"][0]["bullets"][0]["text"] = "Used Git for version control."
        response["experience"][0]["bullets"][0]["source_fragment_ids"] = [
            "experience.acme.bullet.1",
            "skills.tools",
        ]
        self._parse(response)

    def test_v2_classification_evidence_is_bounded(self):
        response = generated_response()
        response["requirement_classifications"][0]["evidence_ids"] = [
            "summary.1",
            "summary.2",
            "skills.tools",
            "skills.programming-languages",
            "skills.spoken-languages",
            "experience.acme.bullet.1",
            "experience.acme.bullet.2",
            "experience.beta.bullet.1",
            "headline",
        ]
        with self.assertRaisesRegex(GroundingError, "at most 8 items"):
            self._parse(response)

        evidence = [
            "summary.1",
            "summary.2",
            "skills.tools",
            "skills.programming-languages",
            "skills.spoken-languages",
            "experience.acme.bullet.1",
            "experience.acme.bullet.2",
            "experience.beta.bullet.1",
        ]
        requirements = [{"id": f"req-{index}", "text": f"Requirement {index}", "priority": "context"} for index in range(1, 9)]
        classifications = [
            {"requirement_id": f"req-{index}", "status": "strong", "evidence_ids": list(evidence)}
            for index in range(1, 9)
        ]
        response = generated_response()
        response["vacancy_requirements"] = requirements
        response["requirement_classifications"] = classifications
        response["interview_topics"] = []
        for block in (
            response["headline"],
            response["summary"][0],
            response["experience"][0]["bullets"][0],
            response["experience"][1]["bullets"][0],
        ):
            block.pop("requirement_ids", None)
        with self.assertRaisesRegex(GroundingError, "exceeds 60 entries"):
            self._parse(response)

    def test_v2_language_contract_and_wrong_language_content(self):
        response = generated_response()
        response["headline"]["text"] = "Quality Engineering | Experiência Profissional"
        with self.assertRaisesRegex(GroundingError, "wrong output language"):
            self._parse(response)

        source = make_pt_source()
        response = starta_generated_response()
        response["experience"][0]["bullets"][0]["text"] = (
            "Planned and executed tests for web applications with the team."
        )
        response["experience"][0]["bullets"][0]["source_fragment_ids"] = ["experience.trustly.bullet.1"]
        source, generation = self._parse(response, source)
        self.assertIn("LANGUAGE_MISMATCH", {warning.code for warning in generation.warnings})

    def test_v2_bullet_limits_and_duplicate_text(self):
        response = generated_response()
        bullets = response["experience"][1]["bullets"]
        bullets.extend([
            {"text": "Reviewed release evidence for web releases.", "source_fragment_ids": ["experience.beta.bullet.1"]},
            {"text": "Documented defects for web releases.", "source_fragment_ids": ["experience.beta.bullet.1"]},
            {"text": "Coordinated defect triage for web releases.", "source_fragment_ids": ["experience.beta.bullet.1"]},
            {"text": "Tracked release readiness for web releases.", "source_fragment_ids": ["experience.beta.bullet.1"]},
        ])
        with self.assertRaisesRegex(GroundingError, "must contain 1-4 bullets"):
            self._parse(response)

        response = generated_response()
        response["experience"][1]["bullets"].append(
            {"text": "Planned test strategies for web applications.", "source_fragment_ids": ["experience.beta.bullet.1"]}
        )
        with self.assertRaisesRegex(GroundingError, "duplicate experience bullet text"):
            self._parse(response)

        source = make_pt_source()
        verbatim = starta_generated_response()
        verbatim["experience"] = []
        for employer in source.employers:
            blocks = [
                {
                    "text": source.fragments[bullet_id].text[2:].strip(),
                    "source_fragment_ids": [bullet_id],
                }
                for bullet_id in employer.bullet_ids[:4]
            ]
            verbatim["experience"].append({"employer": employer.name, "bullets": blocks})
        with self.assertRaisesRegex(GroundingError, "more than 16 bullets"):
            self._parse(verbatim, source)

    def test_v2_limits_are_jointly_satisfiable(self):
        for path, language in (("RESUME_pt-BR.md", "pt-BR"), ("RESUME_en-US.md", "en-US")):
            with self.subTest(language=language):
                source = parse_source(Path(path).read_text(encoding="utf-8"), language)
                self.assertEqual(len(source.skill_ids), 7)
                self.assertLessEqual(
                    MAX_HEADLINE_CHARS
                    + MAX_SUMMARY_BLOCKS * MAX_SUMMARY_CHARS
                    + len(source.skill_ids) * MAX_SKILL_GROUP_CHARS
                    + MAX_TOTAL_BULLETS * MAX_BULLET_CHARS,
                    MAX_TOTAL_CANDIDATE_CHARS,
                )
        response = generated_response()
        response["summary"][0]["text"] = "QA engineer " + "automation " * 80
        with self.assertRaisesRegex(GroundingError, f"exceeds {MAX_SUMMARY_CHARS} characters"):
            self._parse(response)

    def test_v2_numeric_facts_are_boundary_aware(self):
        response = generated_response()
        response["summary"][0]["text"] = "QA engineer with 2 years of Python automation and reliable delivery."
        response["summary"][0]["source_fragment_ids"] = [
            "summary.1",
            "summary.2",
            "skills.programming-languages",
            "experience.acme.date.1",
        ]
        with self.assertRaisesRegex(GroundingError, "metric or date '2"):
            self._parse(response)

        custom_source = parse_source(
            SYNTHETIC_SOURCE.replace(
                "- Planned test strategies for web applications.",
                "- Planned 400 test strategies for web applications.",
            ),
            "en-US",
        )
        supported = generated_response()
        supported["summary"][0]["text"] = "QA engineer with 400 test strategies and reliable delivery."
        supported["summary"][0]["source_fragment_ids"] = [
            "summary.1",
            "summary.2",
            "skills.programming-languages",
            "experience.beta.bullet.1",
        ]
        self._parse(supported, custom_source)

        fabricated = generated_response()
        fabricated["summary"][0]["text"] = "QA engineer with 40% faster delivery."
        fabricated["summary"][0]["source_fragment_ids"] = [
            "summary.1",
            "summary.2",
            "skills.programming-languages",
            "experience.beta.bullet.1",
        ]
        with self.assertRaisesRegex(GroundingError, "metric or date '40"):
            self._parse(fabricated, custom_source)

        pt_source = make_pt_source()
        e2e = starta_generated_response()
        e2e["summary"][1]["text"] = "Atuação com 2 anos de experiência em automação E2E."
        e2e["summary"][1]["source_fragment_ids"] = [
            "summary.2",
            "skills.automacao-de-testes",
            "experience.trustly.bullet.2",
        ]
        with self.assertRaisesRegex(GroundingError, "metric or date '2"):
            self._parse(e2e, pt_source)

        valid = generated_response()
        valid["summary"][0]["text"] = "QA engineer with reliable software delivery since Jan 2024."
        valid["summary"][0]["source_fragment_ids"] = [
            "summary.1",
            "summary.2",
            "skills.programming-languages",
            "experience.acme.date.1",
        ]
        self._parse(valid)

        plus_source = parse_source(
            SYNTHETIC_SOURCE.replace(
                "Experienced in reliable software delivery.",
                "Experienced in 7 years of reliable software delivery.",
            ),
            "en-US",
        )
        plus = generated_response()
        plus["summary"][0]["text"] = "QA engineer with 7+ years of Python automation and reliable delivery."
        plus["summary"][0]["source_fragment_ids"] = [
            "summary.1",
            "summary.2",
            "skills.programming-languages",
        ]
        self._parse(plus, plus_source)

    def test_v2_numeric_unit_context_must_match(self):
        years_source = parse_source(
            SYNTHETIC_SOURCE.replace(
                "Experienced in reliable software delivery.",
                "Experienced in 2 years of reliable software delivery.",
            ),
            "en-US",
        )
        response = generated_response()
        response["summary"][0]["text"] = "QA engineer with 2 defects and reliable delivery."
        response["summary"][0]["source_fragment_ids"] = [
            "summary.1",
            "summary.2",
            "skills.programming-languages",
        ]
        with self.assertRaisesRegex(GroundingError, "metric or date '2 defects'"):
            self._parse(response, years_source)

        months_source = parse_source(
            SYNTHETIC_SOURCE.replace(
                "Experienced in reliable software delivery.",
                "Experienced in 2 months of reliable software delivery.",
            ),
            "en-US",
        )
        response = generated_response()
        response["summary"][0]["text"] = "QA engineer with 2 years of Python automation and reliable delivery."
        response["summary"][0]["source_fragment_ids"] = [
            "summary.1",
            "summary.2",
            "skills.programming-languages",
        ]
        with self.assertRaisesRegex(GroundingError, "metric or date '2 years'"):
            self._parse(response, months_source)

        response = generated_response()
        response["summary"][0]["text"] = "QA engineer with 2 months of Python automation and reliable delivery."
        response["summary"][0]["source_fragment_ids"] = [
            "summary.1",
            "summary.2",
            "skills.programming-languages",
        ]
        self._parse(response, months_source)

        defects_source = parse_source(
            SYNTHETIC_SOURCE.replace(
                "Planned test strategies for web applications.",
                "Planned 2 defects for web applications.",
            ),
            "en-US",
        )
        response = generated_response()
        response["experience"][1]["bullets"][0]["text"] = "Planned 2 defects for web applications."
        response["experience"][1]["bullets"][0]["source_fragment_ids"] = ["experience.beta.bullet.1"]
        self._parse(response, defects_source)

        response = generated_response()
        response["experience"][1]["bullets"][0]["text"] = "Planned 2 tests for web applications."
        response["experience"][1]["bullets"][0]["source_fragment_ids"] = ["experience.beta.bullet.1"]
        with self.assertRaisesRegex(GroundingError, "metric or date '2 tests'"):
            self._parse(response, defects_source)

    def test_v2_percentage_unit_aliases(self):
        percent_source = parse_source(
            SYNTHETIC_SOURCE.replace(
                "Experienced in reliable software delivery.",
                "Improved reliable software delivery by 40% across teams.",
            ),
            "en-US",
        )
        for phrase in ("40 percent", "40 porcento", "40 porcentagem", "40%", "40 pct"):
            with self.subTest(phrase=phrase):
                response = generated_response()
                response["summary"][0]["text"] = (
                    f"QA engineer with {phrase} faster Python automation and reliable delivery."
                )
                response["summary"][0]["source_fragment_ids"] = [
                    "summary.1",
                    "summary.2",
                    "skills.programming-languages",
                ]
                self._parse(response, percent_source)

        years_source = parse_source(
            SYNTHETIC_SOURCE.replace(
                "Experienced in reliable software delivery.",
                "Experienced in 40 years of reliable software delivery.",
            ),
            "en-US",
        )
        response = generated_response()
        response["summary"][0]["text"] = (
            "QA engineer with 40 percent faster Python automation and reliable delivery."
        )
        response["summary"][0]["source_fragment_ids"] = [
            "summary.1",
            "summary.2",
            "skills.programming-languages",
        ]
        with self.assertRaisesRegex(GroundingError, "metric or date '40"):
            self._parse(response, years_source)

    def test_v2_gap_generic_words_stay_usable_and_full_claim_fails(self):
        source = make_pt_source()
        base = starta_generated_response()
        base["vacancy_requirements"].append(
            {"id": "req-unit", "text": "testes unitários", "priority": "required"}
        )
        base["requirement_classifications"].append(
            {"requirement_id": "req-unit", "status": "gap", "evidence_ids": []}
        )
        base["interview_topics"].append({"requirement_id": "req-unit"})
        generation = parse_and_validate_response(json.dumps(base), source)
        self.assertTrue(any("testes" in bullet.text for bullet in generation.all_bullets()))

        claimed = json.loads(json.dumps(base))
        claimed["experience"][0]["bullets"][0]["text"] = (
            "Execução de testes unitários para jornadas críticas de pagamentos."
        )
        with self.assertRaisesRegex(GroundingError, "UNSUPPORTED_REQUIREMENT"):
            parse_and_validate_response(json.dumps(claimed), source)

        grounded = json.loads(json.dumps(base))
        grounded["vacancy_requirements"][-1] = {"id": "req-unit", "text": "Git", "priority": "required"}
        grounded["experience"][0]["bullets"][0]["text"] = (
            "Execução de testes com Git para jornadas críticas de pagamentos."
        )
        grounded["experience"][0]["bullets"][0]["source_fragment_ids"] = [
            "experience.trustly.bullet.1",
            "skills.ferramentas",
        ]
        with self.assertRaisesRegex(GroundingError, "CLASSIFICATION_CONFLICT"):
            parse_and_validate_response(json.dumps(grounded), source)

    def test_v2_strict_skill_echo_rejects_compatibility_forms(self):
        response = generated_response()
        response["skill_groups"][0]["items"] = ["Ｐｙｔｈｏｎ", "Java"]
        with self.assertRaisesRegex(GroundingError, "not an exact source item"):
            self._parse(response)

        response = generated_response()
        response["skill_groups"][0]["items"] = ["Python\u00a0", "Java"]
        source, generation = self._parse(response)
        self.assertIn(
            "**Programming Languages:** Python, Java",
            render_resume(source, generation),
        )

    def test_v2_classification_evidence_is_report_only(self):
        response = generated_response()
        response["requirement_classifications"][0]["evidence_ids"] = [
            "experience.acme.bullet.1",
            "experience.acme.bullet.2",
            "skills.programming-languages",
        ]
        source, generation = self._parse(response)
        self.assertIn("experience.acme.bullet.2", generation.classification_evidence_ids)
        self.assertNotIn("experience.acme.bullet.2", generation.rendered_fragment_ids)
        resume = render_resume(source, generation)
        self.assertNotIn("Used LambdaTest during cross-platform delivery.", resume)
        self.assertIn("Used LambdaTest during cross-platform delivery.", render_report(source, generation))
        manifest = generation.to_manifest()
        self.assertIn("experience.acme.bullet.2", manifest["classification_evidence_ids"])
        self.assertNotIn("experience.acme.bullet.2", manifest["rendered_fragment_ids"])

    def test_v2_manifest_round_trip_and_tamper_rejection(self):
        source, generation = self._parse(generated_response())
        manifest = generation.to_manifest()
        self.assertEqual(manifest["schema_version"], 2)
        self.assertTrue(manifest["review_required"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            write_manifest(path, generation)
            restored = validate_manifest(source, load_manifest(path))
        self.assertEqual(render_resume(source, restored), render_resume(source, generation))
        self.assertEqual(len(restored.warnings), len(generation.warnings))

        tampered = json.loads(json.dumps(manifest))
        tampered["warnings"] = []
        with self.assertRaisesRegex(GroundingError, "warnings are inconsistent"):
            validate_manifest(source, tampered)

        tampered = json.loads(json.dumps(manifest))
        tampered["language"] = "pt-BR"
        with self.assertRaisesRegex(GroundingError, "does not belong"):
            validate_manifest(source, tampered)

        tampered = json.loads(json.dumps(manifest))
        tampered["experience"][0]["bullets"][0]["text"] = "Automated API tests with Kubernetes."
        with self.assertRaisesRegex(GroundingError, "Kubernetes"):
            validate_manifest(source, tampered)

    def test_v2_starta_like_fixture(self):
        source = make_pt_source()
        generation = parse_and_validate_response(json.dumps(starta_generated_response()), source)
        bullets = generation.all_bullets()
        self.assertGreaterEqual(len(bullets), 12)
        self.assertLessEqual(len(bullets), 16)
        self.assertEqual(len(generation.experience), 5)
        self.assertEqual(len(generation.skill_groups), 6)
        self.assertEqual({group.source_fragment_id for group in generation.skill_groups} & {source.spoken_language_id}, {source.spoken_language_id})
        codes = {warning.code for warning in generation.warnings}
        self.assertIn("UNSUPPORTED_CONTENT_WORD", codes)
        self.assertTrue(any("aplicacoes" in warning.message for warning in generation.warnings))
        self.assertEqual(
            codes & {
                "UNSUPPORTED_CLAIM",
                "UNSUPPORTED_PROVENANCE",
                "UNSUPPORTED_REQUIREMENT",
                "CLASSIFICATION_CONFLICT",
            },
            set(),
        )
        resume = render_resume(source, generation)
        report = render_report(source, generation)
        validate_rendered_markdown(source, generation, resume)
        validate_rendered_report(source, generation, report)
        for keyword in ("Selenium", "JMeter", "Jenkins", "SQL Server", "GeneXus", "REST"):
            self.assertIn(keyword, resume)
        self.assertIn("Automação de testes E2E com Selenium, Python e Pytest para aplicações mobile.", resume)
        self.assertIn("## Experiência Profissional", resume)
        self.assertIn("### Pós-graduação Lato Sensu em Cybersecurity", resume)
        self.assertIn("TDD", report)
        self.assertIn("req-tdd", report)
        for block_id in [bullet.block_id for bullet in bullets]:
            self.assertIn(f"`{block_id}`", report)
        manifest = generation.to_manifest()
        restored = validate_manifest(source, json.loads(json.dumps(manifest)))
        self.assertEqual(restored.rendered_fragment_ids, generation.rendered_fragment_ids)

    def test_v2_starta_like_tdd_sonar_claims_fail(self):
        source = make_pt_source()
        response = starta_generated_response()
        response["experience"][0]["bullets"][0]["text"] = (
            "Execução de estratégias de testes com TDD e Sonar para jornadas críticas de pagamentos."
        )
        with self.assertRaisesRegex(GroundingError, "UNSUPPORTED_REQUIREMENT"):
            parse_and_validate_response(json.dumps(response), source)

    def test_v2_missing_employer_entry_and_unknown_employer_fail(self):
        response = generated_response()
        response["experience"][1]["employer"] = "Gamma"
        with self.assertRaisesRegex(GroundingError, "not an exact master employer name"):
            self._parse(response)

    def test_v2_prompt_and_request_schema(self):
        prompt = build_prompt(make_source(), "https://example.test/job", schema_version=2)
        for fragment in (
            "LANGUAGE CONTRACT",
            "PROVENANCE CONTRACT",
            "GAP CONTRACT",
            "BUDGETS",
            "US English",
            "Never translate, re-case, or re-pluralize them.",
            "must cite the master headline fragment",
            "Never cite another employer's bullet.",
            "Do not return a label or kind field.",
            "at most 8 IDs per requirement and at most 60 evidence IDs in total",
        ):
            self.assertIn(fragment, prompt)
        pt_prompt = build_prompt(make_pt_source(), "https://example.test/job", schema_version=2)
        self.assertIn("Brazilian Portuguese", pt_prompt)
        self.assertIn("2 to 7 skill groups", pt_prompt)
        schema = MODEL_RESPONSE_SCHEMA_V2
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]) - {"headline"})
        self.assertEqual(schema["properties"]["summary"]["maxItems"], 2)
        self.assertEqual(schema["properties"]["experience"]["items"]["properties"]["bullets"]["maxItems"], 4)
        self.assertEqual(
            schema["properties"]["requirement_classifications"]["items"]["properties"]["evidence_ids"]["maxItems"],
            8,
        )
        payload = build_request_body("example/model", prompt, use_web=True, schema_version=2)
        self.assertEqual(payload["response_format"]["json_schema"]["name"], "tailored_resume_generation")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertIn("Every generated block must cite", payload["messages"][0]["content"])


class StartaLikeCoverageTests(unittest.TestCase):
    def _skill_id(self, source, label):
        for fragment in source.fragments.values():
            if fragment.kind == "skill" and fragment.text.startswith(f"**{label}:**"):
                return fragment.id
        raise AssertionError(f"missing skill category: {label}")

    def _starta_like_response(self, source):
        employers = {employer.name: employer for employer in source.employers}
        trustly = employers["Trustly"]
        ab_inbev = employers["AB InBev"]
        ci_t = employers["CI&T"]
        e_mix = employers["e.Mix"]
        dngx = employers["DNGX"]
        selected = [
            trustly.bullet_ids[0],
            trustly.bullet_ids[1],
            ab_inbev.bullet_ids[1],
            ab_inbev.bullet_ids[2],
            ci_t.bullet_ids[1],
            ci_t.bullet_ids[3],
            ci_t.bullet_ids[4],
            e_mix.bullet_ids[0],
            e_mix.bullet_ids[1],
            e_mix.bullet_ids[2],
            dngx.bullet_ids[0],
            dngx.bullet_ids[1],
            dngx.bullet_ids[2],
            self._skill_id(source, "Linguagens de Programação"),
            self._skill_id(source, "Automação de Testes"),
            self._skill_id(source, "Testes"),
            self._skill_id(source, "Entrega e Observabilidade"),
            self._skill_id(source, "Ferramentas"),
            source.spoken_language_id,
            source.summary_ids[1],
        ]
        requirements = [
            {"id": "req-java-automation", "text": "Automação de testes com Java", "priority": "required"},
            {"id": "req-selenium", "text": "Selenium", "priority": "required"},
            {"id": "req-functional-nonfunctional", "text": "Testes funcionais e não funcionais", "priority": "required"},
            {"id": "req-performance", "text": "Testes de performance, carga e estresse", "priority": "required"},
            {"id": "req-jenkins-ci", "text": "Jenkins e integração contínua", "priority": "required"},
            {"id": "req-databases-development", "text": "Bancos de dados e desenvolvimento de software", "priority": "preferred"},
            {"id": "req-api-integration", "text": "Testes de API e integração", "priority": "required"},
        ]
        classifications = [
            {"requirement_id": "req-java-automation", "status": "strong", "evidence_ids": [trustly.bullet_ids[1], ci_t.bullet_ids[1]]},
            {"requirement_id": "req-selenium", "status": "strong", "evidence_ids": [ab_inbev.bullet_ids[1], ci_t.bullet_ids[1]]},
            {"requirement_id": "req-functional-nonfunctional", "status": "strong", "evidence_ids": [e_mix.bullet_ids[0], trustly.bullet_ids[0]]},
            {"requirement_id": "req-performance", "status": "strong", "evidence_ids": [e_mix.bullet_ids[2]]},
            {"requirement_id": "req-jenkins-ci", "status": "strong", "evidence_ids": [ci_t.bullet_ids[4], dngx.bullet_ids[2], e_mix.bullet_ids[2]]},
            {"requirement_id": "req-databases-development", "status": "strong", "evidence_ids": [dngx.bullet_ids[0], dngx.bullet_ids[1], ci_t.bullet_ids[3]]},
            {"requirement_id": "req-api-integration", "status": "strong", "evidence_ids": [ab_inbev.bullet_ids[2], e_mix.bullet_ids[1], ci_t.bullet_ids[1]]},
        ]
        return {
            "schema_version": 1,
            "target_company": "Starta",
            "target_role": "Analista de Qualidade Sênior",
            "selected_fragment_ids": selected,
            "vacancy_requirements": requirements,
            "requirement_classifications": classifications,
            "interview_topics": [{"requirement_id": "req-performance"}],
        }

    def test_starta_like_selection_keeps_twelve_to_sixteen_bullets(self):
        source = parse_source(Path("RESUME_pt-BR.md").read_text(encoding="utf-8"), "pt-BR")
        response = self._starta_like_response(source)
        selection = parse_and_validate_response(json.dumps(response), source)
        bullets = [item for item in selection.selected_fragment_ids if source.fragments[item].kind == "bullet"]
        self.assertGreaterEqual(len(bullets), 12)
        self.assertLessEqual(len(bullets), 16)
        self.assertLessEqual(len(selection.selected_fragment_ids), 25)
        for employer in source.employers:
            self.assertTrue(any(item in employer.bullet_ids for item in bullets), employer.name)
        resume = render_resume(source, selection)
        validate_rendered_markdown(source, selection, resume)
        for fragment_id in selection.selected_fragment_ids:
            self.assertIn(plain_markdown(source.fragments[fragment_id].text), plain_markdown(resume))
        for keyword in ("Selenium", "JMeter", "Jenkins", "SQL Server", "GeneXus", "REST"):
            self.assertIn(keyword, resume)


if __name__ == "__main__":
    unittest.main()
