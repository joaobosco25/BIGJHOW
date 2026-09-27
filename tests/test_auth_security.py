from __future__ import annotations

from datetime import timedelta
import re

import pytest

from bigjhow import create_app
from bigjhow.config import Config
from bigjhow.extensions import db
from bigjhow.models import Usuario, utcnow

from .conftest import login


def test_security_headers_are_sent(client):
    response = client.get("/auth/login")
    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store, max-age=0"


def test_csrf_rejects_missing_token_and_accepts_valid_token(app, client):
    app.config["WTF_CSRF_ENABLED"] = True
    rejected = client.post(
        "/auth/login", data={"usuario": "dono", "senha": "SenhaForte!123"}
    )
    assert rejected.status_code == 400
    page = client.get("/auth/login")
    token = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', page.text).group(1)
    accepted = client.post(
        "/auth/login",
        data={"csrf_token": token, "usuario": "dono", "senha": "SenhaForte!123"},
    )
    assert accepted.status_code == 302


def test_login_is_generic_for_unknown_user(client):
    response = login(client, "desconhecido", "senha-qualquer")
    assert response.status_code == 200
    assert "Credenciais inválidas ou acesso temporariamente indisponível" in response.text


def test_login_locks_account_after_five_failures(app, client):
    for _ in range(5):
        response = login(client, password="senha-errada")
        assert response.status_code == 200
    with app.app_context():
        user = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        assert user.bloqueado_ate is not None
        assert user.bloqueado_ate.replace(tzinfo=utcnow().tzinfo) > utcnow() - timedelta(seconds=2)
    response = login(client)
    assert response.status_code == 200
    assert "Credenciais inválidas ou acesso temporariamente indisponível" in response.text


def test_external_redirect_is_rejected(client):
    response = client.post(
        "/auth/login?next=https://example.org/roubo",
        data={"usuario": "dono", "senha": "SenhaForte!123"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_operator_login_goes_directly_to_cashier(client):
    response = login(client, "operador.carnes")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/empresa/1/operacao")


def test_login_cookie_has_expiration_and_security_attributes(client):
    response = login(client)
    cookie = response.headers.get("Set-Cookie", "")
    assert "Expires=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_deactivated_user_session_is_invalidated(app, client):
    login(client)
    with app.app_context():
        user = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        user.ativo = False
        db.session.commit()
    response = client.get("/grupo/estoque")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_production_rejects_non_postgresql(monkeypatch):
    class UnsafeProduction(Config):
        APP_ENV = "production"
        SECRET_KEY = "uma-chave-forte-com-mais-de-trinta-e-dois-caracteres"
        SQLALCHEMY_DATABASE_URI = "sqlite:///nao-pode.sqlite3"

    with pytest.raises(RuntimeError, match="PostgreSQL"):
        create_app(UnsafeProduction)


def test_production_rejects_example_database_password():
    class PlaceholderPassword(Config):
        APP_ENV = "production"
        SECRET_KEY = "uma-chave-forte-com-mais-de-trinta-e-dois-caracteres"
        SQLALCHEMY_DATABASE_URI = (
            "postgresql+psycopg://bigjhow:troque-esta-senha@db:5432/bigjhow"
        )

    with pytest.raises(RuntimeError, match="senha de exemplo"):
        create_app(PlaceholderPassword)


def test_invalid_application_timezone_is_rejected():
    class InvalidTimezone(Config):
        APP_ENV = "testing"
        APP_TIMEZONE = "Zona/Inexistente"
        SECRET_KEY = "uma-chave-forte-com-mais-de-trinta-e-dois-caracteres"
        SQLALCHEMY_DATABASE_URI = "sqlite+pysqlite:///:memory:"

    with pytest.raises(RuntimeError, match="APP_TIMEZONE inválido"):
        create_app(InvalidTimezone)
