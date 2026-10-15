#!/usr/bin/env bash
# Upgrade to a newer release: back up, fetch, migrate, update the catalog, restart.
# Avoid upgrades that change the database schema in the middle of a term;
# the release notes say when one does.
set -euo pipefail
cd "$(dirname "$0")"

bash backup.sh

if [ -n "$(grep '^SYMPRAXI_IMAGE=' .env 2>/dev/null || true)" ]; then
    docker compose pull web worker
else
    git -C .. pull --ff-only
    docker compose build
fi

docker compose up -d --wait db
docker compose run --rm web python manage.py migrate --noinput
docker compose run --rm web python manage.py load_catalog
docker compose up -d --wait
echo "Upgrade finished."
