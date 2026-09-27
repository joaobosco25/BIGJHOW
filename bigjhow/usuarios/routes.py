from __future__ import annotations

import uuid

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import Empresa, Usuario, UsuarioEmpresa, utcnow
from ..security import papel_obrigatorio
from ..services.auditoria import registrar
from ..services.credenciais import (
    normalizar_usuario,
    validar_senha_forte,
    validar_usuario_novo,
)
from .forms import (
    AcaoUsuarioForm,
    CadastroUsuarioForm,
    EdicaoUsuarioForm,
    RedefinirSenhaForm,
)


bp = Blueprint("usuarios", __name__, url_prefix="/usuarios")
_PAPEIS_PROPRIETARIO = [
    ("proprietario", "Proprietário — acesso completo"),
    ("administrador", "Administrador — estoque e operadores autorizados"),
    ("operador", "Operador — consulta e futuro caixa/bip"),
]
_PAPEIS_ADMINISTRADOR = [("operador", "Operador — consulta e futuro caixa/bip")]


def _usuario_alvo(usuario_id: uuid.UUID, *, bloquear: bool = False) -> Usuario:
    query = (
        db.select(Usuario)
        .where(Usuario.id == usuario_id)
        .options(selectinload(Usuario.empresas).selectinload(UsuarioEmpresa.empresa))
    )
    if bloquear:
        query = query.with_for_update().execution_options(populate_existing=True)
    usuario = db.session.scalar(query)
    if usuario is None:
        abort(404)
    return usuario


def _ids_empresas_autorizadas() -> set[int]:
    if current_user.papel == "proprietario":
        return set(db.session.scalars(db.select(Empresa.id).where(Empresa.ativa.is_(True))))
    return current_user.empresas_ids


def _pode_gerenciar(alvo: Usuario) -> bool:
    if current_user.papel == "proprietario":
        return True
    if current_user.papel != "administrador" or alvo.papel != "operador":
        return False
    empresas_alvo = alvo.empresas_ids
    return bool(empresas_alvo) and empresas_alvo.issubset(current_user.empresas_ids)


def _exigir_gerenciamento(alvo: Usuario) -> None:
    if not _pode_gerenciar(alvo):
        abort(403)


def _configurar_formulario(form) -> list[Empresa]:
    ids = _ids_empresas_autorizadas()
    empresas = db.session.scalars(
        db.select(Empresa)
        .where(Empresa.ativa.is_(True), Empresa.id.in_(ids))
        .order_by(Empresa.nome)
    ).all()
    form.empresas.choices = [(empresa.id, empresa.nome) for empresa in empresas]
    form.papel.choices = (
        _PAPEIS_PROPRIETARIO
        if current_user.papel == "proprietario"
        else _PAPEIS_ADMINISTRADOR
    )
    return empresas


def _validar_dados_usuario(form, *, alvo: Usuario | None = None) -> tuple[str, str, set[int]] | None:
    nome = " ".join(form.nome.data.split())
    usuario = normalizar_usuario(form.usuario.data)
    empresas_ids = set(form.empresas.data or [])

    if alvo is None or usuario != alvo.usuario:
        form.usuario.errors.extend(validar_usuario_novo(usuario))
    if len(nome) < 3 or len(nome) > 120:
        form.nome.errors.append("O nome precisa ter de 3 a 120 caracteres.")

    ids_permitidos = _ids_empresas_autorizadas()
    if not empresas_ids.issubset(ids_permitidos):
        abort(403)
    if form.papel.data != "proprietario" and not empresas_ids:
        form.empresas.errors.append("Selecione pelo menos uma empresa autorizada.")
    if form.papel.data == "proprietario":
        empresas_ids = set()

    consulta_existente = db.select(Usuario).where(Usuario.usuario == usuario)
    if alvo is not None:
        consulta_existente = consulta_existente.where(Usuario.id != alvo.id)
    existente = db.session.scalar(consulta_existente)
    if existente:
        form.usuario.errors.append("Este nome de usuário já está em uso.")

    if form.nome.errors or form.usuario.errors or form.empresas.errors:
        return None
    return nome, usuario, empresas_ids


