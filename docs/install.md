# Installing Sympraxi

This guide sets up Sympraxi for one college on a single Linux server. Plan on about 30 minutes, most of it waiting for DNS and the first build.

## What you need

- A Linux VM with 2 to 4 vCPUs, 4 to 8 GB of RAM and 40 GB of disk. These sizes are still an estimate, since the Phase 1 load test ran on a laptop (see [Web workers](#web-workers)).
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

When you've finished editing, keep a copy of `deploy/.env` and `deploy/backup.env` somewhere safe away from the server. "Keys and passwords" below says why.

Then run the script again. It builds the image, creates the database tables, loads the topic vocabulary and resource catalog, and starts everything:

```bash
bash install.sh
```

Create the first site administrator:

```bash
docker compose exec web python manage.py createsuperuser
```

Sign in at `https://writing.example.edu`. You'll be asked to set up two-factor login before anything else. Then open **Site administration** to create your center and add its director. The director adds everyone else on the center's **People** page: one at a time, or many from a CSV file.

## Single sign-on

Students and staff can sign in with their college Microsoft account (Entra ID) or Google Workspace account. Set up either one or both. Each provider takes three settings in `deploy/.env`, and its button appears on the sign-in page only when all three are filled in. If only some are filled in, Sympraxi won't start and its log names the missing setting. Client secrets stay in `deploy/.env`; Sympraxi never stores them in the database.

In the steps below, replace `writing.example.edu` with your site's address. A redirect URI has to match exactly, including `https://` and the slash at the end.

### Microsoft Entra ID

Someone with the Application Administrator or Cloud Application Administrator role does this in the Microsoft Entra admin center.

1. Go to **App registrations** and choose **New registration**.
   - **Name:** your center's name, for example "Example College Writing Center".
   - **Supported account types:** "Accounts in this organizational directory only" (single tenant).
   - **Redirect URI:** platform **Web**, `https://writing.example.edu/accounts/oidc/entra/login/callback/`
2. On the app's **Overview** page, copy the **Application (client) ID** and the **Directory (tenant) ID**.
3. Under **Certificates & secrets**, add a client secret and copy its **Value** (not its ID). It is shown only once. Sign-in stops working the day the secret expires, so put a reminder on someone's calendar to replace it before then.
4. Under **Token configuration**, choose **Add optional claim**, token type **ID**, and add `email` and `xms_edov`. Add `amr` too if you plan to turn on `SYMPRAXI_TRUST_IDP_MFA`. Accept the offer to turn on the Microsoft Graph `email` permission. If a claim isn't in the list, add it in **Manifest** under `optionalClaims` → `idToken`.
5. Under **API permissions**, the app needs only the delegated Microsoft Graph permissions `openid`, `profile` and `email`. `User.Read`, which is added by default, can stay. Granting admin consent spares each person a consent screen at first sign-in.

Sympraxi requests the scopes `openid profile email`. Add to `deploy/.env`:

```
SYMPRAXI_ENTRA_TENANT_ID=<Directory (tenant) ID>
SYMPRAXI_ENTRA_CLIENT_ID=<Application (client) ID>
SYMPRAXI_ENTRA_CLIENT_SECRET=<client secret value>
```

The tenant ID has to be the GUID from step 2. Sympraxi refuses `common`, `organizations` and `consumers`, because those endpoints accept accounts from any organization or personal Microsoft accounts.

Entra's `email` claim is copied from the account's mail attribute, and Microsoft's documentation says never to use it for authorization. `xms_edov` is true when the address is in a domain your tenant has verified. Without it, Sympraxi refuses every Entra sign-in with a message that the email address wasn't confirmed.

### Google Workspace

In the Google Cloud console, use a project that belongs to your Workspace organization.

1. Open **Google Auth Platform**. Under **Branding**, enter the app name and a support email.
2. Under **Audience**, choose **Internal** so only accounts in your Workspace organization can sign in. (See the two-factor section below before choosing External.)
3. Under **Data Access**, the scopes are `openid`, `email` and `profile`. Google classes them as non-sensitive, so they need no review.
4. Under **Clients**, create a client of type **Web application** with the authorized redirect URI `https://writing.example.edu/accounts/oidc/google/login/callback/`. Copy the client ID and client secret.

Add to `deploy/.env`:

```
SYMPRAXI_GOOGLE_HOSTED_DOMAIN=example.edu
SYMPRAXI_GOOGLE_CLIENT_ID=<client ID>
SYMPRAXI_GOOGLE_CLIENT_SECRET=<client secret>
```

The hosted domain is your Workspace organization's domain, the value Google puts in the ID token's `hd` claim. Sympraxi refuses any Google account whose token has a different `hd` or none, which rules out personal Gmail accounts.

Restart after changing `deploy/.env`: `docker compose up -d`.

### Who can sign in

Every SSO sign-in is checked, every time, not only the first:

- Microsoft: the token's tenant (`tid`) is yours, and `xms_edov` is true.
- Google: `hd` is your hosted domain, and `email_verified` is true.
- If `SYMPRAXI_SIGNUP_EMAIL_DOMAINS` is set, the address is at one of those domains. Set it whenever you use SSO.

Someone who passes these checks and already has a Sympraxi account with the same address is linked to that account automatically. If that account's address was never verified, its password is removed, in case someone else registered the address first. An account that is already linked to a different Microsoft or Google identity is never linked to a second one by address: when a college gives an old address to a new person, the new person must not get the previous holder's records. Their sign-in is refused, and a site administrator has to look at the account.

Someone without an account gets one at their first sign-in, as long as `SYMPRAXI_SIGNUP_OPEN` is true (the default). A director then gives them a role on the center's **People** page.

People see and remove their links under **Account** → **Linked accounts**. An account with no password can't remove its last link. Links, unlinks and refused sign-ins go in the audit log, which site administrators can read under **Site administration** → **Audit events**.

### Two-factor login for staff

By default, single sign-on changes nothing here. Staff who sign in through Microsoft or Google still enter a code from their authenticator app, or use a passkey, in Sympraxi. Staff who haven't set one up are sent to do it.

If your college already requires multi-factor login, you can set `SYMPRAXI_TRUST_IDP_MFA=true` to accept it instead. Sympraxi accepts it only when the ID token shows that it happened, meaning the `amr` claim contains `mfa`. When the claim is missing or doesn't contain `mfa`, the usual Sympraxi step applies.

For Microsoft, add the `amr` optional claim (step 4 above). Entra includes `mfa` when the person completed multi-factor login in their current Entra session, so your Conditional Access sign-in frequency decides how often staff are asked again.

Google includes `amr` only for apps that are published (**Audience** shows "In production"), verified (**Verification Center** shows "Verified"), and have **Authentication strength claims** turned on under **Settings** → **Advanced settings**. Sympraxi asks for the claim when `SYMPRAXI_TRUST_IDP_MFA` is on. Google's documentation doesn't say whether an Internal app qualifies, and it warns that the claim can be missing even when requested. When it is missing, Sympraxi can't tell whether Google did multi-factor login, so it asks for its own. A passkey or security-key sign-in at Google may report `swk` or `hwk` without `mfa`, and Sympraxi doesn't count those either.

### Single sign-on only

Once SSO works, set `SYMPRAXI_SSO_ONLY=true` to hide password sign-in, passkey sign-in and sign-up. Everyone then signs in through Microsoft or Google, so turning off someone's college account also ends their access to Sympraxi. Sympraxi won't start with this setting on unless a provider is set up.

Site administrators keep a break-glass sign-in with their password and two-factor login at `https://writing.example.edu/accounts/login/local/`. Use it when the identity provider is down or misconfigured. Anyone else who tries a password there, or on the regular sign-in page, is refused. Keep at least one site administrator with a password and two-factor login (the account from `createsuperuser` will do), and try the break-glass page once a term.

### If sign-in fails

- Microsoft shows error AADSTS50011, or Google shows `redirect_uri_mismatch`: the redirect URI registered with the provider doesn't exactly match the ones above.
- Every Microsoft sign-in says the email address wasn't confirmed: the `email` or `xms_edov` optional claim is missing (step 4).
- Sympraxi shows "Sign-in didn't work": the provider's answer failed a check, for example a token from another tenant. Site administrators can see it under **Site administration** → **Audit events**, as a failed sign-in with the provider and the reason.

## What's running

| Service | Role |
|---|---|
| `caddy` | HTTPS, certificates, compression |
| `web` | The Django application (Gunicorn) |
| `worker` | Background jobs, such as sending email and building full-center exports |
| `scheduler` | Each minute, sends reminder emails, flags possible no-shows and deletes expired exports |
| `db` | PostgreSQL 18 |

Data lives in Docker volumes: `pgdata` (the database), `media` (uploads), and `caddy_data` (certificates).

## Web workers

The `web` service runs Gunicorn, which passes each request to one of a fixed number of worker processes. A worker answers one request at a time, and requests that arrive while every worker is busy wait their turn. The default is 3 workers. To change it, set `WEB_CONCURRENCY` in `deploy/.env` and run `docker compose up -d`.

Start with twice the number of vCPUs plus one: 5 workers on 2 vCPUs, 9 on 4. Plan on about 150 MB of memory for each worker. Each one also keeps a database connection open, and PostgreSQL allows 100.

The Phase 1 load test ran on a laptop with 8 logical processors (`docs/decisions.md`, "Phase 1: first-Monday load test"). In its standard run, 500 students booked over ten minutes, 150 of them in the first minute, while three front desk workers booked for students. Three workers kept 95 percent of requests on every page under 600 ms, but that first minute used about 90 percent of what they could serve. When the same students came five times as fast, three workers fell behind and eight kept up. A VM's processors are usually slower than a laptop's, so a center expecting a rush like that should start above the default.

If pages get slow at busy times while the server's processors have room to spare (check with `top` or `docker stats`), add workers. If the processors are already busy, add vCPUs first.

## Sign-in limits

To slow down password guessing, Sympraxi limits password sign-ins from each internet address: 300 a minute, of which 60 may fail, and 60 new accounts a minute. Each account also gets 5 failed sign-ins in 5 minutes. Anyone over a limit sees a page asking them to wait a minute and try again. Sign-ins through Microsoft or Google don't count toward these limits.

Caddy passes each visitor's address to Sympraxi, which uses it for these limits and for the audit log. If campus IT puts a load balancer or another proxy in front of Caddy, every request reaches Caddy from that proxy's address. Everyone then shares one set of limits, and the audit log records the proxy's address instead of the visitor's.

## Reminders and attendance

The `scheduler` service sends reminder emails, flags students no one marked as here, and reminds tutors about session records they haven't written, once a minute. `install.sh` starts it with everything else. Links in reminders start with `https://` and your `SYMPRAXI_DOMAIN`; set `SYMPRAXI_BASE_URL` in `deploy/.env` only if people reach the site at a different address.

Its health check passes only if a sweep finished in the last three minutes. Check it with:

```bash
docker compose ps scheduler
docker compose logs scheduler --tail 50
```

If it was down for a while, it sends only the reminders that are less than 15 minutes late when it comes back, never a backlog. `upgrade.sh` and `restore.sh` stop it while the database changes and start it again afterward.

Each schedule sets its reminders, its late-cancellation window and how long after the start a student is flagged as a possible no-show (on the schedule's edit page). Directors set the center's limits for booking blocks under **Booking blocks**. Students turn reminders off, and tutors turn them on, under **Account** → **Email reminders**.

## Adding people

Nothing to set up on the server. Directors add people on the center's **People** page: one at a time, or many from a CSV file whose first line names the columns (Email, and Full name or First name and Last name; Preferred name, Pronouns, Student ID and Role are optional). They see who will be added, and which lines need fixing, before anything changes. Between terms, **Turn off a role for everyone** turns off every student role, say, before this term's file goes in.

Someone new gets an account with no password. With single sign-on they just sign in; otherwise they choose "Forgot your password?" on the sign-in page. When `SYMPRAXI_SIGNUP_EMAIL_DOMAINS` is set, only addresses at those domains can be added. The optional welcome email goes out through the `worker` service.

## Announcements and mass email

Nothing to set up on the server. Directors post announcements and email groups of the center's people under **News** on the center page. Mass email goes out through the `worker` service and your SMTP relay, one copy per person, so a large send takes a few minutes and counts against any sending limit your relay has. Ask whoever runs the relay what that limit is before a first email to every student.

## Calendar feeds

Nothing to set up on the server. Anyone can turn on a private calendar feed under **Account** → **Calendar feed** and subscribe to it in their calendar app. Feed addresses start with the site's address, so calendar apps outside the campus network need to reach the server. If yours is reachable only on campus, feeds work only on campus. The access logs hide each feed's secret part.

## Time clock

Nothing to set up on the server. Tutors, front desk staff and directors clock in and out under **Time clock** on the center page, and directors see **Timesheets** there, which download for payroll as CSV or a spreadsheet. The `scheduler` service clocks out anyone still clocked in at midnight, in the center's time zone, so it must be running.

## Files

Students and tutors attach drafts to appointments. Files are kept in the `media` volume under `uploads/`, which Caddy doesn't serve; they download only through Sympraxi, to people allowed to open them. The largest file is `SYMPRAXI_UPLOAD_MAX_MB` in `deploy/.env` (25 by default). To allow larger files, raise it and also raise `max_size` in `deploy/Caddyfile`, which turns away any request over 30 MB.

To scan every file for viruses before anyone can open it, run the optional ClamAV service. It needs about 3 GB of memory for its virus database, which it downloads and keeps up to date itself. In `deploy/.env` set:

```bash
SYMPRAXI_CLAMAV_ADDRESS=clamav:3310
```

then start it with the rest:

```bash
docker compose --profile clamav up -d
```

ClamAV takes a few minutes to load its database the first time; files attached meanwhile wait and are checked once it's ready. Without ClamAV, files can be opened as soon as they're attached and show as not scanned.

## Registration and intake forms

Nothing to set up on the server. Directors build their center's forms in Sympraxi, under **Forms** on the center page: a registration form, asked once when a student first books, and intake forms, asked at each booking (one per schedule, or a default for every schedule). A form's questions reach students only after a director publishes them. Each later change is published as a new version, and earlier answers keep the wording students saw.

## Session-report questions and surveys

Directors add these under **Forms** too. Session-report questions appear on the session record form for tutors; only staff who read records see the answers, unless a director marks a question for the student. A survey goes to students after sessions they came to, either when the tutor finalizes the record or a set time after the appointment ends (**When surveys go out**, on the Forms page). Results are under **Survey results** on the center page, by term, once at least five people have answered in a term.

The `scheduler` service sends the invitations and the `worker` service sends their emails, so both must be running; `install.sh` starts them. Links start with `SYMPRAXI_BASE_URL`, like reminders. A survey link opens without signing in and works once, for 14 days. The answers are stored without the student's name, appointment or time of day, and the `web` service's logs write `/survey/[link]/` in place of each link. Someone with direct access to the database could still work out who answered on a day when few students did, so keep database access to the people who run the server. If you add an access log to Caddy, leave `/survey/` paths out of it.

## Reports and the full export

Nothing to set up for the reports. They're under **Reports** on the center page. Front desk staff and directors see the appointment list for a day or a week and the student list; directors also see statistics, hours offered and booked, and the full export. Each report downloads as CSV, Excel or JSON.

The `worker` service builds a full export and saves it in the `media` volume under `center-exports/`, where only the app's user can read it. Its download link works for 24 hours, and then the `scheduler` deletes the file. Caddy doesn't serve that folder: the file downloads only through Sympraxi, to a director who is signed in, and each download goes in the audit log. `backup.sh` leaves the folder out. The worker writes the ZIP to a temporary file first and then copies it into the volume, so while it builds it needs room for one copy in the container's `/tmp` and one in the `media` volume.

## Backups

```bash
cp backup.env.example backup.env   # install.sh does this for you
bash backup.sh
```

Each backup is a folder under `backups/` containing the database dump, the uploaded files, the migration list and checksums. Schedule it nightly:

```cron
15 2 * * * bash /opt/sympraxi/deploy/backup.sh >> /var/log/sympraxi-backup.log 2>&1
```

To keep encrypted copies off the server, install [restic](https://restic.net) and set `RESTIC_REPOSITORY` and `RESTIC_PASSWORD` in `backup.env`. Nobody can read those copies without `RESTIC_PASSWORD`, and it can't be recovered or reset, so keep a copy of it (see "Keys and passwords").

Backups don't include `deploy/.env` or `deploy/backup.env`. A backup that carried them would also carry the keys that protect it.

Session records and students' form answers are FERPA education records, and backups contain them. Two consequences:

- Keep backup retention in line with your records-retention policy. A record your center purges still exists in every backup until that backup expires.
- Store off-site copies only where the college controls access: campus storage, or a provider under a contract that meets FERPA's school-official requirements.

## Restoring

```bash
bash restore.sh ../backups/20270115T021500Z
```

The script checks the checksums, asks you to confirm, replaces the database and uploads, applies any newer migrations, and restarts. **Test a restore every term** on a spare VM so you know your backups work.

To restore onto a new server, install Sympraxi there first. Before running `restore.sh`, copy `SYMPRAXI_SECRET_KEY` from your saved `deploy/.env` into the new one, along with the email and single sign-on settings, and restart with `docker compose up -d`. The new server keeps its own database password; the backup doesn't need the old one.

## Keys and passwords

Two files on the server hold what the backups leave out:

| File | What's in it | Without a copy |
|---|---|---|
| `deploy/.env` | The secret key, the database password, the SMTP password and the single sign-on client secrets | A restored backup can't be used for two-factor login (below). The other values can be reset or issued again |
| `deploy/backup.env` | The restic repository, its password and the storage account's keys | Nobody can read the off-site backups. Restic can't recover or reset its password |

Keep a copy of both somewhere safe away from the server, such as the college's password vault, and update the copy whenever you change either file. `install.sh` makes both readable only by their owner; keep it that way on the server.

`SYMPRAXI_SECRET_KEY` in `deploy/.env` signs sign-in sessions and password-reset links. It also encrypts what Sympraxi stores for each authenticator app and set of recovery codes, so a copy of the database can't be used to make anyone's two-factor codes. To restore a backup you need the secret key that was in use when it was made: without it, nobody's authenticator app or recovery codes work. Passkeys still do, because Sympraxi stores only their public keys.

### Changing the secret key

Change it if it may have leaked, or when someone who had a copy of `deploy/.env` no longer should.

1. Make a new key:
   ```bash
   LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 60; echo
   ```
2. In `deploy/.env`, add a line `SYMPRAXI_SECRET_KEY_FALLBACKS=` with the current key after the `=`, and put the new key in `SYMPRAXI_SECRET_KEY`.
3. Restart with `docker compose up -d`. Everything keeps working: Sympraxi reads secrets written under either key, and people stay signed in.
4. Rewrite the two-factor secrets under the new key:
   ```bash
   docker compose run --rm web python manage.py reencrypt_mfa_secrets
   ```
5. Delete the `SYMPRAXI_SECRET_KEY_FALLBACKS` line and run `docker compose up -d` again. Anyone who hasn't opened a page since step 3 is signed out, and password-reset links sent before step 3 stop working.
6. Update your saved copy of `deploy/.env`.

If the old key may have leaked, do step 5 as soon as step 4 finishes. While the fallback is set, links made with the old key still work.

### If the secret key is lost

Put a new key in `SYMPRAXI_SECRET_KEY`, restart, and remove the authenticators no key can read:

```bash
docker compose run --rm web python manage.py reencrypt_mfa_secrets --remove-unreadable
```

Each removal goes in the audit log. Staff are asked to set up two-factor login again at their next sign-in, and students who had turned it on can sign in without it until they set it up again. Until the command runs, anyone with an authenticator app gets an error page when they enter a code.

### Changing the database password

`POSTGRES_PASSWORD` in `deploy/.env` is the password Sympraxi uses to reach PostgreSQL. PostgreSQL reads that setting only when it first creates the database, so change the password in PostgreSQL first and then in the file. Between steps 2 and 4 Sympraxi can't open new connections to the database, so some pages may fail for that minute; pick a quiet time.

1. Make a new password:
   ```bash
   LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 40; echo
   ```
2. Open PostgreSQL's prompt, set the password, and quit with `\q`. `\password` asks for it twice and keeps it out of your shell history:
   ```bash
   docker compose exec db psql -U sympraxi -d sympraxi
   ```
   ```
   \password sympraxi
   ```
3. Put the same password in `POSTGRES_PASSWORD` in `deploy/.env`.
4. Restart with `docker compose up -d`, then check with `docker compose ps` that `web`, `worker` and `scheduler` are healthy.
5. Update your saved copy of `deploy/.env`.

`backup.sh` and `restore.sh` reach PostgreSQL from inside its own container, which doesn't use the password, so backups made before the change restore as usual.

## Upgrading

```bash
bash upgrade.sh
```

It backs up, pulls the new code, rebuilds, migrates, updates the topic vocabulary and resource catalog, and restarts. Read the release notes first. Feature releases come out in late May and late December. Avoid schema-changing upgrades during a term unless a security fix requires one.

## If you change the code

Sympraxi is licensed under the AGPL. If you run a modified version, set `SYMPRAXI_SOURCE_CODE_URL` in `deploy/.env` to where your users can get your modified source. The footer's "Get the source code" link uses it.

## Troubleshooting

```bash
docker compose ps
docker compose logs web --tail 100
docker compose logs scheduler --tail 100
docker compose exec web python manage.py check --deploy
```
