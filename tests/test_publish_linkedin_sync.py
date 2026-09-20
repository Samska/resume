import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.publish_linkedin_sync import (
    ALLOWED_FILES,
    PublishError,
    publish,
    validate_output_branch,
    validate_relative_path,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=check
    )


class PublishTestBase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.remote = self.root / "remote.git"
        git(self.repo, "init")
        git(self.repo, "symbolic-ref", "HEAD", "refs/heads/main")
        git(self.repo, "config", "user.name", "Test Bot")
        git(self.repo, "config", "user.email", "bot@example.test")
        for name in ALLOWED_FILES:
            (self.repo / name).write_text(f"# Synthetic\n\n{name} master\n", encoding="utf-8")
        self.pdf = self.repo / "incoming" / "linkedin-profile.pdf"
        self.pdf.parent.mkdir()
        self.pdf.write_bytes(b"%PDF-1.4\n% synthetic fixture\n")
        git(self.repo, "add", "--", *ALLOWED_FILES, "incoming/linkedin-profile.pdf")
        git(self.repo, "commit", "-m", "initial")
        subprocess.run(
            ["git", "init", "--bare", str(self.remote)], check=True, capture_output=True
        )
        git(self.repo, "remote", "add", "origin", str(self.remote).replace("\\", "/"))

    def tearDown(self):
        self._temp.cleanup()

    def prepare(self, marker="updated"):
        (self.repo / "RESUME_en-US.md").write_text(
            f"# Synthetic\n\n{marker} English master\n", encoding="utf-8"
        )
        report = self.repo / "report.md"
        report.write_text("# LinkedIn profile import report\n", encoding="utf-8")
        return report

    def remote_tree(self, branch):
        git(self.repo, "fetch", "--quiet", "origin", branch)
        return git(self.repo, "ls-tree", "-r", "--name-only", "FETCH_HEAD").stdout.split()


