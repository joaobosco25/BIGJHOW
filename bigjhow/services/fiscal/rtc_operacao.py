"""
Roteador RTC do Big Jhow Carnes.

Homologação:
    ambiente 2 / série 9 -> rtc_ensaio existente.

Produção:
    ambiente 1 / série 1 -> motor rtc_produtos explicitamente
    habilitado para produção controlada.

Este módulo NÃO abre o catálogo de produção e NÃO transmite notas.
"""

from urllib.parse import urlsplit

from . import rtc_ensaio
from . import rtc_produtos
from .xml_nfce import XmlFiscalErro


N = "http://www.portalfiscal.inf.br/nfe"


def q(name):
    return f"{{{N}}}{name}"


def text(node, path):
    return node.findtext(
        "/".join(q(p) for p in path.split("/")),
        default="",
    )


def selecionar(estabelecimento, dados=None):

    # Produção controlada Big Jhow Carnes.
    if (
        int(estabelecimento.empresa_id) == 1
        and int(estabelecimento.ambiente) == 1
    ):
        if estabelecimento.provedor != "sefaz_direto":
            raise XmlFiscalErro(
                "Produção fiscal exige provedor sefaz_direto."
            )

        contexto = (
            int(estabelecimento.modelo),
            int(estabelecimento.serie),
            int(estabelecimento.crt),
            estabelecimento.uf,
        )

        if contexto != (65, 1, 3, "MG"):
            raise XmlFiscalErro(
                "RTC de produção exige NFC-e 65, série 1, "
                "CRT 3 e MG."
            )

        if dados is not None:
            contexto_dados = (
                int(dados.ambiente),
                int(dados.modelo),
                int(dados.serie),
                int(dados.tipo_emissao),
            )

            if contexto_dados != (1, 65, 1, 1):
                raise XmlFiscalErro(
                    "Documento diverge do perfil RTC de produção."
                )

        return True

    # Todo o comportamento anterior de homologação permanece aqui.
    return rtc_ensaio.selecionar(
        estabelecimento,
        dados,
    )


def conferir_configuracao(configuracao):

    ambiente = int(configuracao.get("ambiente", 0))

    if ambiente == 2:
        return rtc_ensaio.conferir_configuracao(
            configuracao
        )

    if ambiente != 1:
        raise XmlFiscalErro(
            "Ambiente fiscal RTC inválido."
        )

    # Neste momento validamos segurança estrutural.
    # Os endpoints oficiais exatos serão conferidos antes do go-live.
    for nome in (
        "url_autorizacao",
        "url_consulta",
    ):
        valor = str(configuracao.get(nome) or "").strip()
        u = urlsplit(valor)

        if (
            u.scheme != "https"
            or not u.hostname
            or u.username
            or u.password
            or u.query
            or u.fragment
        ):
            raise XmlFiscalErro(
                f"Endpoint de produção inválido: {nome}"
            )

        # Nunca permitir que produção use o host de homologação.
        if u.hostname.lower() == "hnfce.fazenda.mg.gov.br":
            raise XmlFiscalErro(
                f"Produção aponta para endpoint de homologação: {nome}"
            )


def aplicar(root, perfis):

    inf = root.find(q("infNFe"))

    if inf is None:
        raise XmlFiscalErro("infNFe ausente.")

    ambiente = text(inf, "ide/tpAmb")

    if ambiente == "2":
        return rtc_ensaio.aplicar(
            root,
            perfis=perfis,
        )

    if ambiente != "1":
        raise XmlFiscalErro(
            "Ambiente RTC não suportado."
        )

    if (
        text(inf, "ide/mod") != "65"
        or text(inf, "ide/serie") != "1"
        or text(inf, "ide/tpEmis") != "1"
        or text(inf, "ide/cUF") != "31"
        or text(inf, "emit/CRT") != "3"
    ):
        raise XmlFiscalErro(
            "XML fora do perfil RTC de produção controlada."
        )

    return rtc_produtos.aplicar(
        root,
        perfis,
        ambiente="1",
    )


def conferir(raw):
    # XSD e integridade XMLDSig já validados pelo mecanismo existente.
    return rtc_ensaio.conferir(raw)
