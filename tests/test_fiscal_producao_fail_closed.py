import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from bigjhow.services.fiscal import legado_producao
from bigjhow.services.fiscal.xml_nfce import XmlFiscalErro


ROOT = Path(__file__).resolve().parents[1]
CATALOGO_REAL = ROOT / "instance" / "fiscal_legado_producao.json"


def estabelecimento(
    *,
    ambiente=1,
    serie=1,
    modelo=65,
    empresa_id=1,
):
    return SimpleNamespace(
        empresa_id=empresa_id,
        cnpj="11222333000181",
        ambiente=ambiente,
        modelo=modelo,
        serie=serie,
        crt=3,
        uf="MG",
        provedor="sefaz_direto",
    )


def escrever_catalogo(tmp_path, mutacao=None):
    data = json.loads(
        CATALOGO_REAL.read_text(encoding="utf-8")
    )

    if mutacao is not None:
        mutacao(data)

    destino = tmp_path / "fiscal_legado_producao.json"
    destino.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )

    return data


def app_para(tmp_path):
    return Flask(
        __name__,
        instance_path=str(tmp_path),
        instance_relative_config=False,
    )


def test_catalogo_real_comeca_totalmente_fechado():
    data = json.loads(
        CATALOGO_REAL.read_text(encoding="utf-8")
    )

    assert data["empresa_id"] == 1
    assert data["ambiente"] == 1
    assert data["serie"] == 1

    assert data["habilitado"] is False
    assert data["producao_bloqueada"] is True

    assert len(data["produtos"]) == 1137

    liberados = [
        produto_id
        for produto_id, entry in data["produtos"].items()
        if entry.get("liberado_producao") is True
    ]

    assert liberados == []


def test_trava_principal_bloqueia(tmp_path):
    escrever_catalogo(tmp_path)

    app = app_para(tmp_path)

    with app.app_context():
        with pytest.raises(
            XmlFiscalErro,
            match="Produção fiscal ainda não habilitada",
        ):
            legado_producao.carregar(estabelecimento())


def test_trava_secundaria_bloqueia(tmp_path):
    def mutar(data):
        data["habilitado"] = True
        data["producao_bloqueada"] = True

    escrever_catalogo(tmp_path, mutar)

    app = app_para(tmp_path)

    with app.app_context():
        with pytest.raises(
            XmlFiscalErro,
            match="Produção fiscal permanece bloqueada",
        ):
            legado_producao.carregar(estabelecimento())


def test_homologacao_nao_usa_catalogo_producao(tmp_path):
    escrever_catalogo(tmp_path)

    app = app_para(tmp_path)

    with app.app_context():
        resultado = legado_producao.carregar(
            estabelecimento(
                ambiente=2,
                serie=9,
            )
        )

    assert resultado is None


def test_serie_errada_bloqueia(tmp_path):
    def mutar(data):
        data["habilitado"] = True
        data["producao_bloqueada"] = False

    escrever_catalogo(tmp_path, mutar)

    app = app_para(tmp_path)

    with app.app_context():
        with pytest.raises(
            XmlFiscalErro,
            match="Produção controlada exclusiva",
        ):
            legado_producao.carregar(
                estabelecimento(serie=9)
            )


def test_produto_nao_liberado_bloqueia(tmp_path):
    def mutar(data):
        data["habilitado"] = True
        data["producao_bloqueada"] = False

    data = escrever_catalogo(tmp_path, mutar)

    produto_id = next(iter(data["produtos"]))

    app = app_para(tmp_path)

    with app.app_context():
        catalogo = legado_producao.carregar(
            estabelecimento()
        )

        with pytest.raises(
            XmlFiscalErro,
            match="Produto ainda não liberado",
        ):
            legado_producao.produto_liberado(
                catalogo,
                produto_id,
            )


def test_produto_liberado_explicitamente_passa(tmp_path):
    produto_escolhido = {"id": None}

    def mutar(data):
        data["habilitado"] = True
        data["producao_bloqueada"] = False

        for produto_id, entry in data["produtos"].items():
            if (
                entry.get(
                    "revisao_fiscal_producao_pendente"
                )
                is not True
            ):
                entry["liberado_producao"] = True
                entry[
                    "motivo_bloqueio_producao"
                ] = None

                produto_escolhido["id"] = produto_id
                break

        assert produto_escolhido["id"] is not None

    escrever_catalogo(tmp_path, mutar)

    app = app_para(tmp_path)

    with app.app_context():
        catalogo = legado_producao.carregar(
            estabelecimento()
        )

        entry = legado_producao.produto_liberado(
            catalogo,
            produto_escolhido["id"],
        )

    assert entry["liberado_producao"] is True


def test_emissao_contem_rota_producao_e_homologacao():
    emissao = (
        ROOT
        / "bigjhow"
        / "services"
        / "fiscal"
        / "emissao.py"
    ).read_text(encoding="utf-8")

    assert (
        "legado_producao.carregar(estabelecimento)"
        in emissao
    )

    assert (
        "legado_producao.produto_liberado("
        in emissao
    )

    assert (
        "legado_homologacao.carregar(estabelecimento)"
        in emissao
    )

    assert (
        "Venda bloqueada pela proteção fiscal de produção"
        in emissao
    )
