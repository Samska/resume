import json
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.tailor_resume import build_request_body, request_openrouter
from scripts.resume_grounding import (
    GroundingError,
    parse_and_validate_response,
    parse_source,
    render_report,
    render_resume,
    validate_rendered_markdown,
    validate_rendered_report,
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


def make_response(source, *, requirements=None, selected=None):
    selectable = [fragment.id for fragment in source.fragments.values() if fragment.selectable]
    selected = selected or selectable
    requirements = requirements or [
        {"id": "req-1", "text": "Python automation", "priority": "required"},
        {"id": "req-2", "text": "Swagger documentation", "priority": "preferred"},
    ]
    bullet_ids = [item for item in selected if source.fragments[item].kind == "bullet"]
    evidence = bullet_ids[:1]
    return {
        "schema_version": 1,
        "target_company": "Example Systems",
        "target_role": "QA Automation Engineer",
        "selected_fragment_ids": selected,
        "vacancy_requirements": requirements,
        "strong_matches": [{"requirement_id": "req-1", "evidence_ids": evidence}],
        "partial_matches": [],
        "gaps": [{"requirement_id": "req-2"}],
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
                response["strong_matches"][0]["evidence_ids"] = [source.employers[0].bullet_ids[0]]
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

    def test_known_but_unselected_evidence_fails(self):
        source = make_source()
        invalid_evidence = make_response(source)
        invalid_evidence["selected_fragment_ids"] = [
            item for item in invalid_evidence["selected_fragment_ids"] if item != "skills.tools"
        ]
        invalid_evidence["strong_matches"][0]["evidence_ids"] = ["skills.tools"]
        with self.assertRaisesRegex(GroundingError, "evidence is not selected"):
            parse_and_validate_response(json.dumps(invalid_evidence), source)

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
        self.assertFalse(schema["properties"]["strong_matches"]["items"]["additionalProperties"])
        self.assertFalse(schema["properties"]["gaps"]["items"]["additionalProperties"])
        self.assertEqual(payload["provider"], {"require_parameters": True})
        self.assertEqual({tool["type"] for tool in payload["tools"]}, {"openrouter:web_fetch", "openrouter:web_search"})
        self.assertNotIn("stream", payload)

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
        response["strong_matches"] = [{"requirement_id": "req-1", "evidence_ids": ["experience.acme.bullet.1"]}]
        response["gaps"] = []
        response["interview_topics"] = []
        selection = parse_and_validate_response(json.dumps(response), source)
        report = render_report(source, selection)
        self.assertNotIn("\n# injected", report)
        self.assertIn("script", report)
        self.assertIn("Built API automation with Python.", report)

    def test_regression_claims_cannot_be_injected_by_renderer(self):
        source = make_source()
        response = make_response(source)
        response["partial_matches"] = [{"requirement_id": "req-2", "evidence_ids": ["skills.tools"]}]
        response["gaps"] = []
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
        exact_source_response["strong_matches"] = [
            {"requirement_id": "req-1", "evidence_ids": ["experience.acme.bullet.2"]},
        ]
        exact_source_response["gaps"] = []
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


if __name__ == "__main__":
    unittest.main()
