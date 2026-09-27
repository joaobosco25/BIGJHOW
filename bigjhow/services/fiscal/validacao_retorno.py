"""Conferência de identidade e protocolo; não substitui validação XSD/assinatura."""
from lxml import etree
from .provedores.base import ProvedorIndisponivel, STATUS_AUTORIZADO
from .xml_nfce import qualificar


def validar_autorizacao(resposta, dados):
    # Uma resposta inconsistente pode corresponder a autorização real: pendente,
    # nunca emitir outro número só porque não foi possível reconciliar o retorno.
    def falha():
        raise ProvedorIndisponivel(
            "Retorno de autorização inconsistente. Consulte a chave antes de reenviar."
        )

    if not resposta.xml_autorizado or not resposta.protocolo or resposta.chave != dados.chave:
        falha()
    try:
        raiz = etree.fromstring(resposta.xml_autorizado, etree.XMLParser(resolve_entities=False, no_network=True))
    except (etree.XMLSyntaxError, ValueError):
        falha()
    if raiz.tag != qualificar("nfeProc"):
        falha()
    info = raiz.find(f"{qualificar('NFe')}/{qualificar('infNFe')}")
    prot = raiz.find(f"{qualificar('protNFe')}/{qualificar('infProt')}")
    if info is None or prot is None or info.get('Id') != 'NFe' + dados.chave:
        falha()
    campos = {
        'chNFe': dados.chave,
        'nProt': resposta.protocolo,
        'tpAmb': str(dados.ambiente),
        'cStat': resposta.codigo_status,
    }
    if resposta.codigo_status not in STATUS_AUTORIZADO:
        falha()
    if any(prot.findtext(qualificar(k)) != v for k, v in campos.items()):
        falha()
    if info.findtext(f"{qualificar('ide')}/{qualificar('tpAmb')}") != str(dados.ambiente):
        falha()
