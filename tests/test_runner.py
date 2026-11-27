import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from action_platform import Config
from action_platform.abc.ci_runner import Stage, stage_tuples
from action_platform.core.context import Cause, Change, Context, RunRef
from action_platform.core.exception import DeployError, ProviderError
from action_platform.providers.ci import build_ci_runner

from apx_jenkins.runner import JenkinsRunner
from tests.fake import BASE, FakeJenkins

STARTED = 1795502400777


def world() -> FakeJenkins:
    fake = FakeJenkins()
    fake.folder("team")
    fake.job("team/app", "blue")
    fake.run(
        "team/app",
        7,
        "SUCCESS",
        ["Started by user ana"],
        ["Cloning", "Building", "Finished: SUCCESS"],
        params={"RELEASE_TAG": "v1.2.0"},
        changes=[("abc1234", "Ana Lima", "feat: ship it")],
    )
    fake.run("team/app", 8, None, ["Started by timer"], ["Cloning"])
    fake.stage("team/app", 7, "6", "Build", "SUCCESS", log="built\n")
    fake.stage("team/app", 7, "12", "Deploy", "SUCCESS", log="deployed\n")
    fake.stage("team/app", 8, "6", "Build", "IN_PROGRESS", log="compiling")
    return fake


class Runs(unittest.TestCase):
    def setUp(self):
        self.fake = world()
        self.ci = JenkinsRunner(http=self.fake)

    def test_a_run_carries_parameters_causes_and_changes(self):
        run = self.ci.run("team/app", 7)

        self.assertEqual((run.number, run.status, run.branch), (7, "success", "main"))
        self.assertEqual(run.parameters, {"RELEASE_TAG": "v1.2.0"})
        self.assertEqual(run.causes, [Cause("Started by user ana", "ana")])
        self.assertEqual(run.changes, [Change("abc1234", "Ana Lima", "feat: ship it")])
        self.assertEqual(run.trigger, "Started by user ana")
        self.assertEqual(
            run.started_at, datetime.fromtimestamp(STARTED / 1000, tz=timezone.utc)
        )
        self.assertEqual(run.url, f"{BASE}/job/team/job/app/7/")

    def test_a_cause_without_a_user(self):
        self.assertEqual(
            self.ci.run("team/app", 8).causes, [Cause("Started by timer", None)]
        )

    def test_runs_newest_first_within_the_limit(self):
        runs = self.ci.runs("team/app", 1)

        self.assertEqual([(r.number, r.status) for r in runs], [(8, "running")])
        self.assertIn(
            "{0,1}",
            self.fake.last_query.replace("%7B", "{")
            .replace("%7D", "}")
            .replace("%2C", ","),
        )

    def test_a_missing_build_is_a_provider_error(self):
        with self.assertRaisesRegex(ProviderError, "answered 404"):
            self.ci.run("team/app", 99)

    def test_refused_credentials_are_a_provider_error(self):
        self.fake.status = 401

        with self.assertRaisesRegex(ProviderError, "credentials were refused"):
            self.ci.test()

    def test_test_reaches_the_controller(self):
        self.ci.test()

        self.assertEqual(self.fake.calls[-1][1], "api/json")


class Stages(unittest.TestCase):
    def test_the_stages_of_a_run(self):
        stages = JenkinsRunner(http=world()).stages("team/app", 7)

        self.assertEqual([s.id for s in stages], ["6", "12"])
        self.assertIsInstance(stages[0], Stage)
        self.assertEqual(stages[0].duration_ms, 1200)
        self.assertEqual(
            stage_tuples(stages), [("Build", "SUCCESS"), ("Deploy", "SUCCESS")]
        )


