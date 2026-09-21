#!/bin/sh
# Read-only, LOCAL to the machine on which the operator explicitly runs it.
# Does not connect by SSH or dump environment variables/certificates.
set -eu
uname -srmo
id
podman --version
systemctl --version | head -1
podman info --format 'rootless={{.Host.Security.Rootless}} cgroups={{.Host.CgroupsVersion}} graphRoot={{.Store.GraphRoot}}'
test -x /usr/lib/systemd/system-generators/podman-system-generator
ss -ltn
free -h
df -h
podman ps --format '{{.Names}} {{.Image}} {{.Ports}}'
systemctl list-units --type=service --state=running --no-pager
# The operator also inspects the other Podman mode and nginx as documented.
