"""Testes do núcleo fiscal — as partes puras, sem banco nem rede.

São as que precisam estar matematicamente certas: chave de acesso, dígito
verificador, QR Code, montagem do XML e assinatura digital. Um erro aqui não
aparece como exceção: aparece como rejeição da SEFAZ no balcão.
"""

from __future__ import annotations

import base64
import datetime
from decimal import Decimal

import pytest
from lxml import etree

from bigjhow.services.fiscal import assinatura as mod_assinatura
from bigjhow.services.fiscal import estruturas, qrcode_nfce, qrcode_svg, xml_nfce
from bigjhow.services.fiscal.chave import (
    ChaveInvalida,
    decompor_chave,
    digito_verificador,
    formatar_chave,
    montar_chave,
    validar_chave,
    validar_cnpj,
    validar_cpf,
)


FUSO = datetime.timezone(datetime.timedelta(hours=-3))
EMISSAO = datetime.datetime(2026, 9, 10, 14, 32, 5, tzinfo=FUSO)
CNPJ = "11222333000181"


# --------------------------------------------------------------------------
# Chave de acesso
# --------------------------------------------------------------------------


def test_digito_verificador_conferivel_a_mao():
    # 43 dígitos "1": pesos 2..9 cíclicos somam 229; 229 % 11 = 9; 11 - 9 = 2.
    assert digito_verificador("1" * 43) == 2


def test_chave_tem_44_digitos_e_campos_na_ordem_certa():
    chave = montar_chave(
        codigo_uf=35,
        emissao=EMISSAO,
        cnpj=CNPJ,
        modelo=65,
        serie=1,
        numero=101,
        tipo_emissao=1,
        codigo_numerico=48273615,
    )
    assert len(chave) == 44
    campos = decompor_chave(chave)
    assert campos["codigo_uf"] == 35
    assert campos["ano"] == 26 and campos["mes"] == 9
    assert campos["cnpj"] == CNPJ
    assert campos["modelo"] == 65
    assert campos["serie"] == 1
    assert campos["numero"] == 101
    assert campos["tipo_emissao"] == 1
    assert campos["codigo_numerico"] == 48273615
    assert validar_chave(chave) == chave


def test_chave_com_digito_adulterado_e_recusada():
    chave = montar_chave(
        codigo_uf=35, emissao=EMISSAO, cnpj=CNPJ, modelo=65, serie=1,
        numero=101, tipo_emissao=1, codigo_numerico=48273615,
    )
    adulterada = chave[:43] + str((int(chave[43]) + 1) % 10)
    with pytest.raises(ChaveInvalida):
        validar_chave(adulterada)


def test_formatacao_da_chave_agrupa_de_quatro_em_quatro():
    assert formatar_chave("1" * 44) == " ".join(["1111"] * 11)


@pytest.mark.parametrize("valor", ["11222333000181", "11444777000161", "04252011000110"])
def test_cnpj_valido(valor):
    assert validar_cnpj(valor) == valor


@pytest.mark.parametrize("valor", ["11222333000180", "11111111111111", "123"])
def test_cnpj_invalido(valor):
    with pytest.raises(ChaveInvalida):
        validar_cnpj(valor)


def test_cpf():
    assert validar_cpf("529.982.247-25") == "52998224725"
    with pytest.raises(ChaveInvalida):
        validar_cpf("52998224724")


# --------------------------------------------------------------------------
# QR Code
# --------------------------------------------------------------------------


def test_tabela_de_blocos_bate_com_a_geometria_da_versao():
    """Conferência cruzada: a capacidade calculada pela geometria da versão
    precisa bater com a tabela de blocos. Se uma das duas estiver errada, o
    QR Code sai ilegível — e isso não dá exceção, dá cupom recusado."""
    for versao in sorted(qrcode_svg._BLOCOS_M):
        geometria = qrcode_svg._modulos_de_dados(versao) // 8
        assert geometria == qrcode_svg._total_codewords(versao), f"versão {versao}"


