"""turnos de caixa e fila de impressao segura

Revision ID: a31d7e5c9b42
Revises: f8a72c10d4e6
Create Date: 2026-08-24 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "a31d7e5c9b42"
down_revision = "f8a72c10d4e6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "terminais_caixa",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(length=40), nullable=False),
        sa.Column("nome", sa.String(length=120), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("criado_por_id", sa.Uuid(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "versao >= 1", name=op.f("ck_terminais_caixa_versao_positiva")
        ),
        sa.ForeignKeyConstraint(
            ["criado_por_id"],
            ["usuarios.id"],
            name=op.f("fk_terminais_caixa_criado_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["empresa_id"],
            ["empresas.id"],
            name=op.f("fk_terminais_caixa_empresa_id_empresas"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_terminais_caixa")),
        sa.UniqueConstraint("empresa_id", "codigo", name="empresa_terminal_codigo"),
    )
    op.create_index(
        "ix_terminais_caixa_empresa_ativo",
        "terminais_caixa",
        ["empresa_id", "ativo"],
        unique=False,
    )

    op.create_table(
        "turnos_caixa",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("terminal_id", sa.Uuid(), nullable=False),
        sa.Column("operador_id", sa.Uuid(), nullable=True),
        sa.Column("aberto_por_id", sa.Uuid(), nullable=True),
        sa.Column("fechado_por_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("valor_inicial", sa.Numeric(16, 2), nullable=False),
        sa.Column("dinheiro_esperado", sa.Numeric(16, 2), nullable=True),
        sa.Column("dinheiro_informado", sa.Numeric(16, 2), nullable=True),
        sa.Column("dinheiro_diferenca", sa.Numeric(16, 2), nullable=True),
        sa.Column("pix_esperado", sa.Numeric(16, 2), nullable=True),
        sa.Column("pix_informado", sa.Numeric(16, 2), nullable=True),
        sa.Column("pix_diferenca", sa.Numeric(16, 2), nullable=True),
        sa.Column("debito_esperado", sa.Numeric(16, 2), nullable=True),
        sa.Column("debito_informado", sa.Numeric(16, 2), nullable=True),
        sa.Column("debito_diferenca", sa.Numeric(16, 2), nullable=True),
        sa.Column("credito_esperado", sa.Numeric(16, 2), nullable=True),
        sa.Column("credito_informado", sa.Numeric(16, 2), nullable=True),
        sa.Column("credito_diferenca", sa.Numeric(16, 2), nullable=True),
        sa.Column("total_vendas", sa.Numeric(16, 2), nullable=True),
        sa.Column("total_sangrias", sa.Numeric(16, 2), nullable=True),
        sa.Column("total_suprimentos", sa.Numeric(16, 2), nullable=True),
        sa.Column("justificativa_fechamento", sa.String(length=500), nullable=True),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("aberto_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fechado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('aberto', 'fechado')",
            name=op.f("ck_turnos_caixa_status_valido"),
        ),
        sa.CheckConstraint(
            "valor_inicial >= 0",
            name=op.f("ck_turnos_caixa_valor_inicial_nao_negativo"),
        ),
        sa.CheckConstraint(
            "versao >= 1", name=op.f("ck_turnos_caixa_versao_positiva")
        ),
        sa.CheckConstraint(
            "status = 'aberto' OR (fechado_em IS NOT NULL "
            "AND dinheiro_esperado IS NOT NULL AND dinheiro_informado IS NOT NULL "
            "AND dinheiro_diferenca IS NOT NULL AND pix_esperado IS NOT NULL "
            "AND pix_informado IS NOT NULL AND pix_diferenca IS NOT NULL "
            "AND debito_esperado IS NOT NULL AND debito_informado IS NOT NULL "
            "AND debito_diferenca IS NOT NULL AND credito_esperado IS NOT NULL "
            "AND credito_informado IS NOT NULL AND credito_diferenca IS NOT NULL)",
            name=op.f("ck_turnos_caixa_fechamento_completo"),
        ),
        sa.ForeignKeyConstraint(
            ["aberto_por_id"],
            ["usuarios.id"],
            name=op.f("fk_turnos_caixa_aberto_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["empresa_id"],
            ["empresas.id"],
            name=op.f("fk_turnos_caixa_empresa_id_empresas"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fechado_por_id"],
            ["usuarios.id"],
            name=op.f("fk_turnos_caixa_fechado_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["operador_id"],
            ["usuarios.id"],
            name=op.f("fk_turnos_caixa_operador_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["terminal_id"],
            ["terminais_caixa.id"],
            name=op.f("fk_turnos_caixa_terminal_id_terminais_caixa"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_turnos_caixa")),
    )
    op.create_index(
        "ix_turnos_caixa_empresa_aberto",
        "turnos_caixa",
        ["empresa_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_turnos_caixa_terminal_aberto",
        "turnos_caixa",
        ["terminal_id"],
        unique=True,
        postgresql_where=sa.text("status = 'aberto'"),
        sqlite_where=sa.text("status = 'aberto'"),
    )
    op.create_index(
        "uq_turnos_caixa_operador_aberto",
        "turnos_caixa",
        ["operador_id"],
        unique=True,
        postgresql_where=sa.text("status = 'aberto'"),
        sqlite_where=sa.text("status = 'aberto'"),
    )

    op.create_table(
        "movimentos_caixa",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("turno_id", sa.Uuid(), nullable=False),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("valor", sa.Numeric(16, 2), nullable=False),
        sa.Column("motivo", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("solicitado_por_id", sa.Uuid(), nullable=True),
        sa.Column("decidido_por_id", sa.Uuid(), nullable=True),
        sa.Column("motivo_decisao", sa.String(length=500), nullable=True),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decidido_em", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "tipo IN ('sangria', 'suprimento')",
            name=op.f("ck_movimentos_caixa_tipo_valido"),
        ),
        sa.CheckConstraint(
            "status IN ('pendente', 'aprovado', 'rejeitado')",
            name=op.f("ck_movimentos_caixa_status_valido"),
        ),
        sa.CheckConstraint(
            "valor > 0", name=op.f("ck_movimentos_caixa_valor_positivo")
        ),
        sa.CheckConstraint(
            "versao >= 1", name=op.f("ck_movimentos_caixa_versao_positiva")
        ),
        sa.CheckConstraint(
            "status = 'pendente' OR decidido_em IS NOT NULL",
            name=op.f("ck_movimentos_caixa_decisao_registrada"),
        ),
        sa.ForeignKeyConstraint(
            ["decidido_por_id"],
            ["usuarios.id"],
            name=op.f("fk_movimentos_caixa_decidido_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["empresa_id"],
            ["empresas.id"],
            name=op.f("fk_movimentos_caixa_empresa_id_empresas"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["solicitado_por_id"],
            ["usuarios.id"],
            name=op.f("fk_movimentos_caixa_solicitado_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["turno_id"],
            ["turnos_caixa.id"],
            name=op.f("fk_movimentos_caixa_turno_id_turnos_caixa"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_movimentos_caixa")),
    )
    op.create_index(
        "ix_movimentos_caixa_turno_status",
        "movimentos_caixa",
        ["turno_id", "status"],
        unique=False,
    )

    op.create_table(
        "impressoras",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(length=40), nullable=False),
        sa.Column("nome", sa.String(length=120), nullable=False),
        sa.Column("setor", sa.String(length=80), nullable=False),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("modo", sa.String(length=20), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("criado_por_id", sa.Uuid(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "tipo IN ('cupom', 'producao', 'etiqueta', 'relatorio')",
            name=op.f("ck_impressoras_tipo_valido"),
        ),
        sa.CheckConstraint(
            "modo = 'simulada'", name=op.f("ck_impressoras_modo_simulado_base5")
        ),
        sa.CheckConstraint(
            "versao >= 1", name=op.f("ck_impressoras_versao_positiva")
        ),
        sa.ForeignKeyConstraint(
            ["criado_por_id"],
            ["usuarios.id"],
            name=op.f("fk_impressoras_criado_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["empresa_id"],
            ["empresas.id"],
            name=op.f("fk_impressoras_empresa_id_empresas"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_impressoras")),
        sa.UniqueConstraint("empresa_id", "codigo", name="empresa_impressora_codigo"),
    )
    op.create_index(
        "ix_impressoras_empresa_ativo",
        "impressoras",
        ["empresa_id", "ativo"],
        unique=False,
    )

    op.create_table(
        "regras_impressao",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("terminal_id", sa.Uuid(), nullable=False),
        sa.Column("impressora_id", sa.Uuid(), nullable=False),
        sa.Column("evento", sa.String(length=40), nullable=False),
        sa.Column("copias", sa.Integer(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("criado_por_id", sa.Uuid(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "evento IN ('venda_finalizada', 'venda_cancelada', 'turno_fechado', "
            "'movimento_caixa_aprovado')",
            name=op.f("ck_regras_impressao_evento_valido"),
        ),
        sa.CheckConstraint(
            "copias BETWEEN 1 AND 3",
            name=op.f("ck_regras_impressao_copias_validas"),
        ),
        sa.CheckConstraint(
            "versao >= 1", name=op.f("ck_regras_impressao_versao_positiva")
        ),
        sa.ForeignKeyConstraint(
            ["criado_por_id"],
            ["usuarios.id"],
            name=op.f("fk_regras_impressao_criado_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["empresa_id"],
            ["empresas.id"],
            name=op.f("fk_regras_impressao_empresa_id_empresas"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["impressora_id"],
            ["impressoras.id"],
            name=op.f("fk_regras_impressao_impressora_id_impressoras"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["terminal_id"],
            ["terminais_caixa.id"],
            name=op.f("fk_regras_impressao_terminal_id_terminais_caixa"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_regras_impressao")),
        sa.UniqueConstraint(
            "terminal_id",
            "impressora_id",
            "evento",
            name="terminal_impressora_evento",
        ),
    )
    op.create_index(
        "ix_regras_impressao_empresa_evento",
        "regras_impressao",
        ["empresa_id", "evento"],
        unique=False,
    )

    op.create_table(
        "trabalhos_impressao",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("terminal_id", sa.Uuid(), nullable=False),
        sa.Column("impressora_id", sa.Uuid(), nullable=False),
        sa.Column("regra_id", sa.Uuid(), nullable=True),
        sa.Column("reimpressao_de_id", sa.Uuid(), nullable=True),
        sa.Column("evento", sa.String(length=40), nullable=False),
        sa.Column("entidade", sa.String(length=40), nullable=False),
        sa.Column("entidade_id", sa.String(length=80), nullable=False),
        sa.Column("chave_idempotencia", sa.String(length=180), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("copias", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("tentativas", sa.Integer(), nullable=False),
        sa.Column("erro_codigo", sa.String(length=80), nullable=True),
        sa.Column("erro_mensagem", sa.String(length=240), nullable=True),
        sa.Column("motivo_reimpressao", sa.String(length=500), nullable=True),
        sa.Column("solicitado_por_id", sa.Uuid(), nullable=True),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("impresso_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("falhou_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pendente', 'processando', 'impresso', 'falhou', 'cancelado')",
            name=op.f("ck_trabalhos_impressao_status_valido"),
        ),
        sa.CheckConstraint(
            "tentativas >= 0",
            name=op.f("ck_trabalhos_impressao_tentativas_nao_negativas"),
        ),
        sa.CheckConstraint(
            "copias BETWEEN 1 AND 3",
            name=op.f("ck_trabalhos_impressao_copias_validas"),
        ),
        sa.CheckConstraint(
            "versao >= 1", name=op.f("ck_trabalhos_impressao_versao_positiva")
        ),
        sa.ForeignKeyConstraint(
            ["empresa_id"],
            ["empresas.id"],
            name=op.f("fk_trabalhos_impressao_empresa_id_empresas"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["impressora_id"],
            ["impressoras.id"],
            name=op.f("fk_trabalhos_impressao_impressora_id_impressoras"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["regra_id"],
            ["regras_impressao.id"],
            name=op.f("fk_trabalhos_impressao_regra_id_regras_impressao"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reimpressao_de_id"],
            ["trabalhos_impressao.id"],
            name=op.f(
                "fk_trabalhos_impressao_reimpressao_de_id_trabalhos_impressao"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["solicitado_por_id"],
            ["usuarios.id"],
            name=op.f("fk_trabalhos_impressao_solicitado_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["terminal_id"],
            ["terminais_caixa.id"],
            name=op.f("fk_trabalhos_impressao_terminal_id_terminais_caixa"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trabalhos_impressao")),
        sa.UniqueConstraint("chave_idempotencia", name="chave_idempotencia_unica"),
    )
    op.create_index(
        "ix_trabalhos_impressao_empresa_status",
        "trabalhos_impressao",
        ["empresa_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_trabalhos_impressao_terminal_criado",
        "trabalhos_impressao",
        ["terminal_id", "criado_em"],
        unique=False,
    )

    with op.batch_alter_table("vendas", schema=None) as batch_op:
        batch_op.add_column(sa.Column("turno_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_vendas_turno_id_turnos_caixa"),
            "turnos_caixa",
            ["turno_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_vendas_turno_id", "vendas", ["turno_id"], unique=False)


def downgrade():
    conexao = op.get_bind()
    contagens = (
        conexao.execute(sa.text("SELECT count(*) FROM trabalhos_impressao")).scalar(),
        conexao.execute(sa.text("SELECT count(*) FROM regras_impressao")).scalar(),
        conexao.execute(sa.text("SELECT count(*) FROM impressoras")).scalar(),
        conexao.execute(sa.text("SELECT count(*) FROM movimentos_caixa")).scalar(),
        conexao.execute(sa.text("SELECT count(*) FROM turnos_caixa")).scalar(),
        conexao.execute(sa.text("SELECT count(*) FROM terminais_caixa")).scalar(),
    )
    if any(contagens):
        raise RuntimeError(
            "A reversão foi bloqueada: existem configurações ou operações da Base 5 "
            "que precisam permanecer auditáveis."
        )
    if conexao.execute(
        sa.text("SELECT count(*) FROM vendas WHERE turno_id IS NOT NULL")
    ).scalar():
        raise RuntimeError(
            "A reversão foi bloqueada: existem vendas vinculadas a turnos."
        )

    op.drop_index("ix_vendas_turno_id", table_name="vendas")
    with op.batch_alter_table("vendas", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("fk_vendas_turno_id_turnos_caixa"), type_="foreignkey"
        )
        batch_op.drop_column("turno_id")
    op.drop_table("trabalhos_impressao")
    op.drop_table("regras_impressao")
    op.drop_table("impressoras")
    op.drop_table("movimentos_caixa")
    op.drop_table("turnos_caixa")
    op.drop_table("terminais_caixa")
