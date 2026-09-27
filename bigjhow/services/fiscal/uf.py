"""Unidades federativas, autorizadores e endereços de serviço.

Decisão de projeto: os endereços dos serviços da SEFAZ e a URL de consulta
do QR Code NÃO vêm com valor padrão embutido. Eles mudam com o tempo e variam
por UF; um endereço errado gravado no código faria a loja emitir contra o
lugar errado sem aviso. Aqui eles são campos de configuração obrigatórios do
estabelecimento, e o sistema recusa mudar para o ambiente de produção enquanto
não forem preenchidos e marcados como conferidos.

O arquivo ``docs/FISCAL.md`` indica onde obter cada endereço.
"""

from __future__ import annotations


# Código do IBGE de cada UF (dois primeiros dígitos da chave de acesso).
CODIGO_UF: dict[str, int] = {
    "AC": 12, "AL": 27, "AM": 13, "AP": 16, "BA": 29, "CE": 23, "DF": 53,
    "ES": 32, "GO": 52, "MA": 21, "MG": 31, "MS": 50, "MT": 51, "PA": 15,
    "PB": 25, "PE": 26, "PI": 22, "PR": 41, "RJ": 33, "RN": 24, "RO": 11,
    "RR": 14, "RS": 43, "SC": 42, "SE": 28, "SP": 35, "TO": 17,
}

UF_POR_CODIGO: dict[int, str] = {codigo: sigla for sigla, codigo in CODIGO_UF.items()}

# UFs que operam autorizador próprio de NFC-e. As demais são atendidas pela
# SEFAZ Virtual do Rio Grande do Sul (SVRS). Usado apenas para orientar quem
# está configurando: o endereço em si continua sendo digitado.
AUTORIZADOR_PROPRIO_NFCE = frozenset(
    {"AM", "BA", "CE", "GO", "MG", "MS", "MT", "PE", "PR", "RS", "SP"}
)

AMBIENTE_PRODUCAO = 1
AMBIENTE_HOMOLOGACAO = 2

AMBIENTES = {
    AMBIENTE_PRODUCAO: "Produção",
    AMBIENTE_HOMOLOGACAO: "Homologação",
}

MODELO_NFCE = 65
MODELO_NFE = 55
MODELO_CFE_SAT = 59

MODELOS = {
    MODELO_NFCE: "NFC-e (modelo 65)",
    MODELO_NFE: "NF-e (modelo 55)",
    MODELO_CFE_SAT: "CF-e SAT (modelo 59)",
}

# Tipos de emissão do campo tpEmis (MOC 7.0).
TIPO_EMISSAO_NORMAL = 1
TIPO_EMISSAO_CONTINGENCIA_OFFLINE = 9

TIPOS_EMISSAO = {
    TIPO_EMISSAO_NORMAL: "Normal",
    TIPO_EMISSAO_CONTINGENCIA_OFFLINE: "Contingência off-line (NFC-e)",
}

# Código de Regime Tributário (CRT) do emitente.
CRT_SIMPLES_NACIONAL = 1
CRT_SIMPLES_EXCESSO = 2
CRT_REGIME_NORMAL = 3
CRT_MEI = 4

REGIMES = {
    CRT_SIMPLES_NACIONAL: "Simples Nacional",
    CRT_SIMPLES_EXCESSO: "Simples Nacional — excesso de sublimite",
    CRT_REGIME_NORMAL: "Regime normal",
    CRT_MEI: "Simples Nacional — MEI",
}


def sigla_valida(sigla: str | None) -> str:
    normalizada = (sigla or "").strip().upper()
    if normalizada not in CODIGO_UF:
        raise ValueError("UF inválida.")
    return normalizada


def codigo_da_uf(sigla: str | None) -> int:
    return CODIGO_UF[sigla_valida(sigla)]


def usa_autorizador_proprio(sigla: str | None) -> bool:
    return sigla_valida(sigla) in AUTORIZADOR_PROPRIO_NFCE


def orientacao_autorizador(sigla: str | None) -> str:
    uf = sigla_valida(sigla)
    if uf in AUTORIZADOR_PROPRIO_NFCE:
        return f"{uf} opera autorizador próprio de NFC-e. Use os endereços publicados pela SEFAZ-{uf}."
    return (
        f"{uf} é atendida pela SEFAZ Virtual do RS (SVRS) para NFC-e. "
        "Use os endereços publicados pela SVRS."
    )
