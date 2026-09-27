"""Chave de acesso de 44 dígitos (NF-e / NFC-e) e dígito verificador módulo 11.

Portado do comportamento do sistema legado TPA Centralizador, onde a chave
chegava pronta dentro do XML autorizado (``//nfeProc/protNFe/infProt/chNFe``).
Aqui ela passa a ser montada pelo próprio sistema, porque o novo emissor
precisa gerar o XML antes de transmitir.

Layout oficial (NT 2015/002, Manual de Orientação do Contribuinte 7.0):

    cUF(2) AAMM(4) CNPJ(14) mod(2) serie(3) nNF(9) tpEmis(1) cNF(8) cDV(1)
"""

from __future__ import annotations

import re
from datetime import datetime


DIGITOS = re.compile(r"^\d+$")

# Peso cíclico 2..9 aplicado da direita para a esquerda (MOC 7.0, item 4.3).
_PESOS = (2, 3, 4, 5, 6, 7, 8, 9)


class ChaveInvalida(ValueError):
    pass


def somente_digitos(valor: str | None) -> str:
    return re.sub(r"\D", "", valor or "")


def digito_verificador(chave43: str) -> int:
    """Calcula o cDV (módulo 11) dos 43 primeiros dígitos da chave."""
    if len(chave43) != 43 or not DIGITOS.fullmatch(chave43):
        raise ChaveInvalida("O cálculo do DV exige exatamente 43 dígitos.")
    soma = 0
    for posicao, caractere in enumerate(reversed(chave43)):
        soma += int(caractere) * _PESOS[posicao % 8]
    resto = soma % 11
    return 0 if resto in (0, 1) else 11 - resto


def montar_chave(
    *,
    codigo_uf: int,
    emissao: datetime,
    cnpj: str,
    modelo: int,
    serie: int,
    numero: int,
    tipo_emissao: int,
    codigo_numerico: int,
) -> str:
    """Monta a chave completa de 44 dígitos, já com o dígito verificador."""
    cnpj_limpo = somente_digitos(cnpj)
    if len(cnpj_limpo) != 14:
        raise ChaveInvalida("O CNPJ do emitente precisa ter 14 dígitos.")
    if not 11 <= int(codigo_uf) <= 53:
        raise ChaveInvalida("Código de UF fora da faixa do IBGE.")
    if int(modelo) not in (55, 65, 59):
        raise ChaveInvalida("Modelo de documento não suportado (use 55, 65 ou 59).")
    if not 0 <= int(serie) <= 999:
        raise ChaveInvalida("A série precisa estar entre 0 e 999.")
    if not 1 <= int(numero) <= 999_999_999:
        raise ChaveInvalida("O número do documento precisa estar entre 1 e 999999999.")
    if int(tipo_emissao) not in (1, 2, 3, 4, 5, 6, 7, 9):
        raise ChaveInvalida("Tipo de emissão inválido.")
    if not 0 <= int(codigo_numerico) <= 99_999_999:
        raise ChaveInvalida("O código numérico (cNF) precisa ter até 8 dígitos.")

    corpo = (
        f"{int(codigo_uf):02d}"
        f"{emissao:%y%m}"
        f"{cnpj_limpo}"
        f"{int(modelo):02d}"
        f"{int(serie):03d}"
        f"{int(numero):09d}"
        f"{int(tipo_emissao):01d}"
        f"{int(codigo_numerico):08d}"
    )
    if len(corpo) != 43:
        raise ChaveInvalida("Falha ao montar a chave: corpo diferente de 43 dígitos.")
    return corpo + str(digito_verificador(corpo))


def validar_chave(chave: str) -> str:
    """Confere tamanho e DV. Devolve a chave normalizada."""
    limpa = somente_digitos(chave)
    if len(limpa) != 44:
        raise ChaveInvalida("A chave de acesso precisa ter 44 dígitos.")
    if digito_verificador(limpa[:43]) != int(limpa[43]):
        raise ChaveInvalida("Dígito verificador da chave de acesso não confere.")
    return limpa


def decompor_chave(chave: str) -> dict:
    """Explode a chave nos campos que a compõem (usado na tela de conferência)."""
    limpa = validar_chave(chave)
    return {
        "codigo_uf": int(limpa[0:2]),
        "ano": int(limpa[2:4]),
        "mes": int(limpa[4:6]),
        "cnpj": limpa[6:20],
        "modelo": int(limpa[20:22]),
        "serie": int(limpa[22:25]),
        "numero": int(limpa[25:34]),
        "tipo_emissao": int(limpa[34:35]),
        "codigo_numerico": int(limpa[35:43]),
        "digito_verificador": int(limpa[43]),
    }


def formatar_chave(chave: str) -> str:
    """Agrupa a chave de 4 em 4 dígitos, como impresso no DANFE NFC-e."""
    limpa = somente_digitos(chave)
    return " ".join(limpa[i : i + 4] for i in range(0, len(limpa), 4))


def validar_cnpj(valor: str | None) -> str:
    """Valida CNPJ pelo módulo 11 (dois dígitos)."""
    cnpj = somente_digitos(valor)
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        raise ChaveInvalida("CNPJ inválido.")
    for tamanho in (12, 13):
        # Pesos 2..9 ciclicos, da direita para a esquerda: 5,4,3,2,9,8,... (RFB).
        soma = sum(
            int(cnpj[tamanho - 1 - posicao]) * (2 + posicao % 8)
            for posicao in range(tamanho)
        )
        resto = soma % 11
        digito = 0 if resto < 2 else 11 - resto
        if int(cnpj[tamanho]) != digito:
            raise ChaveInvalida("CNPJ inválido.")
    return cnpj


def validar_cpf(valor: str | None) -> str:
    cpf = somente_digitos(valor)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        raise ChaveInvalida("CPF inválido.")
    for tamanho in (9, 10):
        soma = sum(int(cpf[i]) * (tamanho + 1 - i) for i in range(tamanho))
        resto = (soma * 10) % 11
        digito = 0 if resto == 10 else resto
        if int(cpf[tamanho]) != digito:
            raise ChaveInvalida("CPF inválido.")
    return cpf
