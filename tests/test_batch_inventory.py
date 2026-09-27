from __future__ import annotations

from decimal import Decimal
from io import BytesIO
import uuid

from openpyxl import load_workbook

from bigjhow.extensions import db
from bigjhow.models import (
    Auditoria,
    InventarioLote,
    InventarioLoteItem,
    MovimentoEstoque,
    Produto,
    SaldoEstoque,
)

from .conftest import login


def _adicionar_produto(nome="Segundo Produto", empresa_id=1):
    produto = Produto(
        empresa_id=empresa_id,
        sku_interno=f"TEST-{nome[:4].upper()}-{empresa_id}",
        nome=nome,
        categoria="Testes",
        unidade="KG",
        pesado=True,
        preco_venda=Decimal("20.00"),
    )
    produto.saldo = SaldoEstoque(
        quantidade=Decimal("0"), aguardando_inventario=True
    )
    db.session.add(produto)
    db.session.commit()
    return produto.id


def _criar_lote(client, app, descricao="Inventário inicial de teste"):
    resposta = client.post(
        "/empresa/1/inventarios", data={"descricao": descricao}
    )
    assert resposta.status_code == 302
    with app.app_context():
        lote = db.session.scalar(
            db.select(InventarioLote).where(InventarioLote.empresa_id == 1)
        )
        assert lote is not None
        return lote.id


def _itens(app, lote_id):
    with app.app_context():
        return [
            (item.id, item.produto_id)
            for item in db.session.scalars(
                db.select(InventarioLoteItem)
                .where(InventarioLoteItem.inventario_id == lote_id)
                .order_by(InventarioLoteItem.id)
            )
        ]


def test_operator_and_out_of_scope_administrator_cannot_access_batch_inventory(client):
    login(client, "operador.carnes")
    assert client.get("/empresa/1/inventarios").status_code == 403
    client.post("/auth/logout")
    login(client, "admin.carnes")
    assert client.get("/empresa/1/inventarios").status_code == 200
    assert client.get("/empresa/2/inventarios").status_code == 403


def test_create_batch_snapshots_pending_items_and_blocks_second_draft(app, client):
    with app.app_context():
        _adicionar_produto()
    login(client)
    lote_id = _criar_lote(client, app)
    with app.app_context():
        lote = db.session.get(InventarioLote, lote_id)
        assert lote.status == "rascunho"
        assert len(lote.itens) == 2
        assert all(item.quantidade_contada is None for item in lote.itens)
        assert all(item.quantidade_referencia == Decimal("0.000") for item in lote.itens)
        assert db.session.scalar(
            db.select(Auditoria).where(Auditoria.acao == "inventario_lote_criado")
        ) is not None

    segunda = client.post(
        "/empresa/1/inventarios",
        data={"descricao": "Outro inventário inicial"},
    )
    assert segunda.status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(InventarioLote.id))) == 1


def test_blank_stays_pending_and_zero_is_a_counted_quantity(app, client):
    with app.app_context():
        _adicionar_produto()
    login(client)
    lote_id = _criar_lote(client, app)
    itens = _itens(app, lote_id)
    resposta = client.post(
        f"/empresa/1/inventarios/{lote_id}/salvar",
        data={
            "salvar-versao": "1",
            f"quantidade_{itens[0][0]}": "0",
            f"quantidade_{itens[1][0]}": "",
            "page": "1",
            "situacao": "todos",
        },
    )
    assert resposta.status_code == 302
    with app.app_context():
        primeiro = db.session.get(InventarioLoteItem, itens[0][0])
        segundo = db.session.get(InventarioLoteItem, itens[1][0])
        assert primeiro.quantidade_contada == Decimal("0.000")
        assert primeiro.contado_por_id is not None
        assert segundo.quantidade_contada is None
        assert segundo.contado_por_id is None
        assert db.session.get(InventarioLote, lote_id).versao == 2


