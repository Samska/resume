"""Structural validation of the GitHub Actions workflows.

PyYAML is optional: the test is skipped when it is not installed, and the
string-level contract tests in ``test_linkedin_import`` still run everywhere.
"""

import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - optional local dependency
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def load_workflow(name: str) -> dict:
    document = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # YAML 1.1 parses the bare "on:" key as boolean True.
    if True in document:
        document["on"] = document.pop(True)
    return document


@unittest.skipUnless(yaml is not None, "PyYAML is not installed")
class WorkflowYamlTests(unittest.TestCase):
    def test_sync_workflow_structure(self):
        document = load_workflow("sync-linkedin-profile.yml")
        events = document["on"]
        self.assertEqual(events["push"]["branches"], ["main"])
        self.assertEqual(events["push"]["paths"], ["incoming/linkedin-profile.pdf"])
        inputs = events["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"source_branch", "pdf_path", "target_branch"})
        for name, definition in inputs.items():
            with self.subTest(input=name):
                self.assertTrue(definition["required"])
        self.assertNotIn("default", inputs["source_branch"])
        self.assertNotIn("default", inputs["pdf_path"])
        self.assertEqual(inputs["target_branch"]["default"], "main")
        self.assertEqual(
            document["permissions"], {"contents": "write", "pull-requests": "write"}
        )
        job = document["jobs"]["import"]
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        names = [step.get("name") for step in job["steps"]]
        self.assertEqual(names[0], "Validate workflow inputs")
        self.assertIn("Resolve the input PDF", names)
        self.assertIn("Publish output branch and pull request", names)
        self.assertEqual(names[-1], "Clean up temporary files")
        publish = next(
            step for step in job["steps"] if step.get("name") == "Publish output branch and pull request"
        )
        self.assertIn("publish_linkedin_sync.py", publish["run"])
        self.assertIn("set -euo pipefail", publish["run"])

    def test_job_env_does_not_use_step_only_contexts(self):
        document = load_workflow("sync-linkedin-profile.yml")
        for name, value in document["jobs"]["import"]["env"].items():
            with self.subTest(variable=name):
                self.assertNotIn("runner.", str(value))
        report_path = document["jobs"]["import"]["env"]["REPORT_PATH"]
        self.assertTrue(str(report_path).startswith("/tmp/"))

    def test_build_pdf_workflow_is_unchanged_in_shape(self):
        document = load_workflow("build-pdf.yml")
        events = document["on"]
        self.assertEqual(events["push"]["branches"], ["main"])
        self.assertEqual(document["permissions"], {"contents": "write"})
        self.assertEqual(
            [step.get("name") for step in document["jobs"]["build"]["steps"]],
            [
                None,
                "Install PDF tools",
                "Generate PDFs",
                "Validate generated PDFs",
                "Commit updated PDFs",
            ],
        )


if __name__ == "__main__":
    unittest.main()
