#!/usr/bin/env bash
# CI check of the real production stack: install, serve over HTTPS through Caddy,
# back up, wipe, restore, and confirm the data came back; then check that the
# scheduler is healthy and sends a reminder that falls due. Runs on a throwaway VM.
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
    -e 's/^WEB_CONCURRENCY=.*/WEB_CONCURRENCY=2/' \
    .env

bash install.sh

fail() { echo "SMOKE TEST FAILED: $*" >&2; exit 1; }

echo "--- Health check over HTTPS through Caddy"
curl --fail --silent --show-error --insecure --retry 10 --retry-delay 3 --retry-all-errors \
    https://localhost/healthz || fail "https://localhost/healthz did not answer"

echo "--- Home page shows the configured site name"
# Capture first, then search: piping into grep -q can break the pipe under pipefail.
home="$(curl --fail --silent --show-error --insecure https://localhost/)" || fail "home page request failed"
grep -q "Smoke Test Center" <<<"$home" || fail "home page does not show the site name"

echo "--- Plain HTTP redirects to HTTPS"
status="$(curl --silent --output /dev/null --write-out '%{http_code}' http://localhost/)"
[ "$status" = "308" ] || fail "expected a 308 redirect from http://localhost/, got $status"

echo "--- Gunicorn starts the number of workers in WEB_CONCURRENCY"
# Gunicorn's processes, read from /proc (the image has no ps): the master and its workers.
workers="$(docker compose exec -T web python - <<'PY'
import os

def runs_gunicorn(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            args = f.read().split(b"\0")
    except OSError:
        return False
    return any(arg.endswith(b"/gunicorn") for arg in args[:2])

print(sum(runs_gunicorn(pid) for pid in os.listdir("/proc") if pid.isdigit()) - 1)
PY
)" || fail "could not count the web workers"
[ "$workers" = "2" ] || fail "expected 2 web workers (WEB_CONCURRENCY=2), found $workers"

echo "--- Back up, wipe, restore"
manage() { docker compose exec -T web python manage.py "$@"; }
manage shell -c "from sympraxi.centers.models import Center; Center.objects.create(name='Restore me', slug='restore-me')"
bash backup.sh
manage shell -c "from sympraxi.centers.models import Center; Center.objects.all().delete()"
latest="$(find ../backups -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)"
bash restore.sh "$latest" --yes
manage shell -c "from sympraxi.centers.models import Center; assert Center.objects.filter(slug='restore-me').exists(), 'restore lost data'" \
    || fail "the center created before the backup is missing after restore"

echo "--- Audit log is still append-only after restore"
psql() { docker compose exec -T db psql -U sympraxi -d sympraxi -v ON_ERROR_STOP=1 "$@"; }
psql -c "INSERT INTO privacy_auditevent (occurred_at, actor_label, action, target_type, target_id, user_agent, details) VALUES (now(), 'ci', 'ci.test', '', '', '', '{}');"
if psql -c "UPDATE privacy_auditevent SET action = 'tampered';" >/dev/null 2>&1; then
    fail "the audit log accepted an UPDATE after restore"
fi

echo "--- The scheduler is healthy"
status=""
for _ in $(seq 1 30); do
    status="$(docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q scheduler)")"
    [ "$status" = "healthy" ] && break
    sleep 5
done
[ "$status" = "healthy" ] || fail "the scheduler's health check says ${status:-nothing}"
docker compose exec -T scheduler python -m sympraxi.healthcheck scheduler     || fail "the scheduler hasn't finished a sweep in the last three minutes"

echo "--- The scheduler sends a reminder when it falls due"
# An appointment 61 minutes away on a schedule that reminds an hour before: the
# reminder falls due in a minute, and the next sweep should send it.
manage shell <<'PY'
from datetime import time, timedelta
from django.utils import timezone
from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.scheduling.models import (
    Appointment, Participant, Schedule, Term, TutorAssignment,
)
center = Center.objects.create(name="Reminder check", slug="reminder-check")
today = timezone.localdate()
term = Term.objects.create(
    center=center, name="Check", starts_on=today - timedelta(days=1), ends_on=today + timedelta(days=2)
)
schedule = Schedule.objects.create(
    center=center, term=term, name="Check", starts_on=term.starts_on, ends_on=term.ends_on,
    opens_at=time(0), closes_at=time(23, 59), reminder_1_minutes=60, reminder_2_minutes=None,
)
tutor = User.objects.create_user("tutor@reminder-check.test", full_name="Check Tutor")
student = User.objects.create_user("student@reminder-check.test", full_name="Check Student")
Membership.objects.create(user=tutor, center=center, role=Role.TUTOR)
Membership.objects.create(user=student, center=center, role=Role.STUDENT)
assignment = TutorAssignment.objects.create(schedule=schedule, tutor=tutor)
start = timezone.now() + timedelta(minutes=61)
end = start + timedelta(minutes=30)
appointment = Appointment.objects.create(
    schedule=schedule, assignment=assignment, tutor=tutor, starts_at=start, ends_at=end,
    blocked_until=end, modality="in_person",
)
Participant.objects.create(appointment=appointment, student=student)
PY
sent=""
for _ in $(seq 1 40); do
    sent="$(psql -At -c "SELECT count(*) FROM scheduling_reminder r JOIN accounts_user u ON u.id = r.recipient_id WHERE u.email = 'student@reminder-check.test';")"
    [ "$sent" = "1" ] && break
    sleep 5
done
[ "$sent" = "1" ] || fail "the scheduler didn't send the reminder within three minutes (found ${sent:-0})"

echo "Smoke test passed."
