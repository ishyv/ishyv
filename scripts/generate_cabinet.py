#!/usr/bin/env python3
"""Generate the public artifact-cabinet trace as dependency-free SVG."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any, Mapping, Sequence


GITHUB_API = "https://api.github.com"
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
THEMES = {
    "dark": {
        "bg": "#08090b",
        "surface": "#101318",
        "text": "#f0e8da",
        "soft": "#d8cdb9",
        "muted": "#a79d8b",
        "faint": "#7e7568",
        "line": "#4a4134",
        "accent": "#c79c57",
        "signal": "#79a6a3",
        "grid": "#20262a",
    },
    "light": {
        "bg": "#f4efe5",
        "surface": "#ebe3d5",
        "text": "#201c17",
        "soft": "#393128",
        "muted": "#665b4d",
        "faint": "#766a5a",
        "line": "#b8a98f",
        "accent": "#8a6229",
        "signal": "#416f6d",
        "grid": "#d8cebd",
    },
}


class CabinetError(RuntimeError):
    """Raised when generation cannot produce a complete, truthful cabinet."""


@dataclass(frozen=True)
class ArtifactConfig:
    accession: str
    slug: str


@dataclass(frozen=True)
class RepositoryRecord:
    accession: str
    slug: str
    name: str
    language: str
    created_at: datetime
    latest_commit_at: datetime
    previous_commit_at: datetime

    def with_name(self, name: str) -> "RepositoryRecord":
        return replace(self, name=name)


@dataclass(frozen=True)
class CabinetSummary:
    recently_disturbed: RepositoryRecord
    longest_kept: RepositoryRecord
    returned_to: RepositoryRecord | None


def parse_github_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise CabinetError(f"missing GitHub timestamp: {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CabinetError(f"invalid GitHub timestamp for {field}: {value!r}") from exc
    if parsed.tzinfo is None:
        raise CabinetError(f"GitHub timestamp is missing a timezone: {field}")
    return parsed.astimezone(UTC)


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CabinetError(f"missing repository field: {field}")
    return value.strip()


def _commit_date(commit: Any, index: int) -> datetime:
    try:
        value = commit["commit"]["committer"]["date"]
    except (KeyError, TypeError) as exc:
        raise CabinetError(f"missing commit timestamp at index {index}") from exc
    return parse_github_datetime(value, f"commits[{index}].commit.committer.date")


def normalize_repository(
    config: ArtifactConfig,
    repository: Mapping[str, Any],
    commits: Sequence[Mapping[str, Any]],
) -> RepositoryRecord:
    name = _required_string(repository, "name")
    if name != config.slug:
        raise CabinetError(f"repository name mismatch: expected {config.slug!r}, got {name!r}")
    if repository.get("private") is not False or repository.get("visibility") != "public":
        raise CabinetError(f"repository is not public: {config.slug}")
    if len(commits) < 2:
        raise CabinetError(f"{config.slug} must expose at least two commits")

    language = repository.get("language")
    if language is None:
        language = "unclassified"
    if not isinstance(language, str) or not language.strip():
        raise CabinetError(f"invalid language for {config.slug}")

    latest = _commit_date(commits[0], 0)
    previous = _commit_date(commits[1], 1)
    if latest < previous:
        raise CabinetError(f"commit order is invalid for {config.slug}")
    created = parse_github_datetime(repository.get("created_at"), "created_at")
    if previous < created:
        raise CabinetError(f"a commit predates repository creation for {config.slug}")

    return RepositoryRecord(
        accession=config.accession,
        slug=config.slug,
        name=name,
        language=language.strip(),
        created_at=created,
        latest_commit_at=latest,
        previous_commit_at=previous,
    )


def derive_summary(
    records: Sequence[RepositoryRecord], return_after_days: int = 120
) -> CabinetSummary:
    if not records:
        raise CabinetError("the cabinet requires at least one artifact")
    if return_after_days <= 0:
        raise CabinetError("return_after_days must be positive")

    recently_disturbed = max(records, key=lambda record: record.latest_commit_at)
    longest_kept = max(
        records,
        key=lambda record: record.latest_commit_at - record.created_at,
    )
    returned = [
        record
        for record in records
        if (record.latest_commit_at - record.previous_commit_at).days >= return_after_days
    ]
    returned_to = max(returned, key=lambda record: record.latest_commit_at) if returned else None
    return CabinetSummary(recently_disturbed, longest_kept, returned_to)


def validate_chronology(records: Sequence[RepositoryRecord]) -> None:
    for earlier, later in zip(records, records[1:]):
        if later.created_at < earlier.created_at:
            raise CabinetError(
                "cabinet artifacts must be chronological: "
                f"{later.slug} predates {earlier.slug}"
            )


def _format_date(value: datetime) -> str:
    return f"{value.day:02d} {value.strftime('%b').lower()} {value.year}"


def _format_span(start: datetime, end: datetime) -> str:
    months = (end.year - start.year) * 12 + end.month - start.month
    if end.day < start.day:
        months -= 1
    months = max(months, 0)
    years, remaining_months = divmod(months, 12)
    if years and remaining_months:
        return f"{years}y {remaining_months}m"
    if years:
        return f"{years}y"
    return f"{remaining_months}m"


def _timeline_x(value: datetime, start: datetime, end: datetime) -> float:
    left, right = 250.0, 550.0
    total = max((end - start).total_seconds(), 1.0)
    elapsed = min(max((value - start).total_seconds(), 0.0), total)
    return left + (right - left) * elapsed / total


def _svg_text(x: float, y: float, value: str, css_class: str, **attrs: str) -> str:
    rendered_attrs = " ".join(f'{key.replace("_", "-")}="{escape(str(item), quote=True)}"' for key, item in attrs.items())
    suffix = f" {rendered_attrs}" if rendered_attrs else ""
    return f'<text x="{x:g}" y="{y:g}" class="{css_class}"{suffix}>{escape(value, quote=True)}</text>'


def render_svg(
    records: Sequence[RepositoryRecord],
    summary: CabinetSummary,
    theme: str,
) -> str:
    if theme not in THEMES:
        raise CabinetError(f"unknown theme: {theme}")
    if not records:
        raise CabinetError("cannot render an empty cabinet")

    colors = THEMES[theme]
    earliest = min(record.created_at for record in records)
    latest = max(record.latest_commit_at for record in records)
    row_y = [140 + index * 54 for index in range(len(records))]

    rows: list[str] = []
    for record, y in zip(records, row_y, strict=True):
        start_x = _timeline_x(record.created_at, earliest, latest)
        end_x = _timeline_x(record.latest_commit_at, earliest, latest)
        rows.extend(
            [
                _svg_text(42, y + 5, record.accession, "accession"),
                _svg_text(108, y - 5, record.name, "name"),
                _svg_text(108, y + 20, f"{record.language} · {record.created_at.year}", "meta"),
                f'<line data-lifespan="{escape(record.slug, quote=True)}" x1="{start_x:.2f}" x2="{end_x:.2f}" y1="{y}" y2="{y}" class="lifespan"/>',
                f'<circle cx="{start_x:.2f}" cy="{y}" r="4" class="origin"/>',
                f'<circle cx="{end_x:.2f}" cy="{y}" r="6" class="touch"/>',
                _svg_text(574, y + 24, _format_date(record.latest_commit_at), "date", text_anchor="end"),
            ]
        )

    returned_value = (
        f"{summary.returned_to.name} · after "
        f"{_format_span(summary.returned_to.previous_commit_at, summary.returned_to.latest_commit_at)} quiet"
        if summary.returned_to
        else "nothing old reopened lately"
    )
    summary_rows = [
        ("recently disturbed", f"{summary.recently_disturbed.name} · {_format_date(summary.recently_disturbed.latest_commit_at)}"),
        ("longest kept", f"{summary.longest_kept.name} · {_format_span(summary.longest_kept.created_at, summary.longest_kept.latest_commit_at)}"),
        ("returned to", returned_value),
    ]
    summaries: list[str] = []
    for index, (label, value) in enumerate(summary_rows):
        y = 426 + index * 36
        summaries.append(_svg_text(230, y, label, "summary-label", text_anchor="end"))
        summaries.append(_svg_text(244, y, value, "summary-value"))

    title = "recently disturbed public artifact cabinet"
    description = (
        "Five curated public repositories shown from creation to their latest default-branch commit. "
        f"Most recent: {summary.recently_disturbed.name}. Longest kept: {summary.longest_kept.name}."
    )

    return f'''<svg xmlns="{SVG_NAMESPACE}" width="600" height="540" viewBox="0 0 600 540" role="img" aria-labelledby="title desc">
  <title id="title">{escape(title)}</title>
  <desc id="desc">{escape(description)}</desc>
  <defs>
    <pattern id="grid" width="36" height="36" patternUnits="userSpaceOnUse">
      <path d="M36 0H0V36" fill="none" stroke="{colors['grid']}" stroke-width="1" opacity="0.48"/>
    </pattern>
    <linearGradient id="wash" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{colors['accent']}" stop-opacity="0.08"/>
      <stop offset="0.48" stop-color="{colors['surface']}" stop-opacity="0"/>
      <stop offset="1" stop-color="{colors['signal']}" stop-opacity="0.06"/>
    </linearGradient>
  </defs>
  <style>
    text {{ font-family: "IBM Plex Mono", "Cascadia Mono", Consolas, monospace; font-weight: 400; }}
    .section {{ fill: {colors['accent']}; font-size: 22px; letter-spacing: 1.8px; }}
    .scope {{ fill: {colors['muted']}; font-size: 20px; }}
    .accession {{ fill: {colors['accent']}; font-size: 20px; }}
    .name {{ fill: {colors['text']}; font-size: 22px; }}
    .meta, .date {{ fill: {colors['muted']}; font-size: 20px; }}
    .lifespan {{ stroke: {colors['line']}; stroke-width: 3; stroke-linecap: round; }}
    .origin {{ fill: {colors['accent']}; }}
    .touch {{ fill: {colors['signal']}; stroke: {colors['bg']}; stroke-width: 2; }}
    .summary-label {{ fill: {colors['faint']}; font-size: 20px; }}
    .summary-value {{ fill: {colors['soft']}; font-size: 20px; }}
  </style>
  <rect width="600" height="540" fill="{colors['bg']}"/>
  <rect x="1" y="1" width="598" height="538" fill="none" stroke="{colors['line']}"/>
  <rect x="18" y="18" width="564" height="504" fill="url(#grid)" stroke="{colors['line']}" opacity="0.92"/>
  <rect x="18" y="18" width="564" height="504" fill="url(#wash)"/>
  <path d="M36 54V34H56 M564 54V34H544 M36 500V522H56 M564 500V522H544" fill="none" stroke="{colors['accent']}" opacity="0.52"/>
  {_svg_text(42, 62, '02 / recently disturbed', 'section')}
  {_svg_text(42, 94, 'public repositories · refreshed weekly', 'scope')}
  <line x1="42" x2="558" y1="112" y2="112" stroke="{colors['line']}"/>
  {''.join(rows)}
  <line x1="42" x2="558" y1="394" y2="394" stroke="{colors['line']}"/>
  {''.join(summaries)}
</svg>
'''


class GitHubClient:
    def __init__(self, owner: str, token: str | None = None, timeout: float = 15.0):
        if not owner:
            raise CabinetError("GitHub owner is required")
        self.owner = owner
        self.token = token
        self.timeout = timeout

    def _get_json(self, path: str) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ishyv-artifact-cabinet",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(f"{GITHUB_API}{path}", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise CabinetError(f"GitHub API returned {exc.code} for {path}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise CabinetError(f"GitHub API request failed for {path}: {exc}") from exc
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CabinetError(f"GitHub API returned invalid JSON for {path}") from exc

    def fetch_artifact(self, config: ArtifactConfig) -> RepositoryRecord:
        slug = urllib.parse.quote(config.slug, safe="")
        owner = urllib.parse.quote(self.owner, safe="")
        repository = self._get_json(f"/repos/{owner}/{slug}")
        if not isinstance(repository, dict):
            raise CabinetError(f"GitHub repository response is invalid for {config.slug}")
        branch = _required_string(repository, "default_branch")
        query = urllib.parse.urlencode({"sha": branch, "per_page": 2})
        commits = self._get_json(f"/repos/{owner}/{slug}/commits?{query}")
        if not isinstance(commits, list):
            raise CabinetError(f"GitHub commits response is invalid for {config.slug}")
        return normalize_repository(config, repository, commits)


def load_manifest(path: Path) -> tuple[str, int, list[ArtifactConfig]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CabinetError(f"cannot read cabinet manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CabinetError("cabinet manifest must be an object")
    owner = payload.get("owner")
    threshold = payload.get("return_after_days", 120)
    artifacts_payload = payload.get("artifacts")
    if not isinstance(owner, str) or not owner.strip():
        raise CabinetError("cabinet owner must be a non-empty string")
    if not isinstance(threshold, int) or threshold <= 0:
        raise CabinetError("return_after_days must be a positive integer")
    if not isinstance(artifacts_payload, list) or not artifacts_payload:
        raise CabinetError("cabinet artifacts must be a non-empty list")

    artifacts: list[ArtifactConfig] = []
    for index, item in enumerate(artifacts_payload):
        if not isinstance(item, dict):
            raise CabinetError(f"artifact {index} must be an object")
        accession, slug = item.get("accession"), item.get("slug")
        if not isinstance(accession, str) or not accession.strip():
            raise CabinetError(f"artifact {index} has no accession")
        if not isinstance(slug, str) or not slug.strip():
            raise CabinetError(f"artifact {index} has no slug")
        artifacts.append(ArtifactConfig(accession.strip(), slug.strip()))
    if len({artifact.accession for artifact in artifacts}) != len(artifacts):
        raise CabinetError("cabinet accessions must be unique")
    if len({artifact.slug for artifact in artifacts}) != len(artifacts):
        raise CabinetError("cabinet slugs must be unique")
    return owner.strip(), threshold, artifacts


def records_from_fixture(path: Path, artifacts: Sequence[ArtifactConfig]) -> list[RepositoryRecord]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload["artifacts"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CabinetError(f"cannot read fixture {path}: {exc}") from exc
    if not isinstance(items, list):
        raise CabinetError("fixture artifacts must be a list")
    by_slug = {item.get("slug"): item for item in items if isinstance(item, dict)}
    records: list[RepositoryRecord] = []
    for artifact in artifacts:
        item = by_slug.get(artifact.slug)
        if not isinstance(item, dict):
            raise CabinetError(f"fixture is missing {artifact.slug}")
        records.append(normalize_repository(artifact, item.get("repository", {}), item.get("commits", [])))
    return records


def write_outputs(output_dir: Path, rendered: Mapping[str, str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary: list[tuple[Path, Path]] = []
    try:
        for theme, svg in rendered.items():
            final_path = output_dir / f"cabinet-trace-{theme}.svg"
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="\n", delete=False, dir=output_dir, suffix=".tmp"
            ) as handle:
                handle.write(svg)
                temporary.append((Path(handle.name), final_path))
        for temp_path, final_path in temporary:
            os.replace(temp_path, final_path)
    finally:
        for temp_path, _ in temporary:
            temp_path.unlink(missing_ok=True)


def generate(
    manifest_path: Path,
    output_dir: Path,
    token: str | None = None,
    fixture_path: Path | None = None,
) -> list[RepositoryRecord]:
    owner, threshold, artifacts = load_manifest(manifest_path)
    if fixture_path:
        records = records_from_fixture(fixture_path, artifacts)
    else:
        client = GitHubClient(owner=owner, token=token)
        records = [client.fetch_artifact(artifact) for artifact in artifacts]
    validate_chronology(records)
    summary = derive_summary(records, return_after_days=threshold)
    rendered = {theme: render_svg(records, summary, theme) for theme in THEMES}
    write_outputs(output_dir, rendered)
    return records


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("cabinet.json"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fixture", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        records = generate(
            manifest_path=args.manifest,
            output_dir=args.output_dir,
            token=os.environ.get("GITHUB_TOKEN"),
            fixture_path=args.fixture,
        )
    except CabinetError as exc:
        print(f"cabinet generation stopped: {exc}", file=sys.stderr)
        return 1
    print(f"rendered {len(records)} complete artifacts into {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
