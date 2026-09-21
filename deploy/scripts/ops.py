#!/usr/bin/env python3
"""Meteocentro operations. Explicit subcommands; no SSH or implicit publication."""

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_IMAGE = (
    "docker.io/library/postgres:17.11@sha256:"
    "67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675"
)
IMMUTABLE = re.compile(r"(?:[a-z0-9./:_-]+@)?sha256:[a-f0-9]{64}\Z")
TABLES = (
    "stations",
    "station_sources",
    "observations",
    "latest_observations",
    "daily_summaries",
    "exclusions",
    "audit_events",
    "admin_users",
    "jobs",
)
# Two order-independent numeric fingerprints per table avoid materialising a giant string.
# The archive also has a SHA-256 checksum. These fingerprints are for restore comparison.
SIGNATURE_SQL = (
    "SELECT json_build_object("
    + ",".join(
        f"'{table}',(SELECT json_build_object('rows',count(*),"
        "'a',coalesce(sum(('x'||substr(md5(row_to_json(t)::text),1,16))::bit(64)::bigint),0)::text,"
        "'b',coalesce(sum(('x'||substr(md5(row_to_json(t)::text),17,16))::bit(64)::bigint),0)::text)"
        f" FROM {table} t)"
        for table in TABLES
    )
    + ",'revision',(SELECT version_num FROM alembic_version))"
)


def run(*args, capture=False, **kwargs):
    return subprocess.run(
        [str(a) for a in args],
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        **kwargs,
    ).stdout


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def immutable(value):
    if not isinstance(value, str) or not IMMUTABLE.fullmatch(value):
        raise ValueError("Images must be registry@sha256:digest or local sha256:image_id")
    return value


def read_images(path):
    data = json.loads(path.read_text())
    if not re.fullmatch(r"[a-f0-9]{40}", data["commit"]):
        raise ValueError("commit must be a full Git SHA")
    for name in ("backend", "web", "db"):
        immutable(data[name])
    return data


def safe_path(path):
    # Lexical normalisation: /home on macOS is a symlink, but the rendered path
    # is for the future Linux host and must not become /System/Volumes/Data/….
    value = os.path.abspath(os.path.expanduser(path))
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", value):
        raise ValueError("Deployment paths must be absolute, without spaces or systemd specifiers")
    return value


def env_values(path):
    result = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in result:
                raise ValueError("Invalid/duplicate environment key")
            result[key] = value
    return result


def prepare(args):
    images = read_images(args.images)
    commit = run("git", "rev-parse", "HEAD", capture=True, cwd=ROOT).strip()
    if images["commit"] != commit:
        raise ValueError("Manifest and deployment source must use the same commit")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", args.domain) or "." not in args.domain:
        raise ValueError("A DNS domain is required")
    if not 1024 <= args.port <= 65535:
        raise ValueError("Use an unprivileged loopback port")
    config_dir = safe_path(args.config_dir)
    target = "default.target" if args.mode == "rootless" else "multi-user.target"
    values = {
        "BACKEND_IMAGE": images["backend"],
        "WEB_IMAGE": images["web"],
        "DB_IMAGE": images["db"],
        "CONFIG_DIR": config_dir,
        "DOMAIN": args.domain,
        "PORT": str(args.port),
        "TARGET": target,
        "TLS_CERT": safe_path(
            args.tls_cert or f"/etc/letsencrypt/live/{args.domain}/fullchain.pem"
        ),
        "TLS_KEY": safe_path(args.tls_key or f"/etc/letsencrypt/live/{args.domain}/privkey.pem"),
        "ACME_ROOT": "/var/lib/meteocentro-acme",
    }
    args.output.mkdir(parents=True, exist_ok=False)
    quadlet = args.output / "quadlet"
    quadlet.mkdir()
    for path in (ROOT / "deploy/quadlet").iterdir():
        body = path.read_text()
        for key, value in values.items():
            body = body.replace(f"@{key}@", value)
        if re.search(r"@[A-Z_]+@", body):
            raise ValueError("Unresolved Quadlet token")
        (quadlet / path.name).write_text(body)
    body = (ROOT / "deploy/nginx/meteocentro.conf.example").read_text()
    for key, value in values.items():
        body = body.replace(f"@{key}@", value)
    (args.output / "nginx.conf").write_text(body)
    write_json(
        args.output / "release.json",
        {
            **images,
            "mode": args.mode,
            "config_dir": config_dir,
            "domain": args.domain,
            "port": args.port,
        },
    )
    # Keep operations with the release, outside a moving checkout.
    shutil.copy2(__file__, args.output / "ops.py")
    print(args.output.resolve())


