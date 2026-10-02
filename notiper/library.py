"""Service template library (admin only)."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from . import mapping, service_templates
from .auth import admin_required

bp = Blueprint('library', __name__, url_prefix='/templates')


def _form_data() -> dict:
    return {**request.form.to_dict(), **mapping.pairs_from_form(request.form)}


def _services() -> list[str]:
    return sorted({t['service'] for t in service_templates.list_all() if t['service']}, key=str.lower)


def _get_or_404(template_id):
    tpl = service_templates.get(template_id)
    if tpl is None:
        abort(404)
    return tpl


@bp.route('/')
@admin_required
def template_list():
    return render_template('service_templates.html', items=service_templates.list_all())


@bp.route('/new', methods=['GET', 'POST'])
@admin_required
def template_new():
    tpl = {**service_templates.DEFAULTS, 'id': None}
    errors = []
    if request.method == 'POST':
        tpl, errors = service_templates.normalize(_form_data())
        tpl['id'] = None
        errors += service_templates.validate(tpl)
        if not errors:
            new_id = service_templates.save(tpl)
            flash(f'Template "{tpl["name"]}" created.', 'success')
            return redirect(url_for('library.template_edit', template_id=new_id))
    return render_template('service_template_form.html', tpl=tpl, errors=errors, services=_services())


@bp.route('/<int:template_id>', methods=['GET', 'POST'])
@admin_required
def template_edit(template_id):
    tpl = dict(_get_or_404(template_id))
    errors = []
    if request.method == 'POST':
        tpl, errors = service_templates.normalize(_form_data())
        tpl['id'] = template_id
        errors += service_templates.validate(tpl, existing_id=template_id)
        if not errors:
            service_templates.save(tpl, template_id)
            flash('Template saved.', 'success')
            return redirect(url_for('library.template_edit', template_id=template_id))
    return render_template('service_template_form.html', tpl=tpl, errors=errors, services=_services())


@bp.route('/<int:template_id>/delete', methods=['POST'])
@admin_required
def template_delete(template_id):
    tpl = _get_or_404(template_id)
    service_templates.delete(template_id)
    flash(f'Template "{tpl["name"]}" deleted.', 'success')
    return redirect(url_for('library.template_list'))
