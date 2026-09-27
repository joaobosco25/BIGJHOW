from __future__ import annotations

import re
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from bigjhow.extensions import db
from bigjhow.models import (
    Auditoria,
    CodigoProduto,
    MovimentoEstoque,
    Produto,
    SaldoEstoque,
    Usuario,
    Venda,
    VendaItem,
    VendaPagamento,
)
from bigjhow.services.caixa import (
    CaixaErro,
    _preparar_pagamentos,
    cancelar_venda,
    finalizar_venda,
    iniciar_venda,
    normalizar_dinheiro,
    normalizar_quantidade,
)
from bigjhow.services.codigos import (
    CodigoProdutoConflitante,
    CodigoProdutoInvalido,
    alterar_status_codigo,
    cadastrar_codigo,
    buscar_codigo,
    normalizar_codigo,
    validar_tipo,
)

from .conftest import login


def _usuario_id(usuario: str):
    return db.session.scalar(
        db.select(Usuario.id).where(Usuario.usuario == usuario)
    )


def _produto_pronto(app, *, quantidade="10.000", preco="10.00"):
    with app.app_context():
        produto = db.session.get(Produto, app.config["TEST_PRODUCT_ID"])
        produto.preco_venda = Decimal(preco)
        produto.saldo.quantidade = Decimal(quantidade)
        produto.saldo.aguardando_inventario = False
        db.session.commit()
        return produto.id


def _novo_produto(
    *,
    empresa_id=1,
    nome="Produto Pesado",
    codigo="PESO-001",
    quantidade="8.000",
    preco="20.00",
    pesado=True,
):
    produto = Produto(
        empresa_id=empresa_id,
        sku_interno=f"TEST-{empresa_id}-{codigo}",
        nome=nome,
        categoria="Testes",
        unidade="KG" if pesado else "UN",
        pesado=pesado,
        preco_venda=Decimal(preco),
    )
    produto.saldo = SaldoEstoque(
        quantidade=Decimal(quantidade), aguardando_inventario=False
    )
    db.session.add(produto)
    db.session.flush()
    exibicao, chave = normalizar_codigo(codigo)
    db.session.add(
        CodigoProduto(
            empresa_id=empresa_id,
            produto_id=produto.id,
            codigo=exibicao,
            chave_busca=chave,
            tipo="interno",
            principal=True,
        )
    )
    db.session.commit()
    return produto.id


def _venda_aberta(app, usuario="operador.carnes"):
    with app.app_context():
        venda = iniciar_venda(empresa_id=1, usuario_id=_usuario_id(usuario))
        return venda.id


def _adicionar_codigo(client, venda_id, versao, codigo="001", quantidade=""):
    return client.post(
        f"/empresa/1/caixa/{venda_id}/ler",
        data={
            "leitura-versao": str(versao),
            "leitura-codigo": codigo,
            "leitura-quantidade": quantidade,
        },
        follow_redirects=True,
    )


def _finalizar(client, venda_id, versao, **pagamentos):
    dados = {
        "finalizar-versao": str(versao),
        "finalizar-pagamento_dinheiro": "",
        "finalizar-dinheiro_recebido": "",
        "finalizar-pagamento_pix": "",
        "finalizar-pagamento_debito": "",
        "finalizar-pagamento_credito": "",
        "finalizar-confirmacao": "y",
    }
    dados.update(pagamentos)
    return client.post(
        f"/empresa/1/caixa/{venda_id}/finalizar",
        data=dados,
        follow_redirects=False,
    )


def test_code_normalization_and_ean_validation_are_strict():
    assert normalizar_codigo(" ab-c.123 ") == ("AB-C.123", "ABC123")
    assert validar_tipo("ean", "7894900011517") == "ean"
    with pytest.raises(CodigoProdutoInvalido):
        normalizar_codigo("123 <script>")
    with pytest.raises(CodigoProdutoInvalido):
        validar_tipo("ean", "7894900011518")
    with pytest.raises(CodigoProdutoInvalido):
        validar_tipo("inventado", "ABC123")
    with pytest.raises(CaixaErro):
        normalizar_quantidade("1e3", pesado=True)
    with pytest.raises(CaixaErro):
        normalizar_quantidade("0", pesado=True)
    with pytest.raises(CaixaErro):
        normalizar_dinheiro("10.999")
    with pytest.raises(CaixaErro):
        normalizar_dinheiro("0")


