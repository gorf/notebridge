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
   This produces `notebridge-x.y.z.tar.gz` and `notebridge-x.y.z-py3-none-any.whl` under `dist/`.

3. **Upload to PyPI**  
   ```bash
   twine upload dist/*
   ```
   Enter your PyPI username and password (or token) when prompted.  
   Use `twine upload dist/*` for the first release; the same version cannot be uploaded twice.

4. **(Optional) API token**  
   Create an API token in your PyPI account. When uploading, use username `__token__` and the token as password to avoid using your account password.

## Automated publish with GitHub Actions

You can build and upload to PyPI on tag push:

1. Create an API token on PyPI.
2. Add `PYPI_API_TOKEN` under GitHub repo Settings → Secrets.
3. Add a workflow (e.g. `.github/workflows/publish.yml`) that runs `twine upload` on release/publish using `PYPI_API_TOKEN`.

You can add the full workflow when needed.
