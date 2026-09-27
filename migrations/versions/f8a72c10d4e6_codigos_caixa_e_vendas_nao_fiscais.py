"""codigos, caixa simulado e vendas nao fiscais

Revision ID: f8a72c10d4e6
Revises: c4f36e82a901
Create Date: 2026-08-23 22:30:00.000000

"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import re
import uuid

from alembic import op
import sqlalchemy as sa


revision = "f8a72c10d4e6"
down_revision = "c4f36e82a901"
branch_labels = None
depends_on = None


_SEPARADORES = re.compile(r"[^A-Z0-9]")
_CODIGO_VALIDO = re.compile(r"^[A-Z0-9._/-]{3,80}$")


def _normalizar_legado(valor: str | None) -> tuple[str, str] | None:
    codigo = (valor or "").strip().upper()
    if not _CODIGO_VALIDO.fullmatch(codigo):
        return None
    chave = _SEPARADORES.sub("", codigo)
    if not 3 <= len(chave) <= 80:
        return None
    return codigo, chave


def upgrade():
    op.create_table(
        "codigos_produto",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("produto_id", sa.Uuid(), nullable=False),
        sa.Column("codigo", sa.String(length=80), nullable=False),
        sa.Column("chave_busca", sa.String(length=80), nullable=False),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("principal", sa.Boolean(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("criado_por_id", sa.Uuid(), nullable=True),
        sa.Column("desativado_por_id", sa.Uuid(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("desativado_em", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "tipo IN ('ean', 'interno', 'plu', 'legado', 'outro')",
            name=op.f("ck_codigos_produto_tipo_valido"),
        ),
        sa.CheckConstraint(
            "versao >= 1", name=op.f("ck_codigos_produto_versao_positiva")
        ),
        sa.CheckConstraint(
            "ativo OR NOT principal",
            name=op.f("ck_codigos_produto_principal_precisa_estar_ativo"),
        ),
        sa.ForeignKeyConstraint(
            ["criado_por_id"],
            ["usuarios.id"],
            name=op.f("fk_codigos_produto_criado_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["desativado_por_id"],
            ["usuarios.id"],
            name=op.f("fk_codigos_produto_desativado_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["empresa_id"],
            ["empresas.id"],
            name=op.f("fk_codigos_produto_empresa_id_empresas"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["produto_id"],
            ["produtos.id"],
            name=op.f("fk_codigos_produto_produto_id_produtos"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_codigos_produto")),
        sa.UniqueConstraint(
            "empresa_id", "chave_busca", name="empresa_codigo_busca"
        ),
    )
    op.create_index(
        "ix_codigos_produto_empresa_ativo",
        "codigos_produto",
        ["empresa_id", "ativo"],
        unique=False,
    )
    op.create_index(
        "ix_codigos_produto_produto_ativo",
        "codigos_produto",
        ["produto_id", "ativo"],
        unique=False,
    )
    op.create_index(
        "uq_codigos_produto_principal_ativo",
        "codigos_produto",
        ["produto_id"],
        unique=True,
        postgresql_where=sa.text("principal AND ativo"),
        sqlite_where=sa.text("principal = 1 AND ativo = 1"),
    )

    op.create_table(
        "vendas",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("numero", sa.BigInteger(), nullable=False),
        sa.Column("operador_id", sa.Uuid(), nullable=True),
        sa.Column("finalizado_por_id", sa.Uuid(), nullable=True),
        sa.Column("cancelado_por_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("total", sa.Numeric(16, 2), nullable=False),
        sa.Column("troco", sa.Numeric(16, 2), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("motivo_cancelamento", sa.String(length=500), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalizado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("descartado_em", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('aberta', 'finalizada', 'cancelada', 'descartada')",
            name=op.f("ck_vendas_status_valido"),
        ),
        sa.CheckConstraint("total >= 0", name=op.f("ck_vendas_total_nao_negativo")),
        sa.CheckConstraint("troco >= 0", name=op.f("ck_vendas_troco_nao_negativo")),
        sa.CheckConstraint("versao >= 1", name=op.f("ck_vendas_versao_positiva")),
        sa.ForeignKeyConstraint(
            ["cancelado_por_id"],
            ["usuarios.id"],
            name=op.f("fk_vendas_cancelado_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["empresa_id"],
            ["empresas.id"],
            name=op.f("fk_vendas_empresa_id_empresas"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["finalizado_por_id"],
            ["usuarios.id"],
            name=op.f("fk_vendas_finalizado_por_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["operador_id"],
            ["usuarios.id"],
            name=op.f("fk_vendas_operador_id_usuarios"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vendas")),
        sa.UniqueConstraint("empresa_id", "numero", name="empresa_numero_venda"),
    )
    op.create_index(
        "ix_vendas_empresa_criado",
        "vendas",
        ["empresa_id", "criado_em"],
        unique=False,
    )
    op.create_index(
        "ix_vendas_empresa_status",
        "vendas",
        ["empresa_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_vendas_empresa_operador_aberta",
        "vendas",
        ["empresa_id", "operador_id"],
        unique=True,
        postgresql_where=sa.text("status = 'aberta'"),
        sqlite_where=sa.text("status = 'aberta'"),
    )

    op.create_table(
        "venda_itens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("venda_id", sa.Uuid(), nullable=False),
        sa.Column("produto_id", sa.Uuid(), nullable=False),
        sa.Column("codigo_produto_id", sa.Uuid(), nullable=True),
        sa.Column("movimento_saida_id", sa.Uuid(), nullable=True),
        sa.Column("movimento_estorno_id", sa.Uuid(), nullable=True),
        sa.Column("produto_nome", sa.String(length=240), nullable=False),
        sa.Column("sku_interno", sa.String(length=40), nullable=False),
        sa.Column("codigo_lido", sa.String(length=80), nullable=False),
        sa.Column("unidade", sa.String(length=12), nullable=False),
        sa.Column("pesado", sa.Boolean(), nullable=False),
        sa.Column("preco_unitario", sa.Numeric(14, 2), nullable=False),
        sa.Column("quantidade", sa.Numeric(15, 3), nullable=False),
        sa.Column("subtotal", sa.Numeric(16, 2), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "preco_unitario > 0",
            name=op.f("ck_venda_itens_preco_unitario_positivo"),
        ),
        sa.CheckConstraint(
            "quantidade > 0", name=op.f("ck_venda_itens_quantidade_positiva")
        ),
        sa.CheckConstraint(
            "subtotal > 0", name=op.f("ck_venda_itens_subtotal_positivo")
        ),
        sa.ForeignKeyConstraint(
            ["codigo_produto_id"],
            ["codigos_produto.id"],
            name=op.f("fk_venda_itens_codigo_produto_id_codigos_produto"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["movimento_estorno_id"],
            ["movimentos_estoque.id"],
            name=op.f("fk_venda_itens_movimento_estorno_id_movimentos_estoque"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["movimento_saida_id"],
            ["movimentos_estoque.id"],
            name=op.f("fk_venda_itens_movimento_saida_id_movimentos_estoque"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["produto_id"],
            ["produtos.id"],
            name=op.f("fk_venda_itens_produto_id_produtos"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["venda_id"],
            ["vendas.id"],
            name=op.f("fk_venda_itens_venda_id_vendas"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_venda_itens")),
        sa.UniqueConstraint("movimento_estorno_id", name="venda_item_movimento_estorno"),
        sa.UniqueConstraint("movimento_saida_id", name="venda_item_movimento_saida"),
        sa.UniqueConstraint("venda_id", "produto_id", name="venda_produto"),
    )

    op.create_table(
        "venda_pagamentos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("venda_id", sa.Uuid(), nullable=False),
        sa.Column("metodo", sa.String(length=20), nullable=False),
        sa.Column("valor", sa.Numeric(16, 2), nullable=False),
        sa.Column("valor_recebido", sa.Numeric(16, 2), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "metodo IN ('dinheiro', 'pix', 'debito', 'credito')",
            name=op.f("ck_venda_pagamentos_metodo_valido"),
        ),
        sa.CheckConstraint(
            "valor > 0", name=op.f("ck_venda_pagamentos_valor_positivo")
        ),
        sa.CheckConstraint(
            "valor_recebido >= valor",
            name=op.f("ck_venda_pagamentos_valor_recebido_suficiente"),
        ),
        sa.CheckConstraint(
            "metodo = 'dinheiro' OR valor_recebido = valor",
            name=op.f("ck_venda_pagamentos_valor_recebido_apenas_dinheiro"),
        ),
        sa.ForeignKeyConstraint(
            ["venda_id"],
            ["vendas.id"],
            name=op.f("fk_venda_pagamentos_venda_id_vendas"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_venda_pagamentos")),
        sa.UniqueConstraint("venda_id", "metodo", name="venda_metodo_pagamento"),
    )

    conexao = op.get_bind()
    registros = conexao.execute(
        sa.text(
            "SELECT id, empresa_id, codigo_externo FROM produtos "
            "WHERE ativo = true AND codigo_externo IS NOT NULL"
        )
    ).mappings()
    candidatos = []
    for registro in registros:
        normalizado = _normalizar_legado(registro["codigo_externo"])
        if normalizado is not None:
            codigo, chave = normalizado
            candidatos.append(
                (
                    registro["empresa_id"],
                    uuid.UUID(str(registro["id"])),
                    codigo,
                    chave,
                )
            )
    contagens = Counter((empresa_id, chave) for empresa_id, _, _, chave in candidatos)
    agora = datetime.now(timezone.utc)
    tabela = sa.table(
        "codigos_produto",
        sa.column("id", sa.Uuid()),
        sa.column("empresa_id", sa.Integer()),
        sa.column("produto_id", sa.Uuid()),
        sa.column("codigo", sa.String()),
        sa.column("chave_busca", sa.String()),
        sa.column("tipo", sa.String()),
        sa.column("principal", sa.Boolean()),
        sa.column("ativo", sa.Boolean()),
        sa.column("versao", sa.Integer()),
        sa.column("criado_por_id", sa.Uuid()),
        sa.column("desativado_por_id", sa.Uuid()),
        sa.column("criado_em", sa.DateTime(timezone=True)),
        sa.column("atualizado_em", sa.DateTime(timezone=True)),
        sa.column("desativado_em", sa.DateTime(timezone=True)),
    )
    valores = [
        {
            "id": uuid.uuid4(),
            "empresa_id": empresa_id,
            "produto_id": produto_id,
            "codigo": codigo,
            "chave_busca": chave,
            "tipo": "legado",
            "principal": True,
            "ativo": True,
            "versao": 1,
            "criado_por_id": None,
            "desativado_por_id": None,
            "criado_em": agora,
            "atualizado_em": agora,
            "desativado_em": None,
        }
        for empresa_id, produto_id, codigo, chave in candidatos
        if contagens[(empresa_id, chave)] == 1
    ]
    for inicio in range(0, len(valores), 500):
        conexao.execute(sa.insert(tabela), valores[inicio : inicio + 500])


def downgrade():
    conexao = op.get_bind()
    if conexao.execute(sa.text("SELECT count(*) FROM vendas")).scalar():
        raise RuntimeError(
            "A reversão foi bloqueada: existem vendas que precisam permanecer auditáveis."
        )
    if conexao.execute(
        sa.text(
            "SELECT count(*) FROM codigos_produto "
            "WHERE tipo <> 'legado' OR criado_por_id IS NOT NULL"
        )
    ).scalar():
        raise RuntimeError(
            "A reversão foi bloqueada: existem códigos cadastrados manualmente."
        )

    op.drop_table("venda_pagamentos")
    op.drop_table("venda_itens")
    op.drop_index("uq_vendas_empresa_operador_aberta", table_name="vendas")
    op.drop_index("ix_vendas_empresa_status", table_name="vendas")
    op.drop_index("ix_vendas_empresa_criado", table_name="vendas")
    op.drop_table("vendas")
    op.drop_index(
        "uq_codigos_produto_principal_ativo", table_name="codigos_produto"
    )
    op.drop_index("ix_codigos_produto_produto_ativo", table_name="codigos_produto")
    op.drop_index("ix_codigos_produto_empresa_ativo", table_name="codigos_produto")
    op.drop_table("codigos_produto")
