import datetime as dt
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from scripts.linkedin_import import (
    IMPORT_PROFILE_SCHEMA,
    LANGUAGES,
    Atom,
    CertificationItemProfile,
    EducationItemProfile,
    ExperienceItemProfile,
    Note,
    Profile,
    ProjectItemProfile,
    SkillItemProfile,
    _validate_translation,
    build_profile_prompt,
    build_request_body,
    detect_source_language,
    extract_pdf_text,
    format_date_span,
    parse_and_validate_profile,
    parse_date_span,
    parse_master_model,
    reconcile_masters,
    render_import_report,
    render_master_model,
    request_openrouter,
    run_import,
    validate_input_pdf,
    validate_master_structure,
    validate_model_configuration,
)
from scripts.resume_source import ResumeImportError

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


SYNTHETIC_EXTRACTED = load_fixture("synthetic_linkedin_en-US.txt")
SYNTHETIC_EXTRACTED_PT = load_fixture("synthetic_linkedin_pt-BR.txt")


def atom(text, evidence=None, translation=None):
    if not text:
        return {"text": "", "evidence": "", "translation": ""}
    resolved_evidence = text if evidence is None else evidence
    resolved_translation = text if translation is None else translation
    return {"text": text, "evidence": resolved_evidence, "translation": resolved_translation}


def profile_response(**overrides):
    data = {
        "schema_version": 1,
        "source_language": "en-US",
        "identity": atom("Example Candidate"),
        "headline": atom(
            "Quality Engineering | Test Automation",
            translation="Engenharia de Qualidade | Automacao de Testes",
        ),
        "location": atom("Remote | Brazil", translation="Remoto | Brasil"),
        "contact": {
            "email": atom("candidate@example.test"),
            "linkedin": atom("linkedin.com/in/example"),
            "github": atom("github.com/example"),
            "phone": atom(""),
        },
        "summary": [
            atom(
                "Quality engineer with experience in Python automation.",
                translation="Engenheiro de qualidade com experiencia em automacao.",
            )
        ],
        "skills": [{**atom("Kubernetes"), "category": "tools"}],
        "spoken_languages": [],
        "experience": [
            {
                "employer": atom("Acme Corp"),
                "title": atom("Senior QA Engineer", translation="Engenheiro de QA Senior"),
                "dates": atom("Jan 2024 - Dec 2024", translation="Jan 2024 - Dez 2024"),
                "location": atom("Remote", translation="Remoto"),
                "description": [
                    atom(
                        "Built API automation with Python.",
                        translation="Construiu automacao de API com Python.",
                    )
                ],
            }
        ],
        "education": [
            {
                "credential": atom("Systems Degree", translation="Tecnologo em Sistemas"),
                "institution": atom("Example University"),
                "dates": atom("2018 - 2020"),
            }
        ],
        "certifications": [],
        "projects": [],
        "warnings": [],
        "conflicts": [],
    }
    data.update(overrides)
    return data


def pt_profile_response(**overrides):
    data = {
        "schema_version": 1,
        "source_language": "pt-BR",
        "identity": atom("Example Candidate"),
        "headline": atom(
            "Engenharia de Qualidade | Automação de Testes",
            translation="Quality Engineering | Test Automation",
        ),
        "location": atom("Remoto | Brasil", translation="Remote | Brazil"),
        "contact": {
            "email": atom("candidate@example.test"),
            "linkedin": atom("linkedin.com/in/example"),
            "github": atom("github.com/example"),
            "phone": atom(""),
        },
        "summary": [
            atom(
                "Engenheiro de qualidade com experiência em automação de testes com Python.",
                translation="Quality engineer with experience in Python test automation.",
            )
        ],
        "skills": [
            {**atom("Python"), "category": "programming_languages"},
            {**atom("Kubernetes"), "category": "tools"},
        ],
        "spoken_languages": [],
        "experience": [
            {
                "employer": atom("Acme Brasil"),
                "title": atom("Engenheiro de QA Sênior", translation="Senior QA Engineer"),
                "dates": atom("Jan 2024 - Dez 2024", translation="Jan 2024 - Dec 2024"),
                "location": atom("Remoto", translation="Remote"),
                "description": [
                    atom(
                        "Desenvolvimento de automação de API com Python.",
                        translation="API automation development with Python.",
                    )
                ],
            }
        ],
        "education": [
            {
                "credential": atom("Tecnólogo em Sistemas", translation="Systems Technologist Degree"),
                "institution": atom("Example University"),
                "dates": atom("2018 - 2020"),
            }
        ],
        "certifications": [],
        "projects": [],
        "warnings": [],
        "conflicts": [],
    }
    data.update(overrides)
    return data


def real_masters():
    return {
        language: parse_master_model(
            (REPO_ROOT / f"RESUME_{language}.md").read_text(encoding="utf-8"), language
        )
        for language in LANGUAGES
    }


def direct_atom(value, translation=None):
    if value is None:
        return Atom(None, None, None)
    return Atom(value, value, value if translation is None else translation)


def make_profile(source_language="en-US", **overrides):
    """Build a profile whose identity values match the real masters.

    The values are read from the master files instead of being duplicated in
    the test source, so no contact information is hardcoded here.
    """

    masters = real_masters()
    english, portuguese = masters["en-US"], masters["pt-BR"]
    data = {
        "identity": direct_atom(english.name_line[2:].strip()),
        "headline": direct_atom(
            english.preamble_lines[0].strip(), portuguese.preamble_lines[0].strip()
        ),
        "location": direct_atom(
            english.preamble_lines[1].strip(), portuguese.preamble_lines[1].strip()
        ),
        "contact": {key: direct_atom(None) for key in ("email", "linkedin", "github", "phone")},
        "summary": (),
        "skills": (),
        "spoken_languages": (),
        "experience": (),
        "education": (),
        "certifications": (),
        "projects": (),
    }
    data.update(overrides)
    return Profile(source_language, **data)


REFERENCE = dt.date(2026, 9, 20)


