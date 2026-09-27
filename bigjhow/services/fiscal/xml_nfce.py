"""Montagem do XML da NF-e / NFC-e no leiaute 4.00.

Referência: Manual de Orientação do Contribuinte 7.0 e Notas Técnicas
2018.005 (responsável técnico), 2016.002 e 2019.001.

O módulo é puro: recebe ``DocumentoDados`` e devolve XML. Não conhece banco,
Flask nem rede, o que permite testá-lo isoladamente.
"""

from __future__ import annotations

from decimal import Decimal

from lxml import etree

from .chave import somente_digitos, validar_chave
from .estruturas import (
    DocumentoDados,
    ItemFiscal,
    PagamentoFiscal,
    aliquota,
    data_hora_fiscal,
    dinheiro,
    quantidade,
    unitario,
)


NAMESPACE = "http://www.portalfiscal.inf.br/nfe"
NSMAP = {None: NAMESPACE}
VERSAO_LEIAUTE = "4.00"

# Frase obrigatória no destinatário quando o ambiente é de homologação
# (MOC 7.0, item 5.6). Sem ela a SEFAZ rejeita com a regra G024.
NOME_HOMOLOGACAO = "NF-E EMITIDA EM AMBIENTE DE HOMOLOGACAO - SEM VALOR FISCAL"

SEM_GTIN = "SEM GTIN"

# Códigos tPag do leiaute 4.00.
FORMAS_PAGAMENTO = {
    "01": "Dinheiro",
    "02": "Cheque",
    "03": "Cartão de crédito",
    "04": "Cartão de débito",
    "05": "Crédito da loja",
    "10": "Vale alimentação",
    "11": "Vale refeição",
    "12": "Vale presente",
    "13": "Vale combustível",
    "15": "Boleto bancário",
    "16": "Depósito bancário",
    "17": "Pagamento instantâneo (PIX)",
    "18": "Transferência bancária",
    "19": "Programa de fidelidade",
    "90": "Sem pagamento",
    "99": "Outros",
}

# Tradução das formas de pagamento do caixa para os códigos fiscais.
FORMA_POR_METODO_CAIXA = {
    "dinheiro": "01",
    "pix": "17",
    "debito": "04",
    "credito": "03",
}

CST_ICMS_SUPORTADOS = {"00", "20", "40", "41", "50", "51", "60", "90"}
CSOSN_SUPORTADOS = {"101", "102", "103", "300", "400", "500", "900"}


class XmlFiscalErro(ValueError):
    pass


def qualificar(nome: str) -> str:
    """Nome do elemento já com o namespace da NF-e.

    O lxml só coloca o elemento dentro do namespace quando o nome vem
    qualificado. Sem isso a árvore em memória fica sem namespace enquanto o
    XML serializado fica com ele — as buscas por ``find`` falham e a árvore
    deixa de corresponder ao documento que a SEFAZ recebe.
    """
    return f"{{{NAMESPACE}}}{nome}"


def _elemento(pai, nome: str, texto=None):
    filho = etree.SubElement(pai, qualificar(nome))
    if texto is not None:
        filho.text = str(texto)
    return filho


def _opcional(pai, nome: str, texto):
    if texto is None:
        return None
    valor = str(texto).strip()
    if not valor:
        return None
    return _elemento(pai, nome, valor)


def _texto_fiscal(valor: str | None, *, maximo: int) -> str:
    """Normaliza espaços e corta no limite do campo, como a SEFAZ espera."""
    normalizado = " ".join((valor or "").split())
    return normalizado[:maximo]


# --------------------------------------------------------------------------
# ICMS
# --------------------------------------------------------------------------