def test_reed_solomon_gera_polinomio_divisivel_pelo_gerador():
    dados = [(indice * 37) % 256 for indice in range(16)]
    correcao = qrcode_svg._codewords_correcao(dados, 10)
    resto = list(dados) + list(correcao)
    gerador = qrcode_svg._polinomio_gerador(10)
    for posicao in range(len(dados)):
        coeficiente = resto[posicao]
        if coeficiente:
            for deslocamento, fator in enumerate(gerador):
                resto[posicao + deslocamento] ^= qrcode_svg._multiplicar(fator, coeficiente)
    assert not any(resto[len(dados):])


def test_informacao_de_formato_e_de_versao_batem_com_a_norma():
    assert qrcode_svg._bits_formato(0) == 0b101010000010010
    assert qrcode_svg._bits_versao(7) == 0x07C94
    # O código BCH da versão tem distância mínima 8 entre todas as combinações.
    valores = [qrcode_svg._bits_versao(v) for v in range(7, 41)]
    minima = min(
        bin(a ^ b).count("1")
        for indice, a in enumerate(valores)
        for b in valores[indice + 1:]
    )
    assert minima == 8


def test_qrcode_gera_matriz_quadrada_e_svg_embutido():
    conteudo = "https://exemplo.sefaz.uf.gov.br/consulta?p=" + "1" * 44 + "|3|1"
    matriz = qrcode_svg.matriz_qrcode(conteudo)
    assert len(matriz) == len(matriz[0])
    assert (len(matriz) - 17) % 4 == 0
    svg = qrcode_svg.qrcode_svg(conteudo)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "<path" in svg


def test_qrcode_recusa_conteudo_maior_que_a_capacidade():
    with pytest.raises(qrcode_svg.QRCodeErro):
        qrcode_svg.matriz_qrcode("x" * 5000)


def test_parametros_do_qrcode_nfce_seguem_o_leiaute_versao_3_online():
    chave = montar_chave(
        codigo_uf=35, emissao=EMISSAO, cnpj=CNPJ, modelo=65, serie=1,
        numero=101, tipo_emissao=1, codigo_numerico=48273615,
    )

    parametros = qrcode_nfce.montar_parametros(
        chave=chave,
        ambiente=2,
    )

    assert qrcode_nfce.VERSAO_QRCODE == 3
    assert parametros == f"{chave}|3|2"

    url = qrcode_nfce.montar_qrcode(
        url_consulta_qrcode="https://exemplo.sefaz.uf.gov.br/consulta",
        chave=chave,
        ambiente=2,
    )

    assert url == (
        f"https://exemplo.sefaz.uf.gov.br/consulta"
        f"?p={chave}|3|2"
    )
    assert "CSC-DE-TESTE" not in url
    parametros_url = url.split("?p=", 1)[1].split("|")
    assert parametros_url == [chave, "3", "2"]


def test_qrcode_v3_exige_https_e_bloqueia_contingencia():
    chave = "1" * 43 + str(digito_verificador("1" * 43))

    with pytest.raises(qrcode_nfce.QRCodeFiscalErro):
        qrcode_nfce.montar_qrcode(
            url_consulta_qrcode="http://inseguro.exemplo",
            chave=chave,
            ambiente=2,
        )

    with pytest.raises(
        qrcode_nfce.QRCodeFiscalErro,
        match="contingência offline",
    ):
        qrcode_nfce.montar_qrcode(
            url_consulta_qrcode="https://exemplo",
            chave=chave,
            ambiente=2,
            contingencia=True,
        )

# --------------------------------------------------------------------------
# XML
# --------------------------------------------------------------------------


