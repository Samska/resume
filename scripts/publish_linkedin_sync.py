#!/usr/bin/env python3
"""Publish validated LinkedIn import changes to an output branch and open a PR.

Only the two master Markdown files are ever committed. The uploaded source PDF
is removed from the output tree when it is tracked, so the pull request does not
retain it in the current tree (it may still exist in Git history). Raw extracted
text is never written to the repository. The pull request is never merged
automatically.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ALLOWED_FILES = ("RESUME_en-US.md", "RESUME_pt-BR.md")
OUTPUT_BRANCH_PREFIX = "sync/linkedin-profile-"
COMMIT_MESSAGE = "docs: sync master resumes from LinkedIn profile PDF"
DEFAULT_PR_TITLE = "Sync master resumes from LinkedIn profile PDF"


class PublishError(RuntimeError):
    """An actionable, fail-closed publishing error."""


def _execute(
    command: list[str],
    repo_root: Path,
    *,
    text: bool = True,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, cwd=repo_root, text=text, capture_output=True)
    except OSError as exc:
        raise PublishError(f"command could not be started: {command[0]}") from exc


def _run(
    command: list[str],
    repo_root: Path,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = _execute(command, repo_root)
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
        raise PublishError(f"command failed ({' '.join(command[:2])}): {detail}")
    return completed


def validate_relative_path(value: str, field: str) -> str:
    path = Path(value)
    if not value.strip() or path.is_absolute() or any(part == ".." for part in path.parts):
        raise PublishError(f"{field} must be a repository-relative path without traversal")
    return path.as_posix()


def validate_branch_name(branch: str, field: str) -> str:
    if not branch.strip() or branch.strip() != branch:
        raise PublishError(f"{field} is empty or contains surrounding whitespace")
    completed = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise PublishError(f"{field} is not a valid git branch name")
    return branch


def validate_output_branch(output_branch: str, base_branch: str) -> str:
    if output_branch == base_branch:
        raise PublishError("the output branch must differ from the base branch")
    if not output_branch.startswith(OUTPUT_BRANCH_PREFIX):
        raise PublishError(
            f"the output branch must start with {OUTPUT_BRANCH_PREFIX!r} so only generated "
            f"branches can be published"
        )
    return validate_branch_name(output_branch, "output branch")


def check_staged_safety(repo_root: Path) -> list[str]:
    """Verify that only the master resumes are staged and contain no PDF data."""

    staged = _run(["git", "diff", "--cached", "--name-only"], repo_root).stdout.split()
    if not staged:
        raise PublishError("no master resume changes were staged")
    unexpected = sorted(name for name in staged if name not in ALLOWED_FILES)
    if unexpected:
        raise PublishError("unexpected staged files: " + ", ".join(unexpected))
    for name in staged:
        completed = _execute(["git", "show", f":{name}"], repo_root, text=False)
        if completed.returncode != 0:
            raise PublishError(f"could not read the staged blob for {name}")
        if b"%PDF" in completed.stdout:
            raise PublishError(f"staged file {name} contains raw PDF data")
    return staged


def remove_from_output_tree(repo_root: Path, remove_path: str) -> bool:
    """Stage the deletion of the source PDF when it is tracked.

    Deleting the file from the current tree does not remove it from Git history.
    """

    tracked = _run(["git", "ls-files", "--error-unmatch", remove_path], repo_root, check=False)
    if tracked.returncode != 0:
        return False
    _run(["git", "rm", "--cached", "--quiet", "--", remove_path], repo_root)
    return True


def verify_committed_tree(repo_root: Path, remove_path: str | None) -> list[str]:
    committed = _run(
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"], repo_root
    ).stdout.split()
    unexpected = sorted(
        name
        for name in committed
        if name not in ALLOWED_FILES and name != remove_path
    )
    if unexpected:
        raise PublishError("the commit contains unexpected files: " + ", ".join(unexpected))
    if remove_path:
        tracked = _run(["git", "ls-files", "--error-unmatch", remove_path], repo_root, check=False)
        if tracked.returncode == 0:
            raise PublishError("the output branch still tracks the source PDF")
    return committed


def push_branch(repo_root: Path, output_branch: str, remote: str = "origin") -> None:
    existing = _run(
        ["git", "ls-remote", "--exit-code", "--heads", remote, output_branch],
        repo_root,
        check=False,
    )
    command = ["git", "push"]
    if existing.returncode == 0:
        command.append("--force")
    command.extend(["--set-upstream", remote, output_branch])
    _run(command, repo_root)


def create_or_update_pull_request(
    repo_root: Path,
    output_branch: str,
    base_branch: str,
    report_path: Path,
    title: str = DEFAULT_PR_TITLE,
) -> None:
    listing = _run(
        [
            "gh",
            "pr",
            "list",
            "--head",
            output_branch,
            "--state",
            "open",
            "--json",
            "number",
            "--jq",
            ".[0].number // empty",
        ],
        repo_root,
        check=False,
    )
    if listing.returncode != 0:
        raise PublishError("could not query existing pull requests with gh")
    existing = listing.stdout.strip()
    if existing:
        command = [
            "gh",
            "pr",
            "edit",
            existing,
            "--title",
            title,
            "--body-file",
            str(report_path),
        ]
    else:
        command = [
            "gh",
            "pr",
            "create",
            "--base",
            base_branch,
            "--head",
            output_branch,
            "--title",
            title,
            "--body-file",
            str(report_path),
        ]
    completed = _execute(command, repo_root)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown gh error"
        raise PublishError(f"pull request publishing failed: {detail}")


def publish(
    repo_root: Path,
    base_branch: str,
    output_branch: str,
    report_path: Path,
    remove_path: str | None,
    pr_title: str = DEFAULT_PR_TITLE,
    dry_run: bool = False,
) -> None:
    validate_branch_name(base_branch, "base branch")
    validate_output_branch(output_branch, base_branch)
    if remove_path is not None:
        remove_path = validate_relative_path(remove_path, "remove path")
    if not report_path.is_file():
        raise PublishError("the import report file does not exist")

    _run(["git", "add", "--", *ALLOWED_FILES], repo_root)
    check_staged_safety(repo_root)
    if remove_path:
        remove_from_output_tree(repo_root, remove_path)

    _run(["git", "checkout", "-b", output_branch], repo_root)
    _run(["git", "commit", "-m", COMMIT_MESSAGE], repo_root)
    verify_committed_tree(repo_root, remove_path)

    if dry_run:
        return
    push_branch(repo_root, output_branch)
    create_or_update_pull_request(
        repo_root, output_branch, base_branch, report_path, title=pr_title
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--base-branch", required=True)
    parser.add_argument("--output-branch", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--remove-path")
    parser.add_argument("--pr-title", default=DEFAULT_PR_TITLE)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    report_path = Path(args.report).resolve()
    publish(
        repo_root,
        args.base_branch,
        args.output_branch,
        report_path,
        args.remove_path,
        pr_title=args.pr_title,
        dry_run=args.dry_run,
    )
    action = "prepared locally" if args.dry_run else "published"
    print(f"LinkedIn sync {action} on branch {args.output_branch}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PublishError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
