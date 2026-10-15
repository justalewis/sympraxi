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

Then run the script again. It builds the image, creates the database tables, loads the topic vocabulary and resource catalog, and starts everything:

```bash
bash install.sh
```

Create the first site administrator:

```bash
docker compose exec web python manage.py createsuperuser
```

Sign in at `https://writing.example.edu`. You'll be asked to set up two-factor login before anything else. Then open **Site administration** to create your center and add its director.

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

Someone without an account gets one at their first sign-in, as long as `SYMPRAXI_SIGNUP_OPEN` is true (the default). A director then gives them a role as usual.

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
| `worker` | Background jobs, such as sending email |
| `scheduler` | Each minute, sends reminder emails and flags possible no-shows |
| `db` | PostgreSQL 18 |

Data lives in Docker volumes: `pgdata` (the database), `media` (uploads), and `caddy_data` (certificates).

## Reminders and attendance

The `scheduler` service sends reminder emails and flags students no one marked as here, once a minute. `install.sh` starts it with everything else. Links in reminders start with `https://` and your `SYMPRAXI_DOMAIN`; set `SYMPRAXI_BASE_URL` in `deploy/.env` only if people reach the site at a different address.

Its health check passes only if a sweep finished in the last three minutes. Check it with:

```bash
docker compose ps scheduler
docker compose logs scheduler --tail 50
```

If it was down for a while, it sends only the reminders that are less than 15 minutes late when it comes back, never a backlog. `upgrade.sh` and `restore.sh` stop it while the database changes and start it again afterward.

Each schedule sets its reminders, its late-cancellation window and how long after the start a student is flagged as a possible no-show (on the schedule's edit page). Directors set the center's limits for booking blocks under **Booking blocks**. Students turn reminders off, and tutors turn them on, under **Account** → **Email reminders**.

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
