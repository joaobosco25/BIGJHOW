from __future__ import annotations

from datetime import timezone
import uuid
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import (
    Empresa,
    Impressora,
    MovimentoCaixa,
    RegraImpressao,
    TerminalCaixa,
    TrabalhoImpressao,
    TurnoCaixa,
    Venda,
)
from ..caixa.forms import (
    AcaoVendaForm,
    AtualizarItemForm,
    FinalizarVendaForm,
    IniciarVendaForm,
    LeituraCodigoForm,
)
from ..security import acesso_empresa_obrigatorio, papel_obrigatorio
from ..services.impressao import (
    ImpressaoConflito,
    ImpressaoErro,
    alterar_status_impressora,
    alterar_status_regra,
    criar_impressora,
    criar_regra,
    criar_reimpressao,
    reprocessar_trabalho,
    simular_falha,
    simular_impressao,
)
from ..services.turnos import (
    TurnoConflito,
    TurnoErro,
    abrir_turno,
    alterar_status_terminal,
    criar_terminal,
    decidir_movimento,
    fechar_turno,
    resumo_turno,
    resumos_turnos,
    solicitar_movimento,
    turno_aberto_do_operador,
)
from .forms import (
    AbrirTurnoForm,
    AcaoVersaoForm,
    DecisaoMovimentoForm,
    FecharTurnoForm,
    ImpressoraForm,
    MovimentoTurnoForm,
    RegraImpressaoForm,
    ReimpressaoForm,
    TerminalForm,
)


bp = Blueprint("operacao", __name__, url_prefix="/empresa/<int:empresa_id>/operacao")


def _empresa(empresa_id: int) -> Empresa:
    empresa = db.session.get(Empresa, empresa_id)
    if empresa is None or not empresa.ativa:
        abort(404)
    return empresa


def _uuid(valor: str) -> uuid.UUID:
    try:
        return uuid.UUID(valor)
    except (TypeError, ValueError):
        abort(400)


def _versao(form) -> int:
    try:
        valor = int(form.versao.data)
    except (TypeError, ValueError):
        abort(400)
    if valor < 1:
        abort(400)
    return valor


def _voltar(empresa_id: int):
    return redirect(url_for("operacao.painel", empresa_id=empresa_id))


def _pode_ver_turno(turno: TurnoCaixa) -> bool:
    return current_user.papel in {"proprietario", "administrador"} or (
        turno.operador_id == current_user.id
    )


def _turno_da_empresa(empresa_id: int, turno_id: uuid.UUID) -> TurnoCaixa:
    turno = db.session.scalar(
        db.select(TurnoCaixa)
        .options(
            selectinload(TurnoCaixa.terminal),
            selectinload(TurnoCaixa.operador),
            selectinload(TurnoCaixa.movimentos),
            selectinload(TurnoCaixa.vendas).selectinload(Venda.itens),
            selectinload(TurnoCaixa.vendas).selectinload(Venda.pagamentos),
        )
        .where(TurnoCaixa.id == turno_id, TurnoCaixa.empresa_id == empresa_id)
    )
    if turno is None:
        abort(404)
    if not _pode_ver_turno(turno):
        abort(404)
    return turno


def _consulta_venda(empresa_id: int):
    return (
        db.select(Venda)
        .options(
            selectinload(Venda.itens),
            selectinload(Venda.pagamentos),
            selectinload(Venda.operador),
            selectinload(Venda.turno).selectinload(TurnoCaixa.terminal),
        )
        .where(Venda.empresa_id == empresa_id)
    )


def _trabalho_da_empresa(empresa_id: int, trabalho_id: uuid.UUID) -> TrabalhoImpressao:
    trabalho = db.session.scalar(
        db.select(TrabalhoImpressao)
        .options(
            selectinload(TrabalhoImpressao.terminal),
            selectinload(TrabalhoImpressao.impressora),
            selectinload(TrabalhoImpressao.solicitado_por),
        )
        .where(
            TrabalhoImpressao.id == trabalho_id,
            TrabalhoImpressao.empresa_id == empresa_id,
        )
    )
    if trabalho is None:
        abort(404)
    if current_user.papel == "operador" and trabalho.solicitado_por_id != current_user.id:
        abort(404)
    return trabalho


