#!/usr/bin/env python3
"""Manually invoked CI publication; emits registry digests for the deployment host."""

import argparse
import json
import re
import subprocess
from pathlib import Path

from ops import read_images

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--images", type=Path, required=True)
p.add_argument("--repository", required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
images = read_images(a.images)
if images.get("dirty"):
    raise SystemExit("Refusing to publish a dirty rehearsal build")
if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", a.repository):
    raise SystemExit("Invalid repository")
for role in ["backend", "web"]:
    repo = f"ghcr.io/{a.repository.lower()}-{role}"
    tag = f"{repo}:{images['commit']}"
    subprocess.run(["docker", "tag", images[role], tag], check=True)
    subprocess.run(["docker", "push", tag], check=True)
    digests = json.loads(
        subprocess.check_output(
            ["docker", "image", "inspect", tag, "--format", "{{json .RepoDigests}}"], text=True
        )
    )
    images[role] = next(digest for digest in digests if digest.startswith(repo + "@sha256:"))
a.output.write_text(json.dumps(images, indent=2) + "\n")
