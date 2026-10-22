#!/usr/bin/env bash
# Wipe a DEMO site and start it again from sample data. Run it nightly from cron:
#   30 3 * * * bash /opt/sympraxi/deploy/reset-demo.sh >> /var/log/sympraxi-demo-reset.log 2>&1
#
# This DELETES THE WHOLE DATABASE AND ALL UPLOADS. It refuses to run unless
# SYMPRAXI_DEMO=true is in deploy/.env, so it can't be pointed at a real installation
# by mistake. Site administrator accounts are removed too; make one again after a reset
# with `docker compose exec web python manage.py createsuperuser` if you need it.
set -euo pipefail
cd "$(dirname "$0")"

if ! grep -qE '^SYMPRAXI_DEMO=(true|True|1)$' .env 2>/dev/null; then
    echo "Refusing to reset: SYMPRAXI_DEMO=true is not set in deploy/.env." >&2
    echo "This script deletes every record, and is only for a demo site." >&2
    exit 1
fi

docker compose stop web worker scheduler
docker compose up -d --wait db

docker compose exec -T db dropdb -U sympraxi --if-exists --force sympraxi
docker compose exec -T db createdb -U sympraxi sympraxi
docker compose run --rm --no-deps -T --entrypoint sh web -c 'find /app/media -mindepth 1 -delete'

docker compose run --rm web python manage.py migrate --noinput
docker compose run --rm web python manage.py createcachetable
docker compose run --rm web python manage.py load_catalog
docker compose run --rm web python manage.py load_content_pack
docker compose run --rm web python manage.py seed_demo
docker compose up -d --wait

echo "Demo reset at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
