Русский — [README.md](README.md) | **English**

# torrent-watchdog

A web service that watches RuTracker topics for updates and applies them in qBittorrent. What you put under watch is a link to a topic; the service stores the current `info_hash`, downloads a fresh `.torrent` from that topic on a schedule and shows the updates it finds. With `auto_update` on it replaces the torrent in a remote qBittorrent: pauses the old one, removes it with `deleteFiles=false`, adds the new `.torrent` under the same `save_path`, restores the category and tags and applies the chosen update mode.

Version `0.8.4`. Full history in [CHANGELOG.md](CHANGELOG.md).

## Running it

Docker and a settings file are all you need:

```bash
curl -O https://raw.githubusercontent.com/roxelAlex/torrent-watchdog/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/roxelAlex/torrent-watchdog/main/.env.example
docker compose up -d
```

Two containers come up: the service itself and `torrent-watchdog-flaresolverr` — our own FlareSolverr image, without which RuTracker will not hand over files (see the FlareSolverr section).

Images are built for `linux/amd64` and `linux/arm64`, so a NAS or a Raspberry Pi will do:

```text
roxelalex/torrent-watchdog:latest
roxelalex/torrent-watchdog-flaresolverr:latest
```

From source it is the same thing plus a build:

```bash
git clone https://github.com/roxelAlex/torrent-watchdog.git
cd torrent-watchdog
cp .env.example .env
docker compose up -d --build
```

In a clone Compose picks up `docker-compose.override.yml` on its own and builds locally. To use the published images there as well, add `-f docker-compose.yml`.

Open the web interface:

```text
http://SERVER_IP:8096
```

Sign in through the form at `/login` with the credentials from `.env`:

```text
admin / change_me
```

Before using it for real, change two things in `.env`, not one:

- `APP_AUTH_PASSWORD` — the password;
- `APP_SECRET_KEY` — it signs the session cookie. With the default value a session can be forged without knowing the password.

`APP_AUTH_ENABLED=false` turns sign-in off entirely. That is reasonable if the service is only reachable from a trusted network, but the port is published on all interfaces by default.

HTTP Basic is accepted as a fallback for `/api/*`, which is convenient for scripts; the web interface does not use it.

Next you need to point the service at qBittorrent and give it tracker access — both on the Settings page.

## Configuring .env

The full list of variables with their defaults lives in [.env.example](.env.example). The version is not among them: it is read from the `VERSION` file inside the image, otherwise an updated container would keep showing an old number. The key variables:

```env
APP_PORT=8096
TZ=UTC
DATABASE_URL=sqlite:////data/app.db

QB_HOST=http://192.168.1.10:8080
QB_USERNAME=admin
QB_PASSWORD=adminadmin

CHECK_HOUR=4
CHECK_MINUTE=0
CHECK_MAX_WORKERS=3

EVENT_RETENTION_DAYS=180
TORRENT_FILE_RETENTION_DAYS=30

APP_AUTH_ENABLED=true
APP_AUTH_USERNAME=admin
APP_AUTH_PASSWORD=change_me
APP_SECRET_KEY=change_me_random_secret

RUTRACKER_ENABLED=true
RUTRACKER_USERNAME=
RUTRACKER_PASSWORD=
RUTRACKER_COOKIE=
FLARESOLVER_ADDRESS=http://flaresolverr
FLARESOLVER_PORT=8191
```

If you change `APP_PORT`, docker-compose maps the same port inside and outside the container.

`QB_HOST`, `QB_USERNAME` and `QB_PASSWORD` are read exactly once — when the first client is created in an empty database. After that clients are edited on the Settings page and editing `.env` no longer affects them.

Tracker access and the FlareSolverr address are normally set on the Settings page, and what is saved there takes precedence over `.env`. The variables `RUTRACKER_USERNAME`, `RUTRACKER_PASSWORD`, `RUTRACKER_COOKIE`, `FLARESOLVER_ADDRESS` and `FLARESOLVER_PORT` act as defaults.

The service must run as exactly one uvicorn process. The scheduler lives inside the process, so a second worker would mean a second nightly check: parallel downloads of the same torrent and a race while applying an update.

## qBittorrent on another machine

qBittorrent is not started by this project's `docker-compose.yml`. It has to be reachable over HTTP through its Web API on another machine or host.

Do not use `localhost` or `127.0.0.1` in `QB_HOST`: inside a Docker container that is the `torrent-watchdog` container itself, not the machine running qBittorrent.

Setting up qBittorrent elsewhere:

