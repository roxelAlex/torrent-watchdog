# torrent-watchdog

Watches RuTracker topics for updates and applies them in qBittorrent, downloading only the new files.

What you put under watch is a link to a tracker topic. Once a day the service downloads a fresh `.torrent` from it, compares the `info_hash` and, when the torrent has changed, compares the file lists. In "new files only" mode whatever you already have is marked "do not download", so only the added files are fetched — for a media library of several hundred gigabytes that is the difference between minutes and a day.

## Running it

```bash
curl -O https://raw.githubusercontent.com/roxelAlex/torrent-watchdog/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/roxelAlex/torrent-watchdog/main/.env.example
docker compose up -d
```

Two containers come up: the service and `torrent-watchdog-flaresolverr`. The second is required — Cloudflare guards RuTracker, and stock FlareSolverr will not do: it only returns HTML while `dl.php` sends a file.

The interface is at `http://SERVER_IP:8096`, sign in with `admin` / `change_me` from `.env`. **Change `APP_AUTH_PASSWORD` and `APP_SECRET_KEY`** before using it for real.

## What it does

- Signs in to the tracker with a username and password; the cookie is refreshed automatically when the session expires.
- Several qBittorrent clients, each torrent bound to one of them.
- Two update modes: download only what is new, or replace the torrent entirely and verify the files.
- Version history with rollback to any saved version.
- Telegram notifications with a choice of events.
- Interface in Russian and English; the notification language is configured separately.

## Files on disk

By default the service never deletes them. The single exception is enabled per torrent and only fires when the releaser repacked the torrent from scratch and not a single file matches. Rollbacks and partial updates leave files alone.

## Architectures

`linux/amd64` and `linux/arm64` — suitable for a NAS or a Raspberry Pi.

## Source and documentation

[github.com/roxelAlex/torrent-watchdog](https://github.com/roxelAlex/torrent-watchdog) — full settings reference, how FlareSolverr is used, and the MIT licence.
