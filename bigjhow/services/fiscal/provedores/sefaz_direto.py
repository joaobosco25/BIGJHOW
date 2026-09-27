"""Transmissão direta aos serviços da SEFAZ, com certificado A1 na VPS.

Serviços usados (leiaute 4.00):

* ``NFeAutorizacao4``     — envio síncrono do lote (``enviNFe`` com indSinc=1)
* ``NFeRecepcaoEvento4``  — cancelamento (evento 110111)
* ``NFeInutilizacao4``    — inutilização de faixa de numeração
* ``NFeStatusServico4``   — disponibilidade do autorizador
* ``NFeConsultaProtocolo4`` — consulta/reconciliação por chave

A autenticação é mútua (mTLS) com o certificado do emitente. O ``requests``
só aceita certificado cliente em arquivos PEM, então o PKCS#12 é convertido
para PEM em arquivos temporários com permissão restrita, usados durante a
chamada e apagados em seguida — o material nunca fica em disco de forma
persistente nem entra no banco.

Os endereços dos serviços NÃO têm valor padrão: são configurados por
estabelecimento. Ver ``docs/FISCAL.md``.
"""

from __future__ import annotations

import os
import stat
import tempfile
from datetime import datetime, timezone

from lxml import etree

from ..assinatura import AssinaturaErro, assinar_documento, carregar_certificado
from ..chave import somente_digitos, validar_chave
from ..estruturas import data_hora_fiscal
from ..xml_nfce import NAMESPACE, qualificar
from .base import (
    ProvedorFiscal,
    ProvedorFiscalErro,
    ProvedorIndisponivel,
    RespostaFiscal,
    STATUS_AUTORIZADO,
    STATUS_CANCELADO,
    STATUS_INUTILIZADO,
    justificativa_valida,
)


NAMESPACE_SOAP = "http://www.w3.org/2003/05/soap-envelope"
TEMPO_LIMITE = (10, 45)  # (conexão, leitura) em segundos

EVENTO_CANCELAMENTO = "110111"
VERSAO_EVENTO = "1.00"
VERSAO_INUTILIZACAO = "4.00"


def _requests():
    try:
        import requests
    except ImportError as exc:  # pragma: no cover
        raise ProvedorFiscalErro(
            "A biblioteca 'requests' não está instalada. Instale o requirements.txt."
        ) from exc
    return requests


