"""separa configuracao fiscal por modelo, ambiente e serie

Revision ID: e2b4c6d8f901
Revises: b6c4d8e2a915
Create Date: 2026-09-18 17:10:00.000000

Cria uma tabela aditiva para separar numeração e rota fiscal de NF-e/NFC-e por
empresa, modelo, ambiente e série. A configuração atualmente selecionada em
``estabelecimentos_fiscais`` é copiada para a nova tabela sem apagar ou
reescrever a origem. Assim, o rollback do código continua possível sem perder
o estado anterior.
"""

from alembic import op
import sqlalchemy as sa


revision = "e2b4c6d8f901"
down_revision = "b6c4d8e2a915"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "configuracoes_documentos_fiscais",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("modelo", sa.Integer(), nullable=False),
        sa.Column("ambiente", sa.Integer(), nullable=False),
        sa.Column("serie", sa.Integer(), nullable=False),
        sa.Column("proximo_numero", sa.BigInteger(), nullable=False),
        sa.Column("natureza_operacao", sa.String(length=60), nullable=False),
        sa.Column("informacoes_complementares", sa.String(length=500), nullable=True),
        sa.Column("provedor", sa.String(length=40), nullable=False),
        sa.Column("parametros_provedor", sa.JSON(), nullable=False),
        sa.Column("csc_id", sa.String(length=6), nullable=True),
        sa.Column("url_consulta_qrcode", sa.String(length=300), nullable=True),
        sa.Column("url_consulta_chave", sa.String(length=300), nullable=True),
        sa.Column("enderecos_conferidos", sa.Boolean(), nullable=False),
        sa.Column("emissao_ativa", sa.Boolean(), nullable=False),
        sa.Column("permitir_contingencia", sa.Boolean(), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("atualizado_por_id", sa.Uuid(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("ambiente IN (1, 2)", name="ck_configuracoes_documentos_fiscais_ambiente_valido"),
        sa.CheckConstraint("modelo IN (55, 65, 59)", name="ck_configuracoes_documentos_fiscais_modelo_valido"),
        sa.CheckConstraint("proximo_numero BETWEEN 1 AND 999999999", name="ck_configuracoes_documentos_fiscais_numero_valido"),
        sa.CheckConstraint("serie BETWEEN 0 AND 999", name="ck_configuracoes_documentos_fiscais_serie_valida"),
        sa.CheckConstraint("versao >= 1", name="ck_configuracoes_documentos_fiscais_versao_positiva"),
        sa.ForeignKeyConstraint(["atualizado_por_id"], ["usuarios.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "empresa_id", "modelo", "ambiente", "serie",
            name="empresa_modelo_ambiente_serie_fiscal",
        ),
    )
    op.create_index(
        "ix_config_fiscal_empresa_modelo_ambiente",
        "configuracoes_documentos_fiscais",
        ["empresa_id", "modelo", "ambiente"],
        unique=False,
    )

    # Homologação e produção podem usar o mesmo número sem colisão. A regra
    # anterior não incluía ambiente e por isso impedia as duas sequências.
    with op.batch_alter_table("documentos_fiscais") as batch_op:
        batch_op.drop_constraint("numeracao_unica", type_="unique")
        batch_op.create_unique_constraint(
            "numeracao_unica_ambiente",
            ["empresa_id", "modelo", "ambiente", "serie", "numero"],
        )

    # Inutilização também pertence a um ambiente. Registros antigos recebem o
    # ambiente atualmente configurado da empresa (ou homologação como fallback).
    op.add_column(
        "inutilizacoes_fiscais",
        sa.Column("ambiente", sa.Integer(), nullable=False, server_default="2"),
    )
    op.execute(sa.text("""
        UPDATE inutilizacoes_fiscais
        SET ambiente = COALESCE(
            (SELECT e.ambiente FROM estabelecimentos_fiscais e
             WHERE e.empresa_id = inutilizacoes_fiscais.empresa_id),
            2
        )
    """))
    with op.batch_alter_table("inutilizacoes_fiscais") as batch_op:
        batch_op.drop_constraint("inutilizacao_unica", type_="unique")
        batch_op.create_unique_constraint(
            "inutilizacao_unica_ambiente",
            ["cnpj", "modelo", "ambiente", "serie", "numero"],
        )

    # Compatível com PostgreSQL e SQLite usados pelo projeto/testes. A nova
    # tabela recebe exatamente o estado atual, sem alterar o registro legado.
    op.execute(sa.text("""
        INSERT INTO configuracoes_documentos_fiscais (
            empresa_id, modelo, ambiente, serie, proximo_numero,
            natureza_operacao, informacoes_complementares, provedor,
            parametros_provedor, csc_id, url_consulta_qrcode, url_consulta_chave,
            enderecos_conferidos, emissao_ativa, permitir_contingencia,
            versao, atualizado_por_id, criado_em, atualizado_em
        )
        SELECT
            empresa_id, modelo, ambiente, serie, proximo_numero,
            natureza_operacao, informacoes_complementares, provedor,
            parametros_provedor, csc_id, url_consulta_qrcode, url_consulta_chave,
            enderecos_conferidos, emissao_ativa, permitir_contingencia,
            CASE WHEN versao IS NULL OR versao < 1 THEN 1 ELSE versao END,
            atualizado_por_id, criado_em, atualizado_em
        FROM estabelecimentos_fiscais
    """))


def downgrade():
    conexao = op.get_bind()
    # Se já existem múltiplas configurações de uma empresa, removê-las faria
    # perder sequência/ambiente que a tabela antiga não consegue representar.
    multiplas = conexao.execute(sa.text("""
        SELECT empresa_id
        FROM configuracoes_documentos_fiscais
        GROUP BY empresa_id
        HAVING COUNT(*) > 1
        LIMIT 1
    """)).first()
    if multiplas is not None:
        raise RuntimeError(
            "Downgrade bloqueado: há empresa com múltiplas configurações fiscais; "
            "a tabela legada não consegue preservá-las."
        )

    # O esquema antigo não distingue homologação/produção na unicidade.
    duplicado_documento = conexao.execute(sa.text("""
        SELECT empresa_id, modelo, serie, numero
        FROM documentos_fiscais
        GROUP BY empresa_id, modelo, serie, numero
        HAVING COUNT(*) > 1
        LIMIT 1
    """)).first()
    duplicado_inutilizacao = conexao.execute(sa.text("""
        SELECT cnpj, modelo, serie, numero
        FROM inutilizacoes_fiscais
        GROUP BY cnpj, modelo, serie, numero
        HAVING COUNT(*) > 1
        LIMIT 1
    """)).first()
    if duplicado_documento is not None or duplicado_inutilizacao is not None:
        raise RuntimeError(
            "Downgrade bloqueado: existem números iguais em ambientes distintos; "
            "o esquema anterior não consegue representá-los."
        )

    with op.batch_alter_table("inutilizacoes_fiscais") as batch_op:
        batch_op.drop_constraint("inutilizacao_unica_ambiente", type_="unique")
        batch_op.create_unique_constraint(
            "inutilizacao_unica", ["cnpj", "modelo", "serie", "numero"]
        )
        batch_op.drop_column("ambiente")

    with op.batch_alter_table("documentos_fiscais") as batch_op:
        batch_op.drop_constraint("numeracao_unica_ambiente", type_="unique")
        batch_op.create_unique_constraint(
            "numeracao_unica", ["empresa_id", "modelo", "serie", "numero"]
        )

    op.drop_index(
        "ix_config_fiscal_empresa_modelo_ambiente",
        table_name="configuracoes_documentos_fiscais",
    )
    op.drop_table("configuracoes_documentos_fiscais")
