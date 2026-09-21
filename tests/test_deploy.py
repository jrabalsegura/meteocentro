"""Release safety checks, without containers, network or installed units."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location(
    "ops", Path(__file__).resolve().parents[1] / "deploy/scripts/ops.py"
)
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class DeploymentSafety(unittest.TestCase):
    def test_prepare_separates_secrets_and_rejects_mismatched_credentials(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
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

    def test_incomplete_or_corrupt_backup_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            with self.assertRaises(ValueError):
                ops.verify_backup(path)
            (path / "COMPLETE").touch()
            (path / "database.dump").write_bytes(b"changed")
            (path / "manifest.json").write_text(json.dumps({"sha256": "0" * 64}))
            with self.assertRaises(ValueError):
                ops.verify_backup(path)

    def test_retention_preserves_seven_daily_four_weeks_and_preupdate(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            now = ops.dt.datetime(2026, 9, 21, tzinfo=ops.dt.timezone.utc)
            for age in range(45):
                child = path / f"daily-{age:02d}"
                child.mkdir()
                (child / "COMPLETE").touch()
                (child / "manifest.json").write_text(
                    json.dumps({"created_at": (now - ops.dt.timedelta(days=age)).isoformat()})
                )
            (path / "preupdate-important").mkdir()
            (path / "daily-incomplete").mkdir()
            ops.retain(path)
            self.assertTrue(all((path / f"daily-{i:02d}").exists() for i in range(7)))
            self.assertTrue((path / "preupdate-important").exists())
            self.assertTrue((path / "daily-incomplete").exists())
            self.assertLessEqual(len(list(path.iterdir())), 13)
            self.assertFalse((path / "daily-44").exists())


if __name__ == "__main__":
    unittest.main()
