from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from bigjhow.extensions import db
from bigjhow.models import (
    Auditoria,
    Impressora,
    MovimentoCaixa,
    TerminalCaixa,
    TrabalhoImpressao,
    TurnoCaixa,
    Usuario,
    Venda,
)
from bigjhow.services.caixa import adicionar_item, finalizar_venda, iniciar_venda
from bigjhow.services.impressao import (
    ImpressaoConflito,
    criar_reimpressao,
    enfileirar_venda,
    reprocessar_trabalho,
    simular_falha,
    simular_impressao,
)
from bigjhow.services.turnos import (
    TurnoConflito,
    TurnoErro,
    abrir_turno,
    decidir_movimento,
    fechar_turno,
    normalizar_valor_caixa,
    resumo_turno,
    solicitar_movimento,
)

from .conftest import login


def _usuario(usuario: str):
    return db.session.scalar(db.select(Usuario).where(Usuario.usuario == usuario))


def _turno(usuario: str = "operador.carnes"):
    user = _usuario(usuario)
    return db.session.scalar(
        db.select(TurnoCaixa).where(
            TurnoCaixa.operador_id == user.id, TurnoCaixa.status == "aberto"
        )
    )


def _produto_pronto(app):
    from bigjhow.models import Produto

    produto = db.session.get(Produto, app.config["TEST_PRODUCT_ID"])
    produto.preco_venda = Decimal("10.00")
    produto.saldo.quantidade = Decimal("10.000")
    produto.saldo.aguardando_inventario = False
    db.session.commit()
    return produto


def test_money_input_is_strict_and_never_accepts_sign_or_exponent():
    assert normalizar_valor_caixa("0") == Decimal("0.00")
    assert normalizar_valor_caixa("123,45") == Decimal("123.45")
    for invalido in ("", "-1", "+1", "1e3", "1.000,00", "10.999"):
        with pytest.raises(TurnoErro):
            normalizar_valor_caixa(invalido)


def test_new_sale_is_blocked_without_open_shift(app, client):
    with app.app_context():
        turno = _turno()
        db.session.delete(turno)
        db.session.commit()
    login(client, "operador.carnes")
    response = client.post("/empresa/1/caixa/iniciar", follow_redirects=True)
    assert response.status_code == 200
    assert "Abra um turno" in response.text
    with app.app_context():
        assert db.session.scalar(
            db.select(db.func.count(Venda.id)).where(Venda.status == "aberta")
        ) == 0


def test_shift_routes_enforce_company_and_role_boundaries(app, client):
    login(client, "operador.carnes")
    page = client.get("/empresa/1/operacao")
    assert page.status_code == 200
    assert "<h1>Caixa</h1>" in page.text
    assert "Nova venda" in page.text
    assert "Minhas impressões" in page.text
    assert "Terminais e impressoras" not in page.text
    assert page.headers["Cache-Control"].startswith("no-store")
    assert client.get("/empresa/2/operacao").status_code == 403
    assert client.get("/empresa/1/operacao/configuracao").status_code == 403


def test_all_roles_use_the_same_cashier_page_with_server_side_limits(app, client):
    for usuario in ("operador.carnes", "admin.carnes", "dono"):
        client.post("/auth/logout")
        login(client, usuario)
        page = client.get("/empresa/1/operacao")
        assert page.status_code == 200
        assert "<h1>Caixa</h1>" in page.text
        assert "Nova venda" in page.text
        assert "Operação do caixa" not in page.text
        assert page.text.count(">Caixa</span>") == 1
        if usuario == "operador.carnes":
            assert "Terminais e impressoras" not in page.text
            assert client.get("/empresa/1/operacao/autorizacoes").status_code == 403
        else:
            assert "Terminais e impressoras" in page.text

    antigo = client.get("/empresa/1/caixa")
    assert antigo.status_code == 302
    assert antigo.headers["Location"].endswith("/empresa/1/operacao")