@bp.get("")
@login_required
@acesso_empresa_obrigatorio
def painel(empresa_id: int):
    empresa = _empresa(empresa_id)
    turno = turno_aberto_do_operador(
        empresa_id=empresa_id, operador_id=current_user.id
    )
    terminais = db.session.scalars(
        db.select(TerminalCaixa)
        .where(
            TerminalCaixa.empresa_id == empresa_id,
            TerminalCaixa.ativo.is_(True),
        )
        .order_by(TerminalCaixa.codigo)
    ).all()
    abrir_form = AbrirTurnoForm()
    abrir_form.terminal_id.choices = [
        (str(item.id), f"{item.codigo} · {item.nome}") for item in terminais
    ]
    venda = db.session.scalar(
        _consulta_venda(empresa_id).where(
            Venda.operador_id == current_user.id,
            Venda.status == "aberta",
        )
    )
    leitura_form = finalizar_form = descartar_form = None
    formularios_itens = {}
    formularios_remover = {}
    if venda is not None:
        leitura_form = LeituraCodigoForm(prefix="leitura", versao=str(venda.versao))
        finalizar_form = FinalizarVendaForm(
            prefix="finalizar", versao=str(venda.versao)
        )
        descartar_form = AcaoVendaForm(prefix="descartar", versao=str(venda.versao))
        for item in venda.itens:
            formularios_itens[item.id] = AtualizarItemForm(
                prefix=f"item-{item.id}",
                versao=str(venda.versao),
                quantidade=str(item.quantidade),
            )
            formularios_remover[item.id] = AcaoVendaForm(
                prefix=f"remover-{item.id}", versao=str(venda.versao)
            )

    pendentes = 0
    if current_user.papel in {"proprietario", "administrador"}:
        pendentes = db.session.scalar(
            db.select(db.func.count(MovimentoCaixa.id)).where(
                MovimentoCaixa.empresa_id == empresa_id,
                MovimentoCaixa.status == "pendente",
            )
        ) or 0
    return render_template(
        "operacao/painel.html",
        empresa=empresa,
        turno=turno,
        pendentes=pendentes,
        abrir_form=abrir_form,
        venda=venda,
        etapa_inicial=(
            "pagamento"
            if request.args.get("etapa") == "pagamento"
            and venda is not None
            and venda.itens
            else "produtos"
        ),
        iniciar_form=IniciarVendaForm(),
        leitura_form=leitura_form,
        finalizar_form=finalizar_form,
        descartar_form=descartar_form,
        formularios_itens=formularios_itens,
        formularios_remover=formularios_remover,
    )


@bp.get("/historico")
@login_required
@acesso_empresa_obrigatorio
def historico(empresa_id: int):
    empresa = _empresa(empresa_id)
    query = (
        db.select(TurnoCaixa)
        .options(selectinload(TurnoCaixa.terminal), selectinload(TurnoCaixa.operador))
        .where(TurnoCaixa.empresa_id == empresa_id)
    )
    if current_user.papel == "operador":
        query = query.where(TurnoCaixa.operador_id == current_user.id)
    turnos = db.session.scalars(
        query.order_by(TurnoCaixa.aberto_em.desc()).limit(90)
    ).all()
    resumos = resumos_turnos([turno.id for turno in turnos])
    zona = ZoneInfo(current_app.config["APP_TIMEZONE"])
    dias = []
    por_data = {}
    for turno in turnos:
        aberto_em = turno.aberto_em
        if aberto_em.tzinfo is None:
            aberto_em = aberto_em.replace(tzinfo=timezone.utc)
        data = aberto_em.astimezone(zona).date()
        if data not in por_data:
            grupo = {"data": data.strftime("%d/%m/%Y"), "turnos": []}
            por_data[data] = grupo
            dias.append(grupo)
        por_data[data]["turnos"].append(
            {"turno": turno, "resumo": resumos[turno.id]}
        )
    return render_template("operacao/historico.html", empresa=empresa, dias=dias)


