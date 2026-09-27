from __future__ import annotations

import uuid
from decimal import Decimal, ROUND_HALF_UP

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from ..extensions import db
from ..models import CodigoProduto, Empresa, MovimentoEstoque, Produto, SaldoEstoque, utcnow
from ..security import acesso_empresa_obrigatorio, papel_obrigatorio
from ..services.auditoria import registrar
from ..services.mgv6_cliente import ErroMGV6
from ..services.mgv6_integracao import (
    VinculoMGV6Invalido,
    atualizar_vinculo,
    confirmar_balanca,
    escrita_ativa,
    integracao_ativa,
    sincronizar_preco,
    validar_vinculo,
)
from ..services.planilha_inventario import (
    PlanilhaInventarioInvalida,
    normalizar_quantidade,
)
from .forms import AcaoForm, AjusteEstoqueForm, ProdutoForm


bp = Blueprint("estoque", __name__, url_prefix="/empresa/<int:empresa_id>/estoque")
grupo_bp = Blueprint("grupo", __name__, url_prefix="/grupo")
_QTD = Decimal("0.001")
_PRECO = Decimal("0.01")


def _empresa(empresa_id: int) -> Empresa:
    empresa = db.session.get(Empresa, empresa_id)
    if empresa is None or not empresa.ativa:
        abort(404)
    return empresa


def _produto(empresa_id: int, produto_id: uuid.UUID, *, ativo: bool | None = None) -> Produto:
    query = db.select(Produto).where(
        Produto.id == produto_id,
        Produto.empresa_id == empresa_id,
    )
    if ativo is not None:
        query = query.where(Produto.ativo.is_(ativo))
    produto = db.session.scalar(query)
    if produto is None:
        abort(404)
    return produto


def _produto_bloqueado(
    empresa_id: int, produto_id: uuid.UUID, *, ativo: bool | None = None
) -> Produto:
    query = db.select(Produto).where(
        Produto.id == produto_id,
        Produto.empresa_id == empresa_id,
    )
    if ativo is not None:
        query = query.where(Produto.ativo.is_(ativo))
    produto = db.session.scalar(
        query.with_for_update().execution_options(populate_existing=True)
    )
    if produto is None:
        abort(404)
    return produto


def _motivos_sem_duplicidade(motivos: list[str]) -> list[str]:
    return [m for m in motivos if m not in {"codigo_duplicado", "nome_duplicado"}]


def _reavaliar_duplicidades(empresa_id: int, codigo: str | None, nome: str | None) -> None:
    grupos = []
    if codigo:
        grupos.append((Produto.codigo_externo == codigo, "codigo_duplicado"))
    if nome:
        grupos.append((func.lower(Produto.nome) == nome.lower(), "nome_duplicado"))

    for criterio, motivo in grupos:
        itens = db.session.scalars(
            db.select(Produto).where(
                Produto.empresa_id == empresa_id,
                Produto.ativo.is_(True),
                criterio,
            )
        ).all()
        duplicado = len(itens) > 1
        for item in itens:
            motivos = list(item.motivos_revisao or [])
            if duplicado and motivo not in motivos:
                motivos.append(motivo)
            elif not duplicado and motivo in motivos:
                motivos.remove(motivo)
            item.motivos_revisao = motivos
            item.revisao_pendente = bool(motivos)


