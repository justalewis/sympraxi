#!/usr/bin/env bash
# CI check of the real production stack: install, serve over HTTPS through Caddy,
# back up, wipe, restore, and confirm the data came back. Runs on a throwaway VM.
set -euo pipefail
cd "$(dirname "$0")"

cp .env.example .env
cp backup.env.example backup.env
sed -i \
    -e 's/^SYMPRAXI_DOMAIN=.*/SYMPRAXI_DOMAIN=localhost/' \
    -e 's/^SYMPRAXI_ALLOWED_HOSTS=.*/SYMPRAXI_ALLOWED_HOSTS=localhost/' \
    -e 's|^SYMPRAXI_CSRF_TRUSTED_ORIGINS=.*|SYMPRAXI_CSRF_TRUSTED_ORIGINS=https://localhost|' \
    -e 's/^SYMPRAXI_SECRET_KEY=.*/SYMPRAXI_SECRET_KEY=smoketestsecretsmoketestsecretsmoketestsecret/' \
    -e 's/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=smoketestpassword/' \
    -e 's|^EMAIL_URL=.*|EMAIL_URL=consolemail://|' \
    -e 's/^SYMPRAXI_SITE_NAME=.*/SYMPRAXI_SITE_NAME=Smoke Test Center/' \
    .env

bash install.sh

echo "--- HTTPS through Caddy"
curl --fail --silent --insecure --retry 10 --retry-delay 3 --retry-all-errors https://localhost/healthz
curl --fail --silent --insecure https://localhost/ | grep -q "Smoke Test Center"
curl --silent --output /dev/null --write-out '%{http_code}\n' http://localhost/ | grep -q 308

echo "--- Back up, wipe, restore"
manage() { docker compose exec -T web python manage.py "$@"; }
manage shell -c "from sympraxi.centers.models import Center; Center.objects.create(name='Restore me', slug='restore-me')"
bash backup.sh
manage shell -c "from sympraxi.centers.models import Center; Center.objects.all().delete()"
latest="$(find ../backups -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)"
bash restore.sh "$latest" --yes
manage shell -c "from sympraxi.centers.models import Center; assert Center.objects.filter(slug='restore-me').exists(), 'restore lost data'"

echo "--- Audit log is still append-only after restore"
psql() { docker compose exec -T db psql -U sympraxi -d sympraxi -v ON_ERROR_STOP=1 "$@"; }
psql -c "INSERT INTO privacy_auditevent (occurred_at, actor_label, action, target_type, target_id, user_agent, details) VALUES (now(), 'ci', 'ci.test', '', '', '', '{}');"
if psql -c "UPDATE privacy_auditevent SET action = 'tampered';" >/dev/null 2>&1; then
    echo "Audit log accepted an UPDATE after restore" >&2
    exit 1
fi

echo "Smoke test passed."
