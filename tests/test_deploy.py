"""Release safety checks, without containers, network or installed units."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "ops", Path(__file__).resolve().parents[1] / "deploy/scripts/ops.py"
)
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class DeploymentSafety(unittest.TestCase):
    def test_prepare_separates_secrets_and_rejects_mismatched_credentials(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root).resolve()
            manifest = root / "images.json"
            manifest.write_text(
                json.dumps(
                    {
                        "commit": ops.run(
                            "git", "rev-parse", "HEAD", cwd=ops.ROOT, capture=True
                        ).strip(),
                        "backend": ops.DB_IMAGE,
                        "web": ops.DB_IMAGE,
                        "db": ops.DB_IMAGE,
                        "dirty": True,
                    }
                )
            )
            release = root / "release"
            config = root / "configuration"
            ops.prepare(
                SimpleNamespace(
                    images=manifest,
                    output=release,
                    domain="meteo.example.invalid",
                    port=8088,
                    mode="rootless",
                    config_dir=str(config),
                    tls_cert=None,
                    tls_key=None,
                )
            )
            ops.init_config(SimpleNamespace(release=release))
            data = json.loads((release / "release.json").read_text())
            self.assertNotIn("AEMET_API_KEY", (config / "meteocentro.env").read_text())
            self.assertIn("METEOCLIMATIC_ENABLED=true", (config / "worker.env").read_text())
            with self.assertRaisesRegex(ValueError, "key is missing"):
                ops.check_config(data)
            path = config / "worker.env"
            path.write_text(path.read_text().replace("AEMET_API_KEY=", "AEMET_API_KEY=synthetic"))
            ops.check_config(data)
            (config / "database.env").write_text("DATABASE_URL=wrong\n")
            with self.assertRaisesRegex(ValueError, "credentials differ"):
                ops.check_config(data)

    def test_mutable_images_and_injection_are_rejected(self):
        for image in [
            "postgres:17.11",
            "localhost/api:current",
            "latest",
            "x@sha256:" + "1" * 63,
            "sha256:" + "1" * 64 + "\nExec=evil",
        ]:
            with self.assertRaises(ValueError):
                ops.immutable(image)
        self.assertTrue(ops.immutable(ops.DB_IMAGE))
        with self.assertRaises(ValueError):
            ops.safe_path("/tmp/%h/unsafe")


class DeploymentUpdates(unittest.TestCase):
    def check_update(self, fail_migration=False):
        """Exercise apply's real files/links with only host commands simulated."""
        with tempfile.TemporaryDirectory() as root, ExitStack() as patches:
            root = Path(root).resolve()
            state = root / "state"
            state.mkdir()
            old, new = root / "old", root / "new"
            metadata = {
                "commit": "a" * 40,
                "backend": ops.DB_IMAGE,
                "web": ops.DB_IMAGE,
                "db": ops.DB_IMAGE,
                "dirty": False,
                "mode": "rootless",
                "config_dir": str(root / "config"),
                "port": 8088,
            }
            for directory in (old, new):
                (directory / "quadlet").mkdir(parents=True)
                (directory / "release.json").write_text(json.dumps(metadata))
                (directory / "quadlet/meteocentro-db.volume").write_text(
                    "[Volume]\nVolumeName=meteocentro-db-data\n"
                )
            (state / "current").symlink_to(old, target_is_directory=True)
            events = []

            def command(*args, **kwargs):
                events.append(args)
                if args[:2] == ("podman", "info"):
                    return "v2\n"
                if args[-1] == "migrate" and fail_migration:
                    raise subprocess.CalledProcessError(1, args)
                return ""

            def host_command(args, **kwargs):
                # Any unexpected host operation must fail the test.
                self.assertEqual(
                    args,
                    [
                        "/usr/lib/systemd/system-generators/podman-system-generator",
                        "--user",
                        "--dryrun",
                    ],
                )
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout="\n".join(
                        f"meteocentro-{s}.service" for s in ("db", "api", "worker", "web")
                    ),
                    stderr="",
                )

            original_is_file = Path.is_file
            patches.enter_context(patch.object(ops.sys, "platform", "linux"))
            patches.enter_context(patch.object(ops.os, "geteuid", return_value=1000))
            patches.enter_context(patch.object(Path, "home", return_value=root))
            patches.enter_context(
                patch.object(
                    Path,
                    "is_file",
                    lambda path: path.name == "podman-system-generator" or original_is_file(path),
                )
            )
            patches.enter_context(patch.object(ops, "check_config"))
            patches.enter_context(patch.object(ops, "run", side_effect=command))
            patches.enter_context(patch.object(ops.subprocess, "run", side_effect=host_command))
            patches.enter_context(patch.object(ops, "wait_db"))
            smoke = patches.enter_context(patch.object(ops, "smoke"))
            args = SimpleNamespace(release=new, state=state, engine="podman", min_free_gib=0)
            if fail_migration:
                with self.assertRaises(subprocess.CalledProcessError):
                    ops.apply(args)
                self.assertEqual((state / "current").resolve(), old)
                self.assertFalse((state / "previous").exists())
                smoke.assert_not_called()
            else:
                ops.apply(args)
                self.assertEqual((state / "previous").resolve(), old)
                self.assertNotEqual((state / "current").resolve(), old)
                smoke.assert_called_once_with("http://127.0.0.1:8088")
            attempts = list(state.glob("attempt-*"))
            self.assertEqual(len(attempts), 1)
            status = json.loads((attempts[0] / "status.json").read_text())
            self.assertEqual(status["status"], "failed" if fail_migration else "http_verified")
            migrations = [i for i, cmd in enumerate(events) if cmd[-1] == "migrate"]
            self.assertEqual(len(migrations), 1)
            stops = [
                i for i, cmd in enumerate(events) if cmd[:3] == ("systemctl", "--user", "stop")
            ]
            self.assertLess(stops[0], migrations[0])
            for index in stops:
                self.assertEqual(
                    set(events[index][3:]),
                    {
                        "meteocentro-web",
                        "meteocentro-api",
                        "meteocentro-worker",
                    },
                )
            if fail_migration:
                self.assertGreater(stops[-1], migrations[0])
                self.assertFalse(
                    any(
                        cmd[:3] == ("systemctl", "--user", "start") and "meteocentro-api" in cmd
                        for cmd in events
                    )
                )
            self.assertFalse(any(cmd[:2] == ("podman", "exec") for cmd in events))
            self.assertTrue((root / ".config/containers/systemd/meteocentro-db.volume").is_file())

    def test_update_stops_writers_and_switches_release_after_http_check(self):
        self.check_update()

    def test_failed_migration_preserves_previous_release_and_stops_apps(self):
        self.check_update(fail_migration=True)

    def test_failures_name_the_cause_without_secrets(self):
        import errno
        import io
        from contextlib import redirect_stderr

        secret = "postgresql+psycopg://meteocentro:s3cret@db/meteocentro"
        cases = [
            (
                OSError(errno.EADDRINUSE, "Address already in use"),
                "Address already in use",
            ),
            (
                subprocess.CalledProcessError(1, ["podman", "run", "--env", secret]),
                "podman run exited with 1",
            ),
            (
                BlockingIOError(errno.EAGAIN, "busy"),
                "another operation holds the state lock",
            ),
        ]
        for error, expected in cases:

            def fail(_args, error=error):
                raise error

            output = io.StringIO()
            with (
                patch("sys.argv", ["ops.py", "smoke", "http://127.0.0.1:1"]),
                patch.object(ops, "smoke", side_effect=fail),
                redirect_stderr(output),
            ):
                self.assertEqual(ops.main(), 1)
            self.assertIn(expected, output.getvalue())
            self.assertNotIn("s3cret", output.getvalue())



if __name__ == "__main__":
    unittest.main()