def _montar_icms(pai, item: ItemFiscal, crt: int) -> None:
    grupo = _elemento(pai, "ICMS")
    icms = item.icms
    origem = int(icms.origem)
    if origem not in range(0, 9):
        raise XmlFiscalErro(f"Item {item.numero}: origem da mercadoria inválida.")

    if crt in (1, 4):  # Simples Nacional
        csosn = (icms.csosn or "").strip()
        if csosn not in CSOSN_SUPORTADOS:
            raise XmlFiscalErro(
                f"Item {item.numero}: CSOSN {csosn or '(vazio)'} não configurado ou não suportado."
            )
        if csosn in {"102", "103", "300", "400"}:
            no = _elemento(grupo, "ICMSSN102")
            _elemento(no, "orig", origem)
            _elemento(no, "CSOSN", csosn)
        elif csosn == "101":
            no = _elemento(grupo, "ICMSSN101")
            _elemento(no, "orig", origem)
            _elemento(no, "CSOSN", csosn)
            _elemento(no, "pCredSN", aliquota(icms.percentual_credito_sn or 0))
            _elemento(no, "vCredICMSSN", dinheiro(icms.valor_credito_sn or 0))
        elif csosn == "500":
            no = _elemento(grupo, "ICMSSN500")
            _elemento(no, "orig", origem)
            _elemento(no, "CSOSN", csosn)
            if icms.base_calculo_st_retido is not None:
                _elemento(no, "vBCSTRet", dinheiro(icms.base_calculo_st_retido))
                _elemento(no, "pST", aliquota(icms.aliquota_st_consumidor or 0))
                _elemento(no, "vICMSSTRet", dinheiro(icms.valor_st_retido or 0))
        else:  # 900
            no = _elemento(grupo, "ICMSSN900")
            _elemento(no, "orig", origem)
            _elemento(no, "CSOSN", csosn)
            _elemento(no, "modBC", int(icms.modalidade_bc))
            _elemento(no, "vBC", dinheiro(icms.base_calculo))
            _elemento(no, "pICMS", aliquota(icms.aliquota))
            _elemento(no, "vICMS", dinheiro(icms.valor))
        return

    cst = (icms.cst or "").strip()
    if cst not in CST_ICMS_SUPORTADOS:
        raise XmlFiscalErro(
            f"Item {item.numero}: CST de ICMS {cst or '(vazio)'} não configurado ou não suportado."
        )
    if cst == "00":
        no = _elemento(grupo, "ICMS00")
        _elemento(no, "orig", origem)
        _elemento(no, "CST", cst)
        _elemento(no, "modBC", int(icms.modalidade_bc))
        _elemento(no, "vBC", dinheiro(icms.base_calculo))
        _elemento(no, "pICMS", aliquota(icms.aliquota))
        _elemento(no, "vICMS", dinheiro(icms.valor))
    elif cst == "20":
        no = _elemento(grupo, "ICMS20")
        _elemento(no, "orig", origem)
        _elemento(no, "CST", cst)
        _elemento(no, "modBC", int(icms.modalidade_bc))
        _elemento(no, "pRedBC", aliquota(icms.percentual_reducao_bc or 0))
        _elemento(no, "vBC", dinheiro(icms.base_calculo))
        _elemento(no, "pICMS", aliquota(icms.aliquota))
        _elemento(no, "vICMS", dinheiro(icms.valor))
    elif cst in {"40", "41", "50"}:
        no = _elemento(grupo, "ICMS40")
        _elemento(no, "orig", origem)
        _elemento(no, "CST", cst)
        if icms.valor_desonerado and Decimal(icms.valor_desonerado) > 0:
            _elemento(no, "vICMSDeson", dinheiro(icms.valor_desonerado))
            if icms.motivo_desoneracao is None:
                raise XmlFiscalErro(
                    f"Item {item.numero}: informe o motivo da desoneração (motDesICMS)."
                )
            _elemento(no, "motDesICMS", int(icms.motivo_desoneracao))
    elif cst == "51":
        no = _elemento(grupo, "ICMS51")
        _elemento(no, "orig", origem)
        _elemento(no, "CST", cst)
        _elemento(no, "modBC", int(icms.modalidade_bc))
        _elemento(no, "vBC", dinheiro(icms.base_calculo))
        _elemento(no, "pICMS", aliquota(icms.aliquota))
        _elemento(no, "vICMS", dinheiro(icms.valor))
    elif cst == "60":
        no = _elemento(grupo, "ICMS60")
        _elemento(no, "orig", origem)
        _elemento(no, "CST", cst)
        if icms.base_calculo_st_retido is not None:
            _elemento(no, "vBCSTRet", dinheiro(icms.base_calculo_st_retido))
            _elemento(no, "pST", aliquota(icms.aliquota_st_consumidor or 0))
            _elemento(no, "vICMSSTRet", dinheiro(icms.valor_st_retido or 0))
    else:  # 90
        no = _elemento(grupo, "ICMS90")
        _elemento(no, "orig", origem)
        _elemento(no, "CST", cst)
        _elemento(no, "modBC", int(icms.modalidade_bc))
        _elemento(no, "vBC", dinheiro(icms.base_calculo))
        _elemento(no, "pICMS", aliquota(icms.aliquota))
        _elemento(no, "vICMS", dinheiro(icms.valor))


