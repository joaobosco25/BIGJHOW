"""integração MGV6: mapeamento e status de preço por produto

Revision ID: b6c4d8e2a915
Revises: f1a8d3c7e204
Create Date: 2026-09-11 16:30:00.000000

Migração aditiva. Cria somente metadados de integração com o MGV6; não altera
estoque, vendas, fiscal, códigos de leitura nem preços existentes.
"""

from alembic import op
import sqlalchemy as sa


revision = "b6c4d8e2a915"
down_revision = "f1a8d3c7e204"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "integracoes_mgv6_produtos",
        sa.Column("produto_id", sa.Uuid(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("codigo_item", sa.String(length=6), nullable=False),
        sa.Column("descricao_mgv", sa.String(length=240), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("preco_mgv", sa.Numeric(14, 2), nullable=True),
        sa.Column("preco_enviado", sa.Numeric(14, 2), nullable=True),
        sa.Column("numero_importacao", sa.Integer(), nullable=True),
        sa.Column("ultima_mensagem", sa.String(length=500), nullable=True),
        sa.Column("ultima_tentativa_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sincronizado_mgv_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("carga_solicitada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("balanca_confirmada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("atualizado_por_id", sa.Uuid(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pendente', 'validado', 'mgv_ok', 'aguardando_balanca', 'erro')",
            name=op.f("ck_integracoes_mgv6_produtos_status_valido"),
        ),
        sa.CheckConstraint(
            "versao >= 1",
            name=op.f("ck_integracoes_mgv6_produtos_versao_positiva"),
        ),
        sa.ForeignKeyConstraint(
            ["empresa_id"], ["empresas.id"],
            name=op.f("fk_integracoes_mgv6_produtos_empresa_id_empresas"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["produto_id"], ["produtos.id"],
            name=op.f("fk_integracoes_mgv6_produtos_produto_id_produtos"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["atualizado_por_id"], ["usuarios.id"],
            name=op.f("fk_integracoes_mgv6_produtos_atualizado_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "produto_id", name=op.f("pk_integracoes_mgv6_produtos")
        ),
        sa.UniqueConstraint(
            "empresa_id", "codigo_item", name="empresa_codigo_item_mgv6"
        ),
    )
    op.create_index(
        op.f("ix_integracoes_mgv6_produtos_empresa_id"),
        "integracoes_mgv6_produtos",
        ["empresa_id"],
        unique=False,
    )
    op.create_index(
        "ix_integracoes_mgv6_empresa_status",
        "integracoes_mgv6_produtos",
        ["empresa_id", "status"],
        unique=False,
    )


def downgrade():
    conexao = op.get_bind()
    em_uso = conexao.execute(
        sa.text("SELECT count(*) FROM integracoes_mgv6_produtos")
    ).scalar()
    if em_uso:
        raise RuntimeError(
            "A reversão foi bloqueada: existem vínculos MGV6 cadastrados. "
            "Remova os vínculos conscientemente antes de reverter."
        )
    op.drop_index(
        "ix_integracoes_mgv6_empresa_status",
        table_name="integracoes_mgv6_produtos",
    )
    op.drop_index(
        op.f("ix_integracoes_mgv6_produtos_empresa_id"),
        table_name="integracoes_mgv6_produtos",
    )
    op.drop_table("integracoes_mgv6_produtos")
