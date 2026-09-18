# Creating the private GitHub repository

## Current delivery status

The GitHub connector resolved the authenticated login as SamMausberg. Its exposed actions did not include repository creation. The container had no GitHub CLI or authenticated token environment. No repository was created, no code was sent to GitHub, and no existing repository or permission was changed. The deliverable is an actual local Git history plus source and a wheel.

## Run on your own authenticated machine

Use the bundled repository, or clone the supplied Git bundle. A bundle clone may add an origin pointing to the local bundle; remove that local-only bundle remote before running the first-publication script. Do not remove an existing real GitHub remote to bypass its protection.

```sh
python3 tools/publish_private.py SamMausberg/cairn
python3 tools/publish_private.py SamMausberg/cairn --execute
```

The first command is a local-only dry run. The second requires an already authenticated official `gh` CLI with permission to create a private repository under that same personal account. The script never asks for a pasted token, installs an authentication helper, or changes global credentials. Authentication must be done through the user's normal trusted GitHub CLI flow before execution.

The source tree must be clean, on main, with no configured remotes. The script scans all reachable committed history, not merely the latest files. It creates only a new repository; if the name already exists, it stops without modifying that repository. A source ZIP alone does not contain Git history; use the repository ZIP or the Git bundle for publication.

The sequence is local audit, identity check, private creation, identity/privacy read-back, exact-commit non-force push, final privacy/commit read-back, then local origin registration. Git hooks are disabled for the push, and credential-helper selection is command-scoped. The repository is never intentionally public, even temporarily. No force, delete, reuse-existing, visibility-change or public-fallback mode is implemented.

A failure after creation can leave an empty private repository. The script does not delete it or weaken the checks to recover. A failure after pushing may leave private code uploaded; inspect the reported account and repository through GitHub before trying anything else. Privacy checks cannot prevent an administrator changing visibility concurrently or later. There is no absolute guarantee against a compromised client, service or local Git configuration.

The supplied automated tests use fakes for remote interactions. A successful fake test proves the orchestration takes its expected branches, not that your future account permissions or GitHub request will succeed. No live publication test ran here.
