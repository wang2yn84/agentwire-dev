Release guide for agentwire-dev

This project publishes the CLI and web app to PyPI as `agentwire-dev`. Packaging uses Hatchling via `pyproject.toml`.

Steps

1) Bump version
- Edit `agentwire/__init__.py` and update `__version__`.
- Add an entry to `CHANGELOG.md` for the new version.

2) Sanity check packaging
- Ensure templates/static are present under `agentwire/templates` and `agentwire/static`.
- Confirm `pyproject.toml` includes them under `[tool.hatch.build.targets.wheel].include`.

3) Build artifacts
- Clean: `rm -rf dist/ build/`.
- Build: `hatch build`  (or `python -m build`).

4) Inspect contents
- sdist: `tar -tvf dist/agentwire-dev-<ver>.tar.gz | less`.
- wheel: `unzip -l dist/agentwire_dev-<ver>-py3-none-any.whl | less`.
- Verify `agentwire/templates/` and `agentwire/static/` are included.

5) Validate metadata
- `twine check dist/*`.

6) Publish
- TestPyPI: `twine upload -r testpypi dist/*`.
- PyPI: `twine upload dist/*`.

7) Tag release
- `git tag v<ver> && git push origin v<ver>`.

Notes
- Package name: `agentwire-dev` (import package is `agentwire`).
- Python: >=3.10 as declared in `pyproject.toml`.
- Build backend: Hatchling; no `setup.py` is required.
