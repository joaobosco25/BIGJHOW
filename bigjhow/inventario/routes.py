from __future__ import annotations

import uuid
from decimal import Decimal

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..estoque.forms import (
    FinalizarInventarioForm,
    ImportarInventarioForm,
    InventarioVersaoForm,
    NovoInventarioLoteForm,
)
from ..models import (
    Empresa,
    InventarioLote,
    InventarioLoteItem,
    MovimentoEstoque,
    Produto,
    SaldoEstoque,
    utcnow,
)
from ..security import acesso_empresa_obrigatorio, papel_obrigatorio
from ..services.auditoria import registrar
from ..services.planilha_inventario import (
    MAXIMO_BYTES_PLANILHA,
    PlanilhaInventarioInvalida,
    gerar_planilha,
    ler_planilha,
    normalizar_quantidade,
)


bp = Blueprint("inventario", __name__, url_prefix="/empresa/<int:empresa_id>/inventarios")
_QTD = Decimal("0.001")
_POR_PAGINA = 50


def _empresa(empresa_id: int, *, bloquear: bool = False) -> Empresa:
    consulta = db.select(Empresa).where(Empresa.id == empresa_id, Empresa.ativa.is_(True))
    if bloquear:
        consulta = consulta.with_for_update()
    empresa = db.session.scalar(consulta)
    if empresa is None:
        abort(404)
    return empresa


def _inventario(
    empresa_id: int,
    inventario_id: uuid.UUID,
    *,
    bloquear: bool = False,
) -> InventarioLote:
    consulta = db.select(InventarioLote).where(
        InventarioLote.id == inventario_id,
        InventarioLote.empresa_id == empresa_id,
    )
    if bloquear:
        consulta = consulta.with_for_update().execution_options(populate_existing=True)
    inventario = db.session.scalar(consulta)
    if inventario is None:
        abort(404)
    return inventario


def _versao_enviada(valor: str | None) -> int:
    try:
        versao = int(valor or "")
    except (TypeError, ValueError) as exc:
        raise ValueError("Versão inválida.") from exc
    if versao < 1:
        raise ValueError("Versão inválida.")
    return versao


def _resumo(inventario_id: uuid.UUID) -> dict[str, int]:
    total = db.session.scalar(
        db.select(func.count(InventarioLoteItem.id)).where(
            InventarioLoteItem.inventario_id == inventario_id
        )
    ) or 0
    contados = db.session.scalar(
        db.select(func.count(InventarioLoteItem.id)).where(
            InventarioLoteItem.inventario_id == inventario_id,
            InventarioLoteItem.quantidade_contada.is_not(None),
        )
    ) or 0
    return {"total": total, "contados": contados, "pendentes": total - contados}


def _redirecionar_detalhe(empresa_id: int, inventario_id: uuid.UUID):
    argumentos = {
        "empresa_id": empresa_id,
        "inventario_id": inventario_id,
        "page": max(request.form.get("page", type=int) or 1, 1),
        "q": (request.form.get("q") or "").strip()[:120],
        "situacao": request.form.get("situacao") or "todos",
    }
    return redirect(url_for("inventario.detalhe", **argumentos))


