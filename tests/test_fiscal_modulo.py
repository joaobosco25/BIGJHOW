"""Testes do módulo fiscal integrados ao banco e ao caixa.

Usam o provedor de ensaio, que percorre o fluxo inteiro sem tocar na SEFAZ.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from bigjhow.extensions import db
from bigjhow.models import (
    ConfiguracaoDocumentoFiscal,
    CredencialFiscal,
    DocumentoFiscal,
    EstabelecimentoFiscal,
    InutilizacaoFiscal,
    ProdutoFiscal,
    SaldoEstoque,
    Usuario,
)
from bigjhow.services.caixa import adicionar_item, finalizar_venda, iniciar_venda
from bigjhow.services.fiscal import segredos
from bigjhow.services.fiscal.cadastro import (
    CadastroFiscalErro,
    conferir_prontidao,
    guardar_credencial,
    ler_credencial,
    salvar_classificacao,
    salvar_estabelecimento,
)
from bigjhow.services.fiscal.emissao import emitir_com_seguranca, emitir_venda
from bigjhow.services.fiscal.importador import ResultadoImportacao, importar_conteudo
from bigjhow.services.fiscal.provedores import criar_provedor
from bigjhow.services.fiscal.provedores.homologacao_interna import HomologacaoInterna
from bigjhow.services.fiscal.sequencia import (
    agrupar_faixas,
    detectar_lacunas,
    gerar_codigo_numerico,
    registrar_lacunas,
    reservar_numero,
)


def _operador(app):
    return db.session.scalar(
        db.select(Usuario).where(Usuario.usuario == "operador.carnes")
    )


def _preparar_estoque(app):
    produto_id = app.config["TEST_PRODUCT_ID"]
    saldo = db.session.get(SaldoEstoque, produto_id)
    saldo.quantidade = Decimal("100.000")
    saldo.aguardando_inventario = False
    db.session.commit()
    return produto_id


def _venda_finalizada(app):
    _preparar_estoque(app)
    operador = _operador(app)
    venda = iniciar_venda(empresa_id=1, usuario_id=operador.id)
    venda = adicionar_item(
        empresa_id=1,
        venda_id=venda.id,
        versao=venda.versao,
        codigo_digitado="001",
        quantidade_digitada="2",
        usuario_id=operador.id,
    )
    venda, _ = finalizar_venda(
        empresa_id=1,
        venda_id=venda.id,
        versao=venda.versao,
        pagamentos_digitados={
            "dinheiro": (str(venda.total), str(venda.total)),
            "pix": (None, None),
            "debito": (None, None),
            "credito": (None, None),
        },
        usuario_id=operador.id,
    )
    return venda, operador


# --------------------------------------------------------------------------
# Credenciais
# --------------------------------------------------------------------------


def test_credencial_e_guardada_cifrada_e_nao_em_claro(app_fiscal):
    with app_fiscal.app_context():
        dono = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        guardar_credencial(
            empresa_id=1, nome="csc_token", valor="CSC-SECRETO-123456", usuario_id=dono.id
        )
        registro = db.session.scalar(db.select(CredencialFiscal))
        assert "CSC-SECRETO-123456" not in registro.valor_cifrado
        assert registro.valor_cifrado.startswith("v1.")
        assert registro.dica.endswith("3456")
        assert ler_credencial(1, "csc_token") == "CSC-SECRETO-123456"


def test_credencial_nao_abre_com_outra_chave(app_fiscal):
    with app_fiscal.app_context():
        dono = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        guardar_credencial(
            empresa_id=1, nome="csc_token", valor="CSC-SECRETO", usuario_id=dono.id
        )
        app_fiscal.config["FISCAL_SECRET_KEY"] = "outra-chave-" + "y" * 40
        with pytest.raises(segredos.SegredoErro):
            ler_credencial(1, "csc_token")


# --------------------------------------------------------------------------
# Numeração
# --------------------------------------------------------------------------


def test_numeracao_avanca_e_nao_repete(app_fiscal):
    with app_fiscal.app_context():
        primeiro = reservar_numero(1)
        db.session.commit()
        segundo = reservar_numero(1)
        db.session.commit()
        assert segundo.numero == primeiro.numero + 1
        assert primeiro.serie == 1 and primeiro.modelo == 65


def test_sequencias_de_homologacao_e_producao_sao_independentes(app_fiscal):
    with app_fiscal.app_context():
        estabelecimento = db.session.get(EstabelecimentoFiscal, 1)

        homologacao_1 = reservar_numero(1)
        db.session.commit()
        assert homologacao_1.numero == 1

        db.session.add(
            ConfiguracaoDocumentoFiscal(
                empresa_id=1, modelo=65, ambiente=1, serie=1,
                proximo_numero=1, natureza_operacao="VENDA AO CONSUMIDOR",
                provedor="homologacao_interna", parametros_provedor={},
            )
        )
        estabelecimento.ambiente = 1
        estabelecimento.proximo_numero = 1
        db.session.commit()

        producao_1 = reservar_numero(1)
        db.session.commit()
        assert producao_1.numero == 1

        estabelecimento.ambiente = 2
        db.session.commit()
        homologacao_2 = reservar_numero(1)
        db.session.commit()
        assert homologacao_2.numero == 2

        configs = db.session.scalars(
            db.select(ConfiguracaoDocumentoFiscal).where(
                ConfiguracaoDocumentoFiscal.empresa_id == 1,
                ConfiguracaoDocumentoFiscal.modelo == 65,
                ConfiguracaoDocumentoFiscal.serie == 1,
            )
        ).all()
        por_ambiente = {cfg.ambiente: cfg.proximo_numero for cfg in configs}
        assert por_ambiente == {1: 2, 2: 3}


def test_mesmo_numero_pode_existir_em_ambientes_distintos(app_fiscal):
    with app_fiscal.app_context():
        base = dict(
            empresa_id=1, codigo_numerico=12345678, modelo=65, serie=1, numero=77,
            tipo_emissao=1, cnpj_emitente="11222333000181", status="autorizado",
            protocolo_autorizacao="135260000000001", provedor="homologacao_interna",
            data_emissao=db.func.now(),
        )
        db.session.add(DocumentoFiscal(chave="1" * 44, ambiente=1, **base))
        db.session.add(DocumentoFiscal(chave="2" * 44, ambiente=2, **base))
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(DocumentoFiscal.id))) == 2


def test_codigo_numerico_nunca_e_igual_ao_numero_da_nota():
    for numero in (1, 42, 999999):
        assert gerar_codigo_numerico(numero) != numero


def test_agrupamento_de_faixas():
    assert agrupar_faixas([3, 1, 2, 7, 8, 20]) == [(1, 3), (7, 8), (20, 20)]
    assert agrupar_faixas([]) == []


def test_lacunas_sao_detectadas_entre_notas_autorizadas(app_fiscal):
    with app_fiscal.app_context():
        for numero in (1, 2, 5):
            db.session.add(
                DocumentoFiscal(
                    empresa_id=1,
                    chave=f"{numero:044d}",
                    codigo_numerico=1,
                    modelo=65,
                    serie=1,
                    numero=numero,
                    ambiente=2,
                    tipo_emissao=1,
                    cnpj_emitente="11222333000181",
                    status="autorizado",
                    protocolo_autorizacao=f"P{numero}",
                    provedor="homologacao_interna",
                    data_emissao=db.func.now(),
                )
            )
        db.session.commit()

        lacunas = [item["numero"] for item in detectar_lacunas(1)]
        assert lacunas == [3, 4]

        registrar_lacunas(1)
        db.session.commit()
        pendentes = db.session.scalars(
            db.select(InutilizacaoFiscal.numero).where(
                InutilizacaoFiscal.status == "pendente"
            )
        ).all()
        assert sorted(pendentes) == [3, 4]

        # Rodar de novo não duplica pendência.
        registrar_lacunas(1)
        db.session.commit()
        assert db.session.scalar(
            db.select(db.func.count(InutilizacaoFiscal.id))
        ) == 2


# --------------------------------------------------------------------------
# Prontidão
# --------------------------------------------------------------------------


def test_prontidao_aponta_o_que_falta_para_valor_fiscal(app_fiscal):
    with app_fiscal.app_context():
        prontidao = conferir_prontidao(1)
        assert prontidao["configurado"] is True
        # O provedor de ensaio nunca produz documento com valor fiscal.
        assert prontidao["com_valor_fiscal"] is False
        # "sem valor fiscal" é aviso, não impedimento: o ensaio precisa poder
        # rodar antes de existirem certificado e CSC.
        assert any("ensaio" in item.lower() for item in prontidao["avisos"])
        assert prontidao["pendencias"] == []
        assert prontidao["pode_emitir"] is True


def test_rota_de_ensaio_pode_ser_ligada_sem_csc_nem_certificado(app_fiscal):
    """Sem isto o modo de ensaio seria inútil: ninguém consegue testar o fluxo
    antes de a SEFAZ liberar as credenciais."""
    with app_fiscal.app_context():
        dono = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        estabelecimento = db.session.get(EstabelecimentoFiscal, 1)
        registro = salvar_estabelecimento(
            empresa_id=1,
            dados={
                "cnpj": estabelecimento.cnpj,
                "razao_social": estabelecimento.razao_social,
                "inscricao_estadual": estabelecimento.inscricao_estadual,
                "crt": 1,
                "logradouro": estabelecimento.logradouro,
                "numero": "1520",
                "bairro": estabelecimento.bairro,
                "codigo_municipio": estabelecimento.codigo_municipio,
                "municipio": estabelecimento.municipio,
                "uf": estabelecimento.uf,
                "cep": estabelecimento.cep,
                "modelo": 65,
                "serie": 1,
                "proximo_numero": estabelecimento.proximo_numero,
                "ambiente": 2,
                "provedor": "homologacao_interna",
                "natureza_operacao": "VENDA AO CONSUMIDOR",
                # sem idCSC, sem CSC, sem URL de QR Code, sem responsável técnico
                "emissao_ativa": True,
            },
            usuario_id=dono.id,
        )
        assert registro.emissao_ativa is True


def test_rota_com_valor_fiscal_exige_csc_e_endereco_conferido(app_fiscal):
    """A régua frouxa do ensaio não pode valer para a emissão de verdade."""
    with app_fiscal.app_context():
        dono = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        estabelecimento = db.session.get(EstabelecimentoFiscal, 1)
        with pytest.raises(CadastroFiscalErro) as erro:
            salvar_estabelecimento(
                empresa_id=1,
                dados={
                    "cnpj": estabelecimento.cnpj,
                    "razao_social": estabelecimento.razao_social,
                    "inscricao_estadual": estabelecimento.inscricao_estadual,
                    "crt": 1,
                    "logradouro": estabelecimento.logradouro,
                    "numero": "1520",
                    "bairro": estabelecimento.bairro,
                    "codigo_municipio": estabelecimento.codigo_municipio,
                    "municipio": estabelecimento.municipio,
                    "uf": estabelecimento.uf,
                    "cep": estabelecimento.cep,
                    "modelo": 65,
                    "serie": 1,
                    "proximo_numero": estabelecimento.proximo_numero,
                    "ambiente": 2,
                    "provedor": "sefaz_direto",
                    "natureza_operacao": "VENDA AO CONSUMIDOR",
                    "emissao_ativa": True,
                },
                usuario_id=dono.id,
            )
        assert "pendências" in str(erro.value)


def test_provedor_de_ensaio_recusa_producao():
    provedor = criar_provedor("homologacao_interna", {})
    assert isinstance(provedor, HomologacaoInterna)
    with pytest.raises(Exception):
        provedor.inutilizar(
            cnpj="11222333000181", serie=1, numero_inicial=1, numero_final=1,
            justificativa="justificativa suficientemente longa", ambiente=1,
            modelo=65, codigo_uf=35, ano=2026,
        )


# --------------------------------------------------------------------------
# Classificação fiscal
# --------------------------------------------------------------------------


def test_classificacao_exige_ncm_de_oito_digitos(app_fiscal):
    with app_fiscal.app_context():
        dono = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        produto_id = app_fiscal.config["TEST_PRODUCT_ID"]
        with pytest.raises(CadastroFiscalErro):
            salvar_classificacao(
                empresa_id=1,
                produto_id=produto_id,
                dados={"ncm": "123", "cfop": "5102", "icms_csosn": "102"},
                usuario_id=dono.id,
            )


def test_simples_nacional_exige_csosn(app_fiscal):
    with app_fiscal.app_context():
        dono = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        produto_id = app_fiscal.config["TEST_PRODUCT_ID"]
        with pytest.raises(CadastroFiscalErro):
            salvar_classificacao(
                empresa_id=1,
                produto_id=produto_id,
                dados={"ncm": "02013000", "cfop": "5102", "icms_cst": "00"},
                usuario_id=dono.id,
            )


# --------------------------------------------------------------------------
# Emissão a partir da venda
# --------------------------------------------------------------------------


def test_venda_finalizada_gera_documento_com_itens_e_pagamentos(app_fiscal):
    with app_fiscal.app_context():
        venda, operador = _venda_finalizada(app_fiscal)
        documento = emitir_venda(empresa_id=1, venda_id=venda.id, usuario_id=operador.id)
        assert documento is not None
        assert documento.status == "autorizado"
        assert documento.sem_valor_fiscal is True, "ensaio nunca vale como fiscal"
        assert documento.vale_como_documento_fiscal is False
        assert len(documento.chave) == 44
        assert len(documento.itens) == 1
        assert len(documento.pagamentos) == 1
        assert documento.pagamentos[0].forma == "01"
        assert documento.total_documento == venda.total


def test_emitir_duas_vezes_nao_duplica_documento(app_fiscal):
    with app_fiscal.app_context():
        venda, operador = _venda_finalizada(app_fiscal)
        primeiro = emitir_venda(empresa_id=1, venda_id=venda.id, usuario_id=operador.id)
        segundo = emitir_venda(empresa_id=1, venda_id=venda.id, usuario_id=operador.id)
        assert primeiro.id == segundo.id
        assert db.session.scalar(db.select(db.func.count(DocumentoFiscal.id))) == 1


def test_emissao_desligada_deixa_a_venda_valida_sem_documento(app_fiscal):
    with app_fiscal.app_context():
        estabelecimento = db.session.get(EstabelecimentoFiscal, 1)
        estabelecimento.emissao_ativa = False
        db.session.commit()
        venda, operador = _venda_finalizada(app_fiscal)
        documento, aviso = emitir_com_seguranca(
            empresa_id=1, venda_id=venda.id, usuario_id=operador.id
        )
        assert documento is None and aviso is None
        assert venda.status == "finalizada"


def test_produto_sem_classificacao_bloqueia_emissao_mas_nao_a_venda(app_fiscal):
    with app_fiscal.app_context():
        produto_id = app_fiscal.config["TEST_PRODUCT_ID"]
        db.session.delete(db.session.get(ProdutoFiscal, produto_id))
        db.session.commit()

        venda, operador = _venda_finalizada(app_fiscal)
        documento, aviso = emitir_com_seguranca(
            empresa_id=1, venda_id=venda.id, usuario_id=operador.id
        )
        assert documento is None
        assert aviso is not None and "classificação fiscal" in aviso
        assert venda.status == "finalizada", "a venda continua válida"
        assert db.session.scalar(db.select(db.func.count(DocumentoFiscal.id))) == 0


def test_numero_consumido_fica_registrado_mesmo_sem_autorizacao(app_fiscal):
    """Se o número é reservado, ele precisa deixar rastro — senão vira lacuna
    invisível e pendência fiscal silenciosa."""
    with app_fiscal.app_context():
        antes = db.session.get(EstabelecimentoFiscal, 1).proximo_numero
        venda, operador = _venda_finalizada(app_fiscal)
        documento = emitir_venda(empresa_id=1, venda_id=venda.id, usuario_id=operador.id)
        depois = db.session.get(EstabelecimentoFiscal, 1).proximo_numero
        assert depois == antes + 1
        assert documento.numero == antes


# --------------------------------------------------------------------------
# Importação do acervo antigo
# --------------------------------------------------------------------------


XML_NFEPROC = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe versao="4.00" Id="NFe{chave}">
      <ide><cUF>35</cUF><cNF>12345678</cNF><mod>65</mod><serie>1</serie>
        <nNF>77</nNF><dhEmi>2026-01-15T10:00:00-03:00</dhEmi>
        <tpAmb>1</tpAmb><tpEmis>1</tpEmis></ide>
      <emit><CNPJ>11222333000181</CNPJ></emit>
      <det nItem="1">
        <prod><cProd>SKU9</cProd><cEAN>SEM GTIN</cEAN><xProd>ALCATRA</xProd>
          <NCM>02013000</NCM><CFOP>5102</CFOP><uCom>KG</uCom>
          <qCom>1.0000</qCom><vUnCom>50.00</vUnCom><vProd>50.00</vProd></prod>
        <imposto><ICMS><ICMSSN102><orig>0</orig><CSOSN>102</CSOSN></ICMSSN102></ICMS>
          <PIS><PISNT><CST>07</CST></PISNT></PIS>
          <COFINS><COFINSNT><CST>07</CST></COFINSNT></COFINS></imposto>
      </det>
      <total><ICMSTot><vBC>0.00</vBC><vICMS>0.00</vICMS><vProd>50.00</vProd>
        <vDesc>0.00</vDesc><vPIS>0.00</vPIS><vCOFINS>0.00</vCOFINS>
        <vNF>50.00</vNF></ICMSTot></total>
      <pag><detPag><tPag>01</tPag><vPag>50.00</vPag></detPag></pag>
    </infNFe>
  </NFe>
  <protNFe versao="4.00"><infProt><chNFe>{chave}</chNFe>
    <nProt>135260000000001</nProt><cStat>100</cStat>
    <xMotivo>Autorizado o uso da NF-e</xMotivo>
    <dhRecbto>2026-01-15T10:00:05-03:00</dhRecbto></infProt></protNFe>
</nfeProc>
"""


