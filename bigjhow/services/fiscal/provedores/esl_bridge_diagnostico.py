"""Provedor ESL Bridge somente para diagnóstico — Fase 0.5.

IMPORTANTE:
- não emite NFC-e;
- não cancela NFC-e;
- não inutiliza numeração;
- não altera venda;
- não altera estoque;
- não altera financeiro;
- não grava no banco.

Este provedor existe exclusivamente para testar a comunicação entre
o Big Jhow e o Fiscal Bridge antes da implementação da emissão real.
"""

from __future__ import annotations

from ..esl_bridge_cliente import EslBridgeCliente, EslBridgeErro
from .base import ProvedorFiscal, ProvedorFiscalErro, RespostaFiscal


class EslBridgeDiagnostico(ProvedorFiscal):
    nome = "esl_bridge_diagnostico"
    rotulo = "ESL Bridge — diagnóstico somente leitura"

    produz_documento_fiscal = False
    exige_assinatura_local = False

    _CAPACIDADES_MUTAVEIS = (
        "emitir_nfce",
        "cancelar_nfce",
        "inutilizar_nfce",
    )

    def _cliente(self) -> EslBridgeCliente:
        return EslBridgeCliente()

    def _estado(self) -> tuple[dict, dict]:
        try:
            cliente = self._cliente()
            status = cliente.status()
            capacidades = cliente.capacidades()
        except EslBridgeErro as exc:
            raise ProvedorFiscalErro(
                f"Falha ao consultar Fiscal Bridge: {exc}"
            ) from exc

        return status, capacidades

    def conferir_configuracao(self) -> list[str]:
        pendencias: list[str] = []

        try:
            status, capacidades = self._estado()
        except ProvedorFiscalErro as exc:
            return [str(exc)]

        if status.get("modo") != "diagnostic":
            pendencias.append(
                "Fiscal Bridge não está em modo diagnostic."
            )

        if status.get("emissao_habilitada") is not False:
            pendencias.append(
                "Fiscal Bridge informa emissão habilitada; "
                "Fase 0.5 recusada."
            )

        if status.get("cancelamento_habilitado") is not False:
            pendencias.append(
                "Fiscal Bridge informa cancelamento habilitado; "
                "Fase 0.5 recusada."
            )

        if status.get("inutilizacao_habilitada") is not False:
            pendencias.append(
                "Fiscal Bridge informa inutilização habilitada; "
                "Fase 0.5 recusada."
            )

        for capacidade in self._CAPACIDADES_MUTAVEIS:
            if capacidades.get(capacidade) is not False:
                pendencias.append(
                    f"Capacidade inesperadamente habilitada: {capacidade}."
                )

        return pendencias

    def status_servico(self) -> RespostaFiscal:
        status, capacidades = self._estado()

        pendencias = self.conferir_configuracao()
        seguro = not pendencias

        return RespostaFiscal(
            sucesso=seguro,
            codigo_status="000" if seguro else "998",
            mensagem=(
                "Fiscal Bridge acessível em modo diagnóstico."
                if seguro
                else "Fiscal Bridge recusado pela barreira da Fase 0.5."
            ),
            sem_valor_fiscal=True,
            detalhes={
                "bridge": {
                    "servico": status.get("servico"),
                    "versao": status.get("versao"),
                    "modo": status.get("modo"),
                    "somente_loopback": status.get(
                        "somente_loopback"
                    ),
                },
                "capacidades": {
                    "diagnostico": capacidades.get("diagnostico"),
                    "emitir_nfce": capacidades.get("emitir_nfce"),
                    "consultar_nfce": capacidades.get(
                        "consultar_nfce"
                    ),
                    "cancelar_nfce": capacidades.get(
                        "cancelar_nfce"
                    ),
                    "inutilizar_nfce": capacidades.get(
                        "inutilizar_nfce"
                    ),
                },
                "pendencias": pendencias,
            },
        )

    @staticmethod
    def _operacao_bloqueada(operacao: str):
        raise ProvedorFiscalErro(
            f"Operação {operacao!r} bloqueada: "
            "EslBridgeDiagnostico é somente leitura."
        )

    def autorizar(self, *, dados, xml_assinado: bytes) -> RespostaFiscal:
        return self._operacao_bloqueada("autorizar")

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
        return self._operacao_bloqueada("cancelar")

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
        return self._operacao_bloqueada("inutilizar")