def _montar_contribuicao(pai, item: ItemFiscal, sigla: str) -> None:
    tributo = item.pis if sigla == "PIS" else item.cofins
    grupo = _elemento(pai, sigla)
    cst = (tributo.cst or "").strip()
    if len(cst) != 2 or not cst.isdigit():
        raise XmlFiscalErro(f"Item {item.numero}: CST de {sigla} inválido.")
    if cst in {"01", "02"}:
        no = _elemento(grupo, f"{sigla}Aliq")
        _elemento(no, "CST", cst)
        _elemento(no, "vBC", dinheiro(tributo.base_calculo))
        _elemento(no, "pPIS" if sigla == "PIS" else "pCOFINS", aliquota(tributo.aliquota))
        _elemento(no, "vPIS" if sigla == "PIS" else "vCOFINS", dinheiro(tributo.valor))
    elif cst in {"04", "05", "06", "07", "08", "09"}:
        no = _elemento(grupo, f"{sigla}NT")
        _elemento(no, "CST", cst)
    else:
        no = _elemento(grupo, f"{sigla}Outr")
        _elemento(no, "CST", cst)
        _elemento(no, "vBC", dinheiro(tributo.base_calculo))
        _elemento(no, "pPIS" if sigla == "PIS" else "pCOFINS", aliquota(tributo.aliquota))
        _elemento(no, "vPIS" if sigla == "PIS" else "vCOFINS", dinheiro(tributo.valor))


# --------------------------------------------------------------------------
# Blocos principais
# --------------------------------------------------------------------------


def _montar_ide(pai, dados: DocumentoDados) -> None:
    ide = _elemento(pai, "ide")
    _elemento(ide, "cUF", f"{int(dados.chave[0:2]):02d}")
    _elemento(ide, "cNF", f"{int(dados.codigo_numerico):08d}")
    _elemento(ide, "natOp", _texto_fiscal(dados.natureza_operacao, maximo=60))
    _elemento(ide, "mod", f"{int(dados.modelo):02d}")
    _elemento(ide, "serie", int(dados.serie))
    _elemento(ide, "nNF", int(dados.numero))
    _elemento(ide, "dhEmi", data_hora_fiscal(dados.emissao))
    _elemento(ide, "tpNF", 1)  # 1 = saída
    _elemento(ide, "idDest", 1)  # 1 = operação interna
    _elemento(ide, "cMunFG", int(dados.emitente.endereco.codigo_municipio))
    _elemento(ide, "tpImp", 4 if int(dados.modelo) == 65 else 1)
    _elemento(ide, "tpEmis", int(dados.tipo_emissao))
    _elemento(ide, "cDV", int(dados.chave[43]))
    _elemento(ide, "tpAmb", int(dados.ambiente))
    _elemento(ide, "finNFe", 1)  # 1 = NF-e normal
    _elemento(ide, "indFinal", 1)  # 1 = consumidor final
    _elemento(ide, "indPres", 1)  # 1 = operação presencial
    _elemento(ide, "procEmi", 0)  # 0 = aplicativo do contribuinte
    _elemento(ide, "verProc", _texto_fiscal(dados.versao_aplicativo, maximo=20))