def _chave_valida():
    from bigjhow.services.fiscal.chave import montar_chave
    import datetime

    return montar_chave(
        codigo_uf=35,
        emissao=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
        cnpj="11222333000181",
        modelo=65,
        serie=1,
        numero=77,
        tipo_emissao=1,
        codigo_numerico=12345678,
    )


def test_importa_nfeproc_do_acervo_antigo(app_fiscal):
    with app_fiscal.app_context():
        chave = _chave_valida()
        conteudo = XML_NFEPROC.format(chave=chave).encode("utf-8")
        resultado = ResultadoImportacao()
        importar_conteudo(conteudo, empresa_id=1, resultado=resultado)
        db.session.commit()

        assert resultado.importados == 1
        documento = db.session.scalar(
            db.select(DocumentoFiscal).where(DocumentoFiscal.chave == chave)
        )
        assert documento.status == "autorizado"
        assert documento.protocolo_autorizacao == "135260000000001"
        assert documento.numero == 77
        assert documento.total_documento == Decimal("50.00")
        assert len(documento.itens) == 1
        assert documento.itens[0].ncm == "02013000"
        assert len(documento.arquivos) == 1


def test_importar_o_mesmo_arquivo_duas_vezes_nao_duplica(app_fiscal):
    with app_fiscal.app_context():
        conteudo = XML_NFEPROC.format(chave=_chave_valida()).encode("utf-8")
        resultado = ResultadoImportacao()
        importar_conteudo(conteudo, empresa_id=1, resultado=resultado)
        importar_conteudo(conteudo, empresa_id=1, resultado=resultado)
        db.session.commit()
        assert resultado.importados == 1
        assert resultado.ignorados == 1
        assert db.session.scalar(db.select(db.func.count(DocumentoFiscal.id))) == 1


