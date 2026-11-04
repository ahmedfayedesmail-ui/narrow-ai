# Python package publishing

NARROW v0.5.1 is the legacy pre-Beta packaging release. The earlier v0.5.0 package was successfully exercised on TestPyPI; its source README was older than the current repository documentation, so production publishing will use v0.5.1. PyPI publication is deliberately separate and uses PyPI Trusted Publishing (GitHub OIDC); no long-lived PyPI API token is stored in GitHub.

## Current distribution status

- GitHub source release `v0.5.0`: published.
- TestPyPI `narrow-ai==0.5.0`: upload and clean-index installation succeeded.
- GitHub/PyPI release candidate: `v0.5.1`; publish to TestPyPI first, then PyPI production.
- PyPI project name: `narrow-ai`.
- The `narrow-ai` project now exists on TestPyPI. The production PyPI project was still not present when last checked; the pending publisher configured by the owner will create it on first successful PyPI upload.

## One-time publisher configuration

Configure a Trusted Publisher on each index. These are account-owner steps and cannot be completed through the repository's GitHub connection.

### TestPyPI first

1. Sign in to TestPyPI.
2. Create a **pending publisher** for a new project named `narrow-ai`. Pending publishers do not reserve the name until the first successful upload, so register it immediately before testing the publish.
3. Use these exact GitHub Actions values:
   - Owner: `ahmedfayedesmail-ui`
   - Repository: `narrow-ai`
   - Workflow: `publish-pypi.yml`
   - Environment: `testpypi`
4. Save the pending publisher.

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
3. Choose the corrected release tag, `v0.5.1`.
4. Choose `testpypi` first and run the workflow.
5. Confirm the workflow builds both an sdist and wheel, passes `twine check --strict`, installs the wheel in a clean virtual environment, and publishes successfully.
6. The workflow then installs the exact version from the selected index in a fresh virtual environment and runs `narrow --version`. A green run therefore covers the index upload and the package installation smoke test.
7. Only after the TestPyPI run succeeds, repeat with target `pypi`.

The workflow verifies that the selected tag exactly matches `project.version`, builds from that tag, and separates unprivileged building from the OIDC publishing job. The publishing job receives only artifact-read and OIDC token permissions.

## Install after a successful PyPI publication

```bash
python -m pip install narrow-ai
narrow --version
```

Until the PyPI release is confirmed, use the source-install instructions in the main README.
