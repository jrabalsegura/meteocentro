#!/usr/bin/env python3
"""Four isolated OCI containers, no provider HTTP; migration and persistence rehearsal."""

import argparse
import ipaddress
import json
import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path

from ops import ROOT, SIGNATURE_SQL, psql, read_images, run, smoke, wait_db

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--images", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--engine", choices=["docker", "podman"], default="docker")
p.add_argument("--backend-tests", action="store_true")
p.add_argument(
    "--keep-running",
    action="store_true",
    help="Keep successful synthetic rehearsal open for local review",
)
p.add_argument("--web-port", type=int, default=0, help="Loopback web port; 0 chooses a free port")
a = p.parse_args()
if not 0 <= a.web_port <= 65535:
    p.error("--web-port must be between 0 and 65535")
images = read_images(a.images)
a.output.mkdir(mode=0o700, parents=True, exist_ok=False)
a.output = a.output.resolve()
engine = a.engine
prefix = "meteocentro-phase7-" + uuid.uuid4().hex[:10]
network, volume = prefix, prefix + "-data"
names = {role: prefix + "-" + role for role in ["db", "api", "worker", "web"]}
containers = []
started = time.monotonic()
report = {
    "images": images,
    "provider_http": False,
    "status": "running",
    "backend_tests": "not_requested",
    "containers": names,
    "network": network,
    "volume": volume,
}
envfile = a.output / "test.env"
envfile.write_text(
    f"DATABASE_URL=postgresql+psycopg://meteocentro:synthetic-only@{names['db']}:5432/meteocentro\nENVIRONMENT=test\nAPP_ORIGIN=http://localhost:5173\nPRIVATE_READ=true\nAEMET_ENABLED=false\nMETEOCLIMATIC_ENABLED=false\nMETEOCLIMATIC_TERMS_REFERENCE=synthetic-offline-rehearsal\nPHASE7_SYNTHETIC_ONLY=yes\n"
)
envfile.chmod(0o600)


def app_run(*command, input_path=None):
    with input_path.open("rb") if input_path else open(os.devnull, "rb") as src:
        subprocess.run(
            [
                engine,
                "run",
                "--rm",
                "-i",
                "--network",
                network,
                "--env-file",
                str(envfile),
                images["backend"],
                *command,
            ],
            stdin=src,
            check=True,
        )


def start_role(role):
    image = images["web"] if role == "web" else images["backend"]
    args = [
        engine,
        "run",
        "-d",
        "--name",
        names[role],
        "--network",
        network,
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=32m,mode=1777",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--memory",
        {"api": "512m", "worker": "768m", "web": "128m"}[role],
        "--pids-limit",
        "64" if role == "web" else "128",
    ]
    if role == "web":
        args += ["-p", f"127.0.0.1:{a.web_port or ''}:8080"]
    else:
        args += ["--env-file", str(envfile)]
    if role == "api":
        args += ["--network-alias", "meteocentro-api"]
    if role == "worker":
        args += ["--cpus", "1"]
    args += [image]
    if role != "web":
        args += ["python", "-m", "meteocentro.start", role]
    containers.append(names[role])
    run(*args)


def port(container, internal):
    return run(engine, "port", container, str(internal), capture=True).strip().rsplit(":", 1)[1]