1. Enable the Web UI in qBittorrent.
2. Make sure the Web UI is reachable on the machine's IP and not only on `127.0.0.1`.
3. Check the port, for example `8090`.
4. Open that port in the firewall on the qBittorrent machine.
5. Set it in the `torrent-watchdog` `.env`:

```env
QB_HOST=http://192.168.1.10:8080
QB_USERNAME=your_login
QB_PASSWORD=your_password
```

6. Check reachability from the server:

```bash
curl -I http://192.168.1.10:8080
```

7. If the service runs in Docker, check from inside the container:

```bash
docker exec -it torrent-watchdog sh
wget -S -O- http://192.168.1.10:8080
```

The `QB_USERNAME` user must be allowed to add, delete, pause, start and recheck torrents.

For qBittorrent 5.x the service automatically uses the new `stop/start` endpoints when the older `pause/resume` ones are unavailable.

Use the `Check all` button on the Settings page to test the connection — it polls every configured client. The same page lets you add several qBittorrent clients and then pick the one you want when adding a torrent.

Client statuses are cached for a few seconds, so pages do not wait on an unreachable client every time they open. The `Check all` button polls the clients again, bypassing the cache.

On the first run with an empty database a `qBittorrent` client is created from `QB_HOST`, `QB_USERNAME` and `QB_PASSWORD`.

There is no notion of a "default client". Every torrent is bound to a specific client when added, and the client can be changed in the form. Clients are listed alphabetically, and the first one is pre-selected in the add form and serves as the fallback for torrents without a binding — for example ones added before multi-client support existed.

qBittorrent categories are loaded automatically on the add page and are available through the API:

```text
GET /api/qbittorrent/categories
GET /api/qbittorrent/clients
```

If qBittorrent is unreachable the application still starts. `/health` reports the qBittorrent status, and add or update operations fail with an error in the interface and in `check_events`.

## Adding your first torrent

Only RuTracker topics can be put under watch:

```text
https://rutracker.org/forum/viewtopic.php?t=123456
```

Magnet links and direct `.torrent` links are not accepted — the form and the API reject them with an explanation. The point is that a topic is a stable address: a fresh `.torrent` is downloaded from it every time, so there is something to compare the file list against. A magnet has no torrent file at all, and its `info_hash` never changes, so an update cannot be detected through it even in principle.

Open `Add` and fill in:

- the RuTracker topic link;
- the category and tags if you need them;
- the download folder — only to override the category's path;
- the `auto_update`, `recheck_after_add`, `start_after_recheck` and `add_paused` options.

After that the service downloads the `.torrent` from the topic, computes the `info_hash`, adds the torrent to qBittorrent and creates the first version history entry.

### The download folder and the category

These are not two independent fields. A qBittorrent category usually has a path of its own, and an empty "download folder" means "put it there too". A filled one overrides the category path — which is exactly what qBittorrent does when it receives both `savepath` and `category`.

The category is picked from a dropdown listing every category of the client along with its path, and a "custom category" entry opens fields for a name and, optionally, a path — the category will be created in qBittorrent. A category that has disappeared from the client stays in the list marked "not in the client" so it is not silently replaced on the first save.

A category may have no path of its own. That does not mean "dump it in the common pile": qBittorrent puts such torrents into a subfolder named after the category inside the default folder, so `lidarr` without a path means `/downloads/lidarr`. The service shows the resulting path rather than the default one.

That is why the category comes first in the form, and the folder placeholder shows where the torrent will land if the field stays empty: `same as the category: /music`, or `the client default: /downloads` when the category has no path. The default path is not invented — it comes from `GET /api/v2/app/preferences` of the client selected in the form. The folder field also has suggestions: the default path and every category path. Changing the client rebuilds both the category list and the suggestions.

The torrent page shows the resulting path and where it comes from — `/music · from category "music"`.

Below the category field it says what will happen to the files. qBittorrent reports this through `torrent_changed_tmm_enabled`: with it enabled the torrent is relocated along with the category, with it disabled the files stay put. Nothing needs guessing — the service asks and states it plainly.

## Checking for updates

The scheduler runs daily at `CHECK_HOUR:CHECK_MINUTE` in the `TZ` timezone. The schedule applies after a container restart; the Settings page shows it but does not let you edit it.

Manual checks: the `Check` button in the registry and `Check now` on the torrent page.

Torrents are checked in parallel, up to `CHECK_MAX_WORKERS` at a time. FlareSolverr calls are strictly serialised though: there is one browser in the container, and two simultaneous Cloudflare challenges both fail.

