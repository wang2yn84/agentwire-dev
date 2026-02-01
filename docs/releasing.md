# Releasing agentwire-dev

This project publishes the CLI and web app to PyPI as `agentwire-dev`. Packaging uses Hatchling via `pyproject.toml`.

## Manual release (uv)

1) Bump version
- Edit `agentwire/__init__.py` and update `__version__`.
- Add an entry to `CHANGELOG.md` for the new version.

2) Build artifacts
```bash
uv venv
source .venv/bin/activate
uv pip install --upgrade build twine
rm -rf dist/ build/
python -m build
```

3) Inspect contents (recommended)
- sdist: `tar -tvf dist/agentwire-dev-<ver>.tar.gz | less`.
- wheel: `unzip -l dist/agentwire_dev-<ver>-py3-none-any.whl | less`.
- Verify `agentwire/templates/` and `agentwire/static/` are included.

4) Validate metadata
```bash
```

5) Publish
- TestPyPI: `twine upload -r testpypi dist/*`.
- PyPI: `twine upload dist/*`.

6) Tag release
- `git tag v<ver> && git push origin v<ver>`.

## Notes

- Package name: `agentwire-dev` (import package is `agentwire`).
- Python: >=3.10 as declared in `pyproject.toml`.
- Build backend: Hatchling; no `setup.py` is required.
