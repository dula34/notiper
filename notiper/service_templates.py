"""Reusable service templates: a starting point (URL, headers, query, body template) for new converters."""
import json
import logging

from . import mapping
from .db import get_db, get_meta, now, set_meta

log = logging.getLogger(__name__)

DEFAULTS = {
    'name': '',
    'service': '',
    'description': '',
    **mapping.MAPPING_DEFAULTS,
}
COLUMNS = tuple(DEFAULTS)

# Fields copied into a converter when it is created from a template.
CONVERTER_FIELDS = tuple(mapping.MAPPING_DEFAULTS)

_SAMPLE = json.dumps({
    'title': 'Disk almost full',
    'message': 'Volume /data on nas01 is 95 % full',
    'severity': 'warning',
    'url': 'https://nas01.local/storage',
}, indent=2)


def _qp(**params) -> str:
    return json.dumps([{'key': k, 'value': v} for k, v in params.items()], ensure_ascii=False)


def _headers(**headers) -> str:
    return json.dumps([{'name': k.replace('_', '-'), 'value': v} for k, v in headers.items()], ensure_ascii=False)


BUILTIN = [
    {
        'name': 'Microsoft Teams – Adaptive Card (Workflows)',
        'service': 'Microsoft Teams',
        'description': 'Teams "Post to a channel when a webhook request is received" workflow. '
                       'Paste the workflow URL as target.',
        'target_url': 'https://REPLACE.webhook.office.com/workflows/REPLACE',
        'template': '''{
  "type": "message",
  "attachments": [
    {
      "contentType": "application/vnd.microsoft.card.adaptive",
      "content": {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "msteams": {"width": "Full"},
        "body": [
          {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "wrap": true,
           "color": {{ ("Attention" if severity == "critical" else "Warning" if severity == "warning" else "Default") | json }},
           "text": {{ title | json }}},
          {"type": "TextBlock", "wrap": true, "text": {{ message | json }}}
        ]{% if url %},
        "actions": [
          {"type": "Action.OpenUrl", "title": "Open", "url": {{ url | json }}}
        ]{% endif %}
      }
    }
  ]
}
''',
    },
    {
        'name': 'Microsoft Teams – MessageCard (legacy connector)',
        'service': 'Microsoft Teams',
        'description': 'Legacy Office 365 connector (Incoming Webhook) with a MessageCard.',
        'target_url': 'https://REPLACE.webhook.office.com/webhookb2/REPLACE',
        'template': '''{
  "@type": "MessageCard",
  "@context": "https://schema.org/extensions",
  "summary": {{ title | json }},
  "themeColor": {{ ("D32F2F" if severity == "critical" else "F9A825" if severity == "warning" else "0284C7") | json }},
  "title": {{ title | json }},
  "text": {{ message | json }}{% if url %},
  "potentialAction": [
    {"@type": "OpenUri", "name": "Open", "targets": [{"os": "default", "uri": {{ url | json }}}]}
  ]{% endif %}
}
''',
    },
    {
        'name': 'Slack – Incoming webhook',
        'service': 'Slack',
        'description': 'Slack app incoming webhook with a Block Kit section.',
        'target_url': 'https://hooks.slack.com/services/REPLACE/REPLACE/REPLACE',
        'template': '''{
  "text": {{ title | json }},
  "blocks": [
    {"type": "section", "text": {"type": "mrkdwn", "text": {{ ("*" ~ title ~ "*\\n" ~ message) | json }}}}{% if url %},
    {"type": "actions", "elements": [
      {"type": "button", "text": {"type": "plain_text", "text": "Open"}, "url": {{ url | json }}}
    ]}{% endif %}
  ]
}
''',
    },
    {
        'name': 'Discord – Webhook embed',
        'service': 'Discord',
        'description': 'Discord channel webhook (Server settings → Integrations → Webhooks).',
        'target_url': 'https://discord.com/api/webhooks/REPLACE/REPLACE',
        'template': '''{
  "username": "Notiper",
  "embeds": [
    {
      "title": {{ title | json }},
      "description": {{ message | json }},
      "color": {{ 13840175 if severity == "critical" else 16361509 if severity == "warning" else 165063 }}{% if url %},
      "url": {{ url | json }}{% endif %}
    }
  ]
}
''',
    },
    {
        'name': 'ntfy – Push notification',
        'service': 'ntfy',
        'description': 'ntfy.sh or self-hosted ntfy. Title, priority and tags are sent as query parameters. '
                       'For protected topics add the header Authorization = Bearer <access token>.',
        'target_url': 'https://ntfy.sh/REPLACE-topic',
        'body_format': 'text',
        'query_params': _qp(
            title='{{ title }}',
            priority='{{ "urgent" if severity == "critical" else "high" if severity == "warning" else "default" }}',
            tags='{{ "rotating_light" if severity == "critical" else "warning" if severity == "warning" else "" }}',
            click='{{ url | default("") }}',
        ),
        'template': '{{ message }}\n',
    },
    {
        'name': 'Gotify – Message',
        'service': 'Gotify',
        'description': 'Self-hosted Gotify. Put the application token into the X-Gotify-Key header.',
        'target_url': 'https://gotify.example.com/message',
        'headers': _headers(X_Gotify_Key='REPLACE-app-token'),
        'template': '''{
  "title": {{ title | json }},
  "message": {{ message | json }},
  "priority": {{ 8 if severity == "critical" else 5 if severity == "warning" else 2 }}
}
''',
    },
    {
        'name': 'Telegram – Bot message',
        'service': 'Telegram',
        'description': 'Telegram Bot API sendMessage. Replace the bot token in the URL and the chat_id.',
        'target_url': 'https://api.telegram.org/botREPLACE-bot-token/sendMessage',
        'template': '''{
  "chat_id": "REPLACE-chat-id",
  "parse_mode": "HTML",
  "disable_web_page_preview": true,
  "text": {{ ("<b>" ~ (title | e) ~ "</b>\\n" ~ (message | e) ~ (("\\n" ~ url) if url else "")) | json }}
}
''',
    },
    {
        'name': 'Home Assistant – Webhook trigger',
        'service': 'Home Assistant',
        'description': 'Forwards the event to an automation with a webhook trigger. '
                       'Original payload is available as trigger.json.data.',
        'target_url': 'http://homeassistant.local:8123/api/webhook/REPLACE-webhook-id',
        'template': '''{
  "title": {{ title | json }},
  "message": {{ message | json }},
  "data": {{ payload | json }}
}
''',
    },
]


