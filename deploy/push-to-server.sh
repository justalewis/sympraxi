#!/usr/bin/env bash
# Update a server you reach over SSH to the code committed on this computer.
# Run it from the repository on your own machine (not on the server):
#
#   SERVER=root@137.184.77.6 KEY=~/.ssh/sympraxi_demo bash deploy/push-to-server.sh
#   ... add RESET_DEMO=1 to wipe and re-seed a demo site afterward
#
# It sends what is committed (git HEAD), not uncommitted changes, builds the images on
# the server, applies migrations, and restarts. deploy/.env on the server is never touched.
# Material under content-pack/noncommercial is left out, like in the Docker image.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${SERVER:?Set SERVER, for example SERVER=root@203.0.113.10}"
KEY="${KEY:-$HOME/.ssh/id_ed25519}"
DIR="${REMOTE_DIR:-/opt/sympraxi}"
ssh_opts=(-i "$KEY" -o BatchMode=yes -o ServerAliveInterval=30)

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "Note: uncommitted changes are NOT sent; only the last commit is." >&2
fi
echo "Sending $(git log --oneline -1)"

package="$(mktemp -d)/sympraxi.tgz"
git archive --format=tar.gz -o "$package" HEAD -- . ':(exclude)content-pack/noncommercial'
(cd "$(dirname "$package")" && scp "${ssh_opts[@]}" "$(basename "$package")" "$SERVER:/tmp/sympraxi-update.tgz")

ssh "${ssh_opts[@]}" "$SERVER" bash -s -- "$DIR" "${RESET_DEMO:-}" <<'REMOTE'
set -euo pipefail
dir="$1"; reset="$2"
cd "$dir"
tar -xzf /tmp/sympraxi-update.tgz -C "$dir"
rm -f /tmp/sympraxi-update.tgz
cd deploy
docker compose build
docker compose up -d --wait db
docker compose stop scheduler
docker compose run --rm web python manage.py migrate --noinput
docker compose run --rm web python manage.py load_catalog
docker compose run --rm web python manage.py load_content_pack
if [ -n "$reset" ]; then
    bash reset-demo.sh
else
    docker compose up -d --wait
fi
docker compose ps --format '{{.Service}} {{.Status}}'
REMOTE
echo "Server updated."
