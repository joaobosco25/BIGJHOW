"""
Catálogo fiscal de produção controlada do Big Jhow Carnes.

Este módulo NÃO habilita produção.
Ele apenas carrega e valida o catálogo de produção de forma fail-closed.

Enquanto:
    habilitado != True
ou:
    producao_bloqueada != False

nenhum catálogo de produção é entregue ao runtime.
"""

import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from flask import current_app

from . import legado_homologacao
from .rtc_produtos import money, validar_perfil
from .xml_nfce import XmlFiscalErro


CNPJ = "11222333000181"
FILENAME = "fiscal_legado_producao.json"


def _sem_chaves_repetidas(pares):
    data = {}

    for key, value in pares:
        if key in data:
            raise ValueError("Chave JSON duplicada.")
        data[key] = value

    return data


def carregar(estabelecimento):
    """
    Retorna catálogo de produção somente quando TODAS as travas estiverem abertas.

    Fora da Empresa 1 ou fora do ambiente de produção, retorna None
    para não interferir em homologação ou outras empresas.
    """

    if int(estabelecimento.empresa_id) != 1:
        return None

    if int(estabelecimento.ambiente) != 1:
        return None

    path = Path(current_app.instance_path) / FILENAME

    if not path.exists():
        raise XmlFiscalErro(
            "Catálogo fiscal de produção não encontrado."
        )

    try:
        data = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_sem_chaves_repetidas,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise XmlFiscalErro(
            "Catálogo fiscal de produção ilegível."
        ) from exc

    identidade_catalogo = (
        data.get("versao"),
        data.get("tipo_catalogo"),
        data.get("empresa_id"),
        data.get("cnpj"),
        data.get("ambiente"),
        data.get("serie"),
    )

    esperado_catalogo = (
        1,
        "PRODUCAO_CONTROLADA",
        1,
        CNPJ,
        1,
        1,
    )

    if identidade_catalogo != esperado_catalogo:
        raise XmlFiscalErro(
            "Identificação do catálogo fiscal de produção divergente."
        )

    identidade_estabelecimento = (
        str(estabelecimento.cnpj),
        int(estabelecimento.ambiente),
        int(estabelecimento.modelo),
        int(estabelecimento.serie),
        int(estabelecimento.crt),
        estabelecimento.uf,
        estabelecimento.provedor,
    )

    esperado_estabelecimento = (
        CNPJ,
        1,
        65,
        1,
        3,
        "MG",
        "sefaz_direto",
    )

    if identidade_estabelecimento != esperado_estabelecimento:
        raise XmlFiscalErro(
            "Produção controlada exclusiva da Empresa 1, "
            "MG, CRT 3, NFC-e 65, série 1 e sefaz_direto."
        )

    if data.get("somente_homologacao") is not False:
        raise XmlFiscalErro(
            "Catálogo de produção marcado incorretamente como homologação."
        )

    # TRAVA GLOBAL PRINCIPAL.
    if data.get("habilitado") is not True:
        raise XmlFiscalErro(
            "Produção fiscal ainda não habilitada."
        )

    # TRAVA GLOBAL SECUNDÁRIA.
    if data.get("producao_bloqueada") is not False:
        raise XmlFiscalErro(
            "Produção fiscal permanece bloqueada."
        )

    produtos = data.get("produtos")

    if not isinstance(produtos, dict) or not produtos:
        raise XmlFiscalErro(
            "Catálogo de produção sem produtos candidatos."
        )

    codigos_xml = []

    for produto_id, entry in produtos.items():
        if not isinstance(entry, dict):
            raise XmlFiscalErro(
                f"Entrada inválida no catálogo de produção: {produto_id}"
            )

        perfil = entry.get("perfil")

        if not isinstance(perfil, dict):
            raise XmlFiscalErro(
                f"Perfil fiscal inválido no catálogo de produção: {produto_id}"
            )

        liberado = entry.get("liberado_producao")

        if liberado not in (True, False):
            raise XmlFiscalErro(
                f"Flag liberado_producao inválida: {produto_id}"
            )

        if liberado is True:
            if entry.get("revisao_fiscal_producao_pendente") is True:
                raise XmlFiscalErro(
                    f"Produto liberado com revisão fiscal pendente: {produto_id}"
                )

            validar_perfil(perfil)

            codigo_xml = str(perfil.get("codigo_xml") or "").strip()

            if not codigo_xml:
                raise XmlFiscalErro(
                    f"Produto liberado sem código XML: {produto_id}"
                )

            codigos_xml.append(codigo_xml)

    if len(codigos_xml) != len(set(codigos_xml)):
        raise XmlFiscalErro(
            "Produtos liberados possuem código XML duplicado."
        )

    return data