def _substituir_empresas(alvo: Usuario, empresas_ids: set[int]) -> None:
    alvo.empresas = [UsuarioEmpresa(empresa_id=empresa_id) for empresa_id in sorted(empresas_ids)]


def _bloquear_ultimo_proprietario(alvo: Usuario, novo_papel: str, novo_ativo: bool) -> bool:
    if alvo.papel != "proprietario" or not alvo.ativo:
        return False
    if novo_papel == "proprietario" and novo_ativo:
        return False
    proprietarios = db.session.scalars(
        db.select(Usuario)
        .where(Usuario.papel == "proprietario", Usuario.ativo.is_(True))
        .with_for_update()
    ).all()
    return len(proprietarios) <= 1


def _versao_enviada() -> int:
    try:
        versao = int(request.form.get("versao", ""))
    except (TypeError, ValueError):
        abort(400)
    if versao < 1:
        abort(400)
    return versao


@bp.get("")
@login_required
@papel_obrigatorio("proprietario", "administrador")
def lista():
    busca = request.args.get("q", "").strip()[:120]
    situacao = request.args.get("situacao", "todos")
    if situacao not in {"todos", "ativos", "inativos", "bloqueados"}:
        situacao = "todos"

    query = db.select(Usuario).options(
        selectinload(Usuario.empresas).selectinload(UsuarioEmpresa.empresa)
    )
    if current_user.papel == "administrador":
        ids = current_user.empresas_ids
        vinculados = db.select(UsuarioEmpresa.usuario_id).where(
            UsuarioEmpresa.empresa_id.in_(ids)
        )
        fora_do_escopo = db.select(UsuarioEmpresa.usuario_id).where(
            UsuarioEmpresa.empresa_id.not_in(ids)
        )
        query = query.where(
            Usuario.papel == "operador",
            Usuario.id.in_(vinculados),
            Usuario.id.not_in(fora_do_escopo),
        )

    if situacao == "ativos":
        query = query.where(Usuario.ativo.is_(True))
    elif situacao == "inativos":
        query = query.where(Usuario.ativo.is_(False))
    elif situacao == "bloqueados":
        query = query.where(Usuario.bloqueado_ate > utcnow())

    if busca:
        termo = f"%{busca}%"
        query = query.where(or_(Usuario.nome.ilike(termo), Usuario.usuario.ilike(termo)))

    ordem_papel = db.case(
        (Usuario.papel == "proprietario", 1),
        (Usuario.papel == "administrador", 2),
        else_=3,
    )
    query = query.order_by(ordem_papel, Usuario.nome)
    pagina = request.args.get("page", 1, type=int)
    usuarios = db.paginate(query, page=max(pagina, 1), per_page=30, error_out=False)

    return render_template(
        "usuarios/lista.html",
        usuarios=usuarios,
        busca=busca,
        situacao=situacao,
        acao_form=AcaoUsuarioForm(),
    )


@bp.route("/novo", methods=["GET", "POST"])
@login_required
@papel_obrigatorio("proprietario", "administrador")
def novo():
    form = CadastroUsuarioForm()
    _configurar_formulario(form)
    if form.validate_on_submit():
        dados = _validar_dados_usuario(form)
        erros_senha = validar_senha_forte(
            form.senha_temporaria.data,
            usuario=normalizar_usuario(form.usuario.data),
            nome=form.nome.data,
        )
        form.senha_temporaria.errors.extend(erros_senha)
        if dados is not None and not erros_senha:
            nome, usuario, empresas_ids = dados
            alvo = Usuario(
                nome=nome,
                usuario=usuario,
                papel=form.papel.data,
                ativo=True,
                troca_senha_obrigatoria=True,
            )
            alvo.set_senha(form.senha_temporaria.data)
            _substituir_empresas(alvo, empresas_ids)
            db.session.add(alvo)
            try:
                db.session.flush()
                registrar(
                    "usuario_criado",
                    "usuario",
                    alvo.id,
                    detalhes={
                        "usuario": alvo.usuario,
                        "papel": alvo.papel,
                        "empresas_ids": sorted(empresas_ids),
                        "troca_senha_obrigatoria": True,
                    },
                )
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                form.usuario.errors.append("Este nome de usuário já está em uso.")
            else:
                flash("Usuário criado. A senha temporária deverá ser trocada no primeiro acesso.", "sucesso")
                return redirect(url_for("usuarios.lista"))
    return render_template("usuarios/formulario.html", form=form, alvo=None)