def test_importador_recusa_xml_desconhecido(app_fiscal):
    from bigjhow.services.fiscal.importador import ImportacaoErro

    with app_fiscal.app_context():
        resultado = ResultadoImportacao()
        with pytest.raises(ImportacaoErro):
            importar_conteudo(b"<qualquerCoisa/>", empresa_id=1, resultado=resultado)


# --------------------------------------------------------------------------
# Telas
# --------------------------------------------------------------------------


def _entrar(cliente, usuario="dono"):
    return cliente.post(
        "/auth/login",
        data={"usuario": usuario, "senha": "SenhaForte!123"},
        follow_redirects=False,
    )


def test_painel_fiscal_abre_para_administrador(app_fiscal):
    cliente = app_fiscal.test_client()
    _entrar(cliente)
    resposta = cliente.get("/empresa/1/fiscal")
    assert resposta.status_code == 200
    assert "Documento fiscal" in resposta.get_data(as_text=True)


def test_operador_nao_acessa_o_painel_fiscal(app_fiscal):
    cliente = app_fiscal.test_client()
    _entrar(cliente, "operador.carnes")
    assert cliente.get("/empresa/1/fiscal").status_code == 403
    assert cliente.get("/empresa/1/fiscal/configuracao").status_code == 403


def test_telas_fiscais_nao_ficam_em_cache(app_fiscal):
    cliente = app_fiscal.test_client()
    _entrar(cliente)
    resposta = cliente.get("/empresa/1/fiscal")
    assert resposta.headers.get("Cache-Control") == "no-store, max-age=0"


