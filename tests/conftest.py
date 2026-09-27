from __future__ import annotations

from decimal import Decimal

import pytest

from bigjhow import create_app
from bigjhow.config import TestConfig
from bigjhow.extensions import db
from bigjhow.models import (
    CodigoProduto,
    Empresa,
    Impressora,
    Produto,
    RegraImpressao,
    SaldoEstoque,
    TerminalCaixa,
    TurnoCaixa,
    Usuario,
    UsuarioEmpresa,
)


@pytest.fixture()
def app():
    application = create_app(TestConfig)
    with application.app_context():
        db.create_all()
        companies = [
            Empresa(id=1, nome="Big Jhow Carnes", slug="big-jhow-carnes"),
            Empresa(id=2, nome="Big Jhow Steak House", slug="big-jhow-steak-house"),
            Empresa(id=3, nome="Big Jhow Sushi", slug="big-jhow-sushi"),
        ]
        owner = Usuario(
            nome="Proprietário Teste",
            usuario="dono",
            papel="proprietario",
            troca_senha_obrigatoria=False,
        )
        owner.set_senha("SenhaForte!123")
        administrator = Usuario(
            nome="Administrador Carnes",
            usuario="admin.carnes",
            papel="administrador",
            troca_senha_obrigatoria=False,
        )
        administrator.set_senha("SenhaForte!123")
        administrator.empresas.append(UsuarioEmpresa(empresa_id=1))
        operator = Usuario(
            nome="Operador Carnes",
            usuario="operador.carnes",
            papel="operador",
            troca_senha_obrigatoria=False,
        )
        operator.set_senha("SenhaForte!123")
        operator.empresas.append(UsuarioEmpresa(empresa_id=1))
        product = Produto(
            empresa_id=1,
            sku_interno="TEST-001",
            codigo_externo="001",
            nome="Produto Teste",
            categoria="Testes",
            unidade="UN",
            preco_venda=Decimal("10.00"),
        )
        product.saldo = SaldoEstoque(quantidade=Decimal("0"), aguardando_inventario=True)
        db.session.add_all([*companies, owner, administrator, operator, product])
        db.session.flush()
        terminais = []
        for indice, usuario in enumerate((owner, administrator, operator), start=1):
            terminal = TerminalCaixa(
                empresa_id=1,
                codigo=f"CAIXA-{indice:02d}",
                nome=f"Caixa de teste {indice}",
                criado_por_id=owner.id,
            )
            db.session.add(terminal)
            db.session.flush()
            terminais.append(terminal)
            db.session.add(
                TurnoCaixa(
                    empresa_id=1,
                    terminal_id=terminal.id,
                    operador_id=usuario.id,
                    aberto_por_id=usuario.id,
                    valor_inicial=Decimal("0.00"),
                )
            )
        impressora = Impressora(
            empresa_id=1,
            codigo="SIM-CUPOM-01",
            nome="Impressora simulada de teste",
            setor="Caixa",
            tipo="cupom",
            modo="simulada",
            criado_por_id=owner.id,
        )
        db.session.add(impressora)
        db.session.flush()
        for terminal in terminais:
            for evento in (
                "venda_finalizada",
                "venda_cancelada",
                "turno_fechado",
                "movimento_caixa_aprovado",
            ):
                db.session.add(
                    RegraImpressao(
                        empresa_id=1,
                        terminal_id=terminal.id,
                        impressora_id=impressora.id,
                        evento=evento,
                        copias=1,
                        criado_por_id=owner.id,
                    )
                )
        db.session.add(
            CodigoProduto(
                empresa_id=1,
                produto_id=product.id,
                codigo="001",
                chave_busca="001",
                tipo="legado",
                principal=True,
            )
        )
        db.session.commit()
        product_id = product.id

    application.config["TEST_PRODUCT_ID"] = product_id
    yield application

    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def app_fiscal(app, monkeypatch):
    """Aplicação com a empresa 1 pronta para emitir em modo de ensaio.

    Usa o provedor ``homologacao_interna``: exercita cadastro, numeração,
    montagem do XML, DANFE e impressão sem falar com a SEFAZ. Os documentos
    gerados são marcados como sem valor fiscal.
    """
    from bigjhow.models import EstabelecimentoFiscal, Produto, ProdutoFiscal

    monkeypatch.setenv("FISCAL_SECRET_KEY", "chave-de-teste-" + "x" * 40)
    app.config["FISCAL_SECRET_KEY"] = "chave-de-teste-" + "x" * 40

    with app.app_context():
        db.session.add(
            EstabelecimentoFiscal(
                empresa_id=1,
                cnpj="11222333000181",
                razao_social="BIG JHOW CARNES LTDA",
                nome_fantasia="Big Jhow Carnes",
                inscricao_estadual="110042490114",
                crt=1,
                logradouro="Avenida Brasil",
                numero="1520",
                bairro="Centro",
                codigo_municipio=3550308,
                municipio="Sao Paulo",
                uf="SP",
                cep="01010000",
                modelo=65,
                serie=1,
                proximo_numero=1,
                ambiente=2,
                natureza_operacao="VENDA AO CONSUMIDOR",
                provedor="homologacao_interna",
                parametros_provedor={},
                responsavel_cnpj="11444777000161",
                responsavel_contato="Suporte",
                responsavel_email="ti@exemplo.com.br",
                responsavel_telefone="1133334444",
                enderecos_conferidos=True,
                emissao_ativa=True,
                permitir_contingencia=True,
            )
        )
        for produto in db.session.scalars(
            db.select(Produto).where(Produto.empresa_id == 1)
        ).all():
            db.session.add(
                ProdutoFiscal(
                    produto_id=produto.id,
                    empresa_id=1,
                    ncm="02013000",
                    cfop="5102",
                    origem=0,
                    icms_csosn="102",
                    pis_cst="07",
                    cofins_cst="07",
                    revisao_pendente=False,
                )
            )
        db.session.commit()
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, usuario="dono", password="SenhaForte!123"):
    return client.post(
        "/auth/login",
        data={"usuario": usuario, "senha": password},
        follow_redirects=False,
    )
