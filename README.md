# Samuel Andrade — Resume

[![Build resume PDFs](https://github.com/Samska/resume/actions/workflows/build-pdf.yml/badge.svg)](https://github.com/Samska/resume/actions/workflows/build-pdf.yml)

Version-controlled, ATS-friendly resumes written in Markdown and automatically generated as PDF with GitHub Actions.

[Download — English (PDF)](pdf/Samuel-Andrade-Resume-en-US.pdf) · [Baixar — Português (PDF)](pdf/Samuel-Andrade-Resume-pt-BR.pdf)

## About

Software engineer focused on quality engineering, test automation, and reliable delivery across web, mobile, APIs, accessibility, and CI/CD.

## How it works

1. Edit the relevant Markdown file.
2. Push the change to `main`.
3. GitHub Actions rebuilds the PDFs and validates their text, structure, reading order, and page count.
4. If validation passes, the workflow commits the updated PDFs.

## Generate a tailored resume

The manual **Generate tailored resume** workflow uses OpenRouter to adapt one of the master resumes to a specific vacancy without changing the source files.

### One-time setup

1. Open **Settings → Secrets and variables → Actions** in this repository.
2. Select **New repository secret**.
3. Name it exactly `OPENROUTER_API_KEY` and paste your OpenRouter key as its value.

Never put the key in a Markdown file, workflow input, commit, issue, or Actions log.

### For each vacancy

1. Open **Actions → Generate tailored resume → Run workflow**.
2. Select `en-US` or `pt-BR` and paste the vacancy URL into **Job URL**.
3. Choose one of the human-readable model options. **Automatic (recommended)** lets OpenRouter select an appropriate model; the other options prioritize a specific model family.
4. After the run succeeds, download the `tailored-resume-*` artifact from the workflow run.

The workflow extracts the company, role, and requirements from the URL and uses the identified company and role in the generated filenames. URL retrieval uses OpenRouter web tools and may add a small search/fetch charge to the model cost. The workflow fails rather than generating a resume when it cannot retrieve enough vacancy information.

The artifact contains the tailored Markdown, its PDF, and an advisory match report. It is retained for 30 days and is not committed to the repository. The job description and source resume are sent to OpenRouter and the selected model provider during generation.

The automation preserves identity, employers, dates, education, and chronology, and it fails validation if protected facts disappear or the PDF exceeds two pages. Review the generated resume before applying: language-model output can still require correction.

## Files

| File | Purpose |
| --- | --- |
| [RESUME_en-US.md](RESUME_en-US.md) | English resume source |
| [RESUME_pt-BR.md](RESUME_pt-BR.md) | Currículo em português |
| [pdf/](pdf) | Generated PDF files |
| [scripts/validate_resume.py](scripts/validate_resume.py) | Technical PDF validation |
| [scripts/tailor_resume.py](scripts/tailor_resume.py) | OpenRouter resume adaptation |
| [scripts/validate_tailored_resume.py](scripts/validate_tailored_resume.py) | Tailored-output validation |
| [.github/workflows/build-pdf.yml](.github/workflows/build-pdf.yml) | Markdown-to-PDF automation |
| [.github/workflows/generate-tailored-resume.yml](.github/workflows/generate-tailored-resume.yml) | Manual tailored-resume workflow |

The Markdown files are the source of truth; do not edit the PDF files directly.

## Validation

Each workflow run publishes a technical validation report in its GitHub Actions summary. The report checks whether both PDFs are readable, contain the expected contact details and sections, preserve reverse-chronological experience order, include education, and stay within two pages.

The result is a build-quality check, not an ATS score or a job-match score. Relevance still depends on the requirements and keywords of each job description.