def _montar_emitente(pai, dados: DocumentoDados) -> None:
    emitente = dados.emitente
    no = _elemento(pai, "emit")
    _elemento(no, "CNPJ", somente_digitos(emitente.cnpj))
    _elemento(no, "xNome", _texto_fiscal(emitente.razao_social, maximo=60))
    _opcional(no, "xFant", _texto_fiscal(emitente.nome_fantasia, maximo=60))

    endereco = emitente.endereco
    ender = _elemento(no, "enderEmit")
    _elemento(ender, "xLgr", _texto_fiscal(endereco.logradouro, maximo=60))
    _elemento(ender, "nro", _texto_fiscal(endereco.numero, maximo=60) or "S/N")
    _opcional(ender, "xCpl", _texto_fiscal(endereco.complemento, maximo=60))
    _elemento(ender, "xBairro", _texto_fiscal(endereco.bairro, maximo=60))
    _elemento(ender, "cMun", int(endereco.codigo_municipio))
    _elemento(ender, "xMun", _texto_fiscal(endereco.municipio, maximo=60))
    _elemento(ender, "UF", endereco.uf.upper())
    _elemento(ender, "CEP", somente_digitos(endereco.cep))
    _elemento(ender, "cPais", 1058)
    _elemento(ender, "xPais", "BRASIL")
    _opcional(ender, "fone", somente_digitos(endereco.telefone))

    _elemento(no, "IE", somente_digitos(emitente.inscricao_estadual) or "ISENTO")
    _opcional(no, "IM", somente_digitos(emitente.inscricao_municipal))
    _elemento(no, "CRT", int(emitente.crt))


def _montar_destinatario(pai, dados: DocumentoDados) -> None:
    destinatario = dados.destinatario

    # Consumidor não identificado: o grupo <dest> é omitido,
    # inclusive em homologação.
    if destinatario is None:
        return

    cnpj = somente_digitos(destinatario.cnpj)
    cpf = somente_digitos(destinatario.cpf)

    if not cnpj and not cpf:
        raise XmlFiscalErro(
            "Destinatário informado precisa ter CPF ou CNPJ."
        )

    no = _elemento(pai, "dest")

    if cnpj:
        _elemento(no, "CNPJ", cnpj)
    else:
        _elemento(no, "CPF", cpf)

    if int(dados.ambiente) == 2:
        _elemento(no, "xNome", NOME_HOMOLOGACAO)
    elif destinatario.nome:
        _elemento(
            no,
            "xNome",
            _texto_fiscal(destinatario.nome, maximo=60),
        )

    _elemento(
        no,
        "indIEDest",
        int(destinatario.indicador_ie),
    )


DESCRICAO_ITEM_HOMOLOGACAO = (
    "NOTA FISCAL EMITIDA EM AMBIENTE DE HOMOLOGACAO - SEM VALOR FISCAL"
)


def _montar_item(
    pai,
    item: ItemFiscal,
    crt: int,
    *,
    modelo: int,
    ambiente: int,
) -> None:
    no = _elemento(pai, "det", None)
    no.set("nItem", str(item.numero))
    prod = _elemento(no, "prod")
    _elemento(prod, "cProd", _texto_fiscal(item.codigo_produto, maximo=60))
    _elemento(prod, "cEAN", somente_digitos(item.ean) or SEM_GTIN)

    descricao = item.descricao
    if int(modelo) == 65 and int(ambiente) == 2 and int(item.numero) == 1:
        descricao = DESCRICAO_ITEM_HOMOLOGACAO

    _elemento(prod, "xProd", _texto_fiscal(descricao, maximo=120))
    ncm = somente_digitos(item.ncm)
    if len(ncm) != 8:
        raise XmlFiscalErro(
            f"Item {item.numero} ({item.descricao}): NCM precisa ter 8 dígitos."
        )
    _elemento(prod, "NCM", ncm)
    if item.cest:
        _elemento(prod, "CEST", somente_digitos(item.cest).zfill(7))
    cfop = somente_digitos(item.cfop)
    if len(cfop) != 4:
        raise XmlFiscalErro(f"Item {item.numero} ({item.descricao}): CFOP inválido.")
    _elemento(prod, "CFOP", cfop)
    _elemento(prod, "uCom", _texto_fiscal(item.unidade_comercial, maximo=6))
    _elemento(prod, "qCom", quantidade(item.quantidade_comercial))
    _elemento(prod, "vUnCom", unitario(item.valor_unitario))
    _elemento(prod, "vProd", dinheiro(item.valor_total))
    _elemento(prod, "cEANTrib", somente_digitos(item.ean_tributavel or item.ean) or SEM_GTIN)
    _elemento(prod, "uTrib", _texto_fiscal(item.unidade_tributavel or item.unidade_comercial, maximo=6))
    _elemento(
        prod,
        "qTrib",
        quantidade(
            item.quantidade_tributavel
            if item.quantidade_tributavel is not None
            else item.quantidade_comercial
        ),
    )
    _elemento(
        prod,
        "vUnTrib",
        unitario(
            item.valor_unitario_tributavel
            if item.valor_unitario_tributavel is not None
            else item.valor_unitario
        ),
    )
    if item.valor_desconto and Decimal(item.valor_desconto) > 0:
        _elemento(prod, "vDesc", dinheiro(item.valor_desconto))
    _elemento(prod, "indTot", 1)

    imposto = _elemento(no, "imposto")
    _montar_icms(imposto, item, crt)
    _montar_contribuicao(imposto, item, "PIS")
    _montar_contribuicao(imposto, item, "COFINS")
    if item.informacoes_adicionais:
        _elemento(no, "infAdProd", _texto_fiscal(item.informacoes_adicionais, maximo=500))


