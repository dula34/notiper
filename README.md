# Notiper

Webhook mapper with a web UI. Notiper receives a webhook, maps the payload through a Jinja2 template
and forwards the result to a target webhook (MS Teams, Slack, ntfy, Home Assistant, …).

Template semantics: top-level JSON keys are template variables, and the rendered output is parsed as
JSON/YAML (so trailing commas are tolerated) and sent as JSON.

## Features

- Login with roles
    - **admin**: manages converters and users
    - **operator**: views converters, runs tests, browses and replays deliveries
- Converters (mappings) managed in the UI: slug, target URL, method, template, extra headers,
  headers and query parameters mapped from the payload, JSON or plain-text body, timeout, TLS verification,
  optional incoming token
- Service templates: ready-made formats for Teams, Slack, Discord, ntfy, Gotify, Telegram and Home Assistant,
  plus your own. Start a converter from one and adjust the values (*Save as template* works the other way)
- Live template preview against a sample payload, a *Send test* button, and *load last received payload*
- Delivery log with the received payload, rendered body and target response, plus *Replay*
- SQLite storage in `/data`. No config files to edit.

## Run

```bash
docker compose up -d --build
```

Open <http://localhost:12353>. On first visit you create the admin account, unless you set
`NOTIPER_ADMIN_USERNAME` and `NOTIPER_ADMIN_PASSWORD`.

Send webhooks to `http://<host>:12353/webhook/<slug>` (`POST`, `PUT` or `GET`). If the converter has a
token, pass it as `?token=…`, `X-Notiper-Token: …` or `Authorization: Bearer …`.

```bash
curl -X POST http://localhost:12353/webhook/unifi \
     -H 'Content-Type: application/json' \
     -d '{"events": [{"id": 1, "alert_key": "client.connected"}]}'
```

Responses: `200 {"status":"sent"}`, `401` for a bad token, `403` for a disabled converter, `404` for an
unknown converter, and `502` when rendering failed or the target rejected the message.

`/webhook/log` is a built-in target that only logs what it receives. Use it while building templates.

### Build the image only

```bash
docker build -t notiper:latest .
```

```bash
DOCKER_HOST="ssh://user@ip -p <port>"
docker build -t notiper:latest .
```

Add `--no-cache` to force a full rebuild, e.g. when an old image keeps running after an update.
`docker compose build` builds the same image using the settings from `docker-compose.yml`.

If you build on a different CPU architecture than the server runs (e.g. an Apple Silicon Mac and an
Intel/AMD Synology), build for the server's platform and copy the image over as a file:

```bash
docker buildx build --platform linux/amd64 -t notiper:latest --load .
docker save notiper:latest -o notiper.tar
```

On the server, load it (`sudo docker load -i notiper.tar`, or *Container Manager → Image → Add → Add from file*).
Then remove `build: .` from the compose file so it uses the loaded `notiper:latest`. Use
`--platform linux/arm64` for ARM servers.

## Templates

```jinja
{
  "@type": "MessageCard",
  "summary": "Unifi Event Notification",
  "sections": [
    {% for event in events -%}
    {
      "activityTitle": "Unifi Event {{ event.id }}",
      "activitySubtitle": {{ event.alert_key | json }},
    }{% if not loop.last %},{% endif %}
    {% endfor %}
  ]
}
```

| Variable / filter            | Meaning                                                            |
|------------------------------|--------------------------------------------------------------------|
| `events`, `foo`, …           | top-level keys of the incoming JSON object                         |
| `payload`                    | the whole incoming body (object, array or raw text)                |
| `request.headers` / `.query` | incoming headers (auth headers redacted) and query parameters      |
| `\| json`                    | JSON-encode a value: safe quoting of strings with `"` or new lines |

Everything sent to the target can be templated: the target URL (`https://ntfy.sh/{{ topic }}`), headers (`X-Device` →
`{{ device.name }}`), query parameters (`title` → `{{ events[0].alert_key }}`) and the body. Query parameters and
headers that render to an empty value are left out, so `{% if … %}` makes them
optional. Templates run in a Jinja sandbox.

Header values are masked in the form, and only admins can see them. Operators see header names, and
previews shown to them have the values replaced by `***`.

**Service templates** (admin → *Templates*) store the target URL, method, headers, query parameters,
body template and a sample payload. When you create a converter from one, these are copied into the
converter, so later changes to the template do not affect existing converters.

## Configuration (environment)

| Variable                 | Default   | Description                                                  |
|--------------------------|-----------|--------------------------------------------------------------|
| `PUID` / `PGID`          | `1000`    | User / group the app runs as and that owns `/data`           |
| `NOTIPER_PORT`           | `12353`   | HTTP port                                                    |
| `NOTIPER_DATA_DIR`       | `/data`   | Database and generated secret key                            |
| `NOTIPER_ADMIN_USERNAME` |           | Create this admin on first start (only when no users exist)  |
| `NOTIPER_ADMIN_PASSWORD` |           | Password for the admin above (min. 8 chars)                  |
| `NOTIPER_LOG_LEVEL`      | `info`    | `debug`, `info`, `warning`, `error`                          |
| `NOTIPER_LOG_RETENTION`  | `1000`    | Number of deliveries kept in the log                         |
| `NOTIPER_TRUST_PROXY`    | `0`       | Trust `X-Forwarded-*` headers from a reverse proxy           |
| `NOTIPER_SECURE_COOKIES` | `0`       | Set to `1` when served over HTTPS                            |
| `NOTIPER_PUBLIC_URL`     |           | Base URL shown in the UI for webhook links                   |
| `NOTIPER_SECRET_KEY`     | generated | Session signing key (otherwise stored in `/data/secret_key`) |
| `NOTIPER_MAX_BODY_KB`    | `1024`    | Maximum incoming request size                                |
| `TZ`                     | `UTC`     | Timezone used in the UI                                      |

### Synology / bind-mounted folders

The container starts as root, makes `/data` owned by `PUID:PGID` and then runs Notiper as that user.
Set them to the owner of the mounted folder so you keep access to it from DSM:

```yaml
    volumes:
      - /volume1/docker/notiper:/data
    environment:
      PUID: "1026"   # `id -u` of your DSM user
      PGID: "100"    # `id -g`, usually 100 (users)
```

If you run the container with a fixed `user:` instead, the folder must already be writable for that user.
Otherwise the container exits with a message saying so.

## Lost admin password

```bash
docker exec -it notiper python -m notiper reset-password admin
```

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
NOTIPER_DATA_DIR=./data .venv/bin/python -m notiper
```

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
