"""Pure guards and archive checks; these tests never start or stop services."""

import importlib.util
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/deploy-profile-brain-dev.py"
SPEC = importlib.util.spec_from_file_location("deploy_profile_brain_dev", SCRIPT)
LOADER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LOADER)


class DeploymentGuards(unittest.TestCase):
    def test_only_local_profile_reader_is_accepted(self):
        good = "postgresql://brain_campus_reader@127.0.0.1:55434/rockygpt_profiles_dev_test"
        self.assertEqual(LOADER.runtime_database(good)["hostaddr"], "127.0.0.1")
        for invalid in (good.replace("127.0.0.1", "db.example"), good.replace("brain_campus_reader", "postgres"),
                        good.replace("rockygpt_profiles_dev_test", "neondb"), good + "?hostaddr=192.0.2.1"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                LOADER.runtime_database(invalid)
        with patch.dict(os.environ, {"PGSERVICE": "production"}), self.assertRaises(ValueError):
            LOADER.runtime_database(good)

    def test_revision_must_be_explicit_full_commit_sha(self):
        for value in ("HEAD", "dev", "5c7d4fc", "--help", "a" * 39):
            with self.subTest(value=value), self.assertRaises(ValueError), patch.object(LOADER.subprocess, "check_output") as call:
                LOADER.committed_revision(Path("/brain"), value)
            call.assert_not_called()
        with patch.object(LOADER.subprocess, "check_output", return_value="a" * 40 + "\n"):
            self.assertEqual(LOADER.committed_revision(Path("/brain"), "a" * 40), "a" * 40)

    def test_stop_guard_requires_our_exact_local_nonreload_brain(self):
        brain = Path("/workspace/rockygpt-brain")
        command = str(brain / ".venv/bin/python") + " -m uvicorn rockygpt_brain.api.app:app --host 127.0.0.1 --port 8000"
        self.assertTrue(LOADER.owned_brain_command(command, brain))
        for other in (command.replace("8000", "8001"), command.replace("127.0.0.1", "0.0.0.0"),
                      command.replace("rockygpt_brain.api.app:app", "other.api:app"), command + " --reload",
                      command.replace("/workspace", "/other-workspace")):
            self.assertFalse(LOADER.owned_brain_command(other, brain))

    def test_stop_guard_also_requires_the_brain_or_a_named_build_working_directory(self):
        brain = Path("/workspace/rockygpt-brain")
        self.assertTrue(LOADER.owned_working_directory(str(brain), brain))
        self.assertTrue(LOADER.owned_working_directory("/workspace/.local-logs/profile-feature/brain-builds/" + "a" * 40, brain))
        self.assertFalse(LOADER.owned_working_directory("/other-workspace/rockygpt-brain", brain))
        self.assertFalse(LOADER.owned_working_directory("/workspace/.local-logs/profile-feature/brain-builds/unverified", brain))


class ImmutableArchiveTests(unittest.TestCase):
    def test_build_uses_committed_archive_reuses_valid_copy_and_rejects_tampering(self):
        content = b"source = 'committed version'\n"
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            entry = tarfile.TarInfo("src/rockygpt_brain/__init__.py")
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(LOADER.subprocess, "check_output", return_value=buffer.getvalue()):
                build = LOADER.immutable_build(root / "brain", root / "builds", "a" * 40)
                source = build / "src/rockygpt_brain/__init__.py"
                self.assertEqual(source.read_bytes(), content)
                self.assertEqual(source.stat().st_mode & 0o222, 0)
                self.assertEqual(LOADER.immutable_build(root / "brain", root / "builds", "a" * 40), build)
                source.chmod(0o644)
                source.write_text("tampered")
                with self.assertRaisesRegex(ValueError, "integrity"):
                    LOADER.immutable_build(root / "brain", root / "builds", "a" * 40)
                for path in build.rglob("*"):
                    if path.is_dir():
                        path.chmod(0o755)
                build.chmod(0o755)

    def test_archive_symlinks_are_rejected(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            entry = tarfile.TarInfo("src/escape")
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/etc"
            archive.addfile(entry)
        with tempfile.TemporaryDirectory() as temporary, patch.object(LOADER.subprocess, "check_output", return_value=buffer.getvalue()):
            with self.assertRaisesRegex(ValueError, "unsupported"):
                LOADER.immutable_build(Path(temporary), Path(temporary) / "builds", "a" * 40)


if __name__ == "__main__":
    unittest.main()
