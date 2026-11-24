# apx-jenkins

[Jenkins](https://www.jenkins.io) for [Action Platform](https://github.com/actionplatform/action-platform): access management through Role-based Authorization Strategy, and read-only tools for jobs, builds and the build queue.


Until `apx-jenkins` is on PyPI and in the plugin index, install it from its release tag into the CLI's environment (`action-platform plugin install jenkins` works once it is):

```bash
pip install "apx-jenkins @ git+https://github.com/actionplatform/apx-jenkins@v0.1.0"
```

The access capability runs where the core runs service access: on a hosted platform whose API image carries this plugin, with the options filled under **Plugins** for the organization. The tools and commands run on the machine that installed it.

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

On a machine the tools and commands read the environment variable first and the options file second. The access capability reads only the options, so a platform keeps the credentials sealed.

### What the token needs

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

## Tools

MCP tools `jenkins_*` and the same reads under `action-platform jenkins`. All are read-only.

| Tool | Command | Does |
|---|---|---|
| `jenkins_jobs` | `jenkins jobs [folder]` | The jobs of a folder, or of the root: kind, status and the last build's number, result, time and duration. Pass a job path to read that one job. |
| `jenkins_build` | `jenkins build <job> [number] [--lines N]` | One build, the last when no number is given: result, duration, what started it, branch and revision, and the last `lines` of its console (default 100, at most 2000) with the total. |
| `jenkins_queue` | `jenkins queue` | What waits in the build queue, since when and why. |

A job is addressed by the path Jenkins shows in its URL without the `job/` segments: `team/app`, `team/app/main`.

## Develop

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest -q
```

`tests/fake.py` is a controller in memory that answers only what Jenkins answers. `tests/live/` runs read-only against a real controller and is skipped unless `AP_JENKINS_LIVE=1` and `AP_JENKINS_URL`, `AP_JENKINS_USER` and `AP_JENKINS_TOKEN` are set; it changes no role and starts no build.

Rules for contributors and agents are in `AGENTS.md`.