def test_operator_can_use_cashier_but_cannot_manage_codes_or_other_company(app, client):
    login(client, "operador.carnes")
    produto_id = app.config["TEST_PRODUCT_ID"]

    caixa = client.get("/empresa/1/caixa")
    assert caixa.status_code == 302
    assert caixa.headers["Location"].endswith("/empresa/1/operacao")
    painel = client.get(caixa.headers["Location"])
    assert "Leia os produtos, receba o pagamento" in painel.text
    assert "Menu do caixa" in painel.text
    assert "Operação do caixa" not in painel.text
    assert painel.headers["Cache-Control"].startswith("no-store")
    assert client.get("/empresa/2/caixa").status_code == 403
    assert client.get(f"/empresa/1/estoque/{produto_id}/codigos").status_code == 403
    assert (
        client.post(
            f"/empresa/1/estoque/{produto_id}/codigos",
            data={"codigo": "NOVO-001", "tipo": "interno"},
        ).status_code
        == 403
    )


def test_cashier_uses_single_sale_flow_and_keeps_history_out_of_sale_screen(app, client):
    _produto_pronto(app)
    login(client, "operador.carnes")
    venda_id = _venda_aberta(app)
    _adicionar_codigo(client, venda_id, 1)

    page = client.get("/empresa/1/operacao")
    assert page.status_code == 200
    assert 'data-checkout' in page.text
    assert "Cobrar" in page.text
    assert "Selecione como o cliente pagou" in page.text
    assert page.text.count('data-payment-method=') == 5
    assert "checkout-steps" not in page.text
    assert "Últimos turnos" not in page.text
    assert client.get("/empresa/1/operacao/historico").status_code == 200

    invalid = client.post(
        f"/empresa/1/caixa/{venda_id}/finalizar",
        data={
            "finalizar-versao": "2",
            "finalizar-pagamento_pix": "9,00",
        },
    )
    assert invalid.status_code == 302
    assert invalid.headers["Location"].endswith("/empresa/1/operacao?etapa=pagamento")
    payment = client.get(invalid.headers["Location"])
    assert 'data-initial-step="pagamento"' in payment.text


def test_cashier_mutation_requires_csrf_token_when_protection_is_enabled(app, client):
    login(client, "operador.carnes")
    app.config["WTF_CSRF_ENABLED"] = True
    page = client.get("/empresa/1/operacao")
    token = re.search(
        r'name="csrf_token" type="hidden" value="([^"]+)"', page.text
    ).group(1)
    assert client.post("/empresa/1/caixa/iniciar").status_code == 400
    accepted = client.post(
        "/empresa/1/caixa/iniciar", data={"csrf_token": token}
    )
    assert accepted.status_code == 302


def test_code_is_unique_by_canonical_key_and_inactive_code_stays_reserved(app, client):
    login(client)
    produto_id = app.config["TEST_PRODUCT_ID"]
    endereco = f"/empresa/1/estoque/{produto_id}/codigos"
    criado = client.post(
        endereco,
        data={"codigo": "AB-123", "tipo": "interno", "principal": "y"},
    )
    assert criado.status_code == 302
    with app.app_context():
        novo = db.session.scalar(
            db.select(CodigoProduto).where(CodigoProduto.chave_busca == "AB123")
        )
        antigo = db.session.scalar(
            db.select(CodigoProduto).where(CodigoProduto.chave_busca == "001")
        )
        assert novo.principal is True
        assert antigo.principal is False
        novo_id, versao = novo.id, novo.versao

    desativado = client.post(
        f"{endereco}/{novo_id}/status",
        data={f"status-{novo_id}-versao": str(versao)},
    )
    assert desativado.status_code == 302
    conflito = client.post(
        endereco,
        data={"codigo": "AB123", "tipo": "interno"},
        follow_redirects=True,
    )
    assert conflito.status_code == 200
    assert "já está reservado" in conflito.text
    with app.app_context():
        assert db.session.scalar(
            db.select(db.func.count(CodigoProduto.id)).where(
                CodigoProduto.chave_busca == "AB123"
            )
        ) == 1


