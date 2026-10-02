"""Normalization and validation of the mapping fields shared by converters and service templates."""
import json
import re

from . import engine

METHODS = ('POST', 'PUT', 'PATCH', 'GET')
BODY_FORMATS = ('json', 'text')
HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")

MAPPING_DEFAULTS = {
    'target_url': '',
    'method': 'POST',
    'body_format': 'json',
    'headers': '[]',
    'query_params': '[]',
    'template': '',
    'sample_payload': '',
}
PAIR_FIELDS = {'headers': ('name', 'Headers'), 'query_params': ('key', 'Query parameters')}


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'on', 'yes')


def normalize_pairs(value, key_field: str, label: str) -> tuple[str, list[str]]:
    """Accept a JSON string or a list of {key_field, value} and return canonical JSON plus errors."""
    errors = []
    if isinstance(value, str):
        try:
            value = json.loads(value or '[]')
        except ValueError:
            return '[]', [f'{label} are not valid JSON.']
    rows = []
    for item in value or []:
        key = str(item.get(key_field, '')).strip()
        val = str(item.get('value', ''))
        if not key and not val.strip():
            continue
        if not key:
            errors.append(f'Every entry in {label.lower()} needs a name.')
            continue
        rows.append({key_field: key, 'value': val})
    return json.dumps(rows, ensure_ascii=False), errors


def normalize(raw: dict, defaults: dict, bool_fields=(), int_fields=()) -> tuple[dict, list[str]]:
    data = dict(defaults)
    errors = []
    for key in defaults:
        if raw.get(key) is not None:
            data[key] = raw[key]
    for key, value in data.items():
        if key in PAIR_FIELDS:
            data[key], pair_errors = normalize_pairs(value, *PAIR_FIELDS[key])
            errors += pair_errors
        elif key in ('template', 'sample_payload'):
            data[key] = str(value).replace('\r\n', '\n')
        elif key in bool_fields:
            data[key] = int(_truthy(value))
        elif key in int_fields:
            try:
                data[key] = int(value)
            except (TypeError, ValueError):
                errors.append(f'{key.replace("_", " ").capitalize()} must be a number.')
                data[key] = defaults[key]
        else:
            data[key] = str(value).strip()
    data['method'] = data['method'].upper()
    data['body_format'] = data['body_format'].lower()
    return data, errors


def validate(data: dict) -> list[str]:
    errors = []
    if not data['target_url']:
        errors.append('Target URL is required.')
    elif not data['target_url'].startswith(('http://', 'https://', '{{')):
        errors.append('Target URL must start with http:// or https://.')
    elif err := engine.check_template(data['target_url']):
        errors.append(f'Target URL template: {err}')
    if data['method'] not in METHODS:
        errors.append('Unsupported HTTP method.')
    if data['body_format'] not in BODY_FORMATS:
        errors.append('Unsupported body format.')
    seen = set()
    for header in json.loads(data['headers']):
        if not HEADER_NAME_RE.match(header['name']):
            errors.append(f'Header name "{header["name"]}" contains invalid characters.')
        elif header['name'].lower() in seen:
            errors.append(f'Header "{header["name"]}" is defined twice.')
        elif err := engine.check_template(header['value']):
            errors.append(f'Header "{header["name"]}": {err}')
        seen.add(header['name'].lower())
    for param in json.loads(data['query_params']):
        if err := engine.check_template(param['value']):
            errors.append(f'Query parameter "{param["key"]}": {err}')
    if not data['template'].strip():
        errors.append('Template is required.')
    elif err := engine.check_template(data['template']):
        errors.append(f'Template: {err}')
    if data['sample_payload'].strip():
        try:
            json.loads(data['sample_payload'])
        except ValueError as e:
            errors.append(f'Sample payload is not valid JSON: {e}')
    return errors


def pairs_from_form(form) -> dict:
    """Repeated header_name/header_value and qp_key/qp_value inputs of the mapping form."""
    return {
        'headers': [{'name': k, 'value': v} for k, v in zip(form.getlist('header_name'), form.getlist('header_value'))],
        'query_params': [{'key': k, 'value': v} for k, v in zip(form.getlist('qp_key'), form.getlist('qp_value'))],
    }


def headers_text_to_json(text: str) -> str:
    """Convert the first release's "Name: value" lines into the JSON row format."""
    rows = []
    for line in (text or '').splitlines():
        name, sep, value = line.strip().partition(':')
        if sep and name.strip() and not line.strip().startswith('#'):
            rows.append({'name': name.strip(), 'value': value.strip()})
    return json.dumps(rows, ensure_ascii=False)
