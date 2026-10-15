#!/usr/bin/env bash
# Back up the database and uploaded files into a timestamped folder.
# Run it nightly from cron, for example:
#   15 2 * * * bash /opt/sympraxi/deploy/backup.sh >> /var/log/sympraxi-backup.log 2>&1
# Test a restore every term (restore.sh) so you know the backups work.
set -euo pipefail
cd "$(dirname "$0")"

if [ -f backup.env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./backup.env
    set +a
fi
keep="${BACKUP_KEEP:-14}"
root="${BACKUP_DIR:-../backups}"
dest="${root}/$(date -u +%Y%m%dT%H%M%SZ)"

umask 077
mkdir -p "$dest"

docker compose exec -T db pg_dump -U sympraxi -d sympraxi --format=custom >"$dest/database.dump"
docker compose run --rm --no-deps -T --entrypoint tar web -C /app/media -czf - . >"$dest/media.tar.gz"
docker compose run --rm --no-deps -T web python manage.py showmigrations --plan >"$dest/migrations.txt"
(cd "$dest" && sha256sum database.dump media.tar.gz migrations.txt >SHA256SUMS)

# Keep the newest $keep backups on this server.
find "$root" -mindepth 1 -maxdepth 1 -type d -name '20*' | sort -r | tail -n +"$((keep + 1))" | xargs -r rm -rf

if [ -n "${RESTIC_REPOSITORY:-}" ]; then
    restic backup --tag sympraxi "$dest"
    restic forget --tag sympraxi --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
fi

echo "Backup written to $dest"