def init_config(args):
    release = json.loads((args.release / "release.json").read_text())
    dest = Path(release["config_dir"])
    dest.mkdir(mode=0o700, parents=True, exist_ok=False)
    password = secrets.token_hex(32)
    files = {
        "meteocentro.env": (ROOT / "deploy/env/meteocentro.env.example")
        .read_text()
        .replace("meteocentro.example.invalid", release["domain"]),
        "worker.env": (ROOT / "deploy/env/worker.env.example").read_text(),
        "db.env": (
            f"POSTGRES_DB=meteocentro\nPOSTGRES_USER=meteocentro\nPOSTGRES_PASSWORD={password}\n"
        ),
        "database.env": f"DATABASE_URL=postgresql+psycopg://meteocentro:{password}@meteocentro-db:5432/meteocentro\n",
    }
    for name, body in files.items():
        path = dest / name
        path.write_text(body)
        path.chmod(0o600)
    print("Configuration created, mode 0600. Edit worker.env privately to set the AEMET key.")


def check_config(release):
    config = Path(release["config_dir"])
    for name in ("meteocentro.env", "worker.env", "db.env", "database.env"):
        path = config / name
        if path.stat().st_mode & 0o077:
            raise ValueError(f"{name} must not be readable by group/others")
    app = env_values(config / "meteocentro.env")
    worker = env_values(config / "worker.env")
    if (
        app.get("ENVIRONMENT") != "production"
        or app.get("APP_ORIGIN") != "https://" + release["domain"]
    ):
        raise ValueError("Production HTTPS origin does not match the release")
    if app.get("PRIVATE_READ") != "true":
        raise ValueError(
            "This release procedure requires private reading; review publication separately"
        )
    if worker.get("AEMET_ENABLED") == "true" and not worker.get("AEMET_API_KEY", "").strip():
        raise ValueError("AEMET is enabled but its key is missing")
    if (
        worker.get("METEOCLIMATIC_ENABLED") == "true"
        and not worker.get("METEOCLIMATIC_TERMS_REFERENCE", "").strip()
    ):
        raise ValueError("Meteoclimatic needs the applicable terms reference")
    db = env_values(config / "db.env")
    if db.get("POSTGRES_DB") != "meteocentro" or db.get("POSTGRES_USER") != "meteocentro":
        raise ValueError("Unexpected database name/user")
    password = db.get("POSTGRES_PASSWORD", "")
    if not re.fullmatch(r"[a-f0-9]{64}", password):
        raise ValueError("Use the generated database secret; rotate using the documented procedure")
    if (
        env_values(config / "database.env").get("DATABASE_URL")
        != f"postgresql+psycopg://meteocentro:{password}@meteocentro-db:5432/meteocentro"
    ):
        raise ValueError("Database credentials differ between services")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as src:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def psql(engine, container, sql):
    return run(
        engine,
        "exec",
        container,
        "psql",
        "-XqAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "meteocentro",
        "-d",
        "meteocentro",
        "-c",
        "SET TIME ZONE 'UTC'",
        "-c",
        sql,
        capture=True,
    ).strip()


