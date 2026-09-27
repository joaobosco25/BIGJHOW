from __future__ import annotations

import os
import secrets
from datetime import timedelta

from dotenv import load_dotenv


load_dotenv()




def _env_bool(nome: str, padrao: bool = False) -> bool:
    valor = os.getenv(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() in {"1", "true", "yes", "sim", "on"}


def _env_int(nome: str, padrao: int) -> int:
    valor = os.getenv(nome, "").strip()
    if not valor:
        return padrao
    try:
        return int(valor)
    except ValueError:
        return padrao

def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if value.startswith("postgres://"):
        value = value.replace("postgres://", "postgresql+psycopg://", 1)
    elif value.startswith("postgresql://"):
        value = value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


class Config:
    APP_ENV = os.getenv("APP_ENV", "production").lower()
    APP_TIMEZONE = os.getenv("APP_TIMEZONE", "America/Sao_Paulo").strip()
    SECRET_KEY = os.getenv("SECRET_KEY", "")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = APP_ENV == "production"
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)
    SESSION_REFRESH_EACH_REQUEST = True
    PREFERRED_URL_SCHEME = "https" if APP_ENV == "production" else "http"

    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    MAX_FORM_MEMORY_SIZE = 500_000
    MAX_FORM_PARTS = 100

    WTF_CSRF_TIME_LIMIT = 3600
    WTF_CSRF_SSL_STRICT = APP_ENV == "production"

    # Chave que cifra CSC, senha de certificado e token de gateway.
    # Fica separada da SECRET_KEY: trocar a sessão da aplicação não pode
    # inutilizar as credenciais fiscais já guardadas.
    FISCAL_SECRET_KEY = os.getenv("FISCAL_SECRET_KEY", "").strip()

    # Integração oficial REST do MGV6/Toledo. A palavra-chave fica apenas
    # no ambiente do servidor; nunca é gravada no banco nem enviada ao navegador.
    MGV6_ENABLED = _env_bool("MGV6_ENABLED", False)
    # Escrita é uma ativação separada: o MGV6 cancela uma importação já aberta
    # quando outra é iniciada. Mantenha False enquanto o legado ainda puder importar.
    MGV6_WRITE_ENABLED = _env_bool("MGV6_WRITE_ENABLED", False)
    MGV6_BASE_URL = os.getenv(
        "MGV6_BASE_URL", "http://192.0.2.200:9300/MGV6_WCF/REST"
    ).strip()
    MGV6_PALAVRA_CHAVE = os.getenv("MGV6_PALAVRA_CHAVE", "").strip()
    MGV6_LOJA = _env_int("MGV6_LOJA", 1)
    MGV6_BALANCA = _env_int("MGV6_BALANCA", 1)
    MGV6_TIMEOUT = _env_int("MGV6_TIMEOUT", 5)
    MGV6_SOLICITAR_CARGA = _env_bool("MGV6_SOLICITAR_CARGA", True)
    MGV6_LOCK_PATH = os.getenv("MGV6_LOCK_PATH", "/tmp/bigjhow-mgv6.lock").strip()

    TRUST_PROXY = os.getenv("TRUST_PROXY", "false").lower() == "true"
    TRUSTED_HOSTS = [
        host.strip()
        for host in os.getenv("TRUSTED_HOSTS", "localhost,127.0.0.1").split(",")
        if host.strip()
    ]


class TestConfig(Config):
    TESTING = True
    APP_ENV = "testing"
    SECRET_KEY = secrets.token_hex(32)
    SQLALCHEMY_DATABASE_URI = "sqlite+pysqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_SSL_STRICT = False
    PREFERRED_URL_SCHEME = "http"
    TRUSTED_HOSTS = None
    MGV6_ENABLED = False
    MGV6_WRITE_ENABLED = False
    MGV6_PALAVRA_CHAVE = ""