@bp.route("/<uuid:usuario_id>/editar", methods=["GET", "POST"])
@login_required
@papel_obrigatorio("proprietario", "administrador")
def editar(usuario_id: uuid.UUID):
    alvo = _usuario_alvo(usuario_id)
    _exigir_gerenciamento(alvo)
    form = EdicaoUsuarioForm(obj=alvo)
    _configurar_formulario(form)
    if request.method == "GET":
        form.versao.data = str(alvo.versao)
        form.empresas.data = sorted(alvo.empresas_ids)

    if form.validate_on_submit():
        alvo = _usuario_alvo(usuario_id, bloquear=True)
        _exigir_gerenciamento(alvo)
        try:
            versao = int(form.versao.data)
        except (TypeError, ValueError):
            abort(400)
        if versao != alvo.versao:
            flash("Este usuário foi alterado em outra sessão. Recarregue e tente novamente.", "erro")
            return redirect(url_for("usuarios.editar", usuario_id=alvo.id))

        dados = _validar_dados_usuario(form, alvo=alvo)
        if alvo.id == current_user.id and form.papel.data != alvo.papel:
            form.papel.errors.append("Você não pode alterar o próprio perfil.")
            dados = None
        if _bloquear_ultimo_proprietario(alvo, form.papel.data, alvo.ativo):
            form.papel.errors.append("O último proprietário ativo não pode perder esse perfil.")
            dados = None

        if dados is not None:
            nome, usuario, empresas_ids = dados
            antes = {
                "nome": alvo.nome,
                "usuario": alvo.usuario,
                "papel": alvo.papel,
                "empresas_ids": sorted(alvo.empresas_ids),
            }
            seguranca_alterada = (
                alvo.usuario != usuario
                or alvo.papel != form.papel.data
                or alvo.empresas_ids != empresas_ids
            )
            alvo.nome = nome
            alvo.usuario = usuario
            alvo.papel = form.papel.data
            _substituir_empresas(alvo, empresas_ids)
            alvo.versao += 1
            if seguranca_alterada:
                alvo.sessao_versao += 1
            registrar(
                "usuario_editado",
                "usuario",
                alvo.id,
                detalhes={
                    "antes": antes,
                    "depois": {
                        "nome": alvo.nome,
                        "usuario": alvo.usuario,
                        "papel": alvo.papel,
                        "empresas_ids": sorted(empresas_ids),
                    },
                    "sessoes_invalidadas": seguranca_alterada,
                },
            )
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                form.usuario.errors.append("Este nome de usuário já está em uso.")
            else:
                if alvo.id == current_user.id and seguranca_alterada:
                    flash("Dados atualizados. Entre novamente para aplicar a alteração de segurança.", "sucesso")
                else:
                    flash("Usuário atualizado.", "sucesso")
                return redirect(url_for("usuarios.lista"))

    return render_template("usuarios/formulario.html", form=form, alvo=alvo)


