# Building and deploying with Docker

How to build this fork's image from source and run it, including with a USB NFC reader.
Everything here was run on Linux with Docker 29 and Node 22 inside a container; nothing needs
Python or Node installed on the host.

## 1. Prerequisites

- Docker, and your user in the `docker` group (`sudo usermod -aG docker $USER`, then log out
  and back in; `docker ps` must work without `sudo`).
- This repository checked out on the branch you want to deploy (`git branch --show-current`).

## 2. Build the web client

The image copies in the built web client, which is not committed, so build it first. This uses a
throwaway Node container so the host's Node version doesn't matter (the client needs Node
20.12 or newer; Node 18 fails).

```bash
docker run --rm -v "$PWD/client":/w -w /w -u "$(id -u):$(id -g)" -e HOME=/tmp node:22 \
  sh -c "npm ci --no-audit --no-fund && npm run build"
```

This produces `client/dist/`. Re-run it whenever the files under `client/` change, and after
switching branches: `client/dist` is gitignored, so `git checkout` leaves the old build behind.

> `npm ci` can fail on a flaky network connection. If it does, run the command again.

## 3. Build the image

```bash
docker build -t spoolman-custom:latest \
  --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) \
  --build-arg BUILD_DATE=$(date -u +%FT%TZ) .
```

The first build downloads Python dependencies and takes a few minutes; later builds reuse the
cache. The `GIT_COMMIT` and `BUILD_DATE` arguments are optional and only label the image.

## 4. Run it

```bash
docker run -d --name spoolman -p 7912:8000 \
  -v /path/to/spoolman-data:/home/app/.local/share/spoolman \
  -e TZ=America/Toronto \
  --restart unless-stopped \
  spoolman-custom:latest
```

Then open http://localhost:7912. Check `curl localhost:7912/api/v1/health` returns
`{"status":"healthy"}`.

- **Data:** `/path/to/spoolman-data` holds the SQLite database, logs, backups and cache. It
  must be a directory you keep; the container is disposable, the directory is not. Spoolman
  migrates the database on startup, so **copy `spoolman.db` somewhere safe before upgrading**.
- **Permissions:** the app runs as UID/GID 1000 by default. If your data directory belongs to
  someone else, add `-e PUID=<uid> -e PGID=<gid>`.
- **Port:** the container listens on 8000; the left side of `-p` is your choice.
- **Other settings:** any variable from `.env.example` can be passed with `-e`
  (`SPOOLMAN_DB_TYPE`, `SPOOLMAN_BASE_PATH`, ...).

### Existing data from another instance

The database must come from a build whose migrations this branch knows. On startup Spoolman
fails with `Can't locate revision <id>` when the database is newer than the code. Check with
`docker logs spoolman`, and either deploy a branch that contains that migration or restore a
backup from before it.

## 5. With a USB NFC reader (ACR122U)

The server reads tags itself through the `nfcpy` library, which needs to open the reader over
USB from inside the container.

**On the host, once:** the kernel's `pn533` driver claims the ACR122U and blocks the container
from opening it. Unload it, and keep it from loading again:

```bash
sudo rmmod pn533_usb pn533
echo -e "blacklist pn533_usb\nblacklist pn533" | sudo tee /etc/modprobe.d/blacklist-pn533.conf
```

Unplug and replug the reader afterwards if it is not detected. `lsusb` should show
`072f:2200 Advanced Card Systems, Ltd ACR122U`.

**Run with USB access and NFC switched on:**

```bash
docker run -d --name spoolman -p 7912:8000 \
  -v /path/to/spoolman-data:/home/app/.local/share/spoolman \
  -v /dev/bus/usb:/dev/bus/usb \
  --device-cgroup-rule='c 189:* rmw' \
  -e SPOOLMAN_NFC_ENABLED=TRUE \
  --restart unless-stopped \
  spoolman-custom:latest
```

- Mount `/dev/bus/usb` as a volume rather than using `--device`. A `--device` mapping captures
  one device node at start-up and goes stale when the reader is unplugged and replugged (it gets
  a new number); the volume sees the live bus, and the app reconnects by itself.
- `--device-cgroup-rule='c 189:* rmw'` permits USB character devices without `--privileged`.
- Optional: `SPOOLMAN_NFC_DEVICE` (nfcpy device path, default `usb`) and
  `SPOOLMAN_NFC_READER_TYPE` (default `nfcpy`, the only supported value).

**Check it:**

```bash
curl localhost:7912/api/v1/nfc/status        # {"enabled":true,"status":"connected"}
docker logs spoolman | grep -i "ACR122U"     # using ACS ACR122U PN532v1.6 at usb:...
```

If the status is `error` and the logs say `no reader available on path usb`, the host driver is
still loaded (`lsmod | grep pn533`) or the reader isn't visible (`lsusb`). The "Scan NFC"
button in the web UI only appears while the status is `connected` or the browser supports Web NFC.

Only readers that speak ISO 14443A are usable; the ACR122U cannot read ISO 15693 tags
(OpenPrintTag).

## 6. docker-compose

The same deployment as a compose file (`docker-compose.yml`). Drop the three NFC lines if you
have no reader.

```yaml
services:
  spoolman:
    image: spoolman-custom:latest
    container_name: spoolman
    restart: unless-stopped
    ports:
      - "7912:8000"
    volumes:
      - ./data:/home/app/.local/share/spoolman
      - /dev/bus/usb:/dev/bus/usb
    device_cgroup_rules:
      - "c 189:* rmw"
    environment:
      - TZ=America/Toronto
      - SPOOLMAN_NFC_ENABLED=TRUE
```

`docker compose up -d` starts it; `docker compose logs -f` follows the log.

## 7. Updating and operating

```bash
cp data/spoolman.db data/spoolman.db.bak      # always, before an upgrade
git pull                                       # or switch branch
# repeat step 2 (client) and step 3 (image)
docker rm -f spoolman && docker run ...        # same command as step 4/5
```

- Logs: `docker logs spoolman`; stop: `docker stop spoolman`.
- Spoolman keeps its own automatic backups in `data/backups/`.
- Because the data lives outside the container, removing and recreating the container never
  touches your spools.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `Can't locate revision ...` at start-up | The database is newer than this code. Use a branch that has that migration, or restore an older backup. |
| `The web client has not been built` | Step 2 was skipped, or run on a different checkout. |
| NFC status `error`, `no reader available on path usb` | Host `pn533` driver loaded, or reader not visible in the container. See step 5. |
| Slow to answer for up to a minute after start with `SPOOLMAN_NFC_ENABLED=TRUE` | Observed when no reader could be opened (for example another container already holds it): start-up retries the reader before it finishes. Free the reader or drop the NFC settings. Only one container can use the reader at a time. |
| NFC works, then stops after unplugging the reader | The container was started with `--device` instead of the `/dev/bus/usb` volume. |
| Permission denied on the data directory | Set `PUID`/`PGID` to the owner of the data directory. |
| `permission denied ... docker.sock` | Your user isn't in the `docker` group yet, or you haven't logged out and back in. |
