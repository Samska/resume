#!/usr/bin/env python3
"""Deterministic source grounding for tailored resumes and match reports."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
MAX_REQUIREMENTS = 20
MAX_SELECTED_FRAGMENTS = 25
MAX_SUMMARY_FRAGMENTS = 2
MAX_BULLETS_PER_EMPLOYER = 4
MAX_TOTAL_BULLETS = 16
MAX_VACANCY_TEXT = 300
MAX_TARGET_TEXT = 120

MONTHS = (
    "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|"
    "Fev|Abr|Mai|Ago|Set|Out|Dez"
)
DATE_RE = re.compile(
    rf"(?:{MONTHS})\s+\d{{4}}\s+-\s+(?:{MONTHS})\s+\d{{4}}",
    re.IGNORECASE,
)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
BULLET_RE = re.compile(r"^-\s+\S.*$")
SKILL_RE = re.compile(r"^\*\*([^*]+):\*\*\s+(.+?)\s*$")
ROLE_DATE_RE = re.compile(r"^\*\*([^*]+)\*\*\s+\|\s+(.+?)\s*$")
REQUIREMENT_ID_RE = re.compile(r"^req-[a-z0-9][a-z0-9-]{0,39}$")


class GroundingError(ValueError):
    """An actionable deterministic grounding error."""

    def __init__(self, category: str, message: str):
        super().__init__(f"{category}: {message}")
        self.category = category
        self.reason = message


@dataclass(frozen=True)
class Fragment:
    id: str
    kind: str
    section: str
    owner: str | None
    role: str | None
    text: str
    order: int
    mandatory: bool
    selectable: bool


@dataclass(frozen=True)
class Employer:
    key: str
    name: str
    fragment_ids: tuple[str, ...]
    bullet_ids: tuple[str, ...]
    header_groups: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Education:
    key: str
    fragment_ids: tuple[str, ...]


@dataclass(frozen=True)
class SourceResume:
    language: str
    text: str
    digest: str
    fragments: dict[str, Fragment]
    section_ids: dict[str, str]
    preamble_ids: tuple[str, ...]
    summary_ids: tuple[str, ...]
    skill_ids: tuple[str, ...]
    spoken_language_id: str
    employers: tuple[Employer, ...]
    education: tuple[Education, ...]


@dataclass(frozen=True)
class Requirement:
    id: str
    text: str
    priority: str


@dataclass(frozen=True)
class Match:
    requirement_id: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ValidatedSelection:
    schema_version: int
    language: str
    source_digest: str
    target_company: str
    target_role: str
    selected_fragment_ids: tuple[str, ...]
    requirements: tuple[Requirement, ...]
    strong_matches: tuple[Match, ...]
    partial_matches: tuple[Match, ...]
    gaps: tuple[str, ...]
    interview_topics: tuple[str, ...]

    def to_manifest(self) -> dict[str, Any]:
        classifications: dict[str, dict[str, Any]] = {}
        for match in self.strong_matches:
            classifications[match.requirement_id] = {
                "status": "strong",
                "evidence_ids": list(match.evidence_ids),
            }
        for match in self.partial_matches:
            classifications[match.requirement_id] = {
                "status": "partial",
                "evidence_ids": list(match.evidence_ids),
            }
        for requirement_id in self.gaps:
            classifications[requirement_id] = {"status": "gap", "evidence_ids": []}

        return {
            "schema_version": self.schema_version,
            "language": self.language,
            "source_digest": self.source_digest,
            "target_company": self.target_company,
            "target_role": self.target_role,
            "selected_fragment_ids": list(self.selected_fragment_ids),
            "vacancy_requirements": [
                {"id": item.id, "text": item.text, "priority": item.priority}
                for item in self.requirements
            ],
            "classifications": classifications,
            "interview_topics": list(self.interview_topics),
        }


EXPECTED_SECTIONS = {
    "pt-BR": ("Resumo Profissional", "Habilidades Técnicas", "Experiência Profissional", "Formação"),
    "en-US": ("Professional Summary", "Technical Skills", "Professional Experience", "Education"),
}


def _fail(category: str, message: str) -> None:
    raise GroundingError(category, message)


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return slug or "item"


def _nonempty(lines: Iterable[str]) -> list[str]:
    return [line for line in lines if line.strip()]


def _split_blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _heading_entries(lines: list[str], prefix: str) -> list[list[str]]:
    entries: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            if current:
                entries.append(current)
            current = [line]
        elif current:
            if line.strip():
                current.append(line)
        elif line.strip():
            _fail("SOURCE_PARSE", "experience contains content before its first employer heading")
    if current:
        entries.append(current)
    return entries


def parse_source(text: str, language: str) -> SourceResume:
    """Parse one supported master Markdown file into exact source fragments."""

    if language not in EXPECTED_SECTIONS:
        _fail("SOURCE_PARSE", f"unsupported language: {language}")
    if not text.strip():
        _fail("SOURCE_PARSE", "source Markdown is empty")

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or not re.fullmatch(r"#\s+.+", lines[0]):
        _fail("SOURCE_PARSE", "first line must be one identity heading")

    fragments: dict[str, Fragment] = {}
    order = 0

    def add(
        fragment_id: str,
        kind: str,
        section: str,
        value: str,
        owner: str | None = None,
        role: str | None = None,
        mandatory: bool = False,
        selectable: bool = False,
    ) -> str:
        nonlocal order
        if fragment_id in fragments:
            _fail("SOURCE_PARSE", f"fragment ID collision: {fragment_id}")
        if not value.strip():
            _fail("SOURCE_PARSE", f"empty fragment: {fragment_id}")
        if "```" in value or re.search(r"(?m)^\s*<[^>]+>", value):
            _fail("SOURCE_PARSE", f"unsupported Markdown structure in fragment: {fragment_id}")
        fragments[fragment_id] = Fragment(
            fragment_id,
            kind,
            section,
            owner,
            role,
            value,
            order,
            mandatory,
            selectable,
        )
        order += 1
        return fragment_id

    identity_id = add("identity.name", "identity", "preamble", lines[0], mandatory=True)
    preamble_lines = _nonempty(lines[1 : next((i for i, line in enumerate(lines[1:], 1) if line.startswith("## ")), len(lines))])
    if len(preamble_lines) != 3:
        _fail("SOURCE_PARSE", "preamble must contain headline, location, and contact")
    if "@" not in preamble_lines[2] or "linkedin.com/" not in preamble_lines[2] or "github.com/" not in preamble_lines[2]:
        _fail("SOURCE_PARSE", "contact line is missing expected public contact links")
    preamble_ids = [identity_id]
    preamble_ids.append(add("headline", "headline", "preamble", preamble_lines[0], mandatory=True))
    preamble_ids.append(add("contact.location", "location", "preamble", preamble_lines[1], mandatory=True))
    preamble_ids.append(add("contact.links", "contact", "preamble", preamble_lines[2], mandatory=True))

    headings = [(index, match.group(1)) for index, line in enumerate(lines) if (match := re.fullmatch(r"##\s+(.+?)\s*", line))]
    expected = EXPECTED_SECTIONS[language]
    if tuple(title for _, title in headings) != expected:
        _fail("SOURCE_PARSE", "required language-specific sections are missing or out of order")
    section_ids: dict[str, str] = {}
    section_ranges: dict[str, tuple[int, int]] = {}
    for position, (start, title) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        section_ids[title] = add(f"section.{_slug(title)}", "section_heading", title, lines[start], mandatory=True)
        section_ranges[title] = (start + 1, end)

    summary_title, skills_title, experience_title, education_title = expected
    summary_blocks = _split_blocks(lines[slice(*section_ranges[summary_title])])
    if not summary_blocks:
        _fail("SOURCE_PARSE", "professional summary is empty")
    summary_ids: list[str] = []
    for index, block in enumerate(summary_blocks, 1):
        if any(HEADING_RE.match(line) or BULLET_RE.match(line) or SKILL_RE.match(line) for line in block):
            _fail("SOURCE_PARSE", "summary contains unsupported Markdown structure")
        summary_ids.append(add(f"summary.{index}", "summary", summary_title, "\n".join(block), mandatory=False, selectable=True))

    skill_blocks = [[line] for line in lines[slice(*section_ranges[skills_title])] if line.strip()]
    if not skill_blocks:
        _fail("SOURCE_PARSE", "technical skills are empty")
    skill_ids: list[str] = []
    spoken_language_id: str | None = None
    for block in skill_blocks:
        if len(block) != 1:
            _fail("SOURCE_PARSE", "skill categories must be one-line blocks")
        match = SKILL_RE.fullmatch(block[0])
        if not match:
            _fail("SOURCE_PARSE", "skill section contains a malformed category line")
        label = match.group(1).strip()
        skill_id = add(
            f"skills.{_slug(label)}",
            "skill",
            skills_title,
            block[0],
            mandatory=False,
            selectable=True,
        )
        skill_ids.append(skill_id)
        if label.casefold() in {"idiomas", "spoken languages"}:
            if spoken_language_id is not None:
                _fail("SOURCE_PARSE", "spoken-language category appears more than once")
            spoken_language_id = skill_id
    if spoken_language_id is None:
        _fail("SOURCE_PARSE", "spoken-language category is required")

    experience_lines = lines[slice(*section_ranges[experience_title])]
    experience_blocks = _heading_entries(experience_lines, "### ")
    if not experience_blocks:
        _fail("SOURCE_PARSE", "professional experience is empty")
    employers: list[Employer] = []
    employer_keys: set[str] = set()
    for block in experience_blocks:
        if not block or not block[0].startswith("### "):
            _fail("SOURCE_PARSE", "each experience entry must start with a level-three heading")
        heading = block[0]
        heading_text = heading[4:].strip()
        fragment_ids: list[str] = []
        bullets: list[str] = []
        header_groups: list[tuple[str, ...]] = []
        if "|" in heading_text:
            role_text, employer_name = [part.strip() for part in heading_text.split("|", 1)]
            if not role_text or not employer_name:
                _fail("SOURCE_PARSE", "role and employer names are required")
            employer_key = _slug(employer_name)
            if employer_key in employer_keys:
                _fail("SOURCE_PARSE", f"ambiguous duplicate employer: {employer_name}")
            employer_keys.add(employer_key)
            role_id = add(
                f"experience.{employer_key}.role.{_slug(role_text)}",
                "role",
                experience_title,
                heading,
                owner=employer_name,
                role=role_text,
                mandatory=True,
            )
            fragment_ids.append(role_id)
            if len(block) < 2 or not DATE_RE.search(block[1]):
                _fail("SOURCE_PARSE", f"missing date for {employer_name}")
            date_id = add(
                f"experience.{employer_key}.date.1",
                "date",
                experience_title,
                block[1],
                owner=employer_name,
                role=role_text,
                mandatory=True,
            )
            fragment_ids.append(date_id)
            header_groups.append((role_id, date_id))
            bullet_lines = block[2:]
        else:
            employer_name = heading_text
            employer_key = _slug(employer_name)
            if not employer_name or employer_key in employer_keys:
                _fail("SOURCE_PARSE", f"ambiguous duplicate employer: {employer_name}")
            employer_keys.add(employer_key)
            employer_id = add(
                f"experience.{employer_key}.employer",
                "employer",
                experience_title,
                heading,
                owner=employer_name,
                mandatory=True,
            )
            fragment_ids.append(employer_id)
            if len(block) < 3 or block[1].startswith("-") or block[1].startswith("**"):
                _fail("SOURCE_PARSE", f"missing location for {employer_name}")
            location_id = add(
                f"experience.{employer_key}.location",
                "location",
                experience_title,
                block[1],
                owner=employer_name,
                mandatory=True,
            )
            fragment_ids.append(location_id)
            header_groups.append((employer_id, location_id))
            cursor = 2
            role_group: list[str] = []
            role_index = 0
            while cursor < len(block) and ROLE_DATE_RE.fullmatch(block[cursor]):
                role_index += 1
                role_match = ROLE_DATE_RE.fullmatch(block[cursor])
                assert role_match is not None
                role_text = role_match.group(1).strip()
                role_id = add(
                    f"experience.{employer_key}.role.{_slug(role_text)}",
                    "role_date",
                    experience_title,
                    block[cursor],
                    owner=employer_name,
                    role=role_text,
                    mandatory=True,
                )
                fragment_ids.append(role_id)
                role_group.append(role_id)
                cursor += 1
            if not role_group:
                _fail("SOURCE_PARSE", f"missing role and date for {employer_name}")
            header_groups.append(tuple(role_group))
            bullet_lines = block[cursor:]

        for bullet_index, bullet in enumerate(bullet_lines, 1):
            if not BULLET_RE.fullmatch(bullet):
                _fail("SOURCE_PARSE", f"malformed experience bullet for {employer_name}")
            bullet_id = add(
                f"experience.{employer_key}.bullet.{bullet_index}",
                "bullet",
                experience_title,
                bullet,
                owner=employer_name,
                mandatory=False,
                selectable=True,
            )
            fragment_ids.append(bullet_id)
            bullets.append(bullet_id)
        if not bullets:
            _fail("SOURCE_PARSE", f"employer has no experience bullets: {employer_name}")
        employers.append(Employer(employer_key, employer_name, tuple(fragment_ids), tuple(bullets), tuple(header_groups)))

    education_lines = lines[slice(*section_ranges[education_title])]
    education_blocks = _split_blocks(education_lines)
    if not education_blocks:
        _fail("SOURCE_PARSE", "education is empty")
    education: list[Education] = []
    education_keys: set[str] = set()
    for block in education_blocks:
        if len(block) < 2 or not block[0].startswith("### "):
            _fail("SOURCE_PARSE", "education entries must contain a level-three heading and date line")
        name = block[0][4:].strip()
        key = _slug(name)
        if key in education_keys:
            _fail("SOURCE_PARSE", f"ambiguous duplicate education entry: {name}")
        education_keys.add(key)
        if len(block) != 2 or not DATE_RE.search(block[1]):
            _fail("SOURCE_PARSE", f"malformed education entry: {name}")
        heading_id = add(f"education.{key}", "education", education_title, block[0], owner=name, mandatory=True)
        date_id = add(f"education.{key}.date", "education_date", education_title, block[1], owner=name, mandatory=True)
        education.append(Education(key, (heading_id, date_id)))

    if not employers:
        _fail("SOURCE_PARSE", "at least one employer is required")
    ordered_ids = list(preamble_ids)
    ordered_ids.append(section_ids[summary_title])
    ordered_ids.extend(summary_ids)
    ordered_ids.append(section_ids[skills_title])
    ordered_ids.extend(skill_ids)
    ordered_ids.append(section_ids[experience_title])
    for employer in employers:
        ordered_ids.extend(employer.fragment_ids)
    ordered_ids.append(section_ids[education_title])
    for entry in education:
        ordered_ids.extend(entry.fragment_ids)
    fragments = {
        fragment_id: replace(fragments[fragment_id], order=index)
        for index, fragment_id in enumerate(ordered_ids)
    }
    digest = hashlib.sha256(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")).hexdigest()
    return SourceResume(
        language,
        text.replace("\r\n", "\n").replace("\r", "\n"),
        digest,
        fragments,
        section_ids,
        tuple(preamble_ids),
        tuple(summary_ids),
        tuple(skill_ids),
        spoken_language_id,
        tuple(employers),
        tuple(education),
    )


def _strict_json(raw: str) -> dict[str, Any]:
    candidate = raw.strip()
    if candidate.startswith("```"):
        fenced = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", candidate, flags=re.IGNORECASE | re.DOTALL)
        if not fenced:
            _fail("MODEL_RESPONSE", "fenced response is not one complete JSON object")
        candidate = fenced.group(1).strip()
    elif "```" in candidate:
        _fail("MODEL_RESPONSE", "response contains surrounding prose or code fences")
    elif not (candidate.startswith("{") and candidate.endswith("}")):
        _fail("MODEL_RESPONSE", "response contains surrounding prose")
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        _fail("MODEL_RESPONSE", f"invalid JSON: {exc.msg}")
    if not isinstance(value, dict):
        _fail("MODEL_RESPONSE", "response must be a JSON object")
    return value


def normalize_vacancy_text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        _fail("MODEL_SCHEMA", f"{field} must be a string")
    normalized = unicodedata.normalize("NFKC", value)
    normalized = "".join(char for char in normalized if unicodedata.category(char)[0] != "C" or char in "\t\n\r")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        _fail("MODEL_SCHEMA", f"{field} is empty after sanitization")
    if len(normalized) > limit:
        _fail("MODEL_SCHEMA", f"{field} exceeds {limit} characters")
    return normalized


def escape_markdown(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|>~-])", r"\\\1", value)


def _list_field(data: dict[str, Any], name: str) -> list[Any]:
    value = data.get(name)
    if not isinstance(value, list):
        _fail("MODEL_SCHEMA", f"{name} must be an array")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], context: str) -> None:
    if set(value) != expected:
        unknown = sorted(set(value) - expected)
        missing = sorted(expected - set(value))
        detail = []
        if unknown:
            detail.append("unknown: " + ", ".join(unknown))
        if missing:
            detail.append("missing: " + ", ".join(missing))
        _fail("MODEL_SCHEMA", f"{context} fields are invalid ({'; '.join(detail)})")


def validate_response(data: dict[str, Any], source: SourceResume) -> ValidatedSelection:
    """Validate the closed model response and return a manifest-ready selection."""

    _exact_keys(
        data,
        {
            "schema_version",
            "target_company",
            "target_role",
            "selected_fragment_ids",
            "vacancy_requirements",
            "requirement_classifications",
            "interview_topics",
        },
        "response",
    )
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        _fail("MODEL_SCHEMA", f"unsupported schema version: {data['schema_version']!r}")
    company = normalize_vacancy_text(data["target_company"], "target_company", MAX_TARGET_TEXT)
    role = normalize_vacancy_text(data["target_role"], "target_role", MAX_TARGET_TEXT)

    selected_raw = _list_field(data, "selected_fragment_ids")
    if not 1 <= len(selected_raw) <= MAX_SELECTED_FRAGMENTS:
        _fail("MODEL_SELECTION", f"selected fragment count must be 1-{MAX_SELECTED_FRAGMENTS}")
    selected: list[str] = []
    for value in selected_raw:
        if not isinstance(value, str) or not value:
            _fail("MODEL_SCHEMA", "selected fragment IDs must be non-empty strings")
        if value in selected:
            _fail("MODEL_SCHEMA", f"duplicate selected fragment ID: {value}")
        fragment = source.fragments.get(value)
        if fragment is None:
            _fail("MODEL_SCHEMA", f"unknown selected fragment ID: {value}")
        if not fragment.selectable:
            _fail("MODEL_SELECTION", f"fragment is not selectable: {value}")
        selected.append(value)

    selected_set = set(selected)
    summaries = [item for item in selected if source.fragments[item].kind == "summary"]
    skills = [item for item in selected if source.fragments[item].kind == "skill"]
    bullets = [item for item in selected if source.fragments[item].kind == "bullet"]
    if not 1 <= len(summaries) <= MAX_SUMMARY_FRAGMENTS:
        _fail("MODEL_SELECTION", "selection must contain one or two summary paragraphs")
    if len(skills) < 2 or source.spoken_language_id not in selected_set:
        _fail("MODEL_SELECTION", "selection must contain spoken languages and another skill category")
    if len(bullets) > MAX_TOTAL_BULLETS:
        _fail("MODEL_SELECTION", f"selection contains more than {MAX_TOTAL_BULLETS} bullets")
    for employer in source.employers:
        employer_bullets = [item for item in bullets if item in employer.bullet_ids]
        if not employer_bullets:
            _fail("MODEL_SELECTION", f"selection omits employer: {employer.name}")
        if len(employer_bullets) > MAX_BULLETS_PER_EMPLOYER:
            _fail("MODEL_SELECTION", f"selection contains more than {MAX_BULLETS_PER_EMPLOYER} bullets for {employer.name}")

    requirement_items = _list_field(data, "vacancy_requirements")
    if not requirement_items or len(requirement_items) > MAX_REQUIREMENTS:
        _fail("VACANCY_ANALYSIS", f"vacancy requirements must contain 1-{MAX_REQUIREMENTS} items")
    requirements: list[Requirement] = []
    requirement_ids: set[str] = set()
    for item in requirement_items:
        if not isinstance(item, dict):
            _fail("MODEL_SCHEMA", "vacancy requirements must be objects")
        _exact_keys(item, {"id", "text", "priority"}, "vacancy requirement")
        requirement_id = item["id"]
        if not isinstance(requirement_id, str) or not REQUIREMENT_ID_RE.fullmatch(requirement_id):
            _fail("MODEL_SCHEMA", f"invalid requirement ID: {requirement_id!r}")
        if requirement_id in requirement_ids:
            _fail("MODEL_SCHEMA", f"duplicate requirement ID: {requirement_id}")
        requirement_ids.add(requirement_id)
        priority = item["priority"]
        if priority not in {"required", "preferred", "context"}:
            _fail("MODEL_SCHEMA", f"invalid requirement priority: {priority!r}")
        requirements.append(Requirement(requirement_id, normalize_vacancy_text(item["text"], f"{requirement_id}.text", MAX_VACANCY_TEXT), priority))

    classification_items = _list_field(data, "requirement_classifications")
    if not classification_items or len(classification_items) > MAX_REQUIREMENTS:
        _fail("VACANCY_ANALYSIS", f"requirement classifications must contain 1-{MAX_REQUIREMENTS} items")
    strong_matches: list[Match] = []
    partial_matches: list[Match] = []
    gaps: list[str] = []
    classified: set[str] = set()
    for item in classification_items:
        if not isinstance(item, dict):
            _fail("MODEL_SCHEMA", "requirement classifications must be objects")
        _exact_keys(item, {"requirement_id", "status", "evidence_ids"}, "requirement classification")
        requirement_id = item["requirement_id"]
        if requirement_id not in requirement_ids:
            _fail("MODEL_SCHEMA", f"classification references unknown requirement: {requirement_id}")
        if requirement_id in classified:
            _fail("MODEL_SCHEMA", f"duplicate requirement classification: {requirement_id}")
        classified.add(requirement_id)
        status = item["status"]
        if status not in {"strong", "partial", "gap"}:
            _fail("MODEL_SCHEMA", f"invalid requirement classification: {status!r}")
        evidence_values = _list_field(item, "evidence_ids")
        if status == "gap":
            if evidence_values:
                _fail("EVIDENCE_MAPPING", f"gap classification cannot contain evidence: {requirement_id}")
            gaps.append(requirement_id)
            continue
        if not evidence_values:
            _fail("EVIDENCE_MAPPING", f"{status} classification requires evidence: {requirement_id}")
        evidence_ids: list[str] = []
        for evidence_id in evidence_values:
            if not isinstance(evidence_id, str) or evidence_id in evidence_ids:
                _fail("EVIDENCE_MAPPING", f"invalid or duplicate evidence ID: {evidence_id!r}")
            if evidence_id not in selected_set:
                _fail("EVIDENCE_MAPPING", f"evidence is not selected: {evidence_id}")
            evidence_ids.append(evidence_id)
        match = Match(requirement_id, tuple(evidence_ids))
        if status == "strong":
            strong_matches.append(match)
        else:
            partial_matches.append(match)
    if classified != requirement_ids:
        missing = sorted(requirement_ids - classified)
        _fail("MODEL_SCHEMA", "every vacancy requirement must be classified exactly once (missing: " + ", ".join(missing) + ")")

    strong = tuple(strong_matches)
    partial = tuple(partial_matches)

    topic_items = _list_field(data, "interview_topics")
    topics: list[str] = []
    for item in topic_items:
        if not isinstance(item, dict):
            _fail("MODEL_SCHEMA", "interview topics must be objects")
        _exact_keys(item, {"requirement_id"}, "interview topic")
        requirement_id = item["requirement_id"]
        if requirement_id not in requirement_ids:
            _fail("MODEL_SCHEMA", f"interview topic references unknown requirement: {requirement_id}")
        if requirement_id in topics:
            _fail("MODEL_SCHEMA", f"duplicate interview topic: {requirement_id}")
        topics.append(requirement_id)

    return ValidatedSelection(
        SCHEMA_VERSION,
        source.language,
        source.digest,
        company,
        role,
        tuple(selected),
        tuple(requirements),
        strong,
        partial,
        tuple(gaps),
        tuple(topics),
    )


def parse_and_validate_response(raw: str, source: SourceResume) -> ValidatedSelection:
    return validate_response(_strict_json(raw), source)


def _render_blocks(blocks: Iterable[str]) -> str:
    values = [item.strip("\n") for item in blocks if item.strip()]
    if not values:
        _fail("RENDER", "rendered section is empty")
    return "\n\n".join(values) + "\n"


def render_resume(source: SourceResume, selection: ValidatedSelection) -> str:
    if selection.source_digest != source.digest or selection.language != source.language:
        _fail("MANIFEST", "selection does not belong to the current source")
    fragments = source.fragments
    blocks: list[str] = []
    preamble = [fragments[item].text for item in source.preamble_ids]
    blocks.append("\n".join(preamble))

    blocks.append(fragments[source.section_ids[EXPECTED_SECTIONS[source.language][0]]].text)
    selected_summaries = set(item for item in selection.selected_fragment_ids if fragments[item].kind == "summary")
    blocks.extend(fragments[item].text for item in source.summary_ids if item in selected_summaries)

    skills = [item for item in selection.selected_fragment_ids if fragments[item].kind == "skill"]
    blocks.append(fragments[source.section_ids[EXPECTED_SECTIONS[source.language][1]]].text)
    blocks.extend(fragments[item].text for item in skills)

    experience_title = EXPECTED_SECTIONS[source.language][2]
    blocks.append(fragments[source.section_ids[experience_title]].text)
    selected_set = set(selection.selected_fragment_ids)
    for employer in source.employers:
        employer_blocks: list[str] = []
        for group in employer.header_groups:
            employer_blocks.append("\n".join(fragments[item].text for item in group))
        selected_bullets = [item for item in employer.bullet_ids if item in selected_set]
        employer_blocks.extend(fragments[item].text for item in selected_bullets)
        blocks.append("\n\n".join(employer_blocks))

    education_title = EXPECTED_SECTIONS[source.language][3]
    blocks.append(fragments[source.section_ids[education_title]].text)
    for entry in source.education:
        blocks.append("\n".join(fragments[item].text for item in entry.fragment_ids))
    return _render_blocks(blocks)


REPORT_TEXT = {
    "en-US": {
        "overall": "This report separates vacancy requirements from candidate evidence. It is not a match score or a guarantee.",
        "vacancy": "Requirements are reproduced as sanitized vacancy data and are not candidate evidence.",
        "strong": "The following requirement has selected source evidence:",
        "partial": "The following requirement has related source evidence, but the exact requirement is not fully documented:",
        "gap": "No supporting candidate fragment was selected from the master resume.",
        "changes": "The resume was composed from exact source fragments; no candidate-facing sentence was rewritten.",
        "interview": "Discuss or verify this requirement during an interview; this topic does not assert that the candidate has the skill.",
        "evidence": "Exact source evidence used by this report:",
    },
    "pt-BR": {
        "overall": "Este relatório separa os requisitos da vaga das evidências do candidato. Ele não é uma pontuação nem uma garantia.",
        "vacancy": "Os requisitos são dados sanitizados da vaga e não constituem evidência do candidato.",
        "strong": "O requisito abaixo possui evidência selecionada da fonte:",
        "partial": "O requisito abaixo possui evidência relacionada, mas o requisito exato não está totalmente documentado:",
        "gap": "Nenhum fragmento de suporte foi selecionado do currículo mestre.",
        "changes": "O currículo foi composto por fragmentos exatos da fonte; nenhuma frase voltada ao candidato foi reescrita.",
        "interview": "Discutir ou verificar este requisito em entrevista; este tópico não afirma que o candidato possui a habilidade.",
        "evidence": "Evidências exatas da fonte usadas por este relatório:",
    },
}


def _evidence_block(source: SourceResume, evidence_ids: Iterable[str]) -> list[str]:
    lines: list[str] = []
    for evidence_id in evidence_ids:
        fragment = source.fragments[evidence_id]
        lines.extend(
            [
                f"- Fragment `{evidence_id}`:",
                "  ```text",
                *(f"  {line}" for line in fragment.text.splitlines()),
                "  ```",
            ]
        )
    return lines


def render_report(source: SourceResume, selection: ValidatedSelection) -> str:
    if selection.source_digest != source.digest or selection.language != source.language:
        _fail("MANIFEST", "selection does not belong to the current source")
    text = REPORT_TEXT[source.language]
    requirements = {item.id: item for item in selection.requirements}
    strong = {item.requirement_id: item for item in selection.strong_matches}
    partial = {item.requirement_id: item for item in selection.partial_matches}
    lines = [
        f"# Match report: {escape_markdown(selection.target_company)} | {escape_markdown(selection.target_role)}",
        "",
        "## Overall assessment",
        "",
        text["overall"],
        "",
        "## Vacancy requirements",
        "",
        text["vacancy"],
    ]
    for requirement in selection.requirements:
        lines.append(f"- `{requirement.id}` [{requirement.priority}] {escape_markdown(requirement.text)}")

    lines.extend(["", "## Strong matches", ""])
    if not strong:
        lines.append("- None classified.")
    for requirement_id, match in strong.items():
        requirement = requirements[requirement_id]
        lines.extend([f"- `{requirement.id}`: {escape_markdown(requirement.text)}", f"  {text['strong']}"])
        lines.extend(_evidence_block(source, match.evidence_ids))

    lines.extend(["", "## Partial matches", ""])
    if not partial:
        lines.append("- None classified.")
    for requirement_id, match in partial.items():
        requirement = requirements[requirement_id]
        lines.extend([f"- `{requirement.id}`: {escape_markdown(requirement.text)}", f"  {text['partial']}"])
        lines.extend(_evidence_block(source, match.evidence_ids))

    lines.extend(["", "## Gaps", ""])
    if not selection.gaps:
        lines.append("- None classified.")
    for requirement_id in selection.gaps:
        requirement = requirements[requirement_id]
        lines.append(f"- `{requirement.id}`: {escape_markdown(requirement.text)}. {text['gap']}")

    lines.extend([
        "",
        "## Changes made",
        "",
        f"- {text['changes']}",
        f"- Selected source fragments: {len(selection.selected_fragment_ids)}.",
        "",
        "## Interview points",
        "",
    ])
    if not selection.interview_topics:
        lines.append("- None classified.")
    for requirement_id in selection.interview_topics:
        requirement = requirements[requirement_id]
        lines.append(f"- `{requirement.id}`: {escape_markdown(requirement.text)}. {text['interview']}")

    used_evidence: list[str] = []
    for match in (*selection.strong_matches, *selection.partial_matches):
        for evidence_id in match.evidence_ids:
            if evidence_id not in used_evidence:
                used_evidence.append(evidence_id)
    lines.extend(["", "## Evidence index", "", text["evidence"]])
    if not used_evidence:
        lines.append("- None.")
    else:
        lines.extend(_evidence_block(source, used_evidence))
    return "\n".join(lines).rstrip() + "\n"


def plain_markdown(text: str) -> str:
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    value = re.sub(r"[*_`#]", "", value)
    value = re.sub(r"^\s*[-+]\s+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_extracted(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def validate_rendered_markdown(source: SourceResume, selection: ValidatedSelection, markdown: str) -> None:
    expected = render_resume(source, selection)
    if markdown != expected:
        _fail("MARKDOWN_PROVENANCE", "rendered Markdown differs from deterministic source composition")
    if not markdown.strip():
        _fail("MARKDOWN_PROVENANCE", "rendered Markdown is unexpectedly empty")
    if re.search(r"(?im)^(?:```|~~~|<|---$)", markdown):
        _fail("MARKDOWN_PROVENANCE", "rendered resume contains an ATS-unsafe structure")


def validate_rendered_report(source: SourceResume, selection: ValidatedSelection, report: str) -> None:
    expected = render_report(source, selection)
    if report != expected:
        _fail("REPORT_PROVENANCE", "match report differs from deterministic template rendering")


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("MANIFEST", f"cannot read manifest: {exc}")
    if not isinstance(data, dict):
        _fail("MANIFEST", "manifest must be an object")
    return data


def validate_manifest(source: SourceResume, manifest: dict[str, Any]) -> ValidatedSelection:
    required = {
        "schema_version",
        "language",
        "source_digest",
        "target_company",
        "target_role",
        "selected_fragment_ids",
        "vacancy_requirements",
        "classifications",
        "interview_topics",
    }
    _exact_keys(manifest, required, "manifest")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != SCHEMA_VERSION or manifest["language"] != source.language or manifest["source_digest"] != source.digest:
        _fail("MANIFEST", "manifest does not belong to the current source")
    requirements_data = manifest["vacancy_requirements"]
    classifications = manifest["classifications"]
    if not isinstance(requirements_data, list) or not isinstance(classifications, dict):
        _fail("MANIFEST", "manifest requirement data is malformed")
    if any(not isinstance(item, dict) for item in requirements_data):
        _fail("MANIFEST", "manifest requirements must be objects")
    requirement_ids: set[str] = set()
    for item in requirements_data:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            _fail("MANIFEST", "manifest requirements must contain string IDs")
        requirement_ids.add(item["id"])
    if set(classifications) != requirement_ids:
        _fail("MANIFEST", "manifest classifications do not match its requirements")
    raw: dict[str, Any] = {
        "schema_version": manifest["schema_version"],
        "target_company": manifest["target_company"],
        "target_role": manifest["target_role"],
        "selected_fragment_ids": manifest["selected_fragment_ids"],
        "vacancy_requirements": requirements_data,
        "requirement_classifications": [],
        "interview_topics": [{"requirement_id": item} for item in manifest["interview_topics"]],
    }
    for requirement in requirements_data:
        item = classifications.get(requirement.get("id"))
        if not isinstance(item, dict):
            _fail("MANIFEST", "missing requirement classification")
        status = item.get("status")
        if status not in {"strong", "partial", "gap"}:
            _fail("MANIFEST", "invalid requirement classification")
        _exact_keys(item, {"status", "evidence_ids"}, "manifest classification")
        if not isinstance(item["evidence_ids"], list):
            _fail("MANIFEST", "manifest evidence IDs must be an array")
        if status == "gap" and item["evidence_ids"] != []:
            _fail("MANIFEST", "gap classification cannot contain evidence")
        raw["requirement_classifications"].append({
            "requirement_id": requirement["id"],
            "status": status,
            "evidence_ids": item["evidence_ids"],
        })
    selection = validate_response(raw, source)
    if selection.target_company != manifest["target_company"] or selection.target_role != manifest["target_role"]:
        _fail("MANIFEST", "manifest target metadata is inconsistent")
    return selection


def write_manifest(path: Path, selection: ValidatedSelection) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(selection.to_manifest(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
