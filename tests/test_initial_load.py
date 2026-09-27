from __future__ import annotations

from decimal import Decimal

from bigjhow.config import TestConfig
from bigjhow.extensions import db
from bigjhow.models import Produto
from bigjhow.services.carga_inicial import importar, verificar_integridade


def test_initial_load_is_integral_and_idempotent():
    from bigjhow import create_app

    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        manifest = verificar_integridade()
        assert manifest["arquivos_carga"]["produtos_big_jhow_carnes.csv"]["registros"] == 2682
        assert manifest["arquivos_carga"]["produtos_big_jhow_steak_house.csv"]["registros"] == 221
        first = importar()
        second = importar()
        assert first == {"importados": 2903, "ignorados": 0}
        assert second == {"importados": 0, "ignorados": 2903}
        assert db.session.scalar(db.select(db.func.count(Produto.id))) == 2903
        missing_prices = db.session.scalar(
            db.select(db.func.count(Produto.id)).where(
                Produto.empresa_id == 1,
                Produto.preco_venda == Decimal("0.00"),
            )
        )
        assert missing_prices == 1224
        waiting = db.session.scalars(db.select(Produto)).all()
        assert all(product.saldo.aguardando_inventario for product in waiting)
        assert all(product.saldo.quantidade == Decimal("0.000") for product in waiting)
