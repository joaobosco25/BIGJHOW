from __future__ import annotations

from sqlalchemy import text

from bigjhow import create_app
from bigjhow.config import TestConfig
from bigjhow.extensions import db
from bigjhow.models import CodigoProduto, Produto


def test_base4_migration_upgrades_full_base3_and_imports_only_unambiguous_codes(
    tmp_path,
):
    database_path = tmp_path / "migration-base4.sqlite"

    class MigrationConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite+pysqlite:///{database_path}"

    app = create_app(MigrationConfig)
    runner = app.test_cli_runner()
    base3 = runner.invoke(args=["db", "upgrade", "c4f36e82a901"])
    assert base3.exit_code == 0, base3.output
    carga = runner.invoke(args=["popular-base"])
    assert carga.exit_code == 0, carga.output
    assert "2903 importados" in carga.output
    base4 = runner.invoke(args=["db", "upgrade", "f8a72c10d4e6"])
    assert base4.exit_code == 0, base4.output

    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Produto.id))) == 2903
        assert db.session.scalar(db.select(db.func.count(CodigoProduto.id))) == 2666
        assert db.session.scalar(
            db.select(db.func.count()).select_from(
                db.select(CodigoProduto.empresa_id, CodigoProduto.chave_busca)
                .group_by(CodigoProduto.empresa_id, CodigoProduto.chave_busca)
                .having(db.func.count(CodigoProduto.id) > 1)
                .subquery()
            )
        ) == 0
        revisao = db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert revisao == "f8a72c10d4e6"

    verificacao = runner.invoke(args=["verificar-base4"])
    assert verificacao.exit_code == 0, verificacao.output
    assert "2903 produto(s)" in verificacao.output
    assert "2666 código(s) ativo(s)" in verificacao.output

    with app.app_context():
        produto_id, empresa_id = db.session.execute(
            db.select(Produto.id, Produto.empresa_id).limit(1)
        ).one()
        db.session.add(
            CodigoProduto(
                empresa_id=empresa_id,
                produto_id=produto_id,
                codigo="MANUAL-ROLLBACK-BLOCK",
                chave_busca="MANUALROLLBACKBLOCK",
                tipo="interno",
                principal=False,
            )
        )
        db.session.commit()
    downgrade = runner.invoke(args=["db", "downgrade", "c4f36e82a901"])
    assert downgrade.exit_code != 0
    with app.app_context():
        assert db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "f8a72c10d4e6"