def test_database_rejects_inactive_primary_code(app):
    with app.app_context():
        produto_id = app.config["TEST_PRODUCT_ID"]
        db.session.add(
            CodigoProduto(
                empresa_id=1,
                produto_id=produto_id,
                codigo="INVALID-PRIMARY",
                chave_busca="INVALIDPRIMARY",
                tipo="interno",
                principal=True,
                ativo=False,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_code_status_rotation_reactivation_and_stale_version_are_controlled(app):
    with app.app_context():
        produto_id = app.config["TEST_PRODUCT_ID"]
        usuario_id = _usuario_id("dono")
        novo = cadastrar_codigo(
            empresa_id=1,
            produto_id=produto_id,
            valor="SEC-002",
            tipo="interno",
            principal=False,
            usuario_id=usuario_id,
        )
        assert buscar_codigo(1, "sec002", bloquear=True).id == novo.id
        antigo = db.session.scalar(
            db.select(CodigoProduto).where(CodigoProduto.chave_busca == "001")
        )
        antigo_id, antigo_versao = antigo.id, antigo.versao
        alterar_status_codigo(
            empresa_id=1,
            produto_id=produto_id,
            codigo_id=antigo_id,
            versao=antigo_versao,
            usuario_id=usuario_id,
        )
        assert db.session.get(CodigoProduto, novo.id).principal is True
        antigo = db.session.get(CodigoProduto, antigo_id)
        alterar_status_codigo(
            empresa_id=1,
            produto_id=produto_id,
            codigo_id=antigo_id,
            versao=antigo.versao,
            usuario_id=usuario_id,
        )
        antigo = db.session.get(CodigoProduto, antigo_id)
        assert antigo.ativo is True
        assert antigo.principal is False
        with pytest.raises(CodigoProdutoConflitante):
            alterar_status_codigo(
                empresa_id=1,
                produto_id=produto_id,
                codigo_id=antigo_id,
                versao=antigo.versao - 1,
                usuario_id=usuario_id,
            )
        db.session.rollback()


def test_same_code_can_exist_in_another_company_without_cross_company_resolution(app):
    with app.app_context():
        _novo_produto(empresa_id=2, codigo="001", pesado=False)
        assert db.session.scalar(
            db.select(db.func.count(CodigoProduto.id)).where(
                CodigoProduto.chave_busca == "001"
            )
        ) == 2


def test_stock_filter_lists_active_products_without_cashier_code(app, client):
    with app.app_context():
        sem_codigo = Produto(
            empresa_id=1,
            sku_interno="SEM-CODIGO-001",
            nome="Produto sem código de caixa",
            categoria="Testes",
            unidade="UN",
            preco_venda=Decimal("12.00"),
        )
        sem_codigo.saldo = SaldoEstoque(
            quantidade=Decimal("1.000"), aguardando_inventario=False
        )
        db.session.add(sem_codigo)
        db.session.commit()
    login(client)
    resposta = client.get("/empresa/1/estoque?situacao=sem_codigo")
    assert resposta.status_code == 200
    assert "Sem código (1)" in resposta.text
    assert "Produto sem código de caixa" in resposta.text
    assert "Produto Teste" not in resposta.text


def test_random_pending_unpriced_and_insufficient_products_are_blocked(app, client):
    login(client, "operador.carnes")
    venda_id = _venda_aberta(app)

    desconhecido = _adicionar_codigo(client, venda_id, 1, "999999")
    assert "Código não cadastrado para esta empresa" in desconhecido.text
    pendente = _adicionar_codigo(client, venda_id, 1)
    assert "ainda aguarda inventário" in pendente.text

    with app.app_context():
        produto = db.session.get(Produto, app.config["TEST_PRODUCT_ID"])
        produto.saldo.aguardando_inventario = False
        produto.saldo.quantidade = Decimal("1.000")
        produto.preco_venda = Decimal("0")
        db.session.commit()
    sem_preco = _adicionar_codigo(client, venda_id, 1)
    assert "sem preço de venda válido" in sem_preco.text

    with app.app_context():
        produto = db.session.get(Produto, app.config["TEST_PRODUCT_ID"])
        produto.preco_venda = Decimal("10")
        db.session.commit()
    excesso = _adicionar_codigo(client, venda_id, 1, quantidade="2")
    assert "maior que o saldo disponível" in excesso.text
    with app.app_context():
        assert db.session.scalar(
            db.select(db.func.count(VendaItem.id)).where(VendaItem.venda_id == venda_id)
        ) == 0


def test_weighted_quantity_is_required_and_unit_product_rejects_fraction(app, client):
    with app.app_context():
        pesado_id = _novo_produto()
    _produto_pronto(app)
    login(client, "operador.carnes")
    venda_id = _venda_aberta(app)

    fracao_unidade = _adicionar_codigo(client, venda_id, 1, "001", "0,5")
    assert "somente quantidades inteiras" in fracao_unidade.text
    sem_peso = _adicionar_codigo(client, venda_id, 1, "PESO-001", "")
    assert "Informe uma quantidade positiva" in sem_peso.text
    com_peso = _adicionar_codigo(client, venda_id, 1, "PESO001", "1,250")
    assert "Produto adicionado" in com_peso.text
    with app.app_context():
        item = db.session.scalar(
            db.select(VendaItem).where(VendaItem.produto_id == pesado_id)
        )
        assert item.quantidade == Decimal("1.250")
        assert item.subtotal == Decimal("25.00")


def test_item_can_be_updated_and_removed_without_changing_stock(app, client):
    produto_id = _produto_pronto(app)
    login(client, "operador.carnes")
    venda_id = _venda_aberta(app)
    _adicionar_codigo(client, venda_id, 1)
    with app.app_context():
        item = db.session.scalar(
            db.select(VendaItem).where(VendaItem.venda_id == venda_id)
        )
        item_id = item.id

    atualizada = client.post(
        f"/empresa/1/caixa/{venda_id}/itens/{item_id}/atualizar",
        data={f"item-{item_id}-versao": "2", f"item-{item_id}-quantidade": "3"},
        follow_redirects=True,
    )
    assert "Quantidade atualizada" in atualizada.text
    with app.app_context():
        item = db.session.get(VendaItem, item_id)
        assert item.quantidade == Decimal("3.000")
        assert item.subtotal == Decimal("30.00")
        assert db.session.get(SaldoEstoque, produto_id).quantidade == Decimal("10.000")

    removida = client.post(
        f"/empresa/1/caixa/{venda_id}/itens/{item_id}/remover",
        data={f"remover-{item_id}-versao": "3"},
        follow_redirects=True,
    )
    assert "Item removido" in removida.text
    with app.app_context():
        venda = db.session.get(Venda, venda_id)
        assert venda.total == Decimal("0.00")
        assert venda.versao == 4
        assert db.session.get(VendaItem, item_id) is None
        assert db.session.get(SaldoEstoque, produto_id).quantidade == Decimal("10.000")


def test_invalid_payment_combinations_and_empty_sale_are_rejected(app):
    with app.app_context():
        usuario_id = _usuario_id("operador.carnes")
        venda = iniciar_venda(empresa_id=1, usuario_id=usuario_id)
        with pytest.raises(CaixaErro, match="Adicione ao menos um produto"):
            finalizar_venda(
                empresa_id=1,
                venda_id=venda.id,
                versao=1,
                pagamentos_digitados={"pix": ("10", None)},
                usuario_id=usuario_id,
            )
        db.session.rollback()

        with pytest.raises(CaixaErro, match="ao menos uma forma"):
            _preparar_pagamentos({}, Decimal("10.00"))
        with pytest.raises(CaixaErro, match="Informe também"):
            _preparar_pagamentos(
                {"dinheiro": (None, "10")}, Decimal("10.00")
            )
        with pytest.raises(CaixaErro, match="menor"):
            _preparar_pagamentos(
                {"dinheiro": ("10", "9")}, Decimal("10.00")
            )
        with pytest.raises(CaixaErro, match="somente em dinheiro"):
            _preparar_pagamentos({"pix": ("10", "11")}, Decimal("10.00"))


def test_mixed_payment_finalizes_once_and_generates_audited_nonfiscal_ticket(app, client):
    produto_id = _produto_pronto(app)
    login(client, "operador.carnes")
    venda_id = _venda_aberta(app)
    assert "Produto adicionado" in _adicionar_codigo(client, venda_id, 1).text

    resposta = _finalizar(
        client,
        venda_id,
        2,
        **{
            "finalizar-pagamento_dinheiro": "4,00",
            "finalizar-dinheiro_recebido": "5,00",
            "finalizar-pagamento_pix": "6,00",
        },
    )
    assert resposta.status_code == 302
    assert resposta.headers["Location"].endswith(f"/{venda_id}/ticket")
    ticket = client.get(resposta.headers["Location"])
    assert ticket.status_code == 200
    assert "DOCUMENTO NÃO FISCAL" in ticket.text
    assert "Este comprovante não possui valor fiscal" in ticket.text
    assert "Imprimir via do cliente" in ticket.text
    assert "Nova venda" in ticket.text
    assert ticket.headers["Cache-Control"].startswith("no-store")

    with app.app_context():
        venda = db.session.get(Venda, venda_id)
        assert venda.status == "finalizada"
        assert venda.total == Decimal("10.00")
        assert venda.troco == Decimal("1.00")
        assert db.session.get(SaldoEstoque, produto_id).quantidade == Decimal("9.000")
        pagamentos = db.session.scalars(
            db.select(VendaPagamento).where(VendaPagamento.venda_id == venda_id)
        ).all()
        assert {(p.metodo, p.valor) for p in pagamentos} == {
            ("dinheiro", Decimal("4.00")),
            ("pix", Decimal("6.00")),
        }
        movimentos = db.session.scalars(
            db.select(MovimentoEstoque).where(
                MovimentoEstoque.motivo.contains(venda.numero_formatado)
            )
        ).all()
        assert len(movimentos) == 1
        assert movimentos[0].variacao == Decimal("-1.000")
        assert db.session.scalar(
            db.select(Auditoria).where(Auditoria.acao == "venda_finalizada")
        ) is not None

        _, criada = finalizar_venda(
            empresa_id=1,
            venda_id=venda_id,
            versao=2,
            pagamentos_digitados={"pix": ("10", None)},
            usuario_id=_usuario_id("operador.carnes"),
        )
        assert criada is False
        assert db.session.get(SaldoEstoque, produto_id).quantidade == Decimal("9.000")
        assert db.session.scalar(
            db.select(db.func.count(MovimentoEstoque.id)).where(
                MovimentoEstoque.motivo.contains(venda.numero_formatado)
            )
        ) == 1


def test_payment_mismatch_and_stale_price_fail_without_partial_stock_change(app, client):
    produto_id = _produto_pronto(app)
    with app.app_context():
        segundo_id = _novo_produto(
            nome="Segundo Produto", codigo="SEG-002", pesado=False, preco="5"
        )
    login(client, "operador.carnes")
    venda_id = _venda_aberta(app)
    _adicionar_codigo(client, venda_id, 1, "001")
    _adicionar_codigo(client, venda_id, 2, "SEG-002")

    pagamento_errado = _finalizar(
        client,
        venda_id,
        3,
        **{"finalizar-pagamento_pix": "14,99"},
    )
    assert pagamento_errado.status_code == 302
    with app.app_context():
        assert db.session.get(SaldoEstoque, produto_id).quantidade == Decimal("10.000")
        assert db.session.get(SaldoEstoque, segundo_id).quantidade == Decimal("8.000")
        assert db.session.get(Venda, venda_id).status == "aberta"
        assert db.session.scalar(db.select(db.func.count(VendaPagamento.id))) == 0
        segundo = db.session.get(Produto, segundo_id)
        segundo.preco_venda = Decimal("6.00")
        db.session.commit()

    preco_alterado = _finalizar(
        client,
        venda_id,
        3,
        **{"finalizar-pagamento_pix": "15,00"},
    )
    assert preco_alterado.status_code == 302
    with app.app_context():
        assert db.session.get(SaldoEstoque, produto_id).quantidade == Decimal("10.000")
        assert db.session.get(SaldoEstoque, segundo_id).quantidade == Decimal("8.000")
        assert db.session.get(Venda, venda_id).status == "aberta"
        assert db.session.scalar(db.select(db.func.count(MovimentoEstoque.id))) == 0


@pytest.mark.parametrize(
    ("mudanca", "mensagem"),
    [
        ("codigo", "produto ou código indisponível"),
        ("arquivado", "produto arquivado"),
        ("inventario", "inventário pendente"),
        ("cadastro", "cadastro alterado"),
        ("saldo", "saldo insuficiente"),
    ],
)
def test_finalize_revalidates_every_mutable_product_condition(
    app, client, mudanca, mensagem
):
    produto_id = _produto_pronto(app)
    login(client, "operador.carnes")
    venda_id = _venda_aberta(app)
    _adicionar_codigo(client, venda_id, 1)
    with app.app_context():
        produto = db.session.get(Produto, produto_id)
        if mudanca == "codigo":
            codigo = db.session.scalar(
                db.select(CodigoProduto).where(CodigoProduto.produto_id == produto_id)
            )
            codigo.principal = False
            codigo.ativo = False
        elif mudanca == "arquivado":
            produto.ativo = False
        elif mudanca == "inventario":
            produto.saldo.aguardando_inventario = True
        elif mudanca == "cadastro":
            produto.unidade = "CX"
        elif mudanca == "saldo":
            produto.saldo.quantidade = Decimal("0")
        db.session.commit()

    resposta = _finalizar(
        client,
        venda_id,
        2,
        **{"finalizar-pagamento_pix": "10,00"},
    )
    assert resposta.status_code == 302
    painel = client.get(resposta.headers["Location"])
    assert mensagem in painel.text
    with app.app_context():
        assert db.session.get(Venda, venda_id).status == "aberta"
        assert db.session.scalar(db.select(db.func.count(MovimentoEstoque.id))) == 0
        assert db.session.scalar(db.select(db.func.count(VendaPagamento.id))) == 0


def test_tampered_total_is_blocked_before_payment_or_stock(app, client):
    produto_id = _produto_pronto(app)
    login(client, "operador.carnes")
    venda_id = _venda_aberta(app)
    _adicionar_codigo(client, venda_id, 1)
    with app.app_context():
        venda = db.session.get(Venda, venda_id)
        venda.total = Decimal("1.00")
        db.session.commit()
    resposta = _finalizar(
        client,
        venda_id,
        2,
        **{"finalizar-pagamento_pix": "10,00"},
    )
    assert resposta.status_code == 302
    with app.app_context():
        assert db.session.get(Venda, venda_id).status == "aberta"
        assert db.session.get(SaldoEstoque, produto_id).quantidade == Decimal("10.000")
        assert db.session.scalar(db.select(db.func.count(MovimentoEstoque.id))) == 0


def test_stale_screen_and_other_operator_sale_are_blocked(app):
    _produto_pronto(app)
    owner_client = app.test_client()
    operator_client = app.test_client()
    login(owner_client)
    login(operator_client, "operador.carnes")
    venda_id = _venda_aberta(app, "dono")

    assert operator_client.get(f"/empresa/1/caixa/{venda_id}").status_code == 404
    assert (
        operator_client.post(
            f"/empresa/1/caixa/{venda_id}/cancelar",
            data={"cancelar-versao": "1", "cancelar-motivo": "Sem autorização"},
        ).status_code
        == 403
    )
    primeira = _adicionar_codigo(owner_client, venda_id, 1)
    assert "Produto adicionado" in primeira.text
    repetida = _adicionar_codigo(owner_client, venda_id, 1)
    assert "alterado em outra sessão" in repetida.text
    with app.app_context():
        item = db.session.scalar(
            db.select(VendaItem).where(VendaItem.venda_id == venda_id)
        )
        assert item.quantidade == Decimal("1.000")


def test_admin_cancellation_restores_stock_exactly_once_and_keeps_reason(app):
    produto_id = _produto_pronto(app)
    operator_client = app.test_client()
    admin_client = app.test_client()
    login(operator_client, "operador.carnes")
    login(admin_client, "admin.carnes")
    venda_id = _venda_aberta(app)
    _adicionar_codigo(operator_client, venda_id, 1, quantidade="2")
    _finalizar(
        operator_client,
        venda_id,
        2,
        **{"finalizar-pagamento_debito": "20,00"},
    )

    with app.app_context():
        versao = db.session.get(Venda, venda_id).versao
        assert db.session.get(SaldoEstoque, produto_id).quantidade == Decimal("8.000")
    cancelada = admin_client.post(
        f"/empresa/1/caixa/{venda_id}/cancelar",
        data={
            "cancelar-versao": str(versao),
            "cancelar-motivo": "Cliente solicitou cancelamento",
        },
        follow_redirects=True,
    )
    assert "Venda cancelada e estoque devolvido" in cancelada.text
    with app.app_context():
        venda = db.session.get(Venda, venda_id)
        assert venda.status == "cancelada"
        assert venda.motivo_cancelamento == "Cliente solicitou cancelamento"
        assert db.session.get(SaldoEstoque, produto_id).quantidade == Decimal("10.000")
        _, alterada = cancelar_venda(
            empresa_id=1,
            venda_id=venda_id,
            versao=versao,
            motivo="Repetição segura do cancelamento",
            usuario_id=_usuario_id("admin.carnes"),
        )
        assert alterada is False
        assert db.session.get(SaldoEstoque, produto_id).quantidade == Decimal("10.000")
        assert db.session.scalar(
            db.select(db.func.count(MovimentoEstoque.id)).where(
                MovimentoEstoque.motivo.contains("Cancelamento da venda")
            )
        ) == 1
        assert db.session.scalar(
            db.select(Auditoria).where(Auditoria.acao == "venda_cancelada")
        ) is not None


def test_cancellation_requires_finalized_sale_current_version_and_valid_reason(app):
    with app.app_context():
        usuario_id = _usuario_id("admin.carnes")
        venda = iniciar_venda(empresa_id=1, usuario_id=usuario_id)
        with pytest.raises(CaixaErro, match="entre 5 e 500"):
            cancelar_venda(
                empresa_id=1,
                venda_id=venda.id,
                versao=1,
                motivo="não",
                usuario_id=usuario_id,
            )
        with pytest.raises(CaixaErro, match="Somente uma venda finalizada"):
            cancelar_venda(
                empresa_id=1,
                venda_id=venda.id,
                versao=1,
                motivo="Atendimento ainda aberto",
                usuario_id=usuario_id,
            )
        db.session.rollback()


def test_discard_does_not_move_stock_and_html_is_escaped(app, client):
    produto_id = _produto_pronto(app)
    with app.app_context():
        produto = db.session.get(Produto, produto_id)
        produto.nome = '<script>alert("x")</script>'
        db.session.commit()
    login(client, "operador.carnes")
    venda_id = _venda_aberta(app)
    _adicionar_codigo(client, venda_id, 1)
    painel = client.get("/empresa/1/operacao")
    assert "&lt;script&gt;" in painel.text
    assert '<script>alert("x")</script>' not in painel.text
    descartada = client.post(
        f"/empresa/1/caixa/{venda_id}/descartar",
        data={"descartar-versao": "2"},
        follow_redirects=True,
    )
    assert "sem movimentar estoque" in descartada.text
    with app.app_context():
        assert db.session.get(Venda, venda_id).status == "descartada"
        assert db.session.get(SaldoEstoque, produto_id).quantidade == Decimal("10.000")
        assert db.session.scalar(db.select(db.func.count(MovimentoEstoque.id))) == 0


def test_one_open_sale_per_operator_is_idempotent(app):
    with app.app_context():
        usuario_id = _usuario_id("operador.carnes")
        primeira = iniciar_venda(empresa_id=1, usuario_id=usuario_id)
        segunda = iniciar_venda(empresa_id=1, usuario_id=usuario_id)
        assert segunda.id == primeira.id
        assert db.session.scalar(
            db.select(db.func.count(Venda.id)).where(Venda.status == "aberta")
        ) == 1


def test_quantity_rules_cover_kg_and_discrete_units_explicitly():
    assert normalizar_quantidade("1,250", pesado=True) == Decimal("1.250")
    assert normalizar_quantidade("2.000", pesado=False) == Decimal("2.000")
    with pytest.raises(CaixaErro, match="somente quantidades inteiras"):
        normalizar_quantidade("0,500", pesado=False)