class _CertificadoTemporario:
    """Escreve chave e certificado em PEM temporário para o mTLS do requests."""

    def __init__(self, certificado) -> None:
        self._certificado = certificado
        self._arquivos: list[str] = []

    def __enter__(self) -> tuple[str, str]:
        from cryptography.hazmat.primitives import serialization

        def gravar(conteudo: bytes) -> str:
            descritor, caminho = tempfile.mkstemp(prefix="bigjhow-fiscal-", suffix=".pem")
            os.fchmod(descritor, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(descritor, "wb") as arquivo:
                arquivo.write(conteudo)
            self._arquivos.append(caminho)
            return caminho

        caminho_certificado = gravar(
            self._certificado.certificado.public_bytes(serialization.Encoding.PEM)
        )
        caminho_chave = gravar(
            self._certificado.chave_privada.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        return caminho_certificado, caminho_chave

    def __exit__(self, *_) -> None:
        for caminho in self._arquivos:
            try:
                with open(caminho, "r+b") as arquivo:
                    tamanho = os.fstat(arquivo.fileno()).st_size
                    arquivo.write(b"\x00" * tamanho)
                os.unlink(caminho)
            except OSError:
                pass
        self._arquivos.clear()


class SefazDireto(ProvedorFiscal):
    nome = "sefaz_direto"
    rotulo = "SEFAZ direto (certificado A1 local)"
    produz_documento_fiscal = True

    def __init__(self, configuracao: dict) -> None:
        super().__init__(configuracao)
        self._certificado = None

    # ---------------------------------------------------------------- infra

    def conferir_configuracao(self) -> list[str]:
        pendencias: list[str] = []
        for chave_config, rotulo in (
            ("url_autorizacao", "endereço do serviço NFeAutorizacao4"),
            ("url_evento", "endereço do serviço NFeRecepcaoEvento4"),
            ("url_inutilizacao", "endereço do serviço NFeInutilizacao4"),
            ("url_consulta", "endereço do serviço NFeConsultaProtocolo4"),
            ("certificado_caminho", "caminho do certificado A1 (.pfx)"),
            ("certificado_senha", "senha do certificado A1"),
        ):
            if not (self.configuracao.get(chave_config) or "").strip():
                pendencias.append(f"Configure o {rotulo}.")
        for chave_config in ("url_autorizacao", "url_evento", "url_inutilizacao", "url_consulta"):
            valor = (self.configuracao.get(chave_config) or "").strip()
            if valor and not valor.startswith("https://"):
                pendencias.append(f"O endereço '{chave_config}' precisa usar HTTPS.")
        if not pendencias:
            try:
                certificado = self._abrir_certificado()
            except AssinaturaErro as exc:
                pendencias.append(str(exc))
            else:
                if certificado.vencido:
                    pendencias.append("O certificado digital está vencido.")
                elif certificado.dias_para_vencer <= 15:
                    pendencias.append(
                        f"O certificado digital vence em {certificado.dias_para_vencer} dia(s)."
                    )
        return pendencias

    def _abrir_certificado(self):
        if self._certificado is None:
            self._certificado = carregar_certificado(
                self.configuracao.get("certificado_caminho", ""),
                self.configuracao.get("certificado_senha", ""),
            )
        return self._certificado

    def _endereco(self, chave_config: str) -> str:
        valor = (self.configuracao.get(chave_config) or "").strip()
        if not valor.startswith("https://"):
            raise ProvedorFiscalErro(
                f"O endereço '{chave_config}' da SEFAZ não está configurado."
            )
        return valor

    def _enviar(self, url: str, corpo: etree._Element) -> etree._Element:
        requests = _requests()
        envelope = etree.Element(
            f"{{{NAMESPACE_SOAP}}}Envelope", nsmap={None: NAMESPACE_SOAP}
        )
        corpo_soap = etree.SubElement(envelope, f"{{{NAMESPACE_SOAP}}}Body")
        corpo_soap.append(corpo)
        conteudo = etree.tostring(envelope, encoding="utf-8", xml_declaration=True)
        from ..rtc_ensaio import conferir_transporte
        conferir_transporte(conteudo)

        certificado = self._abrir_certificado()
        try:
            with _CertificadoTemporario(certificado) as (pem_certificado, pem_chave):
                resposta = requests.post(
                    url,
                    data=conteudo,
                    headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                    cert=(pem_certificado, pem_chave),
                    timeout=TEMPO_LIMITE,
                )
        except requests.Timeout as exc:
            raise ProvedorIndisponivel(
                "A SEFAZ não respondeu no tempo limite. Use contingência."
            ) from exc
        except requests.RequestException as exc:
            raise ProvedorIndisponivel(
                f"Falha de comunicação com a SEFAZ: {exc}"
            ) from exc

        if resposta.status_code >= 400:
            raise ProvedorIndisponivel(
                f"A SEFAZ devolveu HTTP {resposta.status_code}."
            )
        try:
            raiz = etree.fromstring(resposta.content)
        except etree.XMLSyntaxError as exc:
            raise ProvedorFiscalErro("A SEFAZ devolveu uma resposta ilegível.") from exc
        return raiz

    @staticmethod
    def _primeiro_texto(raiz: etree._Element, nome: str) -> str | None:
        no = raiz.find(f".//{{{NAMESPACE}}}{nome}")
        return no.text.strip() if no is not None and no.text else None

    # ------------------------------------------------------------ operações

    def autorizar(self, *, dados, xml_assinado: bytes) -> RespostaFiscal:
        url = self._endereco("url_autorizacao")
        nfe = etree.fromstring(xml_assinado)

        envio = etree.Element(qualificar("enviNFe"), nsmap={None: NAMESPACE})
        envio.set("versao", "4.00")
        lote = etree.SubElement(envio, qualificar("idLote"))
        lote.text = str(dados.numero)
        sincrono = etree.SubElement(envio, qualificar("indSinc"))
        sincrono.text = "1"
        envio.append(nfe)

        mensagem = etree.Element(
            "{http://www.portalfiscal.inf.br/nfe/wsdl/NFeAutorizacao4}nfeDadosMsg",
            nsmap={None: "http://www.portalfiscal.inf.br/nfe/wsdl/NFeAutorizacao4"},
        )
        mensagem.append(envio)

        retorno = self._enviar(url, mensagem)
        conteudo = etree.tostring(retorno, encoding="utf-8")

        status_lote = self._primeiro_texto(retorno, "cStat") or "000"
        motivo = self._primeiro_texto(retorno, "xMotivo") or "Sem retorno da SEFAZ."
        protocolo_no = retorno.find(f".//{{{NAMESPACE}}}infProt")

        codigo = status_lote
        if protocolo_no is not None:
            codigo = (
                protocolo_no.findtext(f"{{{NAMESPACE}}}cStat", default=status_lote) or status_lote
            )
            motivo = protocolo_no.findtext(f"{{{NAMESPACE}}}xMotivo", default=motivo) or motivo

        autorizado = codigo in STATUS_AUTORIZADO
        numero_protocolo = None
        recebido_em = None
        xml_autorizado = None
        if protocolo_no is not None:
            numero_protocolo = protocolo_no.findtext(f"{{{NAMESPACE}}}nProt")
            recebimento = protocolo_no.findtext(f"{{{NAMESPACE}}}dhRecbto")
            if recebimento:
                try:
                    recebido_em = datetime.fromisoformat(recebimento)
                except ValueError:
                    recebido_em = None
        if autorizado and protocolo_no is not None:
            xml_autorizado = montar_nfe_proc(nfe, protocolo_no.getparent())

        return RespostaFiscal(
            sucesso=autorizado,
            codigo_status=codigo,
            mensagem=motivo,
            protocolo=numero_protocolo,
            chave=dados.chave,
            recebido_em=recebido_em,
            xml_enviado=xml_assinado,
            xml_retorno=conteudo,
            xml_autorizado=xml_autorizado,
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
        url = self._endereco("url_evento")
        chave_normalizada = validar_chave(chave)
        motivo = justificativa_valida(justificativa)
        cnpj_limpo = somente_digitos(cnpj)
        agora = datetime.now(timezone.utc).astimezone()

        lote = etree.Element(qualificar("envEvento"), nsmap={None: NAMESPACE})
        lote.set("versao", VERSAO_EVENTO)
        etree.SubElement(lote, qualificar("idLote")).text = "1"

        evento = etree.SubElement(lote, qualificar("evento"))
        evento.set("versao", VERSAO_EVENTO)
        identificador = f"ID{EVENTO_CANCELAMENTO}{chave_normalizada}{int(sequencia):02d}"
        info = etree.SubElement(evento, qualificar("infEvento"))
        info.set("Id", identificador)
        etree.SubElement(info, qualificar("cOrgao")).text = chave_normalizada[0:2]
        etree.SubElement(info, qualificar("tpAmb")).text = str(int(ambiente))
        etree.SubElement(info, qualificar("CNPJ")).text = cnpj_limpo
        etree.SubElement(info, qualificar("chNFe")).text = chave_normalizada
        etree.SubElement(info, qualificar("dhEvento")).text = data_hora_fiscal(agora)
        etree.SubElement(info, qualificar("tpEvento")).text = EVENTO_CANCELAMENTO
        etree.SubElement(info, qualificar("nSeqEvento")).text = str(int(sequencia))
        etree.SubElement(info, qualificar("verEvento")).text = VERSAO_EVENTO
        detalhe = etree.SubElement(info, qualificar("detEvento"))
        detalhe.set("versao", VERSAO_EVENTO)
        etree.SubElement(detalhe, qualificar("descEvento")).text = "Cancelamento"
        etree.SubElement(detalhe, qualificar("nProt")).text = str(protocolo)
        etree.SubElement(detalhe, qualificar("xJust")).text = motivo

        assinar_documento(
            evento, elemento_referenciado=info, certificado=self._abrir_certificado()
        )

        mensagem = etree.Element(
            "{http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4}nfeDadosMsg",
            nsmap={None: "http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4"},
        )
        mensagem.append(lote)

        retorno = self._enviar(url, mensagem)
        codigo = self._primeiro_texto(retorno, "cStat") or "000"
        motivo_retorno = self._primeiro_texto(retorno, "xMotivo") or "Sem retorno."
        info_evento = retorno.find(f".//{{{NAMESPACE}}}infEvento")
        if info_evento is not None:
            codigo = info_evento.findtext(f"{{{NAMESPACE}}}cStat", default=codigo) or codigo
            motivo_retorno = (
                info_evento.findtext(f"{{{NAMESPACE}}}xMotivo", default=motivo_retorno)
                or motivo_retorno
            )
        return RespostaFiscal(
            sucesso=codigo in STATUS_CANCELADO,
            codigo_status=codigo,
            mensagem=motivo_retorno,
            protocolo=(
                info_evento.findtext(f"{{{NAMESPACE}}}nProt")
                if info_evento is not None
                else None
            ),
            chave=chave_normalizada,
            xml_enviado=etree.tostring(lote, encoding="utf-8"),
            xml_retorno=etree.tostring(retorno, encoding="utf-8"),
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
        url = self._endereco("url_inutilizacao")
        motivo = justificativa_valida(justificativa)
        cnpj_limpo = somente_digitos(cnpj)
        if numero_final < numero_inicial:
            raise ProvedorFiscalErro("A faixa de inutilização está invertida.")

        identificador = (
            f"ID{int(codigo_uf):02d}{int(ano) % 100:02d}{cnpj_limpo}"
            f"{int(modelo):02d}{int(serie):03d}"
            f"{int(numero_inicial):09d}{int(numero_final):09d}"
        )
        pedido = etree.Element(qualificar("inutNFe"), nsmap={None: NAMESPACE})
        pedido.set("versao", VERSAO_INUTILIZACAO)
        info = etree.SubElement(pedido, qualificar("infInut"))
        info.set("Id", identificador)
        etree.SubElement(info, qualificar("tpAmb")).text = str(int(ambiente))
        etree.SubElement(info, qualificar("xServ")).text = "INUTILIZAR"
        etree.SubElement(info, qualificar("cUF")).text = f"{int(codigo_uf):02d}"
        etree.SubElement(info, qualificar("ano")).text = f"{int(ano) % 100:02d}"
        etree.SubElement(info, qualificar("CNPJ")).text = cnpj_limpo
        etree.SubElement(info, qualificar("mod")).text = f"{int(modelo):02d}"
        etree.SubElement(info, qualificar("serie")).text = str(int(serie))
        etree.SubElement(info, qualificar("nNFIni")).text = str(int(numero_inicial))
        etree.SubElement(info, qualificar("nNFFin")).text = str(int(numero_final))
        etree.SubElement(info, qualificar("xJust")).text = motivo

        assinar_documento(
            pedido, elemento_referenciado=info, certificado=self._abrir_certificado()
        )

        mensagem = etree.Element(
            "{http://www.portalfiscal.inf.br/nfe/wsdl/NFeInutilizacao4}nfeDadosMsg",
            nsmap={None: "http://www.portalfiscal.inf.br/nfe/wsdl/NFeInutilizacao4"},
        )
        mensagem.append(pedido)

        retorno = self._enviar(url, mensagem)
        codigo = self._primeiro_texto(retorno, "cStat") or "000"
        motivo_retorno = self._primeiro_texto(retorno, "xMotivo") or "Sem retorno."
        return RespostaFiscal(
            sucesso=codigo in STATUS_INUTILIZADO,
            codigo_status=codigo,
            mensagem=motivo_retorno,
            protocolo=self._primeiro_texto(retorno, "nProt"),
            xml_enviado=etree.tostring(pedido, encoding="utf-8"),
            xml_retorno=etree.tostring(retorno, encoding="utf-8"),
        )


    def consultar_chave(self, *, chave: str, ambiente: int) -> RespostaFiscal:
        """Consulta ``NFeConsultaProtocolo4`` sem reenviar o documento.

        Essa é a operação usada para resolver resultado desconhecido após
        timeout: primeiro pergunta ao autorizador o estado da chave; somente
        uma consulta conclusiva pode alterar o estado local.
        """
        url = self._endereco("url_consulta")
        chave_normalizada = validar_chave(chave)
        consulta = etree.Element(qualificar("consSitNFe"), nsmap={None: NAMESPACE})
        consulta.set("versao", "4.00")
        etree.SubElement(consulta, qualificar("tpAmb")).text = str(int(ambiente))
        etree.SubElement(consulta, qualificar("xServ")).text = "CONSULTAR"
        etree.SubElement(consulta, qualificar("chNFe")).text = chave_normalizada

        mensagem = etree.Element(
            "{http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsultaProtocolo4}nfeDadosMsg",
            nsmap={None: "http://www.portalfiscal.inf.br/nfe/wsdl/NFeConsultaProtocolo4"},
        )
        mensagem.append(consulta)
        retorno = self._enviar(url, mensagem)

        codigo = self._primeiro_texto(retorno, "cStat") or "000"
        motivo = self._primeiro_texto(retorno, "xMotivo") or "Sem retorno da SEFAZ."
        protocolo_no = retorno.find(f".//{{{NAMESPACE}}}protNFe/{{{NAMESPACE}}}infProt")
        protocolo = None
        recebido_em = None
        chave_retorno = chave_normalizada
        if protocolo_no is not None:
            codigo = protocolo_no.findtext(f"{{{NAMESPACE}}}cStat", default=codigo) or codigo
            motivo = protocolo_no.findtext(f"{{{NAMESPACE}}}xMotivo", default=motivo) or motivo
            protocolo = protocolo_no.findtext(f"{{{NAMESPACE}}}nProt")
            chave_retorno = protocolo_no.findtext(f"{{{NAMESPACE}}}chNFe") or chave_normalizada
            recebimento = protocolo_no.findtext(f"{{{NAMESPACE}}}dhRecbto")
            if recebimento:
                try:
                    recebido_em = datetime.fromisoformat(recebimento)
                except ValueError:
                    recebido_em = None

        return RespostaFiscal(
            sucesso=codigo in STATUS_AUTORIZADO,
            codigo_status=codigo,
            mensagem=motivo,
            protocolo=protocolo,
            chave=chave_retorno,
            recebido_em=recebido_em,
            xml_enviado=etree.tostring(consulta, encoding="utf-8"),
            xml_retorno=etree.tostring(retorno, encoding="utf-8"),
        )

    def status_servico(self) -> RespostaFiscal:
        url = (self.configuracao.get("url_status") or "").strip()
        if not url.startswith("https://"):
            return RespostaFiscal(
                sucesso=False,
                codigo_status="000",
                mensagem="Endereço do NFeStatusServico4 não configurado.",
            )
        consulta = etree.Element(qualificar("consStatServ"), nsmap={None: NAMESPACE})
        consulta.set("versao", "4.00")
        etree.SubElement(consulta, qualificar("tpAmb")).text = str(
            int(self.configuracao.get("ambiente", 2))
        )
        etree.SubElement(consulta, qualificar("cUF")).text = str(
            int(self.configuracao.get("codigo_uf", 0))
        )
        etree.SubElement(consulta, qualificar("xServ")).text = "STATUS"
        mensagem = etree.Element(
            "{http://www.portalfiscal.inf.br/nfe/wsdl/NFeStatusServico4}nfeDadosMsg",
            nsmap={None: "http://www.portalfiscal.inf.br/nfe/wsdl/NFeStatusServico4"},
        )
        mensagem.append(consulta)
        retorno = self._enviar(url, mensagem)
        codigo = self._primeiro_texto(retorno, "cStat") or "000"
        return RespostaFiscal(
            sucesso=codigo == "107",
            codigo_status=codigo,
            mensagem=self._primeiro_texto(retorno, "xMotivo") or "Sem retorno.",
            xml_retorno=etree.tostring(retorno, encoding="utf-8"),
        )


def montar_nfe_proc(nfe: etree._Element, protocolo: etree._Element) -> bytes:
    """Monta o ``nfeProc`` — o XML que precisa ser guardado por cinco anos."""
    processo = etree.Element(qualificar("nfeProc"), nsmap={None: NAMESPACE})
    processo.set("versao", "4.00")
    processo.append(etree.fromstring(etree.tostring(nfe)))
    processo.append(etree.fromstring(etree.tostring(protocolo)))
    return etree.tostring(processo, encoding="utf-8", xml_declaration=True)