def test_classificacao_em_lote_nao_se_declara_revisada(app_fiscal):
    """Aplicar uma regra geral em milhares de itens não é o mesmo que conferir
    item a item. Se o lote se marcasse como revisado, o sistema passaria a dizer
    que está tudo certo — e o NCM errado sairia em produção sem ninguém ver."""
    from bigjhow.services.fiscal.cadastro import (
        aplicar_classificacao_em_lote,
        produtos_aguardando_revisao,
        produtos_sem_classificacao,
    )

    with app_fiscal.app_context():
        dono = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        produto_id = app_fiscal.config["TEST_PRODUCT_ID"]
        db.session.delete(db.session.get(ProdutoFiscal, produto_id))
        db.session.commit()

        assert produtos_sem_classificacao(1) == 1
        assert produtos_aguardando_revisao(1) == 0

        total = aplicar_classificacao_em_lote(
            empresa_id=1,
            dados={"ncm": "02013000", "cfop": "5102", "icms_csosn": "102"},
            usuario_id=dono.id,
        )
        assert total == 1
        # Passa a emitir, mas continua sinalizado como não conferido.
        assert produtos_sem_classificacao(1) == 0
        assert produtos_aguardando_revisao(1) == 1

        registro = db.session.get(ProdutoFiscal, produto_id)
        assert registro.revisao_pendente is True
        assert registro.completo is True
        assert "lote" in (registro.observacao or "").lower()

        prontidao = conferir_prontidao(1)
        assert any("lote" in aviso.lower() for aviso in prontidao["avisos"])


