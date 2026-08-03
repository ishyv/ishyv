from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_cabinet import (  # noqa: E402
    ArtifactConfig,
    CabinetError,
    GitHubClient,
    derive_summary,
    generate,
    normalize_repository,
    render_svg,
    validate_chronology,
)


FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "cabinet-api.json").read_text(encoding="utf-8"))


def fixture_records():
    return [
        normalize_repository(
            ArtifactConfig(accession=item["accession"], slug=item["slug"]),
            item["repository"],
            item["commits"],
        )
        for item in FIXTURE["artifacts"]
    ]


class NormalizeRepositoryTests(unittest.TestCase):
    def test_preserves_manifest_order_and_normalizes_utc_dates(self):
        records = fixture_records()

        self.assertEqual([record.accession for record in records], [f"A-0{i}" for i in range(1, 6)])
        self.assertEqual(records[0].latest_commit_at, datetime(2026, 1, 12, 3, 57, tzinfo=UTC))
        self.assertEqual(records[0].created_at.tzinfo, UTC)

    def test_rejects_a_repository_name_that_does_not_match_the_manifest(self):
        item = FIXTURE["artifacts"][0]
        payload = {**item["repository"], "name": "something-else"}

        with self.assertRaisesRegex(CabinetError, "repository name mismatch"):
            normalize_repository(ArtifactConfig("A-01", "darkh-bot"), payload, item["commits"])

    def test_rejects_missing_or_malformed_commit_dates(self):
        item = FIXTURE["artifacts"][0]

        with self.assertRaisesRegex(CabinetError, "two commits"):
            normalize_repository(ArtifactConfig("A-01", "darkh-bot"), item["repository"], item["commits"][:1])

        malformed = [{"commit": {"committer": {"date": "yesterday"}}}, item["commits"][1]]
        with self.assertRaisesRegex(CabinetError, "invalid GitHub timestamp"):
            normalize_repository(ArtifactConfig("A-01", "darkh-bot"), item["repository"], malformed)

    def test_uses_an_explicit_fallback_when_github_has_no_primary_language(self):
        item = FIXTURE["artifacts"][0]
        payload = {**item["repository"], "language": None}

        record = normalize_repository(ArtifactConfig("A-01", "darkh-bot"), payload, item["commits"])

        self.assertEqual(record.language, "unclassified")

    def test_rejects_any_repository_not_explicitly_reported_as_public(self):
        item = FIXTURE["artifacts"][0]
        private_payload = {**item["repository"], "private": True, "visibility": "private"}

        with self.assertRaisesRegex(CabinetError, "not public"):
            normalize_repository(
                ArtifactConfig("A-01", "darkh-bot"), private_payload, item["commits"]
            )

    def test_rejects_commits_that_predate_repository_creation(self):
        item = FIXTURE["artifacts"][0]
        impossible = {**item["repository"], "created_at": "2027-01-01T00:00:00Z"}

        with self.assertRaisesRegex(CabinetError, "predates repository creation"):
            normalize_repository(
                ArtifactConfig("A-01", "darkh-bot"), impossible, item["commits"]
            )

    def test_rejects_a_manifest_that_is_not_chronological(self):
        records = fixture_records()

        with self.assertRaisesRegex(CabinetError, "chronological"):
            validate_chronology([records[1], records[0], *records[2:]])


class DeriveSummaryTests(unittest.TestCase):
    def test_finds_recent_longest_kept_and_latest_return_after_120_days(self):
        summary = derive_summary(fixture_records(), return_after_days=120)

        self.assertEqual(summary.recently_disturbed.slug, "revenant")
        self.assertEqual(summary.longest_kept.slug, "darkh-bot")
        self.assertEqual(summary.returned_to.slug, "darkh-bot")

    def test_returned_to_is_empty_when_no_commit_gap_reaches_threshold(self):
        records = fixture_records()[1:]

        summary = derive_summary(records, return_after_days=120)

        self.assertIsNone(summary.returned_to)

    def test_rejects_an_empty_cabinet(self):
        with self.assertRaisesRegex(CabinetError, "at least one artifact"):
            derive_summary([])


class RenderSvgTests(unittest.TestCase):
    def test_renders_well_formed_pure_svg_with_accessible_metadata(self):
        records = fixture_records()
        svg = render_svg(records, derive_summary(records), theme="dark")
        root = ET.fromstring(svg)

        self.assertEqual(root.attrib["viewBox"], "0 0 600 540")
        self.assertEqual(root.attrib["role"], "img")
        self.assertNotIn("foreignObject", svg)
        self.assertNotIn("<script", svg)
        self.assertIn("public repositories · refreshed weekly", svg)
        self.assertIn("recently disturbed", svg)
        self.assertIn("revenant · 06 jul 2026", svg)
        self.assertIn("longest kept", svg)
        self.assertIn("darkh-bot · 4y 6m", svg)

    def test_escapes_repository_names_and_text(self):
        records = fixture_records()
        hostile = records[0].with_name('darkh<&"bot')
        svg = render_svg([hostile, *records[1:]], derive_summary([hostile, *records[1:]]), theme="light")

        ET.fromstring(svg)
        self.assertIn("darkh&lt;&amp;&quot;bot", svg)
        self.assertNotIn('darkh<&"bot', svg)

    def test_renders_the_empty_return_state(self):
        records = fixture_records()[1:]
        svg = render_svg(records, derive_summary(records), theme="dark")

        self.assertIn("nothing old reopened lately", svg)

    def test_semantic_and_geometry_snapshot(self):
        records = fixture_records()
        svg = render_svg(records, derive_summary(records), theme="dark")
        root = ET.fromstring(svg)
        namespace = {"svg": "http://www.w3.org/2000/svg"}
        texts = ["".join(node.itertext()) for node in root.findall(".//svg:text", namespace)]
        lines = [
            (line.attrib["x1"], line.attrib["x2"], line.attrib["y1"])
            for line in root.findall(".//svg:line[@data-lifespan]", namespace)
        ]

        self.assertEqual(
            texts[:8],
            [
                "02 / recently disturbed",
                "public repositories · refreshed weekly",
                "A-01",
                "darkh-bot",
                "Python · 2021",
                "12 jan 2026",
                "A-02",
                "lang",
            ],
        )
        self.assertEqual(
            lines,
            [
                ("250.00", "521.14", "140"),
                ("433.69", "539.82", "194"),
                ("501.19", "550.00", "248"),
                ("531.26", "544.10", "302"),
                ("545.71", "549.51", "356"),
            ],
        )


