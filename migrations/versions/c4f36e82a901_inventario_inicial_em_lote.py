"""inventario inicial em lote

Revision ID: c4f36e82a901
Revises: 7b6c9e84a102
Create Date: 2026-08-22 23:20:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "c4f36e82a901"
down_revision = "7b6c9e84a102"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventarios_lote",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("descricao", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("criado_por_id", sa.Uuid(), nullable=True),
        sa.Column("finalizado_por_id", sa.Uuid(), nullable=True),
        sa.Column("cancelado_por_id", sa.Uuid(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalizado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelado_em", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('rascunho', 'finalizado', 'cancelado')",
            name=op.f("ck_inventarios_lote_status_valido"),
        ),
        sa.CheckConstraint(
            "versao >= 1", name=op.f("ck_inventarios_lote_versao_positiva")
        ),
        sa.ForeignKeyConstraint(
            ["cancelado_por_id"],
            ["usuarios.id"],
            name=op.f("fk_inventarios_lote_cancelado_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["criado_por_id"],
            ["usuarios.id"],
            name=op.f("fk_inventarios_lote_criado_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["empresa_id"],
            ["empresas.id"],
            name=op.f("fk_inventarios_lote_empresa_id_empresas"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["finalizado_por_id"],
            ["usuarios.id"],
            name=op.f("fk_inventarios_lote_finalizado_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventarios_lote")),
    )
    op.create_index(
        "ix_inventarios_lote_empresa_criado",
        "inventarios_lote",
        ["empresa_id", "criado_em"],
        unique=False,
    )
    op.create_index(
        "uq_inventarios_lote_empresa_rascunho",
        "inventarios_lote",
        ["empresa_id"],
        unique=True,
        postgresql_where=sa.text("status = 'rascunho'"),
        sqlite_where=sa.text("status = 'rascunho'"),
    )

    op.create_table(
        "inventarios_lote_itens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("inventario_id", sa.Uuid(), nullable=False),
        sa.Column("produto_id", sa.Uuid(), nullable=False),
        sa.Column("quantidade_referencia", sa.Numeric(15, 3), nullable=False),
        sa.Column("aguardando_referencia", sa.Boolean(), nullable=False),
        sa.Column("unidade_referencia", sa.String(length=12), nullable=False),
        sa.Column("pesado_referencia", sa.Boolean(), nullable=False),
        sa.Column("quantidade_contada", sa.Numeric(15, 3), nullable=True),
        sa.Column("contado_por_id", sa.Uuid(), nullable=True),
        sa.Column("contado_em", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "quantidade_contada IS NULL OR quantidade_contada >= 0",
            name=op.f(
                "ck_inventarios_lote_itens_quantidade_contada_nao_negativa"
            ),
        ),
        sa.CheckConstraint(
            "quantidade_referencia >= 0",
            name=op.f(
                "ck_inventarios_lote_itens_quantidade_referencia_nao_negativa"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["contado_por_id"],
            ["usuarios.id"],
            name=op.f("fk_inventarios_lote_itens_contado_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["inventario_id"],
            ["inventarios_lote.id"],
            name=op.f("fk_inventarios_lote_itens_inventario_id_inventarios_lote"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["produto_id"],
            ["produtos.id"],
            name=op.f("fk_inventarios_lote_itens_produto_id_produtos"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventarios_lote_itens")),
        sa.UniqueConstraint(
            "inventario_id", "produto_id", name="inventario_produto"
        ),
    )
    op.create_index(
        "ix_inventarios_lote_itens_inventario_contado",
        "inventarios_lote_itens",
        ["inventario_id", "quantidade_contada"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_inventarios_lote_itens_inventario_contado",
        table_name="inventarios_lote_itens",
    )
    op.drop_table("inventarios_lote_itens")
    op.drop_index(
        "uq_inventarios_lote_empresa_rascunho", table_name="inventarios_lote"
    )
    op.drop_index(
        "ix_inventarios_lote_empresa_criado", table_name="inventarios_lote"
    )
    op.drop_table("inventarios_lote")
