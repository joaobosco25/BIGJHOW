from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from bigjhow.extensions import db
from bigjhow.models import Auditoria, Usuario, utcnow

from .conftest import login


def test_operator_is_read_only_in_interface_and_backend(app, client):
    login(client, "operador.carnes")
    product_id = app.config["TEST_PRODUCT_ID"]

    page = client.get("/empresa/1/estoque")
    assert page.status_code == 200
    assert "PERFIL SOMENTE PARA CONSULTA" in page.text
    assert "Consulte produtos, preços e saldos das empresas autorizadas." in page.text
    assert "Cadastre produtos" not in page.text
    assert "+ Novo produto" not in page.text
    assert "Movimentar</a>" not in page.text
    assert "Editar</a>" not in page.text

    assert client.get("/empresa/1/estoque/novo").status_code == 403
    assert client.get(f"/empresa/1/estoque/{product_id}/editar").status_code == 403
    assert client.get(f"/empresa/1/estoque/{product_id}/movimentar").status_code == 403
    assert client.post(f"/empresa/1/estoque/{product_id}/arquivar").status_code == 403
    assert client.get("/usuarios").status_code == 403
    assert client.get("/auditoria").status_code == 403


def test_administrator_manages_only_operators_inside_scope(app, client):
    login(client, "admin.carnes")

    page = client.get("/usuarios")
    assert page.status_code == 200
    table_body = page.text.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert "Operador Carnes" in table_body
    assert "Proprietário Teste" not in table_body
    assert "Administrador Carnes" not in table_body
    assert client.get("/empresa/1/estoque/novo").status_code == 200
    assert client.get("/empresa/2/estoque").status_code == 403
    assert client.get("/auditoria").status_code == 403

    response = client.post(
        "/usuarios/novo",
        data={
            "nome": "Operador Fora",
            "usuario": "operador.fora",
            "papel": "operador",
            "empresas": ["2"],
            "senha_temporaria": "Temporaria!456X",
            "confirmar_senha": "Temporaria!456X",
        },
    )
    assert response.status_code == 200
    with app.app_context():
        assert db.session.scalar(
            db.select(Usuario).where(Usuario.usuario == "operador.fora")
        ) is None

    with app.app_context():
        owner = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        owner_id = owner.id
    assert client.get(f"/usuarios/{owner_id}/editar").status_code == 403


def test_owner_creates_user_with_temporary_password_and_audit(app, client):
    login(client)
    temporary_password = "Temporaria!456X"
    response = client.post(
        "/usuarios/novo",
        data={
            "nome": "Caixa Novo",
            "usuario": "caixa.novo",
            "papel": "operador",
            "empresas": ["1"],
            "senha_temporaria": temporary_password,
            "confirmar_senha": temporary_password,
        },
    )
    assert response.status_code == 302

    with app.app_context():
        created = db.session.scalar(
            db.select(Usuario).where(Usuario.usuario == "caixa.novo")
        )
        assert created is not None
        assert created.papel == "operador"
        assert created.empresas_ids == {1}
        assert created.troca_senha_obrigatoria is True
        assert created.verificar_senha(temporary_password)
        audit = db.session.scalar(
            db.select(Auditoria).where(
                Auditoria.acao == "usuario_criado",
                Auditoria.entidade_id == str(created.id),
            )
        )
        assert audit is not None
        assert temporary_password not in str(audit.detalhes)
        assert "senha_temporaria" not in str(audit.detalhes)


def test_forced_password_change_keeps_current_session_and_invalidates_old_password(app, client):
    with app.app_context():
        user = Usuario(
            nome="Caixa Temporário",
            usuario="caixa.temp",
            papel="operador",
            troca_senha_obrigatoria=True,
        )
        user.set_senha("Temporaria!456X")
        from bigjhow.models import UsuarioEmpresa

        user.empresas.append(UsuarioEmpresa(empresa_id=1))
        db.session.add(user)
        db.session.commit()

    login(client, "caixa.temp", "Temporaria!456X")
    blocked = client.get("/empresa/1/estoque")
    assert blocked.status_code == 302
    assert blocked.headers["Location"].endswith("/auth/alterar-senha")

    changed = client.post(
        "/auth/alterar-senha",
        data={
            "senha_atual": "Temporaria!456X",
            "nova_senha": "Definitiva!789X",
            "confirmar_senha": "Definitiva!789X",
        },
    )
    assert changed.status_code == 302
    assert client.get("/empresa/1/estoque").status_code == 200

    client.post("/auth/logout")
    assert login(client, "caixa.temp", "Temporaria!456X").status_code == 200
    assert login(client, "caixa.temp", "Definitiva!789X").status_code == 302


def test_security_version_invalidates_existing_session(app, client):
    login(client)
    with app.app_context():
        owner = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        owner.sessao_versao += 1
        db.session.commit()

    response = client.get("/grupo/estoque")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/login")


