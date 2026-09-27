"""Assinatura digital XMLDSig do XML fiscal, com certificado A1 (PKCS#12).

Padrão exigido pela SEFAZ (MOC 7.0, item 5.1):

* assinatura envelopada dentro do elemento ``<NFe>``, logo após ``<infNFe>``;
* referência ao atributo ``Id`` do ``infNFe`` (``URI="#NFe<chave>"``);
* transformações: enveloped-signature seguida de C14N inclusiva;
* resumo SHA-1 e assinatura RSA-SHA1;
* ``KeyInfo`` contendo apenas o certificado do emitente em X509Certificate.

O certificado nunca é gravado no banco. O caminho do arquivo ``.pfx`` e a
senha ficam em variáveis de ambiente do servidor, e a senha é lida sob demanda.
"""

from __future__ import annotations

import base64
import datetime as _datetime
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from lxml import etree


NAMESPACE_ASSINATURA = "http://www.w3.org/2000/09/xmldsig#"
NAMESPACE_NFE = "http://www.portalfiscal.inf.br/nfe"

ALGORITMO_C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
ALGORITMO_ENVELOPADA = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
ALGORITMO_ASSINATURA = "http://www.w3.org/2000/09/xmldsig#rsa-sha1"
ALGORITMO_RESUMO = "http://www.w3.org/2000/09/xmldsig#sha1"


class AssinaturaErro(RuntimeError):
    pass


@dataclass(slots=True)
class Certificado:
    chave_privada: object
    certificado: object
    cnpj_titular: str | None
    valido_de: _datetime.datetime
    valido_ate: _datetime.datetime
    titular: str

    @property
    def vencido(self) -> bool:
        return _datetime.datetime.now(_datetime.timezone.utc) > self.valido_ate

    @property
    def dias_para_vencer(self) -> int:
        delta = self.valido_ate - _datetime.datetime.now(_datetime.timezone.utc)
        return delta.days


def _importar_cryptography():
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.serialization import pkcs12
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise AssinaturaErro(
            "A biblioteca 'cryptography' não está instalada. "
            "Instale as dependências do requirements.txt antes de emitir."
        ) from exc
    return hashes, serialization, padding, pkcs12


def carregar_certificado(caminho: str | os.PathLike, senha: str) -> Certificado:
    """Abre um certificado A1 no formato PKCS#12 (.pfx/.p12)."""
    hashes, serialization, padding, pkcs12 = _importar_cryptography()
    arquivo = Path(caminho)
    if not arquivo.is_file():
        raise AssinaturaErro(f"Certificado digital não encontrado em {arquivo}.")
    try:
        conteudo = arquivo.read_bytes()
    except OSError as exc:
        raise AssinaturaErro("Não foi possível ler o arquivo do certificado.") from exc
    try:
        chave, certificado, _ = pkcs12.load_key_and_certificates(
            conteudo, (senha or "").encode("utf-8")
        )
    except Exception as exc:
        raise AssinaturaErro(
            "Falha ao abrir o certificado. Confira o arquivo e a senha configurada."
        ) from exc
    if chave is None or certificado is None:
        raise AssinaturaErro("O arquivo informado não contém chave privada e certificado.")

    titular = certificado.subject.rfc4514_string()
    cnpj = None
    for parte in titular.replace("\\,", " ").split(","):
        digitos = "".join(caractere for caractere in parte if caractere.isdigit())
        if len(digitos) == 14:
            cnpj = digitos
            break

    return Certificado(
        chave_privada=chave,
        certificado=certificado,
        cnpj_titular=cnpj,
        valido_de=certificado.not_valid_before_utc,
        valido_ate=certificado.not_valid_after_utc,
        titular=titular,
    )


def _canonizar(elemento: etree._Element) -> bytes:
    parser = etree.XMLParser(resolve_entities=False, no_network=True,
        load_dtd=False, remove_blank_text=False)
    normalizado = etree.fromstring(
        etree.tostring(elemento, encoding="utf-8", with_tail=False), parser)
    return etree.tostring(normalizado, method="c14n",
        exclusive=False, with_comments=False)


def _sem_assinatura(elemento: etree._Element) -> etree._Element:
    """Aplica a transformação enveloped-signature: remove Signature da cópia."""
    copia = etree.fromstring(etree.tostring(elemento))
    for assinatura in copia.findall(f".//{{{NAMESPACE_ASSINATURA}}}Signature"):
        assinatura.getparent().remove(assinatura)
    return copia