def _documento(ambiente: int = 2, crt: int = 1, csosn: str = "102", cst: str | None = None):
    endereco = estruturas.Endereco(
        logradouro="Avenida Brasil", numero="1520", bairro="Centro",
        codigo_municipio=3550308, municipio="Sao Paulo", uf="SP", cep="01010000",
    )
    emitente = estruturas.Emitente(
        cnpj=CNPJ, razao_social="BIG JHOW CARNES LTDA", nome_fantasia="Big Jhow",
        inscricao_estadual="110042490114", endereco=endereco, crt=crt,
    )
    item = estruturas.ItemFiscal(
        numero=1, codigo_produto="SKU-001", descricao="PICANHA BOVINA",
        ncm="02013000", cfop="5102", unidade_comercial="KG",
        quantidade_comercial=Decimal("1.250"), valor_unitario=Decimal("89.90"),
        valor_total=Decimal("112.38"),
        icms=estruturas.TributoICMS(origem=0, csosn=csosn, cst=cst),
        pis=estruturas.TributoContribuicao(cst="07"),
        cofins=estruturas.TributoContribuicao(cst="07"),
    )
    chave = montar_chave(
        codigo_uf=35, emissao=EMISSAO, cnpj=CNPJ, modelo=65, serie=1,
        numero=101, tipo_emissao=1, codigo_numerico=48273615,
    )
    return estruturas.DocumentoDados(
        chave=chave, codigo_numerico=48273615, modelo=65, serie=1, numero=101,
        emissao=EMISSAO, ambiente=ambiente, tipo_emissao=1,
        natureza_operacao="VENDA AO CONSUMIDOR", emitente=emitente, itens=[item],
        pagamentos=[estruturas.PagamentoFiscal(forma="01", valor=Decimal("112.38"))],
    )


def _texto(raiz, caminho):
    return raiz.findtext(
        "/".join(f"{{{xml_nfce.NAMESPACE}}}{parte}" for parte in caminho.split("/"))
    )


def test_xml_sai_com_namespace_na_arvore_e_no_documento():
    """O lxml só coloca o elemento no namespace se o nome vier qualificado.
    Sem isso a árvore em memória diverge do XML assinado."""
    raiz = xml_nfce.montar_nfe(_documento())
    assert raiz.tag == f"{{{xml_nfce.NAMESPACE}}}NFe"
    assert raiz.find(f"{{{xml_nfce.NAMESPACE}}}infNFe") is not None
    reanalisado = etree.fromstring(xml_nfce.serializar(raiz))
    assert reanalisado.tag == raiz.tag


def test_data_de_emissao_usa_fuso_com_dois_pontos():
    """O strftime %z produz -0300; o schema da SEFAZ exige -03:00."""
    raiz = xml_nfce.montar_nfe(_documento())
    inf = raiz.find(f"{{{xml_nfce.NAMESPACE}}}infNFe")
    assert _texto(inf, "ide/dhEmi") == "2026-09-10T14:32:05-03:00"


def test_campos_obrigatorios_da_nfce():
    raiz = xml_nfce.montar_nfe(_documento())
    inf = raiz.find(f"{{{xml_nfce.NAMESPACE}}}infNFe")
    assert inf.get("versao") == "4.00"
    assert inf.get("Id").startswith("NFe")
    assert _texto(inf, "ide/mod") == "65"
    assert _texto(inf, "ide/tpImp") == "4"      # DANFE NFC-e
    assert _texto(inf, "ide/indFinal") == "1"   # consumidor final
    assert _texto(inf, "ide/indPres") == "1"    # operação presencial
    assert _texto(inf, "emit/CNPJ") == CNPJ
    assert _texto(inf, "total/ICMSTot/vNF") == "112.38"
    assert _texto(inf, "pag/detPag/tPag") == "01"


def test_homologacao_sem_consumidor_identificado_nao_gera_dest():
    raiz = xml_nfce.montar_nfe(_documento(ambiente=2))
    inf = raiz.find(f"{{{xml_nfce.NAMESPACE}}}infNFe")

    assert inf.find(
        f"{{{xml_nfce.NAMESPACE}}}dest"
    ) is None


