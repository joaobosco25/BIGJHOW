from __future__ import annotations

from datetime import timedelta
from urllib.parse import urljoin, urlsplit

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..models import Usuario, utcnow
from ..services.auditoria import registrar
from ..services.credenciais import normalizar_usuario, validar_senha_forte
from .forms import LoginForm, TrocaSenhaForm


bp = Blueprint("auth", __name__, url_prefix="/auth")
_DUMMY_HASH = generate_password_hash("senha-inexistente", method="scrypt")
_MAX_ATTEMPTS = 5
_LOCKOUT_MINUTES = 15


def _safe_next(target: str | None) -> bool:
    if not target:
        return False
    base = urlsplit(request.host_url)
    candidate = urlsplit(urljoin(request.host_url, target))
    return candidate.scheme in {"http", "https"} and candidate.netloc == base.netloc


def _destino_padrao(user: Usuario) -> str:
    if user.papel == "operador" and user.empresas:
        empresa_id = min(vinculo.empresa_id for vinculo in user.empresas)
        return url_for("operacao.painel", empresa_id=empresa_id)
    return url_for("index")


@bp.before_app_request
def validar_estado_da_sessao():
    if not current_user.is_authenticated:
        return None

    if session.get("sessao_versao") != current_user.sessao_versao:
        logout_user()
        session.clear()
        flash("Sua sessão foi encerrada após uma alteração de segurança.", "aviso")
        return redirect(url_for("auth.login"))

    rotas_liberadas = {"auth.trocar_senha", "auth.logout", "static"}
    if current_user.troca_senha_obrigatoria and request.endpoint not in rotas_liberadas:
        flash("Defina uma nova senha antes de continuar.", "aviso")
        return redirect(url_for("auth.trocar_senha"))
    return None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    form = LoginForm()
    if form.validate_on_submit():
        usuario = normalizar_usuario(form.usuario.data)
        user = db.session.scalar(db.select(Usuario).where(Usuario.usuario == usuario))
        now = utcnow()

        if user is None:
            check_password_hash(_DUMMY_HASH, form.senha.data)
            registrar("login_falhou", "usuario", detalhes={"motivo": "credenciais_invalidas"})
            db.session.commit()
        elif user.bloqueio_ativo(now) or not user.ativo:
            check_password_hash(_DUMMY_HASH, form.senha.data)
            registrar(
                "login_falhou",
                "usuario",
                entidade_id=user.id,
                usuario_id=user.id,
                detalhes={"motivo": "indisponivel"},
            )
            db.session.commit()
        elif user.verificar_senha(form.senha.data):
            user.tentativas_falhas = 0
            user.bloqueado_ate = None
            user.ultimo_login_em = now
            registrar("login_sucesso", "usuario", entidade_id=user.id, usuario_id=user.id)
            db.session.commit()
            session.clear()
            login_user(user, remember=False, duration=timedelta(minutes=30), fresh=True)
            session.permanent = True
            session["sessao_versao"] = user.sessao_versao
            next_url = request.args.get("next")
            return redirect(next_url if _safe_next(next_url) else _destino_padrao(user))
        else:
            user.tentativas_falhas += 1
            if user.tentativas_falhas >= _MAX_ATTEMPTS:
                user.bloqueado_ate = now + timedelta(minutes=_LOCKOUT_MINUTES)
                user.tentativas_falhas = 0
            registrar(
                "login_falhou",
                "usuario",
                entidade_id=user.id,
                usuario_id=user.id,
                detalhes={"motivo": "credenciais_invalidas"},
            )
            db.session.commit()

        flash("Credenciais inválidas ou acesso temporariamente indisponível.", "erro")

    return render_template("auth/login.html", form=form)


@bp.route("/alterar-senha", methods=["GET", "POST"])
@login_required
def trocar_senha():
    form = TrocaSenhaForm()
    if form.validate_on_submit():
        if not current_user.verificar_senha(form.senha_atual.data):
            registrar(
                "troca_senha_falhou",
                "usuario",
                entidade_id=current_user.id,
                detalhes={"motivo": "senha_atual_invalida"},
            )
            db.session.commit()
            form.senha_atual.errors.append("A senha atual está incorreta.")
        elif current_user.verificar_senha(form.nova_senha.data):
            form.nova_senha.errors.append("A nova senha precisa ser diferente da senha atual.")
        else:
            erros = validar_senha_forte(
                form.nova_senha.data,
                usuario=current_user.usuario,
                nome=current_user.nome,
            )
            if erros:
                form.nova_senha.errors.extend(erros)
            else:
                current_user.set_senha(form.nova_senha.data)
                current_user.troca_senha_obrigatoria = False
                current_user.tentativas_falhas = 0
                current_user.bloqueado_ate = None
                current_user.sessao_versao += 1
                current_user.versao += 1
                registrar(
                    "senha_alterada",
                    "usuario",
                    entidade_id=current_user.id,
                    detalhes={"sessoes_anteriores_invalidadas": True},
                )
                db.session.commit()
                session["sessao_versao"] = current_user.sessao_versao
                session.permanent = True
                flash("Senha atualizada e demais sessões encerradas.", "sucesso")
                return redirect(_destino_padrao(current_user))

    return render_template(
        "auth/trocar_senha.html",
        form=form,
        obrigatoria=current_user.troca_senha_obrigatoria,
    )


@bp.post("/logout")
@login_required
def logout():
    registrar("logout", "usuario", entidade_id=current_user.id)
    db.session.commit()
    logout_user()
    session.clear()
    flash("Sessão encerrada com segurança.", "sucesso")
    return redirect(url_for("auth.login"))
