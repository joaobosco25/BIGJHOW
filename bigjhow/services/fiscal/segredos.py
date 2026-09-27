"""Cifragem em repouso das credenciais fiscais.

CSC, senha do certificado e token de gateway não podem ficar em claro no
banco: quem tem um backup do PostgreSQL passaria a poder emitir nota em nome
da empresa. Aqui eles são cifrados com AES-256-GCM, cuja chave é derivada de
``FISCAL_SECRET_KEY`` — variável de ambiente do servidor, nunca gravada.

Formato do valor guardado: ``v1.<nonce em base64url>.<texto cifrado em base64url>``.
O GCM autentica o conteúdo, então adulterar a coluna no banco quebra a
decifragem em vez de devolver lixo silencioso.
"""

from __future__ import annotations

import base64
import os

from flask import current_app


VERSAO = "v1"
TAMANHO_NONCE = 12
CONTEXTO = b"bigjhow-fiscal-credencial"


class SegredoErro(RuntimeError):
    pass


class ChaveAusente(SegredoErro):
    pass


def _importar_cifra():
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError as exc:  # pragma: no cover
        raise SegredoErro(
            "A biblioteca 'cryptography' não está instalada. Instale o requirements.txt."
        ) from exc
    return AESGCM, HKDF, hashes


def chave_configurada() -> bool:
    return bool(_material_bruto(silencioso=True))


def _material_bruto(*, silencioso: bool = False) -> str:
    valor = ""
    try:
        valor = (current_app.config.get("FISCAL_SECRET_KEY") or "").strip()
    except RuntimeError:
        valor = ""
    if not valor:
        valor = (os.getenv("FISCAL_SECRET_KEY") or "").strip()
    if not valor and not silencioso:
        raise ChaveAusente(
            "FISCAL_SECRET_KEY não está definida. Sem ela o sistema não guarda "
            "CSC, senha de certificado nem token de gateway."
        )
    return valor


def _chave() -> bytes:
    AESGCM, HKDF, hashes = _importar_cifra()
    material = _material_bruto()
    if len(material) < 32:
        raise SegredoErro("FISCAL_SECRET_KEY precisa ter pelo menos 32 caracteres.")
    derivada = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=CONTEXTO
    ).derive(material.encode("utf-8"))
    return derivada


def cifrar(valor: str) -> str:
    """Cifra um segredo. Devolve o texto que vai para a coluna do banco."""
    if valor is None or valor == "":
        raise SegredoErro("Não há segredo a guardar.")
    AESGCM, _, _ = _importar_cifra()
    nonce = os.urandom(TAMANHO_NONCE)
    cifrado = AESGCM(_chave()).encrypt(nonce, valor.encode("utf-8"), CONTEXTO)
    return ".".join(
        [
            VERSAO,
            base64.urlsafe_b64encode(nonce).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(cifrado).decode("ascii").rstrip("="),
        ]
    )


def _decodificar(texto: str) -> bytes:
    preenchimento = "=" * (-len(texto) % 4)
    return base64.urlsafe_b64decode(texto + preenchimento)


def decifrar(guardado: str) -> str:
    """Recupera o segredo. Levanta erro se a chave mudou ou o dado foi alterado."""
    partes = (guardado or "").split(".")
    if len(partes) != 3 or partes[0] != VERSAO:
        raise SegredoErro("Credencial fiscal gravada em formato desconhecido.")
    AESGCM, _, _ = _importar_cifra()
    try:
        aberto = AESGCM(_chave()).decrypt(
            _decodificar(partes[1]), _decodificar(partes[2]), CONTEXTO
        )
    except Exception as exc:
        raise SegredoErro(
            "Não foi possível abrir a credencial fiscal. Ou a FISCAL_SECRET_KEY "
            "mudou, ou o registro foi alterado fora do sistema. Recadastre a credencial."
        ) from exc
    return aberto.decode("utf-8")


def mascarar(valor: str | None) -> str:
    """Mostra só o suficiente para a pessoa reconhecer o que está guardado."""
    texto = (valor or "").strip()
    if not texto:
        return "—"
    if len(texto) <= 4:
        return "•" * len(texto)
    return f"{'•' * (len(texto) - 4)}{texto[-4:]}"
