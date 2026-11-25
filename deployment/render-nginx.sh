#!/bin/sh
set -ef

# Use literal map keys. Validate the tokens before interpolating them into nginx.
allowed=''
for host in ${LOGENTRA_PUBLIC_HOST:?Set LOGENTRA_PUBLIC_HOST to the hostname or IP users open}; do
    if ! printf '%s\n' "$host" | grep -Eq '^([A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])$'; then
        printf '%s\n' 'LOGENTRA_PUBLIC_HOST must contain hostnames or IPs without ports (bracket IPv6 addresses).' >&2
        exit 1
    fi
    allowed="$allowed host:$host 1;"
done
if [ -z "$allowed" ]; then
    printf '%s\n' 'Set LOGENTRA_PUBLIC_HOST to the hostname or IP users open.' >&2
    exit 1
fi

sed -e "s@__LOGENTRA_ALLOWED_HOSTS__@$allowed@" -e 's@127.0.0.1:8787@app:8787@g' "$1"