@bp.get("/autorizacoes")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def autorizacoes(empresa_id: int):
    empresa = _empresa(empresa_id)
    pendentes = db.session.scalars(
        db.select(MovimentoCaixa)
        .options(
            selectinload(MovimentoCaixa.turno).selectinload(TurnoCaixa.terminal),
            selectinload(MovimentoCaixa.solicitado_por),
        )
        .where(
            MovimentoCaixa.empresa_id == empresa_id,
            MovimentoCaixa.status == "pendente",
        )
        .order_by(MovimentoCaixa.criado_em)
    ).all()
    formularios_decisao = {
        item.id: DecisaoMovimentoForm(
            prefix=f"decisao-{item.id}", versao=str(item.versao)
        )
        for item in pendentes
    }
    return render_template(
        "operacao/autorizacoes.html",
        empresa=empresa,
        pendentes=pendentes,
        formularios_decisao=formularios_decisao,
    )


@bp.post("/turnos/abrir")
@login_required
@acesso_empresa_obrigatorio
def abrir(empresa_id: int):
    terminais = db.session.scalars(
        db.select(TerminalCaixa).where(
            TerminalCaixa.empresa_id == empresa_id, TerminalCaixa.ativo.is_(True)
        )
    ).all()
    form = AbrirTurnoForm()
    form.terminal_id.choices = [(str(item.id), item.nome) for item in terminais]
    if not form.validate_on_submit():
        flash("Revise o terminal e o valor inicial.", "erro")
        return _voltar(empresa_id)
    try:
        abrir_turno(
            empresa_id=empresa_id,
            terminal_id=_uuid(form.terminal_id.data),
            operador_id=current_user.id,
            valor_inicial=form.valor_inicial.data,
        )
    except (TurnoErro, TurnoConflito) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash("Turno aberto. O caixa já pode iniciar atendimentos.", "sucesso")
        return _voltar(empresa_id)
    return _voltar(empresa_id)


@bp.post("/turnos/<uuid:turno_id>/movimentos")
@login_required
@acesso_empresa_obrigatorio
def movimentar(empresa_id: int, turno_id: uuid.UUID):
    turno = _turno_da_empresa(empresa_id, turno_id)
    form = MovimentoTurnoForm(prefix="movimento")
    if not form.validate_on_submit() or _uuid(form.turno_id.data) != turno.id:
        abort(400)
    administrativo = current_user.papel in {"proprietario", "administrador"}
    try:
        movimento = solicitar_movimento(
            empresa_id=empresa_id,
            turno_id=turno.id,
            tipo=form.tipo.data,
            valor=form.valor.data,
            motivo=form.motivo.data,
            usuario_id=current_user.id,
            autorizar_imediatamente=administrativo,
        )
    except (TurnoErro, TurnoConflito) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash(
            "Movimento aprovado e registrado."
            if movimento.status == "aprovado"
            else "Solicitação registrada e aguardando autorização.",
            "sucesso",
        )
    return _voltar(empresa_id)


@bp.post("/movimentos/<uuid:movimento_id>/<decisao>")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def decidir(empresa_id: int, movimento_id: uuid.UUID, decisao: str):
    if decisao not in {"aprovar", "rejeitar"}:
        abort(404)
    form = DecisaoMovimentoForm(prefix=f"decisao-{movimento_id}")
    if not form.validate_on_submit():
        flash("Informe uma justificativa válida para a decisão.", "erro")
        return redirect(url_for("operacao.autorizacoes", empresa_id=empresa_id))
    try:
        decidir_movimento(
            empresa_id=empresa_id,
            movimento_id=movimento_id,
            versao=_versao(form),
            aprovar=decisao == "aprovar",
            motivo_decisao=form.motivo.data,
            usuario_id=current_user.id,
        )
    except (TurnoErro, TurnoConflito) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash("Decisão registrada com auditoria.", "sucesso")
    return redirect(url_for("operacao.autorizacoes", empresa_id=empresa_id))


@bp.post("/turnos/<uuid:turno_id>/fechar")
@login_required
@acesso_empresa_obrigatorio
def fechar(empresa_id: int, turno_id: uuid.UUID):
    _turno_da_empresa(empresa_id, turno_id)
    form = FecharTurnoForm(prefix="fechar")
    if not form.validate_on_submit():
        flash("Preencha toda a contagem antes do fechamento.", "erro")
        return _voltar(empresa_id)
    try:
        fechar_turno(
            empresa_id=empresa_id,
            turno_id=turno_id,
            versao=_versao(form),
            valores_informados={
                "dinheiro": form.dinheiro.data,
                "pix": form.pix.data,
                "debito": form.debito.data,
                "credito": form.credito.data,
            },
            justificativa=form.justificativa.data,
            usuario_id=current_user.id,
            pode_fechar_outro=current_user.papel in {"proprietario", "administrador"},
        )
    except (TurnoErro, TurnoConflito) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash("Turno fechado. As diferenças foram calculadas no servidor.", "sucesso")
    return _voltar(empresa_id)


