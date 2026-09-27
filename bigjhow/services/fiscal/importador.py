"""Importação do acervo fiscal do servidor local para o sistema novo.

Os caminhos de leitura abaixo são exatamente os que o
``TPA.Centralizador.Business`` usava na ``IntegrationTask`` — foram extraídos
do binário do sistema antigo e mantidos ao pé da letra, para que o histórico
já processado lá seja lido aqui do mesmo jeito:

    nfeProc        //nfeProc/protNFe/infProt/chNFe
                   //nfeProc/NFe/infNFe/emit/CNPJ
                   //nfeProc/NFe/infNFe/ide/cDV
                   //nfeProc/NFe/infNFe/emit/enderEmit/cMun
                   //nfeProc/NFe/infNFe/ide/cUF

    procEventoNFe  //procEventoNFe/retEvento/infEvento/chNFe
                   //procEventoNFe/retEvento/infEvento/nProt
                   //procEventoNFe/retEvento/infEvento/dhRegEvento

    ProcInutNFe    //ProcInutNFe/inutNFe/infInut/@Id
                   //ProcInutNFe/retInutNFe/infInut/nProt
                   //ProcInutNFe/retInutNFe/infInut/mod
                   //ProcInutNFe/retInutNFe/infInut/cUF
                   //ProcInutNFe/retInutNFe/infInut/CNPJ
                   //ProcInutNFe/retInutNFe/infInut/serie
                   //ProcInutNFe/retInutNFe/infInut/nNFIni
                   //ProcInutNFe/retInutNFe/infInut/dhRecbto

    CFeCanc        cancelamento de CF-e SAT (modelo 59)

O XML da SEFAZ vem com namespace; o sistema antigo consultava sem prefixo, o
que indica que ele removia o namespace antes. Aqui a árvore é normalizada da
mesma forma, e por isso os caminhos acima funcionam literalmente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from lxml import etree

from ...extensions import db
from ...models import (
    DocumentoFiscal,
    DocumentoFiscalItem,
    DocumentoFiscalPagamento,
    DocumentoFiscalXml,
    InutilizacaoFiscal,
    utcnow,
)
from .chave import ChaveInvalida, somente_digitos, validar_chave


LIMITE_ARQUIVO = 8 * 1024 * 1024  # 8 MB: XML fiscal legítimo não passa disso


class ImportacaoErro(ValueError):
    pass


@dataclass(slots=True)
class ResultadoImportacao:
    importados: int = 0
    atualizados: int = 0
    ignorados: int = 0
    inutilizacoes: int = 0
    cancelamentos: int = 0
    erros: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.importados + self.atualizados + self.ignorados


def remover_namespace(raiz: etree._Element) -> etree._Element:
    """Deixa a árvore sem namespace, como o integrador antigo enxergava."""
    for elemento in raiz.iter():
        if isinstance(elemento.tag, str) and "}" in elemento.tag:
            elemento.tag = elemento.tag.split("}", 1)[1]
        for atributo in list(elemento.attrib):
            if "}" in atributo:
                elemento.attrib[atributo.split("}", 1)[1]] = elemento.attrib.pop(atributo)
    etree.cleanup_namespaces(raiz)
    return raiz


def analisar(conteudo: bytes) -> etree._Element:
    """Lê o XML com as proteções ligadas (sem entidades externas nem DTD)."""
    if len(conteudo) > LIMITE_ARQUIVO:
        raise ImportacaoErro("Arquivo XML grande demais para ser um documento fiscal.")
    leitor = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )
    try:
        raiz = etree.fromstring(conteudo, parser=leitor)
    except etree.XMLSyntaxError as exc:
        raise ImportacaoErro(f"XML inválido: {exc}") from exc
    return remover_namespace(raiz)


def _texto(raiz: etree._Element, caminho: str) -> str | None:
    no = raiz.find(caminho)
    if no is None or no.text is None:
        return None
    valor = no.text.strip()
    return valor or None


def _decimal(raiz: etree._Element, caminho: str) -> Decimal:
    valor = _texto(raiz, caminho)
    if not valor:
        return Decimal("0")
    try:
        return Decimal(valor)
    except Exception:
        return Decimal("0")


def _data(valor: str | None) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        return None


def identificar(raiz: etree._Element) -> str:
    """Diz qual dos formatos conhecidos do integrador antigo é o arquivo."""
    return {
        "nfeProc": "autorizacao",
        "procEventoNFe": "cancelamento",
        "ProcInutNFe": "inutilizacao",
        "CFeCanc": "cancelamento_sat",
        "CFe": "autorizacao_sat",
        "TpaInformacoes": "tpa",
    }.get(raiz.tag, "desconhecido")


# --------------------------------------------------------------------------
# nfeProc — nota autorizada
# --------------------------------------------------------------------------


def importar_autorizacao(
    raiz: etree._Element, *, empresa_id: int, conteudo: bytes
) -> tuple[DocumentoFiscal, bool]:
    chave_texto = _texto(raiz, "protNFe/infProt/chNFe")
    if not chave_texto:
        raise ImportacaoErro("O arquivo não traz a chave de acesso (protNFe/infProt/chNFe).")
    try:
        chave = validar_chave(chave_texto)
    except ChaveInvalida as exc:
        raise ImportacaoErro(str(exc)) from exc

    existente = db.session.scalar(
        db.select(DocumentoFiscal).where(DocumentoFiscal.chave == chave)
    )
    if existente is not None:
        # Mesma decisão do sistema antigo: "A nota fiscal {0} já foi integrada."
        return existente, False

    inf = raiz.find("NFe/infNFe")
    if inf is None:
        raise ImportacaoErro("O arquivo não traz o grupo infNFe.")
    ide = inf.find("ide")
    protocolo = raiz.find("protNFe/infProt")
    if ide is None or protocolo is None:
        raise ImportacaoErro("O arquivo não traz ide ou protNFe completos.")

    emissao = _data(_texto(ide, "dhEmi")) or utcnow()
    codigo_status = _texto(protocolo, "cStat") or "100"
    documento = DocumentoFiscal(
        empresa_id=empresa_id,
        chave=chave,
        codigo_numerico=int(_texto(ide, "cNF") or 0),
        modelo=int(_texto(ide, "mod") or 65),
        serie=int(_texto(ide, "serie") or 0),
        numero=int(_texto(ide, "nNF") or 0),
        ambiente=int(_texto(ide, "tpAmb") or 2),
        tipo_emissao=int(_texto(ide, "tpEmis") or 1),
        cnpj_emitente=somente_digitos(_texto(inf, "emit/CNPJ")) or chave[6:20],
        documento_destinatario=(
            somente_digitos(_texto(inf, "dest/CPF") or _texto(inf, "dest/CNPJ")) or None
        ),
        nome_destinatario=_texto(inf, "dest/xNome"),
        status="autorizado" if codigo_status in {"100", "150"} else "rejeitado",
        codigo_status=codigo_status,
        mensagem_status=_texto(protocolo, "xMotivo"),
        protocolo_autorizacao=_texto(protocolo, "nProt"),
        total_produtos=_decimal(inf, "total/ICMSTot/vProd"),
        total_desconto=_decimal(inf, "total/ICMSTot/vDesc"),
        total_documento=_decimal(inf, "total/ICMSTot/vNF"),
        total_base_icms=_decimal(inf, "total/ICMSTot/vBC"),
        total_icms=_decimal(inf, "total/ICMSTot/vICMS"),
        total_pis=_decimal(inf, "total/ICMSTot/vPIS"),
        total_cofins=_decimal(inf, "total/ICMSTot/vCOFINS"),
        troco=_decimal(inf, "pag/vTroco"),
        digest_value=_texto(raiz, "NFe/Signature/SignedInfo/Reference/DigestValue"),
        provedor="importacao_legado",
        data_emissao=emissao,
        data_autorizacao=_data(_texto(protocolo, "dhRecbto")),
    )
    db.session.add(documento)
    db.session.flush()

    for elemento in inf.findall("det"):
        try:
            numero_item = int(elemento.get("nItem") or 0)
        except ValueError:
            continue
        produto = elemento.find("prod")
        if produto is None:
            continue
        icms = elemento.find("imposto/ICMS")
        cst = csosn = None
        origem = 0
        base = aliq = valor = Decimal("0")
        if icms is not None and len(icms):
            grupo = icms[0]
            origem = int(_texto(grupo, "orig") or 0)
            cst = _texto(grupo, "CST")
            csosn = _texto(grupo, "CSOSN")
            base = _decimal(grupo, "vBC")
            aliq = _decimal(grupo, "pICMS")
            valor = _decimal(grupo, "vICMS")
        db.session.add(
            DocumentoFiscalItem(
                documento_id=documento.id,
                numero_item=numero_item,
                codigo_produto=(_texto(produto, "cProd") or "")[:60],
                codigo_ean=(
                    None
                    if (_texto(produto, "cEAN") or "SEM GTIN").upper() == "SEM GTIN"
                    else _texto(produto, "cEAN")
                ),
                descricao=(_texto(produto, "xProd") or "")[:120],
                ncm=(somente_digitos(_texto(produto, "NCM")) or "00000000")[:8],
                cest=somente_digitos(_texto(produto, "CEST")) or None,
                cfop=(somente_digitos(_texto(produto, "CFOP")) or "5102")[:4],
                unidade=(_texto(produto, "uCom") or "UN")[:6],
                quantidade=_decimal(produto, "qCom") or Decimal("0.0001"),
                valor_unitario=_decimal(produto, "vUnCom"),
                valor_total=_decimal(produto, "vProd"),
                valor_desconto=_decimal(produto, "vDesc"),
                icms_origem=origem,
                icms_cst=cst,
                icms_csosn=csosn,
                icms_base_calculo=base,
                icms_aliquota=aliq,
                icms_valor=valor,
                pis_cst=(_texto(elemento, "imposto/PIS/PISNT/CST")
                         or _texto(elemento, "imposto/PIS/PISAliq/CST")
                         or _texto(elemento, "imposto/PIS/PISOutr/CST") or "07")[:2],
                cofins_cst=(_texto(elemento, "imposto/COFINS/COFINSNT/CST")
                            or _texto(elemento, "imposto/COFINS/COFINSAliq/CST")
                            or _texto(elemento, "imposto/COFINS/COFINSOutr/CST") or "07")[:2],
            )
        )

    for detalhe in inf.findall("pag/detPag"):
        valor_pago = _decimal(detalhe, "vPag")
        if valor_pago <= 0:
            continue
        db.session.add(
            DocumentoFiscalPagamento(
                documento_id=documento.id,
                forma=(_texto(detalhe, "tPag") or "99")[:2],
                valor=valor_pago,
                cnpj_credenciadora=somente_digitos(_texto(detalhe, "card/CNPJ")) or None,
                bandeira=_texto(detalhe, "card/tBand"),
                numero_autorizacao=_texto(detalhe, "card/cAut"),
            )
        )

    db.session.add(
        DocumentoFiscalXml(
            documento_id=documento.id,
            tipo="autorizado",
            arquivo_xml=conteudo.decode("utf-8", errors="replace"),
        )
    )
    db.session.flush()
    return documento, True


# --------------------------------------------------------------------------
# procEventoNFe — cancelamento
# --------------------------------------------------------------------------


def importar_cancelamento(raiz: etree._Element, *, conteudo: bytes) -> DocumentoFiscal:
    chave_texto = _texto(raiz, "retEvento/infEvento/chNFe")
    if not chave_texto:
        raise ImportacaoErro("O evento não traz a chave de acesso.")
    chave = validar_chave(chave_texto)
    documento = db.session.scalar(
        db.select(DocumentoFiscal).where(DocumentoFiscal.chave == chave)
    )
    if documento is None:
        # Mesma mensagem do integrador antigo.
        raise ImportacaoErro("Nota fiscal inexistente")
    documento.status = "cancelado"
    documento.protocolo_cancelamento = _texto(raiz, "retEvento/infEvento/nProt")
    documento.data_cancelamento = _data(_texto(raiz, "retEvento/infEvento/dhRegEvento"))
    documento.motivo_cancelamento = _texto(raiz, "evento/infEvento/detEvento/xJust")
    documento.versao += 1
    existente = db.session.scalar(
        db.select(DocumentoFiscalXml).where(
            DocumentoFiscalXml.documento_id == documento.id,
            DocumentoFiscalXml.tipo == "cancelamento",
        )
    )
    if existente is None:
        db.session.add(
            DocumentoFiscalXml(
                documento_id=documento.id,
                tipo="cancelamento",
                arquivo_xml=conteudo.decode("utf-8", errors="replace"),
            )
        )
    db.session.flush()
    return documento


# --------------------------------------------------------------------------
# ProcInutNFe — inutilização
# --------------------------------------------------------------------------


def importar_inutilizacao(
    raiz: etree._Element, *, empresa_id: int, conteudo: bytes
) -> tuple[InutilizacaoFiscal | None, bool]:
    info = raiz.find("retInutNFe/infInut")
    if info is None:
        raise ImportacaoErro("O arquivo de inutilização não traz retInutNFe/infInut.")
    cnpj = somente_digitos(_texto(info, "CNPJ"))
    serie = int(_texto(info, "serie") or 0)
    modelo = int(_texto(info, "mod") or 65)
    ambiente = int(
        _texto(info, "tpAmb") or _texto(raiz, "inutNFe/infInut/tpAmb") or 2
    )
    numero_inicial = int(_texto(info, "nNFIni") or 0)
    numero_final = int(_texto(info, "nNFFin") or numero_inicial)
    protocolo = _texto(info, "nProt")
    recebimento = _data(_texto(info, "dhRecbto"))
    if not cnpj or not numero_inicial:
        raise ImportacaoErro("Inutilização sem CNPJ ou sem numeração.")

    criados = 0
    ultimo: InutilizacaoFiscal | None = None
    for numero in range(numero_inicial, numero_final + 1):
        registro = db.session.scalar(
            db.select(InutilizacaoFiscal).where(
                InutilizacaoFiscal.cnpj == cnpj,
                InutilizacaoFiscal.modelo == modelo,
                InutilizacaoFiscal.ambiente == ambiente,
                InutilizacaoFiscal.serie == serie,
                InutilizacaoFiscal.numero == numero,
            )
        )
        if registro is None:
            registro = InutilizacaoFiscal(
                empresa_id=empresa_id,
                cnpj=cnpj,
                modelo=modelo,
                ambiente=ambiente,
                serie=serie,
                numero=numero,
                origem="importacao_legado",
            )
            db.session.add(registro)
            criados += 1
        registro.status = "autorizada"
        registro.protocolo = protocolo
        registro.codigo_status = _texto(info, "cStat")
        registro.mensagem = _texto(info, "xMotivo")
        registro.justificativa = _texto(raiz, "inutNFe/infInut/xJust")
        registro.resolvido_em = recebimento or utcnow()
        registro.arquivo_xml = conteudo.decode("utf-8", errors="replace")
        ultimo = registro
    db.session.flush()
    return ultimo, criados > 0


# --------------------------------------------------------------------------
# Entrada única
# --------------------------------------------------------------------------


def importar_conteudo(
    conteudo: bytes, *, empresa_id: int, resultado: ResultadoImportacao
) -> None:
    raiz = analisar(conteudo)
    formato = identificar(raiz)
    if formato == "autorizacao":
        _, novo = importar_autorizacao(raiz, empresa_id=empresa_id, conteudo=conteudo)
        if novo:
            resultado.importados += 1
        else:
            resultado.ignorados += 1
    elif formato == "cancelamento":
        importar_cancelamento(raiz, conteudo=conteudo)
        resultado.cancelamentos += 1
        resultado.atualizados += 1
    elif formato == "inutilizacao":
        _, novo = importar_inutilizacao(raiz, empresa_id=empresa_id, conteudo=conteudo)
        resultado.inutilizacoes += 1
        if not novo:
            resultado.ignorados += 1
    elif formato in {"autorizacao_sat", "cancelamento_sat"}:
        raise ImportacaoErro(
            "Arquivo de CF-e SAT (modelo 59) reconhecido, mas a importação do "
            "acervo SAT ainda não está habilitada. Guarde o arquivo."
        )
    elif formato == "tpa":
        resultado.ignorados += 1
    else:
        raise ImportacaoErro(f"Formato de arquivo não reconhecido: <{raiz.tag}>.")


def importar_pasta(caminho: str, *, empresa_id: int) -> ResultadoImportacao:
    """Varre uma pasta de XML, como fazia a ``IntegrationTask`` do sistema antigo."""
    pasta = Path(caminho)
    resultado = ResultadoImportacao()
    if not pasta.is_dir():
        resultado.erros.append(f"A pasta {caminho} não existe.")
        return resultado
    for arquivo in sorted(pasta.glob("*.xml")):
        try:
            importar_conteudo(
                arquivo.read_bytes(), empresa_id=empresa_id, resultado=resultado
            )
        except (ImportacaoErro, ChaveInvalida, OSError) as exc:
            resultado.erros.append(f"{arquivo.name}: {exc}")
    return resultado