@bp.route("", methods=["GET", "POST"])
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def lista(empresa_id: int):
    empresa = _empresa(empresa_id)
    form = NovoInventarioLoteForm()
    if form.validate_on_submit():
        _empresa(empresa_id, bloquear=True)
        existente = db.session.scalar(
            db.select(InventarioLote).where(
                InventarioLote.empresa_id == empresa_id,
                InventarioLote.status == "rascunho",
            )
        )
        if existente is not None:
            db.session.rollback()
            flash("Já existe um inventário em rascunho para esta empresa.", "aviso")
            return redirect(
                url_for(
                    "inventario.detalhe",
                    empresa_id=empresa_id,
                    inventario_id=existente.id,
                )
            )

        registros = db.session.execute(
            db.select(Produto, SaldoEstoque)
            .join(SaldoEstoque, SaldoEstoque.produto_id == Produto.id)
            .where(
                Produto.empresa_id == empresa_id,
                Produto.ativo.is_(True),
                SaldoEstoque.aguardando_inventario.is_(True),
            )
            .order_by(Produto.nome, Produto.id)
        ).all()
        if not registros:
            db.session.rollback()
            flash("Não há produtos aguardando inventário nesta empresa.", "aviso")
            return redirect(url_for("inventario.lista", empresa_id=empresa_id))

        inventario = InventarioLote(
            empresa_id=empresa_id,
            descricao=" ".join(form.descricao.data.split()),
            criado_por_id=current_user.id,
        )
        db.session.add(inventario)
        for produto, saldo in registros:
            inventario.itens.append(
                InventarioLoteItem(
                    produto_id=produto.id,
                    quantidade_referencia=Decimal(saldo.quantidade).quantize(_QTD),
                    aguardando_referencia=saldo.aguardando_inventario,
                    unidade_referencia=produto.unidade,
                    pesado_referencia=produto.pesado,
                )
            )
        try:
            db.session.flush()
            registrar(
                "inventario_lote_criado",
                "inventario_lote",
                inventario.id,
                empresa_id,
                {"descricao": inventario.descricao, "total_itens": len(registros)},
            )
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Outro rascunho foi criado ao mesmo tempo. Recarregue a página.", "erro")
            return redirect(url_for("inventario.lista", empresa_id=empresa_id))
        flash(f"Rascunho criado com {len(registros)} produto(s) pendente(s).", "sucesso")
        return redirect(
            url_for(
                "inventario.detalhe",
                empresa_id=empresa_id,
                inventario_id=inventario.id,
            )
        )

    inventarios = db.session.scalars(
        db.select(InventarioLote)
        .options(selectinload(InventarioLote.criado_por))
        .where(InventarioLote.empresa_id == empresa_id)
        .order_by(InventarioLote.criado_em.desc())
    ).all()
    historico = [{"inventario": item, "resumo": _resumo(item.id)} for item in inventarios]
    aguardando = db.session.scalar(
        db.select(func.count(Produto.id))
        .join(SaldoEstoque, SaldoEstoque.produto_id == Produto.id)
        .where(
            Produto.empresa_id == empresa_id,
            Produto.ativo.is_(True),
            SaldoEstoque.aguardando_inventario.is_(True),
        )
    ) or 0
    return render_template(
        "inventario/lista.html",
        empresa=empresa,
        form=form,
        historico=historico,
        aguardando=aguardando,
    )


@bp.get("/<uuid:inventario_id>")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def detalhe(empresa_id: int, inventario_id: uuid.UUID):
    empresa = _empresa(empresa_id)
    inventario = _inventario(empresa_id, inventario_id)
    busca = request.args.get("q", "").strip()[:120]
    situacao = request.args.get("situacao", "todos")
    if situacao not in {"todos", "contados", "pendentes"}:
        situacao = "todos"

    consulta = (
        db.select(InventarioLoteItem)
        .options(selectinload(InventarioLoteItem.produto))
        .join(Produto, Produto.id == InventarioLoteItem.produto_id)
        .where(InventarioLoteItem.inventario_id == inventario.id)
    )
    if situacao == "contados":
        consulta = consulta.where(InventarioLoteItem.quantidade_contada.is_not(None))
    elif situacao == "pendentes":
        consulta = consulta.where(InventarioLoteItem.quantidade_contada.is_(None))
    if busca:
        termo = f"%{busca}%"
        consulta = consulta.where(
            or_(
                Produto.nome.ilike(termo),
                Produto.codigo_externo.ilike(termo),
                Produto.sku_interno.ilike(termo),
                Produto.categoria.ilike(termo),
            )
        )
    consulta = consulta.order_by(Produto.nome, InventarioLoteItem.id)
    pagina = max(request.args.get("page", 1, type=int), 1)
    itens = db.paginate(consulta, page=pagina, per_page=_POR_PAGINA, error_out=False)
    return render_template(
        "inventario/detalhe.html",
        empresa=empresa,
        inventario=inventario,
        itens=itens,
        resumo=_resumo(inventario.id),
        busca=busca,
        situacao=situacao,
        salvar_form=InventarioVersaoForm(prefix="salvar", versao=str(inventario.versao)),
        importar_form=ImportarInventarioForm(prefix="importar", versao=str(inventario.versao)),
        finalizar_form=FinalizarInventarioForm(prefix="finalizar", versao=str(inventario.versao)),
        cancelar_form=InventarioVersaoForm(prefix="cancelar", versao=str(inventario.versao)),
    )