@bp.get("/turnos/<uuid:turno_id>")
@login_required
@acesso_empresa_obrigatorio
def detalhe_turno(empresa_id: int, turno_id: uuid.UUID):
    empresa = _empresa(empresa_id)
    turno = _turno_da_empresa(empresa_id, turno_id)
    return render_template(
        "operacao/turno.html",
        empresa=empresa,
        turno=turno,
        resumo=resumo_turno(turno.id),
        movimento_form=(
            MovimentoTurnoForm(prefix="movimento", turno_id=str(turno.id))
            if turno.status == "aberto"
            else None
        ),
        fechar_form=(
            FecharTurnoForm(prefix="fechar", versao=str(turno.versao))
            if turno.status == "aberto"
            else None
        ),
    )


@bp.get("/configuracao")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def configuracao(empresa_id: int):
    empresa = _empresa(empresa_id)
    terminais = db.session.scalars(
        db.select(TerminalCaixa)
        .where(TerminalCaixa.empresa_id == empresa_id)
        .order_by(TerminalCaixa.codigo)
    ).all()
    impressoras = db.session.scalars(
        db.select(Impressora)
        .where(Impressora.empresa_id == empresa_id)
        .order_by(Impressora.codigo)
    ).all()
    regras = db.session.scalars(
        db.select(RegraImpressao)
        .options(
            selectinload(RegraImpressao.terminal),
            selectinload(RegraImpressao.impressora),
        )
        .where(RegraImpressao.empresa_id == empresa_id)
        .order_by(RegraImpressao.evento, RegraImpressao.criado_em)
    ).all()
    regra_form = RegraImpressaoForm(prefix="regra-nova")
    regra_form.terminal_id.choices = [(str(item.id), item.nome) for item in terminais]
    regra_form.impressora_id.choices = [
        (str(item.id), f"{item.nome} · {item.setor}") for item in impressoras
    ]
    return render_template(
        "operacao/configuracao.html",
        empresa=empresa,
        terminais=terminais,
        impressoras=impressoras,
        regras=regras,
        terminal_form=TerminalForm(prefix="terminal-novo"),
        impressora_form=ImpressoraForm(prefix="impressora-nova"),
        regra_form=regra_form,
        formularios_terminal={
            item.id: AcaoVersaoForm(
                prefix=f"terminal-{item.id}", versao=str(item.versao)
            )
            for item in terminais
        },
        formularios_impressora={
            item.id: AcaoVersaoForm(
                prefix=f"impressora-{item.id}", versao=str(item.versao)
            )
            for item in impressoras
        },
        formularios_regra={
            item.id: AcaoVersaoForm(
                prefix=f"regra-{item.id}", versao=str(item.versao)
            )
            for item in regras
        },
    )


@bp.post("/configuracao/terminais")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def cadastrar_terminal(empresa_id: int):
    form = TerminalForm(prefix="terminal-novo")
    if not form.validate_on_submit():
        flash("Revise os dados do terminal.", "erro")
    else:
        try:
            criar_terminal(
                empresa_id=empresa_id,
                codigo=form.codigo.data,
                nome=form.nome.data,
                usuario_id=current_user.id,
            )
        except (TurnoErro, TurnoConflito) as exc:
            db.session.rollback()
            flash(str(exc), "erro")
        else:
            flash("Terminal cadastrado.", "sucesso")
    return redirect(url_for("operacao.configuracao", empresa_id=empresa_id))


@bp.post("/configuracao/terminais/<uuid:terminal_id>/status")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def status_terminal(empresa_id: int, terminal_id: uuid.UUID):
    form = AcaoVersaoForm(prefix=f"terminal-{terminal_id}")
    if not form.validate_on_submit():
        abort(400)
    try:
        alterar_status_terminal(
            empresa_id=empresa_id,
            terminal_id=terminal_id,
            versao=_versao(form),
            usuario_id=current_user.id,
        )
    except (TurnoErro, TurnoConflito) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash("Status do terminal atualizado.", "sucesso")
    return redirect(url_for("operacao.configuracao", empresa_id=empresa_id))


