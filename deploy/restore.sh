#!/usr/bin/env bash
# Replace ALL current data with a backup made by backup.sh.
#   bash restore.sh ../backups/20270115T021500Z
# Pass --yes as a second argument to skip the confirmation (used by CI).
set -euo pipefail
cd "$(dirname "$0")"

src="${1:?Usage: restore.sh <backup folder> [--yes]}"
src="$(cd "$src" && pwd)"
(cd "$src" && sha256sum --check --quiet SHA256SUMS)

if [ "${2:-}" != "--yes" ]; then
    read -r -p "This replaces every record and upload with the backup in ${src}. Type 'restore' to continue: " answer
    [ "$answer" = "restore" ] || { echo "Cancelled."; exit 1; }
fi

docker compose stop caddy web worker
docker compose up -d --wait db

docker compose exec -T db dropdb -U sympraxi --if-exists sympraxi
docker compose exec -T db createdb -U sympraxi sympraxi
docker compose exec -T db pg_restore -U sympraxi -d sympraxi --no-owner --exit-on-error <"$src/database.dump"

docker compose run --rm --no-deps -T --entrypoint sh web \
    -c 'find /app/media -mindepth 1 -delete && tar -C /app/media -xzf -' <"$src/media.tar.gz"

# A backup from an older release gets the newer migrations applied.
docker compose run --rm web python manage.py migrate --noinput
docker compose up -d --wait

echo "Restored from $src"
