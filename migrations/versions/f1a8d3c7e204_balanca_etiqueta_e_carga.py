"""balanca: leitura da etiqueta pesada e carga de precos

Revision ID: f1a8d3c7e204
Revises: d5e91f7a3c08
Create Date: 2026-09-11 08:00:00.000000

Cria apenas a tabela de configuracao da balanca. Aditiva: nenhuma tabela
existente e alterada.
"""

from alembic import op
import sqlalchemy as sa


revision = "f1a8d3c7e204"
down_revision = "d5e91f7a3c08"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "configuracoes_balanca",
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("modelo", sa.String(length=60), nullable=True),
        sa.Column("prefixo", sa.String(length=1), nullable=False),
        sa.Column("tamanho_plu", sa.Integer(), nullable=False),
        sa.Column("conteudo", sa.String(length=10), nullable=False),
        sa.Column("casas_decimais", sa.Integer(), nullable=False),
        sa.Column("tolerancia", sa.Numeric(6, 2), nullable=False),
        sa.Column("bloquear_divergencia", sa.Boolean(), nullable=False),
        sa.Column("separador_carga", sa.String(length=3), nullable=False),
        sa.Column("codificacao_carga", sa.String(length=20), nullable=False),
        sa.Column("quebra_linha_carga", sa.String(length=8), nullable=False),
        sa.Column("campos_carga", sa.JSON(), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("atualizado_por_id", sa.Uuid(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "conteudo IN ('peso', 'preco')",
            name=op.f("ck_configuracoes_balanca_conteudo_valido"),
        ),
        sa.CheckConstraint(
            "tamanho_plu BETWEEN 4 AND 7",
            name=op.f("ck_configuracoes_balanca_tamanho_plu_valido"),
        ),
        sa.CheckConstraint(
            "casas_decimais BETWEEN 0 AND 3",
            name=op.f("ck_configuracoes_balanca_casas_decimais_validas"),
        ),
        sa.CheckConstraint(
            "tolerancia >= 0", name=op.f("ck_configuracoes_balanca_tolerancia_nao_negativa")
        ),
        sa.CheckConstraint("versao >= 1", name=op.f("ck_configuracoes_balanca_versao_positiva")),
        sa.ForeignKeyConstraint(
            ["empresa_id"], ["empresas.id"],
            name=op.f("fk_configuracoes_balanca_empresa_id_empresas"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["atualizado_por_id"], ["usuarios.id"],
            name=op.f("fk_configuracoes_balanca_atualizado_por_id_usuarios"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("empresa_id", name=op.f("pk_configuracoes_balanca")),
    )


def downgrade():
    # Configuracao de leitura de etiqueta e barata de refazer, mas perde-la sem
    # aviso faria o caixa cobrar errado na proxima venda pesada. Bloqueia se
    # estiver em uso.
    conexao = op.get_bind()
    em_uso = conexao.execute(
        sa.text("SELECT count(*) FROM configuracoes_balanca WHERE ativo")
    ).scalar()
    if em_uso:
        raise RuntimeError(
            "A reversao foi bloqueada: existe balanca configurada e ativa. "
            "Desative a leitura de etiqueta antes de reverter."
        )
    op.drop_table("configuracoes_balanca")
