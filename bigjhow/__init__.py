from __future__ import annotations

import os
import uuid
from ipaddress import ip_address
from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import urlparse

from flask import Flask, redirect, render_template, url_for
from flask_login import current_user
from flask_wtf.csrf import CSRFError
from sqlalchemy.engine import make_url

from .config import Config
from .extensions import csrf, db, login_manager, migrate
from .models import Empresa, Usuario
from .security import configure_proxy, install_security_headers


def create_app(config_object=None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object or Config)
    _validate_config(app)

    os.makedirs(app.instance_path, exist_ok=True)
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    configure_proxy(app)
    install_security_headers(app)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Entre para acessar o sistema."
    login_manager.login_message_category = "aviso"
    login_manager.session_protection = "strong"

    @login_manager.user_loader
    def load_user(user_id: str):
        try:
            user = db.session.get(Usuario, uuid.UUID(user_id))
            return user if user is not None and user.ativo else None
        except (ValueError, TypeError):
            return None

    from .auth.routes import bp as auth_bp
    from .balanca.routes import bp as balanca_bp
    from .auditoria.routes import bp as auditoria_bp
    from .caixa.routes import bp as caixa_bp
    from .codigos.routes import bp as codigos_bp
    from .estoque.routes import bp as estoque_bp
    from .estoque.routes import grupo_bp
    from .fiscal.routes import bp as fiscal_bp
    from .inventario.routes import bp as inventario_bp
    from .operacao.routes import bp as operacao_bp
    from .usuarios.routes import bp as usuarios_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(balanca_bp)
    app.register_blueprint(auditoria_bp)
    app.register_blueprint(caixa_bp)
    app.register_blueprint(codigos_bp)
    app.register_blueprint(estoque_bp)
    app.register_blueprint(fiscal_bp)
    app.register_blueprint(grupo_bp)
    app.register_blueprint(inventario_bp)
    app.register_blueprint(operacao_bp)
    app.register_blueprint(usuarios_bp)

    from .commands import register_commands

    register_commands(app)

    @app.get("/")
    def index():
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if current_user.papel == "proprietario":
            return redirect(url_for("grupo.visao_geral"))
        empresa_id = (
            min(vinculo.empresa_id for vinculo in current_user.empresas)
            if current_user.empresas
            else None
        )
        if empresa_id is not None:
            endpoint = (
                "operacao.painel"
                if current_user.papel == "operador"
                else "estoque.lista"
            )
            return redirect(url_for(endpoint, empresa_id=empresa_id))
        return render_template("erros/sem_acesso.html"), 403

    @app.context_processor
    def navigation_context():
        if not current_user.is_authenticated:
            return {}
        query = db.select(Empresa).where(Empresa.ativa.is_(True)).order_by(Empresa.id)
        if current_user.papel != "proprietario":
            ids = [item.empresa_id for item in current_user.empresas]
            query = query.where(Empresa.id.in_(ids))
        return {"empresas_menu": db.session.scalars(query).all()}

    @app.template_filter("brl")
    def brl(value):
        number = float(value or 0)
        formatted = f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {formatted}"

    @app.template_filter("quantidade")
    def quantidade(value):
        number = float(value or 0)
        formatted = f"{number:,.3f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return formatted.rstrip("0").rstrip(",")

    @app.template_filter("data_hora")
    def data_hora(value):
        if value is None:
            return "—"
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        zona = ZoneInfo(app.config["APP_TIMEZONE"])
        return value.astimezone(zona).strftime("%d/%m/%Y %H:%M")

    @app.errorhandler(CSRFError)
    def handle_csrf(error):
        return render_template("erros/csrf.html"), 400

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("erros/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("erros/404.html"), 404

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        return render_template("erros/500.html"), 500

    return app


def _validate_config(app: Flask) -> None:
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    secret = app.config.get("SECRET_KEY", "")
    production = app.config.get("APP_ENV") == "production"

    try:
        ZoneInfo(app.config.get("APP_TIMEZONE", ""))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RuntimeError("APP_TIMEZONE inválido.") from exc

    if not secret or len(secret) < 32 or "gere-uma-chave" in secret.lower():
        raise RuntimeError("SECRET_KEY segura e com pelo menos 32 caracteres é obrigatória.")
    if not uri:
        raise RuntimeError("DATABASE_URL é obrigatória.")
    if production and not uri.startswith("postgresql+psycopg://"):
        raise RuntimeError("Em produção, DATABASE_URL deve apontar para PostgreSQL via psycopg.")
    if production:
        parsed = make_url(uri)
        password = parsed.password or ""
        if password.lower() in {"password", "changeme", "troque-esta-senha"}:
            raise RuntimeError("A senha de exemplo do PostgreSQL precisa ser substituída.")
        trusted_hosts = app.config.get("TRUSTED_HOSTS") or []
        if "*" in trusted_hosts:
            raise RuntimeError("TRUSTED_HOSTS não pode aceitar todos os hosts em produção.")

    if app.config.get("MGV6_WRITE_ENABLED") and not app.config.get("MGV6_ENABLED"):
        raise RuntimeError(
            "MGV6_WRITE_ENABLED=true exige MGV6_ENABLED=true."
        )

    if app.config.get("MGV6_ENABLED"):
        mgv_url = app.config.get("MGV6_BASE_URL", "")
        mgv_key = app.config.get("MGV6_PALAVRA_CHAVE", "")
        parsed_mgv = urlparse(mgv_url)
        if parsed_mgv.scheme not in {"http", "https"} or not parsed_mgv.hostname:
            raise RuntimeError("MGV6_BASE_URL inválida.")
        try:
            endereco_mgv = ip_address(parsed_mgv.hostname)
        except ValueError:
            endereco_mgv = None
        if endereco_mgv is not None and not (endereco_mgv.is_private or endereco_mgv.is_loopback):
            raise RuntimeError("MGV6_BASE_URL deve apontar para endereço privado/local.")
        if not mgv_key or len(mgv_key) > 10:
            raise RuntimeError(
                "MGV6_PALAVRA_CHAVE é obrigatória e deve ter no máximo 10 caracteres "
                "quando MGV6_ENABLED=true."
            )
        if int(app.config.get("MGV6_LOJA", 0)) <= 0:
            raise RuntimeError("MGV6_LOJA inválida.")
        if int(app.config.get("MGV6_BALANCA", 0)) <= 0:
            raise RuntimeError("MGV6_BALANCA inválida.")