def test_owner_operational_configuration_and_shift_pages_render(app, client):
    client.post("/auth/logout")
    login(client, "dono")
    assert client.get("/empresa/1/operacao/configuracao").status_code == 200
    assert client.get("/empresa/1/operacao/impressoes").status_code == 200
    with app.app_context():
        turno = _turno("operador.carnes")
        turno_id = turno.id
        trabalho = TrabalhoImpressao(
            empresa_id=1,
            terminal_id=turno.terminal_id,
            impressora_id=db.session.scalar(
                db.select(Impressora.id).where(Impressora.empresa_id == 1)
            ),
            evento="turno_fechado",
            entidade="turno_caixa",
            entidade_id=str(turno.id),
            chave_idempotencia=f"render:{turno.id}",
            payload={"teste": "renderização"},
            solicitado_por_id=_usuario("operador.carnes").id,
        )
        db.session.add(trabalho)
        db.session.commit()
        trabalho_id = trabalho.id
    detail = client.get(f"/empresa/1/operacao/turnos/{turno_id}")
    assert detail.status_code == 200
    assert "FECHAMENTO CEGO" in detail.text
    work = client.get(f"/empresa/1/operacao/impressoes/{trabalho_id}")
    assert work.status_code == 200
    assert "imprimir-versao" in work.text
    assert "falhar-versao" in work.text


def test_database_prevents_two_open_shifts_for_terminal_or_operator(app):
    with app.app_context():
        operador = _usuario("operador.carnes")
        terminal_livre = TerminalCaixa(
            empresa_id=1, codigo="CAIXA-99", nome="Caixa livre"
        )
        db.session.add(terminal_livre)
        db.session.commit()
        with pytest.raises(TurnoConflito):
            abrir_turno(
                empresa_id=1,
                terminal_id=terminal_livre.id,
                operador_id=operador.id,
                valor_inicial="0",
            )
        turno_existente = _turno()
        outro = _usuario("admin.carnes")
        with pytest.raises(TurnoConflito):
            abrir_turno(
                empresa_id=1,
                terminal_id=turno_existente.terminal_id,
                operador_id=outro.id,
                valor_inicial="0",
            )


def test_cash_movement_requires_approval_and_cannot_make_expected_cash_negative(app):
    with app.app_context():
        operador = _usuario("operador.carnes")
        admin = _usuario("admin.carnes")
        turno = _turno()
        suprimento = solicitar_movimento(
            empresa_id=1,
            turno_id=turno.id,
            tipo="suprimento",
            valor="10,00",
            motivo="Troco adicional controlado",
            usuario_id=operador.id,
            autorizar_imediatamente=False,
        )
        assert suprimento.status == "pendente"
        decidido = decidir_movimento(
            empresa_id=1,
            movimento_id=suprimento.id,
            versao=suprimento.versao,
            aprovar=True,
            motivo_decisao="Valor conferido pelo administrador",
            usuario_id=admin.id,
        )
        assert decidido.status == "aprovado"
        assert resumo_turno(turno.id)["dinheiro"] == Decimal("10.00")

        sangria = solicitar_movimento(
            empresa_id=1,
            turno_id=turno.id,
            tipo="sangria",
            valor="20,00",
            motivo="Teste de limite de caixa",
            usuario_id=operador.id,
            autorizar_imediatamente=False,
        )
        with pytest.raises(TurnoConflito):
            decidir_movimento(
                empresa_id=1,
                movimento_id=sangria.id,
                versao=sangria.versao,
                aprovar=True,
                motivo_decisao="Tentativa controlada de aprovação",
                usuario_id=admin.id,
            )
        db.session.rollback()
        preservado = db.session.get(MovimentoCaixa, sangria.id)
        assert preservado.status == "pendente"


def test_closed_shift_history_explains_cash_payments_change_and_movements(app, client):
    with app.app_context():
        _produto_pronto(app)
        operador = _usuario("operador.carnes")
        turno = _turno()
        venda = iniciar_venda(empresa_id=1, usuario_id=operador.id)
        venda = adicionar_item(
            empresa_id=1,
            venda_id=venda.id,
            versao=venda.versao,
            codigo_digitado="001",
            quantidade_digitada="1",
            usuario_id=operador.id,
        )
        finalizar_venda(
            empresa_id=1,
            venda_id=venda.id,
            versao=venda.versao,
            pagamentos_digitados={
                "dinheiro": ("10,00", "20,00"),
                "pix": ("", None),
                "debito": ("", None),
                "credito": ("", None),
            },
            usuario_id=operador.id,
        )
        solicitar_movimento(
            empresa_id=1,
            turno_id=turno.id,
            tipo="suprimento",
            valor="5,00",
            motivo="Troco adicional conferido",
            usuario_id=operador.id,
            autorizar_imediatamente=True,
        )
        solicitar_movimento(
            empresa_id=1,
            turno_id=turno.id,
            tipo="sangria",
            valor="3,00",
            motivo="Retirada controlada do caixa",
            usuario_id=operador.id,
            autorizar_imediatamente=True,
        )
        turno = db.session.get(TurnoCaixa, turno.id)
        fechado = fechar_turno(
            empresa_id=1,
            turno_id=turno.id,
            versao=turno.versao,
            valores_informados={
                "dinheiro": "12,00",
                "pix": "0",
                "debito": "0",
                "credito": "0",
            },
            justificativa=None,
            usuario_id=operador.id,
            pode_fechar_outro=False,
        )
        assert fechado.dinheiro_diferenca == Decimal("0.00")

    login(client, "operador.carnes")
    historico = client.get("/empresa/1/operacao/historico")
    assert historico.status_code == 200
    assert "Dinheiro recebido" in historico.text
    assert "R$ 20,00" in historico.text
    assert "Troco entregue" in historico.text
    assert "- R$ 10,00" in historico.text
    assert "+ R$ 5,00" in historico.text
    assert "- R$ 3,00" in historico.text
    assert "Esperado na gaveta" in historico.text
    assert "R$ 12,00" in historico.text


