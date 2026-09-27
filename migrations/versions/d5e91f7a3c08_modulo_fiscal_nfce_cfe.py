"""modulo fiscal: nfc-e, cf-e, numeracao e acervo de xml

Revision ID: d5e91f7a3c08
Revises: a31d7e5c9b42
Create Date: 2026-09-10 15:00:00.000000

Cria as tabelas do módulo fiscal. Todas são novas: nenhuma tabela existente é
alterada, então a migração é aditiva e reversível sem perda de dados de
estoque, caixa ou usuários.

O modelo espelha as entidades do sistema legado (TPA Centralizador:
DocumentoFiscal, DocumentoItem, DocumentoPagamento, DocumentoXml,
Inutilizacao, Estabelecimento), para que o acervo antigo possa ser importado.
"""

from alembic import op
import sqlalchemy as sa


revision = "d5e91f7a3c08"
down_revision = "a31d7e5c9b42"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "estabelecimentos_fiscais",
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("cnpj", sa.String(length=14), nullable=False),
        sa.Column("razao_social", sa.String(length=120), nullable=False),
        sa.Column("nome_fantasia", sa.String(length=120), nullable=True),
        sa.Column("inscricao_estadual", sa.String(length=20), nullable=False),
        sa.Column("inscricao_municipal", sa.String(length=20), nullable=True),
        sa.Column("crt", sa.Integer(), nullable=False),
        sa.Column("logradouro", sa.String(length=120), nullable=False),
        sa.Column("numero", sa.String(length=20), nullable=False),
        sa.Column("complemento", sa.String(length=120), nullable=True),
        sa.Column("bairro", sa.String(length=120), nullable=False),
        sa.Column("codigo_municipio", sa.Integer(), nullable=False),
        sa.Column("municipio", sa.String(length=120), nullable=False),
        sa.Column("uf", sa.String(length=2), nullable=False),
        sa.Column("cep", sa.String(length=8), nullable=False),
        sa.Column("telefone", sa.String(length=14), nullable=True),
        sa.Column("modelo", sa.Integer(), nullable=False),
        sa.Column("serie", sa.Integer(), nullable=False),
        sa.Column("proximo_numero", sa.BigInteger(), nullable=False),
        sa.Column("ambiente", sa.Integer(), nullable=False),
        sa.Column("natureza_operacao", sa.String(length=60), nullable=False),
        sa.Column("informacoes_complementares", sa.String(length=500), nullable=True),
        sa.Column("provedor", sa.String(length=40), nullable=False),
        sa.Column("parametros_provedor", sa.JSON(), nullable=False),
        sa.Column("csc_id", sa.String(length=6), nullable=True),
        sa.Column("url_consulta_qrcode", sa.String(length=300), nullable=True),
        sa.Column("url_consulta_chave", sa.String(length=300), nullable=True),
        sa.Column("responsavel_cnpj", sa.String(length=14), nullable=True),
        sa.Column("responsavel_contato", sa.String(length=60), nullable=True),
        sa.Column("responsavel_email", sa.String(length=60), nullable=True),
        sa.Column("responsavel_telefone", sa.String(length=14), nullable=True),
        sa.Column("enderecos_conferidos", sa.Boolean(), nullable=False),
        sa.Column("emissao_ativa", sa.Boolean(), nullable=False),
        sa.Column("permitir_contingencia", sa.Boolean(), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("atualizado_por_id", sa.Uuid(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("modelo IN (55, 65, 59)", name=op.f("ck_estabelecimentos_fiscais_modelo_valido")),
        sa.CheckConstraint("ambiente IN (1, 2)", name=op.f("ck_estabelecimentos_fiscais_ambiente_valido")),
        sa.CheckConstraint("crt IN (1, 2, 3, 4)", name=op.f("ck_estabelecimentos_fiscais_crt_valido")),
        sa.CheckConstraint("serie BETWEEN 0 AND 999", name=op.f("ck_estabelecimentos_fiscais_serie_valida")),
        sa.CheckConstraint(
            "proximo_numero BETWEEN 1 AND 999999999",
            name=op.f("ck_estabelecimentos_fiscais_numero_valido"),
        ),
        sa.CheckConstraint(
            "length(cnpj) = 14", name=op.f("ck_estabelecimentos_fiscais_cnpj_com_quatorze_digitos")
        ),
        sa.CheckConstraint("versao >= 1", name=op.f("ck_estabelecimentos_fiscais_versao_positiva")),
        sa.ForeignKeyConstraint(
            ["empresa_id"], ["empresas.id"],
            name=op.f("fk_estabelecimentos_fiscais_empresa_id_empresas"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["atualizado_por_id"], ["usuarios.id"],
            name=op.f("fk_estabelecimentos_fiscais_atualizado_por_id_usuarios"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("empresa_id", name=op.f("pk_estabelecimentos_fiscais")),
        sa.UniqueConstraint("cnpj", "modelo", "serie", name="cnpj_modelo_serie"),
    )
    op.create_index(
        op.f("ix_estabelecimentos_fiscais_cnpj"), "estabelecimentos_fiscais", ["cnpj"], unique=False
    )

    op.create_table(
        "credenciais_fiscais",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=40), nullable=False),
        sa.Column("valor_cifrado", sa.String(length=2000), nullable=False),
        sa.Column("dica", sa.String(length=60), nullable=True),
        sa.Column("atualizado_por_id", sa.Uuid(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "nome IN ('csc_token', 'certificado_senha', 'gateway_token')",
            name=op.f("ck_credenciais_fiscais_nome_valido"),
        ),
        sa.ForeignKeyConstraint(
            ["empresa_id"], ["empresas.id"],
            name=op.f("fk_credenciais_fiscais_empresa_id_empresas"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["atualizado_por_id"], ["usuarios.id"],
            name=op.f("fk_credenciais_fiscais_atualizado_por_id_usuarios"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credenciais_fiscais")),
        sa.UniqueConstraint("empresa_id", "nome", name="empresa_credencial"),
    )

    op.create_table(
        "produtos_fiscais",
        sa.Column("produto_id", sa.Uuid(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("ncm", sa.String(length=8), nullable=False),
        sa.Column("cest", sa.String(length=7), nullable=True),
        sa.Column("cfop", sa.String(length=4), nullable=False),
        sa.Column("origem", sa.Integer(), nullable=False),
        sa.Column("gtin", sa.String(length=14), nullable=True),
        sa.Column("unidade_tributavel", sa.String(length=6), nullable=True),
        sa.Column("icms_cst", sa.String(length=3), nullable=True),
        sa.Column("icms_csosn", sa.String(length=3), nullable=True),
        sa.Column("icms_aliquota", sa.Numeric(7, 4), nullable=False),
        sa.Column("icms_modalidade_bc", sa.Integer(), nullable=False),
        sa.Column("icms_reducao_bc", sa.Numeric(7, 4), nullable=False),
        sa.Column("pis_cst", sa.String(length=2), nullable=False),
        sa.Column("pis_aliquota", sa.Numeric(7, 4), nullable=False),
        sa.Column("cofins_cst", sa.String(length=2), nullable=False),
        sa.Column("cofins_aliquota", sa.Numeric(7, 4), nullable=False),
        sa.Column("revisao_pendente", sa.Boolean(), nullable=False),
        sa.Column("observacao", sa.String(length=500), nullable=True),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("atualizado_por_id", sa.Uuid(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(ncm) = 8", name=op.f("ck_produtos_fiscais_ncm_com_oito_digitos")),
        sa.CheckConstraint("length(cfop) = 4", name=op.f("ck_produtos_fiscais_cfop_com_quatro_digitos")),
        sa.CheckConstraint("origem BETWEEN 0 AND 8", name=op.f("ck_produtos_fiscais_origem_valida")),
        sa.CheckConstraint("versao >= 1", name=op.f("ck_produtos_fiscais_versao_positiva")),
        sa.ForeignKeyConstraint(
            ["produto_id"], ["produtos.id"],
            name=op.f("fk_produtos_fiscais_produto_id_produtos"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["empresa_id"], ["empresas.id"],
            name=op.f("fk_produtos_fiscais_empresa_id_empresas"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["atualizado_por_id"], ["usuarios.id"],
            name=op.f("fk_produtos_fiscais_atualizado_por_id_usuarios"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("produto_id", name=op.f("pk_produtos_fiscais")),
    )
    op.create_index(
        op.f("ix_produtos_fiscais_empresa_id"), "produtos_fiscais", ["empresa_id"], unique=False
    )

    op.create_table(
        "documentos_fiscais",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("venda_id", sa.Uuid(), nullable=True),
        sa.Column("chave", sa.String(length=44), nullable=False),
        sa.Column("codigo_numerico", sa.Integer(), nullable=False),
        sa.Column("modelo", sa.Integer(), nullable=False),
        sa.Column("serie", sa.Integer(), nullable=False),
        sa.Column("numero", sa.BigInteger(), nullable=False),
        sa.Column("ambiente", sa.Integer(), nullable=False),
        sa.Column("tipo_emissao", sa.Integer(), nullable=False),
        sa.Column("cnpj_emitente", sa.String(length=14), nullable=False),
        sa.Column("documento_destinatario", sa.String(length=14), nullable=True),
        sa.Column("nome_destinatario", sa.String(length=60), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("codigo_status", sa.String(length=4), nullable=True),
        sa.Column("mensagem_status", sa.String(length=500), nullable=True),
        sa.Column("protocolo_autorizacao", sa.String(length=20), nullable=True),
        sa.Column("protocolo_cancelamento", sa.String(length=20), nullable=True),
        sa.Column("motivo_cancelamento", sa.String(length=500), nullable=True),
        sa.Column("total_produtos", sa.Numeric(16, 2), nullable=False),
        sa.Column("total_desconto", sa.Numeric(16, 2), nullable=False),
        sa.Column("total_documento", sa.Numeric(16, 2), nullable=False),
        sa.Column("total_base_icms", sa.Numeric(16, 2), nullable=False),
        sa.Column("total_icms", sa.Numeric(16, 2), nullable=False),
        sa.Column("total_pis", sa.Numeric(16, 2), nullable=False),
        sa.Column("total_cofins", sa.Numeric(16, 2), nullable=False),
        sa.Column("troco", sa.Numeric(16, 2), nullable=False),
        sa.Column("qrcode_conteudo", sa.String(length=1000), nullable=True),
        sa.Column("digest_value", sa.String(length=60), nullable=True),
        sa.Column("provedor", sa.String(length=40), nullable=False),
        sa.Column("sem_valor_fiscal", sa.Boolean(), nullable=False),
        sa.Column("tentativas", sa.Integer(), nullable=False),
        sa.Column("data_emissao", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_autorizacao", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_cancelamento", sa.DateTime(timezone=True), nullable=True),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("emitido_por_id", sa.Uuid(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pendente', 'processando', 'autorizado', 'rejeitado', "
            "'denegado', 'cancelado', 'contingencia', 'falhou')",
            name=op.f("ck_documentos_fiscais_status_valido"),
        ),
        sa.CheckConstraint("modelo IN (55, 65, 59)", name=op.f("ck_documentos_fiscais_modelo_valido")),
        sa.CheckConstraint("ambiente IN (1, 2)", name=op.f("ck_documentos_fiscais_ambiente_valido")),
        sa.CheckConstraint("tentativas >= 0", name=op.f("ck_documentos_fiscais_tentativas_nao_negativas")),
        sa.CheckConstraint("versao >= 1", name=op.f("ck_documentos_fiscais_versao_positiva")),
        sa.ForeignKeyConstraint(
            ["empresa_id"], ["empresas.id"],
            name=op.f("fk_documentos_fiscais_empresa_id_empresas"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["venda_id"], ["vendas.id"],
            name=op.f("fk_documentos_fiscais_venda_id_vendas"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["emitido_por_id"], ["usuarios.id"],
            name=op.f("fk_documentos_fiscais_emitido_por_id_usuarios"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documentos_fiscais")),
        sa.UniqueConstraint("chave", name="chave_unica"),
        sa.UniqueConstraint("empresa_id", "modelo", "serie", "numero", name="numeracao_unica"),
        sa.UniqueConstraint("venda_id", name="documento_por_venda"),
    )
    op.create_index(op.f("ix_documentos_fiscais_chave"), "documentos_fiscais", ["chave"], unique=False)
    op.create_index(
        op.f("ix_documentos_fiscais_cnpj_emitente"), "documentos_fiscais", ["cnpj_emitente"], unique=False
    )
    op.create_index(
        "ix_documentos_fiscais_empresa_status", "documentos_fiscais", ["empresa_id", "status"], unique=False
    )
    op.create_index(
        "ix_documentos_fiscais_empresa_emissao",
        "documentos_fiscais", ["empresa_id", "data_emissao"], unique=False,
    )
    op.create_index(
        "ix_documentos_fiscais_serie_numero",
        "documentos_fiscais", ["empresa_id", "serie", "numero"], unique=False,
    )

    op.create_table(
        "documentos_fiscais_itens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("documento_id", sa.Uuid(), nullable=False),
        sa.Column("produto_id", sa.Uuid(), nullable=True),
        sa.Column("numero_item", sa.Integer(), nullable=False),
        sa.Column("codigo_produto", sa.String(length=60), nullable=False),
        sa.Column("codigo_ean", sa.String(length=14), nullable=True),
        sa.Column("descricao", sa.String(length=120), nullable=False),
        sa.Column("ncm", sa.String(length=8), nullable=False),
        sa.Column("cest", sa.String(length=7), nullable=True),
        sa.Column("cfop", sa.String(length=4), nullable=False),
        sa.Column("unidade", sa.String(length=6), nullable=False),
        sa.Column("quantidade", sa.Numeric(15, 4), nullable=False),
        sa.Column("valor_unitario", sa.Numeric(21, 10), nullable=False),
        sa.Column("valor_total", sa.Numeric(16, 2), nullable=False),
        sa.Column("valor_desconto", sa.Numeric(16, 2), nullable=False),
        sa.Column("icms_origem", sa.Integer(), nullable=False),
        sa.Column("icms_cst", sa.String(length=3), nullable=True),
        sa.Column("icms_csosn", sa.String(length=3), nullable=True),
        sa.Column("icms_base_calculo", sa.Numeric(16, 2), nullable=False),
        sa.Column("icms_aliquota", sa.Numeric(7, 4), nullable=False),
        sa.Column("icms_valor", sa.Numeric(16, 2), nullable=False),
        sa.Column("pis_cst", sa.String(length=2), nullable=False),
        sa.Column("pis_valor", sa.Numeric(16, 2), nullable=False),
        sa.Column("cofins_cst", sa.String(length=2), nullable=False),
        sa.Column("cofins_valor", sa.Numeric(16, 2), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantidade > 0", name=op.f("ck_documentos_fiscais_itens_quantidade_positiva")),
        sa.CheckConstraint(
            "numero_item > 0", name=op.f("ck_documentos_fiscais_itens_numero_item_positivo")
        ),
        sa.ForeignKeyConstraint(
            ["documento_id"], ["documentos_fiscais.id"],
            name=op.f("fk_documentos_fiscais_itens_documento_id_documentos_fiscais"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["produto_id"], ["produtos.id"],
            name=op.f("fk_documentos_fiscais_itens_produto_id_produtos"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documentos_fiscais_itens")),
        sa.UniqueConstraint("documento_id", "numero_item", name="documento_item_numero"),
    )

    op.create_table(
        "documentos_fiscais_pagamentos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("documento_id", sa.Uuid(), nullable=False),
        sa.Column("forma", sa.String(length=2), nullable=False),
        sa.Column("valor", sa.Numeric(16, 2), nullable=False),
        sa.Column("cnpj_credenciadora", sa.String(length=14), nullable=True),
        sa.Column("bandeira", sa.String(length=2), nullable=True),
        sa.Column("numero_autorizacao", sa.String(length=20), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("valor > 0", name=op.f("ck_documentos_fiscais_pagamentos_valor_positivo")),
        sa.ForeignKeyConstraint(
            ["documento_id"], ["documentos_fiscais.id"],
            name=op.f("fk_documentos_fiscais_pagamentos_documento_id_documentos_fiscais"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documentos_fiscais_pagamentos")),
    )

    op.create_table(
        "documentos_fiscais_xml",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("documento_id", sa.Uuid(), nullable=False),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("arquivo_xml", sa.Text(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "tipo IN ('enviado', 'retorno', 'autorizado', 'cancelamento', 'inutilizacao')",
            name=op.f("ck_documentos_fiscais_xml_tipo_valido"),
        ),
        sa.ForeignKeyConstraint(
            ["documento_id"], ["documentos_fiscais.id"],
            name=op.f("fk_documentos_fiscais_xml_documento_id_documentos_fiscais"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documentos_fiscais_xml")),
        sa.UniqueConstraint("documento_id", "tipo", name="documento_tipo_xml"),
    )

    op.create_table(
        "documentos_fiscais_eventos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("documento_id", sa.Uuid(), nullable=False),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("provedor", sa.String(length=40), nullable=False),
        sa.Column("codigo_status", sa.String(length=4), nullable=True),
        sa.Column("mensagem", sa.String(length=1000), nullable=True),
        sa.Column("sucesso", sa.Boolean(), nullable=False),
        sa.Column("usuario_id", sa.Uuid(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "tipo IN ('emissao', 'reenvio', 'cancelamento', 'consulta', 'contingencia', 'erro')",
            name=op.f("ck_documentos_fiscais_eventos_tipo_valido"),
        ),
        sa.ForeignKeyConstraint(
            ["documento_id"], ["documentos_fiscais.id"],
            name=op.f("fk_documentos_fiscais_eventos_documento_id_documentos_fiscais"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuarios.id"],
            name=op.f("fk_documentos_fiscais_eventos_usuario_id_usuarios"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documentos_fiscais_eventos")),
    )
    op.create_index(
        "ix_documentos_fiscais_eventos_documento",
        "documentos_fiscais_eventos", ["documento_id", "criado_em"], unique=False,
    )

    op.create_table(
        "inutilizacoes_fiscais",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("cnpj", sa.String(length=14), nullable=False),
        sa.Column("modelo", sa.Integer(), nullable=False),
        sa.Column("serie", sa.Integer(), nullable=False),
        sa.Column("numero", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("justificativa", sa.String(length=255), nullable=True),
        sa.Column("protocolo", sa.String(length=20), nullable=True),
        sa.Column("codigo_status", sa.String(length=4), nullable=True),
        sa.Column("mensagem", sa.String(length=500), nullable=True),
        sa.Column("origem", sa.String(length=20), nullable=False),
        sa.Column("arquivo_xml", sa.Text(), nullable=True),
        sa.Column("detectado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolvido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolvido_por_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pendente', 'autorizada', 'rejeitada', 'descartada')",
            name=op.f("ck_inutilizacoes_fiscais_status_valido"),
        ),
        sa.CheckConstraint("numero > 0", name=op.f("ck_inutilizacoes_fiscais_numero_positivo")),
        sa.ForeignKeyConstraint(
            ["empresa_id"], ["empresas.id"],
            name=op.f("fk_inutilizacoes_fiscais_empresa_id_empresas"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolvido_por_id"], ["usuarios.id"],
            name=op.f("fk_inutilizacoes_fiscais_resolvido_por_id_usuarios"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inutilizacoes_fiscais")),
        sa.UniqueConstraint("cnpj", "modelo", "serie", "numero", name="inutilizacao_unica"),
    )
    op.create_index(
        op.f("ix_inutilizacoes_fiscais_cnpj"), "inutilizacoes_fiscais", ["cnpj"], unique=False
    )
    op.create_index(
        "ix_inutilizacoes_fiscais_empresa_status",
        "inutilizacoes_fiscais", ["empresa_id", "status"], unique=False,
    )


def downgrade():
    # O XML autorizado precisa ser guardado por cinco anos, e a numeração
    # inutilizada é prestação de contas à SEFAZ. Reverter esta migração com
    # dado gravado destruiria acervo fiscal, então a reversão é bloqueada —
    # mesmo critério das migrações anteriores deste projeto.
    conexao = op.get_bind()
    contagens = (
        conexao.execute(sa.text("SELECT count(*) FROM documentos_fiscais")).scalar(),
        conexao.execute(sa.text("SELECT count(*) FROM inutilizacoes_fiscais")).scalar(),
        conexao.execute(sa.text("SELECT count(*) FROM credenciais_fiscais")).scalar(),
        conexao.execute(sa.text("SELECT count(*) FROM estabelecimentos_fiscais")).scalar(),
    )
    if any(contagens):
        raise RuntimeError(
            "A reversão foi bloqueada: existem documentos fiscais, inutilizações "
            "ou configuração de emissão que precisam permanecer guardados."
        )

    op.drop_index("ix_inutilizacoes_fiscais_empresa_status", table_name="inutilizacoes_fiscais")
    op.drop_index(op.f("ix_inutilizacoes_fiscais_cnpj"), table_name="inutilizacoes_fiscais")
    op.drop_table("inutilizacoes_fiscais")

    op.drop_index("ix_documentos_fiscais_eventos_documento", table_name="documentos_fiscais_eventos")
    op.drop_table("documentos_fiscais_eventos")
    op.drop_table("documentos_fiscais_xml")
    op.drop_table("documentos_fiscais_pagamentos")
    op.drop_table("documentos_fiscais_itens")

    op.drop_index("ix_documentos_fiscais_serie_numero", table_name="documentos_fiscais")
    op.drop_index("ix_documentos_fiscais_empresa_emissao", table_name="documentos_fiscais")
    op.drop_index("ix_documentos_fiscais_empresa_status", table_name="documentos_fiscais")
    op.drop_index(op.f("ix_documentos_fiscais_cnpj_emitente"), table_name="documentos_fiscais")
    op.drop_index(op.f("ix_documentos_fiscais_chave"), table_name="documentos_fiscais")
    op.drop_table("documentos_fiscais")

    op.drop_index(op.f("ix_produtos_fiscais_empresa_id"), table_name="produtos_fiscais")
    op.drop_table("produtos_fiscais")

    op.drop_table("credenciais_fiscais")

    op.drop_index(op.f("ix_estabelecimentos_fiscais_cnpj"), table_name="estabelecimentos_fiscais")
    op.drop_table("estabelecimentos_fiscais")