def test_password_reset_invalidates_operator_session_and_never_audits_secret(app):
    operator_client = app.test_client()
    owner_client = app.test_client()
    login(operator_client, "operador.carnes")
    login(owner_client)

    with app.app_context():
        operator = db.session.scalar(
            db.select(Usuario).where(Usuario.usuario == "operador.carnes")
        )
        operator_id = operator.id
        operator_version = operator.versao

    temporary_password = "Redefinida!456X"
    response = owner_client.post(
        f"/usuarios/{operator_id}/redefinir-senha",
        data={
            "versao": str(operator_version),
            "senha_temporaria": temporary_password,
            "confirmar_senha": temporary_password,
        },
    )
    assert response.status_code == 302
    invalidated = operator_client.get("/empresa/1/estoque")
    assert invalidated.status_code == 302
    assert invalidated.headers["Location"].endswith("/auth/login")

    with app.app_context():
        audit = db.session.scalar(
            db.select(Auditoria).where(Auditoria.acao == "senha_redefinida")
        )
        assert audit is not None
        assert temporary_password not in str(audit.detalhes)


def test_only_owner_accesses_audit_and_filters_records(client):
    login(client)
    page = client.get("/auditoria?acao=login_sucesso")
    assert page.status_code == 200
    assert "Auditoria" in page.text
    assert "Login realizado" in page.text
    assert "Falha de login" not in page.text


def test_username_is_case_normalized_and_duplicate_is_rejected(app, client):
    login(client)
    first = client.post(
        "/usuarios/novo",
        data={
            "nome": "Operador Normalizado",
            "usuario": "Operador.Novo",
            "papel": "operador",
            "empresas": ["1"],
            "senha_temporaria": "Temporaria!456X",
            "confirmar_senha": "Temporaria!456X",
        },
    )
    assert first.status_code == 302
    duplicate = client.post(
        "/usuarios/novo",
        data={
            "nome": "Outro Operador",
            "usuario": "operador.novo",
            "papel": "operador",
            "empresas": ["1"],
            "senha_temporaria": "OutraSenha!456X",
            "confirmar_senha": "OutraSenha!456X",
        },
    )
    assert duplicate.status_code == 200
    assert "já está em uso" in duplicate.text
    with app.app_context():
        assert db.session.scalar(
            db.select(db.func.count(Usuario.id)).where(Usuario.usuario == "operador.novo")
        ) == 1