def test_blind_closing_requires_reason_for_difference_and_freezes_server_summary(app):
    with app.app_context():
        operador = _usuario("operador.carnes")
        turno = _turno()
        with pytest.raises(TurnoErro):
            fechar_turno(
                empresa_id=1,
                turno_id=turno.id,
                versao=turno.versao,
                valores_informados={
                    "dinheiro": "1,00",
                    "pix": "0",
                    "debito": "0",
                    "credito": "0",
                },
                justificativa="",
                usuario_id=operador.id,
                pode_fechar_outro=False,
            )
        db.session.rollback()
        turno = _turno()
        fechado = fechar_turno(
            empresa_id=1,
            turno_id=turno.id,
            versao=turno.versao,
            valores_informados={
                "dinheiro": "1,00",
                "pix": "0",
                "debito": "0",
                "credito": "0",
            },
            justificativa="Diferença controlada para teste",
            usuario_id=operador.id,
            pode_fechar_outro=False,
        )
        assert fechado.status == "fechado"
        assert fechado.dinheiro_esperado == Decimal("0.00")
        assert fechado.dinheiro_diferenca == Decimal("1.00")
        assert fechado.fechado_em is not None
        assert db.session.scalar(
            db.select(Auditoria).where(Auditoria.acao == "turno_caixa_fechado")
        ) is not None


def test_sale_and_print_job_are_created_once_in_the_same_flow(app):
    with app.app_context():
        produto = _produto_pronto(app)
        operador = _usuario("operador.carnes")
        venda = iniciar_venda(empresa_id=1, usuario_id=operador.id)
        venda = adicionar_item(
            empresa_id=1,
            venda_id=venda.id,
            versao=venda.versao,
            codigo_digitado="001",
            quantidade_digitada="1",
            usuario_id=operador.id,
        )
        venda, criada = finalizar_venda(
            empresa_id=1,
            venda_id=venda.id,
            versao=venda.versao,
            pagamentos_digitados={
                "dinheiro": ("10,00", "10,00"),
                "pix": ("", None),
                "debito": ("", None),
                "credito": ("", None),
            },
            usuario_id=operador.id,
        )
        assert criada is True
        assert produto.saldo.quantidade == Decimal("9.000")
        trabalhos = db.session.scalars(
            db.select(TrabalhoImpressao).where(
                TrabalhoImpressao.entidade_id == str(venda.id),
                TrabalhoImpressao.evento == "venda_finalizada",
            )
        ).all()
        assert len(trabalhos) == 1
        assert trabalhos[0].payload["documento"] == "TICKET SEM VALOR FISCAL"
        enfileirar_venda(venda, evento="venda_finalizada", usuario_id=operador.id)
        db.session.commit()
        assert db.session.scalar(
            db.select(db.func.count(TrabalhoImpressao.id)).where(
                TrabalhoImpressao.entidade_id == str(venda.id),
                TrabalhoImpressao.evento == "venda_finalizada",
            )
        ) == 1


