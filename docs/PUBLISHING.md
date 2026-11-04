# Package publishing

NARROW 1.0.0 is the current source release. GitHub source releases and Python package-index publication are separate distribution steps. PyPI publication uses Trusted Publishing (GitHub OIDC); no long-lived PyPI API token is stored in GitHub.

## GitHub release

The **Publish NARROW release** workflow validates the package version, checks that `docs/RELEASE-NOTES-v1.0.0.md` exists, refuses to overwrite an existing tag or release, and creates a normal GitHub release from the current `main` commit.

For version 1.0.0, the manual workflow authorization phrase is:

```text
RELEASE NARROW 1.0.0
```

## Python package publication

The **Publish Python package** workflow builds an sdist and wheel from the selected release tag, validates metadata with `twine check --strict`, installs the wheel in a clean virtual environment, publishes through Trusted Publishing, and verifies installation from the selected index.

Run `testpypi` first, then `pypi` after the TestPyPI upload and installation verification succeed.

### Trusted Publisher configuration

The account owner configures the publisher on each package index. The required GitHub identity is:

- Owner: `ahmedfayedesmail-ui`
- Repository: `narrow-ai`
- Workflow: `publish-pypi.yml`
- Environment: `testpypi` on TestPyPI; `pypi` on PyPI

If the publisher is not already registered, create it in the relevant package-index account before starting the publish workflow. This is a one-time account-level setting; the repository workflow uses short-lived OIDC credentials.

Official setup guides:
- https://docs.pypi.org/trusted-publishers/
- https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/

## Install

After the package has been published to the index:

```bash
python -m pip install --upgrade narrow-ai
narrow --version
```

The GitHub source checkout is also installable directly:

```bash
git clone https://github.com/ahmedfayedesmail-ui/narrow-ai.git
cd narrow-ai
python -m pip install .
```
