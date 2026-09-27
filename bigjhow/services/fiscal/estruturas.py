"""Estruturas de dados do documento fiscal, independentes de banco e de Flask.

Os nomes de campo espelham as entidades do sistema legado
(``TPA.Centralizador.Entity``: DocumentoFiscal, DocumentoItem,
DocumentoPagamento), para que a importação do histórico e a emissão nova
falem a mesma linguagem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP


CENTAVO = Decimal("0.01")
QUATRO = Decimal("0.0001")
TRES = Decimal("0.001")


def dinheiro(valor) -> str:
    return f"{Decimal(valor or 0).quantize(CENTAVO, rounding=ROUND_HALF_UP):.2f}"


def quantidade(valor) -> str:
    return f"{Decimal(valor or 0).quantize(QUATRO, rounding=ROUND_HALF_UP):.4f}"


def unitario(valor) -> str:
    """Valor unitário aceita até dez casas decimais no leiaute 4.00."""
    return f"{Decimal(valor or 0):.10f}"


def aliquota(valor) -> str:
    return f"{Decimal(valor or 0).quantize(CENTAVO, rounding=ROUND_HALF_UP):.2f}"


def data_hora_fiscal(momento: datetime) -> str:
    """Formato xs:dateTime com fuso separado por dois-pontos (2026-09-10T14:32:05-03:00).

    O ``%z`` do strftime devolve ``-0300``, sem os dois-pontos, e o schema da
    SEFAZ recusa esse formato. Este utilitário evita a armadilha.
    """
    if momento.tzinfo is None:
        raise ValueError("A data de emissão precisa ter fuso horário definido.")
    return momento.replace(microsecond=0).isoformat()


@dataclass(slots=True)
class Endereco:
    logradouro: str
    numero: str
    bairro: str
    codigo_municipio: int
    municipio: str
    uf: str
    cep: str
    complemento: str | None = None
    telefone: str | None = None


@dataclass(slots=True)
class Emitente:
    cnpj: str
    razao_social: str
    nome_fantasia: str | None
    inscricao_estadual: str
    endereco: Endereco
    crt: int
    inscricao_municipal: str | None = None


@dataclass(slots=True)
class Destinatario:
    """Consumidor. Na NFC-e é opcional enquanto o total ficar no limite da UF."""

    cpf: str | None = None
    cnpj: str | None = None
    nome: str | None = None
    indicador_ie: int = 9  # 9 = não contribuinte


@dataclass(slots=True)
class TributoICMS:
    origem: int = 0
    cst: str | None = None  # regime normal
    csosn: str | None = None  # Simples Nacional
    modalidade_bc: int = 3  # 3 = valor da operação
    base_calculo: Decimal = Decimal("0")
    aliquota: Decimal = Decimal("0")
    valor: Decimal = Decimal("0")
    percentual_reducao_bc: Decimal | None = None
    base_calculo_st_retido: Decimal | None = None
    valor_st_retido: Decimal | None = None
    aliquota_st_consumidor: Decimal | None = None
    percentual_credito_sn: Decimal | None = None
    valor_credito_sn: Decimal | None = None
    motivo_desoneracao: int | None = None
    valor_desonerado: Decimal = Decimal("0")


@dataclass(slots=True)
class TributoContribuicao:
    """Serve tanto para PIS quanto para COFINS: os grupos têm a mesma forma."""

    cst: str = "07"
    base_calculo: Decimal = Decimal("0")
    aliquota: Decimal = Decimal("0")
    valor: Decimal = Decimal("0")


@dataclass(slots=True)
class ItemFiscal:
    numero: int
    codigo_produto: str
    descricao: str
    ncm: str
    cfop: str
    unidade_comercial: str
    quantidade_comercial: Decimal
    valor_unitario: Decimal
    valor_total: Decimal
    ean: str | None = None
    ean_tributavel: str | None = None
    unidade_tributavel: str | None = None
    quantidade_tributavel: Decimal | None = None
    valor_unitario_tributavel: Decimal | None = None
    cest: str | None = None
    valor_desconto: Decimal = Decimal("0")
    icms: TributoICMS = field(default_factory=TributoICMS)
    pis: TributoContribuicao = field(default_factory=TributoContribuicao)
    cofins: TributoContribuicao = field(default_factory=TributoContribuicao)
    informacoes_adicionais: str | None = None
    rtc_perfil: dict | None = None


@dataclass(slots=True)
class PagamentoFiscal:
    """Espelha DocumentoPagamento do sistema antigo."""

    forma: str  # código tPag do leiaute 4.00
    valor: Decimal
    indicador: int = 0  # 0 = à vista
    cnpj_credenciadora: str | None = None
    bandeira: str | None = None
    numero_autorizacao: str | None = None
    tipo_integracao: int = 2  # 2 = pagamento não integrado ao sistema de automação


@dataclass(slots=True)
class ResponsavelTecnico:
    cnpj: str
    contato: str
    email: str
    telefone: str


@dataclass(slots=True)
class DocumentoDados:
    chave: str
    codigo_numerico: int
    modelo: int
    serie: int
    numero: int
    emissao: datetime
    ambiente: int
    tipo_emissao: int
    natureza_operacao: str
    emitente: Emitente
    itens: list[ItemFiscal]
    pagamentos: list[PagamentoFiscal]
    destinatario: Destinatario | None = None
    responsavel_tecnico: ResponsavelTecnico | None = None
    troco: Decimal = Decimal("0")
    informacoes_complementares: str | None = None
    versao_aplicativo: str = "BigJhow 5"

    @property
    def total_produtos(self) -> Decimal:
        return sum((item.valor_total for item in self.itens), Decimal("0")).quantize(CENTAVO)

    @property
    def total_desconto(self) -> Decimal:
        return sum((item.valor_desconto for item in self.itens), Decimal("0")).quantize(CENTAVO)

    @property
    def total_base_icms(self) -> Decimal:
        return sum((item.icms.base_calculo for item in self.itens), Decimal("0")).quantize(CENTAVO)

    @property
    def total_icms(self) -> Decimal:
        return sum((item.icms.valor for item in self.itens), Decimal("0")).quantize(CENTAVO)

    @property
    def total_pis(self) -> Decimal:
        return sum((item.pis.valor for item in self.itens), Decimal("0")).quantize(CENTAVO)

    @property
    def total_cofins(self) -> Decimal:
        return sum((item.cofins.valor for item in self.itens), Decimal("0")).quantize(CENTAVO)

    @property
    def total_documento(self) -> Decimal:
        return (self.total_produtos - self.total_desconto).quantize(CENTAVO)
