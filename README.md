# Samuel Andrade — Resume

ATS-friendly resumes maintained in Markdown and generated as PDFs with GitHub Actions.

[English PDF](pdf/Samuel-Andrade-Resume-en-US.pdf) ·
[Portuguese PDF](pdf/Samuel-Andrade-Resume-pt-BR.pdf)

## How it works

The Markdown files are the source of truth. GitHub Actions generates and validates the PDFs, including structure, reading order, extractable text, and page count.

## Tailored resumes

The manual `Generate tailored resume` workflow analyzes a vacancy URL and selects relevant fragments from the selected master resume.

The generated resume and match report are composed from exact source fragments. The model cannot invent candidate claims or rewrite resume content.

To generate one:

1. Open `Actions → Generate tailored resume`.
2. Choose the language and model.
3. Provide a direct vacancy URL.
4. Download the generated artifact after a successful run.

Each artifact contains the tailored Markdown, PDF, and match report. It is retained for 30 days and is not committed to the repository.

## Limitations

This workflow is not an ATS score or job-fit guarantee. Review the generated files before applying. Missing skills remain gaps unless they are added truthfully to the master resume.