try:
    # Let the engine allocate a free subnet, then explicitly configure that same
    # subnet before attaching containers. Docker on the Linux runner rejects
    # --ip on an implicit subnet; the DNS check must reserve the API's old address.
    run(engine, "network", "create", network)
    network_info = json.loads(run(engine, "network", "inspect", network, capture=True))[0]
    subnets = (
        [item["Subnet"] for item in network_info["IPAM"]["Config"]]
        if engine == "docker"
        else [item["subnet"] for item in network_info["subnets"]]
    )
    subnet = next(value for value in subnets if ipaddress.ip_network(value).version == 4)
    run(engine, "network", "rm", network)
    run(engine, "network", "create", "--subnet", subnet, network)
    report["subnet"] = subnet
    run(engine, "volume", "create", volume)
    containers.append(names["db"])
    run(
        engine,
        "run",
        "-d",
        "--name",
        names["db"],
        "--network",
        network,
        "--memory",
        "1g",
        "-p",
        "127.0.0.1::5432",
        "-e",
        "POSTGRES_USER=meteocentro",
        "-e",
        "POSTGRES_PASSWORD=synthetic-only",
        "-e",
        "POSTGRES_DB=meteocentro",
        "-v",
        volume + ":/var/lib/postgresql/data",
        images["db"],
    )
    wait_db(engine, names["db"])
    # Exercise an actual old-to-new schema update containing a real exclusion.
    app_run("alembic", "upgrade", "0005_administration")
    app_run("python", "-", input_path=ROOT / "tests/seed_phase7.py")
    before_migration = json.loads(psql(engine, names["db"], SIGNATURE_SQL))
    app_run("python", "-m", "meteocentro.start", "migrate")
    app_run("alembic", "downgrade", "0005_administration")
    assert json.loads(psql(engine, names["db"], SIGNATURE_SQL)) == before_migration
    app_run("python", "-m", "meteocentro.start", "migrate")
    app_run("alembic", "check")
    after_migration = json.loads(psql(engine, names["db"], SIGNATURE_SQL))
    assert after_migration == {**before_migration, "revision": "0006_operations"}
    report["migration_integrity"] = True
    report["migration"] = "0005_administration -> 0006_operations, with synthetic records"
    start_role("web")
    url = "http://127.0.0.1:" + port(names["web"], 8080)
    # Use the browser's real origin, including an automatically allocated port.
    envfile.write_text(
        envfile.read_text().replace("APP_ORIGIN=http://localhost:5173", f"APP_ORIGIN={url}")
    )
    for role in ["api", "worker"]:
        start_role(role)
    report["url"] = url
    for attempt in range(60):
        try:
            smoke(url)
            break
        except (ValueError, OSError):
            if attempt == 59:
                raise
            time.sleep(1)
    run(engine, "exec", names["web"], "nginx", "-t", "-c", "/tmp/nginx.conf")
    # Exact private login, real API and PostgreSQL, without provider credentials.
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url + "/api/v1/auth/login",
        data=json.dumps(
            {"username": "fixture", "password": "Phase7 synthetic password only!"}
        ).encode(),
        headers={"Content-Type": "application/json", "Origin": url},
    )
    with urllib.request.urlopen(request) as response:
        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        assert response.status == 200
    private = urllib.request.Request(url + "/api/v1/stations", headers={"Cookie": cookie})
    with urllib.request.urlopen(private) as response:
        assert json.load(response)["total"] == 0, "Exclusion must survive the migration"
    # Real recreation of API: nginx must discover a changed network address.
    ip_format = "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"
    old_ip = run(engine, "inspect", names["api"], "--format", ip_format, capture=True).strip()
    run(engine, "rm", "-f", names["api"])
    containers.remove(names["api"])
    holder = prefix + "-reserved-address"
    containers.append(holder)
    run(
        engine,
        "run",
        "-d",
        "--name",
        holder,
        "--network",
        network,
        "--ip",
        old_ip,
        "--entrypoint",
        "sleep",
        images["backend"],
        "120",
    )
    start_role("api")
    new_ip = run(engine, "inspect", names["api"], "--format", ip_format, capture=True).strip()
    assert old_ip != new_ip, "API must move to verify DNS recovery"
    for attempt in range(45):
        try:
            smoke(url)
            break
        except (ValueError, OSError):
            if attempt == 44:
                raise
            time.sleep(1)
    report["api_recreated"] = True
    report["api_addresses"] = {"before": old_ip, "after": new_ip}
    run(engine, "rm", "-f", holder)
    containers.remove(holder)
    run(engine, "stop", names["worker"])
    # Simulate elapsed heartbeat timeout, without claiming a host reboot.
    psql(engine, names["db"], "UPDATE worker_heartbeat SET seen_at=now()-interval '100 seconds'")
    result = subprocess.run(
        [engine, "exec", names["api"], "python", "-m", "meteocentro.operations"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1 and json.loads(result.stdout)["worker"]["state"] == "missing"
    run(engine, "start", names["worker"])
    # These records stay unchanged while providers are disabled, unlike job/heartbeat state.
    persistent_tables = (
        "stations",
        "station_sources",
        "observations",
        "exclusions",
        "audit_events",
    )
    before_restart = json.loads(psql(engine, names["db"], SIGNATURE_SQL))
    run(engine, "restart", names["db"])
    wait_db(engine, names["db"])
    for attempt in range(60):
        result = subprocess.run(
            [engine, "exec", names["api"], "python", "-m", "meteocentro.operations"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            report["diagnosis"] = json.loads(result.stdout)
            break
        if attempt == 59:
            raise ValueError("Worker did not recover after DB restart")
        time.sleep(1)
    report["resources"] = run(
        engine,
        "stats",
        "--no-stream",
        "--format",
        "{{.Name}} {{.MemUsage}} {{.CPUPerc}}",
        *names.values(),
        capture=True,
    )
    run(engine, "stop", names["worker"])
    after_restart = json.loads(psql(engine, names["db"], SIGNATURE_SQL))
    for table in persistent_tables:
        assert after_restart[table] == before_restart[table], f"Changed after restart: {table}"
    assert after_restart["revision"] == after_migration["revision"]
    report["persistence_integrity"] = True
    report["db_restart"] = True
    if a.backend_tests:
        report["backend_tests"] = "running"
        psql(engine, names["db"], "CREATE DATABASE meteocentro_phase7_test")
        url_db = (
            "postgresql+psycopg://meteocentro:synthetic-only@127.0.0.1:"
            + port(names["db"], 5432)
            + "/meteocentro_phase7_test"
        )
        test_env = {**os.environ, "TEST_DATABASE_URL": url_db, "DATABASE_URL": url_db}
        result = subprocess.run(
            [str(ROOT / "backend/.venv/bin/pytest"), "tests", "-q", "--tb=short"],
            cwd=ROOT,
            env=test_env,
            check=False,
        )
        report["backend_tests"] = "passed" if result.returncode == 0 else "failed"
        report["backend_tests_exit_code"] = result.returncode
        result.check_returncode()
    if a.keep_running:
        run(engine, "start", names["worker"])
        smoke(url)
        cleanup = a.output / "stop-preview.sh"
        cleanup.write_text(
            "#!/bin/sh\nset -eu\n"
            + shlex.join([engine, "rm", "-f", *names.values()])
            + "\n"
            + shlex.join([engine, "network", "rm", network])
            + "\n# Synthetic database volumes are retained.\n"
        )
        cleanup.chmod(0o700)
        report["stop_preview"] = str(cleanup)
    report["status"] = "passed"
except BaseException as error:
    report["status"] = "failed"
    report["error_type"] = type(error).__name__
    raise
finally:
    keep = a.keep_running and report["status"] == "passed"
    report["containers_retained"] = keep
    report["duration_seconds"] = round(time.monotonic() - started, 1)
    (a.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    if not keep:
        for name in reversed(containers):
            subprocess.run(
                [engine, "rm", "-f", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        subprocess.run(
            [engine, "network", "rm", network],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    print("REHEARSAL " + report["status"].upper() + ":", a.output / "result.json")
    if keep:
        print("SYNTHETIC PREVIEW:", url)
        print("Login: fixture / Phase7 synthetic password only!")
        print("Stop preview (retain volumes):", report["stop_preview"])
    print("Synthetic database volume retained:", volume)
