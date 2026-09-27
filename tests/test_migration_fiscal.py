"""Migração do módulo fiscal: sobe limpo e recusa reverter com acervo gravado."""

from __future__ import annotations


from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from bigjhow import create_app
from bigjhow.config import TestConfig
from bigjhow.extensions import db
from bigjhow.models import ConfiguracaoDocumentoFiscal, Empresa, EstabelecimentoFiscal


TABELAS_FISCAIS = (
    "estabelecimentos_fiscais",
    "configuracoes_documentos_fiscais",
    "credenciais_fiscais",
    "produtos_fiscais",
    "documentos_fiscais",
    "documentos_fiscais_itens",
    "documentos_fiscais_pagamentos",
    "documentos_fiscais_xml",
    "documentos_fiscais_eventos",
    "inutilizacoes_fiscais",
)


def _aplicacao(tmp_path, nome: str):
    caminho = tmp_path / nome

    class MigrationConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite+pysqlite:///{caminho}"

    return create_app(MigrationConfig)


def test_migracao_fiscal_sobe_ate_o_topo_e_cria_as_tabelas(tmp_path):
    app = _aplicacao(tmp_path, "migracao-fiscal.sqlite")
    runner = app.test_cli_runner()
    resultado = runner.invoke(args=["db", "upgrade"])
    assert resultado.exit_code == 0, resultado.output

    with app.app_context():
        configuracao = app.extensions["migrate"].migrate.get_config()
        topo = ScriptDirectory.from_config(configuracao).get_current_head()
        assert "d5e91f7a3c08" in {
            revisao.revision for revisao in ScriptDirectory.from_config(configuracao).walk_revisions()
        }
        assert db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == topo

        existentes = set(inspect(db.engine).get_table_names())
        faltando = [tabela for tabela in TABELAS_FISCAIS if tabela not in existentes]
        assert not faltando, f"tabelas fiscais ausentes: {faltando}"


def test_reversao_da_migracao_fiscal_e_bloqueada_com_acervo_gravado(tmp_path):
    """Reverter com dado gravado destruiria acervo que precisa durar cinco anos."""
    app = _aplicacao(tmp_path, "reversao-fiscal.sqlite")
    runner = app.test_cli_runner()
    assert runner.invoke(args=["db", "upgrade"]).exit_code == 0

    with app.app_context():
        db.session.add(Empresa(id=1, nome="Big Jhow Carnes", slug="big-jhow-carnes"))
        db.session.flush()
        db.session.add(
            EstabelecimentoFiscal(
                empresa_id=1,
                cnpj="11222333000181",
                razao_social="BIG JHOW CARNES LTDA",
                inscricao_estadual="110042490114",
                logradouro="Avenida Brasil",
                bairro="Centro",
                codigo_municipio=3550308,
                municipio="Sao Paulo",
                uf="SP",
                cep="01010000",
            )
        )
        db.session.commit()

    reversao = runner.invoke(args=["db", "downgrade", "a31d7e5c9b42"])
    assert reversao.exit_code != 0, "a reversão deveria ter sido bloqueada"

    with app.app_context():
        assert db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "d5e91f7a3c08"
        assert "documentos_fiscais" in set(inspect(db.engine).get_table_names())


def test_reversao_da_migracao_fiscal_e_permitida_sem_acervo(tmp_path):
    """Sem nada gravado não há o que preservar, então a reversão é limpa."""
    app = _aplicacao(tmp_path, "reversao-vazia.sqlite")
    runner = app.test_cli_runner()
    assert runner.invoke(args=["db", "upgrade"]).exit_code == 0

    reversao = runner.invoke(args=["db", "downgrade", "a31d7e5c9b42"])
    assert reversao.exit_code == 0, reversao.output

    with app.app_context():
        assert db.session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "a31d7e5c9b42"
        existentes = set(inspect(db.engine).get_table_names())
        assert not [t for t in TABELAS_FISCAIS if t in existentes]


def test_migracao_multiconfiguracao_preserva_e_copia_estado_anterior(tmp_path):
    app = _aplicacao(tmp_path, "migracao-multiconfig.sqlite")
    runner = app.test_cli_runner()
    assert runner.invoke(args=["db", "upgrade", "b6c4d8e2a915"]).exit_code == 0

    with app.app_context():
        db.session.add(Empresa(id=1, nome="Big Jhow Carnes", slug="big-jhow-carnes"))
        db.session.flush()
        db.session.add(
            EstabelecimentoFiscal(
                empresa_id=1, cnpj="11222333000181",
                razao_social="BIG JHOW CARNES LTDA", inscricao_estadual="110042490114",
                logradouro="Avenida Brasil", bairro="Centro", codigo_municipio=3550308,
                municipio="Sao Paulo", uf="SP", cep="01010000", modelo=65,
                ambiente=2, serie=3, proximo_numero=456, provedor="homologacao_interna",
            )
        )
        db.session.commit()

    resultado = runner.invoke(args=["db", "upgrade"])
    assert resultado.exit_code == 0, resultado.output

    with app.app_context():
        cfg = db.session.scalar(db.select(ConfiguracaoDocumentoFiscal))
        assert cfg is not None
        assert (cfg.empresa_id, cfg.modelo, cfg.ambiente, cfg.serie) == (1, 65, 2, 3)
        assert cfg.proximo_numero == 456
        legado = db.session.get(EstabelecimentoFiscal, 1)
        assert legado.proximo_numero == 456
