# Creating the private GitHub repository

`tools/release/publish_private.py` creates one new private repository under a personal account and pushes `main` to it. It is the only publication path, and no test or build performs a real publication.

```sh
python tools/release/publish_private.py SamMausberg/cairn
python tools/release/publish_private.py SamMausberg/cairn --execute
```

The first command is a local-only dry run. The second needs an already authenticated official `gh` CLI with permission to create a private repository under that same personal account. The script never asks for a pasted token, installs an authentication helper, or changes global credentials. Authenticate through your normal trusted GitHub CLI flow before running it.

## Before it will run

The source tree must be clean, on `main`, with no configured remote. A clone of the supplied Git bundle may carry an origin pointing at that bundle; remove that local-only remote first, and never remove a real GitHub remote to bypass its protection. The scan reads all reachable committed history, not the latest files alone. A source ZIP has no Git history, so publish from the repository ZIP or the bundle.

## The sequence

Local audit, identity check, private creation, identity and privacy read-back, exact-commit non-force push, final privacy and commit read-back, then local `origin` registration. Git hooks are disabled for the push, and credential-helper selection is command-scoped. Creation is the collision check: if the name already exists the script stops without modifying that repository. The repository is never intentionally public, even temporarily. No force, delete, reuse-existing, visibility-change or public-fallback mode is implemented.

## What can still go wrong

A failure after creation can leave an empty private repository. The script does not delete it and does not weaken a check to recover. A failure after the push may leave private code uploaded; inspect the reported account and repository through GitHub before trying anything else. Privacy checks cannot prevent an administrator changing visibility concurrently or later, and there is no absolute guarantee against a compromised client, service or local Git configuration.

The automated tests use fakes for every remote interaction. A passing fake test shows that the orchestration takes its expected branches. It does not show that your future account permissions or a GitHub request will succeed. No live publication test ran here.

## What has been published

`evidence/v1_0/RUN_NOTES.md` records that after the 1.0 evidence was collected, `main` and the release tags were pushed at the owner's request to a new private GitHub repository whose privacy was verified before and after the upload. No remote workflow has run: the GitHub workflow is manual-only, private-repository guarded and read-only. No license has been selected and nothing is published for reuse.