class GitHubClientTests(unittest.TestCase):
    def test_api_failure_is_wrapped_and_never_returns_partial_data(self):
        client = GitHubClient(owner="ishyv", token="test-token")

        with patch.object(client, "_get_json", side_effect=CabinetError("GitHub API returned 403")):
            with self.assertRaisesRegex(CabinetError, "403"):
                client.fetch_artifact(ArtifactConfig("A-01", "darkh-bot"))


class GenerateIntegrationTests(unittest.TestCase):
    def test_fixture_generation_writes_both_parseable_themes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)

            records = generate(
                ROOT / "cabinet.json",
                output_dir,
                fixture_path=ROOT / "tests" / "fixtures" / "cabinet-api.json",
            )

            self.assertEqual([record.slug for record in records], [
                "darkh-bot", "lang", "revenant", "hyvui", "ashenmoon"
            ])
            for theme in ("dark", "light"):
                output = output_dir / f"cabinet-trace-{theme}.svg"
                self.assertTrue(output.is_file())
                ET.parse(output)

    def test_failed_fetch_preserves_existing_known_good_outputs(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            output_dir.joinpath("cabinet-trace-dark.svg").write_text("known-good-dark", encoding="utf-8")
            output_dir.joinpath("cabinet-trace-light.svg").write_text("known-good-light", encoding="utf-8")

            with patch.object(GitHubClient, "fetch_artifact", side_effect=CabinetError("rate limited")):
                with self.assertRaisesRegex(CabinetError, "rate limited"):
                    generate(ROOT / "cabinet.json", output_dir, token="test-token")

            self.assertEqual(
                output_dir.joinpath("cabinet-trace-dark.svg").read_text(encoding="utf-8"),
                "known-good-dark",
            )
            self.assertEqual(
                output_dir.joinpath("cabinet-trace-light.svg").read_text(encoding="utf-8"),
                "known-good-light",
            )


class PublishWorkflowTests(unittest.TestCase):
    def test_first_publish_noop_refresh_changed_refresh_and_failed_input(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is required for the generated-branch integration test")

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            remote = root / "remote.git"
            repository = root / "repository"
            assets = root / "assets"
            missing_assets = root / "missing"
            script = ROOT / "scripts" / "publish_generated.sh"

            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repository, check=True)
            repository.joinpath("README.md").write_text("source", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repository, check=True, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repository, check=True)

            assets.mkdir()
            assets.joinpath("cabinet-trace-dark.svg").write_text("dark-v1", encoding="utf-8")
            assets.joinpath("cabinet-trace-light.svg").write_text("light-v1", encoding="utf-8")

            command = [bash, script.as_posix(), assets.as_posix()]
            subprocess.run(command, cwd=repository, check=True, capture_output=True, text=True)
            first_count = self._branch_commit_count(remote)
            self.assertEqual(first_count, 1)
            self.assertEqual(self._branch_file(remote, "cabinet-trace-dark.svg"), "dark-v1")

            subprocess.run(command, cwd=repository, check=True, capture_output=True, text=True)
            self.assertEqual(self._branch_commit_count(remote), first_count)

            assets.joinpath("cabinet-trace-dark.svg").write_text("dark-v2", encoding="utf-8")
            subprocess.run(command, cwd=repository, check=True, capture_output=True, text=True)
            self.assertEqual(self._branch_commit_count(remote), first_count + 1)
            self.assertEqual(self._branch_file(remote, "cabinet-trace-dark.svg"), "dark-v2")

            failed = subprocess.run(
                [bash, script.as_posix(), missing_assets.as_posix()],
                cwd=repository,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(self._branch_commit_count(remote), first_count + 1)

    @staticmethod
    def _branch_commit_count(remote: Path) -> int:
        result = subprocess.run(
            ["git", f"--git-dir={remote}", "rev-list", "--count", "generated"],
            check=True,
            capture_output=True,
            text=True,
        )
        return int(result.stdout.strip())

    @staticmethod
    def _branch_file(remote: Path, name: str) -> str:
        result = subprocess.run(
            ["git", f"--git-dir={remote}", "show", f"generated:{name}"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout


if __name__ == "__main__":
    unittest.main()