def test_print_failure_retry_and_reprint_are_audited_and_versioned(app):
    with app.app_context():
        operador = _usuario("operador.carnes")
        admin = _usuario("admin.carnes")
        turno = _turno()
        trabalho = TrabalhoImpressao(
            empresa_id=1,
            terminal_id=turno.terminal_id,
            impressora_id=db.session.scalar(
                db.select(Impressora.id).where(Impressora.empresa_id == 1)
            ),
            evento="turno_fechado",
            entidade="turno_caixa",
            entidade_id=str(turno.id),
            chave_idempotencia=f"teste-fila:{turno.id}",
            payload={"teste": "controlado"},
            solicitado_por_id=operador.id,
        )
        db.session.add(trabalho)
        db.session.commit()
        trabalho = simular_falha(
            empresa_id=1,
            trabalho_id=trabalho.id,
            versao=trabalho.versao,
            usuario_id=admin.id,
        )
        assert trabalho.status == "falhou"
        with pytest.raises(ImpressaoConflito):
            simular_impressao(
                empresa_id=1,
                trabalho_id=trabalho.id,
                versao=trabalho.versao,
                usuario_id=admin.id,
            )
        db.session.rollback()
        trabalho = db.session.get(TrabalhoImpressao, trabalho.id)
        trabalho = reprocessar_trabalho(
            empresa_id=1,
            trabalho_id=trabalho.id,
            versao=trabalho.versao,
            usuario_id=admin.id,
        )
        trabalho = simular_impressao(
            empresa_id=1,
            trabalho_id=trabalho.id,
            versao=trabalho.versao,
            usuario_id=admin.id,
        )
        assert trabalho.status == "impresso"
        assert trabalho.tentativas == 2
        reimpressao = criar_reimpressao(
            empresa_id=1,
            trabalho_id=trabalho.id,
            motivo="Cliente solicitou segunda via",
            versao=trabalho.versao,
            usuario_id=admin.id,
        )
        assert reimpressao.reimpressao_de_id == trabalho.id
        assert reimpressao.status == "pendente"
        assert reimpressao.chave_idempotencia != trabalho.chave_idempotencia
        with pytest.raises(ImpressaoConflito):
            criar_reimpressao(
                empresa_id=1,
                trabalho_id=trabalho.id,
                motivo="Clique duplicado controlado",
                versao=trabalho.versao - 1,
                usuario_id=admin.id,
            )


def test_operator_cannot_view_another_users_print_job(app, client):
    with app.app_context():
        admin = _usuario("admin.carnes")
        turno = _turno("admin.carnes")
        impressora_id = db.session.scalar(db.select(Impressora.id).where(Impressora.empresa_id == 1))
        trabalho = TrabalhoImpressao(
            empresa_id=1,
            terminal_id=turno.terminal_id,
            impressora_id=impressora_id,
            evento="turno_fechado",
            entidade="turno_caixa",
            entidade_id=str(turno.id),
            chave_idempotencia=f"admin:{turno.id}",
            payload={"teste": True},
            solicitado_por_id=admin.id,
        )
        db.session.add(trabalho)
        db.session.commit()
        trabalho_id = trabalho.id
    login(client, "operador.carnes")
    assert client.get(f"/empresa/1/operacao/impressoes/{trabalho_id}").status_code == 404