@bp.route("/<uuid:usuario_id>/redefinir-senha", methods=["GET", "POST"])
@login_required
@papel_obrigatorio("proprietario", "administrador")
def redefinir_senha(usuario_id: uuid.UUID):
    alvo = _usuario_alvo(usuario_id)
    _exigir_gerenciamento(alvo)
    if alvo.id == current_user.id:
        flash("Use a opção Alterar minha senha para modificar a própria credencial.", "aviso")
        return redirect(url_for("auth.trocar_senha"))

    form = RedefinirSenhaForm()
    if request.method == "GET":
        form.versao.data = str(alvo.versao)
    if form.validate_on_submit():
        alvo = _usuario_alvo(usuario_id, bloquear=True)
        _exigir_gerenciamento(alvo)
        try:
            versao = int(form.versao.data)
        except (TypeError, ValueError):
            abort(400)
        if versao != alvo.versao:
            flash("Este usuário foi alterado em outra sessão. Recarregue e tente novamente.", "erro")
            return redirect(url_for("usuarios.redefinir_senha", usuario_id=alvo.id))

        erros = validar_senha_forte(
            form.senha_temporaria.data,
            usuario=alvo.usuario,
            nome=alvo.nome,
        )
        if alvo.verificar_senha(form.senha_temporaria.data):
            erros.append("A senha temporária precisa ser diferente da senha atual.")
        form.senha_temporaria.errors.extend(erros)
        if not erros:
            alvo.set_senha(form.senha_temporaria.data)
            alvo.troca_senha_obrigatoria = True
            alvo.tentativas_falhas = 0
            alvo.bloqueado_ate = None
            alvo.sessao_versao += 1
            alvo.versao += 1
            registrar(
                "senha_redefinida",
                "usuario",
                alvo.id,
                detalhes={
                    "troca_senha_obrigatoria": True,
                    "sessoes_anteriores_invalidadas": True,
                },
            )
            db.session.commit()
            flash("Senha temporária definida e sessões anteriores encerradas.", "sucesso")
            return redirect(url_for("usuarios.lista"))
    return render_template("usuarios/redefinir_senha.html", form=form, alvo=alvo)


@bp.post("/<uuid:usuario_id>/status")
@login_required
@papel_obrigatorio("proprietario", "administrador")
def alterar_status(usuario_id: uuid.UUID):
    form = AcaoUsuarioForm()
    if not form.validate_on_submit():
        abort(400)
    versao = _versao_enviada()
    alvo = _usuario_alvo(usuario_id, bloquear=True)
    _exigir_gerenciamento(alvo)
    if versao != alvo.versao:
        flash("Este usuário foi alterado em outra sessão. Recarregue e tente novamente.", "erro")
        return redirect(url_for("usuarios.lista"))
    if alvo.id == current_user.id:
        flash("Você não pode desativar a própria conta.", "erro")
        return redirect(url_for("usuarios.lista"))

    novo_status = not alvo.ativo
    if _bloquear_ultimo_proprietario(alvo, alvo.papel, novo_status):
        flash("O último proprietário ativo não pode ser desativado.", "erro")
        return redirect(url_for("usuarios.lista"))

    alvo.ativo = novo_status
    alvo.tentativas_falhas = 0
    alvo.bloqueado_ate = None
    alvo.sessao_versao += 1
    alvo.versao += 1
    registrar(
        "usuario_ativado" if novo_status else "usuario_desativado",
        "usuario",
        alvo.id,
        detalhes={"usuario": alvo.usuario, "ativo": novo_status},
    )
    db.session.commit()
    flash("Usuário ativado." if novo_status else "Usuário desativado e sessões encerradas.", "sucesso")
    return redirect(url_for("usuarios.lista"))


@bp.post("/<uuid:usuario_id>/desbloquear")
@login_required
@papel_obrigatorio("proprietario", "administrador")
def desbloquear(usuario_id: uuid.UUID):
    form = AcaoUsuarioForm()
    if not form.validate_on_submit():
        abort(400)
    versao = _versao_enviada()
    alvo = _usuario_alvo(usuario_id, bloquear=True)
    _exigir_gerenciamento(alvo)
    if versao != alvo.versao:
        flash("Este usuário foi alterado em outra sessão. Recarregue e tente novamente.", "erro")
        return redirect(url_for("usuarios.lista"))
    if not alvo.bloqueio_ativo():
        flash("A conta não está bloqueada neste momento.", "aviso")
        return redirect(url_for("usuarios.lista"))

    alvo.bloqueado_ate = None
    alvo.tentativas_falhas = 0
    alvo.sessao_versao += 1
    alvo.versao += 1
    registrar("usuario_desbloqueado", "usuario", alvo.id, detalhes={"usuario": alvo.usuario})
    db.session.commit()
    flash("Conta desbloqueada.", "sucesso")
    return redirect(url_for("usuarios.lista"))