class InputValidationTests(unittest.TestCase):
    def test_missing_pdf_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.pdf"
            with self.assertRaisesRegex(ResumeImportError, "does not exist"):
                validate_input_pdf(missing)

    def test_wrong_suffix_empty_and_non_pdf_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            wrong_suffix = Path(directory) / "profile.txt"
            wrong_suffix.write_bytes(b"%PDF-1.4\n")
            with self.assertRaisesRegex(ResumeImportError, "not a PDF"):
                validate_input_pdf(wrong_suffix)

            empty = Path(directory) / "empty.pdf"
            empty.write_bytes(b"")
            with self.assertRaisesRegex(ResumeImportError, "empty"):
                validate_input_pdf(empty)

            not_pdf = Path(directory) / "fake.pdf"
            not_pdf.write_bytes(b"this is not a pdf")
            with self.assertRaisesRegex(ResumeImportError, "PDF header"):
                validate_input_pdf(not_pdf)

    def test_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            traversal = Path(directory) / ".." / "linkedin-profile.pdf"
            with self.assertRaisesRegex(ResumeImportError, "traversal"):
                validate_input_pdf(traversal)

    def test_valid_pdf_header_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "linkedin-profile.pdf"
            pdf.write_bytes(b"%PDF-1.4\n% synthetic fixture\n")
            validate_input_pdf(pdf)


