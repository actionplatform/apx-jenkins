"""Read-only against a real controller with Role-based Authorization Strategy, skipped without `AP_JENKINS_LIVE=1`, `AP_JENKINS_URL`, `AP_JENKINS_USER` and `AP_JENKINS_TOKEN`. Nothing here writes: no role changes, no builds."""

import os
import unittest

from action_platform.core.exception import ProviderError
from action_platform.core.service_access import Access
from action_platform.testing.access import MemoryDirectory

from apx_jenkins.access import JenkinsProvider
from apx_jenkins.client import Jenkins, connection
from apx_jenkins.runner import JenkinsRunner

URL, USER, TOKEN = (
    os.environ.get(f"AP_JENKINS_{k}", "") for k in ("URL", "USER", "TOKEN")
)
LIVE = os.environ.get("AP_JENKINS_LIVE") == "1" and all((URL, USER, TOKEN))


@unittest.skipUnless(
    LIVE, "needs a real Jenkins: AP_JENKINS_LIVE=1 and AP_JENKINS_URL/USER/TOKEN"
)
class LiveTest(unittest.TestCase):
    def setUp(self):
        self.jenkins = Jenkins(connection(URL, USER, TOKEN))

    def test_the_root_lists_jobs(self):
        jobs = self.jenkins.jobs()

        self.assertTrue(jobs)
        self.assertTrue(all(j.name and j.url for j in jobs))

    def test_a_folder_lists_its_jobs_and_one_build_reads(self):
        found = None

        for folder in (j for j in self.jenkins.jobs() if j.folder):
            for job in self.jenkins.jobs(folder.name):
                if job.last_number:
                    found = job
                    break

            if found:
                break

        if found is None:
            self.skipTest("no job with a build")

        runner = JenkinsRunner(URL, TOKEN, USER)
        run = runner.run(found.name, found.last_number)
        chunk = runner.log(found.name, found.last_number)

        self.assertEqual(run.number, found.last_number)
        self.assertTrue(run.status)
        self.assertEqual(chunk.next_start, len(chunk.text.encode()))
        self.assertEqual(
            runner.log(found.name, found.last_number, chunk.next_start).text, ""
        )

        try:
            stages = runner.stages(found.name, found.last_number)
        except ProviderError:
            stages = []

        self.assertTrue(all(s.id and s.name for s in stages))

    def test_the_queue_and_the_executors_read(self):
        runner = JenkinsRunner(URL, TOKEN, USER)

        for item in runner.queue():
            self.assertTrue(item.job)

        self.assertTrue(all(e.node is not None for e in runner.executors()))

    def test_a_missing_job_is_a_clear_error(self):
        with self.assertRaisesRegex(Exception, "not found"):
            self.jenkins.jobs("no-such-folder-apx-jenkins")

    def test_access_reads_the_roles_and_audits_without_writing(self):
        provider = JenkinsProvider(URL, USER, TOKEN)
        grants = provider.read()

        self.assertTrue(
            any(g.resource == "global" and g.level == "admin" for g in grants)
        )
        self.assertTrue(all(provider.valid(g.resource, g.level) == "" for g in grants))

        access = Access(MemoryDirectory([]), {"jenkins": provider})
        audit = access.audit()

        self.assertEqual([s.service for s in audit.services], ["jenkins"])
        self.assertTrue(audit.services[0].ok)
        self.assertEqual(access.sync().changes, [])