def test_last_owner_and_self_deactivation_are_blocked(app, client):
    login(client)
    with app.app_context():
        owner = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        owner_id = owner.id
        owner_version = owner.versao

    response = client.post(
        f"/usuarios/{owner_id}/status",
        data={"versao": str(owner_version)},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "não pode desativar a própria conta" in response.text
    with app.app_context():
        assert db.session.get(Usuario, owner_id).ativo is True


def test_owner_edits_operator_scope_and_invalidates_operator_session(app):
    owner_client = app.test_client()
    operator_client = app.test_client()
    login(owner_client)
    login(operator_client, "operador.carnes")
    with app.app_context():
        operator = db.session.scalar(
            db.select(Usuario).where(Usuario.usuario == "operador.carnes")
        )
        operator_id = operator.id
        version = operator.versao

    response = owner_client.post(
        f"/usuarios/{operator_id}/editar",
        data={
            "versao": str(version),
            "nome": "Caixa Atualizado",
            "usuario": "caixa.atualizado",
            "papel": "operador",
            "empresas": ["1", "2"],
        },
    )
    assert response.status_code == 302
    assert operator_client.get("/empresa/1/estoque").status_code == 302

    with app.app_context():
        operator = db.session.get(Usuario, operator_id)
        assert operator.nome == "Caixa Atualizado"
        assert operator.usuario == "caixa.atualizado"
        assert operator.empresas_ids == {1, 2}
        audit = db.session.scalar(
            db.select(Auditoria).where(Auditoria.acao == "usuario_editado")
        )
        assert audit.detalhes["antes"]["usuario"] == "operador.carnes"
        assert audit.detalhes["depois"]["empresas_ids"] == [1, 2]
        assert audit.detalhes["sessoes_invalidadas"] is True


def test_stale_user_edit_is_rejected(app, client):
    login(client)
    with app.app_context():
        operator = db.session.scalar(
            db.select(Usuario).where(Usuario.usuario == "operador.carnes")
        )
        operator_id = operator.id

    response = client.post(
        f"/usuarios/{operator_id}/editar",
        data={
            "versao": "999",
            "nome": "Nome Indevido",
            "usuario": "operador.carnes",
            "papel": "operador",
            "empresas": ["1"],
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "alterado em outra sessão" in response.text
    with app.app_context():
        assert db.session.get(Usuario, operator_id).nome == "Operador Carnes"


def test_administrator_creates_deactivates_reactivates_and_unlocks_operator(app, client):
    login(client, "admin.carnes")
    created = client.post(
        "/usuarios/novo",
        data={
            "nome": "Caixa Gerenciado",
            "usuario": "caixa.gerenciado",
            "papel": "operador",
            "empresas": ["1"],
            "senha_temporaria": "Temporaria!456X",
            "confirmar_senha": "Temporaria!456X",
        },
    )
    assert created.status_code == 302
    with app.app_context():
        operator = db.session.scalar(
            db.select(Usuario).where(Usuario.usuario == "caixa.gerenciado")
        )
        operator_id = operator.id
        operator.bloqueado_ate = utcnow() + timedelta(minutes=15)
        operator.versao += 1
        db.session.commit()
        version = operator.versao

    unlocked = client.post(
        f"/usuarios/{operator_id}/desbloquear", data={"versao": str(version)}
    )
    assert unlocked.status_code == 302
    with app.app_context():
        operator = db.session.get(Usuario, operator_id)
        assert operator.bloqueado_ate is None
        version = operator.versao

    deactivated = client.post(
        f"/usuarios/{operator_id}/status", data={"versao": str(version)}
    )
    assert deactivated.status_code == 302
    with app.app_context():
        operator = db.session.get(Usuario, operator_id)
        assert operator.ativo is False
        version = operator.versao

    reactivated = client.post(
        f"/usuarios/{operator_id}/status", data={"versao": str(version)}
    )
    assert reactivated.status_code == 302
    with app.app_context():
        operator = db.session.get(Usuario, operator_id)
        assert operator.ativo is True
        actions = set(db.session.scalars(db.select(Auditoria.acao)))
        assert {"usuario_desbloqueado", "usuario_desativado", "usuario_ativado"} <= actions


def test_owner_cannot_change_own_role(app, client):
    login(client)
    with app.app_context():
        owner = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        owner_id = owner.id
        version = owner.versao
    response = client.post(
        f"/usuarios/{owner_id}/editar",
        data={
            "versao": str(version),
            "nome": "Proprietário Teste",
            "usuario": "dono",
            "papel": "administrador",
            "empresas": ["1"],
        },
    )
    assert response.status_code == 200
    assert "não pode alterar o próprio perfil" in response.text
    assert "último proprietário ativo" in response.text


def test_owner_can_create_and_deactivate_backup_owner(app, client):
    login(client)
    created = client.post(
        "/usuarios/novo",
        data={
            "nome": "Dona Reserva",
            "usuario": "dona.reserva",
            "papel": "proprietario",
            "senha_temporaria": "Temporaria!456X",
            "confirmar_senha": "Temporaria!456X",
        },
    )
    assert created.status_code == 302
    with app.app_context():
        backup = db.session.scalar(
            db.select(Usuario).where(Usuario.usuario == "dona.reserva")
        )
        assert backup.empresas_ids == set()
        backup_id = backup.id
        version = backup.versao
    response = client.post(
        f"/usuarios/{backup_id}/status", data={"versao": str(version)}
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Usuario, backup_id).ativo is False


def test_password_change_rejects_wrong_same_and_weak_password(client):
    login(client)
    wrong = client.post(
        "/auth/alterar-senha",
        data={
            "senha_atual": "Errada!123456",
            "nova_senha": "NovaSegura!456X",
            "confirmar_senha": "NovaSegura!456X",
        },
    )
    assert wrong.status_code == 200
    assert "senha atual está incorreta" in wrong.text

    same = client.post(
        "/auth/alterar-senha",
        data={
            "senha_atual": "SenhaForte!123",
            "nova_senha": "SenhaForte!123",
            "confirmar_senha": "SenhaForte!123",
        },
    )
    assert same.status_code == 200
    assert "diferente da senha atual" in same.text

    weak = client.post(
        "/auth/alterar-senha",
        data={
            "senha_atual": "SenhaForte!123",
            "nova_senha": "senhafraca12",
            "confirmar_senha": "senhafraca12",
        },
    )
    assert weak.status_code == 200
    assert "letra maiúscula" in weak.text


def test_audit_filters_dates_user_company_and_invalid_values(app, client):
    login(client)
    product_id = app.config["TEST_PRODUCT_ID"]
    client.post(
        f"/empresa/1/estoque/{product_id}/movimentar",
        data={
            "operacao": "inventario",
            "quantidade": "1.500",
            "motivo": "Auditoria de filtro",
        },
    )
    with app.app_context():
        owner = db.session.scalar(db.select(Usuario).where(Usuario.usuario == "dono"))
        owner_id = owner.id
    today = utcnow().astimezone(ZoneInfo("America/Sao_Paulo")).date().isoformat()
    filtered = client.get(
        f"/auditoria?data_inicio={today}&data_fim={today}"
        f"&usuario_id={owner_id}&empresa_id=1&acao=estoque_movimentado"
    )
    assert filtered.status_code == 200
    assert "Estoque movimentado" in filtered.text
    assert "Auditoria de filtro" not in filtered.text

    invalid = client.get("/auditoria?data_inicio=invalida&empresa_id=999")
    assert invalid.status_code == 200
    assert "data inicial é inválida" in invalid.text
    assert "Nenhum registro encontrado" in invalid.text