@bp.post("/configuracao/impressoras")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def cadastrar_impressora(empresa_id: int):
    form = ImpressoraForm(prefix="impressora-nova")
    if not form.validate_on_submit():
        flash("Revise os dados da impressora.", "erro")
    else:
        try:
            criar_impressora(
                empresa_id=empresa_id,
                codigo=form.codigo.data,
                nome=form.nome.data,
                setor=form.setor.data,
                tipo=form.tipo.data,
                usuario_id=current_user.id,
            )
        except (ImpressaoErro, ImpressaoConflito) as exc:
            db.session.rollback()
            flash(str(exc), "erro")
        else:
            flash("Impressora simulada cadastrada.", "sucesso")
    return redirect(url_for("operacao.configuracao", empresa_id=empresa_id))


@bp.post("/configuracao/impressoras/<uuid:impressora_id>/status")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def status_impressora(empresa_id: int, impressora_id: uuid.UUID):
    form = AcaoVersaoForm(prefix=f"impressora-{impressora_id}")
    if not form.validate_on_submit():
        abort(400)
    try:
        alterar_status_impressora(
            empresa_id=empresa_id,
            impressora_id=impressora_id,
            versao=_versao(form),
            usuario_id=current_user.id,
        )
    except (ImpressaoErro, ImpressaoConflito) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash("Status da impressora atualizado.", "sucesso")
    return redirect(url_for("operacao.configuracao", empresa_id=empresa_id))


@bp.post("/configuracao/regras")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def cadastrar_regra(empresa_id: int):
    terminais = db.session.scalars(
        db.select(TerminalCaixa).where(TerminalCaixa.empresa_id == empresa_id)
    ).all()
    impressoras = db.session.scalars(
        db.select(Impressora).where(Impressora.empresa_id == empresa_id)
    ).all()
    form = RegraImpressaoForm(prefix="regra-nova")
    form.terminal_id.choices = [(str(item.id), item.nome) for item in terminais]
    form.impressora_id.choices = [(str(item.id), item.nome) for item in impressoras]
    if not form.validate_on_submit():
        flash("Revise os dados da regra.", "erro")
    else:
        try:
            criar_regra(
                empresa_id=empresa_id,
                terminal_id=_uuid(form.terminal_id.data),
                impressora_id=_uuid(form.impressora_id.data),
                evento=form.evento.data,
                copias=int(form.copias.data),
                usuario_id=current_user.id,
            )
        except (ImpressaoErro, ImpressaoConflito) as exc:
            db.session.rollback()
            flash(str(exc), "erro")
        else:
            flash("Regra de impressão cadastrada.", "sucesso")
    return redirect(url_for("operacao.configuracao", empresa_id=empresa_id))


@bp.post("/configuracao/regras/<uuid:regra_id>/status")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def status_regra(empresa_id: int, regra_id: uuid.UUID):
    form = AcaoVersaoForm(prefix=f"regra-{regra_id}")
    if not form.validate_on_submit():
        abort(400)
    try:
        alterar_status_regra(
            empresa_id=empresa_id,
            regra_id=regra_id,
            versao=_versao(form),
            usuario_id=current_user.id,
        )
    except (ImpressaoErro, ImpressaoConflito) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash("Status da regra atualizado.", "sucesso")
    return redirect(url_for("operacao.configuracao", empresa_id=empresa_id))


@bp.get("/impressoes")
@login_required
@acesso_empresa_obrigatorio
def impressoes(empresa_id: int):
    empresa = _empresa(empresa_id)
    consulta = (
        db.select(TrabalhoImpressao)
        .options(
            selectinload(TrabalhoImpressao.terminal),
            selectinload(TrabalhoImpressao.impressora),
            selectinload(TrabalhoImpressao.solicitado_por),
        )
        .where(TrabalhoImpressao.empresa_id == empresa_id)
    )
    if current_user.papel == "operador":
        consulta = consulta.where(TrabalhoImpressao.solicitado_por_id == current_user.id)
    trabalhos = db.session.scalars(
        consulta.order_by(TrabalhoImpressao.criado_em.desc()).limit(100)
    ).all()
    return render_template(
        "operacao/impressoes.html", empresa=empresa, trabalhos=trabalhos
    )


