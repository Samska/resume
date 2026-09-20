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
3. GitHub Actions rebuilds the PDFs and validates their structure, extractable text, reading order, and page count.
4. If validation passes, the workflow commits the updated PDFs.

## Generate a tailored resume

The manual **Generate tailored resume** workflow accepts a direct vacancy URL, an output language (`en-US` or `pt-BR`), and a model selection, then tailors the matching master resume without changing the source files.

### One-time setup

1. Open **Settings → Secrets and variables → Actions** in this repository.
2. Select **New repository secret**.
3. Name it exactly `OPENROUTER_API_KEY` and paste your OpenRouter key as its value.

Never put the key in a Markdown file, workflow input, commit, issue, or Actions log.

### For each vacancy

1. Open **Actions → Generate tailored resume → Run workflow**.
2. Select the language, choose a model option (**Automatic (recommended)** lets OpenRouter select a model; the others prioritize a specific model family), and paste the vacancy URL into **Job URL**.
3. After a successful run, download the `tailored-resume-*` artifact.

### How the LLM is used

1. Repository code validates the master resume, then sends the vacancy URL and selectable source fragments to one model through OpenRouter.
2. The model retrieves and analyzes the vacancy and returns structured JSON: vacancy requirements, a `strong`/`partial`/`gap` classification per requirement, evidence IDs, selected source-fragment IDs, and interview topics.
3. Repository code validates the response schema, fragment IDs, selection limits, and evidence mappings. Unsupported requirements remain explicit gaps.
4. The resume and match report are composed deterministically from exact master-resume fragments; the PDF is generated from that Markdown and validated for structure, extractable text, reading order, page count, and source provenance.

The LLM does not write or rewrite candidate-facing resume claims, report prose, or evidence excerpts. Invalid sources, insufficient vacancy retrieval, and malformed or unsupported model responses fail the run instead of producing a resume, with no automatic paid retry.

### Output, retention, and limitations

A successful run produces exactly three files in the `tailored-resume-*` artifact: the tailored Markdown, its PDF, and a match report. The artifact is retained for 30 days and is not committed to the repository. Each successful generation uses one OpenRouter request, and vacancy retrieval through OpenRouter web tools may add provider search/fetch charges. The vacancy and source fragments are sent to OpenRouter and the selected model provider.

Review the generated resume before applying. Relevance depends on the requirements and keywords retrieved from the vacancy, and model classifications can still require human correction. This is not an ATS score, a job-fit guarantee, or a hallucination-proof process.

## Files

| File | Purpose |
| --- | --- |
| [RESUME_en-US.md](RESUME_en-US.md) | English resume source |
| [RESUME_pt-BR.md](RESUME_pt-BR.md) | Currículo em português |
| [pdf/](pdf) | Generated PDF files |
| [scripts/validate_resume.py](scripts/validate_resume.py) | Technical PDF validation |
| [scripts/resume_grounding.py](scripts/resume_grounding.py) | Source parsing, grounding, rendering, and provenance validation |
| [scripts/tailor_resume.py](scripts/tailor_resume.py) | OpenRouter vacancy analysis and orchestration |
| [scripts/validate_tailored_resume.py](scripts/validate_tailored_resume.py) | Tailored-output validation |
| [.github/workflows/build-pdf.yml](.github/workflows/build-pdf.yml) | Markdown-to-PDF automation |
| [.github/workflows/generate-tailored-resume.yml](.github/workflows/generate-tailored-resume.yml) | Manual tailored-resume workflow |

## Validation

Each run publishes a deterministic grounding report in its GitHub Actions summary covering source provenance, composition, evidence mappings, PDF readability, resume structure, and the one-to-two-page limit, with selected-fragment, evidence, gap, and employer counts but no full vacancy or candidate source.