def produto_liberado(catalogo, produto_id):
    """
    Retorna a entrada somente quando o produto estiver explicitamente
    liberado para produção. Caso contrário, falha fechado.
    """

    entry = catalogo["produtos"].get(str(produto_id))

    if entry is None:
        raise XmlFiscalErro(
            "Produto não pertence ao catálogo fiscal de produção."
        )

    if entry.get("liberado_producao") is not True:
        raise XmlFiscalErro(
            "Produto ainda não liberado para produção fiscal."
        )

    if entry.get("revisao_fiscal_producao_pendente") is True:
        raise XmlFiscalErro(
            "Produto com revisão fiscal de produção pendente."
        )

    validar_perfil(entry["perfil"])

    return entry



def montar_item(numero, item, catalogo):
    """
    Builder fiscal da produção.

    Sem conversão explícita, preserva o builder validado.
    CXA -> UN somente quando o catálogo de produção traz
    fator comprovado explicitamente.
    """

    entry = produto_liberado(
        catalogo,
        item.produto_id,
    )

    fiscal = legado_homologacao.montar_item(
        numero,
        item,
        catalogo,
    )

    conversao = entry.get(
        "conversao_tributavel_producao"
    )

    if conversao is None:
        return fiscal

    if not isinstance(conversao, dict):
        raise XmlFiscalErro(
            "Conversão tributável de produção inválida."
        )

    if (
        conversao.get("unidade_comercial") != "CXA"
        or conversao.get("unidade_tributavel") != "UN"
    ):
        raise XmlFiscalErro(
            "Conversão tributável fora do escopo aprovado."
        )

    if item.unidade != "CXA":
        raise XmlFiscalErro(
            "Unidade comercial diferente da conversão aprovada."
        )

    fator = Decimal(
        str(conversao.get("fator") or "0")
    )

    if (
        fator <= 1
        or fator != fator.to_integral_value()
    ):
        raise XmlFiscalErro(
            "Fator tributável de produção inválido."
        )

    q_com = Decimal(
        str(fiscal.quantidade_comercial)
    )

    v_un_com = Decimal(
        str(fiscal.valor_unitario)
    )

    q_trib = q_com * fator

    v_un_trib = (
        v_un_com / fator
    ).quantize(
        Decimal("0.0000000001"),
        rounding=ROUND_HALF_UP,
    )

    if q_trib <= 0 or v_un_trib < 0:
        raise XmlFiscalErro(
            "Resultado de conversão tributável inválido."
        )

    if money(q_trib * v_un_trib) != money(
        fiscal.valor_total
    ):
        raise XmlFiscalErro(
            "Conversão tributável não preserva "
            "o valor fiscal do item."
        )

    fiscal.unidade_tributavel = "UN"
    fiscal.quantidade_tributavel = q_trib
    fiscal.valor_unitario_tributavel = v_un_trib

    return fiscal



