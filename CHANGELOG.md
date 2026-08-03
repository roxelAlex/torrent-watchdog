# Changelog

Русская версия — [CHANGELOG.ru.md](CHANGELOG.ru.md).

## 0.8.6

- Nightly checks kept failing while the same check run by hand hours later went through. Cloudflare turns RuTracker away in windows tens of minutes long, and three attempts ten seconds apart burned through all of them inside a single window. The pause between attempts is now 1800 seconds and there are five of them, so the check catches the moment the tracker starts letting requests through again instead of waiting for someone to press the button.
- The budget for passing Cloudflare is 120 seconds instead of 60. On bad nights loading the challenge page alone ate 25–36 of those 60 seconds and the attempt timed out where the browser had seconds left to go; when things are fine the challenge is solved in 12–14 seconds, so the headroom costs nothing. Client-side timeouts were raised to match — otherwise the app would cut the connection before FlareSolverr gives up, and the real reason would never reach the journal.
- A retry after logging in again no longer waits out the long pause. An expired session is fixed by the fresh cookie, and there is nothing left to wait for; the long pause now applies only to Cloudflare refusals.

## 0.8.5

- Release notes are generated from `CHANGELOG.md` by `scripts/release_notes.py`, so a tag no longer arrives with an empty description. All six existing releases were backfilled.
- Documentation is bilingual: `README.en.md` alongside the Russian README, `CHANGELOG.md` in English with `CHANGELOG.ru.md` keeping the Russian original. The GitHub and Docker Hub descriptions are in English.

## 0.8.4

- The version is read from the `VERSION` file inside the image instead of `APP_VERSION` in `.env`. That file is copied once and never touched again, so after updating the container the page footer and `/health` would have kept showing the old number next to new code. The version is now a property rather than a settings field, so an environment variable cannot override it.

## 0.8.3

- Publishing images no longer fails over cosmetics: the Docker Hub description step is marked optional, and one image failing no longer cancels the other.

## 0.8.2

- Alembic now owns the database schema, replacing `create_all` and seven hand-written `ALTER TABLE` statements. The baseline revision handles both an empty database and one that has been running since May: it skips what exists and creates what is missing. Verified on a copy of the live database — 2 torrents, 6 versions and 241 events survived intact.
- A test keeps migrations from drifting away from the models: it creates an empty database, runs `upgrade head` and compares tables, columns and indexes against `app/models.py`. Drift would otherwise only surface for someone installing from scratch.
- Description and topics on GitHub; the Docker Hub description is updated alongside image publishing, using the same secrets and a separate short file because the README exceeds the length limit.

## 0.8.1

- Fixed: with authentication enabled not a single page opened — every one returned 500. `SessionMiddleware` was registered before the auth check, and Starlette makes the last-registered middleware the outermost layer, so the check reached for `request.session` before it existed. The bug stayed invisible under `APP_AUTH_ENABLED=false`, while `.env.example` ships with authentication on — meaning every fresh install was broken.
- The ordering is now locked down by tests: the layer order itself, plus that a page redirects to the login form and the API answers 401 instead of crashing.
- Authentication texts are translated — they used to be Russian regardless of the interface language.
- README links point at the `main` branch instead of a `master` that does not exist.

## 0.8.0

- New per-torrent switch `Delete files on a full repack`. When a releaser reissues a torrent from scratch and not a single file matches, the previous version's files no longer linger on disk as garbage — they are deleted through qBittorrent. Until now the service never deleted files, and such a repack left gigabytes of dead weight behind.
- Five conditions are checked together: the switch is on, this is not a rollback, the file list could be compared, there are zero matches, and a previous torrent exists. Any one of them failing means "do not touch". The decision lives in a dedicated function with a test for every branch.
- The order of operations changed: the new torrent is added and its registration confirmed first, and only then is the old one deleted with its files. The previous order would have left you without data and without a replacement if adding failed.
- A rollback never deletes files — there the dropped files are precisely the ones it exists to bring back.
- Partial updates leave files alone: there is nothing to delete them with, since qBittorrent only knows "all files of a torrent at once". Such files are now listed in the journal so at least you know about them.
- The global default is off: updating the image must not change behaviour on other people's installations.

## 0.7.7

- Fixed the message shown for a repacked torrent. When a releaser replaced the rip entirely and not a single file matched, the service claimed "the file list could not be compared: its saved .torrent is missing" — even though the file was there and the comparison had succeeded, there simply were no files in common. A real case: three AMZN rips of "Yani Neko" were replaced by five NF ones.
- The two outcomes are now separated: "nothing to compare against" and "compared, nothing matches". The second reports how many files left the torrent and how many will be downloaded.
- The same fix in the pre-apply summary: it no longer promises that existing files will be skipped when there are zero matches.