@bp.get("")
@login_required
@acesso_empresa_obrigatorio
def lista(empresa_id: int):
    empresa = _empresa(empresa_id)
    busca = request.args.get("q", "").strip()[:120]
    situacao = request.args.get("situacao", "ativos")
    if situacao not in {"ativos", "revisao", "inventario", "sem_codigo", "arquivados"}:
        situacao = "ativos"

    sem_codigo_ativo = ~db.select(CodigoProduto.id).where(
        CodigoProduto.produto_id == Produto.id,
        CodigoProduto.empresa_id == empresa_id,
        CodigoProduto.ativo.is_(True),
    ).exists()

    query = db.select(Produto).join(SaldoEstoque).where(Produto.empresa_id == empresa_id)
    if situacao == "arquivados":
        query = query.where(Produto.ativo.is_(False))
    else:
        query = query.where(Produto.ativo.is_(True))
        if situacao == "revisao":
            query = query.where(Produto.revisao_pendente.is_(True))
        elif situacao == "inventario":
            query = query.where(SaldoEstoque.aguardando_inventario.is_(True))
        elif situacao == "sem_codigo":
            query = query.where(sem_codigo_ativo)

    if busca:
        term = f"%{busca}%"
        query = query.where(
            or_(
                Produto.nome.ilike(term),
                Produto.codigo_externo.ilike(term),
                Produto.sku_interno.ilike(term),
                Produto.categoria.ilike(term),
            )
        )

    query = query.order_by(Produto.nome.asc(), Produto.origem_linha.asc().nullslast())
    page = request.args.get("page", 1, type=int)
    produtos = db.paginate(query, page=max(page, 1), per_page=50, error_out=False)

    base = db.select(func.count(Produto.id)).where(
        Produto.empresa_id == empresa_id, Produto.ativo.is_(True)
    )
    indicadores = {
        "total": db.session.scalar(base) or 0,
        "revisao": db.session.scalar(base.where(Produto.revisao_pendente.is_(True))) or 0,
        "inventario": db.session.scalar(
            db.select(func.count(Produto.id))
            .join(SaldoEstoque)
            .where(
                Produto.empresa_id == empresa_id,
                Produto.ativo.is_(True),
                SaldoEstoque.aguardando_inventario.is_(True),
            )
        )
        or 0,
        "sem_codigo": db.session.scalar(base.where(sem_codigo_ativo)) or 0,
        "arquivados": db.session.scalar(
            db.select(func.count(Produto.id)).where(
                Produto.empresa_id == empresa_id, Produto.ativo.is_(False)
            )
        )
        or 0,
    }
    return render_template(
        "estoque/lista.html",
        empresa=empresa,
        produtos=produtos,
        indicadores=indicadores,
        busca=busca,
        situacao=situacao,
        acao_form=AcaoForm(),
    )


@bp.route("/novo", methods=["GET", "POST"])
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def novo(empresa_id: int):
    empresa = _empresa(empresa_id)
    form = ProdutoForm()
    if form.validate_on_submit():
        produto = Produto(
            empresa_id=empresa_id,
            sku_interno=f"MAN-{uuid.uuid4().hex[:12].upper()}",
            codigo_externo=(form.codigo_externo.data or "").strip() or None,
            nome=form.nome.data.strip(),
            categoria=form.categoria.data.strip(),
            unidade=form.unidade.data,
            pesado=form.pesado.data,
            preco_venda=form.preco_venda.data.quantize(_PRECO, rounding=ROUND_HALF_UP),
            revisao_pendente=form.revisao_pendente.data,
            motivos_revisao=["revisao_manual"] if form.revisao_pendente.data else [],
            criado_por_id=current_user.id,
        )
        produto.saldo = SaldoEstoque(quantidade=Decimal("0"), aguardando_inventario=True)
        db.session.add(produto)
        db.session.flush()
        try:
            atualizar_vinculo(
                produto,
                form.mgv6_codigo_item.data if integracao_ativa() else None,
                usuario_id=current_user.id,
            )
        except VinculoMGV6Invalido as exc:
            db.session.rollback()
            form.mgv6_codigo_item.errors.append(str(exc))
            return render_template(
                "estoque/formulario.html",
                empresa=empresa,
                form=form,
                produto=None,
                mgv6_ativo=integracao_ativa(),
                mgv6_escrita=escrita_ativa(),
                mgv_acao_form=AcaoForm(),
            )

        _reavaliar_duplicidades(empresa_id, produto.codigo_externo, produto.nome)
        registrar(
            "produto_criado",
            "produto",
            produto.id,
            empresa_id,
            {"sku_interno": produto.sku_interno, "nome": produto.nome},
        )
        db.session.commit()

        if integracao_ativa() and produto.mgv6 is not None:
            try:
                validar_vinculo(produto, usuario_id=current_user.id)
            except (ErroMGV6, VinculoMGV6Invalido) as exc:
                flash(f"Produto cadastrado, mas o MGV6 não foi validado: {exc}", "aviso")
            else:
                flash("Produto cadastrado e vínculo MGV6 validado.", "sucesso")
        else:
            flash("Produto cadastrado. O saldo aguarda inventário.", "sucesso")
        return redirect(url_for("estoque.lista", empresa_id=empresa_id))

    return render_template(
        "estoque/formulario.html",
        empresa=empresa,
        form=form,
        produto=None,
        mgv6_ativo=integracao_ativa(),
        mgv6_escrita=escrita_ativa(),
        mgv_acao_form=AcaoForm(),
    )


