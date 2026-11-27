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