def test_partial_finalization_is_atomic_and_preserves_blank_as_pending(app, client):
    with app.app_context():
        _adicionar_produto()
    login(client)
    lote_id = _criar_lote(client, app)
    itens = _itens(app, lote_id)
    client.post(
        f"/empresa/1/inventarios/{lote_id}/salvar",
        data={
            "salvar-versao": "1",
            f"quantidade_{itens[0][0]}": "0",
            f"quantidade_{itens[1][0]}": "",
        },
    )
    resposta = client.post(
        f"/empresa/1/inventarios/{lote_id}/finalizar",
        data={"finalizar-versao": "2", "finalizar-confirmacao": "y"},
    )
    assert resposta.status_code == 302
    with app.app_context():
        lote = db.session.get(InventarioLote, lote_id)
        saldo_contado = db.session.get(SaldoEstoque, itens[0][1])
        saldo_pendente = db.session.get(SaldoEstoque, itens[1][1])
        assert lote.status == "finalizado"
        assert lote.finalizado_por_id is not None
        assert saldo_contado.quantidade == Decimal("0.000")
        assert saldo_contado.aguardando_inventario is False
        assert saldo_pendente.aguardando_inventario is True
        movimentos = db.session.scalars(
            db.select(MovimentoEstoque).where(
                MovimentoEstoque.motivo.contains("inventário em lote")
            )
        ).all()
        assert len(movimentos) == 1
        assert movimentos[0].quantidade_final == Decimal("0.000")
        assert db.session.scalar(
            db.select(Auditoria).where(Auditoria.acao == "inventario_lote_finalizado")
        ) is not None


def test_finalization_blocks_if_stock_changed_after_snapshot(app, client):
    login(client)
    lote_id = _criar_lote(client, app)
    item_id, produto_id = _itens(app, lote_id)[0]
    client.post(
        f"/empresa/1/inventarios/{lote_id}/salvar",
        data={"salvar-versao": "1", f"quantidade_{item_id}": "9.500"},
    )
    with app.app_context():
        saldo = db.session.get(SaldoEstoque, produto_id)
        saldo.quantidade = Decimal("1.000")
        saldo.aguardando_inventario = False
        db.session.commit()

    resposta = client.post(
        f"/empresa/1/inventarios/{lote_id}/finalizar",
        data={"finalizar-versao": "2", "finalizar-confirmacao": "y"},
        follow_redirects=True,
    )
    assert resposta.status_code == 200
    assert "finalização foi bloqueada" in resposta.text
    assert "Nenhum saldo foi alterado" in resposta.text
    with app.app_context():
        assert db.session.get(InventarioLote, lote_id).status == "rascunho"
        assert db.session.get(SaldoEstoque, produto_id).quantidade == Decimal("1.000")


def test_spreadsheet_roundtrip_imports_zero_and_decimal(app, client):
    with app.app_context():
        _adicionar_produto()
    login(client)
    lote_id = _criar_lote(client, app)
    baixada = client.get(f"/empresa/1/inventarios/{lote_id}/planilha")
    assert baixada.status_code == 200
    assert baixada.headers["Cache-Control"].startswith("no-store")

    workbook = load_workbook(BytesIO(baixada.data))
    planilha = workbook["Inventario"]
    planilha["G2"] = 0
    planilha["G3"] = "3,125"
    conteudo = BytesIO()
    workbook.save(conteudo)
    workbook.close()
    conteudo.seek(0)

    resposta = client.post(
        f"/empresa/1/inventarios/{lote_id}/planilha",
        data={
            "importar-versao": "1",
            "importar-arquivo": (conteudo, "inventario.xlsx"),
        },
        content_type="multipart/form-data",
    )
    assert resposta.status_code == 302
    with app.app_context():
        quantidades = set(
            db.session.scalars(
                db.select(InventarioLoteItem.quantidade_contada).where(
                    InventarioLoteItem.inventario_id == lote_id
                )
            )
        )
        assert quantidades == {Decimal("0.000"), Decimal("3.125")}
        assert db.session.get(InventarioLote, lote_id).versao == 2
        assert db.session.scalar(
            db.select(Auditoria).where(
                Auditoria.acao == "inventario_lote_planilha_importada"
            )
        ) is not None