@bp.post("/<uuid:inventario_id>/salvar")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def salvar(empresa_id: int, inventario_id: uuid.UUID):
    form = InventarioVersaoForm(prefix="salvar")
    if not form.validate_on_submit():
        abort(400)
    campos = [
        (chave.removeprefix("quantidade_"), valor)
        for chave, valor in request.form.items()
        if chave.startswith("quantidade_")
    ]
    if not campos or len(campos) > _POR_PAGINA:
        abort(400)
    try:
        valores = {uuid.UUID(item_id): normalizar_quantidade(valor) for item_id, valor in campos}
        versao = _versao_enviada(form.versao.data)
    except (ValueError, PlanilhaInventarioInvalida):
        flash("Há uma quantidade inválida. Use até três casas decimais e não use negativos.", "erro")
        return _redirecionar_detalhe(empresa_id, inventario_id)
    if len(valores) != len(campos):
        abort(400)

    inventario = _inventario(empresa_id, inventario_id, bloquear=True)
    if inventario.status != "rascunho":
        db.session.rollback()
        flash("Este inventário não aceita mais alterações.", "aviso")
        return _redirecionar_detalhe(empresa_id, inventario_id)
    if inventario.versao != versao:
        db.session.rollback()
        flash("O inventário foi alterado em outra sessão. Recarregue antes de continuar.", "erro")
        return _redirecionar_detalhe(empresa_id, inventario_id)

    itens = db.session.scalars(
        db.select(InventarioLoteItem)
        .where(
            InventarioLoteItem.inventario_id == inventario.id,
            InventarioLoteItem.id.in_(valores),
        )
        .with_for_update()
    ).all()
    if len(itens) != len(valores):
        db.session.rollback()
        abort(400)

    alterados = 0
    agora = utcnow()
    for item in itens:
        quantidade = valores[item.id]
        atual = Decimal(item.quantidade_contada).quantize(_QTD) if item.quantidade_contada is not None else None
        if atual == quantidade:
            continue
        item.quantidade_contada = quantidade
        item.contado_por_id = current_user.id if quantidade is not None else None
        item.contado_em = agora if quantidade is not None else None
        alterados += 1
    if alterados:
        inventario.versao += 1
        registrar(
            "inventario_lote_salvo",
            "inventario_lote",
            inventario.id,
            empresa_id,
            {"itens_alterados": alterados},
        )
        db.session.commit()
        flash(f"Rascunho salvo: {alterados} quantidade(s) alterada(s).", "sucesso")
    else:
        db.session.rollback()
        flash("Nenhuma quantidade foi alterada.", "aviso")
    return redirect(
        url_for(
            "inventario.detalhe",
            empresa_id=empresa_id,
            inventario_id=inventario_id,
            page=max(request.form.get("page", type=int) or 1, 1),
            q=(request.form.get("q") or "").strip()[:120],
            situacao=request.form.get("situacao") or "todos",
        )
    )