def test_classificacao_individual_encerra_a_pendencia(app_fiscal):
    from bigjhow.services.fiscal.cadastro import produtos_aguardando_revisao

    with app_fiscal.app_context():
        dono = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        produto_id = app_fiscal.config["TEST_PRODUCT_ID"]
        registro = db.session.get(ProdutoFiscal, produto_id)
        registro.revisao_pendente = True
        db.session.commit()
        assert produtos_aguardando_revisao(1) == 1

        salvar_classificacao(
            empresa_id=1,
            produto_id=produto_id,
            dados={"ncm": "02013000", "cfop": "5102", "icms_csosn": "102"},
            usuario_id=dono.id,
        )
        assert produtos_aguardando_revisao(1) == 0


def test_produto_classificado_em_lote_emite_normalmente(app_fiscal):
    """Pendência de revisão é aviso ao gestor, não trava de caixa."""
    with app_fiscal.app_context():
        registro = db.session.get(ProdutoFiscal, app_fiscal.config["TEST_PRODUCT_ID"])
        registro.revisao_pendente = True
        db.session.commit()

        venda, operador = _venda_finalizada(app_fiscal)
        documento, aviso = emitir_com_seguranca(
            empresa_id=1, venda_id=venda.id, usuario_id=operador.id
        )
        assert documento is not None
        assert documento.status == "autorizado"


