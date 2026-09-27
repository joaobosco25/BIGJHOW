"""usuarios, perfis fixos e controle de sessoes

Revision ID: 7b6c9e84a102
Revises: 28573b623f02
Create Date: 2026-08-22 19:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "7b6c9e84a102"
down_revision = "28573b623f02"
branch_labels = None
depends_on = None


def upgrade():
    conexao = op.get_bind()
    duplicado = conexao.execute(
        sa.text(
            "SELECT lower(email) FROM usuarios "
            "GROUP BY lower(email) HAVING count(*) > 1 LIMIT 1"
        )
    ).scalar()
    if duplicado is not None:
        raise RuntimeError(
            "A migração foi interrompida: existem logins que diferem apenas por maiúsculas."
        )

    op.drop_index("ix_usuarios_email", table_name="usuarios")
    with op.batch_alter_table("usuarios", schema=None) as batch_op:
        batch_op.drop_constraint("papel_valido", type_="check")
        batch_op.alter_column(
            "email",
            new_column_name="usuario",
            existing_type=sa.String(length=254),
            existing_nullable=False,
        )
        batch_op.add_column(
            sa.Column(
                "troca_senha_obrigatoria",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column("sessao_versao", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("versao", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.create_check_constraint(
            "papel_valido_transicao",
            "papel IN ('proprietario', 'administrador', 'estoquista', 'operador')",
        )
        batch_op.create_check_constraint(
            "tentativas_falhas_nao_negativas", "tentativas_falhas >= 0"
        )
        batch_op.create_check_constraint("sessao_versao_positiva", "sessao_versao >= 1")
        batch_op.create_check_constraint("versao_positiva", "versao >= 1")

    op.execute(sa.text("UPDATE usuarios SET usuario = lower(usuario)"))
    op.execute(sa.text("UPDATE usuarios SET papel = 'operador' WHERE papel = 'estoquista'"))

    with op.batch_alter_table("usuarios", schema=None) as batch_op:
        batch_op.drop_constraint("papel_valido_transicao", type_="check")
        batch_op.create_check_constraint(
            "papel_valido",
            "papel IN ('proprietario', 'administrador', 'operador')",
        )
        batch_op.create_check_constraint("usuario_minusculo", "usuario = lower(usuario)")
        batch_op.alter_column(
            "troca_senha_obrigatoria", existing_type=sa.Boolean(), server_default=None
        )
        batch_op.alter_column(
            "sessao_versao", existing_type=sa.Integer(), server_default=None
        )
        batch_op.alter_column("versao", existing_type=sa.Integer(), server_default=None)

    op.create_index("ix_usuarios_usuario", "usuarios", ["usuario"], unique=True)


def downgrade():
    op.drop_index("ix_usuarios_usuario", table_name="usuarios")
    with op.batch_alter_table("usuarios", schema=None) as batch_op:
        batch_op.drop_constraint("papel_valido", type_="check")
        batch_op.drop_constraint("usuario_minusculo", type_="check")
        batch_op.create_check_constraint(
            "papel_valido_transicao",
            "papel IN ('proprietario', 'administrador', 'estoquista', 'operador')",
        )

    op.execute(sa.text("UPDATE usuarios SET papel = 'estoquista' WHERE papel = 'operador'"))

    with op.batch_alter_table("usuarios", schema=None) as batch_op:
        batch_op.drop_constraint("papel_valido_transicao", type_="check")
        batch_op.drop_constraint("tentativas_falhas_nao_negativas", type_="check")
        batch_op.drop_constraint("sessao_versao_positiva", type_="check")
        batch_op.drop_constraint("versao_positiva", type_="check")
        batch_op.drop_column("versao")
        batch_op.drop_column("sessao_versao")
        batch_op.drop_column("troca_senha_obrigatoria")
        batch_op.alter_column(
            "usuario",
            new_column_name="email",
            existing_type=sa.String(length=254),
            existing_nullable=False,
        )
        batch_op.create_check_constraint(
            "papel_valido",
            "papel IN ('proprietario', 'administrador', 'estoquista')",
        )

    op.create_index("ix_usuarios_email", "usuarios", ["email"], unique=True)