@bp.route("/<uuid:produto_id>/editar", methods=["GET", "POST"])
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def editar(empresa_id: int, produto_id: uuid.UUID):
    empresa = _empresa(empresa_id)
    produto = _produto(empresa_id, produto_id, ativo=True)
    form = ProdutoForm(obj=produto)
    if request.method == "GET":
        form.versao.data = str(produto.versao)
        form.mgv6_codigo_item.data = produto.mgv6.codigo_item if produto.mgv6 else ""

    if form.validate_on_submit():
        produto = _produto_bloqueado(empresa_id, produto_id, ativo=True)
        try:
            versao_enviada = int(form.versao.data)
        except (TypeError, ValueError):
            abort(400)
        if versao_enviada != produto.versao:
            flash("Este produto foi alterado em outra sessão. Recarregue e tente novamente.", "erro")
            return redirect(url_for("estoque.editar", empresa_id=empresa_id, produto_id=produto.id))

        preco_anterior = Decimal(produto.preco_venda).quantize(_PRECO)
        preco_novo = form.preco_venda.data.quantize(_PRECO, rounding=ROUND_HALF_UP)
        preco_alterado = preco_novo != preco_anterior
        nome_alterado = form.nome.data.strip() != produto.nome
        codigo_mgv_anterior = produto.mgv6.codigo_item if produto.mgv6 else ""
        codigo_mgv_novo = (form.mgv6_codigo_item.data or "").strip()

        if integracao_ativa() and bool(form.pesado.data) and preco_alterado and not codigo_mgv_novo:
            form.mgv6_codigo_item.errors.append(
                "Produtos vendidos por peso precisam do código mestre do MGV6 antes de alterar o preço."
            )
            db.session.rollback()
            produto = _produto(empresa_id, produto_id, ativo=True)
            return render_template(
                "estoque/formulario.html",
                empresa=empresa,
                form=form,
                produto=produto,
                mgv6_ativo=True,
                mgv6_escrita=escrita_ativa(),
                mgv_acao_form=AcaoForm(),
            )

        if integracao_ativa() and bool(form.pesado.data) and preco_alterado and not escrita_ativa():
            form.preco_venda.errors.append(
                "A integração MGV6 está em modo somente leitura. Ative MGV6_WRITE_ENABLED "
                "somente quando nenhum sistema legado estiver importando no MGV6."
            )
            db.session.rollback()
            produto = _produto(empresa_id, produto_id, ativo=True)
            return render_template(
                "estoque/formulario.html",
                empresa=empresa,
                form=form,
                produto=produto,
                mgv6_ativo=True,
                mgv6_escrita=False,
                mgv_acao_form=AcaoForm(),
            )

        codigo_anterior = produto.codigo_externo
        nome_anterior = produto.nome
        antes = {
            "codigo_externo": produto.codigo_externo,
            "nome": produto.nome,
            "categoria": produto.categoria,
            "unidade": produto.unidade,
            "pesado": produto.pesado,
            "preco_venda": str(produto.preco_venda),
            "revisao_pendente": produto.revisao_pendente,
            "mgv6_codigo_item": codigo_mgv_anterior or None,
        }
        produto.codigo_externo = (form.codigo_externo.data or "").strip() or None
        produto.nome = form.nome.data.strip()
        produto.categoria = form.categoria.data.strip()
        produto.unidade = form.unidade.data
        produto.pesado = form.pesado.data
        produto.preco_venda = preco_novo
        produto.motivos_revisao = _motivos_sem_duplicidade(list(produto.motivos_revisao or []))
        if form.revisao_pendente.data:
            if not produto.motivos_revisao:
                produto.motivos_revisao = ["revisao_manual"]
        else:
            produto.motivos_revisao = []
        produto.revisao_pendente = bool(produto.motivos_revisao)
        produto.versao += 1

        try:
            atualizar_vinculo(
                produto,
                codigo_mgv_novo if integracao_ativa() else codigo_mgv_anterior,
                usuario_id=current_user.id,
            )
        except VinculoMGV6Invalido as exc:
            db.session.rollback()
            form.mgv6_codigo_item.errors.append(str(exc))
            produto = _produto(empresa_id, produto_id, ativo=True)
            return render_template(
                "estoque/formulario.html",
                empresa=empresa,
                form=form,
                produto=produto,
                mgv6_ativo=integracao_ativa(),
                mgv6_escrita=escrita_ativa(),
                mgv_acao_form=AcaoForm(),
            )

        _reavaliar_duplicidades(empresa_id, codigo_anterior, nome_anterior)
        _reavaliar_duplicidades(empresa_id, produto.codigo_externo, produto.nome)
        registrar(
            "produto_editado",
            "produto",
            produto.id,
            empresa_id,
            {
                "antes": antes,
                "depois": {
                    "codigo_externo": produto.codigo_externo,
                    "nome": produto.nome,
                    "categoria": produto.categoria,
                    "unidade": produto.unidade,
                    "pesado": produto.pesado,
                    "preco_venda": str(produto.preco_venda),
                    "revisao_pendente": produto.revisao_pendente,
                    "mgv6_codigo_item": produto.mgv6.codigo_item if produto.mgv6 else None,
                },
            },
        )
        db.session.commit()

        vinculo_alterado = codigo_mgv_novo != codigo_mgv_anterior
        if integracao_ativa() and produto.mgv6 is not None:
            try:
                if preco_alterado:
                    sincronizar_preco(produto, usuario_id=current_user.id)
                    flash(
                        "Produto atualizado e preço confirmado no MGV6. Confira a balança antes de liberar novas etiquetas.",
                        "sucesso",
                    )
                elif vinculo_alterado or nome_alterado or produto.mgv6.status == "pendente":
                    validar_vinculo(produto, usuario_id=current_user.id)
                    flash("Produto atualizado e vínculo MGV6 validado.", "sucesso")
                else:
                    flash("Produto atualizado.", "sucesso")
            except (ErroMGV6, VinculoMGV6Invalido) as exc:
                flash(
                    f"Produto atualizado, mas a sincronização MGV6 ficou bloqueada: {exc}",
                    "aviso",
                )
        else:
            flash("Produto atualizado.", "sucesso")
        return redirect(url_for("estoque.editar", empresa_id=empresa_id, produto_id=produto.id))

    return render_template(
        "estoque/formulario.html",
        empresa=empresa,
        form=form,
        produto=produto,
        mgv6_ativo=integracao_ativa(),
        mgv6_escrita=escrita_ativa(),
        mgv_acao_form=AcaoForm(),
    )