def test_classificacao_em_lote_grava_em_uma_transacao_so(app_fiscal):
    """Regressão: a primeira versão reaproveitava a função de item único, que
    faz commit por produto. Com alguns milhares de itens a requisição estourava
    o tempo do servidor e o operador via erro 500. O lote precisa ser uma
    transação e um registro de auditoria, não N."""
    from bigjhow.models import Auditoria, Produto, SaldoEstoque
    from bigjhow.services.fiscal.cadastro import aplicar_classificacao_em_lote

    with app_fiscal.app_context():
        dono = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        db.session.delete(db.session.get(ProdutoFiscal, app_fiscal.config["TEST_PRODUCT_ID"]))
        for indice in range(25):
            produto = Produto(
                empresa_id=1,
                sku_interno=f"LOTE-{indice:03d}",
                nome=f"Produto de lote {indice}",
                categoria="Testes",
                unidade="KG",
                preco_venda=Decimal("10.00"),
            )
            produto.saldo = SaldoEstoque(
                quantidade=Decimal("0"), aguardando_inventario=True
            )
            db.session.add(produto)
        db.session.commit()

        antes = db.session.scalar(db.select(db.func.count(Auditoria.id)))
        total = aplicar_classificacao_em_lote(
            empresa_id=1,
            dados={"ncm": "02013000", "cfop": "5102", "icms_csosn": "102"},
            usuario_id=dono.id,
        )
        depois = db.session.scalar(db.select(db.func.count(Auditoria.id)))

        assert total == 26, "deveria classificar o produto do fixture e os 25 novos"
        assert depois - antes == 1, "o lote precisa gerar UM registro de auditoria, não um por produto"

        registro = db.session.scalar(
            db.select(Auditoria)
            .where(Auditoria.acao == "produto_fiscal_lote")
            .order_by(Auditoria.criado_em.desc())
        )
        assert registro is not None
        assert registro.detalhes["produtos"] == 26

        # e todos ficaram utilizáveis, porém ainda sinalizados para conferência
        assert db.session.scalar(
            db.select(db.func.count(ProdutoFiscal.produto_id)).where(
                ProdutoFiscal.revisao_pendente.is_(True)
            )
        ) == 26