def _montar_total(pai, dados: DocumentoDados) -> None:
    total = _elemento(pai, "total")
    icms_total = _elemento(total, "ICMSTot")
    _elemento(icms_total, "vBC", dinheiro(dados.total_base_icms))
    _elemento(icms_total, "vICMS", dinheiro(dados.total_icms))
    _elemento(icms_total, "vICMSDeson", dinheiro(0))
    _elemento(icms_total, "vFCP", dinheiro(0))
    _elemento(icms_total, "vBCST", dinheiro(0))
    _elemento(icms_total, "vST", dinheiro(0))
    _elemento(icms_total, "vFCPST", dinheiro(0))
    _elemento(icms_total, "vFCPSTRet", dinheiro(0))
    _elemento(icms_total, "vProd", dinheiro(dados.total_produtos))
    _elemento(icms_total, "vFrete", dinheiro(0))
    _elemento(icms_total, "vSeg", dinheiro(0))
    _elemento(icms_total, "vDesc", dinheiro(dados.total_desconto))
    _elemento(icms_total, "vII", dinheiro(0))
    _elemento(icms_total, "vIPI", dinheiro(0))
    _elemento(icms_total, "vIPIDevol", dinheiro(0))
    _elemento(icms_total, "vPIS", dinheiro(dados.total_pis))
    _elemento(icms_total, "vCOFINS", dinheiro(dados.total_cofins))
    _elemento(icms_total, "vOutro", dinheiro(0))
    _elemento(icms_total, "vNF", dinheiro(dados.total_documento))


def _montar_pagamento(pai, pagamento: PagamentoFiscal) -> None:
    detalhe = _elemento(pai, "detPag")
    _elemento(detalhe, "indPag", int(pagamento.indicador))
    forma = (pagamento.forma or "").strip()
    if forma not in FORMAS_PAGAMENTO:
        raise XmlFiscalErro(f"Forma de pagamento fiscal desconhecida: {forma!r}.")
    _elemento(detalhe, "tPag", forma)
    _elemento(detalhe, "vPag", dinheiro(pagamento.valor))
    # O grupo de pagamento eletronico tambem e exigido para tPag=17.
    if forma in {"03", "04", "17"}:
        cartao = _elemento(detalhe, "card")
        _elemento(cartao, "tpIntegra", int(pagamento.tipo_integracao))
        if int(pagamento.tipo_integracao) == 1:
            _opcional(cartao, "CNPJ", somente_digitos(pagamento.cnpj_credenciadora))
            _opcional(cartao, "tBand", pagamento.bandeira)
            _opcional(cartao, "cAut", pagamento.numero_autorizacao)


