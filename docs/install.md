# Installing Sympraxi

This guide sets up Sympraxi for one college on a single Linux server. Plan on about 30 minutes, most of it waiting for DNS and the first build.

## What you need

- A Linux VM with 2 to 4 vCPUs, 4 to 8 GB of RAM and 40 GB of disk. These sizes are an estimate until load testing in Phase 1.
- Docker Engine with the Compose plugin (`docker compose version` should work).
- A DNS name for the site (for example `writing.example.edu`) pointing at the server.
- Ports 80 and 443 open to your users. Caddy uses them to get and renew the TLS certificate automatically.
- An SMTP relay that can send mail from your center's address. Ask campus IT.

## Install

The repository address below is the planned public home of the project.

```bash
git clone https://github.com/sympraxi/sympraxi.git /opt/sympraxi
cd /opt/sympraxi/deploy
bash install.sh
```

The first run creates `deploy/.env` with a new secret key and database password. Open it and set:

| Setting | Example |
|---|---|
| `SYMPRAXI_DOMAIN`, `SYMPRAXI_ALLOWED_HOSTS` | `writing.example.edu` |
| `SYMPRAXI_CSRF_TRUSTED_ORIGINS` | `https://writing.example.edu` |
| `SYMPRAXI_SITE_NAME` | `Example College Writing Center` |
| `SYMPRAXI_TIME_ZONE` | `America/Los_Angeles` |
| `SYMPRAXI_FROM_EMAIL` and `EMAIL_URL` | Your center's address and the campus relay |
| `SYMPRAXI_SIGNUP_EMAIL_DOMAINS` | `example.edu` (only these addresses can register) |

Then run the script again. It builds the image, creates the database tables and starts everything:

```bash
bash install.sh
```

Create the first site administrator:

```bash
docker compose exec web python manage.py createsuperuser
```

Sign in at `https://writing.example.edu`. You'll be asked to set up two-factor login before anything else. Then open **Site administration** to create your center and add its director.

## What's running

| Service | Role |
|---|---|
| `caddy` | HTTPS, certificates, compression |
| `web` | The Django application (Gunicorn) |
| `worker` | Background jobs, such as sending email |
| `db` | PostgreSQL 18 |

Data lives in Docker volumes: `pgdata` (the database), `media` (uploads), and `caddy_data` (certificates).

## Backups

```bash
cp backup.env.example backup.env   # install.sh does this for you
bash backup.sh
```

Each backup is a folder under `backups/` containing the database dump, the uploaded files, the migration list and checksums. Schedule it nightly:

```cron
15 2 * * * bash /opt/sympraxi/deploy/backup.sh >> /var/log/sympraxi-backup.log 2>&1
```

To keep encrypted copies off the server, install [restic](https://restic.net) and set `RESTIC_REPOSITORY` and `RESTIC_PASSWORD` in `backup.env`.

Session records are FERPA education records, and backups contain them. Two consequences:

- Keep backup retention in line with your records-retention policy. A record your center purges still exists in every backup until that backup expires.
- Store off-site copies only where the college controls access: campus storage, or a provider under a contract that meets FERPA's school-official requirements.

## Restoring

```bash
bash restore.sh ../backups/20270115T021500Z
```

The script checks the checksums, asks you to confirm, replaces the database and uploads, applies any newer migrations, and restarts. **Test a restore every term** on a spare VM so you know your backups work.

## Upgrading

```bash
bash upgrade.sh
```

It backs up, pulls the new code, rebuilds, migrates and restarts. Read the release notes first. Feature releases come out in late May and late December. Avoid schema-changing upgrades during a term unless a security fix requires one.

## If you change the code

Sympraxi is licensed under the AGPL. If you run a modified version, set `SYMPRAXI_SOURCE_CODE_URL` in `deploy/.env` to where your users can get your modified source. The footer's "Get the source code" link uses it.

## Troubleshooting

```bash
docker compose ps
docker compose logs web --tail 100
docker compose exec web python manage.py check --deploy
```
