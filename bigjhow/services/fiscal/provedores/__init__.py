"""Registro dos provedores de emissão fiscal disponíveis."""

from __future__ import annotations

from .base import (
    ProvedorFiscal,
    ProvedorFiscalErro,
    ProvedorIndisponivel,
    RespostaFiscal,
    justificativa_valida,
)
from .gateway import GatewayHttp
from .homologacao_interna import HomologacaoInterna
from .sefaz_direto import SefazDireto
from .tpa_bridge import TpaBridge


PROVEDORES: dict[str, type[ProvedorFiscal]] = {
    provedor.nome: provedor
    for provedor in (TpaBridge, SefazDireto, GatewayHttp, HomologacaoInterna)
}

ROTULOS = {nome: classe.rotulo for nome, classe in PROVEDORES.items()}


def criar_provedor(nome: str, configuracao: dict) -> ProvedorFiscal:
    classe = PROVEDORES.get((nome or "").strip())
    if classe is None:
        raise ProvedorFiscalErro(f"Provedor fiscal desconhecido: {nome!r}.")
    return classe(configuracao)


__all__ = [
    "GatewayHttp",
    "HomologacaoInterna",
    "PROVEDORES",
    "ProvedorFiscal",
    "ProvedorFiscalErro",
    "ProvedorIndisponivel",
    "ROTULOS",
    "RespostaFiscal",
    "SefazDireto",
    "TpaBridge",
    "criar_provedor",
    "justificativa_valida",
]
