"""Helpers that apply the client-access rule (models.client_scope_ids) to
routes that reach a client through some other id - a company document, a
payslip and so on. Routes that carry client_id themselves are covered once, for
every blueprint, by app._enforce_client_scope."""
from flask import abort, request
from flask_login import current_user

from extensions import db
from models import client_scope_ids

DENIED = ("You are not working on this client, so you cannot open it. Clients are limited to the people on "
          "their engagements. Ask the engagement's Partner or Manager (or an Admin) to add you to the team.")


def ensure_client_access(client_id):
    """403 unless the current user may see this client id (None = no client)."""
    if client_id is None or not current_user.is_authenticated:
        return
    scope = client_scope_ids(current_user)
    if scope is not None and client_id not in scope:
        abort(403, description=DENIED)


def restrict_to_visible_clients(query, column, allow_null=False):
    """Limit a query to rows whose client column is one the current user may
    see (rows with no client are kept when allow_null)."""
    scope = client_scope_ids(current_user)
    if scope is None:
        return query
    clause = column.in_(scope) if scope else db.false()
    if allow_null:
        clause = db.or_(column.is_(None), clause)
    return query.filter(clause)


def guard_blueprint(blueprint, resolvers):
    """Check, before every request to `blueprint`, the client behind each URL
    argument named in `resolvers` ({arg_name: function(value) -> client_id})."""
    @blueprint.before_request
    def _guard_client_scope():
        if not current_user.is_authenticated or client_scope_ids(current_user) is None:
            return
        for arg, resolve in resolvers.items():
            value = (request.view_args or {}).get(arg)
            if value is not None:
                ensure_client_access(resolve(value))
