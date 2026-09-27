"""Production writes are represented by fakes; tests never call a hosting API."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("sync", Path(__file__).resolve().parents[1] / "scripts/sync-environments.py")
SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC)
CHECKPOINT_SPEC = importlib.util.spec_from_file_location("checkpoint", Path(__file__).resolve().parents[1] / "scripts/sync-checkpoint.py")
CHECKPOINT = importlib.util.module_from_spec(CHECKPOINT_SPEC)
CHECKPOINT_SPEC.loader.exec_module(CHECKPOINT)


class CheckpointTests(unittest.TestCase):
    def run_checkpoint(self, responses):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "9", "GITHUB_OUTPUT": str(output)}):
                with patch.object(CHECKPOINT, "api", side_effect=responses):
                    CHECKPOINT.main()
            return output.read_text() if output.exists() else ""

    def test_restores_latest_completed_trusted_workflow_checkpoint(self):
        runs = {"workflow_runs": [{"id": 9}, {"id": 8, "event": "schedule", "status": "completed", "conclusion": "failure"}]}
        artifacts = {"artifacts": [{"name": "environment-sync-state", "expired": False}]}
        self.assertEqual(self.run_checkpoint([runs, artifacts]), "run_id=8\n")

    def test_pr_runs_cannot_supply_production_checkpoints(self):
        runs = {"workflow_runs": [{"id": 8, "event": "pull_request"}]}
        self.assertEqual(self.run_checkpoint([runs]), "")

    def test_expired_checkpoint_fails_closed(self):
        runs = {"workflow_runs": [{"id": 8, "event": "schedule", "status": "completed"}]}
        artifacts = {"artifacts": [{"name": "environment-sync-state", "expired": True}]}
        with self.assertRaisesRegex(RuntimeError, "expired"):
            self.run_checkpoint([runs, artifacts])

    def test_cancelled_run_without_checkpoint_requires_review(self):
        runs = {"workflow_runs": [{"id": 8, "event": "schedule", "status": "completed", "conclusion": "cancelled"}]}
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.run_checkpoint([runs, {"artifacts": []}])

    def test_first_run_needs_no_existing_checkpoint(self):
        self.assertEqual(self.run_checkpoint([{"workflow_runs": [{"id": 9}]}]), "")


class Provider:
    def __init__(self, current):
        self.current = dict(current)
        self.writes = []
        self.deploys = []
        self.fail_key = None
        self.deploy_status = "ready"

    def changes(self, values, *_):
        return [k for k, v in values.items() if self.current.get(k) != v]

    def base_revision(self):
        return "a" * 40

    def put(self, key, value):
        if key == self.fail_key:
            raise SYNC.SyncError("Simulated write failure")
        self.current[key] = value
        self.writes.append(key)

    def deploy(self, base):
        self.deploys.append(base)
        return "dep-test"

    def status(self, _):
        return self.deploy_status


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.json"
        self.target = {"name": "test", "readiness_url": "https://example.invalid/readiness"}
        self.health = patch.object(SYNC, "check_health")
        self.health.start()
        self.addCleanup(self.health.stop)

    def run_sync(self, provider, state, values=None, apply=True):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            SYNC.sync_target(self.target, values or {"KEY": "secret-value"}, provider,
                             state, self.path, "signing-secret", apply, False)
        self.assertNotIn("secret-value", output.getvalue())

    def test_missing_or_empty_source_fails_closed(self):
        for variables in ([], [SimpleNamespace(name="KEY", value="")]):
            with self.assertRaises(SYNC.SyncError):
                SYNC.selected_values(variables, ["KEY"])

    def source_target(self):
        return {"provider": "vercel", "sources": [{"environment_id": "shared", "keys": ["ABUSE_HASH_KEY"]}],
                "settings": {"BRAIN_URL": "https://brain.example"}}

    def test_public_settings_are_not_required_in_the_secret_store(self):
        values = SYNC.desired_values({"shared": [SimpleNamespace(name="ABUSE_HASH_KEY", value="secret")]}, self.source_target())
        self.assertEqual(values, {"ABUSE_HASH_KEY": "secret", "BRAIN_URL": "https://brain.example"})

    def test_obsolete_password_manager_settings_cannot_override_versioned_settings(self):
        values = SYNC.desired_values({"shared": [SimpleNamespace(name="ABUSE_HASH_KEY", value="secret"),
                                     SimpleNamespace(name="BRAIN_URL", value="https://obsolete.example")]}, self.source_target())
        self.assertEqual(values["BRAIN_URL"], "https://brain.example")

    def test_secret_cannot_be_replaced_with_a_public_config_value(self):
        for settings in ({"ABUSE_HASH_KEY": "unsafe"}, {"BRAIN_URL": ""}):
            target = self.source_target()
            target["settings"] = settings
            with self.assertRaises(SYNC.SyncError):
                SYNC.desired_values({"shared": [SimpleNamespace(name="ABUSE_HASH_KEY", value="secret")]}, target)
        target = self.source_target()
        target["sources"][0]["keys"] = ["BRAIN_URL"]
        with self.assertRaises(SYNC.SyncError):
            SYNC.desired_values({"shared": [SimpleNamespace(name="BRAIN_URL", value="https://brain.example")]}, target)

    def test_each_secret_comes_only_from_its_assigned_source(self):
        target = {"provider": "render", "sources": [
            {"environment_id": "shared", "keys": ["DATABASE_URL", "BRAIN_OPENAI_API_KEY"]},
            {"environment_id": "production", "keys": ["BRAIN_LEDGER_DATABASE_URL"]}]}
        sources = {"shared": [SimpleNamespace(name="DATABASE_URL", value="campus"),
                              SimpleNamespace(name="BRAIN_OPENAI_API_KEY", value="api"),
                              SimpleNamespace(name="BRAIN_LEDGER_DATABASE_URL", value="obsolete")],
                   "production": [SimpleNamespace(name="BRAIN_LEDGER_DATABASE_URL", value="ledger"),
                                  SimpleNamespace(name="ABUSE_HASH_KEY", value="not-for-brain")]}
        self.assertEqual(SYNC.desired_values(sources, target),
                         {"DATABASE_URL": "campus", "BRAIN_OPENAI_API_KEY": "api", "BRAIN_LEDGER_DATABASE_URL": "ledger"})
        sources["production"] = []
        with self.assertRaises(SYNC.SyncError):
            SYNC.desired_values(sources, target)

    def test_duplicate_source_assignment_is_rejected(self):
        target = self.source_target()
        target["sources"].append({"environment_id": "another", "keys": ["ABUSE_HASH_KEY"]})
        with self.assertRaises(SYNC.SyncError):
            SYNC.desired_values({"shared": [SimpleNamespace(name="ABUSE_HASH_KEY", value="secret")]}, target)

    def test_dry_run_does_not_write_or_deploy(self):
        p, state = Provider({}), {}
        self.run_sync(p, state, apply=False)
        self.assertEqual(p.writes, [])
        self.assertEqual(p.deploys, [])
        self.assertFalse(self.path.exists())

    def test_unchanged_values_do_not_redeploy(self):
        p = Provider({"KEY": "secret-value", "UNMANAGED": "keep"})
        self.run_sync(p, {})
        self.assertEqual(p.deploys, [])
        self.assertEqual(p.current["UNMANAGED"], "keep")

    def test_changed_values_deploy_live_commit_and_state_has_no_secrets(self):
        p, state = Provider({}), {}
        self.run_sync(p, state)
        self.assertEqual(p.deploys, ["a" * 40])
        self.assertFalse(state["test"]["pending"])
        self.assertNotIn("secret-value", self.path.read_text())
        self.assertNotIn("signing-secret", self.path.read_text())
        self.run_sync(p, state)
        self.assertEqual(len(p.deploys), 1)

    def test_partial_write_is_resumed_and_deployed(self):
        p, state = Provider({}), {}
        p.fail_key = "SECOND"
        with self.assertRaises(SYNC.SyncError):
            self.run_sync(p, state, {"KEY": "secret-value", "SECOND": "other"})
        self.assertTrue(json.loads(self.path.read_text())["test"]["pending"])
        self.assertEqual(p.deploys, [])
        p.fail_key = None
        self.run_sync(p, state, {"KEY": "secret-value", "SECOND": "other"})
        self.assertEqual(p.writes, ["KEY", "SECOND"])
        self.assertEqual(len(p.deploys), 1)

    def test_failed_deploy_stops_without_redeploy_loop(self):
        p = Provider({"KEY": "secret-value"})
        p.deploy_status = "failed"
        revision = SYNC.fingerprint("signing-secret", self.target, {"KEY": "secret-value"})
        state = {"test": {"pending": True, "deployment": "dep-failed", "revision": revision}}
        with self.assertRaises(SYNC.SyncError):
            self.run_sync(p, state)
        self.assertEqual(p.deploys, [])
        self.assertEqual(p.writes, [])

    def test_pending_deployment_blocks_new_writes(self):
        p = Provider({"KEY": "old"})
        p.deploy_status = "pending"
        state = {"test": {"pending": True, "deployment": "dep-running"}}
        with self.assertRaisesRegex(SYNC.SyncError, "still running"):
            self.run_sync(p, state)
        self.assertEqual(p.writes, [])

    def test_render_queued_deployment_blocks_mutation(self):
        p = SYNC.Render({"service_id": "test"}, "token")
        p.api = Mock()
        p.api.call.return_value = [{"deploy": {"status": "queued"}},
                                  {"deploy": {"status": "live", "commit": {"id": "a" * 40}}}]
        with self.assertRaisesRegex(SYNC.SyncError, "in progress"):
            p.base_revision()

    def test_http_error_body_and_token_are_not_in_diagnostic(self):
        api = SYNC.API("https://api.render.com/v1", "secret-token")
        error = SYNC.HTTPError("https://api.render.com/v1", 403, "secret-token", {}, io.BytesIO(b"secret-value"))
        with patch.object(SYNC, "urlopen", side_effect=error):
            with self.assertRaises(SYNC.SyncError) as caught:
                api.call("GET", "/services")
        self.assertNotIn("secret", str(caught.exception))

    def test_completed_pending_deployment_is_not_repeated(self):
        p = Provider({"KEY": "secret-value"})
        state = {"test": {"pending": True, "deployment": "dep-finished"}}
        self.run_sync(p, state)
        self.assertFalse(state["test"]["pending"])
        self.assertEqual(p.deploys, [])

    def test_varying_secret_changes_fingerprint_and_order_does_not(self):
        a = SYNC.fingerprint("secret", self.target, {"A": "1", "B": "2"})
        self.assertEqual(a, SYNC.fingerprint("secret", self.target, {"B": "2", "A": "1"}))
        self.assertNotEqual(a, SYNC.fingerprint("different", self.target, {"A": "1", "B": "2"}))

    def vercel(self, rows):
        p = SYNC.Vercel({"team_slug": "team", "project": "test"}, "token")
        p.api = Mock()
        p.api.call.return_value = {"envs": rows}
        return p

    def test_vercel_cannot_touch_shared_preview_scope(self):
        p = self.vercel([{"key": "KEY", "target": ["production", "preview"]}])
        with self.assertRaisesRegex(SYNC.SyncError, "shared with preview"):
            p.changes({"KEY": "new"}, {}, "revision", True)

    def test_vercel_requires_reviewed_bootstrap(self):
        p = self.vercel([])
        with self.assertRaisesRegex(SYNC.SyncError, "bootstrap"):
            p.changes({"KEY": "new"}, {}, "revision", False)

    def test_vercel_rebuilds_pinned_code_with_fresh_project_environment(self):
        p = self.vercel([])
        p.api.call.return_value = {"id": "dpl-test"}
        source = {"type": "github", "repoId": 123, "ref": "a" * 40, "sha": "a" * 40}
        self.assertEqual(p.deploy(source), "dpl-test")
        body = p.api.call.call_args.args[2]
        self.assertEqual(body["gitSource"], source)
        self.assertNotIn("deploymentId", body)
        self.assertEqual(p.api.call.call_args.kwargs["query"], {"forceNew": "1"})

    def test_sensitive_vercel_update_only_sends_value(self):
        p = self.vercel([{"id": "env-1", "key": "KEY", "target": ["production"], "type": "sensitive"}])
        p.changes({"KEY": "new"}, {}, "revision", True)
        p.put("KEY", "new")
        self.assertEqual(p.api.call.call_args.args, ("PATCH", "/v9/projects/test/env/env-1", {"value": "new"}))

    def test_vercel_rejects_lost_sensitive_protection(self):
        p = self.vercel([{"id": "env-1", "key": "KEY", "target": ["production"], "type": "plain"}])
        with self.assertRaisesRegex(SYNC.SyncError, "must be Sensitive"):
            p.changes({"KEY": "secret"}, {}, "revision", True)

    def test_vercel_skips_unchanged_and_detects_server_side_edits(self):
        p = self.vercel([{"key": "KEY", "target": ["production"], "id": "env-1", "updatedAt": 3, "type": "sensitive"}])
        desired = {"KEY": "secret"}
        p.changes(desired, {}, "revision", True)
        previous = {"revision": "revision", "metadata": p.metadata(desired)}
        self.assertEqual(p.changes(desired, previous, "revision", False), [])
        p.api.call.return_value["envs"][0]["updatedAt"] = 4
        self.assertEqual(p.changes(desired, previous, "revision", False), ["KEY"])

    def test_render_pagination_includes_later_variables(self):
        p = SYNC.Render({"service_id": "test"}, "token")
        p.api = Mock()
        first = [{"envVar": {"key": f"K{i}", "value": "x"}, "cursor": f"c{i}"} for i in range(100)]
        p.api.call.side_effect = [first, [{"envVar": {"key": "KEY", "value": "same"}}]]
        self.assertEqual(p.changes({"KEY": "same"}, {}, "revision", False), [])
        self.assertEqual(p.api.call.call_args.kwargs["query"]["cursor"], "c99")


if __name__ == "__main__":
    unittest.main()
