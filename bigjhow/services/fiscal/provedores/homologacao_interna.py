"""Provedor de ensaio: exercita o fluxo inteiro sem falar com a SEFAZ.

Serve para conferir cadastro fiscal, montagem do XML, numeração, DANFE e
impressão antes de o certificado chegar. O documento produzido NÃO tem valor
fiscal, e isso é dito em todos os lugares onde ele aparece: a resposta vem
com ``sem_valor_fiscal=True``, o status é ``999`` (fora da faixa da SEFAZ) e
o DANFE sai carimbado.

Este provedor recusa-se a operar em ambiente de produção.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from lxml import etree

from ..chave import validar_chave
from ..xml_nfce import NAMESPACE, qualificar
from .base import ProvedorFiscal, ProvedorFiscalErro, RespostaFiscal, justificativa_valida


AVISO = "ENSAIO INTERNO — DOCUMENTO SEM VALOR FISCAL, NÃO TRANSMITIDO À SEFAZ"


class HomologacaoInterna(ProvedorFiscal):
    nome = "homologacao_interna"
    rotulo = "Ensaio interno (sem valor fiscal)"
    produz_documento_fiscal = False
    # Não há certificado no ensaio: o XML segue sem assinatura.
    exige_assinatura_local = False

    def _recusar_producao(self, ambiente: int) -> None:
        if int(ambiente) == 1:
            raise ProvedorFiscalErro(
                "O provedor de ensaio não pode ser usado em produção: ele não "
                "transmite à SEFAZ e o documento não tem valor fiscal."
            )

    def conferir_configuracao(self) -> list[str]:
        # Nada aqui impede a emissão: o ensaio não precisa de certificado, CSC
        # nem endereço de SEFAZ. O alerta de "sem valor fiscal" é dado pela
        # camada de cadastro, como aviso, e carimbado em cada documento.
        return []

    def _protocolo(self, semente: str) -> str:
        return "999" + hashlib.sha256(semente.encode("utf-8")).hexdigest()[:12].upper()

    def autorizar(self, *, dados, xml_assinado: bytes) -> RespostaFiscal:
        self._recusar_producao(dados.ambiente)
        chave = validar_chave(dados.chave)
        agora = datetime.now(timezone.utc).astimezone()
        protocolo = self._protocolo(chave)

        nfe = etree.fromstring(xml_assinado)
        processo = etree.Element(qualificar("nfeProc"), nsmap={None: NAMESPACE})
        processo.set("versao", "4.00")
        processo.append(nfe)
        simulado = etree.SubElement(processo, qualificar("protNFe"))
        simulado.set("versao", "4.00")
        info = etree.SubElement(simulado, qualificar("infProt"))
        etree.SubElement(info, qualificar("chNFe")).text = chave
        etree.SubElement(info, qualificar("nProt")).text = protocolo
        etree.SubElement(info, qualificar("cStat")).text = "999"
        etree.SubElement(info, qualificar("xMotivo")).text = AVISO

        return RespostaFiscal(
            sucesso=True,
            codigo_status="999",
            mensagem=AVISO,
            protocolo=protocolo,
            chave=chave,
            recebido_em=agora,
            xml_enviado=xml_assinado,
            xml_autorizado=etree.tostring(processo, encoding="utf-8", xml_declaration=True),
            sem_valor_fiscal=True,
        )

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
        self._recusar_producao(ambiente)
        justificativa_valida(justificativa)
        return RespostaFiscal(
            sucesso=True,
            codigo_status="999",
            mensagem=AVISO,
            protocolo=self._protocolo(f"cancelamento:{chave}"),
            chave=validar_chave(chave),
            recebido_em=datetime.now(timezone.utc).astimezone(),
            sem_valor_fiscal=True,
        )

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
        self._recusar_producao(ambiente)
        justificativa_valida(justificativa)
        return RespostaFiscal(
            sucesso=True,
            codigo_status="999",
            mensagem=AVISO,
            protocolo=self._protocolo(f"inutilizacao:{cnpj}:{serie}:{numero_inicial}"),
            recebido_em=datetime.now(timezone.utc).astimezone(),
            sem_valor_fiscal=True,
        )