class PublishTests(PublishTestBase):
    def test_dry_run_creates_generated_branch_and_excludes_pdf(self):
        report = self.prepare()
        publish(
            self.repo,
            "main",
            "sync/linkedin-profile-1",
            report,
            "incoming/linkedin-profile.pdf",
            dry_run=True,
        )
        self.assertEqual(
            git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
            "sync/linkedin-profile-1",
        )
        tree = git(self.repo, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
        self.assertIn("RESUME_en-US.md", tree)
        self.assertIn("RESUME_pt-BR.md", tree)
        self.assertNotIn("incoming/linkedin-profile.pdf", tree)
        committed = git(
            self.repo, "show", "--name-only", "--pretty=format:", "HEAD"
        ).stdout.split()
        self.assertEqual(
            sorted(committed), ["RESUME_en-US.md", "incoming/linkedin-profile.pdf"]
        )
        missing = git(
            self.repo, "ls-remote", "--exit-code", "--heads", "origin", "sync/linkedin-profile-1",
            check=False,
        )
        self.assertNotEqual(missing.returncode, 0)

    def test_publish_pushes_branch_and_opens_pull_request(self):
        report = self.prepare()
        with patch(
            "scripts.publish_linkedin_sync.create_or_update_pull_request"
        ) as create_pr:
            publish(
                self.repo,
                "main",
                "sync/linkedin-profile-2",
                report,
                "incoming/linkedin-profile.pdf",
            )
        create_pr.assert_called_once()
        args = create_pr.call_args
        self.assertEqual(args.args[1], "sync/linkedin-profile-2")
        self.assertEqual(args.args[2], "main")
        tree = self.remote_tree("sync/linkedin-profile-2")
        self.assertIn("RESUME_en-US.md", tree)
        self.assertIn("RESUME_pt-BR.md", tree)
        self.assertNotIn("incoming/linkedin-profile.pdf", tree)

    def test_remote_branch_is_refreshed_on_rerun(self):
        report = self.prepare("first")
        with patch("scripts.publish_linkedin_sync.create_or_update_pull_request"):
            publish(
                self.repo, "main", "sync/linkedin-profile-3", report,
                "incoming/linkedin-profile.pdf",
            )
        self.pdf.unlink()
        git(self.repo, "checkout", "main")
        git(self.repo, "branch", "-D", "sync/linkedin-profile-3")
        report = self.prepare("second")
        with patch("scripts.publish_linkedin_sync.create_or_update_pull_request"):
            publish(
                self.repo, "main", "sync/linkedin-profile-3", report,
                "incoming/linkedin-profile.pdf",
            )
        git(self.repo, "fetch", "--quiet", "origin", "sync/linkedin-profile-3")
        content = git(self.repo, "show", "FETCH_HEAD:RESUME_en-US.md").stdout
        self.assertIn("second English master", content)

    def test_unexpected_staged_files_fail_closed(self):
        report = self.prepare()
        extra = self.repo / "notes.txt"
        extra.write_text("unexpected\n", encoding="utf-8")
        git(self.repo, "add", "notes.txt")
        with self.assertRaisesRegex(PublishError, "unexpected staged files"):
            publish(
                self.repo, "main", "sync/linkedin-profile-4", report,
                "incoming/linkedin-profile.pdf",
            )

    def test_pdf_payload_in_staged_resume_fails_closed(self):
        report = self.prepare()
        (self.repo / "RESUME_pt-BR.md").write_text(
            "# Synthetic\n\n%PDF-1.4 raw payload\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(PublishError, "raw PDF data"):
            publish(
                self.repo, "main", "sync/linkedin-profile-5", report,
                "incoming/linkedin-profile.pdf",
            )

    def test_pr_failure_propagates(self):
        report = self.prepare()
        with patch(
            "scripts.publish_linkedin_sync.create_or_update_pull_request",
            side_effect=PublishError("pull request publishing failed: boom"),
        ):
            with self.assertRaisesRegex(PublishError, "boom"):
                publish(
                    self.repo, "main", "sync/linkedin-profile-6", report,
                    "incoming/linkedin-profile.pdf",
                )

    def test_missing_pdf_tracking_is_not_an_error(self):
        git(self.repo, "rm", "--cached", "--quiet", "--", "incoming/linkedin-profile.pdf")
        git(self.repo, "commit", "-m", "remove pdf")
        report = self.prepare()
        publish(
            self.repo, "main", "sync/linkedin-profile-7", report,
            "incoming/linkedin-profile.pdf", dry_run=True,
        )
        tree = git(self.repo, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
        self.assertNotIn("incoming/linkedin-profile.pdf", tree)

    def test_missing_report_fails_closed(self):
        with self.assertRaisesRegex(PublishError, "report file does not exist"):
            publish(
                self.repo, "main", "sync/linkedin-profile-8",
                self.repo / "missing-report.md", None, dry_run=True,
            )

    def test_output_branch_must_be_generated_and_differ_from_base(self):
        with self.assertRaisesRegex(PublishError, "must differ"):
            validate_output_branch("main", "main")
        with self.assertRaisesRegex(PublishError, "must start with"):
            validate_output_branch("feature/manual", "main")
        self.assertEqual(
            validate_output_branch("sync/linkedin-profile-9", "main"),
            "sync/linkedin-profile-9",
        )

    def test_relative_path_validation(self):
        self.assertEqual(validate_relative_path("incoming/linkedin-profile.pdf", "path"), "incoming/linkedin-profile.pdf")
        with self.assertRaisesRegex(PublishError, "traversal"):
            validate_relative_path("../linkedin-profile.pdf", "path")
        with self.assertRaisesRegex(PublishError, "traversal"):
            validate_relative_path(str(REPO_ROOT / "linkedin-profile.pdf"), "path")

    def test_module_never_merges_pull_requests(self):
        source = (REPO_ROOT / "scripts" / "publish_linkedin_sync.py").read_text(encoding="utf-8")
        self.assertNotIn("gh pr merge", source)
        self.assertNotIn("git merge", source)


if __name__ == "__main__":
    unittest.main()