If the `info_hash` is unchanged the status stays `active`. If it changed, a new version is created and the status becomes `update_available`.

After the nightly check a cleanup runs: events older than `EVENT_RETENTION_DAYS` and `.torrent` files older than `TORRENT_FILE_RETENTION_DAYS` that no version references are deleted. Set either to zero to disable the cleanup.

## Auto update

With `auto_update` enabled a newly found version is applied automatically:

1. the old torrent is paused;
2. the old torrent is removed from qBittorrent with `deleteFiles=false`;
3. the new `.torrent` is added under the same `save_path`;
4. the previous category and tags are restored;
5. the chosen update mode is applied;
6. the torrent is started if that is enabled.

Two modes are available:

- `Download new files only` — the mode for a media library. The service compares the old and new `.torrent`, marks files already present as `do not download` in the new torrent and leaves the new ones selected. No `recheck` is started, so qBittorrent does not re-download files that already carry your tags.
- `Replace entirely and verify files` — the classic mode: add the new torrent and start a recheck. qBittorrent verifies the files already on disk and downloads only what is missing.

If the saved `.torrent` of the previous version is unavailable there is nothing to compare against. In that case the `new files only` mode does not set priorities but starts a recheck instead: otherwise qBittorrent would assume the disk is empty and download the whole torrent again. The journal says so explicitly.

## When files are deleted

By default, never. The service removes torrents from qBittorrent with `deleteFiles=false` and the data stays on disk.

The single exception is enabled by the `Delete files on a full repack` switch on a torrent (the global default is `DEFAULT_DELETE_REPLACED_FILES`, `false`). Files of the previous version are deleted only when everything below holds at once:

- the switch is on for this torrent;
- this is not a rollback;
- the file list could be compared;
- **not a single** file matches, meaning the releaser repacked the torrent from scratch;
- a previous torrent exists and differs from the new one.

Any condition failing means "do not touch". The decision is made by one function, `may_remove_replaced_files` in `app/services/update_applier.py`, with a test for every branch.

The order of operations is special in this case: the new torrent is added and its registration confirmed first, and only then is the old one deleted with its files. Otherwise a failure while adding would leave you without data and without a replacement.

**Partial updates never delete files.** When some files match and some are dropped, there is nothing to remove the dropped ones with: qBittorrent can only delete all files of a torrent at once, and some of them are needed by the new version. The service writes to the journal what stayed on disk.

Removing a torrent from tracking and rolling back never delete files under any settings.

## RuTracker

This is the only supported source. For a link like:

```text
https://rutracker.org/forum/viewtopic.php?t=123456
```

the resolver takes the topic id and downloads:

```text
https://rutracker.org/forum/dl.php?t=123456
```

### Authorisation

The main way is a username and password on the Settings page. The service signs in on its own and stores the session cookie; when it expires the service notices (a page arrives instead of a `.torrent`) and signs in again without asking you for anything.

Signing in is performed by a browser inside FlareSolverr: Cloudflare guards the form and an ordinary request cannot get through. That is why automatic sign-in only works with our own image from this compose file — with a third-party FlareSolverr you have to paste the cookie by hand.

The `Test sign-in` button on the Settings page signs in immediately and shows the result, so you do not have to wait for the nightly check to learn whether the password is right.

The password is stored in the database in plain text, same as the qBittorrent passwords.

Repeated sign-ins happen no more often than `RUTRACKER_LOGIN_MIN_INTERVAL_SECONDS` (5 minutes by default): a wrong password must not hammer the tracker. The button on the Settings page bypasses that limit — a human pressed it.

**Captcha.** The tracker shows one after failed attempts and on suspicious activity from a new IP. Nothing can solve it automatically. In that case the service says so plainly and you need to sign in from a browser once and paste the cookie by hand:

```env
RUTRACKER_COOKIE=bb_session=...
```

The cookie field is on the Settings page too, inside the `Cookie by hand` block. The cookie obtained by automatic sign-in lands there as well. What is saved in the settings takes precedence, with `.env` as the fallback.

The full Cookie string is required: `cf_clearance` alone is not enough, that is a Cloudflare cookie rather than tracker authorisation. The string must contain `bb_session`, `bb_t` or `bb_data`.

### FlareSolverr

When RuTracker shows a Cloudflare challenge a `.torrent` cannot be fetched with a plain HTTP request. A second container, `torrent-watchdog-flaresolverr`, is started for that in `docker-compose.yml`.

