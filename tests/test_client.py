import unittest

from apx_jenkins.client import Jenkins, JenkinsError, job_path, moment
from tests.fake import FakeJenkins


def world() -> FakeJenkins:
    fake = FakeJenkins()
    fake.folder("app")
    fake.folder("tools")
    fake.job("app/release", "blue")
    fake.job("app/nightly", "red")
    fake.job("app/new")
    fake.job("tools/Deploy to prod", "yellow")
    fake.run("app/release", 1, "FAILURE", ["Started by user ana"], ["a"])
    fake.run(
        "app/release",
        2,
        "SUCCESS",
        ["Started by timer"],
        [f"line {n}" for n in range(1, 251)],
    )
    fake.run("app/nightly", 7, None, ["Started by an SCM change"], ["compiling"])
    return fake


class Jobs(unittest.TestCase):
    def setUp(self):
        self.fake = world()
        self.jenkins = Jenkins(self.fake)

    def test_the_root_lists_folders(self):
        jobs = self.jenkins.jobs()

        self.assertEqual([j.name for j in jobs], ["app", "tools"])
        self.assertTrue(all(j.folder and j.status is None for j in jobs))

    def test_a_folder_lists_its_jobs_with_the_last_build(self):
        jobs = {j.name: j for j in self.jenkins.jobs("app")}

        release = jobs["app/release"]
        self.assertEqual(
            (release.status, release.last_number, release.last_result),
            ("success", 2, "SUCCESS"),
        )
        self.assertEqual(release.last_at, moment(1795502400777))
        self.assertEqual(release.last_duration_s, 99.179)
        self.assertEqual(release.kind, "WorkflowJob")
        self.assertFalse(release.folder)

    def test_a_running_build_has_no_duration_yet(self):
        nightly = {j.name: j for j in self.jenkins.jobs("app")}["app/nightly"]

        self.assertEqual(nightly.last_result, "BUILDING")
        self.assertIsNone(nightly.last_duration_s)

    def test_a_job_that_never_ran_has_no_last_build(self):
        new = {j.name: j for j in self.jenkins.jobs("app")}["app/new"]

        self.assertEqual(new.status, "not built")
        self.assertIsNone(new.last_number)

    def test_a_job_path_answers_the_job_itself(self):
        jobs = self.jenkins.jobs("app/release")

        self.assertEqual([j.name for j in jobs], ["app/release"])

    def test_names_are_escaped_in_the_path(self):
        jobs = self.jenkins.jobs("tools")

        self.assertEqual(jobs[0].name, "tools/Deploy to prod")
        self.assertEqual(
            job_path("tools/Deploy to prod"), "/job/tools/job/Deploy%20to%20prod"
        )
        self.assertEqual(
            self.jenkins.jobs("tools/Deploy to prod")[0].status, "unstable"
        )

    def test_the_tree_is_sent_as_a_query(self):
        self.jenkins.jobs("app")

        route, sent = self.fake.calls[0][1], self.fake.last_query
        self.assertEqual(route, "job/app/api/json")
        self.assertIn("jobs%5B", sent)

    def test_a_missing_folder_says_so(self):
        with self.assertRaisesRegex(JenkinsError, "folder 'nope' not found"):
            self.jenkins.jobs("nope")

    def test_refused_credentials_say_so(self):
        self.fake.status = 403

        with self.assertRaisesRegex(JenkinsError, "refused"):
            self.jenkins.jobs()

    def test_dotted_paths_are_refused(self):
        with self.assertRaises(JenkinsError):
            self.jenkins.jobs("app/../tools")


class Builds(unittest.TestCase):
    def setUp(self):
        self.fake = world()
        self.jenkins = Jenkins(self.fake)

    def test_the_last_build_with_the_tail_of_its_console(self):
        b = self.jenkins.build("app/release", lines=3)

        self.assertEqual(b.number, 2)
        self.assertEqual(b.result, "SUCCESS")
        self.assertEqual(b.duration_s, 99.179)
        self.assertEqual(b.causes, ["Started by timer"])
        self.assertEqual(b.revision, "96a8ee275617d5b6f63a7aa834a6c23634c7ee6b")
        self.assertEqual(b.branch, "refs/remotes/origin/main")
        self.assertEqual(b.console, ["line 248", "line 249", "line 250"])
        self.assertEqual(b.console_lines, 250)
        self.assertTrue(b.console_truncated)
        self.assertEqual(b.name, "app » release #2")

    def test_a_numbered_build(self):
        b = self.jenkins.build("app/release", 1)

        self.assertEqual((b.number, b.result, b.console), (1, "FAILURE", ["a"]))
        self.assertFalse(b.console_truncated)

    def test_a_running_build(self):
        b = self.jenkins.build("app/nightly")

        self.assertEqual((b.result, b.building, b.duration_s), ("BUILDING", True, None))

    def test_no_lines_asks_for_no_console(self):
        b = self.jenkins.build("app/release", lines=0)

        self.assertEqual(b.console, [])
        self.assertFalse([c for c in self.fake.calls if c[1].endswith("consoleText")])

    def test_lines_are_capped(self):
        self.jenkins.build("app/release", lines=10**6)

        asked = [c[2]["limit"] for c in self.fake.calls if c[1].endswith("consoleText")]
        self.assertEqual(asked, [2000])

    def test_a_missing_build_says_so(self):
        with self.assertRaisesRegex(JenkinsError, "build 9 of 'app/release' not found"):
            self.jenkins.build("app/release", 9)

    def test_a_job_without_builds_says_so(self):
        with self.assertRaisesRegex(JenkinsError, "not found"):
            self.jenkins.build("app/new")

    def test_the_job_is_required(self):
        with self.assertRaisesRegex(JenkinsError, "name the job"):
            self.jenkins.build("")


class Queue(unittest.TestCase):
    def test_an_empty_queue(self):
        self.assertEqual(Jenkins(FakeJenkins()).queue(), [])

    def test_what_waits_and_why(self):
        fake = FakeJenkins()
        fake.queued = [
            {
                "id": 41,
                "why": "Waiting for next available executor",
                "inQueueSince": 1795502400000,
                "blocked": False,
                "stuck": False,
                "buildable": True,
                "task": {
                    "_class": "org.jenkinsci.plugins.workflow.job.WorkflowJob",
                    "name": "release",
                    "fullName": "app/release",
                    "url": "https://jenkins.example.com/job/app/job/release/",
                },
            }
        ]

        [item] = Jenkins(fake).queue()

        self.assertEqual(
            (item.id, item.job, item.why, item.buildable, item.blocked),
            (41, "app/release", "Waiting for next available executor", True, False),
        )
        self.assertEqual(item.since, moment(1795502400000))