def backup(engine, container, dest, release=None):
    dest.mkdir(mode=0o700, parents=True, exist_ok=False)
    # Hold one exported REPEATABLE READ snapshot for both pg_dump and signatures.
    command = [
        engine,
        "exec",
        "-i",
        container,
        "psql",
        "-XqAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "meteocentro",
        "-d",
        "meteocentro",
    ]
    session = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        session.stdin.write(
            "SET TIME ZONE 'UTC';\n"
            "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\nSELECT pg_export_snapshot();\n"
            + SIGNATURE_SQL
            + ";\n"
        )
        session.stdin.flush()
        snapshot_id = session.stdout.readline().strip()
        if not re.fullmatch(r"[A-Fa-f0-9-]+", snapshot_id):
            raise ValueError("Could not export PostgreSQL snapshot")
        signatures = json.loads(session.stdout.readline())
        archive = dest / "database.dump"
        with archive.open("xb") as output:
            subprocess.run(
                [
                    engine,
                    "exec",
                    container,
                    "pg_dump",
                    "-U",
                    "meteocentro",
                    "-d",
                    "meteocentro",
                    "-Fc",
                    "--no-owner",
                    "--no-acl",
                    f"--snapshot={snapshot_id}",
                ],
                stdout=output,
                check=True,
            )
        archive.chmod(0o600)
        session.stdin.write("COMMIT;\n\\q\n")
        session.stdin.flush()
        if session.wait(timeout=30):
            raise ValueError("Snapshot session failed")
        metadata = {
            "created_at": dt.datetime.now(dt.UTC).isoformat(),
            "sha256": sha256(archive),
            "signatures": signatures,
        }
        if release:
            shutil.copytree(release, dest / "release")
            conf = Path(json.loads((release / "release.json").read_text())["config_dir"])
            # Recoverable non-secret configuration. Secrets are backed up separately.
            write_json(
                dest / "configuration.json",
                {
                    name: {
                        k: v
                        for k, v in env_values(conf / name).items()
                        if not any(
                            part in k
                            for part in ("KEY", "PASSWORD", "TOKEN", "SECRET", "DATABASE_URL")
                        )
                    }
                    for name in ("meteocentro.env", "worker.env")
                },
            )
        write_json(dest / "manifest.json", metadata)
        (dest / "COMPLETE").write_text(
            "Backup completed; external encryption/copy is a separate step.\n"
        )
        return dest
    finally:
        if session.poll() is None:
            session.terminate()
            session.wait(timeout=10)
        session.stdin.close()
        session.stdout.close()


def verify_backup(directory):
    if not (directory / "COMPLETE").is_file():
        raise ValueError("Incomplete backup")
    metadata = json.loads((directory / "manifest.json").read_text())
    if sha256(directory / "database.dump") != metadata["sha256"]:
        raise ValueError("Backup checksum mismatch")
    return metadata


