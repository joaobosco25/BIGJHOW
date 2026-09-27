from __future__ import annotations

import contextlib
import json
import os
import threading
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


class ErroMGV6(RuntimeError):
    """Erro de integração com o WebService oficial do MGV6."""


class ErroConfiguracaoMGV6(ErroMGV6):
    pass


class ErroComunicacaoMGV6(ErroMGV6):
    pass


class ErroRespostaMGV6(ErroMGV6):
    pass


@dataclass(frozen=True)
class ResultadoSincronizacaoPreco:
    codigo_item: str
    preco_confirmado: Decimal
    numero_importacao: int
    carga_solicitada: bool
    aviso_carga: str | None = None


_lock_local = threading.Lock()


def _texto_seguro(value: Any, limite: int = 500) -> str:
    if value is None:
        return ""
    texto = " ".join(str(value).split())
    return texto[:limite]


class ClienteMGV6:
    """Cliente REST mínimo para MGV6 1.6.x.

    A Toledo documenta que métodos que alteram dados não podem ser chamados em
    paralelo. Por isso o fluxo de importação é serializado por um lock de
    arquivo (entre os workers do Gunicorn) e por um lock local como fallback.

    A palavra-chave nunca é inserida em mensagens de erro nem logs.
    """

    def __init__(
        self,
        base_url: str,
        palavra_chave: str,
        *,
        loja: int = 1,
        balanca: int | None = 1,
        timeout: float = 5.0,
        solicitar_carga: bool = True,
        lock_path: str = "/tmp/bigjhow-mgv6.lock",
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.palavra_chave = palavra_chave
        self.loja = int(loja)
        self.balanca = int(balanca) if balanca is not None else None
        self.timeout = float(timeout)
        self.solicitar_carga = bool(solicitar_carga)
        self.lock_path = lock_path
        self.session = session or requests.Session()
        # Evita encaminhar chamadas para IP privado através de proxy herdado do SO.
        self.session.trust_env = False
        self._validar_configuracao()

    def _validar_configuracao(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ErroConfiguracaoMGV6("MGV6_BASE_URL inválida.")
        if not self.palavra_chave or len(self.palavra_chave) > 10:
            raise ErroConfiguracaoMGV6(
                "MGV6_PALAVRA_CHAVE deve ter entre 1 e 10 caracteres."
            )
        if self.loja <= 0:
            raise ErroConfiguracaoMGV6("MGV6_LOJA deve ser maior que zero.")
        if self.balanca is not None and self.balanca <= 0:
            raise ErroConfiguracaoMGV6("MGV6_BALANCA deve ser maior que zero.")
        if not (1 <= self.timeout <= 30):
            raise ErroConfiguracaoMGV6("MGV6_TIMEOUT deve ficar entre 1 e 30 segundos.")

    def _url(self, caminho: str) -> str:
        return f"{self.base_url}/{caminho.lstrip('/')}"

    def _json(self, resposta: requests.Response, operacao: str) -> dict[str, Any]:
        try:
            resposta.raise_for_status()
        except requests.HTTPError as exc:
            raise ErroComunicacaoMGV6(
                f"MGV6 respondeu HTTP {resposta.status_code} em {operacao}."
            ) from exc
        try:
            payload = resposta.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ErroRespostaMGV6(
                f"MGV6 devolveu uma resposta inválida em {operacao}."
            ) from exc
        if not isinstance(payload, dict):
            raise ErroRespostaMGV6(
                f"MGV6 devolveu um formato inesperado em {operacao}."
            )
        return payload

    def _mensagem_timeout(self, caminho: str, tipo: str) -> str:
        segundos = f"{self.timeout:g}s"
        if tipo == "conexao":
            return f"Não foi possível conectar ao MGV6 em {caminho} dentro de {segundos}."
        if tipo == "leitura":
            return (
                f"MGV6 aceitou a conexão, mas não respondeu em {caminho} "
                f"dentro de {segundos}."
            )
        return f"MGV6 excedeu o tempo limite em {caminho} ({segundos})."

    def _get(self, caminho: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            resposta = self.session.get(
                self._url(caminho), params=params or {}, timeout=self.timeout
            )
        except requests.ConnectTimeout as exc:
            raise ErroComunicacaoMGV6(
                self._mensagem_timeout(caminho, "conexao")
            ) from exc
        except requests.ReadTimeout as exc:
            raise ErroComunicacaoMGV6(
                self._mensagem_timeout(caminho, "leitura")
            ) from exc
        except requests.Timeout as exc:
            raise ErroComunicacaoMGV6(
                self._mensagem_timeout(caminho, "generico")
            ) from exc
        except requests.RequestException as exc:
            raise ErroComunicacaoMGV6(
                f"Falha de comunicação com o MGV6 em {caminho}."
            ) from exc
        return self._json(resposta, caminho)

    def _post(self, caminho: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resposta = self.session.post(
                self._url(caminho), json=payload, timeout=self.timeout
            )
        except requests.ConnectTimeout as exc:
            raise ErroComunicacaoMGV6(
                self._mensagem_timeout(caminho, "conexao")
            ) from exc
        except requests.ReadTimeout as exc:
            raise ErroComunicacaoMGV6(
                self._mensagem_timeout(caminho, "leitura")
            ) from exc
        except requests.Timeout as exc:
            raise ErroComunicacaoMGV6(
                self._mensagem_timeout(caminho, "generico")
            ) from exc
        except requests.RequestException as exc:
            raise ErroComunicacaoMGV6(
                f"Falha de comunicação com o MGV6 em {caminho}."
            ) from exc
        return self._json(resposta, caminho)

    @staticmethod
    def _erro_exportacao(payload: dict[str, Any], chave_resultado: str) -> None:
        erro = payload.get(chave_resultado)
        if erro:
            mensagem = _texto_seguro(
                erro.get("Msg") if isinstance(erro, dict) else erro
            ) or "O MGV6 recusou a consulta."
            raise ErroRespostaMGV6(mensagem)

    @staticmethod
    def _validar_retorno_importacao(
        valor: Any,
        *,
        operacao: str,
        aceitar_vazio: bool = False,
    ) -> None:
        # Métodos que retornam List<ERetornoImp> devolvem null ou [] quando
        # todos os registros foram importados corretamente.
        if valor is None or valor == []:
            if aceitar_vazio:
                return
            raise ErroRespostaMGV6(f"MGV6 não confirmou {operacao}.")

        registros = valor if isinstance(valor, list) else [valor]
        problemas: list[str] = []
        for registro in registros:
            if not isinstance(registro, dict):
                problemas.append(_texto_seguro(registro) or "retorno inválido")
                continue
            importado = registro.get("Importado")
            mensagem = _texto_seguro(registro.get("Msg"))
            codigo = _texto_seguro(registro.get("Codigo"))
            if importado is False or mensagem:
                detalhe = mensagem or f"código {codigo or 'desconhecido'}"
                problemas.append(detalhe)
        if problemas:
            raise ErroRespostaMGV6(
                f"MGV6 recusou {operacao}: {'; '.join(problemas[:3])}"
            )

    @staticmethod
    def _confirmar_resultado_importacao(
        payload: dict[str, Any], chave: str, operacao: str
    ) -> dict[str, Any]:
        valor = payload.get(chave)
        if not isinstance(valor, dict):
            raise ErroRespostaMGV6(f"MGV6 não confirmou {operacao}.")
        mensagem = _texto_seguro(valor.get("Msg"))
        if valor.get("Importado") is not True:
            raise ErroRespostaMGV6(
                mensagem or f"MGV6 recusou {operacao}."
            )
        return valor

    def versao(self) -> str:
        payload = self._get("ObtemVersao")
        versao = _texto_seguro(payload.get("ObtemVersaoResult"), 40)
        if not versao:
            raise ErroRespostaMGV6("MGV6 não informou a versão.")
        return versao

    def consultar_lojas(self) -> list[int]:
        payload = self._get(
            "ConsultaLojas", params={"palavraChave": self.palavra_chave}
        )
        self._erro_exportacao(payload, "ConsultaLojasResult")
        retorno = payload.get("retorno")
        if not isinstance(retorno, list):
            raise ErroRespostaMGV6("MGV6 não devolveu a lista de lojas.")
        return [int(item) for item in retorno]

    def consultar_item(self, codigo_item: str | int) -> dict[str, Any]:
        codigo = str(codigo_item).strip()
        if not codigo.isdigit() or not (1 <= len(codigo) <= 6):
            raise ErroConfiguracaoMGV6("Código MGV6 deve ter de 1 a 6 dígitos.")
        payload = self._get(
            "ConsultaItem",
            params={
                "palavraChave": self.palavra_chave,
                "loja": self.loja,
                "codItem": int(codigo),
            },
        )
        self._erro_exportacao(payload, "ConsultaItemResult")
        item = payload.get("item")
        if not isinstance(item, dict):
            raise ErroRespostaMGV6(f"Item MGV6 {codigo} não foi encontrado.")
        return item

    def detalhes_balancas(self) -> list[dict[str, Any]]:
        payload = self._get(
            "ExportaDetalhesBalancas",
            params={"palavraChave": self.palavra_chave, "loja": self.loja},
        )
        self._erro_exportacao(payload, "ExportaDetalhesBalancasResult")
        for chave in ("detalhesDaBalanca", "detalhesDasBalancas", "retorno"):
            valor = payload.get(chave)
            if isinstance(valor, list):
                return [item for item in valor if isinstance(item, dict)]
        return []

    def comunicacoes_pendentes(self) -> list[dict[str, Any]]:
        payload = self._get(
            "ExportaComunicacoesPendentes",
            params={"palavraChave": self.palavra_chave, "loja": self.loja},
        )
        self._erro_exportacao(payload, "ExportaComunicacoesPendentesResult")
        for chave in ("comunicacoes", "retorno"):
            valor = payload.get(chave)
            if isinstance(valor, list):
                return [item for item in valor if isinstance(item, dict)]
        return []

    @contextlib.contextmanager
    def _lock_importacao(self):
        """Serializa importações entre workers do Gunicorn."""
        with _lock_local:
            handle = None
            try:
                try:
                    import fcntl  # Linux no container de produção

                    caminho = Path(self.lock_path)
                    caminho.parent.mkdir(parents=True, exist_ok=True)
                    handle = caminho.open("a+")
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                except (ImportError, OSError):
                    # O lock local ainda protege uma única instância/processo.
                    handle = None
                yield
            finally:
                if handle is not None:
                    try:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    finally:
                        handle.close()

    def _iniciar_importacao(self) -> int:
        payload = self._post(
            "IniciaImportacao",
            {
                "loja": self.loja,
                "palavraChave": self.palavra_chave,
                "quantidadeDeArquivos": 1,
                "tipoDeImportacao": 1,
            },
        )
        valor = self._confirmar_resultado_importacao(
            payload, "IniciaImportacaoResult", "o início da importação"
        )
        codigo = _texto_seguro(valor.get("Codigo"), 20)
        if not codigo.isdigit():
            raise ErroRespostaMGV6("MGV6 não informou o número da importação.")

        # A documentação do MGV6 informa que IniciaImportacao pode cancelar uma
        # importação anterior que esteja em andamento e devolver esse fato em Msg.
        # Não seguimos silenciosamente nesse cenário: cancelamos a nossa sessão e
        # exigimos intervenção para evitar concorrência com o sistema legado.
        aviso = _texto_seguro(valor.get("Msg"))
        if aviso:
            numero = int(codigo)
            self._cancelar_importacao(numero)
            raise ErroRespostaMGV6(
                "MGV6 emitiu um alerta ao iniciar a importação: " + aviso
            )
        return int(codigo)

    def _cancelar_importacao(self, numero: int) -> None:
        with contextlib.suppress(Exception):
            self._post("CancelaImportacao", {"numeroDaImportacao": numero})

    def _importar_preco(self, numero: int, codigo_item: str, preco: Decimal) -> None:
        payload = self._post(
            "ImportaPreco",
            {
                "numeroDaImportacao": numero,
                "lista": [{"Codigo": codigo_item, "Preco": float(preco)}],
            },
        )
        self._validar_retorno_importacao(
            payload.get("ImportaPrecoResult"),
            operacao="a alteração de preço",
            aceitar_vazio=True,
        )

    def _solicitar_carga(self, numero: int) -> tuple[bool, str | None]:
        if not self.solicitar_carga or self.balanca is None:
            return False, None
        payload = self._post(
            "SolicitaCargaNaBalanca",
            {
                "numeroDaImportacao": numero,
                "solicitacaoCarga": {
                    "Balancas": [self.balanca],
                    "OpcoesDeComunicacao": [
                        {
                            # Item alterado, não uma carga completa.
                            "OpcoesComunicacaoInt": 2,
                            "Simultaneidade": False,
                            "TipoDeDadoInt": 3,
                        }
                    ],
                },
            },
        )
        valor = payload.get("SolicitaCargaNaBalancaResult")
        try:
            self._validar_retorno_importacao(
                valor,
                operacao="a solicitação de carga da balança",
                aceitar_vazio=True,
            )
        except ErroMGV6 as exc:
            # A alteração de preço no MGV6 continua válida mesmo se a balança
            # estiver offline. O chamador recebe o aviso e mantém estado pendente.
            return False, str(exc)
        return True, None

    def _finalizar_importacao(self, numero: int) -> None:
        payload = self._post("FinalizaImportacao", {"numeroDaImportacao": numero})
        self._confirmar_resultado_importacao(
            payload, "FinalizaImportacaoResult", "a finalização da importação"
        )

    def sincronizar_preco(
        self, codigo_item: str | int, preco: Decimal
    ) -> ResultadoSincronizacaoPreco:
        codigo = str(codigo_item).strip()
        if not codigo.isdigit() or not (1 <= len(codigo) <= 6):
            raise ErroConfiguracaoMGV6("Código MGV6 deve ter de 1 a 6 dígitos.")
        valor = Decimal(preco).quantize(Decimal("0.01"))
        if valor < 0:
            raise ErroConfiguracaoMGV6("Preço MGV6 não pode ser negativo.")

        with self._lock_importacao():
            numero = self._iniciar_importacao()
            carga_solicitada = False
            aviso_carga = None
            try:
                self._importar_preco(numero, codigo, valor)
                carga_solicitada, aviso_carga = self._solicitar_carga(numero)
                self._finalizar_importacao(numero)
            except Exception:
                self._cancelar_importacao(numero)
                raise

            # Verificação independente após a escrita: não confia apenas no HTTP 200.
            item = self.consultar_item(codigo)
            try:
                confirmado = Decimal(str(item.get("Preco"))).quantize(Decimal("0.01"))
            except Exception as exc:
                raise ErroRespostaMGV6(
                    "MGV6 não devolveu o preço do item após a importação."
                ) from exc
            if confirmado != valor:
                raise ErroRespostaMGV6(
                    f"MGV6 não confirmou o preço enviado para o item {codigo}."
                )

            return ResultadoSincronizacaoPreco(
                codigo_item=codigo,
                preco_confirmado=confirmado,
                numero_importacao=numero,
                carga_solicitada=carga_solicitada,
                aviso_carga=aviso_carga,
            )


def cliente_de_configuracao(config: dict[str, Any]) -> ClienteMGV6:
    if not config.get("MGV6_ENABLED"):
        raise ErroConfiguracaoMGV6("Integração MGV6 está desativada.")
    return ClienteMGV6(
        config.get("MGV6_BASE_URL", ""),
        config.get("MGV6_PALAVRA_CHAVE", ""),
        loja=config.get("MGV6_LOJA", 1),
        balanca=config.get("MGV6_BALANCA", 1),
        timeout=config.get("MGV6_TIMEOUT", 5),
        solicitar_carga=config.get("MGV6_SOLICITAR_CARGA", True),
        lock_path=config.get("MGV6_LOCK_PATH", "/tmp/bigjhow-mgv6.lock"),
    )