def test_homologacao_com_consumidor_identificado_usa_nome_obrigatorio():
    dados = _documento(ambiente=2)
    dados.destinatario = estruturas.Destinatario(
        cpf="12345678909",
        nome="CLIENTE TESTE",
    )

    raiz = xml_nfce.montar_nfe(dados)
    inf = raiz.find(f"{{{xml_nfce.NAMESPACE}}}infNFe")

    assert _texto(inf, "dest/CPF") == "12345678909"
    assert (
        _texto(inf, "dest/xNome")
        == xml_nfce.NOME_HOMOLOGACAO
    )


def test_destinatario_informado_sem_documento_e_rejeitado():
    dados = _documento(ambiente=1)
    dados.destinatario = estruturas.Destinatario(
        nome="CLIENTE SEM DOCUMENTO",
    )

    with pytest.raises(
        xml_nfce.XmlFiscalErro,
        match="CPF ou CNPJ",
    ):
        xml_nfce.montar_nfe(dados)


def test_producao_sem_consumidor_identificado_nao_gera_grupo_dest():
    raiz = xml_nfce.montar_nfe(_documento(ambiente=1))
    inf = raiz.find(f"{{{xml_nfce.NAMESPACE}}}infNFe")
    assert inf.find(f"{{{xml_nfce.NAMESPACE}}}dest") is None


def test_homologacao_nfce_usa_descricao_obrigatoria_no_primeiro_item():
    raiz = xml_nfce.montar_nfe(_documento(ambiente=2))
    inf = raiz.find(f"{{{xml_nfce.NAMESPACE}}}infNFe")

    assert _texto(inf, "det/prod/xProd") == xml_nfce.DESCRICAO_ITEM_HOMOLOGACAO


def test_producao_nfce_preserva_descricao_real_do_produto():
    raiz = xml_nfce.montar_nfe(_documento(ambiente=1))
    inf = raiz.find(f"{{{xml_nfce.NAMESPACE}}}infNFe")

    assert _texto(inf, "det/prod/xProd") == "PICANHA BOVINA"


def test_homologacao_altera_somente_primeiro_item():
    from dataclasses import replace

    dados = _documento(ambiente=2)
    segundo = replace(
        dados.itens[0],
        numero=2,
        codigo_produto="SKU-002",
        descricao="COSTELA BOVINA",
    )
    dados.itens.append(segundo)

    raiz = xml_nfce.montar_nfe(dados)
    inf = raiz.find(f"{{{xml_nfce.NAMESPACE}}}infNFe")

    descricoes = [
        no.findtext(
            f"{{{xml_nfce.NAMESPACE}}}prod/"
            f"{{{xml_nfce.NAMESPACE}}}xProd"
        )
        for no in inf.findall(f"{{{xml_nfce.NAMESPACE}}}det")
    ]

    assert descricoes == [
        xml_nfce.DESCRICAO_ITEM_HOMOLOGACAO,
        "COSTELA BOVINA",
    ]


def test_simples_nacional_usa_csosn_e_regime_normal_usa_cst():
    inf_simples = xml_nfce.montar_nfe(_documento(crt=1, csosn="102")).find(
        f"{{{xml_nfce.NAMESPACE}}}infNFe"
    )
    assert inf_simples.find(f".//{{{xml_nfce.NAMESPACE}}}ICMSSN102") is not None

    inf_normal = xml_nfce.montar_nfe(_documento(crt=3, csosn=None, cst="00")).find(
        f"{{{xml_nfce.NAMESPACE}}}infNFe"
    )
    assert inf_normal.find(f".//{{{xml_nfce.NAMESPACE}}}ICMS00") is not None


def test_produto_sem_ncm_valido_e_recusado_antes_de_transmitir():
    dados = _documento()
    dados.itens[0].ncm = "123"
    with pytest.raises(xml_nfce.XmlFiscalErro) as erro:
        xml_nfce.montar_nfe(dados)
    assert "NCM" in str(erro.value)