def wait_db(engine, name):
    for _ in range(60):
        result = subprocess.run(
            [
                engine,
                "exec",
                name,
                "pg_isready",
                "-U",
                "meteocentro",
                "-d",
                "meteocentro",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise ValueError("PostgreSQL did not become ready")


def restore(args):
    metadata = verify_backup(args.backup)
    image = immutable(args.db_image)
    # No target parameter: it is impossible to address the live database here.
    name = "meteocentro-restore-" + uuid.uuid4().hex[:12]
    volume = name + "-data"
    run(args.engine, "volume", "create", volume)
    try:
        run(
            args.engine,
            "run",
            "-d",
            "--name",
            name,
            "--network=none",
            "--memory=1g",
            "--env",
            "POSTGRES_USER=meteocentro",
            "--env",
            "POSTGRES_DB=meteocentro",
            "--env",
            "POSTGRES_PASSWORD",
            "--volume",
            f"{volume}:/var/lib/postgresql/data",
            image,
            env={**os.environ, "POSTGRES_PASSWORD": secrets.token_hex(32)},
        )
        wait_db(args.engine, name)
        with (args.backup / "database.dump").open("rb") as src:
            subprocess.run(
                [
                    args.engine,
                    "exec",
                    "-i",
                    name,
                    "pg_restore",
                    "-U",
                    "meteocentro",
                    "-d",
                    "meteocentro",
                    "--exit-on-error",
                    "--single-transaction",
                    "--no-owner",
                    "--no-acl",
                ],
                stdin=src,
                check=True,
            )
        actual = json.loads(psql(args.engine, name, SIGNATURE_SQL))
        if actual != metadata["signatures"]:
            raise ValueError("Restored counts/fingerprints/schema differ from the backup snapshot")
        report = {
            "status": "verified",
            "container": name,
            "volume": volume,
            "signatures": actual,
        }
        if args.report:
            write_json(args.report, report)
        print(json.dumps(report))
    finally:
        # Keep the isolated volume (also on failure) for investigation/promotion.
        subprocess.run(
            [args.engine, "rm", "-f", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"Isolated volume retained: {volume}", file=sys.stderr)


def smoke(url):
    for path, expected in (
        ("/health/ready", 200),
        ("/", 200),
        ("/gestion", 200),
        ("/api/v1/stations", 401),
        ("/api/v1/stations/00000000-0000-4000-8000-000000000007/export.csv", 401),
        ("/api/v1/admin/operations", 401),
    ):
        try:
            response = urllib.request.urlopen(url.rstrip("/") + path, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            if response.status != expected:
                raise ValueError(f"Smoke {path}: expected {expected}, got {response.status}")
            if path == "/health/ready" and json.load(response).get("status") != "ready":
                raise ValueError("Readiness did not reach API")
            if path.startswith("/api/") and "no-store" not in response.headers.get(
                "Cache-Control", ""
            ):
                raise ValueError("Private API cache policy missing")
    print("HTTP readiness, SPA routes and private API/CSV/admin: OK")


@contextlib.contextmanager
def lock(state):
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "operation.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def systemctl(release, *args):
    return run("systemctl", *(["--user"] if release["mode"] == "rootless" else []), *args)


def apply(args):
    if sys.platform != "linux" or args.engine != "podman":
        raise ValueError(
            "Release installation requires Linux and Podman; use the local rehearsal on Mac"
        )
    release = json.loads((args.release / "release.json").read_text())
    read_images(args.release / "release.json")
    check_config(release)
    if release.get("dirty", False):
        raise ValueError("A rehearsal build cannot be installed as a release")
    if (release["mode"] == "rootful") != (os.geteuid() == 0):
        raise ValueError("Run as root only for rootful; as the service user for rootless")
    if run("podman", "info", "--format", "{{.Host.CgroupsVersion}}", capture=True).strip() != "v2":
        raise ValueError("Quadlet requires cgroups v2")
    unit_dir = (
        Path("/etc/containers/systemd")
        if release["mode"] == "rootful"
        else Path.home() / ".config/containers/systemd"
    )
    generator = Path("/usr/lib/systemd/system-generators/podman-system-generator")
    if not generator.is_file():
        raise ValueError("Quadlet generator missing")
    # Validate only this release, before installing or stopping anything.
    generator_env = {
        **os.environ,
        "QUADLET_UNIT_DIRS": str((args.release / "quadlet").resolve()),
    }
    result = subprocess.run(
        [
            str(generator),
            *(["--user"] if release["mode"] == "rootless" else []),
            "--dryrun",
        ],
        env=generator_env,
        capture_output=True,
        text=True,
    )
    if (
        result.returncode
        or "unsupported" in result.stderr.lower()
        or not all(
            f"meteocentro-{s}.service" in result.stdout for s in ("db", "api", "worker", "web")
        )
    ):
        raise ValueError("Quadlet validation failed; run the documented dryrun for details")
    with lock(args.state):
        previous = args.state / "current"
        old = json.loads((previous / "release.json").read_text()) if previous.exists() else None
        if old and any(old[key] != release[key] for key in ("mode", "config_dir", "db", "port")):
            raise ValueError(
                "Mode/config/DB image/port changes need a dedicated migration; update refused"
            )
        if (
            old
            and (previous / "quadlet/meteocentro-db.volume").read_text()
            != (args.release / "quadlet/meteocentro-db.volume").read_text()
        ):
            raise ValueError("Changing the database volume requires the recovery procedure")
        if not old:
            for name in ("db", "api", "worker", "web"):
                loaded = subprocess.run(
                    [
                        "systemctl",
                        *(["--user"] if release["mode"] == "rootless" else []),
                        "show",
                        f"meteocentro-{name}.service",
                        "--property=LoadState",
                        "--value",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                if loaded != "not-found":
                    raise ValueError("Existing systemd unit: inventory before adoption")
                found = subprocess.run(["podman", "container", "exists", f"meteocentro-{name}"])
                if found.returncode == 0 or (unit_dir / f"meteocentro-{name}.container").exists():
                    raise ValueError(
                        "Existing unmanaged Meteocentro installation: inventory before adoption"
                    )
            for path in (args.release / "quadlet").iterdir():
                if (unit_dir / path.name).exists():
                    raise ValueError("Existing Quadlet file: inventory before adoption")
            if (
                subprocess.run(
                    ["podman", "network", "exists", "meteocentro"], check=False
                ).returncode
                == 0
            ):
                raise ValueError("Existing network: inventory before adoption")
            volume_body = (args.release / "quadlet/meteocentro-db.volume").read_text()
            volume_name = re.search(r"^VolumeName=([a-zA-Z0-9_-]+)$", volume_body, re.M)
            if not volume_name:
                raise ValueError("Invalid volume name")
            volume_name = volume_name.group(1)
            exists = subprocess.run(["podman", "volume", "exists", volume_name], check=False)
            if exists.returncode == 0 and args.adopt_volume != volume_name:
                raise ValueError("Existing volume requires --adopt-volume with its exact name")
            import socket

            with socket.socket() as sock:
                sock.bind(("127.0.0.1", release["port"]))
        if shutil.disk_usage(args.state).free < args.min_free_gib * 1024**3:
            raise ValueError("Insufficient free space for the selected reserve")
        if old:
            db_size = int(
                psql("podman", "meteocentro-db", "SELECT pg_database_size(current_database())")
            )
            backup_parent = args.backups.resolve()
            while not backup_parent.exists():
                backup_parent = backup_parent.parent
            if shutil.disk_usage(backup_parent).free < max(
                db_size * 2, args.min_free_gib * 1024**3
            ):
                raise ValueError("Backup filesystem needs at least twice the current database size")
        for image in (release["db"], release["backend"], release["web"]):
            if "@sha256:" in image:
                run("podman", "pull", image)
            run("podman", "image", "inspect", image, "--format", "{{.Id}}")
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
        event = args.state / ("attempt-" + stamp)
        event.mkdir()
        shutil.copytree(args.release, event / "release")
        write_json(
            event / "status.json",
            {
                "status": "prepared",
                "previous": str(previous.resolve()) if old else None,
            },
        )
        try:
            if old:
                # Quiesce all writers before the backup/migration cutover.
                systemctl(
                    release,
                    "stop",
                    "meteocentro-web",
                    "meteocentro-worker",
                    "meteocentro-api",
                )
                backup(
                    "podman",
                    "meteocentro-db",
                    args.backups / ("preupdate-" + stamp),
                    previous.resolve(),
                )
            unit_dir.mkdir(parents=True, exist_ok=True)
            for path in (event / "release/quadlet").iterdir():
                shutil.copy2(path, unit_dir / path.name)
            systemctl(release, "daemon-reload")
            systemctl(release, "start", "meteocentro-db")
            wait_db("podman", "meteocentro-db")
            run(
                "podman",
                "run",
                "--rm",
                "--name",
                "meteocentro-migrate",
                "--network",
                "meteocentro",
                "--env-file",
                Path(release["config_dir"]) / "meteocentro.env",
                "--env-file",
                Path(release["config_dir"]) / "database.env",
                release["backend"],
                "python",
                "-m",
                "meteocentro.start",
                "migrate",
            )
            systemctl(
                release,
                "reset-failed",
                "meteocentro-api",
                "meteocentro-worker",
                "meteocentro-web",
            )
            systemctl(
                release,
                "start",
                "meteocentro-api",
                "meteocentro-worker",
                "meteocentro-web",
            )
            for attempt in range(60):
                try:
                    smoke(f"http://127.0.0.1:{release['port']}")
                    break
                except (ValueError, OSError):
                    if attempt == 59:
                        raise
                    time.sleep(2)
            if old:
                prev_link = args.state / "previous"
                prev_link.unlink(missing_ok=True)
                prev_link.symlink_to(previous.resolve(), target_is_directory=True)
            next_link = args.state / "next"
            next_link.unlink(missing_ok=True)
            next_link.symlink_to((event / "release").resolve(), target_is_directory=True)
            next_link.replace(previous)
            write_json(
                event / "status.json",
                {
                    "status": "http_verified",
                    "ingestion": "check separately after provider publication",
                },
            )
        except Exception:
            # No automatic old-image rollback across a potentially incompatible schema.
            systemctl(
                release,
                "stop",
                "meteocentro-web",
                "meteocentro-worker",
                "meteocentro-api",
            )
            write_json(
                event / "status.json",
                {
                    "status": "failed",
                    "action": "Services stopped. Preserve DB; inspect backup and schema.",
                },
            )
            raise
    print("Release installed locally on this host. Nginx/certificates were not changed.")


def retain(directory, now=None):
    now = now or dt.datetime.now(dt.UTC)
    complete = []
    for path in directory.iterdir():
        if path.is_dir() and path.name.startswith("daily-") and (path / "COMPLETE").is_file():
            created = dt.datetime.fromisoformat(
                json.loads((path / "manifest.json").read_text())["created_at"]
            )
            complete.append((created, path))
    complete.sort(reverse=True)
    keep = {path for _, path in complete[:7]}
    weeks = set()
    for created, path in complete:
        week = created.isocalendar()[:2]
        if week not in weeks and len(weeks) < 4:
            weeks.add(week)
            keep.add(path)
    # Never prune pre-update backups or incomplete/investigation directories.
    for _, path in complete:
        if path not in keep:
            shutil.rmtree(path)


def daily(args):
    with lock(args.state):
        current = (args.state / "current").resolve(strict=True)
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup(args.engine, "meteocentro-db", args.backups / ("daily-" + stamp), current)
        retain(args.backups)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--images", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--mode", choices=["rootless", "rootful"], default="rootless")
    p.add_argument("--domain", required=True)
    p.add_argument("--port", type=int, default=8088)
    p.add_argument("--config-dir", required=True)
    p.add_argument("--tls-cert")
    p.add_argument("--tls-key")
    p.set_defaults(func=prepare)
    p = sub.add_parser("init-config")
    p.add_argument("--release", type=Path, required=True)
    p.set_defaults(func=init_config)
    p = sub.add_parser("apply")
    p.add_argument("--release", type=Path, required=True)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--backups", type=Path, required=True)
    p.add_argument("--min-free-gib", type=int, default=5)
    p.add_argument("--adopt-volume", help="First installation only: reviewed imported volume name")
    p.add_argument("--engine", choices=["podman"], default="podman")
    p.set_defaults(func=apply)
    p = sub.add_parser("backup")
    p.add_argument("--engine", choices=["podman", "docker"], default="podman")
    p.add_argument("--container", default="meteocentro-db")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--release", type=Path)
    p.set_defaults(func=lambda a: print(backup(a.engine, a.container, a.output, a.release)))
    p = sub.add_parser("restore-check")
    p.add_argument("--engine", choices=["podman", "docker"], default="podman")
    p.add_argument("--backup", type=Path, required=True)
    p.add_argument("--db-image", default=DB_IMAGE)
    p.add_argument("--report", type=Path)
    p.set_defaults(func=restore)
    p = sub.add_parser("smoke")
    p.add_argument("url")
    p.set_defaults(func=lambda a: smoke(a.url))
    p = sub.add_parser("daily-backup")
    p.add_argument("--engine", choices=["podman", "docker"], default="podman")
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--backups", type=Path, required=True)
    p.set_defaults(func=daily)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        args.func(args)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        # Avoid dumping subprocess environments or database connection strings.
        detail = (
            str(error) if isinstance(error, ValueError) else "inspect local logs and permissions"
        )
        print(
            f"Operation failed: {type(error).__name__}: {detail}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