class Logs(unittest.TestCase):
    def setUp(self):
        self.ci = JenkinsRunner(http=world())

    def test_the_whole_console_and_where_to_resume(self):
        chunk = self.ci.log("team/app", 7)

        self.assertEqual(chunk.text, "Cloning\nBuilding\nFinished: SUCCESS\n")
        self.assertEqual(chunk.next_start, len(chunk.text.encode()))
        self.assertFalse(chunk.more)

    def test_resuming_from_an_offset(self):
        chunk = self.ci.log("team/app", 7, start=8)

        self.assertEqual(chunk.text, "Building\nFinished: SUCCESS\n")

    def test_a_running_build_has_more(self):
        self.assertTrue(self.ci.log("team/app", 8).more)

    def test_one_stage(self):
        chunk = self.ci.log("team/app", 7, node="12")

        self.assertEqual((chunk.text, chunk.more), ("deployed\n", False))
        self.assertTrue(self.ci.log("team/app", 8, node="6").more)

    def test_an_unknown_node_is_a_provider_error(self):
        with self.assertRaises(ProviderError):
            self.ci.log("team/app", 7, node="99")


class Reports(unittest.TestCase):
    def setUp(self):
        self.fake = world()
        self.ci = JenkinsRunner(http=self.fake)

    def test_no_report_is_none(self):
        self.assertIsNone(self.ci.tests("team/app", 7))

    def test_totals_and_failures(self):
        self.fake.report(
            "team/app",
            7,
            passed=10,
            skipped=2,
            failures=[
                {
                    "className": "app.tests.Pay",
                    "name": "test_refund",
                    "errorDetails": "expected 1",
                    "errorStackTrace": "Traceback ...",
                }
            ],
        )

        report = self.ci.tests("team/app", 7)

        self.assertEqual(
            (report.total, report.failed, report.skipped, report.passed),
            (13, 1, 2, 10),
        )
        self.assertEqual(
            (report.failures[0].class_name, report.failures[0].message),
            ("app.tests.Pay", "expected 1"),
        )

    def test_artifacts_and_their_content(self):
        self.fake.archive("team/app", 7, "dist/app.whl", b"x" * 70000)

        self.assertEqual(
            [a.path for a in self.ci.artifacts("team/app", 7)], ["dist/app.whl"]
        )
        self.assertEqual(
            b"".join(self.ci.artifact("team/app", 7, "dist/app.whl")), b"x" * 70000
        )

    def test_artifact_paths_cannot_climb(self):
        with self.assertRaises(ProviderError):
            list(self.ci.artifact("team/app", 7, "../secrets"))

    def test_pending_inputs(self):
        self.fake.ask("team/app", 8, "Approve", "Deploy to prod?")

        [question] = self.ci.pending_inputs("team/app", 8)

        self.assertEqual(
            (question.id, question.message), ("Approve", "Deploy to prod?")
        )


class QueueAndExecutors(unittest.TestCase):
    def test_what_waits(self):
        fake = world()
        fake.queued = [
            {
                "id": 41,
                "why": "Waiting for next available executor",
                "inQueueSince": 1795502400000,
                "task": {"_class": "x", "name": "app", "fullName": "team/app"},
            }
        ]

        [item] = JenkinsRunner(http=fake).queue()

        self.assertEqual(
            (item.job, item.why), ("team/app", "Waiting for next available executor")
        )
        self.assertEqual(item.since.year, 2026)

    def test_executors_say_what_they_run(self):
        fake = world()
        fake.computers = [
            {
                "_class": "hudson.model.Hudson$MasterComputer",
                "displayName": "Built-In Node",
                "executors": [
                    {
                        "_class": "hudson.model.Executor",
                        "idle": False,
                        "currentExecutable": {
                            "_class": "org.jenkinsci.plugins.workflow.job.WorkflowRun",
                            "number": 8,
                            "url": f"{BASE}/job/team/job/app/8/",
                        },
                    },
                    {
                        "_class": "hudson.model.Executor",
                        "idle": True,
                        "currentExecutable": None,
                    },
                ],
                "oneOffExecutors": [],
            }
        ]

        busy, idle = JenkinsRunner(http=fake).executors()

        self.assertEqual(
            (busy.node, busy.busy, busy.job, busy.number),
            ("Built-In Node", True, "team/app", 8),
        )
        self.assertEqual((idle.busy, idle.job), (False, None))