@bp.get("/<uuid:inventario_id>/planilha")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def baixar_planilha(empresa_id: int, inventario_id: uuid.UUID):
    empresa = _empresa(empresa_id)
    inventario = _inventario(empresa_id, inventario_id)
    itens = db.session.scalars(
        db.select(InventarioLoteItem)
        .options(selectinload(InventarioLoteItem.produto))
        .join(Produto, Produto.id == InventarioLoteItem.produto_id)
        .where(InventarioLoteItem.inventario_id == inventario.id)
        .order_by(Produto.nome, InventarioLoteItem.id)
    ).all()
    conteudo = gerar_planilha(inventario, itens)
    resposta = Response(
        conteudo,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    nome = f"inventario-{empresa.slug}-{str(inventario.id)[:8]}-v{inventario.versao}.xlsx"
    resposta.headers["Content-Disposition"] = f'attachment; filename="{nome}"'
    resposta.headers["Cache-Control"] = "no-store, max-age=0"
    return resposta


@bp.post("/<uuid:inventario_id>/planilha")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def importar_planilha(empresa_id: int, inventario_id: uuid.UUID):
    form = ImportarInventarioForm(prefix="importar")
    if not form.validate_on_submit():
        flash("Envie somente a planilha XLSX gerada pelo sistema.", "erro")
        return _redirecionar_detalhe(empresa_id, inventario_id)
    inventario_atual = _inventario(empresa_id, inventario_id)
    try:
        versao = _versao_enviada(form.versao.data)
    except ValueError:
        abort(400)
    if inventario_atual.status != "rascunho" or inventario_atual.versao != versao:
        flash("O inventário foi alterado. Recarregue e baixe uma nova planilha.", "erro")
        return _redirecionar_detalhe(empresa_id, inventario_id)

    conteudo = form.arquivo.data.stream.read(MAXIMO_BYTES_PLANILHA + 1)
    ids = set(
        db.session.scalars(
            db.select(InventarioLoteItem.id).where(
                InventarioLoteItem.inventario_id == inventario_id
            )
        )
    )
    try:
        valores = ler_planilha(conteudo, inventario_id, versao, ids)
    except PlanilhaInventarioInvalida as exc:
        flash(str(exc), "erro")
        return _redirecionar_detalhe(empresa_id, inventario_id)

    inventario = _inventario(empresa_id, inventario_id, bloquear=True)
    if inventario.status != "rascunho" or inventario.versao != versao:
        db.session.rollback()
        flash("O inventário mudou durante a importação. Nenhum dado foi salvo.", "erro")
        return _redirecionar_detalhe(empresa_id, inventario_id)
    itens = db.session.scalars(
        db.select(InventarioLoteItem)
        .where(InventarioLoteItem.inventario_id == inventario.id)
        .with_for_update()
    ).all()
    agora = utcnow()
    alterados = 0
    preenchidos = 0
    for item in itens:
        quantidade = valores[item.id]
        atual = Decimal(item.quantidade_contada).quantize(_QTD) if item.quantidade_contada is not None else None
        if quantidade is not None:
            preenchidos += 1
        if atual == quantidade:
            continue
        item.quantidade_contada = quantidade
        item.contado_por_id = current_user.id if quantidade is not None else None
        item.contado_em = agora if quantidade is not None else None
        alterados += 1
    inventario.versao += 1
    registrar(
        "inventario_lote_planilha_importada",
        "inventario_lote",
        inventario.id,
        empresa_id,
        {"itens_alterados": alterados, "itens_preenchidos": preenchidos},
    )
    db.session.commit()
    flash(
        f"Planilha importada: {preenchidos} item(ns) contado(s) e {len(itens) - preenchidos} pendente(s).",
        "sucesso",
    )
    return redirect(url_for("inventario.detalhe", empresa_id=empresa_id, inventario_id=inventario_id))


@bp.post("/<uuid:inventario_id>/finalizar")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def finalizar(empresa_id: int, inventario_id: uuid.UUID):
    form = FinalizarInventarioForm(prefix="finalizar")
    if not form.validate_on_submit():
        flash("Marque a confirmação antes de finalizar o inventário.", "erro")
        return _redirecionar_detalhe(empresa_id, inventario_id)
    try:
        versao = _versao_enviada(form.versao.data)
    except ValueError:
        abort(400)
    inventario = _inventario(empresa_id, inventario_id, bloquear=True)
    if inventario.status != "rascunho":
        db.session.rollback()
        flash("Este inventário já foi encerrado.", "aviso")
        return _redirecionar_detalhe(empresa_id, inventario_id)
    if inventario.versao != versao:
        db.session.rollback()
        flash("O inventário foi alterado em outra sessão. Revise antes de finalizar.", "erro")
        return _redirecionar_detalhe(empresa_id, inventario_id)

    registros = db.session.execute(
        db.select(InventarioLoteItem, Produto, SaldoEstoque)
        .join(Produto, Produto.id == InventarioLoteItem.produto_id)
        .join(SaldoEstoque, SaldoEstoque.produto_id == Produto.id)
        .where(
            InventarioLoteItem.inventario_id == inventario.id,
            InventarioLoteItem.quantidade_contada.is_not(None),
        )
        .order_by(InventarioLoteItem.produto_id)
        .with_for_update()
    ).all()
    if not registros:
        db.session.rollback()
        flash("Preencha ao menos uma quantidade antes de finalizar.", "erro")
        return _redirecionar_detalhe(empresa_id, inventario_id)

    conflitos = []
    for item, produto, saldo in registros:
        saldo_atual = Decimal(saldo.quantidade).quantize(_QTD)
        if (
            produto.empresa_id != empresa_id
            or not produto.ativo
            or produto.unidade != item.unidade_referencia
            or produto.pesado != item.pesado_referencia
            or saldo_atual != Decimal(item.quantidade_referencia).quantize(_QTD)
            or saldo.aguardando_inventario != item.aguardando_referencia
        ):
            conflitos.append(produto.nome)
    if conflitos:
        db.session.rollback()
        nomes = ", ".join(conflitos[:3])
        complemento = "…" if len(conflitos) > 3 else ""
        flash(
            f"A finalização foi bloqueada: {len(conflitos)} produto(s) mudaram desde a criação do rascunho ({nomes}{complemento}). Nenhum saldo foi alterado.",
            "erro",
        )
        return _redirecionar_detalhe(empresa_id, inventario_id)

    agora = utcnow()
    for item, produto, saldo in registros:
        anterior = Decimal(saldo.quantidade).quantize(_QTD)
        final = Decimal(item.quantidade_contada).quantize(_QTD)
        saldo.quantidade = final
        saldo.aguardando_inventario = False
        db.session.add(
            MovimentoEstoque(
                empresa_id=empresa_id,
                produto_id=produto.id,
                usuario_id=current_user.id,
                tipo="inventario",
                quantidade_anterior=anterior,
                variacao=final - anterior,
                quantidade_final=final,
                motivo=f"{inventario.descricao} — inventário em lote {str(inventario.id)[:8]}",
            )
        )
    inventario.status = "finalizado"
    inventario.finalizado_por_id = current_user.id
    inventario.finalizado_em = agora
    inventario.versao += 1
    resumo = _resumo(inventario.id)
    registrar(
        "inventario_lote_finalizado",
        "inventario_lote",
        inventario.id,
        empresa_id,
        {
            "itens_finalizados": len(registros),
            "itens_mantidos_pendentes": resumo["pendentes"],
        },
    )
    db.session.commit()
    flash(
        f"Inventário finalizado: {len(registros)} saldo(s) confirmado(s); {resumo['pendentes']} produto(s) continuam aguardando contagem.",
        "sucesso",
    )
    return redirect(url_for("inventario.detalhe", empresa_id=empresa_id, inventario_id=inventario_id))


@bp.post("/<uuid:inventario_id>/cancelar")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def cancelar(empresa_id: int, inventario_id: uuid.UUID):
    form = InventarioVersaoForm(prefix="cancelar")
    if not form.validate_on_submit():
        abort(400)
    try:
        versao = _versao_enviada(form.versao.data)
    except ValueError:
        abort(400)
    inventario = _inventario(empresa_id, inventario_id, bloquear=True)
    if inventario.status != "rascunho" or inventario.versao != versao:
        db.session.rollback()
        flash("O inventário foi alterado ou já foi encerrado.", "erro")
        return _redirecionar_detalhe(empresa_id, inventario_id)
    inventario.status = "cancelado"
    inventario.cancelado_por_id = current_user.id
    inventario.cancelado_em = utcnow()
    inventario.versao += 1
    registrar(
        "inventario_lote_cancelado",
        "inventario_lote",
        inventario.id,
        empresa_id,
        _resumo(inventario.id),
    )
    db.session.commit()
    flash("Rascunho cancelado. Nenhum saldo foi alterado.", "sucesso")
    return redirect(url_for("inventario.lista", empresa_id=empresa_id))
