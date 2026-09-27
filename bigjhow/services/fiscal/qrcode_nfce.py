"""Conteúdo do QR Code v3 da NFC-e.

Emissão online (tpEmis=1):
    <URL>?p=chNFe|3|tpAmb

A contingência offline v3 permanece bloqueada até ser
implementada e validada contra o schema oficial vigente.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal

from .chave import validar_chave
from .estruturas import data_hora_fiscal


VERSAO_QRCODE = 3

CAMPOS_CONTINGENCIA = ("chNFe", "nVersao", "tpAmb", "dhEmi", "vNF", "digVal", "cIdToken")


class QRCodeFiscalErro(ValueError):
    pass


def _hexadecimal(texto: str) -> str:
    return texto.encode("utf-8").hex().upper()


def _valor(numero: Decimal | str) -> str:
    return f"{Decimal(numero):.2f}"


def montar_parametros(
    *,
    chave: str,
    ambiente: int,
    id_token: str | None = None,
    contingencia: bool = False,
    emissao: datetime | None = None,
    valor_total: Decimal | str | None = None,
    digest_value: str | None = None,
) -> str:
    """Monta os parâmetros do QR Code v3 da NFC-e.

    No modo online (tpEmis=1), o leiaute vigente é:
        chNFe|3|tpAmb

    A contingência offline v3 permanece bloqueada até sua
    implementação e validação completas.
    """
    chave_normalizada = validar_chave(chave)

    if ambiente not in (1, 2):
        raise QRCodeFiscalErro(
            "Ambiente do QR Code precisa ser 1 (produção) "
            "ou 2 (homologação)."
        )

    if contingencia:
        raise QRCodeFiscalErro(
            "QR Code v3 em contingência offline ainda não "
            "está habilitado no Big Jhow."
        )

    return f"{chave_normalizada}|{VERSAO_QRCODE}|{ambiente}"

def calcular_hash(parametros: str, csc: str) -> str:
    """SHA-1 dos parâmetros concatenados ao CSC, em hexadecimal maiúsculo."""
    if not csc:
        raise QRCodeFiscalErro("O CSC (Código de Segurança do Contribuinte) não está configurado.")
    return hashlib.sha1(f"{parametros}{csc}".encode("utf-8")).hexdigest().upper()


def montar_qrcode(
    *,
    url_consulta_qrcode: str,
    chave: str,
    ambiente: int,
    id_token: str | None = None,
    csc: str | None = None,
    contingencia: bool = False,
    emissao: datetime | None = None,
    valor_total: Decimal | str | None = None,
    digest_value: str | None = None,
) -> str:
    """Devolve a URL do QR Code v3 da NFC-e.

    Na emissão online o QR Code v3 não utiliza CSC,
    idCSC nem SHA-1.
    """
    url = (url_consulta_qrcode or "").strip()

    if not url.startswith("https://"):
        raise QRCodeFiscalErro(
            "A URL de consulta do QR Code precisa estar "
            "configurada e usar HTTPS."
        )

    parametros = montar_parametros(
        chave=chave,
        ambiente=ambiente,
        id_token=id_token,
        contingencia=contingencia,
        emissao=emissao,
        valor_total=valor_total,
        digest_value=digest_value,
    )

    separador = "&" if "?" in url else "?"

    return f"{url}{separador}p={parametros}"