@bp.post("/<uuid:produto_id>/mgv6/validar")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def mgv6_validar(empresa_id: int, produto_id: uuid.UUID):
    if not AcaoForm().validate_on_submit():
        abort(400)
    produto = _produto(empresa_id, produto_id, ativo=True)
    try:
        validar_vinculo(produto, usuario_id=current_user.id)
    except (ErroMGV6, VinculoMGV6Invalido) as exc:
        flash(str(exc), "erro")
    else:
        flash("Vínculo com o MGV6 validado.", "sucesso")
    return redirect(url_for("estoque.editar", empresa_id=empresa_id, produto_id=produto.id))


@bp.post("/<uuid:produto_id>/mgv6/sincronizar")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def mgv6_sincronizar(empresa_id: int, produto_id: uuid.UUID):
    if not AcaoForm().validate_on_submit():
        abort(400)
    produto = _produto(empresa_id, produto_id, ativo=True)
    try:
        sincronizar_preco(produto, usuario_id=current_user.id)
    except (ErroMGV6, VinculoMGV6Invalido) as exc:
        flash(str(exc), "erro")
    else:
        flash("Preço confirmado no MGV6; confira a Prix antes de confirmar a balança.", "sucesso")
    return redirect(url_for("estoque.editar", empresa_id=empresa_id, produto_id=produto.id))


