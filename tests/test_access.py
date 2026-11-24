import unittest

from action_platform.core.service_access import (
    Access,
    AccessError,
    Grant,
    Op,
    Person,
    spec,
)
from action_platform.testing.access import MemoryDirectory

from apx_jenkins.access import JenkinsProvider, folder_pattern
from tests.fake import FakeJenkins

ANA = Person("ana@acme.dev", "u-ana", "Ana", "owner", ("platform",))
BEN = Person("ben@acme.dev", "u-ben", "Ben", "developer", ("backend",))


class World(unittest.TestCase):
    def setUp(self):
        self.jenkins = FakeJenkins()
        self.jenkins.role(
            "globalRoles", "admin", ["hudson.model.Hudson.Administer"], ["ana"]
        )
        self.directory = MemoryDirectory([ANA, BEN])
        self.provider = JenkinsProvider(
            "https://jenkins.example.com", "u", "t", http=self.jenkins
        )
        self.access = Access(self.directory, {"jenkins": self.provider})
        self.links = {"ana@acme.dev": {"jenkins": "ana"}}

    def bootstrapped(self):
        self.access.sync(apply=True, links=self.links)


class Import(World):
    def test_first_sync_imports_and_changes_nothing(self):
        plan = self.access.sync(apply=True)

        self.assertTrue(plan.bootstrap)
        self.assertEqual(plan.changes, [])
        self.assertEqual(self.jenkins.writes(), [])
        self.assertEqual(list(self.directory.mapping.imported), ["jenkins"])
        self.assertEqual(self.access.sync().changes, [])

    def test_dry_run_import_stores_nothing(self):
        plan = self.access.sync()

        self.assertTrue(plan.bootstrap and plan.dry_run)
        self.assertEqual(self.directory.saved, 0)

    def test_no_drift_after_the_import(self):
        self.bootstrapped()
        audit = self.access.audit()

        self.assertEqual(audit.imported, ["jenkins"])
        self.assertTrue(all(s.ok and not s.drift for s in audit.services))

    def test_a_folder_read_through_a_role_is_left_alone(self):
        self.jenkins.role("projectRoles", "folder:api:build", [], ["ben"])
        self.links["ben@acme.dev"] = {"jenkins": "ben"}
        self.bootstrapped()

        self.assertEqual(self.access.sync().changes, [])


class Drift(World):
    def test_a_team_policy_adds_the_role_and_global_read(self):
        self.links["ben@acme.dev"] = {"jenkins": "ben"}
        self.bootstrapped()
        self.directory.mapping.teams["backend"] = [
            spec("jenkins", "folder/api", "read"),
            spec("jenkins", "global", "read"),
        ]

        plan = self.access.sync(apply=True)

        self.assertEqual(
            sorted(c.resource for c in plan.changes), ["folder/api", "global"]
        )
        self.assertEqual(self.jenkins.roles["globalRoles"]["read"]["sids"], ["ben"])
        self.assertEqual(
            self.jenkins.roles["projectRoles"]["folder:api:read"]["sids"], ["ben"]
        )
        self.assertEqual(self.access.sync().changes, [])

    def test_leaving_the_organization_is_drift(self):
        self.links["ben@acme.dev"] = {"jenkins": "ben"}
        self.jenkins.role("globalRoles", "read", [], ["ben"])
        self.bootstrapped()
        self.directory.members = [ANA]

        drift = {
            (c.account, c.resource, c.action)
            for s in self.access.audit().services
            for c in s.drift
        }

        self.assertIn(("ben", "global", "remove"), drift)


