"""Cliente somente-leitura para o BigJhow Fiscal Bridge.

FASE 0.5:
- não emite NFC-e;
- não cancela;
- não inutiliza;
- não grava no banco;
- não possui integração com o fluxo de venda.

Esta camada existe apenas para validar comunicação e diagnóstico.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class EslBridgeErro(RuntimeError):
    """Falha de configuração ou comunicação com o bridge."""


@dataclass(frozen=True, slots=True)
class EslBridgeConfiguracao:
    url_base: str
    token: str
    timeout: float = 5.0

    @classmethod
    def do_ambiente(cls) -> "EslBridgeConfiguracao":
        url = (
            os.getenv(
                "ESL_BRIDGE_URL",
                "http://host.docker.internal:12165",
            )
            .strip()
            .rstrip("/")
        )

        token = os.getenv("ESL_BRIDGE_TOKEN", "").strip()

        timeout_bruto = os.getenv("ESL_BRIDGE_TIMEOUT", "5").strip()
        try:
            timeout = float(timeout_bruto)
        except ValueError as exc:
            raise EslBridgeErro(
                "ESL_BRIDGE_TIMEOUT precisa ser numérico."
            ) from exc

        if not url:
            raise EslBridgeErro("ESL_BRIDGE_URL não configurada.")

        if len(token) < 32:
            raise EslBridgeErro(
                "ESL_BRIDGE_TOKEN ausente ou curto."
            )

        if not 0.5 <= timeout <= 30:
            raise EslBridgeErro(
                "ESL_BRIDGE_TIMEOUT deve ficar entre 0.5 e 30 segundos."
            )

        return cls(
            url_base=url,
            token=token,
            timeout=timeout,
        )


class EslBridgeCliente:
    """Cliente HTTP estritamente somente-leitura da Fase 0."""

    _ENDPOINTS_PERMITIDOS = frozenset({
        "status",
        "capacidades",
        "diagnostico",
    })

    def __init__(
        self,
        configuracao: EslBridgeConfiguracao | None = None,
    ) -> None:
        self.configuracao = (
            configuracao or EslBridgeConfiguracao.do_ambiente()
        )

    def _get(self, endpoint: str) -> dict[str, Any]:
        if endpoint not in self._ENDPOINTS_PERMITIDOS:
            raise EslBridgeErro(
                f"Endpoint não permitido na Fase 0.5: {endpoint!r}."
            )

        url = f"{self.configuracao.url_base}/v1/{endpoint}"

        requisicao = urllib.request.Request(
            url,
            headers={
                "X-BigJhow-Bridge-Token":
                    self.configuracao.token,
                "Accept": "application/json",
            },
            method="GET",
        )

        try:
            with urllib.request.urlopen(
                requisicao,
                timeout=self.configuracao.timeout,
            ) as resposta:
                conteudo = resposta.read()

                if resposta.status != 200:
                    raise EslBridgeErro(
                        f"Bridge respondeu HTTP {resposta.status}."
                    )

        except urllib.error.HTTPError as exc:
            raise EslBridgeErro(
                f"Bridge respondeu HTTP {exc.code}."
            ) from exc

        except urllib.error.URLError as exc:
            raise EslBridgeErro(
                f"Não foi possível acessar o Fiscal Bridge: "
                f"{exc.reason!s}."
            ) from exc

        except TimeoutError as exc:
            raise EslBridgeErro(
                "Tempo limite ao acessar o Fiscal Bridge."
            ) from exc

        try:
            dados = json.loads(conteudo.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EslBridgeErro(
                "Fiscal Bridge retornou JSON inválido."
            ) from exc

        if not isinstance(dados, dict):
            raise EslBridgeErro(
                "Fiscal Bridge retornou estrutura inesperada."
            )

        return dados

    def status(self) -> dict[str, Any]:
        return self._get("status")

    def capacidades(self) -> dict[str, Any]:
        return self._get("capacidades")

    def diagnostico(self) -> dict[str, Any]:
        return self._get("diagnostico")