## 0.7.6

- Ready to publish: MIT licence, images `roxelalex/torrent-watchdog` and `roxelalex/torrent-watchdog-flaresolverr` for `linux/amd64` and `linux/arm64`.
- `docker-compose.yml` pulls the published images, while building from source moved to `docker-compose.override.yml` — Compose picks it up automatically, so the command stays the same in a clone while anyone who downloaded only the compose file has nothing to build.
- GitHub Actions: tests on every push, publishing on a version tag. The tag is checked against the `VERSION` file first, otherwise an image would ship under the wrong number.
- Defaults no longer describe somebody's home network: `TZ=UTC` instead of `Asia/Yekaterinburg`, a neutral qBittorrent address.
- Provenance labels added to the images, and `Dockerfile.flaresolverr` now states that it extends the official MIT-licensed FlareSolverr.

## 0.7.5

- Fixed: on the add page neither the custom category, nor the path suggestions, nor the delete confirmations worked. The script reached a `const` before its declaration and died entirely at load — and because the guard read `qbClientSelect &&`, everything kept working on the torrent page where that element is absent, so the breakage showed up in exactly one place.
- All elements are now looked up at the top of the file, before functions and bindings.
- Added a load-time check for the script: `node --check` only catches syntax and lets this class of error through.

## 0.7.4

- Fixed the folder shown for categories without a path of their own. qBittorrent puts such torrents into a subfolder named after the category inside the default path — `lidarr` without a path means `/downloads/lidarr`, verified against live clients. The service showed plain `/downloads` plus a note saying "no path set", which was wrong in both directions. The rule now lives in one place on the server, and templates and script use the computed path.
- Such categories are back in the download-folder suggestions: they used to drop out silently even though they do have a folder.
- A custom category can be given a path right away. An empty field means "let qBittorrent decide" — which is that same named subfolder.

## 0.7.3

- The category is picked from an ordinary dropdown listing every category of the client along with its path. It used to be an autocomplete field, and the browser filters suggestions by whatever is already typed: on the torrent page the field is pre-filled, so the list always held exactly one row — the current one.
- The "custom category" entry opens a field for a new name. The category is genuinely created in qBittorrent: `setCategory` answers `409` for a category that does not exist, so without creating it the entry would only have worked in appearance.
- A category that disappeared from the client is not lost silently — it stays in the list marked "not in the client".
- The download folder gained suggestions: the client's default path and the category paths. Nothing is invented, everything comes from the qBittorrent API.
- The category list and the path suggestions are rebuilt when the client changes.

## 0.7.2

- The default path is taken from qBittorrent itself (`GET /api/v2/app/preferences`) instead of being described in words: the form now says `the client default: /downloads`.
- The category field states what will happen to the files. The client reports this through `torrent_changed_tmm_enabled`; both current clients have it enabled, so changing a category relocates the torrent.

## 0.7.1

- The download folder and the category no longer look independent. A qBittorrent category usually has its own path, an empty folder field means "put it there too", and a filled one overrides it — which is exactly what qBittorrent does when given both `savepath` and `category`. The form now shows this: the category comes first, and the folder placeholder fills in the selected category's path.
- The torrent page shows the resulting path and where it comes from. It used to show a dash even though the files sat in the category folder: both current torrents have an empty `save_path` while qBittorrent keeps them in `/music` and `/series`.

## 0.7.0

- Telegram notifications with formatting: an icon per event type, the event in bold, the torrent title in italics and the text below. The icon makes the chat list readable without opening the message. Everything interpolated is escaped — otherwise an angle bracket in a torrent title stops Telegram from delivering the message at all.
- The bot reports found updates, applied updates and errors. The set of events is chosen with checkboxes; routine no-change checks are off by default.
- The notification language is configured separately from the interface language: the scheduler has neither a request nor a cookie, and a different person may be reading the messages.
- Sending happens in a separate thread and never brings a check down: a silent Telegram is no reason to consider a torrent unchecked, the reason goes to the log.
- A "Send a test" button on the settings page checks the token and chat ID right away.
- The token, like the passwords, is not shown in the form; an empty field means "keep it".
- The notification form has its own route: the shared one would have wiped the neighbouring form's fields with empty values.

## 0.6.0