class ExtractionTests(unittest.TestCase):
    def _pdf(self, directory):
        pdf = Path(directory) / "linkedin-profile.pdf"
        pdf.write_bytes(b"%PDF-1.4\n% synthetic fixture\n")
        return pdf

    def test_extraction_uses_layout_mode_and_returns_text(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = self._pdf(directory)
            completed = subprocess.CompletedProcess(["pdftotext"], 0, stdout="profile text", stderr="")
            with patch("scripts.linkedin_import.subprocess.run", return_value=completed) as run:
                self.assertEqual(extract_pdf_text(pdf), "profile text")
            command = run.call_args[0][0]
            self.assertEqual(command[:2], ["pdftotext", "-layout"])
            self.assertEqual(command[-1], "-")

    def test_extraction_failure_and_empty_output_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = self._pdf(directory)
            with patch("scripts.linkedin_import.subprocess.run", side_effect=FileNotFoundError):
                with self.assertRaisesRegex(ResumeImportError, "pdftotext is not available"):
                    extract_pdf_text(pdf)
            with patch(
                "scripts.linkedin_import.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "pdftotext"),
            ):
                with self.assertRaisesRegex(ResumeImportError, "could not read"):
                    extract_pdf_text(pdf)
            empty = subprocess.CompletedProcess(["pdftotext"], 0, stdout=" \n ", stderr="")
            with patch("scripts.linkedin_import.subprocess.run", return_value=empty):
                with self.assertRaisesRegex(ResumeImportError, "no extractable text"):
                    extract_pdf_text(pdf)

    def test_oversized_extraction_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = self._pdf(directory)
            huge = subprocess.CompletedProcess(["pdftotext"], 0, stdout="x" * 200_001, stderr="")
            with patch("scripts.linkedin_import.subprocess.run", return_value=huge):
                with self.assertRaisesRegex(ResumeImportError, "larger than the supported limit"):
                    extract_pdf_text(pdf)


class LanguageDetectionTests(unittest.TestCase):
    def test_portuguese_and_english_fixtures_are_detected(self):
        self.assertEqual(detect_source_language(SYNTHETIC_EXTRACTED_PT), "pt-BR")
        self.assertEqual(detect_source_language(SYNTHETIC_EXTRACTED), "en-US")

    def test_ambiguous_text_returns_none(self):
        self.assertIsNone(detect_source_language("Python Java SQL Kubernetes Docker"))

    def test_declared_language_conflicting_with_detection_fails_closed(self):
        with self.assertRaisesRegex(ResumeImportError, "conflicts with the detected"):
            parse_and_validate_profile(json.dumps(profile_response()), SYNTHETIC_EXTRACTED_PT)

    def test_portuguese_profile_parses_with_portuguese_source(self):
        profile = parse_and_validate_profile(json.dumps(pt_profile_response()), SYNTHETIC_EXTRACTED_PT)
        self.assertEqual(profile.source_language, "pt-BR")
        self.assertEqual(profile.detected_language, "pt-BR")
        self.assertEqual(profile.experience[0].employer.text, "Acme Brasil")
        self.assertEqual(profile.experience[0].title.value("en-US", "pt-BR"), "Senior QA Engineer")
        self.assertEqual(profile.experience[0].dates.value("en-US", "pt-BR"), "Jan 2024 - Dec 2024")


class ProfileValidationTests(unittest.TestCase):
    def _parse(self, data):
        return parse_and_validate_profile(json.dumps(data), SYNTHETIC_EXTRACTED)

    def test_valid_profile_parses(self):
        profile = self._parse(profile_response())
        self.assertEqual(profile.source_language, "en-US")
        self.assertEqual(profile.detected_language, "en-US")
        self.assertEqual(profile.identity.text, "Example Candidate")
        self.assertEqual(len(profile.experience), 1)
        self.assertEqual(profile.experience[0].employer.text, "Acme Corp")
        self.assertEqual(len(profile.education), 1)

    def test_missing_and_unknown_fields_fail_closed(self):
        data = profile_response()
        del data["summary"]
        with self.assertRaisesRegex(ResumeImportError, "missing: summary"):
            self._parse(data)
        data = profile_response()
        data["resume_markdown"] = "- invented"
        with self.assertRaisesRegex(ResumeImportError, "unknown: resume_markdown"):
            self._parse(data)
        data = profile_response()
        del data["warnings"]
        with self.assertRaisesRegex(ResumeImportError, "missing: warnings"):
            self._parse(data)
        with self.assertRaisesRegex(ResumeImportError, "invalid JSON"):
            parse_and_validate_profile("{not json}", SYNTHETIC_EXTRACTED)
        with self.assertRaisesRegex(ResumeImportError, "surrounding prose"):
            parse_and_validate_profile("Here it is:\n" + json.dumps(profile_response()), SYNTHETIC_EXTRACTED)

    def test_fenced_json_response_is_accepted(self):
        fenced = "```json\n" + json.dumps(profile_response()) + "\n```"
        profile = parse_and_validate_profile(fenced, SYNTHETIC_EXTRACTED)
        self.assertEqual(profile.identity.text, "Example Candidate")

    def test_evidence_must_be_a_verbatim_quote(self):
        data = profile_response()
        data["identity"]["evidence"] = "This quote does not exist in the PDF"
        with self.assertRaisesRegex(ResumeImportError, "verbatim quote"):
            self._parse(data)

    def test_empty_atom_requires_empty_evidence_and_translation(self):
        data = profile_response()
        data["contact"]["phone"] = {"text": "", "evidence": "Example Candidate", "translation": ""}
        with self.assertRaisesRegex(ResumeImportError, "leave evidence and translation empty"):
            self._parse(data)

    def test_required_atoms_cannot_be_empty(self):
        data = profile_response()
        data["headline"] = {"text": "", "evidence": "", "translation": ""}
        with self.assertRaisesRegex(ResumeImportError, "headline is required"):
            self._parse(data)

    def test_equal_translation_fields_reject_changed_values(self):
        data = profile_response()
        data["experience"][0]["employer"]["translation"] = "Empresa Acme"
        with self.assertRaisesRegex(ResumeImportError, "keep the source value unchanged"):
            self._parse(data)

    def test_translation_cannot_change_numeric_facts(self):
        data = profile_response()
        data["experience"][0]["dates"]["translation"] = "Jan 2024 - Dez 2025"
        with self.assertRaisesRegex(ResumeImportError, "changes numeric facts"):
            self._parse(data)
        data = profile_response()
        data["experience"][0]["dates"]["translation"] = "Jan 2024 - Sep 2024"
        with self.assertRaisesRegex(ResumeImportError, "changes dates"):
            self._parse(data)

    def test_translation_cannot_change_protected_terms(self):
        data = profile_response()
        data["experience"][0]["description"][0]["translation"] = "Construiu automacao com Selenium."
        with self.assertRaisesRegex(ResumeImportError, "protected names or terms"):
            self._parse(data)

    def test_controlled_translation_accepts_localized_months_and_units(self):
        _validate_translation("Sep 2024 - Jan 2026", "Set 2024 - Jan 2026", "field")
        _validate_translation("7+ years of experience", "7+ anos de experiencia", "field")
        _validate_translation(
            "Automação de testes E2E com Java",
            "E2E test automation with Java",
            "field",
        )
        with self.assertRaisesRegex(ResumeImportError, "protected names or terms"):
            _validate_translation(
                "Automação de testes E2E com JMeter",
                "E2E test automation with Selenium",
                "field",
            )

    def test_skill_category_is_validated(self):
        data = profile_response()
        data["skills"][0]["category"] = "invented_category"
        with self.assertRaisesRegex(ResumeImportError, "category is unsupported"):
            self._parse(data)

    def test_notes_are_parsed_and_validated(self):
        data = profile_response()
        data["warnings"] = [{"message": "No phone number in the profile.", "evidence": ""}]
        data["conflicts"] = [
            {"message": "Two end dates for one role.", "evidence": "Jan 2024 - Dec 2024"}
        ]
        profile = self._parse(data)
        self.assertEqual(profile.warnings[0].message, "No phone number in the profile.")
        self.assertIsNone(profile.warnings[0].evidence)
        self.assertEqual(profile.conflicts[0].evidence, "Jan 2024 - Dec 2024")

    def test_notes_fail_closed_on_bad_evidence_and_unknown_keys(self):
        data = profile_response()
        data["warnings"] = [{"message": "Suspicious.", "evidence": "not present in the PDF"}]
        with self.assertRaisesRegex(ResumeImportError, "verbatim quote"):
            self._parse(data)
        data = profile_response()
        data["warnings"] = [{"message": "", "evidence": ""}]
        with self.assertRaisesRegex(ResumeImportError, "message is required"):
            self._parse(data)
        data = profile_response()
        data["conflicts"] = [{"message": "x", "evidence": "", "extra": 1}]
        with self.assertRaisesRegex(ResumeImportError, "unknown: extra"):
            self._parse(data)


class MasterModelTests(unittest.TestCase):
    def test_real_masters_round_trip_byte_for_byte(self):
        for language in LANGUAGES:
            with self.subTest(language=language):
                text = (REPO_ROOT / f"RESUME_{language}.md").read_text(encoding="utf-8")
                model = parse_master_model(text, language)
                self.assertEqual(render_master_model(model), text)
                self.assertEqual(len(model.experience), 5)
                self.assertEqual(len(model.education), 2)

    def test_validate_master_structure_accepts_real_masters(self):
        for language in LANGUAGES:
            with self.subTest(language=language):
                text = (REPO_ROOT / f"RESUME_{language}.md").read_text(encoding="utf-8")
                validate_master_structure(text, language)

    def test_validate_master_structure_rejects_broken_sources(self):
        text = (REPO_ROOT / "RESUME_en-US.md").read_text(encoding="utf-8")
        with self.assertRaisesRegex(ResumeImportError, "sections must be exactly"):
            validate_master_structure(text.replace("## Education", "## Certifications"), "en-US")
        with self.assertRaisesRegex(ResumeImportError, "sections must be exactly"):
            swapped = text.replace("## Professional Summary", "## __TEMP__", 1)
            swapped = swapped.replace("## Technical Skills", "## Professional Summary", 1)
            swapped = swapped.replace("## __TEMP__", "## Technical Skills", 1)
            validate_master_structure(swapped, "en-US")
        with self.assertRaisesRegex(ResumeImportError, "skill line is malformed"):
            validate_master_structure(text.replace("**Tools:** Git", "Tools: Git"), "en-US")
        with self.assertRaisesRegex(ResumeImportError, "raw HTML"):
            validate_master_structure(
                text.replace("## Education", "<div>injected</div>\n\n## Education"), "en-US"
            )
        with self.assertRaisesRegex(ResumeImportError, "fenced code block"):
            validate_master_structure(
                text.replace("## Education", "```\ninjected\n```\n\n## Education"), "en-US"
            )
        with self.assertRaisesRegex(ResumeImportError, "unsupported language"):
            validate_master_structure(text, "fr-FR")

    def test_parse_date_span_present_marker_uses_reference_month(self):
        span = parse_date_span("Jan 2026 - Present", REFERENCE)
        self.assertEqual(span.start, (2026, 1))
        self.assertEqual(span.end, (2026, 9))
        self.assertTrue(span.present)
        year_span = parse_date_span("2018 - 2020", REFERENCE)
        self.assertEqual(year_span.start, (2018, 1))
        self.assertEqual(year_span.end, (2020, 12))
        self.assertIsNone(parse_date_span("sometime", REFERENCE))

    def test_date_spans_format_in_both_languages(self):
        span = parse_date_span("Sep 2024 - Jan 2026", REFERENCE)
        self.assertEqual(span.start, (2024, 9))
        self.assertEqual(span.end, (2026, 1))
        self.assertEqual(format_date_span(span, "en-US"), "Sep 2024 - Jan 2026")
        self.assertEqual(format_date_span(span, "pt-BR"), "Set 2024 - Jan 2026")


class ReconciliationTests(unittest.TestCase):
    def test_no_change_profile_preserves_both_masters_exactly(self):
        masters = real_masters()
        result = reconcile_masters(masters, make_profile(), REFERENCE)
        self.assertEqual(result.applied, ())
        self.assertEqual(result.conflicts, ())
        for language in LANGUAGES:
            self.assertEqual(render_master_model(result.models[language]), masters[language].text)

    def test_conflicting_title_and_dates_are_reported_not_applied(self):
        masters = real_masters()
        profile = make_profile(
            experience=(
                ExperienceItemProfile(
                    direct_atom("Trustly"),
                    direct_atom("Quality Engineer"),
                    direct_atom("Jan 2026 - Sep 2026"),
                    direct_atom("Brazil"),
                    (direct_atom("Risk-based testing."),),
                ),
            )
        )
        result = reconcile_masters(masters, profile, REFERENCE)
        self.assertTrue(any("title differs" in item for item in result.conflicts))
        for language in LANGUAGES:
            self.assertEqual(render_master_model(result.models[language]), masters[language].text)

        profile = make_profile(
            experience=(
                ExperienceItemProfile(
                    direct_atom("Trustly"),
                    direct_atom("Senior QA Engineer"),
                    direct_atom("Jan 2026 - Aug 2026"),
                    direct_atom("Brazil"),
                    (direct_atom("Risk-based testing."),),
                ),
            )
        )
        result = reconcile_masters(masters, profile, REFERENCE)
        self.assertTrue(any("dates differ" in item for item in result.conflicts))
        for language in LANGUAGES:
            self.assertEqual(render_master_model(result.models[language]), masters[language].text)

    def test_conflicting_employer_variant_is_reported_not_duplicated(self):
        masters = real_masters()
        profile = make_profile(
            experience=(
                ExperienceItemProfile(
                    direct_atom("Trustly Inc."),
                    direct_atom("Senior QA Engineer"),
                    direct_atom("Jan 2026 - Sep 2026"),
                    direct_atom("Brazil"),
                    (direct_atom("Risk-based testing."),),
                ),
            )
        )
        result = reconcile_masters(masters, profile, REFERENCE)
        self.assertTrue(any("employer name differs" in item for item in result.conflicts))
        self.assertFalse(result.applied)
        for language in LANGUAGES:
            text = render_master_model(result.models[language])
            self.assertEqual(text, masters[language].text)
            self.assertNotIn("Trustly Inc.", text)

    def test_new_employer_is_added_in_reverse_chronological_order(self):
        profile = make_profile(
            experience=(
                ExperienceItemProfile(
                    direct_atom("NewCo"),
                    direct_atom("QA Engineer", "Engenheiro de QA"),
                    direct_atom("Feb 2024 - Jun 2024"),
                    direct_atom("Remote", "Remoto"),
                    (direct_atom("Built API tests.", "Construiu testes de API."),),
                ),
            )
        )
        result = reconcile_masters(real_masters(), profile, REFERENCE)
        for language in LANGUAGES:
            text = render_master_model(result.models[language])
            validate_master_structure(text, language)
            self.assertIn("NewCo", text)
            positions = [
                text.index(name)
                for name in ("Trustly", "AB InBev", "NewCo", "CI&T", "e.Mix", "DNGX")
            ]
            self.assertEqual(positions, sorted(positions), language)
        english = render_master_model(result.models["en-US"])
        self.assertIn("### QA Engineer | NewCo\nFeb 2024 - Jun 2024 | Remote", english)
        portuguese = render_master_model(result.models["pt-BR"])
        self.assertIn("### Engenheiro de QA | NewCo\nFev 2024 - Jun 2024 | Remoto", portuguese)
        self.assertTrue(any("added employer `NewCo`" in item for item in result.applied))

    def test_new_role_is_added_to_a_multi_role_employer(self):
        profile = make_profile(
            experience=(
                ExperienceItemProfile(
                    direct_atom("CI&T"),
                    direct_atom("QA Lead"),
                    direct_atom("Jan 2021 - Dec 2021"),
                    direct_atom(None),
                    (),
                ),
            )
        )
        result = reconcile_masters(real_masters(), profile, REFERENCE)
        english = render_master_model(result.models["en-US"])
        validate_master_structure(english, "en-US")
        self.assertIn("**QA Lead** | Jan 2021 - Dec 2021", english)
        self.assertLess(
            english.index("**Mid-Level QA Engineer** | Jan 2022 - Dec 2022"),
            english.index("**QA Lead** | Jan 2021 - Dec 2021"),
        )
        self.assertIn("### CI&T", english)
        self.assertTrue(any("added role `QA Lead`" in item for item in result.applied))

    def test_new_role_converts_a_single_role_employer_safely(self):
        profile = make_profile(
            experience=(
                ExperienceItemProfile(
                    direct_atom("Trustly"),
                    direct_atom("QA Tech Lead"),
                    direct_atom("Mar 2023 - Dec 2023"),
                    direct_atom(None),
                    (),
                ),
            )
        )
        result = reconcile_masters(real_masters(), profile, REFERENCE)
        expected = {
            "en-US": ("Brazil", "Defined and executed risk-based test strategies"),
            "pt-BR": ("Brasil", "Definição e execução de estratégias"),
        }
        for language in LANGUAGES:
            text = render_master_model(result.models[language])
            validate_master_structure(text, language)
            location, bullet = expected[language]
            self.assertIn(f"### Trustly\n{location}", text)
            self.assertIn("**Senior QA Engineer**", text)
            self.assertIn(bullet, text)
        english = render_master_model(result.models["en-US"])
        self.assertIn("**QA Tech Lead** | Mar 2023 - Dec 2023", english)
        portuguese = render_master_model(result.models["pt-BR"])
        self.assertIn("**QA Tech Lead** | Mar 2023 - Dez 2023", portuguese)

    def test_education_skill_and_language_additions(self):
        profile = make_profile(
            skills=(SkillItemProfile(direct_atom("Kubernetes"), "tools"),),
            spoken_languages=(
                direct_atom(
                    "German (limited working proficiency)",
                    "Alemão (proficiência limitada)",
                ),
            ),
            education=(
                EducationItemProfile(
                    direct_atom("Master of Testing", "Mestrado em Testes"),
                    direct_atom("Example University"),
                    direct_atom("Jan 2023 - Dec 2024"),
                ),
            ),
        )
        result = reconcile_masters(real_masters(), profile, REFERENCE)
        english = render_master_model(result.models["en-US"])
        portuguese = render_master_model(result.models["pt-BR"])
        validate_master_structure(english, "en-US")
        validate_master_structure(portuguese, "pt-BR")
        self.assertIn("Kubernetes", english)
        self.assertIn("**Tools:**", english)
        self.assertIn("Kubernetes", portuguese)
        self.assertIn("**Ferramentas:**", portuguese)
        self.assertIn("German (limited working proficiency)", english)
        self.assertIn("Alemão (proficiência limitada)", portuguese)
        self.assertIn("### Master of Testing | Example University", english)
        self.assertLess(
            english.index("### Master of Testing | Example University"),
            english.index("### Postgraduate Specialization in Cybersecurity"),
        )
        self.assertEqual(len(parse_master_model(english, "en-US").education), 3)

    def test_unmapped_skill_and_certifications_are_reported(self):
        profile = make_profile(
            skills=(SkillItemProfile(direct_atom("Rust"), "other"),),
            certifications=(
                CertificationItemProfile(
                    direct_atom("Example Certificate"),
                    direct_atom("Example Issuer"),
                    direct_atom(None),
                ),
            ),
            projects=(ProjectItemProfile(direct_atom("Example Project"), ()),),
        )
        masters = real_masters()
        result = reconcile_masters(masters, profile, REFERENCE)
        self.assertTrue(any("could not be mapped" in item for item in result.warnings))
        self.assertTrue(any("certification" in item for item in result.warnings))
        self.assertTrue(any("project" in item for item in result.warnings))
        for language in LANGUAGES:
            self.assertEqual(render_master_model(result.models[language]), masters[language].text)

    def test_missing_dates_are_reported_without_inventing_values(self):
        profile = make_profile(
            experience=(
                ExperienceItemProfile(
                    direct_atom("NewCo"),
                    direct_atom("QA Engineer"),
                    direct_atom(None),
                    direct_atom("Remote"),
                    (direct_atom("Built API tests."),),
                ),
            )
        )
        masters = real_masters()
        result = reconcile_masters(masters, profile, REFERENCE)
        self.assertTrue(any("dates could not be parsed" in item for item in result.warnings))
        self.assertFalse(result.applied)
        for language in LANGUAGES:
            self.assertEqual(render_master_model(result.models[language]), masters[language].text)

    def test_present_marker_is_recorded_and_reported(self):
        profile = make_profile(
            experience=(
                ExperienceItemProfile(
                    direct_atom("NewCo"),
                    direct_atom("QA Engineer"),
                    direct_atom("Jan 2025 - Present"),
                    direct_atom("Remote"),
                    (direct_atom("Built API tests."),),
                ),
            )
        )
        result = reconcile_masters(real_masters(), profile, REFERENCE)
        english = render_master_model(result.models["en-US"])
        portuguese = render_master_model(result.models["pt-BR"])
        self.assertIn("Jan 2025 - Sep 2026", english)
        self.assertIn("Jan 2025 - Set 2026", portuguese)
        self.assertTrue(any("reported as current" in item for item in result.warnings))

    def test_missing_profile_fields_produce_warnings(self):
        empty_contact = {key: direct_atom(None) for key in ("email", "linkedin", "github", "phone")}
        profile = make_profile(
            summary=(),
            skills=(),
            spoken_languages=(),
            experience=(),
            education=(),
            contact=empty_contact,
        )
        masters = real_masters()
        result = reconcile_masters(masters, profile, REFERENCE)
        warnings = " ".join(result.warnings)
        for fragment in (
            "About/summary",
            "experience entries",
            "education entries",
            "skills",
            "email",
            "phone number",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, warnings)
        for language in LANGUAGES:
            self.assertEqual(render_master_model(result.models[language]), masters[language].text)

    def test_model_notes_surface_in_report_lists(self):
        profile = make_profile(
            warnings=(Note("One role has no dates.", None),),
            conflicts=(Note("Two end dates for one role.", "Jan 2024 - Dec 2024"),),
        )
        result = reconcile_masters(real_masters(), profile, REFERENCE)
        self.assertTrue(any("LinkedIn PDF warning: One role has no dates." in item for item in result.warnings))
        self.assertTrue(any("LinkedIn PDF conflict: Two end dates" in item for item in result.conflicts))

    def test_existing_content_absent_from_pdf_is_preserved(self):
        profile = make_profile(
            experience=(
                ExperienceItemProfile(
                    direct_atom("Trustly"),
                    direct_atom("Senior QA Engineer"),
                    direct_atom("Jan 2026 - Sep 2026"),
                    direct_atom("Brazil"),
                    (direct_atom("Risk-based testing."),),
                ),
            )
        )
        masters = real_masters()
        result = reconcile_masters(masters, profile, REFERENCE)
        preserved_bullets = {
            "en-US": "risk-based test strategies",
            "pt-BR": "estratégias de testes baseadas em risco",
        }
        for language in LANGUAGES:
            text = render_master_model(result.models[language])
            for employer in ("AB InBev", "CI&T", "e.Mix", "DNGX"):
                self.assertIn(employer, text)
            self.assertIn(preserved_bullets[language], text)
        english = render_master_model(result.models["en-US"])
        portuguese = render_master_model(result.models["pt-BR"])
        self.assertIn("Postgraduate Specialization in Cybersecurity", english)
        self.assertIn("Pós-graduação Lato Sensu em Cybersecurity", portuguese)

    def test_existing_lines_are_preserved_except_targeted_skill_line(self):
        masters = real_masters()
        profile = make_profile(skills=(SkillItemProfile(direct_atom("Kubernetes"), "tools"),))
        result = reconcile_masters(masters, profile, REFERENCE)
        for language in LANGUAGES:
            before = set(masters[language].text.splitlines())
            after = set(render_master_model(result.models[language]).splitlines())
            changed = before - after
            self.assertTrue(changed, language)
            for line in changed:
                self.assertRegex(line, r"^\*\*[^*]+:\*\*")

    def test_portuguese_source_generates_invariant_facts_in_both_languages(self):
        profile = make_profile(
            source_language="pt-BR",
            experience=(
                ExperienceItemProfile(
                    direct_atom("NewCo"),
                    direct_atom("Engenheiro de QA", "QA Engineer"),
                    direct_atom("Fev 2024 - Jun 2024", "Feb 2024 - Jun 2024"),
                    direct_atom("Remoto", "Remote"),
                    (
                        direct_atom(
                            "Automatizou 500 testes de API.",
                            "Automated 500 API tests.",
                        ),
                    ),
                ),
            ),
            skills=(SkillItemProfile(direct_atom("Kubernetes"), "tools"),),
        )
        result = reconcile_masters(real_masters(), profile, REFERENCE)
        english = render_master_model(result.models["en-US"])
        portuguese = render_master_model(result.models["pt-BR"])
        validate_master_structure(english, "en-US")
        validate_master_structure(portuguese, "pt-BR")
        for text in (english, portuguese):
            self.assertIn("NewCo", text)
            self.assertIn("500", text)
            self.assertIn("Kubernetes", text)
        self.assertIn("### QA Engineer | NewCo\nFeb 2024 - Jun 2024 | Remote", english)
        self.assertIn("### Engenheiro de QA | NewCo\nFev 2024 - Jun 2024 | Remoto", portuguese)
        self.assertIn("Automated 500 API tests.", english)
        self.assertIn("Automatizou 500 testes de API.", portuguese)

    def test_reconciliation_is_deterministic_and_idempotent(self):
        profile = make_profile(
            skills=(SkillItemProfile(direct_atom("Kubernetes"), "tools"),),
            experience=(
                ExperienceItemProfile(
                    direct_atom("NewCo"),
                    direct_atom("QA Engineer", "Engenheiro de QA"),
                    direct_atom("Feb 2024 - Jun 2024"),
                    direct_atom("Remote", "Remoto"),
                    (direct_atom("Built API tests.", "Construiu testes de API."),),
                ),
            ),
        )
        masters = real_masters()
        first = reconcile_masters(masters, profile, REFERENCE)
        second = reconcile_masters(masters, profile, REFERENCE)
        for language in LANGUAGES:
            self.assertEqual(
                render_master_model(first.models[language]),
                render_master_model(second.models[language]),
            )
        third = reconcile_masters(first.models, profile, REFERENCE)
        self.assertEqual(third.applied, ())
        for language in LANGUAGES:
            self.assertEqual(
                render_master_model(third.models[language]),
                render_master_model(first.models[language]),
            )

    def test_output_never_contains_raw_pdf_or_extracted_text(self):
        profile = make_profile(
            experience=(
                ExperienceItemProfile(
                    direct_atom("NewCo"),
                    direct_atom("QA Engineer"),
                    direct_atom("Feb 2024 - Jun 2024"),
                    direct_atom("Remote"),
                    (direct_atom("Built API tests."),),
                ),
            )
        )
        result = reconcile_masters(real_masters(), profile, REFERENCE)
        for language in LANGUAGES:
            text = render_master_model(result.models[language])
            self.assertNotIn("%PDF", text)
            self.assertNotIn("linkedin-profile", text)
            self.assertNotIn("Page 1 of 2", text)
            self.assertNotIn("www.linkedin.com/in/example", text)


class ReportTests(unittest.TestCase):
    def test_report_documents_source_filename_and_pr_statements(self):
        profile = make_profile()
        result = reconcile_masters(real_masters(), profile, REFERENCE)
        report = render_import_report("incoming/linkedin-profile.pdf", profile, False, result)
        for fragment in (
            "# LinkedIn profile import report",
            "incoming/linkedin-profile.pdf",
            "Source language",
            "not included in this pull request",
            "Human review is required",
            "not merged automatically",
            "## Applied updates",
            "## Conflicts requiring manual review",
            "## Missing or unsupported information",
            "## Preserved resume content",
            "## Validation",
            "ATS-friendly structure",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment.lower(), report.lower())

    def test_report_records_detected_language_and_counts(self):
        profile = parse_and_validate_profile(json.dumps(pt_profile_response()), SYNTHETIC_EXTRACTED_PT)
        result = reconcile_masters(real_masters(), profile, REFERENCE)
        report = render_import_report("incoming/linkedin-profile.pdf", profile, True, result)
        self.assertIn("`pt-BR` (deterministic detection: `pt-BR`)", report)
        self.assertIn(f"Applied updates: `{len(result.applied)}`", report)


class RequestTests(unittest.TestCase):
    def test_request_body_is_strict_json_schema(self):
        body = build_request_body("example/model", "prompt")
        self.assertEqual(
            body["response_format"]["json_schema"]["name"], "linkedin_profile_extraction"
        )
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertIs(body["response_format"]["json_schema"]["schema"], IMPORT_PROFILE_SCHEMA)
        self.assertEqual(body["provider"], {"require_parameters": True})
        self.assertEqual(body["temperature"], 0.0)
        self.assertNotIn("tools", body)
        self.assertNotIn("stream", body)
        self.assertFalse(IMPORT_PROFILE_SCHEMA["additionalProperties"])
        self.assertEqual(
            set(IMPORT_PROFILE_SCHEMA["required"]), set(IMPORT_PROFILE_SCHEMA["properties"])
        )
        for key in ("warnings", "conflicts", "source_language"):
            self.assertIn(key, IMPORT_PROFILE_SCHEMA["properties"])

    def test_anthropic_models_get_the_response_healing_plugin(self):
        anthropic = build_request_body("anthropic/claude-3.5-sonnet", "prompt")
        self.assertEqual(anthropic["plugins"], [{"id": "response-healing"}])
        other = build_request_body("example/model", "prompt")
        self.assertNotIn("plugins", other)

    def test_prompt_sends_only_the_extracted_text(self):
        prompt = build_profile_prompt(SYNTHETIC_EXTRACTED)
        self.assertIn("Example Candidate", prompt)
        self.assertIn("EXTRACTED LINKEDIN PDF TEXT", prompt)
        self.assertIn("warnings", prompt)
        self.assertIn("conflicts", prompt)
        self.assertNotIn("samuel.andradetp@live.com", prompt)
        self.assertNotIn("RESUME_en-US.md", prompt)
        self.assertNotIn("Jundiaí, SP, Brazil | Open to remote opportunities", prompt)

    def test_model_configuration_fails_closed(self):
        with self.assertRaisesRegex(ResumeImportError, "model is missing or invalid"):
            validate_model_configuration("not a model")
        with self.assertRaisesRegex(ResumeImportError, "model is missing or invalid"):
            validate_model_configuration("")
        self.assertEqual(validate_model_configuration("openrouter/auto"), "openrouter/auto")

    def test_request_rejects_missing_key_and_invalid_model(self):
        with self.assertRaisesRegex(ResumeImportError, "OPENROUTER_API_KEY"):
            request_openrouter("", "example/model", "prompt")
        with self.assertRaisesRegex(ResumeImportError, "model is missing or invalid"):
            request_openrouter("not-used", "bad model", "prompt")

    def test_completion_failure_does_not_retry_or_leak_content(self):
        response_body = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": '{"secret":"must not leak"}'},
                }
            ]
        }

        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        with patch(
            "scripts.linkedin_import.urllib.request.urlopen",
            return_value=FakeResponse(json.dumps(response_body).encode()),
        ) as urlopen:
            with self.assertRaisesRegex(RuntimeError, "completion failed: length") as raised:
                request_openrouter("not-used", "example/model", "prompt")
        self.assertNotIn("must not leak", str(raised.exception))
        urlopen.assert_called_once()

    def test_http_failure_is_reported_without_retry(self):
        error = urllib.error.HTTPError("https://openrouter.ai", 429, "rate limited", {}, None)
        with patch("scripts.linkedin_import.urllib.request.urlopen", side_effect=error) as urlopen:
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                request_openrouter("not-used", "example/model", "prompt")
        urlopen.assert_called_once()