def test_situacao_tributaria_ausente_e_recusada():
    dados = _documento(csosn=None)
    with pytest.raises(xml_nfce.XmlFiscalErro):
        xml_nfce.montar_nfe(dados)


def test_documento_sem_pagamento_e_recusado():
    dados = _documento()
    dados.pagamentos = []
    with pytest.raises(xml_nfce.XmlFiscalErro):
        xml_nfce.montar_nfe(dados)


def test_totais_do_documento():
    dados = _documento()
    assert dados.total_produtos == Decimal("112.38")
    assert dados.total_documento == Decimal("112.38")


# --------------------------------------------------------------------------
# Assinatura digital
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def certificado_de_teste(tmp_path_factory):
    """Gera um A1 de mentira só para exercitar o caminho da assinatura."""
    pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
        x509.NameAttribute(NameOID.COMMON_NAME, f"BIG JHOW CARNES LTDA:{CNPJ}"),
    ])
    agora = datetime.datetime.now(datetime.timezone.utc)
    certificado = (
        x509.CertificateBuilder()
        .subject_name(nome).issuer_name(nome)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - datetime.timedelta(days=1))
        .not_valid_after(agora + datetime.timedelta(days=365))
        .sign(chave, hashes.SHA256())
    )
    caminho = tmp_path_factory.mktemp("fiscal") / "teste.pfx"
    caminho.write_bytes(
        pkcs12.serialize_key_and_certificates(
            b"teste", chave, certificado, None,
            serialization.BestAvailableEncryption(b"senha-de-teste"),
        )
    )
    return caminho, certificado


def test_certificado_abre_com_a_senha_certa_e_recusa_a_errada(certificado_de_teste):
    caminho, _ = certificado_de_teste
    aberto = mod_assinatura.carregar_certificado(str(caminho), "senha-de-teste")
    assert aberto.cnpj_titular == CNPJ
    assert not aberto.vencido
    with pytest.raises(mod_assinatura.AssinaturaErro):
        mod_assinatura.carregar_certificado(str(caminho), "senha-errada")


def test_certificado_inexistente_da_erro_claro():
    with pytest.raises(mod_assinatura.AssinaturaErro):
        mod_assinatura.carregar_certificado("/caminho/que/nao/existe.pfx", "x")


def test_assinatura_fica_no_lugar_certo_e_confere_criptograficamente(certificado_de_teste):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    caminho, certificado_publico = certificado_de_teste
    aberto = mod_assinatura.carregar_certificado(str(caminho), "senha-de-teste")

    raiz = xml_nfce.montar_nfe(_documento())
    inf = raiz.find(f"{{{xml_nfce.NAMESPACE}}}infNFe")
    mod_assinatura.assinar_documento(raiz, elemento_referenciado=inf, certificado=aberto)

    ns = f"{{{mod_assinatura.NAMESPACE_ASSINATURA}}}"
    sig = raiz.find(f"{ns}Signature")
    assert sig is not None
    assert raiz[-1] is sig, "a Signature precisa ser irmã de infNFe, no fim de NFe"
    referencia = sig.find(f"{ns}SignedInfo/{ns}Reference")
    assert referencia.get("URI") == f"#{inf.get('Id')}"
    assert mod_assinatura.conferir_assinatura(raiz, inf)

    info = sig.find(f"{ns}SignedInfo")
    canonico = etree.tostring(info, method="c14n", exclusive=False, with_comments=False)
    valor = base64.b64decode(sig.find(f"{ns}SignatureValue").text)
    certificado_publico.public_key().verify(
        valor, canonico, padding.PKCS1v15(), hashes.SHA1()
    )


