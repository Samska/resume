import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.resume_grounding import (
    GroundingError,
    extracted_text_variants,
    normalize_extracted,
    parse_and_validate_response,
    parse_source,
    plain_markdown,
    render_report,
    render_resume,
    write_manifest,
)


from tests.test_tailor_resume import (
    SYNTHETIC_SOURCE,
    generated_response,
    make_pt_source,
    make_response,
    starta_generated_response,
)


class ManifestValidationTests(unittest.TestCase):
    def _write_valid_case(self, directory: str):
        source_path = Path(directory) / "source.md"
        markdown_path = Path(directory) / "resume.md"
        report_path = Path(directory) / "report.md"
        manifest_path = Path(directory) / "manifest.json"
        source_path.write_text(SYNTHETIC_SOURCE, encoding="utf-8")
        source = parse_source(SYNTHETIC_SOURCE, "en-US")
        selection = parse_and_validate_response(json.dumps(make_response(source)), source)
        markdown_path.write_text(render_resume(source, selection), encoding="utf-8")
        report_path.write_text(render_report(source, selection), encoding="utf-8")
        manifest_path.write_text(json.dumps(selection.to_manifest()), encoding="utf-8")
        return source_path, markdown_path, report_path, manifest_path

    def test_content_mode_succeeds_without_pdf_and_skips_pdf_validation(self):
        from scripts import validate_tailored_resume

        with tempfile.TemporaryDirectory() as directory:
            source, markdown, report, manifest = self._write_valid_case(directory)
            argv = [
                "validate_tailored_resume.py",
                "--mode", "content",
                "--markdown", str(markdown),
                "--source", str(source),
                "--report", str(report),
                "--manifest", str(manifest),
                "--language", "en-US",
            ]
            with patch.object(sys, "argv", argv), patch.object(validate_tailored_resume, "validate_pdf") as validate_pdf:
                self.assertEqual(validate_tailored_resume.main(), 0)
                validate_pdf.assert_not_called()

    def test_full_mode_requires_pdf(self):
        from scripts import validate_tailored_resume

        argv = [
            "validate_tailored_resume.py",
            "--mode", "full",
            "--markdown", "resume.md",
            "--source", "source.md",
            "--report", "report.md",
            "--manifest", "manifest.json",
            "--language", "en-US",
        ]
        with patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit):
                validate_tailored_resume.main()

    def test_content_mode_rejects_tampering_before_pdf_validation(self):
        from scripts import validate_tailored_resume

        with tempfile.TemporaryDirectory() as directory:
            source, markdown, report, manifest = self._write_valid_case(directory)
            for path, marker in ((markdown, "unsupported claim"), (report, "Candidate probably knows Swagger.")):
                original = path.read_text(encoding="utf-8")
                path.write_text(original + "\n" + marker + "\n", encoding="utf-8")
                argv = [
                    "validate_tailored_resume.py",
                    "--mode", "content",
                    "--markdown", str(markdown),
                    "--source", str(source),
                    "--report", str(report),
                    "--manifest", str(manifest),
                    "--language", "en-US",
                ]
                with patch.object(sys, "argv", argv), patch.object(validate_tailored_resume, "validate_pdf") as validate_pdf:
                    with self.assertRaises(GroundingError):
                        validate_tailored_resume.main()
                    validate_pdf.assert_not_called()
                path.write_text(original, encoding="utf-8")

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

    def test_manifest_accepts_mandatory_structural_evidence(self):
        source = parse_source(SYNTHETIC_SOURCE, "en-US")
        response = make_response(
            source,
            requirements=[{"id": "req-1", "text": "Location context", "priority": "required"}],
            classifications=[
                {"requirement_id": "req-1", "status": "strong", "evidence_ids": ["contact.location"]},
            ],
        )
        response["interview_topics"] = []
        selection = parse_and_validate_response(json.dumps(response), source)
        manifest = selection.to_manifest()
        self.assertEqual(manifest["classifications"]["req-1"]["evidence_ids"], ["contact.location"])
        from scripts.resume_grounding import load_manifest, validate_manifest

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            restored = validate_manifest(source, load_manifest(path))
        self.assertEqual(restored.strong_matches[0].evidence_ids, ("contact.location",))

    def test_reconciled_evidence_is_rendered_and_persisted(self):
        from scripts.resume_grounding import (
            load_manifest,
            validate_manifest,
            validate_rendered_markdown,
            validate_rendered_report,
        )

        source = parse_source(SYNTHETIC_SOURCE, "en-US")
        selected = [item for item in make_response(source)["selected_fragment_ids"] if item != "skills.tools"]
        response = make_response(
            source,
            selected=selected,
            classifications=[
                {"requirement_id": "req-1", "status": "strong", "evidence_ids": ["skills.tools"]},
                {"requirement_id": "req-2", "status": "gap", "evidence_ids": []},
            ],
        )
        self.assertNotIn("skills.tools", response["selected_fragment_ids"])
        selection = parse_and_validate_response(json.dumps(response), source)
        self.assertIn("skills.tools", selection.selected_fragment_ids)
        resume = render_resume(source, selection)
        report = render_report(source, selection)
        self.assertIn("**Tools:** Git, LambdaTest", resume)
        self.assertIn("**Tools:** Git, LambdaTest", report)
        validate_rendered_markdown(source, selection, resume)
        validate_rendered_report(source, selection, report)
        manifest = selection.to_manifest()
        self.assertIn("skills.tools", manifest["selected_fragment_ids"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            restored = validate_manifest(source, load_manifest(path))
        self.assertEqual(restored.selected_fragment_ids, selection.selected_fragment_ids)
        self.assertEqual(render_resume(source, restored), resume)

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
        ordered_steps = (
            "name: Generate tailored Markdown and match report",
            "name: Validate grounded Markdown and report",
            "name: Generate tailored PDF",
            "name: Validate tailored PDF",
            "name: Verify tailored artifact contents",
            "name: Upload tailored resume",
        )
        positions = [workflow.index(step) for step in ordered_steps]
        self.assertEqual(positions, sorted(positions))
        content_step = workflow[positions[1] : positions[2]]
        full_step = workflow[positions[3] : positions[4]]
        self.assertIn("--mode content", content_step)
        self.assertNotIn("--pdf", content_step)
        self.assertIn("--mode full", full_step)
        self.assertIn("--pdf \"$TAILORED_PDF\"", full_step)
        self.assertIn("if-no-files-found: error", workflow)
        self.assertIn("RUNNER_TEMP/tailored-resume-manifest.json", workflow)
        upload_block = workflow[positions[5] :]
        self.assertEqual(upload_block.count("${{ env."), 3)


class PdfTextNormalizationTests(unittest.TestCase):
    def test_line_wrapping_whitespace_and_nbsp_are_normalized(self):
        self.assertEqual(normalize_extracted("Python\n  automation"), "Python automation")
        self.assertEqual(normalize_extracted("Python\u00a0automation"), "Python automation")
        self.assertEqual(normalize_extracted("Python\fautomation"), "Python automation")

    def test_soft_hyphens_and_zero_width_characters_are_removed(self):
        self.assertEqual(normalize_extracted("soft\u00adhyphen"), "softhyphen")
        self.assertEqual(normalize_extracted("zero\u200bwidth\u200cjoin\ufeffed"), "zerowidthjoined")

    def test_compatibility_ligatures_and_unicode_hyphens_are_folded(self):
        self.assertEqual(normalize_extracted("of\ufb01ce \ufb02ow"), "office flow")
        self.assertEqual(normalize_extracted("non\u2011breaking"), "non-breaking")

    def test_harmless_punctuation_spacing_is_collapsed(self):
        self.assertEqual(normalize_extracted("Testing (WCAG) , Visual"), "Testing (WCAG), Visual")
        self.assertEqual(normalize_extracted("Integration ( WCAG )"), "Integration (WCAG)")

    def test_extracted_variants_cover_both_line_break_hyphen_forms(self):
        self.assertEqual(
            extracted_text_variants("accessibil-\nity testing"),
            ("accessibil-ity testing", "accessibility testing"),
        )
        self.assertEqual(
            extracted_text_variants("cross-\nplatform checks"),
            ("cross-platform checks", "crossplatform checks"),
        )

    def test_variants_collapse_wrapped_lines_without_hyphenation(self):
        self.assertEqual(extracted_text_variants("Python\n  automation"), ("Python automation",))


class GeneratedV2ManifestAndPdfTests(unittest.TestCase):
    def _write_v2_case(self, directory, source_text, language, response):
        source_path = Path(directory) / "source.md"
        markdown_path = Path(directory) / "resume.md"
        report_path = Path(directory) / "report.md"
        manifest_path = Path(directory) / "manifest.json"
        source_path.write_text(source_text, encoding="utf-8")
        source = parse_source(source_text, language)
        generation = parse_and_validate_response(json.dumps(response), source)
        markdown = render_resume(source, generation)
        report = render_report(source, generation)
        markdown_path.write_text(markdown, encoding="utf-8")
        report_path.write_text(report, encoding="utf-8")
        write_manifest(manifest_path, generation)
        return source, generation, source_path, markdown_path, report_path, manifest_path

    def _validate_pdf_text(self, pdf_text, source, selection, pages=2):
        from scripts import validate_tailored_resume

        padded_text = pdf_text + "\n" + ("extracted filler text " * 100)

        def fake_command(*args):
            if args[0] == "pdftotext":
                return padded_text
            if args[0] == "pdfinfo":
                return f"Pages: {pages}\n"
            raise AssertionError(f"unexpected command: {args}")

        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "resume.pdf"
            pdf.write_bytes(b"%PDF-1.4\n" + b"0" * 10_100)
            with patch.object(validate_tailored_resume, "command", side_effect=fake_command):
                return validate_tailored_resume.validate_pdf(pdf, source, selection)

    def test_v2_content_mode_round_trip_and_tampered_generated_text_rejected(self):
        from scripts import validate_tailored_resume

        with tempfile.TemporaryDirectory() as directory:
            source, generation, source_path, markdown_path, report_path, manifest_path = self._write_v2_case(
                directory, SYNTHETIC_SOURCE, "en-US", generated_response()
            )
            markdown = markdown_path.read_text(encoding="utf-8")
            self.assertIn("- Automated API tests with Python.", markdown)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("experience.acme.bullet.1", report)
            argv = [
                "validate_tailored_resume.py",
                "--mode", "content",
                "--markdown", str(markdown_path),
                "--source", str(source_path),
                "--report", str(report_path),
                "--manifest", str(manifest_path),
                "--language", "en-US",
            ]
            with patch.object(sys, "argv", argv), patch.object(validate_tailored_resume, "validate_pdf") as validate_pdf:
                self.assertEqual(validate_tailored_resume.main(), 0)
                validate_pdf.assert_not_called()

            markdown_path.write_text(markdown + "\n- invented claim\n", encoding="utf-8")
            with patch.object(sys, "argv", argv), patch.object(validate_tailored_resume, "validate_pdf") as validate_pdf:
                with self.assertRaisesRegex(GroundingError, "differs from deterministic"):
                    validate_tailored_resume.main()
                validate_pdf.assert_not_called()
            markdown_path.write_text(markdown, encoding="utf-8")

            report_path.write_text(report + "\nCandidate probably knows Swagger.\n", encoding="utf-8")
            with patch.object(sys, "argv", argv), patch.object(validate_tailored_resume, "validate_pdf"):
                with self.assertRaisesRegex(GroundingError, "differs from deterministic"):
                    validate_tailored_resume.main()
            report_path.write_text(report, encoding="utf-8")
            self.assertEqual(generation.schema_version, 2)

    def test_v2_pdf_checks_generated_blocks_and_fixed_facts(self):
        source = parse_source(SYNTHETIC_SOURCE, "en-US")
        generation = parse_and_validate_response(json.dumps(generated_response()), source)
        pdf_text = plain_markdown(render_resume(source, generation))
        results = self._validate_pdf_text(pdf_text, source, generation)
        self.assertTrue(all(passed for passed, _ in results), [message for passed, message in results if not passed])

        bullet_text = generation.all_bullets()[0].text
        pdf_text = plain_markdown(render_resume(source, generation)).replace(bullet_text, "")
        results = self._validate_pdf_text(pdf_text, source, generation)
        failures = [message for passed, message in results if not passed]
        self.assertTrue(any("experience.acme.bullet.1" in message for message in failures), failures)

        pdf_text = plain_markdown(render_resume(source, generation)).replace("Example Candidate", "")
        results = self._validate_pdf_text(pdf_text, source, generation)
        failures = [message for passed, message in results if not passed]
        self.assertTrue(any("identity.name" in message for message in failures), failures)

        results = self._validate_pdf_text(plain_markdown(render_resume(source, generation)), source, generation, pages=3)
        self.assertFalse(results[0][0])

    def test_v2_starta_like_fixture_mocked_pdf_and_manifest(self):
        source = make_pt_source()
        generation = parse_and_validate_response(json.dumps(starta_generated_response()), source)
        resume = render_resume(source, generation)
        report = render_report(source, generation)
        pdf_text = plain_markdown(resume)
        results = self._validate_pdf_text(pdf_text, source, generation)
        self.assertTrue(all(passed for passed, _ in results), [message for passed, message in results if not passed])
        self.assertIn("## Experiência Profissional", resume)
        self.assertIn("### Senior QA Engineer | Trustly", resume)
        self.assertIn("Universidade Cruzeiro do Sul", resume)
        self.assertIn("Selenium, Python e Pytest", report)
        self.assertIn("## Advisory warnings", report)

        removed = generation.all_bullets()[0].text
        results = self._validate_pdf_text(pdf_text.replace(removed, ""), source, generation)
        failures = [message for passed, message in results if not passed]
        self.assertTrue(any("experience.trustly.bullet.1" in message for message in failures), failures)


class PdfProvenanceMatchingTests(unittest.TestCase):
    def _real_selection(self):
        source = parse_source(Path("RESUME_en-US.md").read_text(encoding="utf-8"), "en-US")
        selected = [source.summary_ids[0], *source.skill_ids]
        selected.extend(employer.bullet_ids[0] for employer in source.employers)
        response = make_response(source, selected=selected)
        selection = parse_and_validate_response(json.dumps(response), source)
        return source, selection

    def _validate_pdf_text(self, pdf_text, source, selection):
        from scripts import validate_tailored_resume

        def fake_command(*args):
            if args[0] == "pdftotext":
                return pdf_text
            if args[0] == "pdfinfo":
                return "Pages: 1\n"
            raise AssertionError(f"unexpected command: {args}")

        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "resume.pdf"
            pdf.write_bytes(b"%PDF-1.4\n" + b"0" * 10_100)
            with patch.object(validate_tailored_resume, "command", side_effect=fake_command):
                return validate_tailored_resume.validate_pdf(pdf, source, selection)

    def _extracted_resume_text(self, source, selection):
        return plain_markdown(render_resume(source, selection))

    def test_summary_and_skills_fragments_match_with_hyphenation_artifacts(self):
        source, selection = self._real_selection()
        self.assertIn("summary.1", selection.selected_fragment_ids)
        self.assertIn("skills.testing", selection.selected_fragment_ids)
        pdf_text = self._extracted_resume_text(source, selection)
        for word, artifact in (
            ("accessibility", "accessibil-\nity"),
            ("Accessibility", "Accessibil-\nity"),
            ("Performance", "Perfor-\nmance"),
            ("strategies", "strate-\ngies"),
        ):
            pdf_text = pdf_text.replace(word, artifact)
        results = self._validate_pdf_text(pdf_text, source, selection)
        self.assertTrue(all(passed for passed, _ in results), [message for passed, message in results if not passed])

    def test_absent_selected_fragment_is_still_reported(self):
        source, selection = self._real_selection()
        pdf_text = self._extracted_resume_text(source, selection)
        bullet_id = source.employers[0].bullet_ids[0]
        pdf_text = pdf_text.replace(plain_markdown(source.fragments[bullet_id].text), "")
        results = self._validate_pdf_text(pdf_text, source, selection)
        failures = [message for passed, message in results if not passed]
        self.assertTrue(any(bullet_id in message for message in failures), failures)

    def test_materially_changed_fragment_is_still_reported(self):
        source, selection = self._real_selection()
        pdf_text = self._extracted_resume_text(source, selection)
        bullet_id = source.employers[0].bullet_ids[0]
        bullet_text = plain_markdown(source.fragments[bullet_id].text)
        altered = bullet_text.replace("payment", "banking")
        self.assertNotEqual(altered, bullet_text)
        pdf_text = pdf_text.replace(bullet_text, altered)
        results = self._validate_pdf_text(pdf_text, source, selection)
        failures = [message for passed, message in results if not passed]
        self.assertTrue(any(bullet_id in message for message in failures), failures)

    def test_missing_mandatory_fragment_still_fails(self):
        source, selection = self._real_selection()
        pdf_text = self._extracted_resume_text(source, selection).replace("Samuel Andrade", "")
        results = self._validate_pdf_text(pdf_text, source, selection)
        mandatory = next(passed for passed, message in results if message == "Mandatory source facts are preserved")
        self.assertFalse(mandatory)


if __name__ == "__main__":
    unittest.main()