class RunImportTests(unittest.TestCase):
    def _pdf(self, directory):
        pdf = Path(directory) / "linkedin-profile.pdf"
        pdf.write_bytes(b"%PDF-1.4\n% synthetic fixture\n")
        return pdf

    def _run(self, directory, response, extracted=SYNTHETIC_EXTRACTED, raw=False):
        payload = response if raw else json.dumps(response)
        completed = subprocess.CompletedProcess(["pdftotext"], 0, stdout=extracted, stderr="")
        with patch("scripts.linkedin_import.subprocess.run", return_value=completed):
            return run_import(
                self._pdf(directory),
                model="example/model",
                api_key="not-used",
                reference_date=REFERENCE,
                repo_root=REPO_ROOT,
                request=lambda api_key, model, prompt: payload,
            )

    def test_end_to_end_import_adds_new_facts_and_keeps_existing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            outcome = self._run(directory, profile_response())
        self.assertTrue(outcome.changed)
        english = outcome.texts["en-US"]
        portuguese = outcome.texts["pt-BR"]
        validate_master_structure(english, "en-US")
        validate_master_structure(portuguese, "pt-BR")
        self.assertIn("Acme Corp", english)
        self.assertIn("Acme Corp", portuguese)
        self.assertIn("Kubernetes", english)
        self.assertIn("Kubernetes", portuguese)
        self.assertIn("Defined and executed risk-based test strategies", english)
        self.assertIn("Definição e execução de estratégias", portuguese)
        self.assertNotIn("%PDF", english)
        self.assertNotIn("%PDF", portuguese)
        self.assertNotIn("Page 1 of 2", english)
        self.assertNotIn(SYNTHETIC_EXTRACTED.strip(), portuguese)
        self.assertIn("Master resume changes proposed: `yes`", outcome.report)
        positions = [
            english.index(name)
            for name in ("Trustly", "AB InBev", "Acme Corp", "CI&T", "e.Mix", "DNGX")
        ]
        self.assertEqual(positions, sorted(positions))

    def test_end_to_end_import_from_portuguese_source(self):
        with tempfile.TemporaryDirectory() as directory:
            outcome = self._run(directory, pt_profile_response(), extracted=SYNTHETIC_EXTRACTED_PT)
        self.assertTrue(outcome.changed)
        english = outcome.texts["en-US"]
        portuguese = outcome.texts["pt-BR"]
        self.assertIn("Acme Brasil", english)
        self.assertIn("Acme Brasil", portuguese)
        self.assertIn("Senior QA Engineer", english)
        self.assertIn("Engenheiro de QA Sênior", portuguese)
        self.assertIn("Jan 2024 - Dec 2024", english)
        self.assertIn("Jan 2024 - Dez 2024", portuguese)
        self.assertIn("`pt-BR`", outcome.report)

    def test_end_to_end_import_reports_no_changes_without_touching_masters(self):
        response = profile_response(
            summary=[],
            skills=[],
            spoken_languages=[],
            experience=[],
            education=[],
            certifications=[],
            projects=[],
        )
        with tempfile.TemporaryDirectory() as directory:
            outcome = self._run(directory, response)
        self.assertFalse(outcome.changed)
        for language in LANGUAGES:
            self.assertEqual(
                outcome.texts[language],
                (REPO_ROOT / f"RESUME_{language}.md").read_text(encoding="utf-8"),
            )
        self.assertIn("Master resume changes proposed: `no`", outcome.report)

    def test_model_failure_stops_the_import(self):
        def failing_request(api_key, model, prompt):
            raise RuntimeError("OpenRouter returned HTTP 429.")

        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.CompletedProcess(
                ["pdftotext"], 0, stdout=SYNTHETIC_EXTRACTED, stderr=""
            )
            with patch("scripts.linkedin_import.subprocess.run", return_value=completed):
                with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                    run_import(
                        self._pdf(directory),
                        model="example/model",
                        api_key="not-used",
                        reference_date=REFERENCE,
                        repo_root=REPO_ROOT,
                        request=failing_request,
                    )

    def test_malformed_model_response_stops_the_import(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ResumeImportError, "invalid JSON"):
                self._run(directory, "{not valid json}", raw=True)

    def test_cli_writes_masters_and_sets_the_changed_output(self):
        from scripts import linkedin_import

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for language in LANGUAGES:
                (root / f"RESUME_{language}.md").write_text(
                    (REPO_ROOT / f"RESUME_{language}.md").read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            pdf = root / "linkedin-profile.pdf"
            pdf.write_bytes(b"%PDF-1.4\n% synthetic fixture\n")
            report = root / "linkedin-import-report.md"
            github_output = root / "github-output.txt"
            github_output.write_text("", encoding="utf-8")
            completed = subprocess.CompletedProcess(
                ["pdftotext"], 0, stdout=SYNTHETIC_EXTRACTED, stderr=""
            )
            argv = [
                "linkedin_import.py",
                "--pdf",
                str(pdf),
                "--report",
                str(report),
                "--repo-root",
                str(root),
                "--reference-date",
                "2026-09-20",
            ]
            with (
                patch("scripts.linkedin_import.subprocess.run", return_value=completed),
                patch(
                    "scripts.linkedin_import.request_openrouter",
                    return_value=json.dumps(profile_response()),
                ),
                patch.dict(
                    os.environ,
                    {"OPENROUTER_API_KEY": "not-used", "GITHUB_OUTPUT": str(github_output)},
                    clear=False,
                ),
                patch.object(sys, "argv", argv),
            ):
                self.assertEqual(linkedin_import.main(), 0)
            self.assertEqual(github_output.read_text(encoding="utf-8"), "changed=true\n")
            self.assertIn("Acme Corp", (root / "RESUME_en-US.md").read_text(encoding="utf-8"))
            self.assertIn("Acme Corp", (root / "RESUME_pt-BR.md").read_text(encoding="utf-8"))
            self.assertIn("LinkedIn profile import report", report.read_text(encoding="utf-8"))

    def test_cli_fails_closed_without_api_key_and_leaves_masters_untouched(self):
        from scripts import linkedin_import

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for language in LANGUAGES:
                (root / f"RESUME_{language}.md").write_text(
                    (REPO_ROOT / f"RESUME_{language}.md").read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            pdf = root / "linkedin-profile.pdf"
            pdf.write_bytes(b"%PDF-1.4\n% synthetic fixture\n")
            report = root / "linkedin-import-report.md"
            argv = [
                "linkedin_import.py",
                "--pdf",
                str(pdf),
                "--report",
                str(report),
                "--repo-root",
                str(root),
            ]
            with (
                patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}, clear=False),
                patch.object(sys, "argv", argv),
                self.assertRaisesRegex(ResumeImportError, "OPENROUTER_API_KEY"),
            ):
                linkedin_import.main()
            for language in LANGUAGES:
                self.assertEqual(
                    (root / f"RESUME_{language}.md").read_text(encoding="utf-8"),
                    (REPO_ROOT / f"RESUME_{language}.md").read_text(encoding="utf-8"),
                )
            self.assertFalse(report.exists())

    def test_cli_model_failure_leaves_masters_untouched(self):
        from scripts import linkedin_import

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for language in LANGUAGES:
                (root / f"RESUME_{language}.md").write_text(
                    (REPO_ROOT / f"RESUME_{language}.md").read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            pdf = root / "linkedin-profile.pdf"
            pdf.write_bytes(b"%PDF-1.4\n% synthetic fixture\n")
            report = root / "linkedin-import-report.md"
            completed = subprocess.CompletedProcess(
                ["pdftotext"], 0, stdout=SYNTHETIC_EXTRACTED, stderr=""
            )
            argv = [
                "linkedin_import.py",
                "--pdf",
                str(pdf),
                "--report",
                str(report),
                "--repo-root",
                str(root),
            ]
            with (
                patch("scripts.linkedin_import.subprocess.run", return_value=completed),
                patch(
                    "scripts.linkedin_import.request_openrouter",
                    side_effect=RuntimeError("OpenRouter returned HTTP 500."),
                ),
                patch.dict(os.environ, {"OPENROUTER_API_KEY": "not-used"}, clear=False),
                patch.object(sys, "argv", argv),
                self.assertRaisesRegex(RuntimeError, "HTTP 500"),
            ):
                linkedin_import.main()
            for language in LANGUAGES:
                self.assertEqual(
                    (root / f"RESUME_{language}.md").read_text(encoding="utf-8"),
                    (REPO_ROOT / f"RESUME_{language}.md").read_text(encoding="utf-8"),
                )
            self.assertFalse(report.exists())


class WorkflowContractTests(unittest.TestCase):
    def _workflow(self):
        return (REPO_ROOT / ".github" / "workflows" / "sync-linkedin-profile.yml").read_text(
            encoding="utf-8"
        )

    def test_workflow_triggers_permissions_and_no_auto_merge(self):
        workflow = self._workflow()
        for fragment in (
            "branches: [main]",
            "incoming/linkedin-profile.pdf",
            "workflow_dispatch:",
            "source_branch:",
            "pdf_path:",
            "target_branch:",
            "contents: write",
            "pull-requests: write",
            "set -euo pipefail",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, workflow)
        for forbidden in (
            "gh pr merge",
            "git merge",
            "git push --force origin main",
            "git add -A",
            "|| true",
            "git reset --hard",
            "git checkout --",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, workflow)

    def test_manual_inputs_are_required_and_have_no_implicit_pdf(self):
        workflow = self._workflow()
        dispatch = workflow[workflow.index("workflow_dispatch:") : workflow.index("permissions:")]
        source_block = dispatch[dispatch.index("source_branch:") : dispatch.index("pdf_path:")]
        pdf_block = dispatch[dispatch.index("pdf_path:") : dispatch.index("target_branch:")]
        self.assertIn("required: true", source_block)
        self.assertNotIn("default:", source_block)
        self.assertIn("required: true", pdf_block)
        self.assertNotIn("default:", pdf_block)

    def test_missing_pdf_is_a_successful_noop_for_push_and_a_failure_for_manual(self):
        workflow = self._workflow()
        self.assertIn("available=false", workflow)
        self.assertIn('elif [ "$EVENT_NAME" = "workflow_dispatch" ]', workflow)
        self.assertIn("exit 1", workflow)
        self.assertIn("the workflow exits successfully without processing", workflow)

    def test_workflow_creates_only_the_expected_branch_and_removes_the_pdf(self):
        workflow = self._workflow()
        self.assertIn("sync/linkedin-profile-${{ github.run_id }}", workflow)
        self.assertIn("python3 scripts/publish_linkedin_sync.py", workflow)
        self.assertIn('--base-branch "$TARGET_BRANCH"', workflow)
        self.assertIn('--remove-path "$PDF_PATH"', workflow)
        self.assertIn("poppler-utils", workflow)
        self.assertIn("pdftotext", (REPO_ROOT / "scripts" / "linkedin_import.py").read_text(encoding="utf-8"))

    def test_workflow_uploads_only_the_report_artifact(self):
        workflow = self._workflow()
        artifact_index = workflow.index("uses: actions/upload-artifact")
        next_step = workflow.index("\n      - name:", artifact_index)
        artifact_block = workflow[artifact_index:next_step]
        self.assertIn("linkedin-import-report", artifact_block)
        self.assertNotIn(".pdf", artifact_block)
        self.assertIn("if-no-files-found: error", artifact_block)

    def test_workflow_cleans_up_temporary_files(self):
        workflow = self._workflow()
        cleanup_index = workflow.index("name: Clean up temporary files")
        cleanup_block = workflow[cleanup_index:]
        self.assertIn("if: always()", cleanup_block)
        self.assertIn("rm -rf source", cleanup_block)
        self.assertIn('rm -f "$REPORT_PATH"', cleanup_block)

    def test_readme_documents_the_production_workflow_and_privacy_warning(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for fragment in (
            "incoming/linkedin-profile.pdf",
            "workflow_dispatch",
            "OPENROUTER_API_KEY",
            "OPENROUTER_MODEL",
            "public",
            "Git history",
            "not merged automatically",
            "sync-linkedin-profile",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, readme)


if __name__ == "__main__":
    unittest.main()