def carregar_prevalidacao():
    """
    Carrega a allowlist de produção exclusivamente para impedir que
    produto não aprovado chegue ao pagamento/finalização.

    Esta função NÃO abre a produção fiscal. A transmissão real continua
    dependente de carregar(), que exige as duas travas globais abertas.
    """

    path = Path(current_app.instance_path) / FILENAME

    if not path.exists():
        raise XmlFiscalErro(
            "Catálogo fiscal de produção não encontrado."
        )

    try:
        data = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_sem_chaves_repetidas,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise XmlFiscalErro(
            "Catálogo fiscal de produção ilegível."
        ) from exc

    identidade = (
        data.get("versao"),
        data.get("tipo_catalogo"),
        data.get("empresa_id"),
        data.get("cnpj"),
        data.get("ambiente"),
        data.get("serie"),
    )

    esperado = (
        1,
        "PRODUCAO_CONTROLADA",
        1,
        CNPJ,
        1,
        1,
    )

    if identidade != esperado:
        raise XmlFiscalErro(
            "Identificação do catálogo fiscal "
            "de produção divergente."
        )

    if data.get("somente_homologacao") is not False:
        raise XmlFiscalErro(
            "Catálogo de produção marcado como homologação."
        )

    if data.get("habilitado") not in (True, False):
        raise XmlFiscalErro(
            "Flag global de habilitação inválida."
        )

    if data.get("producao_bloqueada") not in (True, False):
        raise XmlFiscalErro(
            "Flag global de bloqueio inválida."
        )

    produtos = data.get("produtos")

    if not isinstance(produtos, dict) or not produtos:
        raise XmlFiscalErro(
            "Catálogo de produção sem produtos."
        )

    codigos_liberados = []

    for produto_id, entry in produtos.items():

        if not isinstance(entry, dict):
            raise XmlFiscalErro(
                f"Entrada fiscal inválida: {produto_id}"
            )

        perfil = entry.get("perfil")

        if not isinstance(perfil, dict):
            raise XmlFiscalErro(
                f"Perfil fiscal inválido: {produto_id}"
            )

        liberado = entry.get("liberado_producao")

        if liberado not in (True, False):
            raise XmlFiscalErro(
                f"Flag de liberação inválida: {produto_id}"
            )

        if liberado is not True:
            continue

        if entry.get(
            "revisao_fiscal_producao_pendente"
        ) is True:
            raise XmlFiscalErro(
                f"Produto liberado com revisão pendente: "
                f"{produto_id}"
            )

        validar_perfil(perfil)

        codigo_xml = str(
            perfil.get("codigo_xml") or ""
        ).strip()

        if not codigo_xml:
            raise XmlFiscalErro(
                f"Produto liberado sem código fiscal: "
                f"{produto_id}"
            )

        codigos_liberados.append(codigo_xml)

        conversao = entry.get(
            "conversao_tributavel_producao"
        )

        if conversao is not None:

            if not isinstance(conversao, dict):
                raise XmlFiscalErro(
                    "Conversão tributável inválida."
                )

            if (
                conversao.get("unidade_comercial") != "CXA"
                or conversao.get("unidade_tributavel") != "UN"
            ):
                raise XmlFiscalErro(
                    "Conversão tributável fora do perfil aprovado."
                )

            try:
                fator = Decimal(
                    str(conversao.get("fator"))
                )
            except Exception as exc:
                raise XmlFiscalErro(
                    "Fator tributável inválido."
                ) from exc

            if (
                fator <= 1
                or fator != fator.to_integral_value()
            ):
                raise XmlFiscalErro(
                    "Fator tributável inválido."
                )

    if len(codigos_liberados) != len(
        set(codigos_liberados)
    ):
        raise XmlFiscalErro(
            "Produtos liberados possuem código fiscal duplicado."
        )

    return data


def prevalidar_produtos(produto_ids):
    """
    Fail-closed do caixa.

    Todos os produtos da venda devem pertencer explicitamente à
    allowlist de produção antes de o caixa processar pagamentos,
    baixar estoque ou finalizar a venda.
    """

    ids = list(
        dict.fromkeys(
            str(produto_id)
            for produto_id in produto_ids
        )
    )

    if not ids:
        raise XmlFiscalErro(
            "Venda sem produtos para pré-validação fiscal."
        )

    catalogo = carregar_prevalidacao()

    for produto_id in ids:
        produto_liberado(
            catalogo,
            produto_id,
        )

    return True