This is **not the stock FlareSolverr image**. It is built from [Dockerfile.flaresolverr](Dockerfile.flaresolverr) and adds a `POST /download` endpoint ([flaresolverr-extended.py](flaresolverr-extended.py)) to the regular FlareSolverr: it passes the Cloudflare check and downloads the file with the browser itself through CDP `Page.setDownloadBehavior`. Stock FlareSolverr cannot do that — it only returns page HTML, while `dl.php` sends a binary file. The same image also carries `POST /login` for signing in.

The address and port are set on the Settings page or in `.env`:

```env
FLARESOLVER_ADDRESS=http://flaresolverr
FLARESOLVER_PORT=8191
```

The hostname matters here. Browser downloading is enabled only when the host is exactly `flaresolverr`, that is when talking to our own container from this compose file. Any other address is treated as stock FlareSolverr and the service falls back: it obtains fresh cookies and a User-Agent through it and then downloads the `.torrent` with an ordinary request. The fallback does not work on every topic.

An empty address disables FlareSolverr — the service downloads directly using the stored cookie.

Do not expose FlareSolverr to the internet; it should be reachable only by torrent-watchdog. Its port is not published in `docker-compose.yml`.

Secrets and passkeys in URLs are masked in the logs, and the tracker password never reaches them.

If RuTracker is temporarily unavailable or returns something other than a `.torrent`, the resolver retries — three times by default. When a page arrives instead of a file it is almost always an expired session: with a username and password set the service signs in again between attempts. Settings:

```env
RUTRACKER_RETRY_DELAY_SECONDS=10
RUTRACKER_MAX_ATTEMPTS=3
```

`RUTRACKER_MAX_ATTEMPTS=0` means retry indefinitely, but a finite number is better in practice. Otherwise a DNS or network problem can make a manual or scheduled check never finish. If the cookie is missing or the resolver is disabled the error is returned immediately, because retrying cannot fix a configuration problem.

## Recovering from an error

Open the torrent page. It shows `last_error` and the event history. You can:

- press `Check now`;
- apply the found version manually;
- apply one of the saved older versions from the history.

A rollback takes the same path as an ordinary apply: it removes the current torrent from qBittorrent with `deleteFiles=false`, adds the saved older `.torrent`, applies the torrent's update mode and updates the current `info_hash`. Files on disk are not deleted.

A torrent stuck in the `updating` state because the service was restarted mid-apply is moved to `error` on the next start, with an explanation in the journal.

## Journal and logs

Check events are on the `Journal` page — the last 300 records with filters by event type and torrent. Container logs:

```bash
docker logs -f torrent-watchdog
```

Secret URL parameters are masked in the logs, for example `passkey=***`. Cookies and passwords are never printed.

## Data

The SQLite database:

```text
./data/app.db
```

Saved `.torrent` files:

```text
./data/torrents/<tracked_id>/<info_hash>.torrent
./data/torrents/_pending/<info_hash>.torrent
```

`_pending` is a staging folder: the source is downloaded there before the torrent has an `id`, after which the file is moved. If adding failed the file stays there and is removed by the cleanup.

Inside the container these paths live under `/data`. With SQLite in WAL mode `app.db-wal` and `app.db-shm` sit next to the database — that is normal; to copy the database take them along or stop the container first.

## Telegram notifications

The bot reports what needs attention: an update was found, an update was applied, a check failed, qBittorrent is unreachable. A message looks like this:

```text
🆕 Update found
   HOYO-MiX — Zenless Zero Zone OST [FLAC, 24 bit]

   Update found. New files: 3, already present: 470, removed from the torrent: 0.
```

Every event type has its own icon — 🆕 an update, ✅ applied, ❌ failed, ⚠️ an error, 🔌 qBittorrent unreachable — so the chat list tells you what happened without opening the message.

The set of events is chosen with checkboxes on the `Settings` page; routine no-change checks are off by default, otherwise the bot would write twice a day about nothing.

What you need:

