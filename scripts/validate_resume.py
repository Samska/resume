#!/usr/bin/env python3
"""Validate generated resume PDFs for basic ATS-readable structure."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

RESUMES = {
    "English": {
        "pdf": Path("pdf/Samuel-Andrade-Resume-en-US.pdf"),
        "sections": ["Professional Summary", "Technical Skills", "Professional Experience", "Education"],
        "timeline": ["Trustly", "AB InBev", "CI&T", "e.Mix", "DNGX"],
        "education": ["Postgraduate Specialization in Cybersecurity", "Technologist Degree in Systems Analysis and Development"],
    },
    "Português": {
        "pdf": Path("pdf/Samuel-Andrade-Resume-pt-BR.pdf"),
        "sections": ["Resumo Profissional", "Habilidades Técnicas", "Experiência Profissional", "Formação"],
        "timeline": ["Trustly", "AB InBev", "CI&T", "e.Mix", "DNGX"],
        "education": ["Pós-graduação Lato Sensu em Cybersecurity", "Tecnólogo em Análise e Desenvolvimento de Sistemas"],
    },
}

def run(command: list[str]) -> str:
    return subprocess.run(command, check=True, text=True, capture_output=True).stdout

def positions_are_ordered(text: str, terms: list[str]) -> bool:
    positions = [text.find(term) for term in terms]
    return all(position >= 0 for position in positions) and positions == sorted(positions)

def validate(label: str, config: dict[str, object]) -> tuple[list[tuple[str, bool, str]], bool]:
    pdf = config["pdf"]
    checks: list[tuple[str, bool, str]] = []
    try:
        metadata = run(["pdfinfo", str(pdf)])
        extracted = run(["pdftotext", "-layout", str(pdf), "-"])
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        return [("PDF legível", False, str(error))], False

    page_match = re.search(r"^Pages:\s+(\d+)$", metadata, re.MULTILINE)
    pages = int(page_match.group(1)) if page_match else 0
    checks.append(("PDF legível", bool(extracted.strip()), f"{len(extracted.strip())} caracteres extraídos"))
    checks.append(("Até duas páginas", 1 <= pages <= 2, f"{pages} página(s)"))
    checks.append(("Texto suficiente", len(extracted.strip()) >= 1_500, f"{len(extracted.strip())} caracteres"))
    identity = ["Samuel Andrade", "samuel.andradetp@live.com", "linkedin.com/in/Samska", "github.com/Samska"]
    missing_identity = [term for term in identity if term not in extracted]
    checks.append(("Identidade e contatos", not missing_identity, "completos" if not missing_identity else "faltando: " + ", ".join(missing_identity)))
    sections = config["sections"]
    missing_sections = [term for term in sections if term not in extracted]
    checks.append(("Seções principais", not missing_sections, "completas" if not missing_sections else "faltando: " + ", ".join(missing_sections)))
    timeline = config["timeline"]
    checks.append(("Experiência em ordem", positions_are_ordered(extracted, timeline), "ordem cronológica inversa"))
    education = config["education"]
    missing_education = [term for term in education if term not in extracted]
    checks.append(("Formação", not missing_education, "completa" if not missing_education else "faltando: " + ", ".join(missing_education)))
    return checks, all(passed for _, passed, _ in checks)

def main() -> int:
    report = [
        "## Validação técnica dos currículos",
        "",
        "> Esta validação mede legibilidade e estrutura do PDF. Ela não representa uma pontuação de ATS ou aderência a uma vaga.",
        "",
        "| Currículo | Resultado | Verificações |",
        "| --- | --- | --- |",
    ]
    success = True
    details: list[str] = []

    for label, config in RESUMES.items():
        checks, passed = validate(label, config)
        success = success and passed
        passed_count = sum(result for _, result, _ in checks)
        status = "✅ Aprovado" if passed else "❌ Falhou"
        report.append(f"| {label} | {status} | {passed_count}/{len(checks)} |")
        details.extend(["", f"### {label}", ""])
        details.extend(f"- {'✅' if result else '❌'} **{name}:** {detail}" for name, result, detail in checks)

    report.extend(details)
    output = "\n".join(report) + "\n"
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as file:
            file.write(output)
    print(output)
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
