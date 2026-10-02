(function () {
    'use strict';

    const CSRF = document.querySelector('meta[name="csrf-token"]')?.content || '';

    // Confirm destructive forms.
    document.querySelectorAll('form[data-confirm]').forEach(form => {
        form.addEventListener('submit', e => {
            if (!confirm(form.dataset.confirm)) e.preventDefault();
        });
    });

    // Click-to-copy.
    function flashCopied(el) {
        el.classList.add('copied');
        setTimeout(() => el.classList.remove('copied'), 1200);
    }
    document.querySelectorAll('[data-copy-text]').forEach(el => {
        el.addEventListener('click', () => navigator.clipboard.writeText(el.dataset.copyText).then(() => flashCopied(el)));
    });
    document.querySelectorAll('[data-copy-target]').forEach(btn => {
        btn.addEventListener('click', () => {
            const target = document.querySelector(btn.dataset.copyTarget);
            navigator.clipboard.writeText(target.textContent).then(() => {
                btn.textContent = 'copied';
                setTimeout(() => (btn.textContent = 'copy'), 1200);
            });
        });
    });

    // Clickable table rows.
    document.querySelectorAll('tr[data-href]').forEach(tr => {
        tr.addEventListener('click', e => {
            if (!e.target.closest('a, button, form')) location.href = tr.dataset.href;
        });
    });

    // Slug follows the name until edited by hand.
    const slugSource = document.querySelector('[data-slug-source]');
    const slugTarget = document.querySelector('[data-slug-target]');
    if (slugSource && slugTarget) {
        slugTarget.addEventListener('input', () => (slugTarget.dataset.slugTouched = '1'));
        slugSource.addEventListener('input', () => {
            if (slugTarget.dataset.slugTouched) return;
            slugTarget.value = slugSource.value.normalize('NFD').replace(/[̀-ͯ]/g, '')
                .toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64);
        });
    }

    // Random URL-safe token of `bytes` random bytes (4/3 characters per byte).
    function randomToken(bytes) {
        const raw = crypto.getRandomValues(new Uint8Array(bytes));
        return btoa(String.fromCharCode(...raw)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }

    const tokenBtn = document.querySelector('[data-generate-token]');
    if (tokenBtn) {
        tokenBtn.addEventListener('click', () => {
            document.querySelector('[data-token-input]').value = randomToken(24);
        });
    }

    // Tab key inserts spaces in code textareas.
    document.querySelectorAll('textarea.code').forEach(ta => {
        ta.addEventListener('keydown', e => {
            if (e.key !== 'Tab' || e.shiftKey || e.ctrlKey || e.metaKey) return;
            e.preventDefault();
            ta.setRangeText('  ', ta.selectionStart, ta.selectionEnd, 'end');
            ta.dispatchEvent(new Event('input', {bubbles: true}));
        });
    });

    // Repeatable key/value rows (query parameters, headers).
    document.querySelectorAll('[data-pairs]').forEach(panel => {
        const rows = panel.querySelector('[data-pairs-rows]');
        const tpl = panel.querySelector('[data-pairs-template]');
        const count = panel.querySelector('[data-pairs-count]');
        const changed = () => {
            count.textContent = rows ? rows.children.length : count.textContent;
            panel.closest('form')?.dispatchEvent(new Event('input', {bubbles: true}));
        };
        panel.querySelector('[data-pairs-add]')?.addEventListener('click', () => {
            rows.appendChild(tpl.content.cloneNode(true));
            rows.lastElementChild.querySelector('input')?.focus();
            changed();
        });
        panel.addEventListener('click', e => {
            const remove = e.target.closest('[data-qp-remove]');
            if (remove) {
                remove.closest('.qp-row').remove();
                changed();
            }
            const reveal = e.target.closest('[data-reveal]');
            if (reveal) {
                const input = reveal.closest('.masked-input').querySelector('input');
                input.type = input.type === 'password' ? 'text' : 'password';
                reveal.classList.toggle('revealed', input.type === 'text');
            }
        });
    });

    // Remember unsaved edits so switching the starting template can warn.
    let formDirty = false;
    document.getElementById('conv-form')?.addEventListener('input', e => {
        if (e.isTrusted) formDirty = true;
    });
    const startFrom = document.querySelector('[data-start-from]');
    if (startFrom) {
        startFrom.addEventListener('change', () => {
            if (formDirty && !confirm('Replace your changes with the selected template?')) return;
            const url = new URL(startFrom.dataset.baseUrl, location.href);
            if (startFrom.value) url.searchParams.set('from', startFrom.value);
            location.href = url;
        });
    }

    // --- Test panel ---------------------------------------------------------------------
    const panel = document.getElementById('test-panel');
    if (!panel) return;

    const form = document.getElementById('conv-form');
    const isAdmin = panel.dataset.admin === '1';
    const payloadEl = panel.querySelector('[data-payload]');
    const resultBox = panel.querySelector('[data-result]');
    const resultLine = panel.querySelector('[data-result-line]');
    const resultBody = panel.querySelector('[data-result-body]');
    const liveToggle = panel.querySelector('[data-live]');
    const liveStatus = panel.querySelector('[data-live-status]');
    const previewBtn = panel.querySelector('[data-preview]');
    const sendBtn = panel.querySelector('[data-send]');
    const hasConverterForm = !!form.querySelector('[name="slug"]');

    function collect() {
        const data = {converter_id: panel.dataset.converterId || null, payload: payloadEl.value};
        if (isAdmin) {
            const fd = new FormData(form);
            ['template', 'target_url', 'body_format', 'method', 'timeout', 'slug'].forEach(k => {
                if (fd.has(k)) data[k] = fd.get(k);
            });
            if (hasConverterForm) data.verify_tls = fd.has('verify_tls');
            const pairs = (keyName, valueName, keyField) => {
                const values = fd.getAll(valueName);
                return fd.getAll(keyName).map((k, i) => ({[keyField]: k, value: values[i]}));
            };
            data.query_params = pairs('qp_key', 'qp_value', 'key');
            data.headers = pairs('header_name', 'header_value', 'name');
        }
        return data;
    }

    async function postJSON(url, data) {
        const resp = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-CSRF-Token': CSRF},
            body: JSON.stringify(data),
        });
        if (!(resp.headers.get('content-type') || '').includes('json')) {
            throw new Error(`HTTP ${resp.status} – are you still logged in?`);
        }
        return resp.json();
    }

    function show(ok, line, body) {
        resultBox.hidden = false;
        resultLine.className = 'result-line ' + (ok ? 'ok' : 'fail');
        resultLine.textContent = line;
        resultBody.textContent = body || '';
        resultBody.hidden = !body;
    }

    async function preview() {
        liveStatus.textContent = 'rendering…';
        try {
            const r = await postJSON(panel.dataset.previewUrl, collect());
            if (r.ok) {
                const extra = Object.entries(r.headers || {})
                    .filter(([k]) => !['content-type', 'user-agent'].includes(k.toLowerCase()))
                    .map(([k, v]) => `${k}: ${v}`).join('\n');
                show(true, `${r.method} ${r.url}  ·  ${r.content_type}`, (extra ? extra + '\n\n' : '') + r.body);
            }
            else show(false, r.error, '');
        } catch (err) {
            show(false, err.message, '');
        }
        liveStatus.textContent = '';
    }

    async function sendTest() {
        if (!confirm('Send the rendered message to the target URL now?')) return;
        sendBtn.disabled = true;
        try {
            const r = await postJSON(panel.dataset.sendUrl, collect());
            if (r.ok) show(true, `Sent · HTTP ${r.status} · ${r.duration_ms} ms`, r.response);
            else show(false, r.error || `HTTP ${r.status}`, r.response);
            if (r.delivery_url) {
                const a = document.createElement('a');
                a.href = r.delivery_url;
                a.textContent = '  → delivery detail';
                resultLine.appendChild(a);
            }
        } catch (err) {
            show(false, err.message, '');
        }
        sendBtn.disabled = false;
    }

    let timer = null;
    function schedulePreview() {
        if (!liveToggle.checked) return;
        clearTimeout(timer);
        timer = setTimeout(preview, 450);
    }

    previewBtn.addEventListener('click', preview);
    sendBtn?.addEventListener('click', sendTest);
    payloadEl.addEventListener('input', schedulePreview);
    if (isAdmin) form.addEventListener('input', schedulePreview);

    const loadLast = panel.querySelector('[data-load-last]');
    if (loadLast) {
        loadLast.addEventListener('click', async () => {
            const resp = await fetch(panel.dataset.lastUrl);
            const r = await resp.json();
            if (r.payload !== undefined) {
                payloadEl.value = r.payload;
                preview();
            } else {
                show(false, r.error, '');
            }
        });
    }

    if (payloadEl.value.trim() && form.querySelector('[name="template"]').value.trim()) preview();
})();