- The interface speaks two languages, Russian and English. A dropdown in the header shows the flag and code; the choice is remembered in a cookie for a year, and the default language is set by `APP_LANGUAGE`.
- The journal and torrent errors are translated too. The database stores an event code and its parameters, and the text is assembled at display time — an event lives for years while the reader's language may change tomorrow.
- Entries written before translations existed were parsed back into codes by their known wording: 254 of 255 recognised, the remaining legacy entry is shown exactly as it was recorded.
- Dates use ISO format in the English version and the familiar `dd.mm.yyyy` in Russian.
- Error texts are raised as a code plus parameters and translated in the router: they used to be Russian regardless of the page language.
- Hints drawn by JavaScript are translated as well: the texts arrive from the template in data attributes rather than being hard-coded in the script.
- A new language is added with a single file in `app/locales` — the directory is scanned automatically, nothing needs registering. Missing keys fall back to Russian instead of breaking the page.
- A test keeps the catalogues from drifting: it checks for missing and extra keys, matching placeholders inside strings, and that every key used in templates exists.

## 0.5.0

- Signing in to RuTracker with a username and password instead of pasting cookies by hand. The service signs in on its own and refreshes the cookie when the session expires: if a page arrives instead of a `.torrent`, it logs in between download attempts and retries.
- Signing in is performed by the browser inside our own FlareSolverr image — a new `POST /login` endpoint. Cloudflare guards the form, and the tracker expects the submit button's value in cp1251; a genuine form submission solves both.
- A "Test sign-in" button on the settings page: the result is visible immediately instead of after the nightly check.
- A captcha cannot be automated, and the service says so plainly instead of failing with a vague error. The cookie field remains as an escape hatch and moved into a "Cookie by hand" block; the cookie obtained by automatic sign-in lands there too.
- Repeated sign-ins happen at most once every 5 minutes so a wrong password does not hammer the tracker. Parallel checks do not each sign in: one does, the rest pick up its result.
- The password is not shown in the form and never reaches the template; an empty field means "keep it".
- The FlareSolverr transport moved into its own module: both downloading and signing in go through it.

## 0.4.0

qBittorrent clients:

- The notion of a "default client" is gone: the badge, the "Make default" button, the flag on the add form and the `is_default` column in the database. A torrent is already bound to a specific client when added, and a separate marker merely duplicated that choice while quietly deciding on the user's behalf.
- Clients are listed alphabetically. The first one is pre-selected in the add form and serves as the fallback for torrents without a binding.

Source:

- Only RuTracker topic links are accepted. Magnet links and direct `.torrent` links are rejected by the form and the API with an explanation. A topic is a stable address that yields a fresh `.torrent` every time; a magnet has no torrent file at all and its `info_hash` never changes, so an update cannot be detected through it even in principle.

Interface:

- The interface was redrawn: colour now appears only where a human decision is needed. Rails and beacons were removed from calm rows.
- The registry gained a watch strip — the last 14 check outcomes, with stroke height encoding severity. It immediately revealed a past run of failures that had been visible nowhere.
- Counter tiles were replaced by a service status line: what needs attention, when the last and next checks are, how many clients are reachable. The next check time comes from the live scheduler job.
- Hour and minute fields were removed from the settings: the form wrote them to the database while the scheduler read only `.env`. The schedule is now shown as a fact.

Texts brought in line with behaviour:

- The summary for a found version no longer promises that "existing files will not be downloaded again" in full-replacement mode.
- When there is nothing to compare against, the report no longer looks like a success, and the update starts a recheck instead of re-downloading the whole torrent.
- The add form states that magnet links are added but not tracked for updates.
- The "Check all" button checks every client rather than only the default one, as it did under its former name "Check connection".
- The message about a missing RuTracker cookie points at the settings page too, not just at `.env`.
- A torrent stuck in the "updating" state after a service restart is moved to an error state with an explanation.

Performance:

- The qBittorrent probe received a separate short timeout, one request instead of two, parallel polling and a cache. An unreachable client used to cost a page 30 seconds on every render.
- SQLite switched to WAL, `busy_timeout` and `foreign_keys` enabled, composite indexes added.
- The watch strip is built with a single query for the whole registry, and the client is loaded via `joinedload`.
- Adding a torrent downloads the source once instead of twice.
- Checks run in parallel, but FlareSolverr calls are serialised: two simultaneous Cloudflare challenges in one browser both fail.

Fixes:

