from __future__ import annotations

from alembic.script import ScriptDirectory
from sqlalchemy import text

from bigjhow import create_app
from bigjhow.config import TestConfig
from bigjhow.extensions import db
from bigjhow.models import Impressora, RegraImpressao, TerminalCaixa, TurnoCaixa


def test_base5_migration_setup_verifier_and_protected_downgrade(tmp_path):
    database_path = tmp_path / "migration-base5.sqlite"

    class MigrationConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite+pysqlite:///{database_path}"

    app = create_app(MigrationConfig)
    runner = app.test_cli_runner()
    base4 = runner.invoke(args=["db", "upgrade", "f8a72c10d4e6"])
    assert base4.exit_code == 0, base4.output
    carga = runner.invoke(args=["popular-base"])
    assert carga.exit_code == 0, carga.output
    base5 = runner.invoke(args=["db", "upgrade"])
    assert base5.exit_code == 0, base5.output
    preparo = runner.invoke(args=["preparar-base5"])
    assert preparo.exit_code == 0, preparo.output
    assert "3 terminal(is)" in preparo.output
    repetido = runner.invoke(args=["preparar-base5"])
    assert repetido.exit_code == 0, repetido.output
    assert "0 terminal(is)" in repetido.output
    verificacao = runner.invoke(args=["verificar-base5"])
    assert verificacao.exit_code == 0, verificacao.output

    with app.app_context():
        # A revisão de topo muda a cada migração nova; o que importa aqui é que
        # o "db upgrade" tenha levado o banco até a última.
        configuracao = app.extensions["migrate"].migrate.get_config()
        topo = ScriptDirectory.from_config(configuracao).get_current_head()
        assert db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == topo
        assert db.session.scalar(db.select(db.func.count(TerminalCaixa.id))) == 3
        assert db.session.scalar(db.select(db.func.count(Impressora.id))) == 3
        assert db.session.scalar(db.select(db.func.count(RegraImpressao.id))) == 12
        terminal = db.session.scalar(db.select(TerminalCaixa).limit(1))
        db.session.add(
            TurnoCaixa(
                empresa_id=terminal.empresa_id,
                terminal_id=terminal.id,
                valor_inicial=0,
            )
        )
        db.session.commit()

    downgrade = runner.invoke(args=["db", "downgrade", "f8a72c10d4e6"])
    assert downgrade.exit_code != 0
    with app.app_context():
        # As migrações posteriores sem dado gravado revertem sem perda; a
        # reversão para antes da Base 5 é que fica bloqueada, porque há turno,
        # terminal e impressora que precisam continuar auditáveis.
        assert db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "a31d7e5c9b42"
