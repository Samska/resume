# Samuel Andrade — Resume

[![Build resume PDFs](https://github.com/Samska/resume/actions/workflows/build-pdf.yml/badge.svg)](https://github.com/Samska/resume/actions/workflows/build-pdf.yml)

Version-controlled, ATS-friendly resumes written in Markdown and generated as PDF with GitHub Actions.

[Download — English (PDF)](pdf/Samuel-Andrade-Resume-en-US.pdf) · [Baixar — Português (PDF)](pdf/Samuel-Andrade-Resume-pt-BR.pdf)

The Markdown resumes are the source of truth; do not edit the PDFs directly.

## About

Software engineer focused on quality engineering, test automation, and reliable delivery across web, mobile, APIs, accessibility, and CI/CD.

## How it works

1. Edit the relevant Markdown file.
2. Push the change to `main`.
3. GitHub Actions rebuilds the Portuguese and English PDFs into [`pdf/`](pdf) and validates their structure, extractable text, reading order, and page count.
4. If validation passes, the workflow commits the updated PDFs.

You can also run the same workflow manually from **Actions → Build resume PDFs → Run workflow**.

This repository contains no vacancy analysis, tailored-resume generation, match reports, or provenance manifests. The only model-backed integration is the LinkedIn profile import described below.

## Importing a LinkedIn profile PDF

The **Sync LinkedIn profile** workflow turns a LinkedIn profile PDF into a reviewable pull request with proposed updates to both master resumes:

- [`RESUME_en-US.md`](RESUME_en-US.md) (English);
- [`RESUME_pt-BR.md`](RESUME_pt-BR.md) (Brazilian Portuguese).

You do not need to run Python or any local script.

### Production workflow

1. Wait until this feature is merged into `main`.
2. Upload or replace `incoming/linkedin-profile.pdf` through the GitHub web UI.
3. Commit the PDF directly to `main`.
4. Wait for GitHub Actions to process it (**Actions → Sync LinkedIn profile**).
5. Review the generated pull request.
6. Check both `RESUME_en-US.md` and `RESUME_pt-BR.md`.
7. Merge the pull request only after validating the changes.
8. Confirm that the PDF is removed from the current repository tree (the pull request removes it from the output branch).
9. Remember that this repository is public and that the uploaded PDF may still exist in the input branch and in Git history.

### What the workflow does

- Extracts the PDF text locally with `pdftotext -layout` inside the runner.
- Detects the source language (a LinkedIn export is normally `pt-BR`).
- Sends only the extracted text to one OpenRouter model using the existing `OPENROUTER_API_KEY` repository secret and the `OPENROUTER_MODEL` repository variable (default: `openrouter/auto`). The model output budget is configurable through the `LINKEDIN_MAX_RESPONSE_TOKENS` repository variable (default: `24000`); neither variable contains secrets.
- Requires a strict JSON response where every value carries a verbatim evidence quote from the PDF and a translation of the same fact for the other resume language. The model never writes Markdown.
- Validates employers, titles, dates, locations, technologies, metrics, certifications, education, project names, URLs, and contact information, and fails closed on malformed, unsupported, or contradictory responses. There are no automatic paid retries.
- Reconciles the profile against the current resumes without deleting existing content, and reports new facts, conflicts, and missing information.
- Renders both Markdown files deterministically from the same validated factual model.
- Creates a `sync/linkedin-profile-<run_id>` branch and opens a pull request against the target branch. The pull request is not merged automatically.

If the import produces no resume changes, no empty pull request is created. If the PDF is absent, automatic runs exit successfully without processing, while manual runs fail with a clear message.

### Manual test run (feature branch)

Use `workflow_dispatch` to smoke-test the feature from the development branch without modifying `main`:

1. Place a **synthetic** PDF on the development branch (never a real personal profile in a test).
2. Open **Actions → Sync LinkedIn profile → Run workflow**.
3. Select the `feature/linkedin-profile-sync` branch in the branch selector.
4. Fill in all required inputs:
   - `source_branch`: `feature/linkedin-profile-sync`
   - `pdf_path`: `incoming/linkedin-profile.pdf`
   - `target_branch`: `feature/linkedin-profile-sync` (or another review branch)
5. Run the workflow and review the pull request it opens against the target branch.

All three inputs are required. A manual run with a missing PDF fails clearly.

### Privacy

- The uploaded PDF is only read from the runner checkout. It is not committed to the output branch, not included in the pull request, not uploaded as a workflow artifact, and raw extracted text is never written to the repository.
- Deleting the PDF from the current tree does not remove it from Git history. Because this repository is public, the uploaded PDF may be visible in the branch where it was uploaded and in past commits. Treat the uploaded PDF as public information.
- The import report used as the pull request body contains only the proposed resume changes, warnings, and conflicts. Workflow logs never print the full extracted text or the API key.

## Files

| File | Purpose |
| --- | --- |
| [RESUME_en-US.md](RESUME_en-US.md) | English resume source |
| [RESUME_pt-BR.md](RESUME_pt-BR.md) | Currículo em português |
| [pdf/](pdf) | Generated PDF files |
| [scripts/validate_resume.py](scripts/validate_resume.py) | Technical PDF validation |
| [scripts/linkedin_import.py](scripts/linkedin_import.py) | LinkedIn PDF extraction, validation, reconciliation, and deterministic rendering |
| [scripts/resume_source.py](scripts/resume_source.py) | Master-resume structure validation and text primitives |
| [scripts/publish_linkedin_sync.py](scripts/publish_linkedin_sync.py) | Output branch, PDF removal, and pull request publishing |
| [.github/workflows/build-pdf.yml](.github/workflows/build-pdf.yml) | Markdown-to-PDF automation |
| [.github/workflows/sync-linkedin-profile.yml](.github/workflows/sync-linkedin-profile.yml) | LinkedIn profile import automation |

## Validation

Each workflow run generates both PDFs and validates readability, structure, identity and contact details, main sections, experience order, education, and the one-to-two-page limit. The generated PDFs are committed only when validation passes.