class Grants(World):
    def test_folder_grant_creates_the_role_and_global_read(self):
        self.bootstrapped()

        plan = self.access.grant(
            "ben@acme.dev", "jenkins", "folder/api", "build", account="ben", apply=True
        )

        self.assertEqual(
            {(c.resource, c.level) for c in plan.changes},
            {("folder/api", "build"), ("global", "read")},
        )
        role = self.jenkins.roles["projectRoles"]["folder:api:build"]
        self.assertEqual(role["pattern"], folder_pattern("api"))
        self.assertIn("hudson.model.Item.Build", role["permissions"])
        self.assertEqual(role["sids"], ["ben"])
        self.assertEqual(self.jenkins.roles["globalRoles"]["read"]["sids"], ["ben"])

    def test_folder_names_are_escaped_in_the_pattern(self):
        self.assertEqual(folder_pattern("a.b"), r"^a\.b(/.*)?$")

    def test_a_dry_run_writes_nothing(self):
        self.bootstrapped()

        plan = self.access.grant(
            "ben@acme.dev", "jenkins", "folder/api", "build", account="ben"
        )

        self.assertEqual(len(plan.changes), 2)
        self.assertEqual(self.jenkins.writes(), [])
        self.assertNotIn("folder:api:build", self.jenkins.roles["projectRoles"])

    def test_a_change_moves_the_account_to_the_new_role(self):
        self.jenkins.role("projectRoles", "folder:api:read", [], ["ben"])
        self.jenkins.role("globalRoles", "read", [], ["ben"])
        self.links["ben@acme.dev"] = {"jenkins": "ben"}
        self.bootstrapped()

        self.access.grant(
            "ben@acme.dev", "jenkins", "folder/api", "configure", apply=True
        )

        roles = self.jenkins.roles["projectRoles"]
        self.assertEqual(roles["folder:api:configure"]["sids"], ["ben"])
        self.assertEqual(roles["folder:api:read"]["sids"], [])

    def test_unknown_levels_are_refused(self):
        self.bootstrapped()

        with self.assertRaisesRegex(AccessError, "takes read, build, configure"):
            self.access.grant("ben@acme.dev", "jenkins", "folder/api", "owner")

    def test_the_last_global_admin_is_never_removed(self):
        self.jenkins.role("globalRoles", "read", [], ["ben"])
        self.links["ben@acme.dev"] = {"jenkins": "ben"}
        self.bootstrapped()

        plan = self.access.offboard("ana@acme.dev", apply=True)

        self.assertEqual(
            [(c.resource, c.status) for c in plan.changes], [("global", "refused")]
        )
        self.assertEqual(self.jenkins.roles["globalRoles"]["admin"]["sids"], ["ana"])

    def test_a_second_admin_lets_the_first_go(self):
        self.jenkins.roles["globalRoles"]["admin"]["sids"].append("ben")
        self.links["ben@acme.dev"] = {"jenkins": "ben"}
        self.bootstrapped()

        plan = self.access.offboard("ana@acme.dev", apply=True)

        self.assertEqual([c.status for c in plan.changes], ["applied"])
        self.assertEqual(self.jenkins.roles["globalRoles"]["admin"]["sids"], ["ben"])


class Reading(unittest.TestCase):
    def test_without_role_strategy_it_says_what_to_install(self):
        provider = JenkinsProvider(
            "https://j", "u", "t", http=FakeJenkins(active=False)
        )

        with self.assertRaisesRegex(AccessError, "role-strategy"):
            provider.read()

    def test_the_legacy_sid_list_is_read(self):
        fake = FakeJenkins(legacy=True)
        fake.role("globalRoles", "admin", [], ["ana"])
        fake.role("projectRoles", "folder:api:build", [], ["ben"])
        fake.role("projectRoles", "deployers", [], ["ci"])

        grants = JenkinsProvider("https://j", "u", "t", http=fake).read()

        self.assertEqual(
            grants,
            sorted(
                [
                    Grant("jenkins", "ana", "global", "admin"),
                    Grant("jenkins", "ben", "folder/api", "build"),
                    Grant("jenkins", "ci", "item-role/deployers", "assigned"),
                ]
            ),
        )

    def test_roles_it_did_not_create_stay_visible(self):
        fake = FakeJenkins()
        fake.role("globalRoles", "auditors", [], ["eve"])
        fake.role("globalRoles", "read", [], ["ben"])

        grants = JenkinsProvider("https://j", "u", "t", http=fake).read()

        self.assertEqual(
            grants,
            sorted(
                [
                    Grant("jenkins", "eve", "global-role/auditors", "assigned"),
                    Grant("jenkins", "ben", "global", "read"),
                ]
            ),
        )

    def test_groups_are_assigned_as_groups(self):
        fake = FakeJenkins()
        fake.role("globalRoles", "read", [], [])
        provider = JenkinsProvider("https://j", "u", "t", http=fake)

        provider.apply(Op("add", Grant("jenkins", "group:devs", "global", "read")))

        self.assertEqual(fake.roles["globalRoles"]["read"]["sids"], ["devs"])
        self.assertEqual(fake.writes()[-1][1].rsplit("/", 1)[-1], "assignGroupRole")

    def test_an_older_role_strategy_assigns_through_the_generic_call(self):
        fake = FakeJenkins(legacy=True)
        fake.role("globalRoles", "read", [], [])
        provider = JenkinsProvider("https://j", "u", "t", http=fake)

        provider.apply(Op("add", Grant("jenkins", "ben", "global", "read")))

        self.assertEqual(fake.roles["globalRoles"]["read"]["sids"], ["ben"])
        self.assertEqual(fake.writes()[-1][1].rsplit("/", 1)[-1], "assignRole")