1. Create a bot with [@BotFather](https://t.me/BotFather) and get the token.
2. Find out the chat ID — [@userinfobot](https://t.me/userinfobot) will tell you. A group ID starts with a minus sign and the bot has to be added to the group.
3. Enter both on the `Settings` page and press `Send a test`: the result is immediate, no need to wait for the nightly check.

```env
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
NOTIFY_LANGUAGE=ru
```

**The notification language is configured separately** from the interface language: the scheduler has neither a request nor a cookie, and a different person may well be reading the messages. The list of languages is the same as in the interface — a new language is picked up here too.

Sending happens in a separate thread and never delays a check. If Telegram is unreachable the check still counts as done: a silent bot is no reason to consider a torrent unchecked. The reason goes to the container log as a warning.

The token is stored in the database in plain text, like the qBittorrent and tracker passwords, and is not shown in the form: an empty field means "keep it".

## Interface language

The interface is available in Russian and English. The header shows the flag and code of the current language on the right; clicking it opens the list. The choice is remembered in a cookie for a year and does not depend on who signed in. The default language is set in `.env`:

```env
APP_LANGUAGE=ru
```

The journal is translated as well: the database stores an event code and its parameters, and the text is assembled at display time. Switching the language therefore changes old records too, not just the menu. Entries written before translations existed were parsed back into codes by their known wording; whatever could not be parsed is shown exactly as it was recorded.

### Adding a language

1. Copy [app/locales/ru.py](app/locales/ru.py) to `app/locales/<code>.py` — for example `de.py`.
2. Replace `NAME` with the language name, `FLAG` with its flag, `DATE_FORMAT` with the date format used there, and translate the values. **Do not change the keys** — everything is found by them.
3. Rebuild the image: `docker compose up -d --build torrent-watchdog`.

Nothing else is needed: `app/locales` is scanned at startup and the language appears in the switcher on its own. It does not have to be registered in code.

The flag is optional — without it the language gets a globe icon. Translating everything at once is not required either: missing keys fall back to the Russian catalogue and the page does not break. A test checks completeness: it fails when a catalogue is missing keys, has extra ones, or loses a placeholder such as `{count}`.

Error and event texts live in the catalogue too, under the `error.*` and `msg.*` keys.

## Publishing images

GitHub Actions builds and publishes on a version tag:

```bash
git tag v0.8.5 && git push --tags
```

The workflow checks the tag against the `VERSION` file and refuses to publish when they disagree — otherwise an image would ship under the wrong number. It then builds both images for two architectures and pushes them tagged `0.8.5` and `latest`. Release notes are taken from `CHANGELOG.md` by `scripts/release_notes.py`, so the same text is never written twice.

The repository secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` are required (create the token in Docker Hub → Account Settings → Personal access tokens; `Read & Write` is enough to publish, updating the Docker Hub description needs broader rights).

Building and pushing by hand works too:

```bash
docker buildx build --platform linux/amd64,linux/arm64 --push \
  -t roxelalex/torrent-watchdog:0.8.5 -t roxelalex/torrent-watchdog:latest .
docker buildx build --platform linux/amd64,linux/arm64 --push \
  -f Dockerfile.flaresolverr \
  -t roxelalex/torrent-watchdog-flaresolverr:0.8.5 \
  -t roxelalex/torrent-watchdog-flaresolverr:latest .
```

To run images from another account or local ones, set this in `.env`:

```env
IMAGE_PREFIX=another-account
IMAGE_TAG=0.8.4
```

## Database schema

Alembic owns the schema. Migrations run when the container starts; nothing has to be launched by hand.

The baseline revision handles both an empty database and one that has been running since the early versions: it skips existing tables and creates missing columns and indexes. Before that the schema was maintained by `create_all` and seven hand-written `ALTER TABLE` statements.

A new migration is created the usual way:

```bash
docker compose exec torrent-watchdog alembic revision --autogenerate -m "what changed"
```

A test keeps migrations from drifting away from the models: it creates an empty database, runs `upgrade head` and compares tables, columns and indexes against `app/models.py`.

## Guarding against leaked secrets

The repository ships a hook that scans file contents before a commit for Docker Hub, GitHub and Telegram token prefixes and RuTracker session cookies. Enable it with one command after cloning:

```bash
git config core.hooksPath scripts/git-hooks
```

It checks contents rather than names: a `.gitignore` mask only catches files called `token` or `secret`, while a secret can just as easily end up in `docker.txt` or `notes.md`.

## Tests

```bash
docker run --rm -v "$PWD:/src:ro" -w /tmp/proj python:3.12-slim \
  sh -c "cp -r /src/. /tmp/proj && pip install -q -r requirements-dev.txt && python -m pytest -q"
```

Covered: `info_hash` computation, file list comparison between versions, pending version selection, category path resolution, category choice, translation completeness, the file deletion decision and database migrations.

The page script is checked separately — its syntax and that it survives page load:

```bash
docker run --rm -v "$PWD:/src:ro" -w /src node:22-slim \
  sh -c "node --check app/static/app.js && node tests/js/smoke.js /src/app/static/app.js"
```

The load check is not a formality: reaching a `const` before its declaration kills the whole file and the page silently loses its interactivity, which `node --check` does not see.

## Licence

MIT — see [LICENSE](LICENSE).
