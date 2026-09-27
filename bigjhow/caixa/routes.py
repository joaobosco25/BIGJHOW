from __future__ import annotations

import uuid

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import Empresa, TurnoCaixa, Venda
from ..security import acesso_empresa_obrigatorio, papel_obrigatorio
from ..models import DocumentoFiscal, EstabelecimentoFiscal
from ..services.fiscal.emissao import EmissaoErro, cancelar_documento, emitir_com_seguranca
from ..services.caixa import (
    CaixaConflito,
    CaixaErro,
    adicionar_item,
    atualizar_item,
    cancelar_venda,
    descartar_venda,
    finalizar_venda,
    iniciar_venda,
    remover_item,
)
from .forms import (
    AcaoVendaForm,
    AtualizarItemForm,
    CancelarVendaForm,
    FinalizarVendaForm,
    IniciarVendaForm,
    LeituraCodigoForm,
)


bp = Blueprint("caixa", __name__, url_prefix="/empresa/<int:empresa_id>/caixa")


def _empresa(empresa_id: int) -> Empresa:
    empresa = db.session.get(Empresa, empresa_id)
    if empresa is None or not empresa.ativa:
        abort(404)
    return empresa


def _consulta_venda(empresa_id: int):
    return (
        db.select(Venda)
        .options(
            selectinload(Venda.itens),
            selectinload(Venda.pagamentos),
            selectinload(Venda.operador),
            selectinload(Venda.finalizado_por),
            selectinload(Venda.cancelado_por),
            selectinload(Venda.turno).selectinload(TurnoCaixa.terminal),
        )
        .where(Venda.empresa_id == empresa_id)
    )


def _venda_autorizada(empresa_id: int, venda_id: uuid.UUID) -> Venda:
    venda = db.session.scalar(
        _consulta_venda(empresa_id).where(Venda.id == venda_id)
    )
    if venda is None:
        abort(404)
    if current_user.papel == "operador" and venda.operador_id != current_user.id:
        abort(404)
    return venda


def _versao(form) -> int:
    try:
        versao = int(form.versao.data)
    except (TypeError, ValueError):
        abort(400)
    if versao < 1:
        abort(400)
    return versao


def _redirecionar(empresa_id: int, *, etapa: str | None = None):
    argumentos = {"empresa_id": empresa_id}
    if etapa == "pagamento":
        argumentos["etapa"] = etapa
    return redirect(url_for("operacao.painel", **argumentos))


@bp.get("")
@login_required
@acesso_empresa_obrigatorio
def painel(empresa_id: int):
    return redirect(url_for("operacao.painel", empresa_id=empresa_id))


@bp.get("/historico")
@login_required
@acesso_empresa_obrigatorio
def historico(empresa_id: int):
    return redirect(url_for("operacao.historico", empresa_id=empresa_id))


@bp.post("/iniciar")
@login_required
@acesso_empresa_obrigatorio
def iniciar(empresa_id: int):
    form = IniciarVendaForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        venda = iniciar_venda(empresa_id=empresa_id, usuario_id=current_user.id)
    except CaixaErro as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash(f"Atendimento #{venda.numero_formatado} iniciado.", "sucesso")
    return _redirecionar(empresa_id)


@bp.post("/<uuid:venda_id>/ler")
@login_required
@acesso_empresa_obrigatorio
def ler_codigo(empresa_id: int, venda_id: uuid.UUID):
    _venda_autorizada(empresa_id, venda_id)
    form = LeituraCodigoForm(prefix="leitura")
    if not form.validate_on_submit():
        flash("Informe um código válido.", "erro")
        return _redirecionar(empresa_id)
    try:
        adicionar_item(
            empresa_id=empresa_id,
            venda_id=venda_id,
            versao=_versao(form),
            codigo_digitado=form.codigo.data,
            quantidade_digitada=form.quantidade.data,
            usuario_id=current_user.id,
        )
    except (CaixaErro, CaixaConflito) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash("Produto adicionado ao atendimento.", "sucesso")
    return _redirecionar(empresa_id)


