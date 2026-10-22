#!/usr/bin/env bash
# First-time setup on a Linux server with Docker Engine and the Compose plugin.
# Run it twice: the first run creates deploy/.env with fresh secrets for you to
# finish editing; the second run builds and starts Sympraxi.
set -euo pipefail
cd "$(dirname "$0")"

secret() {
    # Letters and digits only, so the values are safe inside URLs.
    LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c "$1" || true
}

if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    sed -i "s/^SYMPRAXI_SECRET_KEY=.*/SYMPRAXI_SECRET_KEY=$(secret 60)/" .env
    sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(secret 40)/" .env
    if [ ! -f backup.env ]; then
        cp backup.env.example backup.env
        chmod 600 backup.env
    fi
    echo "Created deploy/.env with new secrets."
    echo "Edit the domain, email and institution settings in deploy/.env, then run this script again."
    exit 0
fi

docker compose build
docker compose up -d --wait db
docker compose run --rm web python manage.py migrate --noinput
docker compose run --rm web python manage.py load_catalog
docker compose run --rm web python manage.py load_content_pack
docker compose run --rm web python manage.py createcachetable
docker compose up -d --wait

domain=$(grep '^SYMPRAXI_DOMAIN=' .env | cut -d= -f2)
echo
echo "Sympraxi is running at https://${domain}"
echo "Create the first site administrator with:"
echo "  docker compose exec web python manage.py createsuperuser"