def normalize(raw: dict) -> tuple[dict, list[str]]:
    return mapping.normalize(raw, DEFAULTS)


def validate(data: dict, existing_id=None) -> list[str]:
    errors = []
    if not data['name']:
        errors.append('Name is required.')
    else:
        row = get_db().execute('SELECT id FROM service_templates WHERE name = ?', (data['name'],)).fetchone()
        if row and row['id'] != existing_id:
            errors.append(f'A template named "{data["name"]}" already exists.')
    return errors + mapping.validate(data)


def get(template_id):
    return get_db().execute('SELECT * FROM service_templates WHERE id = ?', (template_id,)).fetchone()


def list_all():
    return get_db().execute(
        'SELECT * FROM service_templates ORDER BY service COLLATE NOCASE, name COLLATE NOCASE'
    ).fetchall()


def save(data: dict, template_id=None) -> int:
    db = get_db()
    ts = now()
    values = [data[c] for c in COLUMNS]
    if template_id is None:
        cur = db.execute(
            f"INSERT INTO service_templates ({', '.join(COLUMNS)}, created_at, updated_at) "
            f"VALUES ({', '.join('?' * len(COLUMNS))}, ?, ?)",
            (*values, ts, ts),
        )
        template_id = cur.lastrowid
    else:
        db.execute(
            f"UPDATE service_templates SET {', '.join(f'{c} = ?' for c in COLUMNS)}, updated_at = ? WHERE id = ?",
            (*values, ts, template_id),
        )
    db.commit()
    return template_id


def delete(template_id):
    db = get_db()
    db.execute('DELETE FROM service_templates WHERE id = ?', (template_id,))
    db.commit()


def unique_name(base: str) -> str:
    name, i = base, 2
    while get_db().execute('SELECT 1 FROM service_templates WHERE name = ?', (name,)).fetchone():
        name = f'{base} ({i})'
        i += 1
    return name


def builtin_row(name: str) -> dict | None:
    """Current definition of a built-in template, normalized like a stored row."""
    for raw in BUILTIN:
        if raw['name'] == name:
            return normalize({'sample_payload': _SAMPLE, **raw})[0]
    return None


def seed_builtin():
    """Insert the built-in templates once. Deleted ones are not re-created on restart."""
    if get_meta('builtin_templates_seeded'):
        return
    for raw in BUILTIN:
        data, errors = normalize({'sample_payload': _SAMPLE, **raw})
        errors += validate(data)
        if errors:
            log.error("Built-in template '%s' is invalid: %s", raw['name'], errors)
            continue
        data['name'] = unique_name(data['name'])
        save(data)
    set_meta('builtin_templates_seeded', '1')
