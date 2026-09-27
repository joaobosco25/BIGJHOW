from __future__ import annotations

from decimal import Decimal

import pytest

from bigjhow.extensions import db
from bigjhow.models import Produto, Usuario
from bigjhow.services.mgv6_cliente import ResultadoSincronizacaoPreco
from bigjhow.services.mgv6_integracao import (
    VinculoMGV6Invalido,
    atualizar_vinculo,
    confirmar_balanca,
    pode_usar_etiqueta,
    sincronizar_preco,
    validar_vinculo,
)


class ClienteMGVFake:
    def __init__(self, *, descricao="Produto Teste", preco=Decimal("10.00"), carga=True):
        self.descricao = descricao
        self.preco = Decimal(preco)
        self.carga = carga
        self.enviados = []

    def consultar_item(self, codigo):
        return {
            "Codigo": str(codigo),
            "Descritivo1Linha": self.descricao,
            "Preco": float(self.preco),
            "CodDepartamento": 1,
            "TipoVendaInt": 0,
            "ListaDeBalancaAssociadas": [1],
        }

    def sincronizar_preco(self, codigo, preco):
        valor = Decimal(preco).quantize(Decimal("0.01"))
        self.enviados.append((str(codigo), valor))
        self.preco = valor
        return ResultadoSincronizacaoPreco(
            codigo_item=str(codigo),
            preco_confirmado=valor,
            numero_importacao=41,
            carga_solicitada=self.carga,
            aviso_carga=None,
        )


def _owner():
    return db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))


def test_vinculo_valida_descricao_e_preco(app, monkeypatch):
    from bigjhow.services import mgv6_integracao as svc

    with app.app_context():
        app.config["MGV6_ENABLED"] = True
        produto = db.session.get(Produto, app.config["TEST_PRODUCT_ID"])
        owner = _owner()
        atualizar_vinculo(produto, "27", usuario_id=owner.id)
        db.session.commit()
        fake = ClienteMGVFake(descricao=produto.nome, preco=Decimal("10.00"))
        monkeypatch.setattr(svc, "cliente_de_configuracao", lambda config: fake)

        vinculo = validar_vinculo(produto, usuario_id=owner.id)

        assert vinculo.codigo_item == "27"
        assert vinculo.status == "validado"
        assert vinculo.preco_mgv == Decimal("10.00")


def test_codigo_que_aponta_para_outro_produto_e_bloqueado(app, monkeypatch):
    from bigjhow.services import mgv6_integracao as svc

    with app.app_context():
        app.config["MGV6_ENABLED"] = True
        produto = db.session.get(Produto, app.config["TEST_PRODUCT_ID"])
        owner = _owner()
        atualizar_vinculo(produto, "27", usuario_id=owner.id)
        db.session.commit()
        fake = ClienteMGVFake(descricao="FILE TILAPIA 500G", preco=Decimal("28.99"))
        monkeypatch.setattr(svc, "cliente_de_configuracao", lambda config: fake)

        with pytest.raises(VinculoMGV6Invalido, match="FILE TILAPIA"):
            validar_vinculo(produto, usuario_id=owner.id)

        assert produto.mgv6.status == "erro"


def test_preco_sincronizado_aguarda_confirmacao_fisica_da_balanca(app, monkeypatch):
    from bigjhow.services import mgv6_integracao as svc

    with app.app_context():
        app.config["MGV6_ENABLED"] = True
        app.config["MGV6_WRITE_ENABLED"] = True
        produto = db.session.get(Produto, app.config["TEST_PRODUCT_ID"])
        owner = _owner()
        atualizar_vinculo(produto, "27", usuario_id=owner.id)
        db.session.commit()
        fake = ClienteMGVFake(descricao=produto.nome, preco=Decimal("10.00"), carga=True)
        monkeypatch.setattr(svc, "cliente_de_configuracao", lambda config: fake)
        validar_vinculo(produto, usuario_id=owner.id)

        produto.preco_venda = Decimal("12.50")
        db.session.commit()
        vinculo = sincronizar_preco(produto, usuario_id=owner.id)

        assert fake.enviados == [("27", Decimal("12.50"))]
        assert vinculo.preco_mgv == Decimal("12.50")
        assert vinculo.status == "aguardando_balanca"
        permitido, mensagem = pode_usar_etiqueta(produto)
        assert permitido is False
        assert "balança ainda não foi confirmada" in mensagem

        confirmar_balanca(produto, usuario_id=owner.id)
        permitido, mensagem = pode_usar_etiqueta(produto)
        assert permitido is True
        assert mensagem is None


def test_escrita_desativada_bloqueia_sincronizacao(app, monkeypatch):
    from bigjhow.services import mgv6_integracao as svc

    with app.app_context():
        app.config["MGV6_ENABLED"] = True
        app.config["MGV6_WRITE_ENABLED"] = False
        produto = db.session.get(Produto, app.config["TEST_PRODUCT_ID"])
        owner = _owner()
        atualizar_vinculo(produto, "27", usuario_id=owner.id)
        db.session.commit()
        fake = ClienteMGVFake(descricao=produto.nome, preco=Decimal("10.00"), carga=True)
        monkeypatch.setattr(svc, "cliente_de_configuracao", lambda config: fake)

        with pytest.raises(VinculoMGV6Invalido, match="somente quando nenhum sistema legado"):
            sincronizar_preco(produto, usuario_id=owner.id)

        assert fake.enviados == []


def test_produto_pesado_rejeita_item_mgv_vendido_por_unidade(app, monkeypatch):
    from bigjhow.services import mgv6_integracao as svc

    with app.app_context():
        app.config["MGV6_ENABLED"] = True
        app.config["MGV6_BALANCA"] = 1
        produto = db.session.get(Produto, app.config["TEST_PRODUCT_ID"])
        produto.pesado = True
        owner = _owner()
        atualizar_vinculo(produto, "27", usuario_id=owner.id)
        db.session.commit()

        fake = ClienteMGVFake(descricao=produto.nome, preco=Decimal("10.00"))
        original = fake.consultar_item
        fake.consultar_item = lambda codigo: {**original(codigo), "TipoVendaInt": 1}
        monkeypatch.setattr(svc, "cliente_de_configuracao", lambda config: fake)

        with pytest.raises(VinculoMGV6Invalido, match="venda por peso"):
            validar_vinculo(produto, usuario_id=owner.id)


def test_produto_pesado_exige_associacao_com_balanca_configurada(app, monkeypatch):
    from bigjhow.services import mgv6_integracao as svc

    with app.app_context():
        app.config["MGV6_ENABLED"] = True
        app.config["MGV6_BALANCA"] = 1
        produto = db.session.get(Produto, app.config["TEST_PRODUCT_ID"])
        produto.pesado = True
        owner = _owner()
        atualizar_vinculo(produto, "27", usuario_id=owner.id)
        db.session.commit()

        fake = ClienteMGVFake(descricao=produto.nome, preco=Decimal("10.00"))
        original = fake.consultar_item
        fake.consultar_item = lambda codigo: {
            **original(codigo),
            "ListaDeBalancaAssociadas": [2],
        }
        monkeypatch.setattr(svc, "cliente_de_configuracao", lambda config: fake)

        with pytest.raises(VinculoMGV6Invalido, match="balança 1"):
            validar_vinculo(produto, usuario_id=owner.id)