@bp.post("/<uuid:produto_id>/mgv6/confirmar-balanca")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def mgv6_confirmar_balanca(empresa_id: int, produto_id: uuid.UUID):
    if not AcaoForm().validate_on_submit():
        abort(400)
    produto = _produto(empresa_id, produto_id, ativo=True)
    try:
        confirmar_balanca(produto, usuario_id=current_user.id)
    except (ErroMGV6, VinculoMGV6Invalido) as exc:
        flash(str(exc), "erro")
    else:
        flash("Preço conferido fisicamente na balança. Etiquetas liberadas.", "sucesso")
    return redirect(url_for("estoque.editar", empresa_id=empresa_id, produto_id=produto.id))


@bp.route("/<uuid:produto_id>/movimentar", methods=["GET", "POST"])
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def movimentar(empresa_id: int, produto_id: uuid.UUID):
    empresa = _empresa(empresa_id)
    produto = _produto(empresa_id, produto_id, ativo=True)
    form = AjusteEstoqueForm()

    quantidade = None
    formulario_valido = form.validate_on_submit()
    if formulario_valido:
        try:
            quantidade = normalizar_quantidade(form.quantidade.data)
        except PlanilhaInventarioInvalida:
            form.quantidade.errors.append(
                "Use números sem separador de milhar e até três casas decimais. "
                "Exemplo: 2,500 para dois quilos e meio."
            )
        else:
            if quantidade is None:
                form.quantidade.errors.append("Informe a quantidade.")

    if formulario_valido and quantidade is not None:
        produto = _produto_bloqueado(empresa_id, produto_id, ativo=True)
        saldo = db.session.scalar(
            db.select(SaldoEstoque)
            .where(SaldoEstoque.produto_id == produto.id)
            .with_for_update()
        )
        if saldo is None:
            abort(409)
        anterior = Decimal(saldo.quantidade).quantize(_QTD)
        quantidade = quantidade.quantize(_QTD, rounding=ROUND_HALF_UP)

        if form.operacao.data == "inventario":
            final = quantidade
            variacao = final - anterior
        elif form.operacao.data == "entrada":
            if quantidade <= 0:
                form.quantidade.errors.append("A entrada deve ser maior que zero.")
                return render_template("estoque/movimentar.html", empresa=empresa, produto=produto, form=form)
            variacao = quantidade
            final = anterior + quantidade
        else:
            if quantidade <= 0:
                form.quantidade.errors.append("A saída deve ser maior que zero.")
                return render_template("estoque/movimentar.html", empresa=empresa, produto=produto, form=form)
            variacao = -quantidade
            final = anterior - quantidade

        if final < 0:
            form.quantidade.errors.append("A operação resultaria em saldo negativo.")
            return render_template("estoque/movimentar.html", empresa=empresa, produto=produto, form=form)

        saldo.quantidade = final
        saldo.aguardando_inventario = False
        movimento = MovimentoEstoque(
            empresa_id=empresa_id,
            produto_id=produto.id,
            usuario_id=current_user.id,
            tipo=form.operacao.data,
            quantidade_anterior=anterior,
            variacao=variacao,
            quantidade_final=final,
            motivo=form.motivo.data.strip(),
        )
        db.session.add(movimento)
        db.session.flush()
        registrar(
            "estoque_movimentado",
            "movimento_estoque",
            movimento.id,
            empresa_id,
            {
                "produto_id": str(produto.id),
                "tipo": movimento.tipo,
                "quantidade_anterior": str(anterior),
                "variacao": str(variacao),
                "quantidade_final": str(final),
            },
        )
        db.session.commit()
        flash("Movimentação registrada no histórico.", "sucesso")
        return redirect(url_for("estoque.lista", empresa_id=empresa_id))

    return render_template("estoque/movimentar.html", empresa=empresa, produto=produto, form=form)


