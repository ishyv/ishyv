# Setup

The profile uses one repository-owned generator. It reads public metadata for the five repositories in [`cabinet.json`](cabinet.json), renders two theme-aware SVGs, and publishes them to the `generated` branch.

## First deployment

1. Push the profile source to the `main` branch.
2. Open **Actions → cabinet trace → Run workflow**.
3. Confirm the workflow creates the `generated` branch with:
   - `cabinet-trace-dark.svg`
   - `cabinet-trace-light.svg`
4. Reload the profile after GitHub's image cache refreshes.

The workflow also runs every Sunday. It commits only when repository facts change, so an idle cabinet does not generate history noise.

## Local verification

```bash
python -m unittest discover -s tests -v
python scripts/generate_cabinet.py --manifest cabinet.json --fixture tests/fixtures/cabinet-api.json --output-dir build/cabinet
```

For a live public-data smoke test, omit `--fixture`. `GITHUB_TOKEN` is optional locally and helps avoid anonymous API limits:

```bash
python scripts/generate_cabinet.py --manifest cabinet.json --output-dir build/cabinet
```

Set `GITHUB_TOKEN` in your shell first when authenticated requests are needed.

Only public repository metadata is read. The profile does not use a personal access token, private contribution data, third-party metric renderers, or remote fonts.
