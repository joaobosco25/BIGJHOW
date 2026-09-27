from __future__ import annotations

from decimal import Decimal

import pytest

from bigjhow.services.mgv6_cliente import ClienteMGV6, ErroRespostaMGV6


class RespostaFake:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError()

    def json(self):
        return self.payload


class SessaoFake:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []
        self.trust_env = True

    def get(self, url, params=None, timeout=None):
        self.chamadas.append(("GET", url, params, timeout))
        return RespostaFake(self.respostas.pop(0))

    def post(self, url, json=None, timeout=None):
        self.chamadas.append(("POST", url, json, timeout))
        return RespostaFake(self.respostas.pop(0))


def test_consulta_item_usa_codigo_mestre_e_nao_plu_expandido():
    sessao = SessaoFake([
        {
            "ConsultaItemResult": None,
            "item": {"Codigo": "27", "Descritivo1aLinha": "PRIME STEAK", "Preco": 59.99},
        }
    ])
    cliente = ClienteMGV6(
        "http://192.0.2.200:9300/MGV6_WCF/REST",
        "abc123",
        loja=1,
        session=sessao,
    )

    item = cliente.consultar_item("27")

    assert item["Codigo"] == "27"
    metodo, url, params, timeout = sessao.chamadas[0]
    assert metodo == "GET"
    assert url.endswith("/ConsultaItem")
    assert params["loja"] == 1
    assert params["codItem"] == 27
    assert params["palavraChave"] == "abc123"
    assert timeout == 5


def test_sincronizacao_preco_segue_fluxo_oficial_e_confere_preco():
    sessao = SessaoFake([
        {"IniciaImportacaoResult": {"Codigo": "41", "Importado": True, "Msg": None}},
        {"ImportaPrecoResult": []},
        {"SolicitaCargaNaBalancaResult": {"Codigo": "", "Importado": True, "Msg": None}},
        {"FinalizaImportacaoResult": {"Codigo": "41", "Importado": True, "Msg": None}},
        {"ConsultaItemResult": None, "item": {"Codigo": "27", "Preco": 64.90}},
    ])
    cliente = ClienteMGV6(
        "http://192.0.2.200:9300/MGV6_WCF/REST",
        "abc123",
        loja=1,
        balanca=1,
        session=sessao,
        lock_path="/tmp/teste-mgv6.lock",
    )

    resultado = cliente.sincronizar_preco("27", Decimal("64.90"))

    assert resultado.preco_confirmado == Decimal("64.90")
    assert resultado.numero_importacao == 41
    assert resultado.carga_solicitada is True
    posts = [c for c in sessao.chamadas if c[0] == "POST"]
    assert [c[1].rsplit("/", 1)[-1] for c in posts] == [
        "IniciaImportacao",
        "ImportaPreco",
        "SolicitaCargaNaBalanca",
        "FinalizaImportacao",
    ]
    assert posts[1][2] == {
        "numeroDaImportacao": 41,
        "lista": [{"Codigo": "27", "Preco": 64.9}],
    }
    assert posts[2][2] == {
        "numeroDaImportacao": 41,
        "solicitacaoCarga": {
            "Balancas": [1],
            "OpcoesDeComunicacao": [
                {
                    "OpcoesComunicacaoInt": 2,
                    "Simultaneidade": False,
                    "TipoDeDadoInt": 3,
                }
            ],
        },
    }


def test_erro_semantico_do_importa_preco_cancela_importacao():
    sessao = SessaoFake([
        {"IniciaImportacaoResult": {"Codigo": "9", "Importado": True, "Msg": None}},
        {"ImportaPrecoResult": [{"Codigo": "27", "Importado": False, "Msg": "Item inválido"}]},
        {"CancelaImportacaoResult": {"Codigo": "", "Importado": True, "Msg": None}},
    ])
    cliente = ClienteMGV6(
        "http://192.0.2.200:9300/MGV6_WCF/REST",
        "abc123",
        loja=1,
        balanca=1,
        session=sessao,
        lock_path="/tmp/teste-mgv6-erro.lock",
    )

    with pytest.raises(ErroRespostaMGV6, match="Item inválido"):
        cliente.sincronizar_preco("27", Decimal("64.90"))

    assert sessao.chamadas[-1][1].endswith("/CancelaImportacao")