def test_database_rejects_invalid_printer_mode(app):
    with app.app_context():
        db.session.add(
            Impressora(
                empresa_id=1,
                codigo="FISICA-NAO-PERMITIDA",
                nome="Acesso físico indevido",
                setor="Caixa",
                tipo="cupom",
                modo="usb",
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_operator_can_open_shift_through_validated_route(app, client):
    with app.app_context():
        turno = _turno()
        terminal_id = turno.terminal_id
        db.session.delete(turno)
        db.session.commit()
    login(client, "operador.carnes")
    response = client.post(
        "/empresa/1/operacao/turnos/abrir",
        data={
            "terminal_id": str(terminal_id),
            "valor_inicial": "25,50",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/empresa/1/operacao")
    caixa = client.get(response.headers["Location"])
    assert "Turno aberto" in caixa.text
    with app.app_context():
        turno = _turno()
        assert turno.valor_inicial == Decimal("25.50")
        assert turno.aberto_por_id == _usuario("operador.carnes").id
    duplicado = client.post(
        "/empresa/1/operacao/turnos/abrir",
        data={
            "terminal_id": str(terminal_id),
            "valor_inicial": "0",
            "confirmacao": "y",
        },
        follow_redirects=True,
    )
    assert "já possui um turno aberto" in duplicado.text


def test_movement_approval_and_administrative_blind_close_work_through_routes(
    app, client
):
    with app.app_context():
        turno_id = _turno().id
    login(client, "operador.carnes")
    solicitado = client.post(
        f"/empresa/1/operacao/turnos/{turno_id}/movimentos",
        data={
            "movimento-turno_id": str(turno_id),
            "movimento-tipo": "suprimento",
            "movimento-valor": "15,00",
            "movimento-motivo": "Troco controlado para abertura",
        },
        follow_redirects=True,
    )
    assert "aguardando autorização" in solicitado.text
    with app.app_context():
        movimento = db.session.scalar(
            db.select(MovimentoCaixa).where(MovimentoCaixa.status == "pendente")
        )
        movimento_id, versao = movimento.id, movimento.versao

    client.post("/auth/logout")
    login(client, "dono")
    aprovado = client.post(
        f"/empresa/1/operacao/movimentos/{movimento_id}/aprovar",
        data={
            f"decisao-{movimento_id}-versao": str(versao),
            f"decisao-{movimento_id}-motivo": "Valor contado e autorizado",
        },
        follow_redirects=True,
    )
    assert "Decisão registrada" in aprovado.text
    with app.app_context():
        turno = db.session.get(TurnoCaixa, turno_id)
        versao_turno = turno.versao
    fechado = client.post(
        f"/empresa/1/operacao/turnos/{turno_id}/fechar",
        data={
            "fechar-versao": str(versao_turno),
            "fechar-dinheiro": "15,00",
            "fechar-pix": "0",
            "fechar-debito": "0",
            "fechar-credito": "0",
            "fechar-justificativa": "Fechamento administrativo controlado",
        },
        follow_redirects=True,
    )
    assert "Turno fechado" in fechado.text
    with app.app_context():
        turno = db.session.get(TurnoCaixa, turno_id)
        assert turno.status == "fechado"
        assert turno.dinheiro_diferenca == Decimal("0.00")
        assert db.session.scalar(
            db.select(db.func.count(TrabalhoImpressao.id)).where(
                TrabalhoImpressao.evento == "turno_fechado",
                TrabalhoImpressao.entidade_id == str(turno_id),
            )
        ) == 1


def test_open_sale_and_pending_movement_each_block_shift_closing(app):
    with app.app_context():
        operador = _usuario("operador.carnes")
        turno = _turno()
        venda = iniciar_venda(empresa_id=1, usuario_id=operador.id)
        with pytest.raises(TurnoErro, match="atendimento aberto"):
            fechar_turno(
                empresa_id=1,
                turno_id=turno.id,
                versao=turno.versao,
                valores_informados={
                    "dinheiro": "0",
                    "pix": "0",
                    "debito": "0",
                    "credito": "0",
                },
                justificativa=None,
                usuario_id=operador.id,
                pode_fechar_outro=False,
            )
        db.session.rollback()
        venda = db.session.get(Venda, venda.id)
        venda.status = "descartada"
        db.session.commit()
        turno = _turno()
        solicitar_movimento(
            empresa_id=1,
            turno_id=turno.id,
            tipo="suprimento",
            valor="5",
            motivo="Movimento ainda sem autorização",
            usuario_id=operador.id,
            autorizar_imediatamente=False,
        )
        turno = _turno()
        with pytest.raises(TurnoErro, match="sangrias e suprimentos"):
            fechar_turno(
                empresa_id=1,
                turno_id=turno.id,
                versao=turno.versao,
                valores_informados={
                    "dinheiro": "0",
                    "pix": "0",
                    "debito": "0",
                    "credito": "0",
                },
                justificativa=None,
                usuario_id=operador.id,
                pode_fechar_outro=False,
            )


def test_admin_configuration_routes_create_and_version_resources(app, client):
    login(client, "dono")
    terminal = client.post(
        "/empresa/1/operacao/configuracao/terminais",
        data={
            "terminal-novo-codigo": "CAIXA-ROTA",
            "terminal-novo-nome": "Terminal criado pela rota",
        },
        follow_redirects=True,
    )
    assert "Terminal cadastrado" in terminal.text
    impressora = client.post(
        "/empresa/1/operacao/configuracao/impressoras",
        data={
            "impressora-nova-codigo": "SIM-ROTA",
            "impressora-nova-nome": "Impressora simulada da rota",
            "impressora-nova-setor": "Expedição",
            "impressora-nova-tipo": "producao",
        },
        follow_redirects=True,
    )
    assert "Impressora simulada cadastrada" in impressora.text
    with app.app_context():
        terminal_obj = db.session.scalar(
            db.select(TerminalCaixa).where(TerminalCaixa.codigo == "CAIXA-ROTA")
        )
        impressora_obj = db.session.scalar(
            db.select(Impressora).where(Impressora.codigo == "SIM-ROTA")
        )
        terminal_id, terminal_versao = terminal_obj.id, terminal_obj.versao
        impressora_id, impressora_versao = impressora_obj.id, impressora_obj.versao
    regra = client.post(
        "/empresa/1/operacao/configuracao/regras",
        data={
            "regra-nova-terminal_id": str(terminal_id),
            "regra-nova-impressora_id": str(impressora_id),
            "regra-nova-evento": "venda_finalizada",
            "regra-nova-copias": "2",
        },
        follow_redirects=True,
    )
    assert "Regra de impressão cadastrada" in regra.text
    with app.app_context():
        from bigjhow.models import RegraImpressao

        regra_obj = db.session.scalar(
            db.select(RegraImpressao).where(
                RegraImpressao.terminal_id == terminal_id,
                RegraImpressao.impressora_id == impressora_id,
            )
        )
        regra_id, regra_versao = regra_obj.id, regra_obj.versao
    assert client.post(
        f"/empresa/1/operacao/configuracao/regras/{regra_id}/status",
        data={f"regra-{regra_id}-versao": str(regra_versao)},
    ).status_code == 302
    assert client.post(
        f"/empresa/1/operacao/configuracao/impressoras/{impressora_id}/status",
        data={f"impressora-{impressora_id}-versao": str(impressora_versao)},
    ).status_code == 302
    assert client.post(
        f"/empresa/1/operacao/configuracao/terminais/{terminal_id}/status",
        data={f"terminal-{terminal_id}-versao": str(terminal_versao)},
    ).status_code == 302
    with app.app_context():
        assert db.session.get(TerminalCaixa, terminal_id).ativo is False
        assert db.session.get(Impressora, impressora_id).ativo is False


def test_print_queue_routes_cover_failure_retry_completion_and_reprint(app, client):
    with app.app_context():
        operador = _usuario("operador.carnes")
        turno = _turno()
        trabalho = TrabalhoImpressao(
            empresa_id=1,
            terminal_id=turno.terminal_id,
            impressora_id=db.session.scalar(
                db.select(Impressora.id).where(Impressora.empresa_id == 1)
            ),
            evento="turno_fechado",
            entidade="turno_caixa",
            entidade_id=str(turno.id),
            chave_idempotencia=f"rotas-fila:{turno.id}",
            payload={"origem": "teste de rotas"},
            solicitado_por_id=operador.id,
        )
        db.session.add(trabalho)
        db.session.commit()
        trabalho_id, versao = trabalho.id, trabalho.versao
    login(client, "dono")
    falha = client.post(
        f"/empresa/1/operacao/impressoes/{trabalho_id}/falhar",
        data={"falhar-versao": str(versao)},
        follow_redirects=True,
    )
    assert "SIMULACAO_SEM_PAPEL" in falha.text
    with app.app_context():
        versao = db.session.get(TrabalhoImpressao, trabalho_id).versao
    reprocessado = client.post(
        f"/empresa/1/operacao/impressoes/{trabalho_id}/reprocessar",
        data={"reprocessar-versao": str(versao)},
        follow_redirects=True,
    )
    assert "Pendente" in reprocessado.text
    with app.app_context():
        versao = db.session.get(TrabalhoImpressao, trabalho_id).versao
    impresso = client.post(
        f"/empresa/1/operacao/impressoes/{trabalho_id}/imprimir",
        data={"imprimir-versao": str(versao)},
        follow_redirects=True,
    )
    assert "Impresso" in impresso.text
    with app.app_context():
        versao = db.session.get(TrabalhoImpressao, trabalho_id).versao
    reimpressao = client.post(
        f"/empresa/1/operacao/impressoes/{trabalho_id}/reimprimir",
        data={
            "versao": str(versao),
            "motivo": "Segunda via solicitada no teste de rotas",
        },
        follow_redirects=True,
    )
    assert "Reimpressão criada" in reimpressao.text
    with app.app_context():
        assert db.session.scalar(
            db.select(db.func.count(TrabalhoImpressao.id)).where(
                TrabalhoImpressao.reimpressao_de_id == trabalho_id
            )
        ) == 1
