"""Contrato comum dos provedores de emissão fiscal.

Um provedor é o que efetivamente leva o documento até a SEFAZ. O restante do
sistema (caixa, DANFE, relatórios) não sabe qual está em uso: fala sempre com
esta interface. Trocar de rota é mudar um campo na configuração do
estabelecimento, não reescrever o caixa.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


# Códigos de status da SEFAZ que representam autorização efetiva.
STATUS_AUTORIZADO = {"100", "150"}
STATUS_CANCELADO = {"101", "135", "155"}
STATUS_INUTILIZADO = {"102", "563"}
STATUS_DUPLICIDADE = {"204", "539"}
# Rejeições em que o número não pode ser reaproveitado e a nota precisa ser
# reemitida com número novo (a numeração fica com lacuna, a inutilizar depois).
STATUS_DENEGADO = {"110", "301", "302", "303"}


class ProvedorFiscalErro(RuntimeError):
    """Falha de comunicação ou de configuração — não é rejeição da SEFAZ."""


class ProvedorIndisponivel(ProvedorFiscalErro):
    """O provedor não respondeu. A venda deve seguir para contingência."""


@dataclass(slots=True)
class RespostaFiscal:
    """Resultado de uma operação fiscal, seja ela aceita ou rejeitada."""

    sucesso: bool
    codigo_status: str
    mensagem: str
    protocolo: str | None = None
    chave: str | None = None
    recebido_em: datetime | None = None
    xml_enviado: bytes | None = None
    xml_retorno: bytes | None = None
    xml_autorizado: bytes | None = None
    digest_value: str | None = None
    sem_valor_fiscal: bool = False
    detalhes: dict = field(default_factory=dict)

    @property
    def duplicidade(self) -> bool:
        return self.codigo_status in STATUS_DUPLICIDADE

    @property
    def denegado(self) -> bool:
        return self.codigo_status in STATUS_DENEGADO


class ProvedorFiscal(ABC):
    """Interface que todo provedor precisa cumprir."""

    #: identificador gravado na configuração do estabelecimento
    nome: str = "base"
    #: rótulo mostrado nas telas
    rotulo: str = "Provedor base"
    #: quando False, o documento emitido NÃO tem valor fiscal
    produz_documento_fiscal: bool = False
    #: quando True, o XML precisa chegar já assinado com o certificado A1
    exige_assinatura_local: bool = True

    def __init__(self, configuracao: dict) -> None:
        self.configuracao = configuracao or {}

    @abstractmethod
    def autorizar(self, *, dados, xml_assinado: bytes) -> RespostaFiscal:
        """Transmite o documento e devolve o resultado da autorização."""

    @abstractmethod
    def cancelar(
        self,
        *,
        chave: str,
        protocolo: str,
        justificativa: str,
        cnpj: str,
        ambiente: int,
        sequencia: int = 1,
    ) -> RespostaFiscal:
        """Registra o evento de cancelamento do documento."""

    @abstractmethod
    def inutilizar(
        self,
        *,
        cnpj: str,
        serie: int,
        numero_inicial: int,
        numero_final: int,
        justificativa: str,
        ambiente: int,
        modelo: int,
        codigo_uf: int,
        ano: int,
    ) -> RespostaFiscal:
        """Inutiliza uma faixa de numeração não utilizada."""

    def conferir_configuracao(self) -> list[str]:
        """Lista pendências que impedem a emissão. Lista vazia = pronto."""
        return []

    def consultar_chave(self, *, chave: str, ambiente: int) -> RespostaFiscal:
        """Consulta o protocolo da NF-e/NFC-e pela chave.

        Provedores que não implementam uma consulta confiável devem falhar
        explicitamente: uma resposta inventada aqui poderia provocar reenvio
        duplicado após timeout.
        """
        raise ProvedorFiscalErro(
            "Este provedor não implementa consulta de protocolo por chave."
        )

    def status_servico(self) -> RespostaFiscal:
        """Consulta opcional de disponibilidade. Provedores podem não ter."""
        return RespostaFiscal(
            sucesso=True,
            codigo_status="000",
            mensagem="Este provedor não expõe consulta de status.",
        )


def justificativa_valida(texto: str | None) -> str:
    """A SEFAZ exige justificativa de 15 a 255 caracteres em cancelamento e inutilização."""
    normalizada = " ".join((texto or "").split())
    if not 15 <= len(normalizada) <= 255:
        raise ProvedorFiscalErro(
            "A justificativa precisa ter entre 15 e 255 caracteres."
        )
    return normalizada
