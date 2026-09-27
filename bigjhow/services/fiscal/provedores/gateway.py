"""Emissão por gateway HTTP de terceiro (Focus NFe, Webmania, NFe.io e afins).

O gateway recebe a venda em JSON, cuida do certificado e da transmissão, e
devolve a chave, o protocolo e o XML autorizado. É a rota mais rápida de
colocar em pé quando o certificado ainda não está na mão.

O mapeamento de campos varia de fornecedor para fornecedor, então ele fica em
``mapa_campos`` na configuração, e não no código. O padrão embutido segue o
formato mais comum (Focus NFe). O token nunca vai para o banco: fica em
variável de ambiente do servidor.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime

from ..chave import somente_digitos, validar_chave
from ..estruturas import data_hora_fiscal, dinheiro, quantidade
from .base import (
    ProvedorFiscal,
    ProvedorFiscalErro,
    ProvedorIndisponivel,
    RespostaFiscal,
    justificativa_valida,
)


TEMPO_LIMITE = (10, 45)

# Caminhos, dentro do JSON de resposta, de onde ler cada informação.
MAPA_PADRAO = {
    "status": "status",
    "status_autorizado": "autorizado",
    "codigo": "status_sefaz",
    "mensagem": "mensagem_sefaz",
    "protocolo": "numero_protocolo",
    "chave": "chave_nfe",
    "xml": "caminho_xml_nota_fiscal",
    "xml_conteudo": "xml",
}


def _requests():
    try:
        import requests
    except ImportError as exc:  # pragma: no cover
        raise ProvedorFiscalErro(
            "A biblioteca 'requests' não está instalada. Instale o requirements.txt."
        ) from exc
    return requests


def _ler(dados: dict, caminho: str):
    """Lê um valor aninhado usando pontos: ``retorno.protocolo.numero``."""
    atual = dados
    for parte in (caminho or "").split("."):
        if not isinstance(atual, dict) or parte not in atual:
            return None
        atual = atual[parte]
    return atual


class GatewayHttp(ProvedorFiscal):
    nome = "gateway"
    rotulo = "Gateway de emissão (API de terceiro)"
    produz_documento_fiscal = True
    # O gateway guarda o certificado e assina do lado dele.
    exige_assinatura_local = False

    def conferir_configuracao(self) -> list[str]:
        pendencias: list[str] = []
        url = (self.configuracao.get("url_base") or "").strip()
        if not url:
            pendencias.append("Informe a URL base do gateway.")
        elif not url.startswith("https://"):
            pendencias.append("A URL do gateway precisa usar HTTPS.")
        if not (self.configuracao.get("token") or "").strip():
            pendencias.append("Informe o token de autenticação do gateway.")
        return pendencias

    def _mapa(self) -> dict:
        mapa = dict(MAPA_PADRAO)
        mapa.update(self.configuracao.get("mapa_campos") or {})
        return mapa

    def _cabecalhos(self) -> dict:
        token = (self.configuracao.get("token") or "").strip()
        if not token:
            raise ProvedorFiscalErro("O token do gateway não está configurado.")
        estilo = (self.configuracao.get("estilo_autenticacao") or "basic").lower()
        if estilo == "bearer":
            autorizacao = f"Bearer {token}"
        elif estilo == "token":
            autorizacao = f"Token {token}"
        else:
            codificado = base64.b64encode(f"{token}:".encode("utf-8")).decode("ascii")
            autorizacao = f"Basic {codificado}"
        return {
            "Authorization": autorizacao,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _chamar(self, metodo: str, caminho: str, corpo: dict | None = None) -> dict:
        requests = _requests()
        base = (self.configuracao.get("url_base") or "").strip().rstrip("/")
        if not base.startswith("https://"):
            raise ProvedorFiscalErro("A URL base do gateway não está configurada.")
        try:
            resposta = requests.request(
                metodo,
                f"{base}/{caminho.lstrip('/')}",
                headers=self._cabecalhos(),
                data=json.dumps(corpo) if corpo is not None else None,
                timeout=TEMPO_LIMITE,
            )
        except requests.Timeout as exc:
            raise ProvedorIndisponivel("O gateway não respondeu no tempo limite.") from exc
        except requests.RequestException as exc:
            raise ProvedorIndisponivel(f"Falha ao falar com o gateway: {exc}") from exc

        if resposta.status_code in (401, 403):
            raise ProvedorFiscalErro("O gateway recusou o token configurado.")
        if resposta.status_code >= 500:
            raise ProvedorIndisponivel(f"O gateway devolveu HTTP {resposta.status_code}.")
        try:
            return resposta.json()
        except ValueError as exc:
            raise ProvedorFiscalErro("O gateway devolveu uma resposta que não é JSON.") from exc

    def _montar_corpo(self, dados) -> dict:
        emitente = dados.emitente
        return {
            "natureza_operacao": dados.natureza_operacao,
            "data_emissao": data_hora_fiscal(dados.emissao),
            "modelo": int(dados.modelo),
            "serie": int(dados.serie),
            "numero": int(dados.numero),
            "tipo_documento": 1,
            "presenca_comprador": 1,
            "consumidor_final": 1,
            "ambiente": int(dados.ambiente),
            "cnpj_emitente": somente_digitos(emitente.cnpj),
            "nome_emitente": emitente.razao_social,
            "inscricao_estadual_emitente": somente_digitos(emitente.inscricao_estadual),
            "regime_tributario_emitente": int(emitente.crt),
            "cpf_destinatario": (
                somente_digitos(dados.destinatario.cpf)
                if dados.destinatario and dados.destinatario.cpf
                else None
            ),
            "valor_total": dinheiro(dados.total_documento),
            "valor_produtos": dinheiro(dados.total_produtos),
            "valor_desconto": dinheiro(dados.total_desconto),
            "valor_troco": dinheiro(dados.troco),
            "items": [
                {
                    "numero_item": item.numero,
                    "codigo_produto": item.codigo_produto,
                    "descricao": item.descricao,
                    "codigo_ncm": somente_digitos(item.ncm),
                    "cfop": somente_digitos(item.cfop),
                    "unidade_comercial": item.unidade_comercial,
                    "quantidade_comercial": quantidade(item.quantidade_comercial),
                    "valor_unitario_comercial": str(item.valor_unitario),
                    "valor_bruto": dinheiro(item.valor_total),
                    "codigo_barras_comercial": item.ean or None,
                    "cest": item.cest or None,
                    "origem": item.icms.origem,
                    "codigo_situacao_operacao_simples_nacional": item.icms.csosn,
                    "icms_situacao_tributaria": item.icms.cst,
                    "pis_situacao_tributaria": item.pis.cst,
                    "cofins_situacao_tributaria": item.cofins.cst,
                }
                for item in dados.itens
            ],
            "formas_pagamento": [
                {
                    "forma_pagamento": pagamento.forma,
                    "valor_pagamento": dinheiro(pagamento.valor),
                }
                for pagamento in dados.pagamentos
            ],
        }

    def autorizar(self, *, dados, xml_assinado: bytes) -> RespostaFiscal:
        mapa = self._mapa()
        caminho = (self.configuracao.get("caminho_emissao") or "v2/nfce").strip()
        referencia = f"{dados.emitente.cnpj}-{dados.serie}-{dados.numero}"
        retorno = self._chamar("POST", f"{caminho}?ref={referencia}", self._montar_corpo(dados))

        status = str(_ler(retorno, mapa["status"]) or "").lower()
        autorizado = status == str(mapa["status_autorizado"]).lower()
        chave = _ler(retorno, mapa["chave"])
        conteudo_xml = _ler(retorno, mapa["xml_conteudo"])
        return RespostaFiscal(
            sucesso=autorizado,
            codigo_status=str(_ler(retorno, mapa["codigo"]) or ("100" if autorizado else "000")),
            mensagem=str(_ler(retorno, mapa["mensagem"]) or status or "Sem mensagem."),
            protocolo=(
                str(_ler(retorno, mapa["protocolo"]))
                if _ler(retorno, mapa["protocolo"])
                else None
            ),
            chave=validar_chave(chave) if chave else dados.chave,
            recebido_em=datetime.now().astimezone(),
            xml_enviado=xml_assinado,
            xml_retorno=json.dumps(retorno, ensure_ascii=False).encode("utf-8"),
            xml_autorizado=(
                conteudo_xml.encode("utf-8") if isinstance(conteudo_xml, str) else None
            ),
            detalhes={"referencia": referencia},
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
        mapa = self._mapa()
        motivo = justificativa_valida(justificativa)
        caminho = (self.configuracao.get("caminho_cancelamento") or "v2/nfce").strip()
        retorno = self._chamar(
            "DELETE",
            f"{caminho}/{validar_chave(chave)}",
            {"justificativa": motivo},
        )
        status = str(_ler(retorno, mapa["status"]) or "").lower()
        return RespostaFiscal(
            sucesso=status == "cancelado",
            codigo_status=str(_ler(retorno, mapa["codigo"]) or "000"),
            mensagem=str(_ler(retorno, mapa["mensagem"]) or status or "Sem mensagem."),
            protocolo=(
                str(_ler(retorno, mapa["protocolo"]))
                if _ler(retorno, mapa["protocolo"])
                else None
            ),
            chave=chave,
            xml_retorno=json.dumps(retorno, ensure_ascii=False).encode("utf-8"),
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
        mapa = self._mapa()
        motivo = justificativa_valida(justificativa)
        caminho = (self.configuracao.get("caminho_inutilizacao") or "v2/nfce_inutilizacao").strip()
        retorno = self._chamar(
            "POST",
            caminho,
            {
                "cnpj": somente_digitos(cnpj),
                "modelo": int(modelo),
                "serie": int(serie),
                "numero_inicial": int(numero_inicial),
                "numero_final": int(numero_final),
                "justificativa": motivo,
            },
        )
        status = str(_ler(retorno, mapa["status"]) or "").lower()
        return RespostaFiscal(
            sucesso=status in {"autorizado", "inutilizado"},
            codigo_status=str(_ler(retorno, mapa["codigo"]) or "000"),
            mensagem=str(_ler(retorno, mapa["mensagem"]) or status or "Sem mensagem."),
            protocolo=(
                str(_ler(retorno, mapa["protocolo"]))
                if _ler(retorno, mapa["protocolo"])
                else None
            ),
            xml_retorno=json.dumps(retorno, ensure_ascii=False).encode("utf-8"),
        )
