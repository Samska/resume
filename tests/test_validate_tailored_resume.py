import json
import tempfile
import unittest
from pathlib import Path

from scripts.resume_grounding import (
    GroundingError,
    normalize_extracted,
    parse_and_validate_response,
    parse_source,
    render_report,
    render_resume,
)


from tests.test_tailor_resume import SYNTHETIC_SOURCE, make_response


class ManifestValidationTests(unittest.TestCase):
    def test_manifest_round_trip_and_source_mismatch(self):
        source = parse_source(SYNTHETIC_SOURCE, "en-US")
        selection = parse_and_validate_response(json.dumps(make_response(source)), source)
        manifest = selection.to_manifest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            from scripts.resume_grounding import load_manifest, validate_manifest

            restored = validate_manifest(source, load_manifest(path))
            self.assertEqual(restored.source_digest, source.digest)
            changed_source = parse_source(SYNTHETIC_SOURCE.replace("Python", "Java"), "en-US")
            with self.assertRaisesRegex(GroundingError, "does not belong"):
                validate_manifest(changed_source, load_manifest(path))

            malformed = dict(manifest)
            malformed["vacancy_requirements"] = [{"id": [], "text": "bad", "priority": "required"}]
            with self.assertRaisesRegex(GroundingError, "string IDs"):
                validate_manifest(source, malformed)

    def test_report_and_resume_are_rejected_when_tampered(self):
        source = parse_source(SYNTHETIC_SOURCE, "en-US")
        selection = parse_and_validate_response(json.dumps(make_response(source)), source)
        from scripts.resume_grounding import validate_rendered_markdown, validate_rendered_report

        with self.assertRaises(GroundingError):
            validate_rendered_markdown(source, selection, render_resume(source, selection).replace("Git", "Swagger"))
        with self.assertRaises(GroundingError):
            validate_rendered_report(source, selection, render_report(source, selection) + "\nCandidate probably knows Swagger.\n")

    def test_report_has_only_stable_sections_and_pdf_normalization_is_non_fuzzy(self):
        source = parse_source(SYNTHETIC_SOURCE, "en-US")
        selection = parse_and_validate_response(json.dumps(make_response(source)), source)
        report = render_report(source, selection)
        for heading in (
            "## Overall assessment",
            "## Vacancy requirements",
            "## Strong matches",
            "## Partial matches",
            "## Gaps",
            "## Changes made",
            "## Interview points",
            "## Evidence index",
        ):
            self.assertIn(heading, report)
        self.assertEqual(normalize_extracted("Python\n  automation"), "Python automation")
        self.assertNotEqual(normalize_extracted("Python automation"), "Python testing automation")

    def test_workflow_validates_before_upload_and_fails_on_missing_files(self):
        workflow = Path(".github/workflows/generate-tailored-resume.yml").read_text(encoding="utf-8")
        self.assertLess(workflow.index("name: Validate tailored resume"), workflow.index("name: Upload tailored resume"))
        self.assertIn("if-no-files-found: error", workflow)
        self.assertIn("RUNNER_TEMP/tailored-resume-manifest.json", workflow)


if __name__ == "__main__":
    unittest.main()
