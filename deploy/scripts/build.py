#!/usr/bin/env python3
"""Build from a clean, exact commit. --allow-dirty is exclusively for local rehearsal."""

import argparse
import json
import subprocess
from pathlib import Path

from ops import DB_IMAGE, ROOT

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--engine", choices=["podman", "docker"], default="podman")
p.add_argument("--platform", choices=["linux/amd64", "linux/arm64"], default="linux/amd64")
p.add_argument("--output", type=Path, required=True)
p.add_argument("--allow-dirty", action="store_true")
a = p.parse_args()
commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
if dirty and not a.allow_dirty:
    raise SystemExit("Commit the reviewed changes first. Working tree is not clean.")
if a.output.exists():
    raise SystemExit("Output already exists; choose a new release manifest")
images = {"commit": commit, "platform": a.platform, "dirty": dirty, "db": DB_IMAGE}
for service in ["backend", "web"]:
    tag = f"localhost/meteocentro-{service}:{commit}" + ("-rehearsal" if dirty else "")
    subprocess.run(
        [
            a.engine,
            "build",
            "--platform",
            a.platform,
            "-f",
            f"deploy/containers/Containerfile.{service}",
            "--label",
            f"org.opencontainers.image.revision={commit}",
            "--label",
            "org.opencontainers.image.source=https://github.com/jrabalsegura/meteocentro",
            "-t",
            tag,
            ".",
        ],
        cwd=ROOT,
        check=True,
    )
    image_id = subprocess.check_output(
        [a.engine, "image", "inspect", tag, "--format", "{{.Id}}"], text=True
    ).strip()
    # Podman omits the algorithm prefix that Docker includes; manifests require it.
    images[service] = image_id if image_id.startswith("sha256:") else "sha256:" + image_id
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(json.dumps(images, indent=2) + "\n")
print(a.output.resolve())
