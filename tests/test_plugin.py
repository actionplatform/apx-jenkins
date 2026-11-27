import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from action_platform.core.service_access import AccessError
from action_platform.plugins import Loaded, Plugins, PluginState, registry
from action_platform.plugins.options import FileOptions
from typer.testing import CliRunner

from apx_jenkins import JenkinsPlugin, cli
from apx_jenkins.access import JenkinsProvider
from apx_jenkins.client import JenkinsError
from apx_jenkins.settings import connect, setting
from tests.test_client import world

ENV = {
    "AP_JENKINS_URL": "https://jenkins.example.com",
    "AP_JENKINS_USER": "ana",
    "AP_JENKINS_TOKEN": "secret-token",
}


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.options = FileOptions("jenkins", Path(self.tmp.name))


class Access(Store):
    def test_without_a_url_there_is_no_provider(self):
        self.assertIsNone(JenkinsPlugin().access(self.options))

    def test_missing_keys_are_named(self):
        self.options.set("url", "https://jenkins.example.com")

        with self.assertRaisesRegex(AccessError, "user, token"):
            JenkinsPlugin().access(self.options)

        self.options.set("user", "ana")

        with self.assertRaisesRegex(AccessError, "token option to manage access"):
            JenkinsPlugin().access(self.options)

    def test_complete_options_give_the_provider(self):
        for k, v in {"url": "https://j", "user": "ana", "token": "t"}.items():
            self.options.set(k, v)

        self.assertIsInstance(JenkinsPlugin().access(self.options), JenkinsProvider)

    def test_the_environment_is_not_read(self):
        with mock.patch.dict("os.environ", ENV):
            self.assertIsNone(JenkinsPlugin().access(self.options))

    def test_the_declared_options(self):
        kinds = {o.key: o.kind for o in JenkinsPlugin.options}

        self.assertEqual(kinds, {"url": "url", "user": "text", "token": "secret"})
        self.assertEqual(JenkinsPlugin.min_core, "0.38")


class Settings(Store):
    def test_the_environment_wins_over_the_options_file(self):
        self.options.set("url", "https://from-file")

        with mock.patch.dict("os.environ", {"AP_JENKINS_URL": "https://from-env"}):
            self.assertEqual(setting("url", self.options), "https://from-env")

        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(setting("url", self.options), "https://from-file")

    def test_missing_settings_are_named_without_values(self):
        self.options.set("url", "https://jenkins.example.com")

        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(JenkinsError) as caught:
                connect(self.options)

        self.assertIn("AP_JENKINS_USER, AP_JENKINS_TOKEN", str(caught.exception))
        self.assertNotIn("jenkins.example.com", str(caught.exception))

    def test_the_connection_carries_basic_auth(self):
        with mock.patch.dict("os.environ", ENV):
            http = connect(self.options).http

        self.assertTrue(http.headers["authorization"].startswith("Basic "))
        self.assertEqual(http.base, "https://jenkins.example.com")


class Surface(Store):
    def setUp(self):
        super().setUp()
        self.plugins = Plugins(
            [Loaded(JenkinsPlugin(), "apx-jenkins", "0.2.0")],
            PluginState(file=Path(self.tmp.name) / "plugins.json"),
        )
        registry._current = self.plugins
        self.addCleanup(registry.reset)
        fake = world()
        for target in (
            "apx_jenkins.settings.connection",
            "apx_jenkins.runner.connection",
        ):
            patch = mock.patch(target, lambda *a: fake)
            patch.start()
            self.addCleanup(patch.stop)
        env = mock.patch.dict("os.environ", ENV)
        env.start()
        self.addCleanup(env.stop)

    def call(self, mcp, name, args):
        result = asyncio.run(mcp.call_tool(name, args))

        if result.structured_content:
            return result.structured_content.get("result", result.structured_content)

        return [json.loads(c.text) for c in result.content]

    def test_tools_are_prefixed_and_read_only(self):
        from action_platform.mcp import server

        mcp = server.build()
        tools = {t.name: t for t in asyncio.run(mcp.list_tools())}

        self.assertTrue(
            {"jenkins_jobs", "jenkins_build", "jenkins_queue"} <= set(tools)
        )
        self.assertTrue(tools["jenkins_jobs"].annotations.read_only_hint)
        self.assertTrue(tools["jenkins_build"].annotations.read_only_hint)
        self.assertTrue(tools["jenkins_queue"].annotations.read_only_hint)

    def test_the_jobs_tool_answers(self):
        from action_platform.mcp import server

        jobs = asyncio.run(server.build().call_tool("jenkins_jobs", {"folder": "app"}))

        self.assertEqual(
            sorted(json.loads(c.text)["name"] for c in jobs.content),
            ["app/new", "app/nightly", "app/release"],
        )

    def test_the_aliases_answer_as_the_generic_tools(self):
        from action_platform.mcp import server

        mcp = server.build()
        build = self.call(mcp, "jenkins_build", {"job": "app/release", "number": 1})
        latest = self.call(mcp, "jenkins_build", {"job": "app/release"})

        self.assertEqual(
            build, self.call(mcp, "ci_run", {"job": "app/release", "number": 1})
        )
        self.assertEqual((build["number"], build["status"]), (1, "failure"))
        self.assertEqual(build["causes"][0]["user"], "ana")
        self.assertEqual(latest["number"], 2)
        self.assertEqual(
            self.call(mcp, "jenkins_queue", {}), self.call(mcp, "ci_queue", {})
        )
        self.assertEqual(
            self.call(mcp, "ci_log", {"job": "app/release", "number": 1})["text"],
            "a\n",
        )


class Commands(Store):
    def setUp(self):
        super().setUp()
        fake = world()
        for target in (
            "apx_jenkins.settings.connection",
            "apx_jenkins.runner.connection",
        ):
            patch = mock.patch(target, lambda *a: fake)
            patch.start()
            self.addCleanup(patch.stop)
        env = mock.patch.dict("os.environ", ENV)
        env.start()
        self.addCleanup(env.stop)
        self.run_cli = CliRunner().invoke

    def test_jobs(self):
        out = self.run_cli(cli.app, ["jobs", "app"])

        self.assertEqual(out.exit_code, 0, out.output)
        self.assertIn("app/release\tWorkflowJob\tsuccess\t#2 SUCCESS", out.output)

    def test_build_is_ci_run(self):
        out = self.run_cli(cli.app, ["build", "app/release", "1"])

        self.assertEqual(out.exit_code, 0, out.output)
        build = json.loads(out.output)
        self.assertEqual((build["number"], build["status"]), (1, "failure"))
        self.assertEqual(build["causes"][0]["description"], "Started by user ana")

    def test_queue_empty(self):
        out = self.run_cli(cli.app, ["queue"])

        self.assertEqual((out.exit_code, json.loads(out.output)), (0, []))
