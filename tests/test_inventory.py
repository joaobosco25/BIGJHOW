from __future__ import annotations

from decimal import Decimal

from bigjhow.extensions import db
from bigjhow.models import Auditoria, MovimentoEstoque, Produto

from .conftest import login


def test_company_scope_blocks_unlinked_operator(client):
    login(client, "operador.carnes")
    assert client.get("/empresa/1/estoque").status_code == 200
    assert client.get("/empresa/2/estoque").status_code == 403


def test_new_product_starts_at_zero_waiting_inventory(app, client):
    login(client)
    response = client.post(
        "/empresa/1/estoque/novo",
        data={
            "versao": "1",
            "codigo_externo": "NOVO-01",
            "nome": "Novo Produto",
            "categoria": "Nova Categoria",
            "unidade": "UN",
            "preco_venda": "25.50",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        product = db.session.scalar(db.select(Produto).where(Produto.nome == "Novo Produto"))
        assert product is not None
        assert product.saldo.quantidade == Decimal("0.000")
        assert product.saldo.aguardando_inventario is True
        assert db.session.scalar(
            db.select(Auditoria).where(Auditoria.acao == "produto_criado")
        ) is not None


def test_inventory_movement_is_audited(app, client):
    login(client)
    product_id = app.config["TEST_PRODUCT_ID"]
    response = client.post(
        f"/empresa/1/estoque/{product_id}/movimentar",
        data={"operacao": "inventario", "quantidade": "12.750", "motivo": "Contagem inicial completa"},
    )
    assert response.status_code == 302
    with app.app_context():
        product = db.session.get(Produto, product_id)
        assert product.saldo.quantidade == Decimal("12.750")
        assert product.saldo.aguardando_inventario is False
        movement = db.session.scalar(db.select(MovimentoEstoque))
        assert movement.quantidade_final == Decimal("12.750")
        assert movement.usuario_id is not None


def test_inventory_movement_accepts_brazilian_decimal_without_scaling(app, client):
    login(client)
    product_id = app.config["TEST_PRODUCT_ID"]

    page = client.get(f"/empresa/1/estoque/{product_id}/movimentar")
    assert page.status_code == 200
    assert 'name="quantidade"' in page.text
    assert 'type="text"' in page.text
    assert "2,500 significa dois quilos e meio" in page.text

    response = client.post(
        f"/empresa/1/estoque/{product_id}/movimentar",
        data={
            "operacao": "inventario",
            "quantidade": "2,500",
            "motivo": "Contagem brasileira controlada",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        product = db.session.get(Produto, product_id)
        assert product.saldo.quantidade == Decimal("2.500")
        movement = db.session.scalar(db.select(MovimentoEstoque))
        assert movement.quantidade_final == Decimal("2.500")


def test_inventory_movement_rejects_thousand_separator_and_ambiguous_value(app, client):
    login(client)
    product_id = app.config["TEST_PRODUCT_ID"]
    response = client.post(
        f"/empresa/1/estoque/{product_id}/movimentar",
        data={
            "operacao": "inventario",
            "quantidade": "2.500,000",
            "motivo": "Entrada ambígua bloqueada",
        },
    )
    assert response.status_code == 200
    assert "sem separador de milhar" in response.text
    with app.app_context():
        assert db.session.get(Produto, product_id).saldo.quantidade == Decimal("0.000")
        assert db.session.scalar(db.select(MovimentoEstoque)) is None


def test_negative_stock_is_blocked(app, client):
    login(client)
    product_id = app.config["TEST_PRODUCT_ID"]
    response = client.post(
        f"/empresa/1/estoque/{product_id}/movimentar",
        data={"operacao": "saida", "quantidade": "1", "motivo": "Teste de saída inválida"},
    )
    assert response.status_code == 200
    assert "saldo negativo" in response.text
    with app.app_context():
        assert db.session.get(Produto, product_id).saldo.quantidade == Decimal("0.000")
        assert db.session.scalar(db.select(MovimentoEstoque)) is None


def test_archive_is_soft_delete_and_requires_zero_stock(app, client):
    login(client)
    product_id = app.config["TEST_PRODUCT_ID"]
    response = client.post(f"/empresa/1/estoque/{product_id}/arquivar")
    assert response.status_code == 302
    with app.app_context():
        product = db.session.get(Produto, product_id)
        assert product.ativo is False
        assert product.arquivado_em is not None
        assert db.session.scalar(
            db.select(Auditoria).where(Auditoria.acao == "produto_arquivado")
        ) is not None


def test_archive_with_stock_is_blocked(app, client):
    login(client)
    product_id = app.config["TEST_PRODUCT_ID"]
    client.post(
        f"/empresa/1/estoque/{product_id}/movimentar",
        data={"operacao": "inventario", "quantidade": "2", "motivo": "Inventário para teste"},
    )
    response = client.post(
        f"/empresa/1/estoque/{product_id}/arquivar", follow_redirects=True
    )
    assert response.status_code == 200
    assert "só pode ser arquivado quando o saldo for zero" in response.text
    with app.app_context():
        assert db.session.get(Produto, product_id).ativo is True


def test_stale_product_edit_is_rejected(app, client):
    login(client)
    product_id = app.config["TEST_PRODUCT_ID"]
    first = client.post(
        f"/empresa/1/estoque/{product_id}/editar",
        data={
            "versao": "1",
            "codigo_externo": "001",
            "nome": "Produto Atualizado",
            "categoria": "Testes",
            "unidade": "UN",
            "preco_venda": "11.00",
        },
    )
    assert first.status_code == 302
    stale = client.post(
        f"/empresa/1/estoque/{product_id}/editar",
        data={
            "versao": "1",
            "codigo_externo": "001",
            "nome": "Sobrescrita Indevida",
            "categoria": "Testes",
            "unidade": "UN",
            "preco_venda": "12.00",
        },
        follow_redirects=True,
    )
    assert stale.status_code == 200
    assert "alterado em outra sessão" in stale.text
    with app.app_context():
        assert db.session.get(Produto, product_id).nome == "Produto Atualizado"


def test_search_input_is_treated_as_data(client):
    login(client)
    response = client.get("/empresa/1/estoque?q=%27%20OR%201%3D1--")
    assert response.status_code == 200
    assert "Nenhum produto encontrado" in response.text