@bp.post("/<uuid:venda_id>/itens/<uuid:item_id>/atualizar")
@login_required
@acesso_empresa_obrigatorio
def alterar_item(
    empresa_id: int, venda_id: uuid.UUID, item_id: uuid.UUID
):
    _venda_autorizada(empresa_id, venda_id)
    form = AtualizarItemForm(prefix=f"item-{item_id}")
    if not form.validate_on_submit():
        flash("Informe uma quantidade válida.", "erro")
        return _redirecionar(empresa_id)
    try:
        atualizar_item(
            empresa_id=empresa_id,
            venda_id=venda_id,
            item_id=item_id,
            versao=_versao(form),
            quantidade_digitada=form.quantidade.data,
            usuario_id=current_user.id,
        )
    except (CaixaErro, CaixaConflito) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash("Quantidade atualizada.", "sucesso")
    return _redirecionar(empresa_id)


@bp.post("/<uuid:venda_id>/itens/<uuid:item_id>/remover")
@login_required
@acesso_empresa_obrigatorio
def excluir_item(
    empresa_id: int, venda_id: uuid.UUID, item_id: uuid.UUID
):
    _venda_autorizada(empresa_id, venda_id)
    form = AcaoVendaForm(prefix=f"remover-{item_id}")
    if not form.validate_on_submit():
        abort(400)
    try:
        remover_item(
            empresa_id=empresa_id,
            venda_id=venda_id,
            item_id=item_id,
            versao=_versao(form),
            usuario_id=current_user.id,
        )
    except (CaixaErro, CaixaConflito) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash("Item removido.", "sucesso")
    return _redirecionar(empresa_id)


@bp.post("/<uuid:venda_id>/finalizar")
@login_required
@acesso_empresa_obrigatorio
def finalizar(empresa_id: int, venda_id: uuid.UUID):
    _venda_autorizada(empresa_id, venda_id)
    form = FinalizarVendaForm(prefix="finalizar")
    if not form.validate_on_submit():
        flash("Revise os valores do pagamento antes de finalizar.", "erro")
        return _redirecionar(empresa_id, etapa="pagamento")
    pagamentos = {
        "dinheiro": (form.pagamento_dinheiro.data, form.dinheiro_recebido.data),
        "pix": (form.pagamento_pix.data, None),
        "debito": (form.pagamento_debito.data, None),
        "credito": (form.pagamento_credito.data, None),
    }
    try:
        venda, criada = finalizar_venda(
            empresa_id=empresa_id,
            venda_id=venda_id,
            versao=_versao(form),
            pagamentos_digitados=pagamentos,
            usuario_id=current_user.id,
        )
    except (CaixaErro, CaixaConflito) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
        return _redirecionar(empresa_id, etapa="pagamento")
    flash(
        "Venda finalizada e ticket gerado."
        if criada
        else "A venda já estava finalizada; nenhum estoque foi baixado novamente.",
        "sucesso" if criada else "aviso",
    )

    # A venda já está gravada e o estoque já baixou. A emissão fiscal roda em
    # seguida, em transação própria: se a SEFAZ estiver fora do ar, o operador
    # é avisado e a venda continua válida — o caixa nunca trava por causa disso.
    documento, aviso = emitir_com_seguranca(
        empresa_id=empresa_id, venda_id=venda.id, usuario_id=current_user.id
    )
    if aviso:
        flash(aviso, "aviso")
    if documento is not None and documento.status == "autorizado":
        return redirect(
            url_for(
                "fiscal.danfe", empresa_id=empresa_id, documento_id=documento.id
            )
        )
    return redirect(
        url_for("caixa.ticket", empresa_id=empresa_id, venda_id=venda.id)
    )


@bp.post("/<uuid:venda_id>/descartar")
@login_required
@acesso_empresa_obrigatorio
def descartar(empresa_id: int, venda_id: uuid.UUID):
    _venda_autorizada(empresa_id, venda_id)
    form = AcaoVendaForm(prefix="descartar")
    if not form.validate_on_submit():
        abort(400)
    try:
        _, alterada = descartar_venda(
            empresa_id=empresa_id,
            venda_id=venda_id,
            versao=_versao(form),
            usuario_id=current_user.id,
        )
    except (CaixaErro, CaixaConflito) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash(
            "Atendimento descartado sem movimentar estoque."
            if alterada
            else "O atendimento já estava descartado.",
            "sucesso" if alterada else "aviso",
        )
    return _redirecionar(empresa_id)


