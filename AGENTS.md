# AGENTS.md

Rules an AI agent (or a new contributor) follows in this repository.

## What this is

`apx-jenkins`, an Action Platform plugin: the access provider for Jenkins on Role-based Authorization Strategy (`access.py`), and `jenkins_*` tools and `action-platform jenkins` commands that read jobs, builds and the queue. The contract every plugin follows is `SPEC.md` in [apx-example](https://github.com/actionplatform/apx-example); read it before changing the shape of anything here.

## Code

- Python 3.11+, `ruff check . && ruff format --check .`, `pytest` green before a commit.
- No comments inside function bodies; no test docstrings. Module docstrings say what the module is for.
- `register` declares only: no I/O at import or in `register`. `access(options)` reads only `options`.
- Never monkey-patch `action_platform.*`.
- Talk to Jenkins through `client.JenkinsHttp` only; tests drive `tests/fake.py`, never the network. The fake answers only what Jenkins answers.
- Tools and commands are read-only. `tests/live/` is read-only too and runs only with `AP_JENKINS_LIVE=1` and `AP_JENKINS_URL`, `AP_JENKINS_USER`, `AP_JENKINS_TOKEN` set.
- Never print or commit a credential or a host.

## Commits and branches

[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/): `type(scope): description`; one commit per concern; stage files explicitly. Git-flow: `<kind>/<issue>-<slug>` from `master`, kinds `feature bugfix hotfix release chore docs refactor test ci perf`; never commit on `master`. One pull request per issue.

## Release

`action-platform release <level>`; the tag publishes to PyPI; then a PR to `plugins-index` with the new `latest`.
