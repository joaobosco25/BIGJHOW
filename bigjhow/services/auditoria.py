from __future__ import annotations

from flask import has_request_context, request
from flask_login import current_user

from ..extensions import db
from ..models import Auditoria


def registrar(
    acao: str,
    entidade: str,
    entidade_id: object | None = None,
    empresa_id: int | None = None,
    detalhes: dict | None = None,
    usuario_id=None,
) -> Auditoria:
    if usuario_id is None and has_request_context() and current_user.is_authenticated:
        usuario_id = current_user.id

    ip = None
    if has_request_context():
        ip = request.remote_addr

    registro = Auditoria(
        empresa_id=empresa_id,
        usuario_id=usuario_id,
        acao=acao,
        entidade=entidade,
        entidade_id=str(entidade_id) if entidade_id is not None else None,
        detalhes=detalhes or {},
        ip=ip,
    )
    db.session.add(registro)
    return registro
