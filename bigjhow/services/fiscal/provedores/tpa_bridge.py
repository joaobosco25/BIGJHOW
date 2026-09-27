"""Ponte com o emissor fiscal TPA que já roda na loja.

Esta é a rota que reaproveita o que a Big Jhow já tem homologado. O emissor
local (TPA Emissor / TPACentral) continua sendo quem assina e transmite à
SEFAZ; o sistema novo, na VPS, apenas entrega a venda e recolhe o retorno.

O que foi extraído do sistema antigo e está reproduzido aqui:

* protocolo de socket, texto puro delimitado por barra vertical, observado em
  ``TPA.Centralizador.Business``::

      0|INUTILNFCE|65|<serie>|<numero>|<numero>

  A conexão é TCP direta com ``SocketClient`` (ASCII, envia e lê a resposta na
  mesma conexão), exatamente como o ``EmissorBusiness`` fazia.

* integração por pasta de XML, observada em ``IntegrationTask``: o emissor
  deposita ``nfeProc``, ``procEventoNFe``, ``ProcInutNFe``, ``CFeCanc`` e
  ``TpaInformacoes`` em um diretório, que é varrido e movido após processar.

* endereços do centralizador registrados em ``BIGJHOW_REL/mapa_fiscal.txt``
  (``http://localhost:8000/CentralizadorService`` e a porta 8001), com os
  métodos ``InserirNotaFiscal``, ``CancelarNotaFiscal``,
  ``InutilizarNotaFiscal``, ``InserirNotaFiscalSAT`` e
  ``CancelarNotaFiscalSAT``.

O formato da RESPOSTA do socket não pôde ser recuperado do sistema antigo (a
lógica está compilada em DLL). Por isso o reconhecimento de sucesso é
configurável em ``padrao_sucesso`` e a resposta bruta é sempre gravada, para
que o ajuste seja feito na primeira execução real sem alterar código.
"""

from __future__ import annotations

import os
import re
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

from ..chave import somente_digitos, validar_chave
from .base import (
    ProvedorFiscal,
    ProvedorFiscalErro,
    ProvedorIndisponivel,
    RespostaFiscal,
    justificativa_valida,
)


# Comandos do emissor TPA, no formato observado no sistema legado.
COMANDO_INUTILIZAR = "0|INUTILNFCE|{modelo}|{serie}|{numero}|{numero}"
COMANDO_CANCELAR = "0|CANCNFCE|{modelo}|{chave}|{justificativa}"

PADRAO_SUCESSO_PADRAO = r"^\s*0\s*\|"
TEMPO_LIMITE_SOCKET = 30
TEMPO_LIMITE_RETORNO = 60
INTERVALO_LEITURA = 0.5

NAMESPACE_NFE = "http://www.portalfiscal.inf.br/nfe"


class ClienteSocketEmissor:
    """Reprodução do ``SocketClient`` do TPA: conecta, envia, lê e encerra."""

    def __init__(self, host: str, porta: int, *, tempo_limite: int = TEMPO_LIMITE_SOCKET) -> None:
        self.host = host
        self.porta = int(porta)
        self.tempo_limite = tempo_limite

    def enviar(self, comando: str) -> str:
        try:
            with socket.create_connection(
                (self.host, self.porta), timeout=self.tempo_limite
            ) as conexao:
                conexao.settimeout(self.tempo_limite)
                conexao.sendall(comando.encode("ascii", errors="replace"))
                pedacos: list[bytes] = []
                while True:
                    try:
                        pedaco = conexao.recv(4096)
                    except socket.timeout:
                        break
                    if not pedaco:
                        break
                    pedacos.append(pedaco)
                    if len(pedaco) < 4096:
                        break
        except OSError as exc:
            raise ProvedorIndisponivel(
                f"Não foi possível falar com o emissor em {self.host}:{self.porta} ({exc})."
            ) from exc
        return b"".join(pedacos).decode("ascii", errors="replace").strip()