def _montar_pagamentos(pai, dados: DocumentoDados) -> None:
    grupo = _elemento(pai, "pag")
    if not dados.pagamentos:
        raise XmlFiscalErro("O documento fiscal precisa de ao menos um pagamento.")
    for pagamento in dados.pagamentos:
        _montar_pagamento(grupo, pagamento)
    if Decimal(dados.troco or 0) > 0:
        _elemento(grupo, "vTroco", dinheiro(dados.troco))


def montar_nfe(dados: DocumentoDados) -> etree._Element:
    """Monta o elemento ``<NFe>`` completo, ainda sem assinatura."""
    chave = validar_chave(dados.chave)
    if not dados.itens:
        raise XmlFiscalErro("O documento fiscal precisa de ao menos um item.")
    if len(dados.itens) > 990:
        raise XmlFiscalErro("O leiaute 4.00 aceita no máximo 990 itens por documento.")

    raiz = etree.Element(qualificar("NFe"), nsmap=NSMAP)
    inf = etree.SubElement(raiz, qualificar("infNFe"))
    inf.set("versao", VERSAO_LEIAUTE)
    inf.set("Id", f"NFe{chave}")

    _montar_ide(inf, dados)
    _montar_emitente(inf, dados)
    _montar_destinatario(inf, dados)
    for item in dados.itens:
        _montar_item(
            inf,
            item,
            int(dados.emitente.crt),
            modelo=int(dados.modelo),
            ambiente=int(dados.ambiente),
        )
    _montar_total(inf, dados)

    transporte = _elemento(inf, "transp")
    _elemento(transporte, "modFrete", 9)  # 9 = sem ocorrência de transporte

    _montar_pagamentos(inf, dados)

    complemento = _texto_fiscal(dados.informacoes_complementares, maximo=5000)
    if complemento:
        adicionais = _elemento(inf, "infAdic")
        _elemento(adicionais, "infCpl", complemento)

    if dados.responsavel_tecnico is not None:
        responsavel = _elemento(inf, "infRespTec")
        _elemento(responsavel, "CNPJ", somente_digitos(dados.responsavel_tecnico.cnpj))
        _elemento(responsavel, "xContato", _texto_fiscal(dados.responsavel_tecnico.contato, maximo=60))
        _elemento(responsavel, "email", _texto_fiscal(dados.responsavel_tecnico.email, maximo=60))
        _elemento(responsavel, "fone", somente_digitos(dados.responsavel_tecnico.telefone))

    return raiz


def serializar(elemento: etree._Element) -> bytes:
    """Serializa sem quebras de linha nem espaços — exigência da assinatura."""
    return etree.tostring(elemento, encoding="utf-8", xml_declaration=False)


def montar_xml(dados: DocumentoDados) -> bytes:
    return serializar(montar_nfe(dados))


def caminho_ns(*nomes: str) -> str:
    """Monta um caminho de busca qualificado: caminho_ns("infNFe", "ide")."""
    return "/".join(qualificar(nome) for nome in nomes)


def encontrar(raiz: etree._Element, *nomes: str) -> etree._Element | None:
    return raiz.find(caminho_ns(*nomes))


def texto_de(raiz: etree._Element, *nomes: str) -> str | None:
    no = encontrar(raiz, *nomes)
    return no.text if no is not None else None


def incluir_suplemento(xml: bytes, *, qrcode: str, url_chave: str) -> bytes:
    """Insere infNFeSupl sem alterar infNFe, que é o elemento assinado."""
    if not qrcode or not url_chave:
        raise XmlFiscalErro("NFC-e precisa de QR Code e URL de consulta por chave.")
    raiz = etree.fromstring(xml)
    if raiz.tag != qualificar("NFe"):
        raise XmlFiscalErro("XML de envio precisa ter raiz NFe.")
    if raiz.find(qualificar("infNFeSupl")) is not None:
        raise XmlFiscalErro("O XML já contém informações suplementares.")
    suplemento = etree.Element(qualificar("infNFeSupl"))
    _elemento(suplemento, "qrCode", qrcode)
    _elemento(suplemento, "urlChave", url_chave)
    raiz.insert(1, suplemento)
    return serializar(raiz)