@bp.post("/<uuid:produto_id>/arquivar")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def arquivar(empresa_id: int, produto_id: uuid.UUID):
    form = AcaoForm()
    if not form.validate_on_submit():
        abort(400)
    produto = _produto_bloqueado(empresa_id, produto_id, ativo=True)
    saldo = db.session.scalar(
        db.select(SaldoEstoque)
        .where(SaldoEstoque.produto_id == produto.id)
        .with_for_update()
    )
    if saldo is None:
        abort(409)
    if Decimal(saldo.quantidade) != 0:
        flash("O produto só pode ser arquivado quando o saldo for zero.", "erro")
        return redirect(url_for("estoque.lista", empresa_id=empresa_id))
    produto.ativo = False
    produto.arquivado_em = utcnow()
    produto.arquivado_por_id = current_user.id
    produto.versao += 1
    _reavaliar_duplicidades(empresa_id, produto.codigo_externo, produto.nome)
    registrar("produto_arquivado", "produto", produto.id, empresa_id, {"nome": produto.nome})
    db.session.commit()
    flash("Produto arquivado. O histórico foi preservado.", "sucesso")
    return redirect(url_for("estoque.lista", empresa_id=empresa_id))


@bp.post("/<uuid:produto_id>/restaurar")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def restaurar(empresa_id: int, produto_id: uuid.UUID):
    form = AcaoForm()
    if not form.validate_on_submit():
        abort(400)
    produto = _produto_bloqueado(empresa_id, produto_id, ativo=False)
    produto.ativo = True
    produto.arquivado_em = None
    produto.arquivado_por_id = None
    produto.versao += 1
    _reavaliar_duplicidades(empresa_id, produto.codigo_externo, produto.nome)
    registrar("produto_restaurado", "produto", produto.id, empresa_id, {"nome": produto.nome})
    db.session.commit()
    flash("Produto restaurado.", "sucesso")
    return redirect(url_for("estoque.lista", empresa_id=empresa_id, situacao="arquivados"))


@grupo_bp.get("/estoque")
@login_required
def visao_geral():
    if current_user.papel == "proprietario":
        empresas = db.session.scalars(
            db.select(Empresa).where(Empresa.ativa.is_(True)).order_by(Empresa.id)
        ).all()
    else:
        ids = [vinculo.empresa_id for vinculo in current_user.empresas]
        empresas = db.session.scalars(
            db.select(Empresa)
            .where(Empresa.ativa.is_(True), Empresa.id.in_(ids))
            .order_by(Empresa.id)
        ).all()
    if not empresas:
        abort(403)

    cards = []
    for empresa in empresas:
        total = db.session.scalar(
            db.select(func.count(Produto.id)).where(
                Produto.empresa_id == empresa.id, Produto.ativo.is_(True)
            )
        ) or 0
        revisao = db.session.scalar(
            db.select(func.count(Produto.id)).where(
                Produto.empresa_id == empresa.id,
                Produto.ativo.is_(True),
                Produto.revisao_pendente.is_(True),
            )
        ) or 0
        inventario = db.session.scalar(
            db.select(func.count(Produto.id))
            .join(SaldoEstoque)
            .where(
                Produto.empresa_id == empresa.id,
                Produto.ativo.is_(True),
                SaldoEstoque.aguardando_inventario.is_(True),
            )
        ) or 0
        cards.append({"empresa": empresa, "total": total, "revisao": revisao, "inventario": inventario})

    return render_template("estoque/grupo.html", cards=cards)
