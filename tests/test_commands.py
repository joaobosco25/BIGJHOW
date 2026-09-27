from __future__ import annotations

from sqlalchemy import text

from bigjhow.extensions import db
from bigjhow.models import Usuario, Venda


def test_create_admin_cli_hashes_password(app):
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=[
            "criar-admin",
            "--nome",
            "Novo Dono",
            "--usuario",
            "novo.dono",
            "--password",
            "OutraSenha!456",
        ]
    )
    assert result.exit_code == 0, result.output
    with app.app_context():
        user = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "novo.dono"))
        assert user is not None
        assert user.senha_hash.startswith("scrypt:")
        assert "OutraSenha!456" not in user.senha_hash
        assert user.papel == "proprietario"


def test_create_admin_cli_rejects_weak_password(app):
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=[
            "criar-admin",
            "--nome",
            "Novo Dono",
            "--usuario",
            "dono.fraco",
            "--password",
            "fraca",
        ]
    )
    assert result.exit_code != 0
    assert "pelo menos 12 caracteres" in result.output


def test_base4_verification_command_reports_expected_revision(app):
    with app.app_context():
        db.session.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        db.session.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('f8a72c10d4e6')")
        )
        db.session.commit()
    result = app.test_cli_runner().invoke(args=["verificar-base4"])
    assert result.exit_code == 0, result.output
    assert "[OK] Migration f8a72c10d4e6" in result.output
    assert "1 código(s) ativo(s)" in result.output

    with app.app_context():
        owner = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        db.session.add(
            Venda(
                empresa_id=1,
                numero=999,
                operador_id=owner.id,
                status="finalizada",
            )
        )
        db.session.commit()
    inconsistente = app.test_cli_runner().invoke(args=["verificar-base4"])
    assert inconsistente.exit_code != 0
    assert "vendas encerradas sem itens" in inconsistente.output
    assert "pagamentos inconsistentes" in inconsistente.output
