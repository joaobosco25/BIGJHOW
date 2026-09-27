"""Balança do açougue: leitura da etiqueta pesada e baixa de estoque.

O caso central é o do balcão: pesa a carne, imprime a etiqueta, o caixa bipa,
e o peso sai do estoque na conclusão da venda.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from bigjhow.extensions import db
from bigjhow.models import (
    CodigoProduto,
    ConfiguracaoBalanca,
    IntegracaoMGV6Produto,
    MovimentoEstoque,
    Produto,
    SaldoEstoque,
    Usuario,
)
from bigjhow.services.balanca import (
    CAMPOS_CARGA_PADRAO,
    EtiquetaInvalida,
    LayoutEtiqueta,
    ean13_valido,
    gerar_carga,
    identificar_etiqueta,
    ler_etiqueta,
    montar_etiqueta,
    parece_etiqueta,
    peso_a_partir_do_valor,
    variantes_de_plu,
)
from bigjhow.services.caixa import adicionar_item, finalizar_venda, iniciar_venda


# Etiqueta real impressa na balança da loja:
#   PRIME STEAK · PESO 1,258 kg · R$/kg 59,99 · TOTAL R$ 75,47
ETIQUETA_REAL = "2002700075478"
PLU_REAL = "002700"
PRECO_REAL = Decimal("59.99")
PESO_REAL = Decimal("1.258")
TOTAL_REAL = Decimal("75.47")

LAYOUT_LOJA = LayoutEtiqueta(
    prefixo="2", tamanho_plu=6, conteudo="preco", casas_decimais=2, ativo=True
)


# --------------------------------------------------------------------------
# Decodificação pura
# --------------------------------------------------------------------------


def test_etiqueta_real_da_loja_e_decodificada_corretamente():
    assert ean13_valido(ETIQUETA_REAL)
    lida = ler_etiqueta(ETIQUETA_REAL, LAYOUT_LOJA)
    assert lida.plu == PLU_REAL
    assert lida.valor_total == TOTAL_REAL


def test_peso_derivado_bate_com_o_peso_impresso_na_etiqueta():
    """A etiqueta traz o valor; o peso sai da divisão pelo preço do sistema.
    Com o preço certo, o resultado é o mesmo peso que a balança imprimiu."""
    lida = ler_etiqueta(ETIQUETA_REAL, LAYOUT_LOJA)
    peso, recalculado = peso_a_partir_do_valor(lida.valor_total, PRECO_REAL)
    assert peso == PESO_REAL
    assert recalculado == TOTAL_REAL


@pytest.mark.parametrize("codigo", ["7891000100103", "7894900011517", "4006381333931"])
def test_ean13_de_produtos_reais_e_aceito(codigo):
    assert ean13_valido(codigo)


def test_ean13_com_digito_trocado_e_recusado():
    assert not ean13_valido("7891000100104")


def test_etiqueta_com_peso_embutido():
    layout = LayoutEtiqueta(
        prefixo="2", tamanho_plu=6, conteudo="peso", casas_decimais=3, ativo=True
    )
    codigo = montar_etiqueta("001234", Decimal("1.250"), layout)
    lida = ler_etiqueta(codigo, layout)
    assert lida.plu == "001234"
    assert lida.peso == Decimal("1.250")
    assert lida.traz_peso


def test_etiqueta_borrada_e_recusada_em_vez_de_cobrar_outro_produto():
    with pytest.raises(EtiquetaInvalida) as erro:
        ler_etiqueta("2002700075470", LAYOUT_LOJA)  # último dígito trocado
    assert "verificador" in str(erro.value)


def test_codigo_comum_nao_e_tratado_como_etiqueta():
    assert not parece_etiqueta("7891000100103", LAYOUT_LOJA)


def test_layout_desligado_nao_decodifica():
    desligado = LayoutEtiqueta(ativo=False)
    assert not parece_etiqueta(ETIQUETA_REAL, desligado)


def test_variantes_de_plu_cobrem_zeros_a_esquerda():
    variantes = variantes_de_plu("002700")
    assert "002700" in variantes and "2700" in variantes


# --------------------------------------------------------------------------
# Integração com o caixa
# --------------------------------------------------------------------------


@pytest.fixture()
def app_balanca(app):
    """Empresa 1 com a balança configurada como a da loja e um corte pesável."""
    with app.app_context():
        db.session.add(
            ConfiguracaoBalanca(
                empresa_id=1,
                ativo=True,
                modelo="Prix 5 Plus",
                prefixo="2",
                tamanho_plu=6,
                conteudo="preco",
                casas_decimais=2,
                tolerancia=Decimal("0.05"),
                bloquear_divergencia=True,
                campos_carga=list(CAMPOS_CARGA_PADRAO),
            )
        )
        carne = Produto(
            empresa_id=1,
            sku_interno="CARNE-001",
            nome="Prime Steak",
            categoria="Carnes",
            unidade="KG",
            pesado=True,
            preco_venda=PRECO_REAL,
        )
        carne.saldo = SaldoEstoque(
            quantidade=Decimal("50.000"), aguardando_inventario=False
        )
        db.session.add(carne)
        db.session.flush()
        db.session.add(
            CodigoProduto(
                empresa_id=1,
                produto_id=carne.id,
                codigo=PLU_REAL,
                chave_busca=PLU_REAL,
                tipo="plu",
                principal=True,
            )
        )
        db.session.commit()
        app.config["TEST_CARNE_ID"] = carne.id
    return app


def _operador():
    return db.session.scalar(
        db.select(Usuario).where(Usuario.usuario == "operador.carnes")
    )


def test_bipar_etiqueta_encontra_o_produto_pelo_plu(app_balanca):
    with app_balanca.app_context():
        codigo_produto, etiqueta = identificar_etiqueta(1, ETIQUETA_REAL)
        produto = db.session.get(Produto, codigo_produto.produto_id)
        assert produto.nome == "Prime Steak"
        assert etiqueta.valor_total == TOTAL_REAL


def test_venda_pesada_baixa_o_peso_exato_do_estoque(app_balanca):
    """O caso que a loja precisa: 50 kg em estoque, vende 1,258 kg pela
    etiqueta, sobra 48,742 kg — e a baixa acontece ao concluir a venda."""
    with app_balanca.app_context():
        operador = _operador()
        carne_id = app_balanca.config["TEST_CARNE_ID"]

        saldo_antes = Decimal(db.session.get(SaldoEstoque, carne_id).quantidade)
        assert saldo_antes == Decimal("50.000")

        venda = iniciar_venda(empresa_id=1, usuario_id=operador.id)
        venda = adicionar_item(
            empresa_id=1,
            venda_id=venda.id,
            versao=venda.versao,
            codigo_digitado=ETIQUETA_REAL,
            quantidade_digitada=None,
            usuario_id=operador.id,
        )

        item = venda.itens[0]
        assert item.quantidade == PESO_REAL
        assert item.subtotal == TOTAL_REAL
        assert item.codigo_lido == ETIQUETA_REAL

        # Antes de concluir, o estoque não pode ter mudado.
        assert Decimal(db.session.get(SaldoEstoque, carne_id).quantidade) == saldo_antes

        venda, _ = finalizar_venda(
            empresa_id=1,
            venda_id=venda.id,
            versao=venda.versao,
            pagamentos_digitados={
                "dinheiro": (str(venda.total), str(venda.total)),
                "pix": (None, None),
                "debito": (None, None),
                "credito": (None, None),
            },
            usuario_id=operador.id,
        )

        saldo_depois = Decimal(db.session.get(SaldoEstoque, carne_id).quantidade)
        assert saldo_depois == Decimal("48.742")
        assert saldo_antes - saldo_depois == PESO_REAL

        movimento = db.session.scalar(
            db.select(MovimentoEstoque).where(
                MovimentoEstoque.produto_id == carne_id,
                MovimentoEstoque.tipo == "saida",
            )
        )
        assert movimento is not None
        assert movimento.variacao == -PESO_REAL
        assert movimento.quantidade_final == Decimal("48.742")


def test_quantidade_digitada_e_ignorada_quando_vem_etiqueta(app_balanca):
    """Quem pesou foi a balança. O que o operador digitar não pode prevalecer."""
    with app_balanca.app_context():
        operador = _operador()
        venda = iniciar_venda(empresa_id=1, usuario_id=operador.id)
        venda = adicionar_item(
            empresa_id=1,
            venda_id=venda.id,
            versao=venda.versao,
            codigo_digitado=ETIQUETA_REAL,
            quantidade_digitada="99",
            usuario_id=operador.id,
        )
        assert venda.itens[0].quantidade == PESO_REAL


def test_duas_etiquetas_do_mesmo_corte_somam_o_peso(app_balanca):
    with app_balanca.app_context():
        operador = _operador()
        outra = montar_etiqueta(PLU_REAL, Decimal("29.99"), LAYOUT_LOJA)
        venda = iniciar_venda(empresa_id=1, usuario_id=operador.id)
        venda = adicionar_item(
            empresa_id=1, venda_id=venda.id, versao=venda.versao,
            codigo_digitado=ETIQUETA_REAL, quantidade_digitada=None,
            usuario_id=operador.id,
        )
        venda = adicionar_item(
            empresa_id=1, venda_id=venda.id, versao=venda.versao,
            codigo_digitado=outra, quantidade_digitada=None,
            usuario_id=operador.id,
        )
        segundo_peso = (Decimal("29.99") / PRECO_REAL).quantize(Decimal("0.001"))
        assert venda.itens[0].quantidade == PESO_REAL + segundo_peso


def test_balanca_com_preco_atrasado_recusa_o_item(app_balanca):
    """Preço alterado no Big Jhow não pode liberar etiqueta antiga da Prix.

    A etiqueta desta loja embute o valor total, não o preço/kg original. Por isso
    não existe uma comparação matemática confiável capaz de descobrir sozinha
    qual preço a balança usou no momento da impressão. A proteção real em produção
    é o vínculo MGV6/Prix: se o Big Jhow mudou de preço e o MGV6 ainda confirma o
    preço anterior, a etiqueta precisa ficar bloqueada até nova sincronização.
    """
    with app_balanca.app_context():
        app_balanca.config["MGV6_ENABLED"] = True
        operador = _operador()
        carne = db.session.get(Produto, app_balanca.config["TEST_CARNE_ID"])
        carne.nome = "PRIME STEAK"
        carne.mgv6 = IntegracaoMGV6Produto(
            empresa_id=1,
            codigo_item="27",
            descricao_mgv="PRIME STEAK",
            status="mgv_ok",
            preco_mgv=PRECO_REAL,
            preco_enviado=PRECO_REAL,
        )
        carne.preco_venda = Decimal("70.00")  # Big Jhow mudou; MGV6/Prix ainda no preço anterior.
        db.session.commit()

        venda = iniciar_venda(empresa_id=1, usuario_id=operador.id)
        with pytest.raises(Exception) as erro:
            adicionar_item(
                empresa_id=1, venda_id=venda.id, versao=venda.versao,
                codigo_digitado=ETIQUETA_REAL, quantidade_digitada=None,
                usuario_id=operador.id,
            )
        mensagem = str(erro.value).lower()
        assert "não foi confirmado no mgv6" in mensagem
        assert "sincronize" in mensagem


def test_etiqueta_de_plu_nao_cadastrado_da_mensagem_util(app_balanca):
    with app_balanca.app_context():
        operador = _operador()
        desconhecida = montar_etiqueta("009999", Decimal("10.00"), LAYOUT_LOJA)
        venda = iniciar_venda(empresa_id=1, usuario_id=operador.id)
        with pytest.raises(Exception) as erro:
            adicionar_item(
                empresa_id=1, venda_id=venda.id, versao=venda.versao,
                codigo_digitado=desconhecida, quantidade_digitada=None,
                usuario_id=operador.id,
            )
        assert "009999" in str(erro.value)


def test_codigo_comum_continua_funcionando_com_a_balanca_ligada(app_balanca):
    """Refrigerante bipado pelo EAN não pode ser confundido com etiqueta."""
    with app_balanca.app_context():
        operador = _operador()
        produto_id = app_balanca.config["TEST_PRODUCT_ID"]
        saldo = db.session.get(SaldoEstoque, produto_id)
        saldo.quantidade = Decimal("50.000")
        saldo.aguardando_inventario = False
        db.session.commit()

        venda = iniciar_venda(empresa_id=1, usuario_id=operador.id)
        venda = adicionar_item(
            empresa_id=1, venda_id=venda.id, versao=venda.versao,
            codigo_digitado="001", quantidade_digitada="3",
            usuario_id=operador.id,
        )
        venda, _ = finalizar_venda(
            empresa_id=1, venda_id=venda.id, versao=venda.versao,
            pagamentos_digitados={
                "dinheiro": (str(venda.total), str(venda.total)),
                "pix": (None, None), "debito": (None, None), "credito": (None, None),
            },
            usuario_id=operador.id,
        )
        assert Decimal(db.session.get(SaldoEstoque, produto_id).quantidade) == Decimal("47.000")


# --------------------------------------------------------------------------
# Carga de preços
# --------------------------------------------------------------------------


def test_carga_traz_os_produtos_com_plu_e_o_preco_do_sistema(app_balanca):
    with app_balanca.app_context():
        conteudo, nome, linhas = gerar_carga(1)
        assert linhas == 1
        texto = conteudo.decode("latin-1")
        assert PLU_REAL in texto
        assert "PRIME STEAK" in texto, "a balança imprime sem acento e em maiúsculas"
        assert "59.99" in texto
        assert texto.endswith("\r\n")
        assert nome.endswith(".txt")


def test_carga_acompanha_a_mudanca_de_preco(app_balanca):
    """É isto que mantém a balança em dia: mudou no sistema, sai na carga."""
    with app_balanca.app_context():
        carne = db.session.get(Produto, app_balanca.config["TEST_CARNE_ID"])
        carne.preco_venda = Decimal("64.90")
        db.session.commit()
        conteudo, _, _ = gerar_carga(1)
        assert "64.90" in conteudo.decode("latin-1")


def test_produto_por_unidade_e_sem_plu_nao_vai_para_a_balanca(app_balanca):
    """O refrigerante do fixture é por unidade e o código dele é do tipo
    legado — a balança não precisa conhecê-lo."""
    with app_balanca.app_context():
        conteudo, _, linhas = gerar_carga(1)
        assert linhas == 1
        assert "PRODUTO TESTE" not in conteudo.decode("latin-1")


def test_produto_pesavel_entra_na_carga_mesmo_com_codigo_legado(app_balanca):
    """A carga inicial trouxe os códigos da loja como legado. Exigir retipagem
    de milhares de itens antes de carregar a balança travaria a operação."""
    with app_balanca.app_context():
        linguica = Produto(
            empresa_id=1, sku_interno="CARNE-002", nome="Linguiça Toscana",
            categoria="Carnes", unidade="KG", pesado=True,
            preco_venda=Decimal("24.50"),
        )
        linguica.saldo = SaldoEstoque(quantidade=Decimal("10"), aguardando_inventario=False)
        db.session.add(linguica)
        db.session.flush()
        db.session.add(
            CodigoProduto(
                empresa_id=1, produto_id=linguica.id, codigo="003100",
                chave_busca="003100", tipo="legado", principal=True,
            )
        )
        db.session.commit()

        conteudo, _, linhas = gerar_carga(1)
        texto = conteudo.decode("latin-1")
        assert linhas == 2
        assert "003100" in texto
        assert "LINGUICA TOSCANA" in texto, "sem acento, como a balança imprime"
        assert "24.50" in texto


# --------------------------------------------------------------------------
# Proteção de preço MGV6 -> Prix
# --------------------------------------------------------------------------


def test_etiqueta_mapeada_fica_bloqueada_ate_confirmar_a_prix(app_balanca):
    with app_balanca.app_context():
        app_balanca.config["MGV6_ENABLED"] = True
        operador = _operador()
        carne = db.session.get(Produto, app_balanca.config["TEST_CARNE_ID"])
        carne.nome = "PRIME STEAK"
        carne.mgv6 = IntegracaoMGV6Produto(
            empresa_id=1,
            codigo_item="27",
            descricao_mgv="PRIME STEAK",
            status="aguardando_balanca",
            preco_mgv=PRECO_REAL,
            preco_enviado=PRECO_REAL,
        )
        db.session.commit()

        venda = iniciar_venda(empresa_id=1, usuario_id=operador.id)
        with pytest.raises(Exception) as erro:
            adicionar_item(
                empresa_id=1,
                venda_id=venda.id,
                versao=venda.versao,
                codigo_digitado=ETIQUETA_REAL,
                quantidade_digitada=None,
                usuario_id=operador.id,
            )
        assert "balança ainda não foi confirmada" in str(erro.value)


def test_etiqueta_mapeada_e_confirmada_volta_a_ser_aceita(app_balanca):
    with app_balanca.app_context():
        app_balanca.config["MGV6_ENABLED"] = True
        operador = _operador()
        carne = db.session.get(Produto, app_balanca.config["TEST_CARNE_ID"])
        carne.nome = "PRIME STEAK"
        carne.mgv6 = IntegracaoMGV6Produto(
            empresa_id=1,
            codigo_item="27",
            descricao_mgv="PRIME STEAK",
            status="mgv_ok",
            preco_mgv=PRECO_REAL,
            preco_enviado=PRECO_REAL,
        )
        db.session.commit()

        venda = iniciar_venda(empresa_id=1, usuario_id=operador.id)
        venda = adicionar_item(
            empresa_id=1,
            venda_id=venda.id,
            versao=venda.versao,
            codigo_digitado=ETIQUETA_REAL,
            quantidade_digitada=None,
            usuario_id=operador.id,
        )
        assert venda.itens[0].quantidade == PESO_REAL