def test_stale_or_formula_spreadsheet_is_rejected_without_overwrite(app, client):
    login(client)
    lote_id = _criar_lote(client, app)
    item_id, _ = _itens(app, lote_id)[0]
    original = client.get(f"/empresa/1/inventarios/{lote_id}/planilha").data
    client.post(
        f"/empresa/1/inventarios/{lote_id}/salvar",
        data={"salvar-versao": "1", f"quantidade_{item_id}": "4"},
    )
    antiga = client.post(
        f"/empresa/1/inventarios/{lote_id}/planilha",
        data={
            "importar-versao": "2",
            "importar-arquivo": (BytesIO(original), "antiga.xlsx"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "planilha está desatualizada" in antiga.text

    atual = client.get(f"/empresa/1/inventarios/{lote_id}/planilha").data
    workbook = load_workbook(BytesIO(atual))
    workbook["Inventario"]["G2"] = "=1+1"
    formula = BytesIO()
    workbook.save(formula)
    workbook.close()
    formula.seek(0)
    recusada = client.post(
        f"/empresa/1/inventarios/{lote_id}/planilha",
        data={
            "importar-versao": "2",
            "importar-arquivo": (formula, "formula.xlsx"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "contém fórmula" in recusada.text
    with app.app_context():
        item = db.session.get(InventarioLoteItem, item_id)
        assert item.quantidade_contada == Decimal("4.000")
        assert db.session.get(InventarioLote, lote_id).versao == 2


def test_invalid_screen_quantity_and_stale_version_do_not_overwrite(app, client):
    login(client)
    lote_id = _criar_lote(client, app)
    item_id, _ = _itens(app, lote_id)[0]
    invalida = client.post(
        f"/empresa/1/inventarios/{lote_id}/salvar",
        data={"salvar-versao": "1", f"quantidade_{item_id}": "-2"},
        follow_redirects=True,
    )
    assert "quantidade inválida" in invalida.text
    client.post(
        f"/empresa/1/inventarios/{lote_id}/salvar",
        data={"salvar-versao": "1", f"quantidade_{item_id}": "2"},
    )
    obsoleta = client.post(
        f"/empresa/1/inventarios/{lote_id}/salvar",
        data={"salvar-versao": "1", f"quantidade_{item_id}": "8"},
        follow_redirects=True,
    )
    assert "alterado em outra sessão" in obsoleta.text
    with app.app_context():
        assert db.session.get(InventarioLoteItem, item_id).quantidade_contada == Decimal(
            "2.000"
        )


def test_cancel_preserves_history_and_changes_no_stock(app, client):
    login(client)
    lote_id = _criar_lote(client, app)
    resposta = client.post(
        f"/empresa/1/inventarios/{lote_id}/cancelar",
        data={"cancelar-versao": "1"},
    )
    assert resposta.status_code == 302
    with app.app_context():
        lote = db.session.get(InventarioLote, lote_id)
        produto = db.session.get(Produto, app.config["TEST_PRODUCT_ID"])
        assert lote.status == "cancelado"
        assert lote.cancelado_em is not None
        assert produto.saldo.quantidade == Decimal("0.000")
        assert produto.saldo.aguardando_inventario is True
        assert db.session.scalar(
            db.select(Auditoria).where(Auditoria.acao == "inventario_lote_cancelado")
        ) is not None


def test_finalization_requires_explicit_confirmation_and_a_count(app, client):
    login(client)
    lote_id = _criar_lote(client, app)
    sem_confirmar = client.post(
        f"/empresa/1/inventarios/{lote_id}/finalizar",
        data={"finalizar-versao": "1"},
        follow_redirects=True,
    )
    assert "Marque a confirmação" in sem_confirmar.text
    vazio = client.post(
        f"/empresa/1/inventarios/{lote_id}/finalizar",
        data={"finalizar-versao": "1", "finalizar-confirmacao": "y"},
        follow_redirects=True,
    )
    assert "ao menos uma quantidade" in vazio.text
    with app.app_context():
        assert db.session.get(InventarioLote, lote_id).status == "rascunho"
        assert db.session.scalar(db.select(MovimentoEstoque)) is None


def test_invalid_incomplete_or_foreign_spreadsheet_is_rejected(app, client):
    with app.app_context():
        _adicionar_produto()
    login(client)
    lote_id = _criar_lote(client, app)
    invalida = client.post(
        f"/empresa/1/inventarios/{lote_id}/planilha",
        data={
            "importar-versao": "1",
            "importar-arquivo": (BytesIO(b"not-an-xlsx"), "invalida.xlsx"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "não é uma planilha XLSX válida" in invalida.text

    original = client.get(f"/empresa/1/inventarios/{lote_id}/planilha").data
    workbook = load_workbook(BytesIO(original))
    workbook["Inventario"].delete_rows(3)
    incompleta = BytesIO()
    workbook.save(incompleta)
    workbook.close()
    incompleta.seek(0)
    resposta = client.post(
        f"/empresa/1/inventarios/{lote_id}/planilha",
        data={
            "importar-versao": "1",
            "importar-arquivo": (incompleta, "incompleta.xlsx"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "planilha está incompleta" in resposta.text

    workbook = load_workbook(BytesIO(original))
    workbook["Inventario"]["A2"] = str(uuid.uuid4())
    adulterada = BytesIO()
    workbook.save(adulterada)
    workbook.close()
    adulterada.seek(0)
    resposta = client.post(
        f"/empresa/1/inventarios/{lote_id}/planilha",
        data={
            "importar-versao": "1",
            "importar-arquivo": (adulterada, "adulterada.xlsx"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "não pertence a este inventário" in resposta.text
    with app.app_context():
        assert all(
            item.quantidade_contada is None
            for item in db.session.scalars(db.select(InventarioLoteItem))
        )


def test_export_neutralizes_formula_like_product_text(app, client):
    with app.app_context():
        _adicionar_produto(nome="=1+1")
    login(client)
    lote_id = _criar_lote(client, app)
    resposta = client.get(f"/empresa/1/inventarios/{lote_id}/planilha")
    workbook = load_workbook(BytesIO(resposta.data), data_only=False)
    sheet = workbook["Inventario"]
    cells = [sheet.cell(row, 4) for row in range(2, sheet.max_row + 1)]
    formula_like = next(cell for cell in cells if "1+1" in str(cell.value))
    assert formula_like.data_type == "s"
    assert str(formula_like.value).startswith("'")
    workbook.close()


def test_no_pending_product_creates_no_batch_and_company_path_is_bound(app, client):
    login(client)
    product_id = app.config["TEST_PRODUCT_ID"]
    client.post(
        f"/empresa/1/estoque/{product_id}/movimentar",
        data={
            "operacao": "inventario",
            "quantidade": "0",
            "motivo": "Contagem individual concluída",
        },
    )
    sem_pendentes = client.post(
        "/empresa/1/inventarios",
        data={"descricao": "Inventário sem produtos"},
        follow_redirects=True,
    )
    assert "Não há produtos aguardando inventário" in sem_pendentes.text
    with app.app_context():
        assert db.session.scalar(db.select(InventarioLote)) is None
        _adicionar_produto(nome="Produto Empresa Dois", empresa_id=2)
    criado = client.post(
        "/empresa/2/inventarios", data={"descricao": "Inventário da empresa dois"}
    )
    assert criado.status_code == 302
    with app.app_context():
        lote_id = db.session.scalar(
            db.select(InventarioLote.id).where(InventarioLote.empresa_id == 2)
        )
    assert client.get(f"/empresa/1/inventarios/{lote_id}").status_code == 404
