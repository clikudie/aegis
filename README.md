# aegis MVP

Timer-first local service to power off TV after N minutes.

## Features
- One-shot sleep timer
- Local web UI (`/`)
- LG webOS power-off helper script (`scripts/lg_poweroff.py`)

## Run

```bash
python3 app.py
```

Open:
- `http://127.0.0.1:8787/`
- `http://127.0.0.1:8787/status`

## Run With Docker Compose

```bash
docker compose up -d
```

Then open `http://<truenas-ip>:8787/`.

Stop:

```bash
docker compose down
```

## LG Pairing / Power-Off

Pair once:

```bash
docker compose exec aegis python /app/scripts/lg_poweroff.py --pair-only
```

Test power-off:

```bash
docker compose exec aegis python /app/scripts/lg_poweroff.py
```

## Environment
- `HOST` (default `127.0.0.1`)
- `PORT` (default `8787`)
- `TZ_NAME` (default `America/Los_Angeles`)
- `POWER_OFF_CMD` (command executed when timer expires)
- `LG_TV_HOST` (optional initial host hint for `lg_poweroff.py`)
- `LG_TV_HOST_CACHE_FILE` (default `/data/lgtv-host.txt`)
- `LG_TV_KEY_FILE` (default `/data/lgtv-key.json`)

## API

### `GET /status`
Returns current timer and last action fields.

### `POST /timer`

```json
{ "minutes": 45 }
```

### `POST /timer/cancel`

```json
{}
```