def test_classificacao_em_lote_recusa_dados_invalidos_sem_gravar_nada(app_fiscal):
    """A validação acontece uma vez, antes do laço: ou aplica em todos, ou em nenhum."""
    from bigjhow.services.fiscal.cadastro import aplicar_classificacao_em_lote

    with app_fiscal.app_context():
        dono = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        db.session.delete(db.session.get(ProdutoFiscal, app_fiscal.config["TEST_PRODUCT_ID"]))
        db.session.commit()

        with pytest.raises(CadastroFiscalErro):
            aplicar_classificacao_em_lote(
                empresa_id=1,
                dados={"ncm": "123", "cfop": "5102", "icms_csosn": "102"},
                usuario_id=dono.id,
            )
        db.session.rollback()
        assert db.session.scalar(db.select(db.func.count(ProdutoFiscal.produto_id))) == 0


def test_pagamento_fiscal_usa_recebido_e_preserva_troco(app_fiscal):
    from bigjhow.models import VendaPagamento
    from bigjhow.services.fiscal.emissao import montar_dados
    with app_fiscal.app_context():
        venda, _ = _venda_finalizada(app_fiscal)
        pagamento = db.session.scalar(db.select(VendaPagamento).where(VendaPagamento.venda_id == venda.id))
        pagamento.valor_recebido = Decimal("50.00")
        venda.troco = Decimal("30.00")
        db.session.commit()
        dados = montar_dados(venda, db.session.get(EstabelecimentoFiscal, 1), numero=1, serie=1, modelo=65, codigo_numerico=12345678, tipo_emissao=1)
        assert dados.pagamentos[0].valor == Decimal("50.00")
        assert sum(p.valor for p in dados.pagamentos) - dados.troco == dados.total_documento


def test_timeout_preserva_xml_sem_falsa_contingencia(app_fiscal, monkeypatch):
    from bigjhow.models import DocumentoFiscalXml
    from bigjhow.services.fiscal.provedores.base import ProvedorIndisponivel
    def indisponivel(*args, **kwargs):
        raise ProvedorIndisponivel("Resposta perdida")
    monkeypatch.setattr(HomologacaoInterna, "autorizar", indisponivel)
    with app_fiscal.app_context():
        venda, operador = _venda_finalizada(app_fiscal)
        documento = emitir_venda(empresa_id=1, venda_id=venda.id, usuario_id=operador.id)
        assert documento.status == "pendente"
        assert documento.tipo_emissao == 1
        xml = db.session.scalar(db.select(DocumentoFiscalXml).where(DocumentoFiscalXml.documento_id == documento.id, DocumentoFiscalXml.tipo == "enviado"))
        assert xml is not None and documento.chave in xml.arquivo_xml


def test_reenvio_real_nao_reconstroi_nota(app_fiscal, monkeypatch):
    from bigjhow.services.fiscal.emissao import reenviar, EmissaoErro
    with app_fiscal.app_context():
        venda, operador = _venda_finalizada(app_fiscal)
        documento = emitir_venda(empresa_id=1, venda_id=venda.id, usuario_id=operador.id)
        documento.status = "pendente"
        db.session.commit()
        monkeypatch.setattr(HomologacaoInterna, "produz_documento_fiscal", True)
        def proibido(*args, **kwargs):
            pytest.fail("Reenvio não deveria transmitir")
        monkeypatch.setattr(HomologacaoInterna, "autorizar", proibido)
        with pytest.raises(EmissaoErro, match="Reenvio automático indisponível"):
            reenviar(empresa_id=1, documento_id=documento.id)