@bp.get("/<uuid:venda_id>")
@login_required
@acesso_empresa_obrigatorio
def detalhe(empresa_id: int, venda_id: uuid.UUID):
    empresa = _empresa(empresa_id)
    venda = _venda_autorizada(empresa_id, venda_id)
    cancelar_form = None
    if current_user.papel in {"proprietario", "administrador"} and venda.status == "finalizada":
        cancelar_form = CancelarVendaForm(
            prefix="cancelar", versao=str(venda.versao)
        )
    return render_template(
        "caixa/detalhe.html",
        empresa=empresa,
        venda=venda,
        cancelar_form=cancelar_form,
    )


@bp.get("/<uuid:venda_id>/ticket")
@login_required
@acesso_empresa_obrigatorio
def ticket(empresa_id: int, venda_id: uuid.UUID):
    empresa = _empresa(empresa_id)
    venda = _venda_autorizada(empresa_id, venda_id)
    if venda.status not in {"finalizada", "cancelada"}:
        abort(404)
    # O comprovante do caixa e o documento fiscal são coisas diferentes. Dizer
    # aqui o que aconteceu com a emissão evita o operador sair achando que saiu
    # nota quando não saiu.
    documento = db.session.scalar(
        db.select(DocumentoFiscal).where(DocumentoFiscal.venda_id == venda.id)
    )
    estabelecimento = db.session.get(EstabelecimentoFiscal, empresa_id)
    return render_template(
        "caixa/ticket.html",
        empresa=empresa,
        venda=venda,
        documento=documento,
        emissao_ativa=bool(estabelecimento and estabelecimento.emissao_ativa),
        iniciar_form=IniciarVendaForm(),
    )


@bp.post("/<uuid:venda_id>/cancelar")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def cancelar(empresa_id: int, venda_id: uuid.UUID):
    venda = _venda_autorizada(empresa_id, venda_id)
    form = CancelarVendaForm(prefix="cancelar")
    if not form.validate_on_submit():
        flash("Informe um motivo válido para cancelar a venda.", "erro")
        return redirect(
            url_for("caixa.detalhe", empresa_id=empresa_id, venda_id=venda_id)
        )
    try:
        venda, alterada = cancelar_venda(
            empresa_id=empresa_id,
            venda_id=venda_id,
            versao=_versao(form),
            motivo=form.motivo.data,
            usuario_id=current_user.id,
        )
    except (CaixaErro, CaixaConflito) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash(
            "Venda cancelada e estoque devolvido."
            if alterada
            else "A venda já estava cancelada; o estoque não foi devolvido novamente.",
            "sucesso" if alterada else "aviso",
        )
        if alterada:
            _cancelar_documento_da_venda(empresa_id, venda, form.motivo.data)
    return redirect(
        url_for("caixa.detalhe", empresa_id=empresa_id, venda_id=venda.id)
    )


def _cancelar_documento_da_venda(empresa_id: int, venda, motivo: str) -> None:
    """Leva o cancelamento até a SEFAZ quando a venda tinha documento fiscal.

    Cancelar a venda no sistema sem cancelar o documento deixaria uma nota
    autorizada sem venda por trás — divergência que aparece na apuração. Se a
    SEFAZ recusar (o prazo da NFC-e é curto), o operador é avisado para tratar
    por devolução, e o cancelamento da venda continua valendo.
    """
    documento = db.session.scalar(
        db.select(DocumentoFiscal).where(DocumentoFiscal.venda_id == venda.id)
    )
    if documento is None or documento.status != "autorizado":
        return
    justificativa = f"Cancelamento da venda {venda.numero_formatado}: {motivo}"
    try:
        cancelar_documento(
            empresa_id=empresa_id,
            documento_id=documento.id,
            justificativa=justificativa[:255],
            usuario_id=current_user.id,
        )
    except (EmissaoErro, ValueError) as exc:
        db.session.rollback()
        flash(
            "A venda foi cancelada, mas o documento fiscal continua autorizado: "
            f"{exc} Trate por devolução ou nota de ajuste.",
            "erro",
        )
    else:
        flash("Documento fiscal cancelado na SEFAZ.", "sucesso")
