from __future__ import annotations

import uuid

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import CodigoProduto, Empresa, Produto
from ..security import acesso_empresa_obrigatorio, papel_obrigatorio
from ..services.codigos import (
    CodigoProdutoConflitante,
    CodigoProdutoInvalido,
    alterar_status_codigo,
    cadastrar_codigo,
)
from .forms import AcaoCodigoForm, CodigoProdutoForm


bp = Blueprint(
    "codigos",
    __name__,
    url_prefix="/empresa/<int:empresa_id>/estoque/<uuid:produto_id>/codigos",
)


def _empresa(empresa_id: int) -> Empresa:
    empresa = db.session.get(Empresa, empresa_id)
    if empresa is None or not empresa.ativa:
        abort(404)
    return empresa


def _produto(empresa_id: int, produto_id: uuid.UUID) -> Produto:
    produto = db.session.scalar(
        db.select(Produto).where(
            Produto.id == produto_id,
            Produto.empresa_id == empresa_id,
        )
    )
    if produto is None:
        abort(404)
    return produto


@bp.route("", methods=["GET", "POST"])
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def lista(empresa_id: int, produto_id: uuid.UUID):
    empresa = _empresa(empresa_id)
    produto = _produto(empresa_id, produto_id)
    form = CodigoProdutoForm()
    if form.validate_on_submit():
        try:
            cadastrar_codigo(
                empresa_id=empresa_id,
                produto_id=produto_id,
                valor=form.codigo.data,
                tipo=form.tipo.data,
                principal=bool(form.principal.data),
                usuario_id=current_user.id,
            )
        except (CodigoProdutoInvalido, CodigoProdutoConflitante) as exc:
            db.session.rollback()
            form.codigo.errors.append(str(exc))
        else:
            flash("Código cadastrado e pronto para leitura.", "sucesso")
            return redirect(
                url_for("codigos.lista", empresa_id=empresa_id, produto_id=produto_id)
            )

    codigos = db.session.scalars(
        db.select(CodigoProduto)
        .where(CodigoProduto.produto_id == produto_id)
        .order_by(
            CodigoProduto.ativo.desc(),
            CodigoProduto.principal.desc(),
            CodigoProduto.criado_em,
        )
    ).all()
    formularios_status = {
        codigo.id: AcaoCodigoForm(
            prefix=f"status-{codigo.id}", versao=str(codigo.versao)
        )
        for codigo in codigos
    }
    return render_template(
        "codigos/lista.html",
        empresa=empresa,
        produto=produto,
        codigos=codigos,
        form=form,
        formularios_status=formularios_status,
    )


@bp.post("/<uuid:codigo_id>/status")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def alterar_status(
    empresa_id: int, produto_id: uuid.UUID, codigo_id: uuid.UUID
):
    _produto(empresa_id, produto_id)
    form = AcaoCodigoForm(prefix=f"status-{codigo_id}")
    if not form.validate_on_submit():
        abort(400)
    try:
        versao = int(form.versao.data)
    except (TypeError, ValueError):
        abort(400)
    try:
        codigo = alterar_status_codigo(
            empresa_id=empresa_id,
            produto_id=produto_id,
            codigo_id=codigo_id,
            versao=versao,
            usuario_id=current_user.id,
        )
    except (CodigoProdutoInvalido, CodigoProdutoConflitante) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash("Código ativado." if codigo.ativo else "Código desativado.", "sucesso")
    return redirect(
        url_for("codigos.lista", empresa_id=empresa_id, produto_id=produto_id)
    )