def test_resposta_http_200_com_erro_de_consulta_nao_e_aceita():
    sessao = SessaoFake([
        {"ConsultaItemResult": {"Msg": "Palavra chave inválida."}, "item": None}
    ])
    cliente = ClienteMGV6(
        "http://192.0.2.200:9300/MGV6_WCF/REST",
        "abc123",
        loja=1,
        session=sessao,
    )

    with pytest.raises(ErroRespostaMGV6, match="Palavra chave inválida"):
        cliente.consultar_item("27")


def test_alerta_ao_iniciar_importacao_interrompe_fluxo():
    sessao = SessaoFake([
        {
            "IniciaImportacaoResult": {
                "Codigo": "15",
                "Importado": True,
                "Msg": "Havia outra importação em andamento e ela foi cancelada.",
            }
        },
        {"CancelaImportacaoResult": {"Codigo": "15", "Importado": True, "Msg": None}},
    ])
    cliente = ClienteMGV6(
        "http://192.0.2.200:9300/MGV6_WCF/REST",
        "abc123",
        loja=1,
        balanca=1,
        session=sessao,
        lock_path="/tmp/teste-mgv6-concorrencia.lock",
    )

    with pytest.raises(ErroRespostaMGV6, match="alerta ao iniciar"):
        cliente.sincronizar_preco("27", Decimal("64.90"))

    assert [c[1].rsplit("/", 1)[-1] for c in sessao.chamadas] == [
        "IniciaImportacao",
        "CancelaImportacao",
    ]


def test_timeout_de_leitura_informa_endpoint_sem_expor_segredo():
    import requests

    class SessaoTimeout:
        trust_env = True

        def post(self, url, json=None, timeout=None):
            raise requests.ReadTimeout("segredo-nao-pode-vazar")

    cliente = ClienteMGV6(
        "http://192.0.2.200:9300/MGV6_WCF/REST",
        "abc123",
        loja=1,
        balanca=1,
        timeout=20,
        solicitar_carga=False,
        session=SessaoTimeout(),
        lock_path="/tmp/teste-mgv6-timeout.lock",
    )

    from bigjhow.services.mgv6_cliente import ErroComunicacaoMGV6

    with pytest.raises(ErroComunicacaoMGV6) as excinfo:
        cliente.sincronizar_preco("27", Decimal("59.99"))

    mensagem = str(excinfo.value)
    assert "IniciaImportacao" in mensagem
    assert "20s" in mensagem
    assert "segredo" not in mensagem


def test_timeout_em_importa_preco_informa_etapa_exata():
    import requests

    class SessaoTimeoutImportaPreco:
        def __init__(self):
            self.trust_env = True
            self.contagem = 0

        def post(self, url, json=None, timeout=None):
            self.contagem += 1
            if url.endswith("/IniciaImportacao"):
                return RespostaFake({
                    "IniciaImportacaoResult": {
                        "Codigo": "77",
                        "Importado": True,
                        "Msg": None,
                    }
                })
            if url.endswith("/ImportaPreco"):
                raise requests.ReadTimeout("timeout")
            if url.endswith("/CancelaImportacao"):
                return RespostaFake({
                    "CancelaImportacaoResult": {
                        "Codigo": "77",
                        "Importado": True,
                        "Msg": None,
                    }
                })
            raise AssertionError(url)

    cliente = ClienteMGV6(
        "http://192.0.2.200:9300/MGV6_WCF/REST",
        "abc123",
        loja=1,
        balanca=1,
        timeout=20,
        solicitar_carga=False,
        session=SessaoTimeoutImportaPreco(),
        lock_path="/tmp/teste-mgv6-timeout-preco.lock",
    )

    from bigjhow.services.mgv6_cliente import ErroComunicacaoMGV6

    with pytest.raises(ErroComunicacaoMGV6, match="ImportaPreco"):
        cliente.sincronizar_preco("27", Decimal("59.99"))