@bp.get("/impressoes/<uuid:trabalho_id>")
@login_required
@acesso_empresa_obrigatorio
def trabalho(empresa_id: int, trabalho_id: uuid.UUID):
    empresa = _empresa(empresa_id)
    item = _trabalho_da_empresa(empresa_id, trabalho_id)
    return render_template(
        "operacao/trabalho.html",
        empresa=empresa,
        trabalho=item,
        imprimir_form=AcaoVersaoForm(prefix="imprimir", versao=str(item.versao)),
        falhar_form=AcaoVersaoForm(prefix="falhar", versao=str(item.versao)),
        reprocessar_form=AcaoVersaoForm(
            prefix="reprocessar", versao=str(item.versao)
        ),
        reimpressao_form=ReimpressaoForm(versao=str(item.versao)),
    )


def _acao_impressao(empresa_id: int, trabalho_id: uuid.UUID, acao: str):
    item = _trabalho_da_empresa(empresa_id, trabalho_id)
    form = AcaoVersaoForm(prefix=acao)
    if not form.validate_on_submit():
        abort(400)
    try:
        if acao == "imprimir":
            simular_impressao(
                empresa_id=empresa_id,
                trabalho_id=item.id,
                versao=_versao(form),
                usuario_id=current_user.id,
            )
        elif acao == "falhar":
            if current_user.papel not in {"proprietario", "administrador"}:
                abort(403)
            simular_falha(
                empresa_id=empresa_id,
                trabalho_id=item.id,
                versao=_versao(form),
                usuario_id=current_user.id,
            )
        elif acao == "reprocessar":
            if current_user.papel not in {"proprietario", "administrador"}:
                abort(403)
            reprocessar_trabalho(
                empresa_id=empresa_id,
                trabalho_id=item.id,
                versao=_versao(form),
                usuario_id=current_user.id,
            )
        else:
            abort(404)
    except (ImpressaoErro, ImpressaoConflito) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash("Trabalho de impressão atualizado.", "sucesso")
    return redirect(
        url_for("operacao.trabalho", empresa_id=empresa_id, trabalho_id=trabalho_id)
    )


@bp.post("/impressoes/<uuid:trabalho_id>/imprimir")
@login_required
@acesso_empresa_obrigatorio
def imprimir(empresa_id: int, trabalho_id: uuid.UUID):
    return _acao_impressao(empresa_id, trabalho_id, "imprimir")


@bp.post("/impressoes/<uuid:trabalho_id>/falhar")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def falhar(empresa_id: int, trabalho_id: uuid.UUID):
    return _acao_impressao(empresa_id, trabalho_id, "falhar")


@bp.post("/impressoes/<uuid:trabalho_id>/reprocessar")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def reprocessar(empresa_id: int, trabalho_id: uuid.UUID):
    return _acao_impressao(empresa_id, trabalho_id, "reprocessar")


@bp.post("/impressoes/<uuid:trabalho_id>/reimprimir")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def reimprimir(empresa_id: int, trabalho_id: uuid.UUID):
    _trabalho_da_empresa(empresa_id, trabalho_id)
    form = ReimpressaoForm()
    if not form.validate_on_submit():
        flash("Informe um motivo válido para a reimpressão.", "erro")
    else:
        try:
            criado = criar_reimpressao(
                empresa_id=empresa_id,
                trabalho_id=trabalho_id,
                motivo=form.motivo.data,
                versao=_versao(form),
                usuario_id=current_user.id,
            )
        except (ImpressaoErro, ImpressaoConflito) as exc:
            db.session.rollback()
            flash(str(exc), "erro")
        else:
            flash("Reimpressão criada com motivo e trilha de auditoria.", "sucesso")
            return redirect(
                url_for(
                    "operacao.trabalho", empresa_id=empresa_id, trabalho_id=criado.id
                )
            )
    return redirect(
        url_for("operacao.trabalho", empresa_id=empresa_id, trabalho_id=trabalho_id)
    )
