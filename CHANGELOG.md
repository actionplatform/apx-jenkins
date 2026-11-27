# Changelog

## Unreleased

### Features
- **runner:** a log read never goes past its window (256 KB by default): the console is read window by window, never whole
- **runner:** the Jenkins CIRunner, moved from the core and registered on the `action_platform.ci_runner` entry point, with stages, logs from an offset, tests, artifacts, the queue, executors and pending inputs

### Breaking changes
- needs action-platform 0.38 or later, whose core no longer carries a Jenkins runner
- `jenkins_build` and `action-platform jenkins build` are aliases of `ci_run` / `ci run`: they answer the run with its parameters, causes and changes, and no longer the console; read it with `ci_log` / `ci log` (the `lines` argument is gone)
- `jenkins_queue` and `action-platform jenkins queue` are aliases of `ci_queue` / `ci queue`: each item is `job`, `since` and `why`

## v0.1.0 — 2026-11-24

### Features
- **plugin:** jenkins plugin with jenkins_* tools and commands
- **access:** Jenkins access provider on role-strategy
- **client:** read jobs, builds and the queue through the JSON API

### Bug Fixes
- **access:** compare global admins without case in guard

### Docs
- AGENTS.md for apx-jenkins
- **readme:** access, tools, options and development

### Tests
- **live:** read-only suite against a real controller

### Chores
- package metadata for apx-jenkins
- remove the template's example plugin, spec and manifests
