#!/bin/sh
set -eu
resolver=$(awk '$1 == "nameserver" {print $2; exit}' /etc/resolv.conf)
test -n "$resolver"
case "$resolver" in *:*) resolver="[$resolver]" ;; esac
sed "s/@RESOLVER@/$resolver/g" /etc/nginx/nginx.conf > /tmp/nginx.conf
exec nginx -c /tmp/nginx.conf "$@"
