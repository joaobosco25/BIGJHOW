from __future__ import annotations

from functools import wraps

from flask import abort, current_app, request
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix


def configure_proxy(app) -> None:
    if app.config.get("TRUST_PROXY"):
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=1,
            x_proto=1,
            x_host=1,
            x_port=1,
        )


def install_security_headers(app) -> None:
    @app.after_request
    def add_headers(response):
        response.headers["Content-Security-Policy"] = "; ".join(
            [
                "default-src 'self'",
                "script-src 'self'",
                "style-src 'self'",
                "img-src 'self' data:",
                "font-src 'self'",
                "connect-src 'self'",
                "object-src 'none'",
                "base-uri 'self'",
                "form-action 'self'",
                "frame-ancestors 'none'",
            ]
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        if current_app.config.get("APP_ENV") == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.endpoint and request.endpoint.startswith(
            (
                "auth.",
                "auditoria.",
                "balanca.",
                "caixa.",
                "codigos.",
                "estoque.",
                "fiscal.",
                "grupo.",
                "inventario.",
                "operacao.",
                "usuarios.",
            )
        ):
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response


def acesso_empresa_obrigatorio(view):
    @wraps(view)
    def wrapped(empresa_id: int, *args, **kwargs):
        if not current_user.is_authenticated or not current_user.pode_acessar(empresa_id):
            abort(403)
        return view(empresa_id, *args, **kwargs)

    return wrapped


def papel_obrigatorio(*papeis: str):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or current_user.papel not in papeis:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
