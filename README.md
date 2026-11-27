# apx-jenkins

[Jenkins](https://www.jenkins.io) for [Action Platform](https://github.com/actionplatform/action-platform): the CI runner the platform deploys and runs CI through, access management through Role-based Authorization Strategy, and read-only tools for jobs, builds and the build queue.

The core does not know Jenkins: this plugin brings the Jenkins `CIRunner`, registered in the `action_platform.ci_runner` entry-point group, and the core resolves it by name wherever `run_by = "jenkins"` or a Jenkins CI host appears. The access capability runs where the core runs service access: on a hosted platform whose API image carries this plugin, with the options filled under **Plugins** for the organization. The tools and commands run on the machine that installed it.

## Install

```bash
action-platform plugin install jenkins
```

Then fill the options under Plugins, Configure on the platform, or on a machine either export the `AP_JENKINS_*` variables or store the options (`~/.action-platform/plugins/jenkins.json`).

| Option | Environment variable | What |
|---|---|---|
| `url` | `AP_JENKINS_URL` | The controller's address, e.g. `https://jenkins.example.com` |
| `user` | `AP_JENKINS_USER` | The Jenkins user id the token belongs to |
| `token` | `AP_JENKINS_TOKEN` | That user's API token (user menu, Security, API Token) |

On a machine the tools, the commands and the core's `ci_*` tools read the environment variable first and the options file second. The access capability reads only the options, so a platform keeps the credentials sealed.

### What the token needs

- CI runner: read on the jobs it follows, and Item/Build on the jobs a deploy or Run CI starts.
- Tools and commands: the user must be able to read the jobs, builds and queue it asks for. They never write.
- Access: the user must be a Jenkins administrator (Overall/Administer), the [Role-based Authorization Strategy](https://plugins.jenkins.io/role-strategy/) plugin installed, and selected under Manage Jenkins, Security, Authorization. Without it the provider answers with what to install.

## Access

The plugin gives the platform's [service access](https://github.com/actionplatform/action-platform/blob/master/docs/concept_service_access.md) a `jenkins` service: who holds which role, drift against the organization's mapping, and grants, revokes and syncs. Accounts are Jenkins user ids; `group:<name>` addresses a group.

| Resource | Levels | What it is |
|---|---|---|
| `global` | `read`, `admin` | A global role named `read` (Overall/Read, View/Read) or `admin` (Overall/Administer) |
| `folder/<name>` | `read`, `build`, `configure` | An item role `folder:<name>:<level>` matching the folder and every job in it |
| `global-role/<name>` | `assigned` | A global role this plugin did not create |
| `item-role/<name>` | `assigned` | An item role this plugin did not create, such as one that opens a single job |

Item roles hold these permissions, each level including the one before it:

| Level | Permissions |
|---|---|
| `read` | Item/Read, Item/Discover |
| `build` | `read` + Item/Build, Item/Cancel, Item/Workspace |
| `configure` | `build` + Item/Configure, Item/Create, Item/Delete, Run/Delete, Run/Update |

- A folder is invisible without Overall/Read, so a folder grant also adds `global read` to the account.
- A role the plugin needs is created on the first grant; a change of level assigns the new role and unassigns the old one.
- Roles it did not create are read as `global-role/<name>` and `item-role/<name>` with level `assigned`, so an import records them and a sync leaves them alone.
- An administrator can never be the last one: a change that would leave Jenkins without a global `admin` is refused before anything is sent.
- Older versions of role-strategy without the user and group assignment calls are served through the generic `assignRole`.

## CI runner

`apx_jenkins.runner.JenkinsRunner` implements the core's `CIRunner` contract on the controller's own API:

| Method | Jenkins |
|---|---|
| `test` | `api/json?tree=mode` |
| `runs`, `run` | `api/json` with `builds[…]`: status, branch and revision, `parameters`, `causes` (with the user id), `changeSets` |
| `start`, `run_of` | `buildWithParameters` (or `build`) with the crumb, then the queue item until it becomes a build; a cancelled item is an error |
| `stages` | `wfapi/describe`; parallel stages are listed as stages, `parent_id` stays empty since the endpoint does not say which stage holds them |
| `log`, `log_stream` | `logText/progressiveText?start=` for the console, read no further than the window (`limit`, 256 KB by default) and cut after a line break, `next_start` and `more` from `X-Text-Size` and `X-More-Data`; `execution/node/{id}/wfapi/log` for one stage or step, windowed the same way. `log_stream` walks the console window by window without holding it |
| `tests` | `testReport/api/json`; none when the build published no report |
| `artifacts`, `artifact` | `artifacts[relativePath]` and `artifact/<path>`, streamed |
| `queue`, `executors` | `queue/api/json`, `computer/api/json` |
| `pending_inputs` | `wfapi/pendingInputActions` |

## Tools

The core's `ci_runs`, `ci_run`, `ci_log` and `ci_queue` tools (and `action-platform ci …`) read Jenkins through the runner. This plugin adds `jenkins_*` tools and `action-platform jenkins` commands. All are read-only.

| Tool | Command | Does |
|---|---|---|
| `jenkins_jobs` | `jenkins jobs [folder]` | The jobs of a folder, or of the root: kind, status and the last build's number, result, time and duration. Pass a job path to read that one job. |
| `jenkins_build` | `jenkins build <job> [number]` | Alias of `ci_run`: one build, the last when no number is given, with its parameters, causes and changes. The console is `ci_log`. |
| `jenkins_queue` | `jenkins queue` | Alias of `ci_queue`: what waits in the build queue, since when and why. |

A job is addressed by the path Jenkins shows in its URL without the `job/` segments: `team/app`, `team/app/main`.

## Develop

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest -q
```

`tests/fake.py` is a controller in memory that answers only what Jenkins answers. `tests/live/` runs read-only against a real controller and is skipped unless `AP_JENKINS_LIVE=1` and `AP_JENKINS_URL`, `AP_JENKINS_USER` and `AP_JENKINS_TOKEN` are set; it changes no role and starts no build.

Rules for contributors and agents are in `AGENTS.md`.