class TpaBridge(ProvedorFiscal):
    nome = "tpa_bridge"
    rotulo = "Ponte com o emissor TPA da loja"
    produz_documento_fiscal = True
    # Quem assina é o emissor da loja, com o certificado que já está lá.
    exige_assinatura_local = False

    def conferir_configuracao(self) -> list[str]:
        pendencias: list[str] = []
        if not (self.configuracao.get("emissor_host") or "").strip():
            pendencias.append("Informe o endereço de rede do emissor TPA.")
        try:
            porta = int(self.configuracao.get("emissor_porta") or 0)
        except (TypeError, ValueError):
            porta = 0
        if not 1 <= porta <= 65535:
            pendencias.append("Informe a porta do emissor TPA (a instalação padrão usa 8001).")
        for chave_config, rotulo in (
            ("pasta_envio", "pasta onde o emissor lê os pedidos"),
            ("pasta_retorno", "pasta onde o emissor grava os XML autorizados"),
        ):
            caminho = (self.configuracao.get(chave_config) or "").strip()
            if not caminho:
                pendencias.append(f"Informe a {rotulo}.")
            elif not Path(caminho).is_dir():
                pendencias.append(f"A {rotulo} não existe ou não está acessível: {caminho}")
        return pendencias

    # ---------------------------------------------------------------- infra

    def _pasta(self, chave_config: str) -> Path:
        caminho = (self.configuracao.get(chave_config) or "").strip()
        if not caminho:
            raise ProvedorFiscalErro(f"A pasta '{chave_config}' não está configurada.")
        pasta = Path(caminho)
        if not pasta.is_dir():
            raise ProvedorIndisponivel(f"A pasta '{caminho}' não está acessível.")
        return pasta

    def _socket(self) -> ClienteSocketEmissor:
        host = (self.configuracao.get("emissor_host") or "").strip()
        porta = self.configuracao.get("emissor_porta")
        if not host or not porta:
            raise ProvedorFiscalErro("O endereço do emissor TPA não está configurado.")
        return ClienteSocketEmissor(
            host,
            int(porta),
            tempo_limite=int(self.configuracao.get("tempo_limite_socket", TEMPO_LIMITE_SOCKET)),
        )

    def _sucesso(self, resposta: str) -> bool:
        padrao = (self.configuracao.get("padrao_sucesso") or PADRAO_SUCESSO_PADRAO).strip()
        try:
            return re.search(padrao, resposta or "") is not None
        except re.error as exc:
            raise ProvedorFiscalErro(
                "O padrão de sucesso configurado para o emissor é inválido."
            ) from exc

    @staticmethod
    def _partes(resposta: str) -> list[str]:
        return [parte.strip() for parte in (resposta or "").split("|")]

    def _gravar_pedido(self, chave: str, conteudo: bytes) -> Path:
        pasta = self._pasta("pasta_envio")
        provisorio = pasta / f".{chave}-{uuid.uuid4().hex}.tmp"
        definitivo = pasta / f"{chave}.xml"
        # Grava com nome temporário e renomeia: o emissor nunca vê arquivo parcial.
        provisorio.write_bytes(conteudo)
        os.replace(provisorio, definitivo)
        return definitivo

    def _aguardar_retorno(self, chave: str) -> tuple[bytes | None, etree._Element | None]:
        pasta = self._pasta("pasta_retorno")
        limite = time.monotonic() + int(
            self.configuracao.get("tempo_limite_retorno", TEMPO_LIMITE_RETORNO)
        )
        while time.monotonic() < limite:
            for arquivo in sorted(pasta.glob("*.xml")):
                try:
                    conteudo = arquivo.read_bytes()
                except OSError:
                    continue
                if chave.encode("ascii") not in conteudo:
                    continue
                try:
                    raiz = etree.fromstring(conteudo)
                except etree.XMLSyntaxError:
                    continue
                if raiz.tag != f"{{{NAMESPACE_NFE}}}nfeProc":
                    continue
                return conteudo, raiz
            time.sleep(INTERVALO_LEITURA)
        return None, None

    # ------------------------------------------------------------ operações

    def autorizar(self, *, dados, xml_assinado: bytes) -> RespostaFiscal:
        chave = validar_chave(dados.chave)
        self._gravar_pedido(chave, xml_assinado)
        conteudo, raiz = self._aguardar_retorno(chave)
        if raiz is None:
            raise ProvedorIndisponivel(
                "O emissor da loja não devolveu o XML autorizado no tempo esperado. "
                "A venda ficou pendente de emissão."
            )

        protocolo = raiz.find(f".//{{{NAMESPACE_NFE}}}infProt")
        codigo = "000"
        motivo = "Retorno do emissor sem status."
        numero_protocolo = None
        recebido_em = None
        if protocolo is not None:
            codigo = protocolo.findtext(f"{{{NAMESPACE_NFE}}}cStat", default="000") or "000"
            motivo = protocolo.findtext(f"{{{NAMESPACE_NFE}}}xMotivo", default=motivo) or motivo
            numero_protocolo = protocolo.findtext(f"{{{NAMESPACE_NFE}}}nProt")
            recebimento = protocolo.findtext(f"{{{NAMESPACE_NFE}}}dhRecbto")
            if recebimento:
                try:
                    recebido_em = datetime.fromisoformat(recebimento)
                except ValueError:
                    recebido_em = None
        return RespostaFiscal(
            sucesso=codigo in {"100", "150"},
            codigo_status=codigo,
            mensagem=motivo,
            protocolo=numero_protocolo,
            chave=chave,
            recebido_em=recebido_em,
            xml_enviado=xml_assinado,
            xml_retorno=conteudo,
            xml_autorizado=conteudo if codigo in {"100", "150"} else None,
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
        chave_normalizada = validar_chave(chave)
        motivo = justificativa_valida(justificativa)
        comando = COMANDO_CANCELAR.format(
            modelo=int(chave_normalizada[20:22]),
            chave=chave_normalizada,
            justificativa=motivo,
        )
        resposta = self._socket().enviar(comando)
        partes = self._partes(resposta)
        sucesso = self._sucesso(resposta)
        return RespostaFiscal(
            sucesso=sucesso,
            codigo_status=partes[0] if partes and partes[0] else "000",
            mensagem=resposta or "O emissor não devolveu mensagem.",
            protocolo=partes[2] if len(partes) > 2 and sucesso else None,
            chave=chave_normalizada,
            recebido_em=datetime.now(timezone.utc),
            detalhes={"comando": comando, "resposta_bruta": resposta},
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
        justificativa_valida(justificativa)
        if numero_final < numero_inicial:
            raise ProvedorFiscalErro("A faixa de inutilização está invertida.")

        cliente = self._socket()
        resultados: list[dict] = []
        falhas = 0
        # O emissor legado trata um número por comando (o formato repete o
        # mesmo valor como início e fim da faixa).
        for numero in range(int(numero_inicial), int(numero_final) + 1):
            comando = COMANDO_INUTILIZAR.format(
                modelo=int(modelo), serie=int(serie), numero=numero
            )
            resposta = cliente.enviar(comando)
            aceito = self._sucesso(resposta)
            if not aceito:
                falhas += 1
            resultados.append(
                {
                    "numero": numero,
                    "aceito": aceito,
                    "comando": comando,
                    "resposta_bruta": resposta,
                }
            )

        total = len(resultados)
        aceitos = total - falhas
        if falhas == 0:
            mensagem = "Todas as inutilizações foram realizadas com sucesso."
        elif aceitos:
            mensagem = f"{aceitos} de {total} números foram inutilizados; {falhas} falharam."
        else:
            mensagem = "Nenhum número foi inutilizado pelo emissor."
        return RespostaFiscal(
            sucesso=falhas == 0,
            codigo_status="102" if falhas == 0 else "000",
            mensagem=mensagem,
            recebido_em=datetime.now(timezone.utc),
            detalhes={"numeros": resultados, "cnpj": somente_digitos(cnpj)},
        )

    def status_servico(self) -> RespostaFiscal:
        pendencias = self.conferir_configuracao()
        if pendencias:
            return RespostaFiscal(
                sucesso=False, codigo_status="000", mensagem="; ".join(pendencias)
            )
        try:
            cliente = self._socket()
            with socket.create_connection(
                (cliente.host, cliente.porta), timeout=5
            ):
                pass
        except OSError as exc:
            return RespostaFiscal(
                sucesso=False,
                codigo_status="000",
                mensagem=f"Emissor da loja inacessível: {exc}",
            )
        return RespostaFiscal(
            sucesso=True, codigo_status="107", mensagem="Emissor da loja respondendo."
        )
