# Maintainer: Publishing to PyPI

## Prerequisites

- Register at [PyPI](https://pypi.org) and create the project (if not already created).
- Install build and upload tools: `pip install build twine`.

## Release steps

1. **Bump version**  
   Edit `version = "x.y.z"` in `pyproject.toml`.

2. **Build locally**  
   From the project root:
   ```bash
   python -m build
   ```
   This produces `joplin_obsidian_bridge-x.y.z.tar.gz` and `joplin_obsidian_bridge-x.y.z-py3-none-any.whl` under `dist/`.

3. **Upload to PyPI**  
   ```bash
   twine upload dist/*
   ```
   Enter your PyPI username and password (or token) when prompted.  
   Use `twine upload dist/*` for the first release; the same version cannot be uploaded twice.

4. **(Optional) API token**  
   Create an API token in your PyPI account. When uploading, use username `__token__` and the token as password to avoid using your account password.

## Automated publish with GitHub Actions

The repo includes `.github/workflows/publish.yml`. It runs when you **publish a GitHub Release** (or run it manually from the Actions tab).

**One-time setup**

1. Create an API token on [PyPI](https://pypi.org/manage/account/token/) (scope: entire account or this project).
2. In your GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**.
3. Name: `PYPI_API_TOKEN`, Value: paste the token (starts with `pypi-`).

**Release flow**

1. Bump `version` in `pyproject.toml` (e.g. `0.2.0` → `0.3.0`).
2. Commit, push to `main`.
3. On GitHub: **Releases → Create a new release**; choose a tag (e.g. `v0.3.0`), publish.
4. The **Publish to PyPI** workflow runs and uploads the new version.

You can also trigger it manually: **Actions → Publish to PyPI → Run workflow**.