def test_adulterar_o_xml_depois_de_assinar_quebra_o_resumo(certificado_de_teste):
    caminho, _ = certificado_de_teste
    aberto = mod_assinatura.carregar_certificado(str(caminho), "senha-de-teste")
    raiz = xml_nfce.montar_nfe(_documento())
    inf = raiz.find(f"{{{xml_nfce.NAMESPACE}}}infNFe")
    mod_assinatura.assinar_documento(raiz, elemento_referenciado=inf, certificado=aberto)

    total = inf.find(
        f"{{{xml_nfce.NAMESPACE}}}total/{{{xml_nfce.NAMESPACE}}}ICMSTot"
        f"/{{{xml_nfce.NAMESPACE}}}vNF"
    )
    total.text = "999.99"
    assert not mod_assinatura.conferir_assinatura(raiz, inf)


def test_suplemento_preserva_assinatura_e_ordem(certificado_de_teste):
    caminho, _ = certificado_de_teste
    aberto = mod_assinatura.carregar_certificado(str(caminho), "senha-de-teste")
    raiz = xml_nfce.montar_nfe(_documento())
    inf = raiz.find(xml_nfce.qualificar("infNFe"))
    mod_assinatura.assinar_documento(raiz, elemento_referenciado=inf, certificado=aberto)
    assinado = xml_nfce.serializar(raiz)
    final = xml_nfce.incluir_suplemento(assinado, qrcode="https://exemplo.test/?p=abc|3|2", url_chave="https://exemplo.test/chave")
    resultado = etree.fromstring(final)
    assert [etree.QName(x).localname for x in resultado] == ["infNFe", "infNFeSupl", "Signature"]
    assert mod_assinatura.conferir_assinatura(resultado, resultado[0])
    assert resultado[1][0].text == "https://exemplo.test/?p=abc|3|2"


def test_suplemento_exige_consulta_por_chave():
    with pytest.raises(xml_nfce.XmlFiscalErro):
        xml_nfce.incluir_suplemento(xml_nfce.montar_xml(_documento()), qrcode="abc", url_chave="")

def test_soap12_default_namespace_preserva_assinatura_rsa(
    certificado_de_teste,
):
    """O Envelope SOAP 1.2 nao pode invalidar a XMLDSig da NFe."""
    import base64
    import inspect

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from lxml import etree

    from bigjhow.services.fiscal import assinatura as assinatura_mod
    from bigjhow.services.fiscal import xml_nfce as xml_mod
    from bigjhow.services.fiscal.provedores import sefaz_direto as sefaz_mod

    fonte = inspect.getsource(
        sefaz_mod.SefazDireto._enviar
    )

    assert (
        'nsmap={None: NAMESPACE_SOAP}'
        in fonte
    )

    assert (
        'nsmap={"soap12": NAMESPACE_SOAP}'
        not in fonte
    )

    caminho, _ = certificado_de_teste

    certificado = assinatura_mod.carregar_certificado(
        str(caminho),
        "senha-de-teste",
    )

    nfe = xml_mod.montar_nfe(
        _documento()
    )

    inf_nfe = nfe.find(
        xml_mod.qualificar("infNFe")
    )

    assert inf_nfe is not None

    assinatura_mod.assinar_documento(
        nfe,
        elemento_referenciado=inf_nfe,
        certificado=certificado,
    )

    xml_assinado = xml_mod.serializar(
        nfe
    )

    nfe_envio = etree.fromstring(
        xml_assinado
    )

    envio = etree.Element(
        xml_mod.qualificar("enviNFe"),
        nsmap={
            None: xml_mod.NAMESPACE
        },
    )

    envio.set(
        "versao",
        "4.00",
    )

    etree.SubElement(
        envio,
        xml_mod.qualificar("idLote"),
    ).text = "1"

    etree.SubElement(
        envio,
        xml_mod.qualificar("indSinc"),
    ).text = "1"

    envio.append(
        nfe_envio
    )

    wsdl = (
        "http://www.portalfiscal.inf.br/"
        "nfe/wsdl/NFeAutorizacao4"
    )

    mensagem = etree.Element(
        f"{{{wsdl}}}nfeDadosMsg",
        nsmap={
            None: wsdl
        },
    )

    mensagem.append(
        envio
    )

    envelope = etree.Element(
        f"{{{sefaz_mod.NAMESPACE_SOAP}}}Envelope",
        nsmap={
            None: sefaz_mod.NAMESPACE_SOAP
        },
    )

    body = etree.SubElement(
        envelope,
        f"{{{sefaz_mod.NAMESPACE_SOAP}}}Body",
    )

    body.append(
        mensagem
    )

    transmitido = etree.fromstring(
        etree.tostring(
            envelope,
            encoding="utf-8",
            xml_declaration=True,
        )
    )

    dsig = (
        "http://www.w3.org/"
        "2000/09/xmldsig#"
    )

    nfe_final = transmitido.find(
        f".//{{{xml_mod.NAMESPACE}}}NFe"
    )

    assert nfe_final is not None

    signature = nfe_final.find(
        f"{{{dsig}}}Signature"
    )

    assert signature is not None

    signed_info = signature.find(
        f"{{{dsig}}}SignedInfo"
    )

    signature_value = signature.findtext(
        f"{{{dsig}}}SignatureValue"
    )

    certificado_b64 = signature.findtext(
        f".//{{{dsig}}}X509Certificate"
    )

    assert signed_info is not None
    assert signature_value
    assert certificado_b64

    signed_info_c14n = etree.tostring(
        signed_info,
        method="c14n",
        exclusive=False,
        with_comments=False,
    )

    certificado_publico = (
        x509.load_der_x509_certificate(
            base64.b64decode(
                certificado_b64
            )
        )
    )

    # InvalidSignature gera falha no pytest.
    certificado_publico.public_key().verify(
        base64.b64decode(
            signature_value
        ),
        signed_info_c14n,
        padding.PKCS1v15(),
        hashes.SHA1(),
    )

    namespaces = dict(
        signed_info.nsmap
    )

    # A causa da rejeicao 297 foi justamente
    # soap12 ficar em escopo no SignedInfo.
    assert "soap12" not in namespaces

