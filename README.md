# aegis MVP

Minimal local service to enforce TV usage rules:
- Turn TV off in N minutes
- Allowed schedule windows
- Temporary/permanent schedule overrides
- Mobile web UI at `/`

## Run

```bash
python3 app.py
```

Then open:
- `http://127.0.0.1:8787/` (web UI)
- `http://127.0.0.1:8787/status` (JSON status)

## Run With Docker Compose

```bash
docker compose up -d
```

Then open `http://<truenas-ip>:8787/` from your iPhone (same LAN or via Tailscale).

Set your TV IP in `docker-compose.yml` (`LG_TV_HOST`) before first run.

Pair once from inside the running container:

```bash
docker compose exec aegis python /app/scripts/lg_poweroff.py --pair-only
```

Accept the pairing prompt on TV. Key is persisted to `./data/lgtv-key.json`.

Optional power-off test from inside container:

```bash
docker compose exec aegis python /app/scripts/lg_poweroff.py
```

Stop:

```bash
docker compose down
```

## LG Pairing (one-time) and Power-Off Command

This MVP expects a command to execute when policy says "turn off TV".
- Uses Python helper: `scripts/lg_poweroff.py` (`pywebostv`)

For Docker Compose in this repo, `POWER_OFF_CMD` already points to Python helper.
For local host runs, set:

```bash
POWER_OFF_CMD='python3 scripts/lg_poweroff.py --host 192.168.1.50' python3 app.py
```

Optional env vars:
- `HOST` (default `127.0.0.1`)
- `PORT` (default `8787`)
- `TZ_NAME` (default `America/Los_Angeles`)
- `STATE_FILE` (default `state.json`, stores schedule + override settings)
- `TV_STATE_POLL_SECONDS` (default `5`, auto TV-state probe interval)
- `POWER_OFF_CMD` (shell command to execute when service decides to turn TV off)

Example (simulate power off action):

```bash
POWER_OFF_CMD='echo "power off triggered"' python3 app.py
```

## API

### Get status

```bash
curl -s http://127.0.0.1:8787/status | jq
```

(`GET /api/status` is also supported.)

### Set timer (off in N minutes)

```bash
curl -s -X POST http://127.0.0.1:8787/timer \
  -H 'content-type: application/json' \
  -d '{"minutes":45}' | jq
```

(`POST /api/timer` is also supported.)

### Cancel timer

```bash
curl -s -X POST http://127.0.0.1:8787/timer/cancel \
  -H 'content-type: application/json' \
  -d '{}' | jq
```

(`POST /api/timer/cancel` is also supported.)

### Set schedule

Strict mode (immediate off outside window):

```bash
curl -s -X POST http://127.0.0.1:8787/schedule \
  -H 'content-type: application/json' \
  -d '{
    "enabled": true,
    "mode": "strict",
    "windows": [
      {"day":"saturday","start":"13:00","end":"15:00"}
    ]
  }' | jq
```

Graceful mode (off after grace period outside window):

```bash
curl -s -X POST http://127.0.0.1:8787/schedule \
  -H 'content-type: application/json' \
  -d '{
    "enabled": true,
    "mode": "graceful",
    "grace_minutes": 5,
    "windows": [
      {"day":"saturday","start":"13:00","end":"15:00"}
    ]
  }' | jq
```

(`POST /api/schedule` is also supported.)

### Override schedule

Temporary override:

```bash
curl -s -X POST http://127.0.0.1:8787/override \
  -H 'content-type: application/json' \
  -d '{"mode":"temporary","minutes":60}' | jq
```

Permanent override:

```bash
curl -s -X POST http://127.0.0.1:8787/override \
  -H 'content-type: application/json' \
  -d '{"mode":"permanent"}' | jq
```

Disable override:

```bash
curl -s -X POST http://127.0.0.1:8787/override \
  -H 'content-type: application/json' \
  -d '{"mode":"none"}' | jq
```

(`POST /api/override` is also supported.)

### Update known TV power state

Optional debug endpoint. In normal operation, TV state is detected automatically by
probing `LG_TV_HOST` on webOS ports `3001/3000` at `TV_STATE_POLL_SECONDS` interval.
If detected state is on and policy disallows usage, service triggers `POWER_OFF_CMD`.

```bash
curl -s -X POST http://127.0.0.1:8787/tv-state \
  -H 'content-type: application/json' \
  -d '{"is_on":true}' | jq
```

(`POST /api/tv-state` is also supported.)

## Notes

- Schedule and override settings are persisted to `STATE_FILE`; timer state is in-memory only.
- `state.json` is persisted under `./data` when using Docker Compose.
- TV on/off state is auto-detected when `LG_TV_HOST` is set.
- Override bypasses schedule enforcement. It does not cancel an already-set sleep timer.
- Sleep timer executes independently when it expires, unless you cancel it explicitly.
- Integrate your LG webOS control by setting `POWER_OFF_CMD` to a command that sends LG `powerOff`.
- If you need deeper integration, replace `PowerController.power_off()` with direct API calls.
- Tailscale setup is compatible with this architecture: keep the service bound on `0.0.0.0`, and access it only through your Tailscale network / ACL policy.