- `latest_pending_version` can no longer offer a version older than the current one for applying. On live data it offered versions from 13 June and 19 July instead of the current ones from 9 and 21 July; through the API that would have been applied silently.
- After the nightly check, events older than 180 days and unreferenced `.torrent` files older than 30 days are removed.
- The container runs as a non-root user; a healthcheck and `.dockerignore` were added — the build context used to include the `data` folder with the database and cookies.
- Tests added: `info_hash`, file list comparison, pending version selection.

## 0.3.7

- Changing the qBittorrent category was added to the torrent page.
- Categories are loaded from the qBittorrent client the torrent is bound to.
- A category change is saved to qBittorrent and SQLite together and recorded in the event history.
- New API endpoint `POST /api/torrents/{id}/category`.

## 0.3.6

- Fixed matching qBittorrent files against those from the `.torrent`: the torrent's root folder in the qBittorrent path is now taken into account.
- The `New files only` mode now correctly deselects existing files and keeps new ones selected.

## 0.3.5

- Recovery after a partially completed update was added.
- If the new torrent is already present in qBittorrent, the service reuses it instead of adding it again and failing with `409 Conflict`.
- Applying the same version twice no longer deletes the target torrent that was already added.

## 0.3.4

- Fixed applying updates when qBittorrent is slow to register the new torrent.
- A transient `404` from the file list is now retried for up to 30 seconds.
- Once the wait runs out, a clear error is reported instead of continuing with file priorities left unapplied.

## 0.3.3

- New `updated` status: after an update is applied the beacon turns violet without pulsing.
- The `updated` status holds until the next check; if nothing changed, the torrent returns to `active`.

## 0.3.2

- Coloured status beacons: an available update blinks amber, an error red, an update in progress violet.
- Times in the web interface are shown in the local `dd.mm.yyyy hh:mm` format with the `Asia/Yekaterinburg` timezone.
- Hash values were removed from ordinary lists, version history and events; they remain only in the collapsed technical details block.

## 0.3.1

- On the add page, qBittorrent categories are now loaded dynamically from the selected client.

## 0.3.0

- New `New files only` update mode.
- The service compares the file lists of the old and the new `.torrent`.
- When an update is applied, old files are marked `do not download` in qBittorrent while new ones stay selected.
- In `New files only` mode no recheck is started, so qBittorrent does not re-download locally modified files carrying tags.
- `TZ=Asia/Yekaterinburg` is set explicitly in the Dockerfile.

## 0.2.1

- Compatibility with qBittorrent 5.2: a successful login may return `204 No Content` and a session cookie instead of the text `Ok.`.

## 0.2.0

- Support for multiple qBittorrent clients.
- Existing torrents are bound automatically to the `Основной` client created from the current `.env` settings.
- The add form gained a qBittorrent client selector.
- The settings page gained a client list, a connection check and a form for adding a new client.
- The interface was simplified: fewer technical fields on the main page and the torrent page, technical details hidden in a separate block.

## 0.1.6

- RuTracker retries within a single check are now bounded, so the scheduler and manual checks no longer hang forever on DNS or network errors.
- Defaults changed to `RUTRACKER_MAX_ATTEMPTS=3` and `RUTRACKER_RETRY_DELAY_SECONDS=10`.

## 0.1.5

- Refreshed web interface design: a more informative main panel, improved statuses and cards.
- Russian labels for statuses and events.
- Better visual readability of tables, torrent details and mobile cards.

## 0.1.4

- Compatibility with qBittorrent 5.x: fallback from `pause/resume` to `stop/start`.
- On a partial failure after adding a torrent, the service keeps the qBittorrent hash it received.

## 0.1.3

- The RuTracker resolver now uses cookies saved on the settings page, falling back to `.env`.
- Added a check that the cookie carries RuTracker authorisation parameters and not just `cf_clearance`.

## 0.1.2

- The RuTracker resolver now retries downloading the `.torrent` until it succeeds.
- New settings `RUTRACKER_RETRY_DELAY_SECONDS` and `RUTRACKER_MAX_ATTEMPTS`.
- RuTracker configuration errors, such as an empty cookie, are still returned immediately.

## 0.1.1

- Categories are loaded from the connected qBittorrent.
- New API endpoint `/api/qbittorrent/categories`.
- The add form now shows qBittorrent categories as suggestions.

## 0.1.0

- Initial version.
- Adding torrents for tracking.
- Update detection by `info_hash`.
- Integration with a remote qBittorrent Web API.
- Safe torrent replacement without deleting files.
- RuTracker resolver for topic URLs.
- Web interface in Russian.
- SQLite database with automatic table initialisation.
- Dockerfile and docker-compose for running the service.