def calcular_digest(elemento: etree._Element) -> str:
    """DigestValue (SHA-1 em base64) do elemento referenciado."""
    # A Reference da NF-e/NFC-e aponta para infNFe.
    # Signature e irma de infNFe, portanto a transformacao
    # enveloped-signature nao remove nada do no referenciado.
    #
    # Canonicaliza a representacao serializada do elemento com os namespaces
    # em escopo. A normalizacao evita a divergencia observada na C14N de
    # subarvores com lxml 6.0.2/libxml2 2.14.6 frente ao verificador .NET.
    canonico = _canonizar(elemento)
    return base64.b64encode(hashlib.sha1(canonico).digest()).decode("ascii")


def assinar_documento(
    raiz: etree._Element,
    *,
    elemento_referenciado: etree._Element,
    certificado: Certificado,
) -> etree._Element:
    """Assina ``elemento_referenciado`` e insere a Signature dentro de ``raiz``.

    Devolve a própria raiz, já com a assinatura anexada.
    """
    hashes, serialization, padding, _ = _importar_cryptography()

    identificador = elemento_referenciado.get("Id")
    if not identificador:
        raise AssinaturaErro("O elemento a assinar precisa ter o atributo Id.")

    # nsmap explícito para o XML sair com xmlns padrão (<Signature xmlns="...">)
    # em vez de um prefixo gerado automaticamente pelo lxml.
    assinatura = etree.Element(
        f"{{{NAMESPACE_ASSINATURA}}}Signature", nsmap={None: NAMESPACE_ASSINATURA}
    )
    info = etree.SubElement(assinatura, f"{{{NAMESPACE_ASSINATURA}}}SignedInfo")
    etree.SubElement(
        info, f"{{{NAMESPACE_ASSINATURA}}}CanonicalizationMethod"
    ).set("Algorithm", ALGORITMO_C14N)
    etree.SubElement(info, f"{{{NAMESPACE_ASSINATURA}}}SignatureMethod").set(
        "Algorithm", ALGORITMO_ASSINATURA
    )
    referencia = etree.SubElement(info, f"{{{NAMESPACE_ASSINATURA}}}Reference")
    referencia.set("URI", f"#{identificador}")
    transformacoes = etree.SubElement(
        referencia, f"{{{NAMESPACE_ASSINATURA}}}Transforms"
    )
    etree.SubElement(transformacoes, f"{{{NAMESPACE_ASSINATURA}}}Transform").set(
        "Algorithm", ALGORITMO_ENVELOPADA
    )
    etree.SubElement(transformacoes, f"{{{NAMESPACE_ASSINATURA}}}Transform").set(
        "Algorithm", ALGORITMO_C14N
    )
    etree.SubElement(referencia, f"{{{NAMESPACE_ASSINATURA}}}DigestMethod").set(
        "Algorithm", ALGORITMO_RESUMO
    )
    raiz.append(assinatura)
    resumo = etree.SubElement(referencia, f"{{{NAMESPACE_ASSINATURA}}}DigestValue")
    resumo.text = calcular_digest(elemento_referenciado)

    valor = etree.SubElement(assinatura, f"{{{NAMESPACE_ASSINATURA}}}SignatureValue")
    valor.text = base64.b64encode(
        certificado.chave_privada.sign(_canonizar(info), padding.PKCS1v15(), hashes.SHA1())
    ).decode("ascii")

    chave_info = etree.SubElement(assinatura, f"{{{NAMESPACE_ASSINATURA}}}KeyInfo")
    dados_x509 = etree.SubElement(chave_info, f"{{{NAMESPACE_ASSINATURA}}}X509Data")
    certificado_x509 = etree.SubElement(
        dados_x509, f"{{{NAMESPACE_ASSINATURA}}}X509Certificate"
    )
    certificado_x509.text = base64.b64encode(
        certificado.certificado.public_bytes(serialization.Encoding.DER)
    ).decode("ascii")

    return raiz


def extrair_digest(raiz: etree._Element) -> str | None:
    """Lê o DigestValue já gravado — usado no QR Code de contingência."""
    no = raiz.find(f".//{{{NAMESPACE_ASSINATURA}}}DigestValue")
    return no.text if no is not None else None


def conferir_assinatura(raiz: etree._Element, elemento_referenciado: etree._Element) -> bool:
    """Confere se o DigestValue gravado corresponde ao conteúdo atual.

    Não substitui a validação da SEFAZ; serve para detectar corrupção do XML
    entre a assinatura e o envio.
    """
    gravado = extrair_digest(raiz)
    if gravado is None:
        return False
    return gravado == calcular_digest(elemento_referenciado)
