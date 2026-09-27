"""DANFE NFC-e: o cupom que o cliente leva.

Não é o documento fiscal — o documento é o XML autorizado. O DANFE é a
representação impressa dele, e precisa trazer, por norma: identificação do
emitente, itens, totais, forma de pagamento, chave de acesso legível, número,
série, data, protocolo de autorização e o QR Code de consulta.
"""

from __future__ import annotations

from decimal import Decimal

from ...models import DocumentoFiscal, EstabelecimentoFiscal
from .chave import formatar_chave
from .qrcode_svg import QRCodeErro, qrcode_svg


AVISO_HOMOLOGACAO = (
    "EMITIDA EM AMBIENTE DE HOMOLOGAÇÃO — SEM VALOR FISCAL"
)
AVISO_ENSAIO = "ENSAIO INTERNO — SEM VALOR FISCAL, NÃO TRANSMITIDA À SEFAZ"
AVISO_CONTINGENCIA = (
    "EMITIDA EM CONTINGÊNCIA — AGUARDANDO AUTORIZAÇÃO DA SEFAZ"
)

ROTULO_FORMA = {
    "01": "Dinheiro",
    "02": "Cheque",
    "03": "Cartão de crédito",
    "04": "Cartão de débito",
    "05": "Crédito da loja",
    "10": "Vale alimentação",
    "11": "Vale refeição",
    "12": "Vale presente",
    "13": "Vale combustível",
    "15": "Boleto",
    "16": "Depósito bancário",
    "17": "PIX",
    "18": "Transferência bancária",
    "19": "Programa de fidelidade",
    "90": "Sem pagamento",
    "99": "Outros",
}


def aviso_do_documento(documento: DocumentoFiscal) -> str | None:
    """Frase de destaque no topo do cupom, quando ele não vale como fiscal."""
    if documento.sem_valor_fiscal:
        return AVISO_ENSAIO
    if int(documento.ambiente) == 2:
        return AVISO_HOMOLOGACAO
    if documento.status == "contingencia":
        return AVISO_CONTINGENCIA
    if documento.status == "cancelado":
        return "DOCUMENTO CANCELADO"
    if documento.status != "autorizado":
        return "DOCUMENTO AINDA NÃO AUTORIZADO"
    return None


def qrcode_do_documento(documento: DocumentoFiscal) -> str | None:
    """SVG do QR Code, ou None quando ainda não há conteúdo para codificar."""
    conteudo = (documento.qrcode_conteudo or "").strip()
    if not conteudo:
        return None
    try:
        return qrcode_svg(conteudo, tamanho_alvo=200)
    except QRCodeErro:
        return None


def montar_danfe(
    documento: DocumentoFiscal, estabelecimento: EstabelecimentoFiscal | None
) -> dict:
    """Reúne tudo que o cupom precisa mostrar, já formatado."""
    itens = [
        {
            "numero": item.numero_item,
            "codigo": item.codigo_produto,
            "descricao": item.descricao,
            "quantidade": str(Decimal(item.quantidade).normalize()),
            "unidade": item.unidade,
            "valor_unitario": f"{Decimal(item.valor_unitario):.2f}",
            "valor_total": f"{Decimal(item.valor_total):.2f}",
        }
        for item in documento.itens
    ]
    pagamentos = [
        {
            "forma": ROTULO_FORMA.get(pagamento.forma, pagamento.forma),
            "valor": f"{Decimal(pagamento.valor):.2f}",
        }
        for pagamento in documento.pagamentos
    ]
    venda = documento.venda
    if documento.documento_destinatario:
        digitos = documento.documento_destinatario
        if len(digitos) == 11:
            consumidor = f"CPF {digitos[:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:]}"
        else:
            consumidor = f"CNPJ {digitos}"
        if documento.nome_destinatario:
            consumidor = f"{consumidor} — {documento.nome_destinatario}"
    else:
        consumidor = "CONSUMIDOR NÃO IDENTIFICADO"

    return {
        "documento": {65: "DANFE NFC-e", 55: "DANFE NF-e", 59: "CF-e SAT"}.get(int(documento.modelo), "Documento fiscal"),
        "aviso": aviso_do_documento(documento),
        "consumidor": consumidor,
        "operador": (
            venda.operador.nome if venda is not None and venda.operador else None
        ),
        "terminal": (
            venda.turno.terminal.codigo
            if venda is not None and venda.turno is not None and venda.turno.terminal
            else None
        ),
        "venda": venda.numero_formatado if venda is not None else None,
        "emitente": {
            "razao_social": estabelecimento.razao_social if estabelecimento else "",
            "nome_fantasia": estabelecimento.nome_fantasia if estabelecimento else "",
            "cnpj": estabelecimento.cnpj_formatado if estabelecimento else "",
            "inscricao_estadual": estabelecimento.inscricao_estadual if estabelecimento else "",
            "endereco": (
                f"{estabelecimento.logradouro}, {estabelecimento.numero} — "
                f"{estabelecimento.bairro}, {estabelecimento.municipio}/{estabelecimento.uf}"
                if estabelecimento
                else ""
            ),
        },
        "numero": documento.numero_formatado,
        "serie": documento.serie,
        "modelo": documento.modelo,
        "emissao": documento.data_emissao.isoformat() if documento.data_emissao else None,
        "autorizacao": (
            documento.data_autorizacao.isoformat() if documento.data_autorizacao else None
        ),
        "protocolo": documento.protocolo_autorizacao,
        "chave": documento.chave,
        "chave_formatada": formatar_chave(documento.chave),
        "ambiente": int(documento.ambiente),
        "status": documento.status,
        "itens": itens,
        "quantidade_itens": len(itens),
        "total_produtos": f"{Decimal(documento.total_produtos):.2f}",
        "total_desconto": f"{Decimal(documento.total_desconto):.2f}",
        "total": f"{Decimal(documento.total_documento):.2f}",
        "troco": f"{Decimal(documento.troco):.2f}",
        "pagamentos": pagamentos,
        "qrcode_conteudo": documento.qrcode_conteudo,
        "url_consulta": (
            estabelecimento.url_consulta_chave if estabelecimento else None
        ),
        "tem_valor_fiscal": documento.vale_como_documento_fiscal
        and int(documento.ambiente) == 1,
    }
