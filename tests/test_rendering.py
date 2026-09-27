from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from decimal import Decimal

from bigjhow.extensions import db
from bigjhow.models import Produto, Venda

from .conftest import login


class PageInspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: list[str] = []
        self.label_targets: list[str] = []
        self.inline_scripts = 0
        self.inline_styles = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.append(attributes["id"])
        if tag == "label" and "for" in attributes:
            self.label_targets.append(attributes["for"])
        if tag == "script" and "src" not in attributes:
            self.inline_scripts += 1
        if "style" in attributes:
            self.inline_styles += 1


def test_all_operational_pages_render_with_valid_local_structure(app, client):
    login(client)
    product_id = app.config["TEST_PRODUCT_ID"]
    client.post("/empresa/1/inventarios", data={"descricao": "Inventário visual"})
    with app.app_context():
        from bigjhow.extensions import db
        from bigjhow.models import InventarioLote, TurnoCaixa

        inventory_id = db.session.scalar(db.select(InventarioLote.id))
        turno_id = db.session.scalar(db.select(TurnoCaixa.id))
    urls = [
        "/grupo/estoque",
        "/empresa/1/estoque",
        "/empresa/1/estoque/novo",
        f"/empresa/1/estoque/{product_id}/editar",
        f"/empresa/1/estoque/{product_id}/movimentar",
        f"/empresa/1/estoque/{product_id}/codigos",
        "/empresa/1/operacao",
        "/empresa/1/operacao/historico",
        "/empresa/1/operacao/autorizacoes",
        "/empresa/1/operacao/impressoes",
        "/empresa/1/operacao/configuracao",
        f"/empresa/1/operacao/turnos/{turno_id}",
        "/empresa/1/inventarios",
        f"/empresa/1/inventarios/{inventory_id}",
        "/usuarios",
        "/usuarios/novo",
        "/auditoria",
        "/auth/alterar-senha",
    ]
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, url
        inspector = PageInspector()
        inspector.feed(response.text)
        assert len(inspector.ids) == len(set(inspector.ids)), url
        assert set(inspector.label_targets).issubset(set(inspector.ids)), url
        assert inspector.inline_scripts == 0, url
        assert inspector.inline_styles == 0, url
        assert "BIG JHOW" in response.text


def test_responsive_styles_include_sidebar_and_small_screen_rules():
    css = (
        Path(__file__).resolve().parents[1] / "bigjhow" / "static" / "css" / "app.css"
    ).read_text(encoding="utf-8")
    assert css.count("{") == css.count("}")
    assert "@media (max-width: 820px)" in css
    assert "@media (max-width: 560px)" in css
    assert "body.sidebar-open .sidebar" in css


def test_open_cashier_and_ticket_render_without_duplicate_ids_or_inline_code(app, client):
    login(client)
    with app.app_context():
        produto = db.session.get(Produto, app.config["TEST_PRODUCT_ID"])
        produto.saldo.aguardando_inventario = False
        produto.saldo.quantidade = Decimal("2.000")
        db.session.commit()
    assert client.post("/empresa/1/caixa/iniciar").status_code == 302
    with app.app_context():
        venda = db.session.scalar(db.select(Venda).where(Venda.status == "aberta"))
        venda_id = venda.id
    client.post(
        f"/empresa/1/caixa/{venda_id}/ler",
        data={
            "leitura-versao": "1",
            "leitura-codigo": "001",
            "leitura-quantidade": "",
        },
    )
    caixa = client.get("/empresa/1/operacao")
    assert caixa.status_code == 200
    inspector = PageInspector()
    inspector.feed(caixa.text)
    assert len(inspector.ids) == len(set(inspector.ids))
    assert set(inspector.label_targets).issubset(set(inspector.ids))
    assert inspector.inline_scripts == 0
    assert inspector.inline_styles == 0

    finalizada = client.post(
        f"/empresa/1/caixa/{venda_id}/finalizar",
        data={
            "finalizar-versao": "2",
            "finalizar-pagamento_dinheiro": "10,00",
            "finalizar-dinheiro_recebido": "10,00",
        },
    )
    assert finalizada.status_code == 302
    ticket = client.get(finalizada.headers["Location"])
    assert ticket.status_code == 200
    inspector = PageInspector()
    inspector.feed(ticket.text)
    assert len(inspector.ids) == len(set(inspector.ids))
    assert set(inspector.label_targets).issubset(set(inspector.ids))
    assert inspector.inline_scripts == 0
    assert inspector.inline_styles == 0
