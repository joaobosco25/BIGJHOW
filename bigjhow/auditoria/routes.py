from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, render_template, request
from flask_login import login_required
from sqlalchemy import false
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import Auditoria, Empresa, Usuario
from ..security import papel_obrigatorio


bp = Blueprint("auditoria", __name__, url_prefix="/auditoria")
ROTULOS_ACOES = {
    "codigo_produto_ativado": "Código de produto ativado",
    "codigo_produto_criado": "Código de produto criado",
    "codigo_produto_desativado": "Código de produto desativado",
    "estoque_movimentado": "Estoque movimentado",
    "inventario_lote_cancelado": "Inventário em lote cancelado",
    "inventario_lote_criado": "Inventário em lote criado",
    "inventario_lote_finalizado": "Inventário em lote finalizado",
    "inventario_lote_planilha_importada": "Planilha de inventário importada",
    "inventario_lote_salvo": "Rascunho de inventário salvo",
    "login_falhou": "Falha de login",
    "login_sucesso": "Login realizado",
    "logout": "Sessão encerrada",
    "produto_arquivado": "Produto arquivado",
    "produto_criado": "Produto criado",
    "produto_editado": "Produto editado",
    "produto_restaurado": "Produto restaurado",
    "senha_alterada": "Senha alterada",
    "senha_redefinida": "Senha redefinida",
    "troca_senha_falhou": "Falha ao trocar senha",
    "usuario_ativado": "Usuário ativado",
    "usuario_criado": "Usuário criado",
    "usuario_desativado": "Usuário desativado",
    "usuario_desbloqueado": "Usuário desbloqueado",
    "usuario_editado": "Usuário editado",
    "venda_cancelada": "Venda cancelada",
    "venda_descartada": "Atendimento descartado",
    "venda_finalizada": "Venda finalizada",
    "venda_iniciada": "Atendimento iniciado",
    "venda_item_adicionado": "Item adicionado à venda",
    "venda_item_atualizado": "Item da venda atualizado",
    "venda_item_removido": "Item removido da venda",
}


def _data_iso(valor: str) -> date | None:
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


@bp.get("")
@login_required
@papel_obrigatorio("proprietario")
def lista():
    data_inicio_texto = request.args.get("data_inicio", "")[:10]
    data_fim_texto = request.args.get("data_fim", "")[:10]
    acao = request.args.get("acao", "").strip()[:80]
    usuario_texto = request.args.get("usuario_id", "").strip()[:36]
    empresa_id = request.args.get("empresa_id", type=int)
    erro_filtro = None

    data_inicio = _data_iso(data_inicio_texto)
    data_fim = _data_iso(data_fim_texto)
    if data_inicio_texto and data_inicio is None:
        erro_filtro = "A data inicial é inválida."
    elif data_fim_texto and data_fim is None:
        erro_filtro = "A data final é inválida."
    elif data_inicio and data_fim and data_fim < data_inicio:
        erro_filtro = "A data final não pode ser anterior à data inicial."

    usuario_id = None
    if usuario_texto:
        try:
            usuario_id = uuid.UUID(usuario_texto)
        except ValueError:
            erro_filtro = erro_filtro or "O usuário selecionado é inválido."

    empresas = db.session.scalars(db.select(Empresa).order_by(Empresa.nome)).all()
    usuarios = db.session.scalars(db.select(Usuario).order_by(Usuario.nome)).all()
    acoes = db.session.scalars(
        db.select(Auditoria.acao).distinct().order_by(Auditoria.acao)
    ).all()
    if acao and acao not in acoes:
        erro_filtro = erro_filtro or "A ação selecionada é inválida."

    ids_empresas = {empresa.id for empresa in empresas}
    if empresa_id is not None and empresa_id not in ids_empresas:
        erro_filtro = erro_filtro or "A empresa selecionada é inválida."
    ids_usuarios = {usuario.id for usuario in usuarios}
    if usuario_id is not None and usuario_id not in ids_usuarios:
        erro_filtro = erro_filtro or "O usuário selecionado é inválido."

    query = (
        db.select(Auditoria)
        .options(selectinload(Auditoria.usuario), selectinload(Auditoria.empresa))
        .order_by(Auditoria.criado_em.desc(), Auditoria.id.desc())
    )
    if not erro_filtro:
        zona = ZoneInfo(current_app.config["APP_TIMEZONE"])
        if data_inicio:
            inicio_local = datetime.combine(data_inicio, time.min, tzinfo=zona)
            query = query.where(Auditoria.criado_em >= inicio_local.astimezone(timezone.utc))
        if data_fim:
            fim_exclusivo_local = datetime.combine(
                data_fim + timedelta(days=1), time.min, tzinfo=zona
            )
            query = query.where(Auditoria.criado_em < fim_exclusivo_local.astimezone(timezone.utc))
        if acao:
            query = query.where(Auditoria.acao == acao)
        if usuario_id:
            query = query.where(Auditoria.usuario_id == usuario_id)
        if empresa_id is not None:
            query = query.where(Auditoria.empresa_id == empresa_id)
    else:
        query = query.where(false())

    pagina = request.args.get("page", 1, type=int)
    registros = db.paginate(query, page=max(pagina, 1), per_page=50, error_out=False)
    return render_template(
        "auditoria/lista.html",
        registros=registros,
        empresas=empresas,
        usuarios_filtro=usuarios,
        acoes=acoes,
        rotulos_acoes=ROTULOS_ACOES,
        data_inicio=data_inicio_texto,
        data_fim=data_fim_texto,
        acao=acao,
        usuario_id=usuario_texto,
        empresa_id=empresa_id,
        erro_filtro=erro_filtro,
    )