def test_digest_infnfe_preserva_contexto_c14n_inclusiva(
    certificado_de_teste,
):
    """DigestValue deve usar a C14N inclusiva do infNFe ainda na arvore."""
    import base64
    import hashlib

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from lxml import etree

    from bigjhow.services.fiscal import assinatura as assinatura_mod
    from bigjhow.services.fiscal import xml_nfce as xml_mod
    from bigjhow.services.fiscal.provedores import sefaz_direto as sefaz_mod

    def b64sha1(valor):
        return base64.b64encode(
            hashlib.sha1(valor).digest()
        ).decode("ascii")

    caminho, certificado_publico = certificado_de_teste

    certificado = assinatura_mod.carregar_certificado(
        str(caminho),
        "senha-de-teste",
    )

    nfe = xml_mod.montar_nfe(
        _documento()
    )

    inf = nfe.find(
        xml_mod.qualificar("infNFe")
    )

    assert inf is not None

    # -------------------------------------------------
    # ORACULO INDEPENDENTE:
    # canonicaliza diretamente o no ainda anexado.
    # Nao chama calcular_digest().
    # -------------------------------------------------

    c14n_inclusiva = etree.tostring(
        inf,
        method="c14n",
        exclusive=False,
        with_comments=False,
    )

    esperado = b64sha1(
        c14n_inclusiva
    )

    # Reproduz exatamente a antiga regressao:
    # serializa o subtree, reparseia e perde contexto.
    destacado = etree.fromstring(
        etree.tostring(
            inf,
            encoding="utf-8",
        )
    )

    c14n_destacada = etree.tostring(
        destacado,
        method="c14n",
        exclusive=False,
        with_comments=False,
    )

    digest_antigo = b64sha1(
        c14n_destacada
    )

    # A fixture deve realmente expor a regressao.
    assert esperado != digest_antigo

    assinatura_mod.assinar_documento(
        nfe,
        elemento_referenciado=inf,
        certificado=certificado,
    )

    ns = assinatura_mod.NAMESPACE_ASSINATURA

    signature = nfe.find(
        f"{{{ns}}}Signature"
    )

    assert signature is not None

    signed_info = signature.find(
        f"{{{ns}}}SignedInfo"
    )

    reference = signed_info.find(
        f"{{{ns}}}Reference"
    )

    digest_xml = reference.findtext(
        f"{{{ns}}}DigestValue"
    )

    assert digest_xml == esperado
    assert digest_xml != digest_antigo

    # -------------------------------------------------
    # RSA independente
    # -------------------------------------------------

    assinatura_binaria = base64.b64decode(
        signature.findtext(
            f"{{{ns}}}SignatureValue"
        )
    )

    signed_info_c14n = etree.tostring(
        signed_info,
        method="c14n",
        exclusive=False,
        with_comments=False,
    )

    certificado_publico.public_key().verify(
        assinatura_binaria,
        signed_info_c14n,
        padding.PKCS1v15(),
        hashes.SHA1(),
    )

    # -------------------------------------------------
    # Leva o MESMO XML ate o contexto final SOAP
    # e recalcula o DigestValue independentemente.
    # -------------------------------------------------

    nfe_envio = etree.fromstring(
        xml_mod.serializar(
            nfe
        )
    )

    envio = etree.Element(
        xml_mod.qualificar(
            "enviNFe"
        ),
        nsmap={
            None: xml_mod.NAMESPACE
        },
    )

    envio.set(
        "versao",
        "4.00",
    )

    etree.SubElement(
        envio,
        xml_mod.qualificar(
            "idLote"
        ),
    ).text = "1"

    etree.SubElement(
        envio,
        xml_mod.qualificar(
            "indSinc"
        ),
    ).text = "1"

    envio.append(
        nfe_envio
    )

    wsdl = (
        "http://www.portalfiscal.inf.br/"
        "nfe/wsdl/NFeAutorizacao4"
    )

    mensagem = etree.Element(
        f"{{{wsdl}}}nfeDadosMsg",
        nsmap={
            None: wsdl
        },
    )

    mensagem.append(
        envio
    )

    envelope = etree.Element(
        f"{{{sefaz_mod.NAMESPACE_SOAP}}}Envelope",
        nsmap={
            None: sefaz_mod.NAMESPACE_SOAP
        },
    )

    body = etree.SubElement(
        envelope,
        f"{{{sefaz_mod.NAMESPACE_SOAP}}}Body",
    )

    body.append(
        mensagem
    )

    final = etree.fromstring(
        etree.tostring(
            envelope,
            encoding="utf-8",
            xml_declaration=True,
        )
    )

    inf_final = final.find(
        f".//{{{xml_mod.NAMESPACE}}}infNFe"
    )

    signature_final = final.find(
        f".//{{{ns}}}Signature"
    )

    assert inf_final is not None
    assert signature_final is not None

    digest_final_xml = (
        signature_final.findtext(
            f"{{{ns}}}SignedInfo/"
            f"{{{ns}}}Reference/"
            f"{{{ns}}}DigestValue"
        )
    )

    digest_final_independente = b64sha1(
        etree.tostring(
            inf_final,
            method="c14n",
            exclusive=False,
            with_comments=False,
        )
    )

    assert (
        digest_final_xml
        == digest_final_independente
    )

    si_final = signature_final.find(
        f"{{{ns}}}SignedInfo"
    )

    valor_final = base64.b64decode(
        signature_final.findtext(
            f"{{{ns}}}SignatureValue"
        )
    )

    certificado_publico.public_key().verify(
        valor_final,
        etree.tostring(
            si_final,
            method="c14n",
            exclusive=False,
            with_comments=False,
        ),
        padding.PKCS1v15(),
        hashes.SHA1(),
    )
