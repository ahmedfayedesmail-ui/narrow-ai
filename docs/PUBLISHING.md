# Python package publishing

NARROW v0.5.0 is currently published as a GitHub Alpha release. PyPI publication is deliberately separate and uses PyPI Trusted Publishing (GitHub OIDC); no long-lived PyPI API token is stored in GitHub.

## Current distribution status

- GitHub source release: published as `v0.5.0`.
- Python distributions: built and validated in CI; not yet published to PyPI.
- PyPI project name: `narrow-ai`.
- The exact project JSON endpoints on PyPI and TestPyPI returned 404 when checked on 2026-11-03. Recheck availability immediately before the first publish.

## One-time publisher configuration

Configure a Trusted Publisher on each index. These are account-owner steps and cannot be completed through the repository's GitHub connection.

### TestPyPI first

1. Sign in to TestPyPI.
2. Create a pending publisher for a new project.
3. Create a **pending publisher** for a new project named `narrow-ai`. Pending publishers do not reserve the name until the first successful upload, so register it immediately before testing the publish.
4. Use these exact GitHub Actions values:
   - Owner: `ahmedfayedesmail-ui`
   - Repository: `narrow-ai`
   - Workflow: `publish-pypi.yml`
   - Environment: `testpypi`
5. Save the pending publisher.

### PyPI production

After the TestPyPI upload and install smoke test succeeds, sign in to PyPI and create a pending publisher for the new `narrow-ai` project with:
   - Owner: `ahmedfayedesmail-ui`
   - Repository: `narrow-ai`
   - Workflow: `publish-pypi.yml`
   - Environment: `pypi`

Do not configure a publisher against a different repository, workflow, or environment. Trusted Publishing uses short-lived OIDC credentials instead of a saved API token.

Official setup guides:
- PyPI Trusted Publishing: https://docs.pypi.org/trusted-publishers/
- Creating a new PyPI project with a Trusted Publisher: https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/

## Publish procedure

1. Open the repository's Actions tab.
2. Select **Publish Python package**.
3. Choose the existing tag, initially `v0.5.0`.
4. Choose `testpypi` first and run the workflow.
5. Confirm the workflow builds both an sdist and wheel, passes `twine check --strict`, installs the wheel in a clean virtual environment, and publishes successfully.
6. Install from TestPyPI in a clean environment and run `narrow --version`.
7. Only after that succeeds, repeat with target `pypi`.

The workflow verifies that the selected tag exactly matches `project.version`, builds from that tag, and separates unprivileged building from the OIDC publishing job. The publishing job receives only artifact-read and OIDC token permissions.

## Install after a successful PyPI publication

```bash
python -m pip install narrow-ai
narrow --version
```

Until the PyPI release is confirmed, use the source-install instructions in the main README.
