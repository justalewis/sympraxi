# Video rooms with Jitsi

Sympraxi can give every online appointment its own video room on a Jitsi server you run. It is optional. A center that skips it keeps using tutors' meeting links, and everything else works the same.

This page covers what Sympraxi does, what the Jitsi server has to be set up to accept, and how to get video through a campus network that blocks most traffic. The first two parts follow the Jitsi project's documentation for its Docker setup; check them against the version you install, because its settings change between releases. The last part, on locked-down networks, is guidance and has to be tested on your own network before you rely on it.

## What Sympraxi does

- Each online appointment gets one room. The room's name is a keyed hash of the appointment's ID (`sympraxi-` and 24 characters), so it can't be worked out from anything people can see.
- Nobody enters with just the name. Sympraxi signs a token (a JSON Web Token, HS256) for one person and one room. It names the person, says whether they moderate, and is good from ten minutes before the start until half an hour after the end.
- Only the appointment's tutor and the students booked in it are handed a token, from the **Join the video session** button on the appointment page. Directors and front desk staff aren't in the session, so they don't get one. The tutor is the moderator.
- The button opens the room in the same window. The token is in the address Sympraxi redirects to, so that response is never cached and sends no referrer.
- Each entry is written to the audit log (who, which appointment, and whether as moderator). The conversation itself is not recorded and doesn't touch Sympraxi.

## What Jitsi has to accept

Run Jitsi Meet on its own host name, for example `meet.example.edu`, with JWT sign-in turned on and guests turned off, so no one can open a room without a token from Sympraxi. In the `.env` file of the `docker-jitsi-meet` setup, that is:

```bash
PUBLIC_URL=https://meet.example.edu
ENABLE_AUTH=1
AUTH_TYPE=jwt
ENABLE_GUESTS=0
JWT_APP_ID=sympraxi
JWT_APP_SECRET=<a long random secret>
JWT_ACCEPTED_ISSUERS=sympraxi
JWT_ACCEPTED_AUDIENCES=jitsi
ENABLE_LOBBY=1
```

Generate the secret with `openssl rand -hex 32`. `JWT_APP_ID` is the token's issuer, and the audience Sympraxi sets is `jitsi`. The lobby makes students wait until the tutor, the moderator, lets them in.

## What Sympraxi needs

Put three lines in `deploy/.env` and restart the `web` service:

```bash
SYMPRAXI_JITSI_DOMAIN=meet.example.edu
SYMPRAXI_JITSI_APP_ID=sympraxi
SYMPRAXI_JITSI_APP_SECRET=<the same secret>
```

The secret belongs only in these two files. With any of the three empty, no center can turn video on.

Then a director opens **Video rooms for online appointments** under **Center tools** on the center page and turns video on. From then on:

- A tutor's online availability no longer needs a meeting link. One they have still works, and an appointment with a link shows the link.
- A day moved online (**Switch a day to online**) takes in tutors who have no link.
- Calendar files for online appointments say to join from the appointment page.

Appointments booked before video was turned on, and in-person appointments, get no room.

## Getting through a campus network

Browsers send Jitsi's media over UDP to the video bridge (port 10000 by default). Where the network blocks UDP, clients fall back to relaying it through a TURN server, and where it allows only web traffic, the TURN server must be reachable on TCP port 443 over TLS. The Jitsi Meet documentation describes two ways to do that: run coturn on a separate address so it can take 443 itself, or share 443 with the web server by routing on the host name (a `turn.example.edu` name, with the proxy passing TLS traffic for it through to coturn without opening it). The Docker setup's `.env` has `TURN_HOST`, `TURNS_HOST`, `TURN_PORT` and `TURNS_PORT` settings for an external TURN server.

Test it before you promise it. From a computer on the campus network, or on a phone hotspot configured to block UDP, join a room and look at the connection details in Jitsi's participant menu: a connection that works over relay shows its transport. This is the exit test for video in the build plan, and only your own network can answer it.

## What it doesn't do yet

- Live captions are worth evaluating in Jitsi; Sympraxi does nothing with them yet.
- Rooms aren't recorded. Recording student sessions raises consent questions (see `docs/01-feature-map.md`), so it stays off until a center asks and there is a way to record the consent.
- With the shared document on (see "Shared documents" in install.md), an online appointment's room page puts the video in a frame beside the document. Otherwise the video opens in its own window.