class Starting(unittest.TestCase):
    def setUp(self):
        self.fake = world()
        self.ci = JenkinsRunner(http=self.fake)

    def test_a_build_is_queued_with_its_parameters_and_the_crumb(self):
        ref = self.ci.start("team/app", "", {"RELEASE_TAG": "v1.2.0"})

        posted = [c for c in self.fake.calls if c[0] == "POST"]
        self.assertEqual(posted[0][1], "job/team/job/app/buildWithParameters")
        self.assertEqual(ref.id, "queue:100")
        self.assertEqual(self.fake.waiting[100]["params"], {"RELEASE_TAG": "v1.2.0"})

    def test_the_queue_item_becomes_the_build(self):
        ref = self.ci.start("team/app", "main")

        self.assertIsNone(self.ci.run_of("team/app", ref))

        self.fake.leave(100, "team/app", 8)

        self.assertEqual(self.ci.run_of("team/app", ref).number, 8)

    def test_a_cancelled_item_is_a_deploy_error(self):
        ref = self.ci.start("team/app", "main")
        self.fake.cancel(100)

        with self.assertRaisesRegex(DeployError, "cancelled"):
            self.ci.run_of("team/app", ref)

    def test_a_ref_without_a_queue_item_is_looked_up_among_runs(self):
        with self.assertRaises(NotImplementedError):
            self.ci.run_of("team/app", RunRef(id="team/app@main"))


class ThroughTheCore(unittest.TestCase):
    def test_the_core_builds_this_runner_from_the_entry_point(self):
        runner = build_ci_runner(
            "jenkins", base_url="https://jenkins.example.com", username="ana", token="t"
        )

        self.assertIsInstance(runner, JenkinsRunner)
        self.assertTrue(runner.http.headers["authorization"].startswith("Basic "))

    def test_a_dispatched_target_deploys_through_this_runner(self):
        fake = world()
        config = Config.from_dict(
            {
                "project": {"name": "app"},
                "deploy": {
                    "targets": [
                        {
                            "name": "prod",
                            "kind": "acme/cluster",
                            "run_by": "jenkins",
                            "observe": False,
                            "job": "team/app",
                            "version_parameter": "RELEASE_TAG",
                        }
                    ]
                },
            }
        )
        config.ci = [JenkinsRunner(http=fake)]
        [target] = config.dispatched()
        original = fake.queue_build

        def queue_and_run(route, q):
            res = original(route, q)
            fake.run(
                "team/app", 9, "SUCCESS", ["Started by user ana"], ["ok"], params=q
            )
            fake.leave(100, "team/app", 9)
            return res

        fake.queue_build = queue_and_run

        with (
            TemporaryDirectory() as tmp,
            mock.patch("action_platform.providers.deploy.dispatched.POLL_SECONDS", 0),
        ):
            target.preflight(
                Context(
                    repo_root=Path(tmp),
                    next_version="1.2.0",
                    stage="prod",
                    tag="v1.2.0",
                )
            )
            result = target.deploy(
                Context(
                    repo_root=Path(tmp),
                    next_version="1.2.0",
                    stage="prod",
                    tag="v1.2.0",
                )
            )

        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.url, f"{BASE}/job/team/job/app/9/")
        self.assertEqual(fake.waiting[100]["executable"]["number"], 9)

    def test_without_the_version_parameter_the_deploy_is_refused(self):
        config = Config.from_dict(
            {
                "deploy": {
                    "targets": [
                        {
                            "name": "prod",
                            "kind": "pypi",
                            "package": "app",
                            "run_by": "jenkins",
                            "observe": False,
                            "job": "team/app",
                        }
                    ]
                }
            }
        )
        config.ci = [JenkinsRunner(http=world())]

        with (
            TemporaryDirectory() as tmp,
            self.assertRaisesRegex(DeployError, "version_parameter"),
        ):
            config.dispatched()[0].preflight(
                Context(repo_root=Path(tmp), next_version="1.0.0", stage="prod")
            )
