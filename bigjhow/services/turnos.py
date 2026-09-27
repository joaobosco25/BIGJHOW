from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import uuid

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (
    Empresa,
    MovimentoCaixa,
    TerminalCaixa,
    TurnoCaixa,
    Usuario,
    Venda,
    VendaPagamento,
    utcnow,
)
from .auditoria import registrar


_CENTAVO = Decimal("0.01")
_MAX_DINHEIRO = Decimal("99999999999999.99")
_DINHEIRO_RE = re.compile(r"^\d{1,14}(?:[.,]\d{1,2})?$")
_CODIGO_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,39}$")
_METODOS = ("dinheiro", "pix", "debito", "credito")


class TurnoErro(ValueError):
    pass


class TurnoConflito(TurnoErro):
    pass


def normalizar_valor_caixa(valor: str | Decimal | None) -> Decimal:
    texto = str(valor if valor is not None else "").strip()
    if not texto or not _DINHEIRO_RE.fullmatch(texto):
        raise TurnoErro("Informe um valor entre zero e o limite, com até duas casas decimais.")
    try:
        numero = Decimal(texto.replace(",", ".")).quantize(_CENTAVO)
    except InvalidOperation as exc:
        raise TurnoErro("Valor monetário inválido.") from exc
    if numero < 0 or numero > _MAX_DINHEIRO:
        raise TurnoErro("O valor monetário está fora do limite permitido.")
    return numero


def _normalizar_texto(valor: str | None, *, minimo: int, maximo: int, rotulo: str) -> str:
    texto = " ".join((valor or "").split())
    if not minimo <= len(texto) <= maximo:
        raise TurnoErro(f"{rotulo} precisa ter entre {minimo} e {maximo} caracteres.")
    return texto


def normalizar_codigo_terminal(valor: str | None) -> str:
    codigo = (valor or "").strip().upper()
    if not _CODIGO_RE.fullmatch(codigo):
        raise TurnoErro(
            "O código do terminal deve ter de 3 a 40 caracteres: letras, números, ponto, hífen ou sublinhado."
        )
    return codigo


def criar_terminal(
    *, empresa_id: int, codigo: str, nome: str, usuario_id: uuid.UUID
) -> TerminalCaixa:
    empresa = db.session.get(Empresa, empresa_id)
    if empresa is None or not empresa.ativa:
        raise TurnoErro("Empresa não encontrada ou inativa.")
    codigo_normalizado = normalizar_codigo_terminal(codigo)
    nome_normalizado = _normalizar_texto(nome, minimo=3, maximo=120, rotulo="O nome")
    existente = db.session.scalar(
        db.select(TerminalCaixa).where(
            TerminalCaixa.empresa_id == empresa_id,
            TerminalCaixa.codigo == codigo_normalizado,
        )
    )
    if existente is not None:
        raise TurnoConflito("Já existe um terminal com este código na empresa.")
    terminal = TerminalCaixa(
        empresa_id=empresa_id,
        codigo=codigo_normalizado,
        nome=nome_normalizado,
        criado_por_id=usuario_id,
    )
    db.session.add(terminal)
    db.session.flush()
    registrar(
        "terminal_caixa_criado",
        "terminal_caixa",
        terminal.id,
        empresa_id,
        {"codigo": terminal.codigo, "nome": terminal.nome},
        usuario_id=usuario_id,
    )
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise TurnoConflito("O terminal não pôde ser criado por conflito.") from exc
    return terminal


def alterar_status_terminal(
    *,
    empresa_id: int,
    terminal_id: uuid.UUID,
    versao: int,
    usuario_id: uuid.UUID,
) -> TerminalCaixa:
    terminal = db.session.scalar(
        db.select(TerminalCaixa)
        .where(TerminalCaixa.id == terminal_id, TerminalCaixa.empresa_id == empresa_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if terminal is None:
        raise TurnoErro("Terminal não encontrado nesta empresa.")
    if terminal.versao != versao:
        raise TurnoConflito("O terminal foi alterado em outra sessão.")
    if terminal.ativo:
        aberto = db.session.scalar(
            db.select(TurnoCaixa.id).where(
                TurnoCaixa.terminal_id == terminal.id,
                TurnoCaixa.status == "aberto",
            )
        )
        if aberto is not None:
            raise TurnoErro("Feche o turno deste terminal antes de desativá-lo.")
    terminal.ativo = not terminal.ativo
    terminal.versao += 1
    registrar(
        "terminal_caixa_ativado" if terminal.ativo else "terminal_caixa_desativado",
        "terminal_caixa",
        terminal.id,
        empresa_id,
        {"codigo": terminal.codigo},
        usuario_id=usuario_id,
    )
    db.session.commit()
    return terminal


def turno_aberto_do_operador(
    *, empresa_id: int, operador_id: uuid.UUID
) -> TurnoCaixa | None:
    return db.session.scalar(
        db.select(TurnoCaixa).where(
            TurnoCaixa.empresa_id == empresa_id,
            TurnoCaixa.operador_id == operador_id,
            TurnoCaixa.status == "aberto",
        )
    )


def abrir_turno(
    *,
    empresa_id: int,
    terminal_id: uuid.UUID,
    operador_id: uuid.UUID,
    valor_inicial: str | Decimal,
) -> TurnoCaixa:
    valor = normalizar_valor_caixa(valor_inicial)
    usuario = db.session.get(Usuario, operador_id)
    if usuario is None or not usuario.ativo or not usuario.pode_acessar(empresa_id):
        raise TurnoErro("Usuário sem acesso ativo a esta empresa.")
    terminal = db.session.scalar(
        db.select(TerminalCaixa)
        .where(
            TerminalCaixa.id == terminal_id,
            TerminalCaixa.empresa_id == empresa_id,
            TerminalCaixa.ativo.is_(True),
        )
        .with_for_update()
    )
    if terminal is None:
        raise TurnoErro("Terminal inexistente, inativo ou de outra empresa.")
    existente = db.session.scalar(
        db.select(TurnoCaixa).where(
            TurnoCaixa.operador_id == operador_id,
            TurnoCaixa.status == "aberto",
        )
    )
    if existente is not None:
        raise TurnoConflito("Este usuário já possui um turno aberto.")
    ocupado = db.session.scalar(
        db.select(TurnoCaixa.id).where(
            TurnoCaixa.terminal_id == terminal_id,
            TurnoCaixa.status == "aberto",
        )
    )
    if ocupado is not None:
        raise TurnoConflito("Este terminal já possui um turno aberto.")
    turno = TurnoCaixa(
        empresa_id=empresa_id,
        terminal_id=terminal_id,
        operador_id=operador_id,
        aberto_por_id=operador_id,
        valor_inicial=valor,
    )
    db.session.add(turno)
    db.session.flush()
    registrar(
        "turno_caixa_aberto",
        "turno_caixa",
        turno.id,
        empresa_id,
        {
            "terminal": terminal.codigo,
            "valor_inicial": str(valor),
            "operador_id": str(operador_id),
        },
        usuario_id=operador_id,
    )
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise TurnoConflito("Não foi possível abrir o turno por conflito concorrente.") from exc
    return turno


def _turno_bloqueado(empresa_id: int, turno_id: uuid.UUID) -> TurnoCaixa:
    turno = db.session.scalar(
        db.select(TurnoCaixa)
        .where(TurnoCaixa.id == turno_id, TurnoCaixa.empresa_id == empresa_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if turno is None:
        raise TurnoErro("Turno não encontrado nesta empresa.")
    return turno


def resumos_turnos(
    turno_ids: list[uuid.UUID] | tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, dict[str, Decimal | int]]:
    """Calcula vários resumos sem executar uma consulta por turno."""
    ids = list(dict.fromkeys(turno_ids))
    if not ids:
        return {}

    turnos = db.session.scalars(
        db.select(TurnoCaixa).where(TurnoCaixa.id.in_(ids))
    ).all()
    resumos: dict[uuid.UUID, dict[str, Decimal | int]] = {}
    for turno in turnos:
        resumos[turno.id] = {
            "dinheiro": Decimal(turno.valor_inicial).quantize(_CENTAVO),
            "dinheiro_vendas": Decimal("0.00"),
            "dinheiro_recebido": Decimal("0.00"),
            "troco": Decimal("0.00"),
            "pix": Decimal("0.00"),
            "debito": Decimal("0.00"),
            "credito": Decimal("0.00"),
            "total_vendas": Decimal("0.00"),
            "sangrias": Decimal("0.00"),
            "suprimentos": Decimal("0.00"),
            "vendas_finalizadas": 0,
            "vendas_canceladas": 0,
        }

    linhas_pagamento = db.session.execute(
        db.select(
            Venda.turno_id,
            VendaPagamento.metodo,
            func.sum(VendaPagamento.valor),
            func.sum(VendaPagamento.valor_recebido),
        )
        .join(VendaPagamento, VendaPagamento.venda_id == Venda.id)
        .where(Venda.turno_id.in_(ids), Venda.status == "finalizada")
        .group_by(Venda.turno_id, VendaPagamento.metodo)
    ).all()
    for turno_id, metodo, valor, valor_recebido in linhas_pagamento:
        resumo = resumos.get(turno_id)
        if resumo is None:
            continue
        aplicado = Decimal(valor or 0).quantize(_CENTAVO)
        recebido = Decimal(valor_recebido or 0).quantize(_CENTAVO)
        resumo[metodo] = aplicado
        resumo["total_vendas"] = (
            Decimal(resumo["total_vendas"]) + aplicado
        ).quantize(_CENTAVO)
        if metodo == "dinheiro":
            resumo["dinheiro_vendas"] = aplicado
            resumo["dinheiro_recebido"] = recebido
            resumo["troco"] = (recebido - aplicado).quantize(_CENTAVO)

    linhas_movimento = db.session.execute(
        db.select(
            MovimentoCaixa.turno_id,
            MovimentoCaixa.tipo,
            func.sum(MovimentoCaixa.valor),
        )
        .where(
            MovimentoCaixa.turno_id.in_(ids),
            MovimentoCaixa.status == "aprovado",
        )
        .group_by(MovimentoCaixa.turno_id, MovimentoCaixa.tipo)
    ).all()
    for turno_id, tipo, valor in linhas_movimento:
        resumo = resumos.get(turno_id)
        if resumo is not None:
            resumo["sangrias" if tipo == "sangria" else "suprimentos"] = Decimal(
                valor or 0
            ).quantize(_CENTAVO)

    linhas_venda = db.session.execute(
        db.select(Venda.turno_id, Venda.status, func.count(Venda.id))
        .where(
            Venda.turno_id.in_(ids),
            Venda.status.in_(("finalizada", "cancelada")),
        )
        .group_by(Venda.turno_id, Venda.status)
    ).all()
    for turno_id, status, quantidade in linhas_venda:
        resumo = resumos.get(turno_id)
        if resumo is not None:
            resumo[
                "vendas_finalizadas" if status == "finalizada" else "vendas_canceladas"
            ] = int(quantidade)

    for turno in turnos:
        resumo = resumos[turno.id]
        dinheiro = (
            Decimal(turno.valor_inicial)
            + Decimal(resumo["dinheiro_vendas"])
            + Decimal(resumo["suprimentos"])
            - Decimal(resumo["sangrias"])
        ).quantize(_CENTAVO)
        if dinheiro < 0:
            raise TurnoConflito("Os movimentos deixaram o caixa esperado negativo.")
        resumo["dinheiro"] = dinheiro
    return resumos


def resumo_turno(turno_id: uuid.UUID) -> dict[str, Decimal | int]:
    resumo = resumos_turnos([turno_id]).get(turno_id)
    if resumo is None:
        raise TurnoErro("Turno não encontrado.")
    return resumo


def solicitar_movimento(
    *,
    empresa_id: int,
    turno_id: uuid.UUID,
    tipo: str,
    valor: str | Decimal,
    motivo: str,
    usuario_id: uuid.UUID,
    autorizar_imediatamente: bool,
) -> MovimentoCaixa:
    if tipo not in {"sangria", "suprimento"}:
        raise TurnoErro("Tipo de movimento inválido.")
    numero = normalizar_valor_caixa(valor)
    if numero <= 0:
        raise TurnoErro("O movimento precisa ter valor maior que zero.")
    motivo_normalizado = _normalizar_texto(
        motivo, minimo=5, maximo=500, rotulo="O motivo"
    )
    turno = _turno_bloqueado(empresa_id, turno_id)
    if turno.status != "aberto":
        raise TurnoConflito("O turno já foi fechado.")
    if turno.operador_id != usuario_id and not autorizar_imediatamente:
        raise TurnoErro("Somente o operador do turno pode solicitar este movimento.")
    agora = utcnow()
    movimento = MovimentoCaixa(
        empresa_id=empresa_id,
        turno_id=turno.id,
        tipo=tipo,
        valor=numero,
        motivo=motivo_normalizado,
        solicitado_por_id=usuario_id,
        status="aprovado" if autorizar_imediatamente else "pendente",
        decidido_por_id=usuario_id if autorizar_imediatamente else None,
        decidido_em=agora if autorizar_imediatamente else None,
        motivo_decisao="Autorização administrativa imediata" if autorizar_imediatamente else None,
    )
    db.session.add(movimento)
    db.session.flush()
    acao = "movimento_caixa_aprovado" if autorizar_imediatamente else "movimento_caixa_solicitado"
    registrar(
        acao,
        "movimento_caixa",
        movimento.id,
        empresa_id,
        {
            "turno_id": str(turno.id),
            "tipo": tipo,
            "valor": str(numero),
            "motivo": motivo_normalizado,
        },
        usuario_id=usuario_id,
    )
    if autorizar_imediatamente:
        from .impressao import enfileirar_evento

        enfileirar_evento(
            empresa_id=empresa_id,
            terminal_id=turno.terminal_id,
            evento="movimento_caixa_aprovado",
            entidade="movimento_caixa",
            entidade_id=movimento.id,
            payload={
                "tipo": movimento.tipo,
                "valor": str(movimento.valor),
                "motivo": movimento.motivo,
                "turno_id": str(turno.id),
            },
            usuario_id=usuario_id,
        )
        db.session.flush()
        resumo_turno(turno.id)
    db.session.commit()
    return movimento


def decidir_movimento(
    *,
    empresa_id: int,
    movimento_id: uuid.UUID,
    versao: int,
    aprovar: bool,
    motivo_decisao: str,
    usuario_id: uuid.UUID,
) -> MovimentoCaixa:
    movimento = db.session.scalar(
        db.select(MovimentoCaixa)
        .where(
            MovimentoCaixa.id == movimento_id,
            MovimentoCaixa.empresa_id == empresa_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if movimento is None:
        raise TurnoErro("Movimento não encontrado nesta empresa.")
    if movimento.status != "pendente":
        raise TurnoConflito("Este movimento já foi decidido.")
    if movimento.versao != versao:
        raise TurnoConflito("O movimento foi alterado em outra sessão.")
    decisao = _normalizar_texto(
        motivo_decisao, minimo=5, maximo=500, rotulo="A justificativa"
    )
    turno = _turno_bloqueado(empresa_id, movimento.turno_id)
    if turno.status != "aberto":
        raise TurnoConflito("Não é possível decidir movimento de turno fechado.")
    movimento.status = "aprovado" if aprovar else "rejeitado"
    movimento.decidido_por_id = usuario_id
    movimento.decidido_em = utcnow()
    movimento.motivo_decisao = decisao
    movimento.versao += 1
    registrar(
        "movimento_caixa_aprovado" if aprovar else "movimento_caixa_rejeitado",
        "movimento_caixa",
        movimento.id,
        empresa_id,
        {
            "turno_id": str(turno.id),
            "tipo": movimento.tipo,
            "valor": str(movimento.valor),
            "justificativa": decisao,
        },
        usuario_id=usuario_id,
    )
    if aprovar:
        from .impressao import enfileirar_evento

        enfileirar_evento(
            empresa_id=empresa_id,
            terminal_id=turno.terminal_id,
            evento="movimento_caixa_aprovado",
            entidade="movimento_caixa",
            entidade_id=movimento.id,
            payload={
                "tipo": movimento.tipo,
                "valor": str(movimento.valor),
                "motivo": movimento.motivo,
                "turno_id": str(turno.id),
            },
            usuario_id=usuario_id,
        )
        db.session.flush()
        resumo_turno(turno.id)
    db.session.commit()
    return movimento


def fechar_turno(
    *,
    empresa_id: int,
    turno_id: uuid.UUID,
    versao: int,
    valores_informados: dict[str, str | Decimal],
    justificativa: str | None,
    usuario_id: uuid.UUID,
    pode_fechar_outro: bool,
) -> TurnoCaixa:
    turno = _turno_bloqueado(empresa_id, turno_id)
    if turno.status != "aberto":
        raise TurnoConflito("Este turno já foi fechado.")
    if turno.versao != versao:
        raise TurnoConflito("O turno foi alterado em outra sessão.")
    fechando_outro = turno.operador_id != usuario_id
    if fechando_outro and not pode_fechar_outro:
        raise TurnoErro("Somente o operador do turno ou um administrador pode fechá-lo.")
    venda_aberta = db.session.scalar(
        db.select(Venda.id).where(Venda.turno_id == turno.id, Venda.status == "aberta")
    )
    if venda_aberta is not None:
        raise TurnoErro("Finalize ou descarte o atendimento aberto antes de fechar o turno.")
    pendente = db.session.scalar(
        db.select(MovimentoCaixa.id).where(
            MovimentoCaixa.turno_id == turno.id,
            MovimentoCaixa.status == "pendente",
        )
    )
    if pendente is not None:
        raise TurnoErro("Decida todas as sangrias e suprimentos antes de fechar o turno.")
    informados = {
        metodo: normalizar_valor_caixa(valores_informados.get(metodo))
        for metodo in _METODOS
    }
    resumo = resumo_turno(turno.id)
    diferencas = {
        metodo: (informados[metodo] - Decimal(resumo[metodo])).quantize(_CENTAVO)
        for metodo in _METODOS
    }
    exige_justificativa = fechando_outro or any(valor != 0 for valor in diferencas.values())
    texto_justificativa = " ".join((justificativa or "").split())
    if exige_justificativa and not 5 <= len(texto_justificativa) <= 500:
        raise TurnoErro(
            "Informe uma justificativa de 5 a 500 caracteres para diferenças ou fechamento administrativo."
        )
    if texto_justificativa and len(texto_justificativa) > 500:
        raise TurnoErro("A justificativa deve ter no máximo 500 caracteres.")
    turno.dinheiro_esperado = resumo["dinheiro"]
    turno.dinheiro_informado = informados["dinheiro"]
    turno.dinheiro_diferenca = diferencas["dinheiro"]
    turno.pix_esperado = resumo["pix"]
    turno.pix_informado = informados["pix"]
    turno.pix_diferenca = diferencas["pix"]
    turno.debito_esperado = resumo["debito"]
    turno.debito_informado = informados["debito"]
    turno.debito_diferenca = diferencas["debito"]
    turno.credito_esperado = resumo["credito"]
    turno.credito_informado = informados["credito"]
    turno.credito_diferenca = diferencas["credito"]
    turno.total_vendas = resumo["total_vendas"]
    turno.total_sangrias = resumo["sangrias"]
    turno.total_suprimentos = resumo["suprimentos"]
    turno.justificativa_fechamento = texto_justificativa or None
    turno.status = "fechado"
    turno.fechado_por_id = usuario_id
    turno.fechado_em = utcnow()
    turno.versao += 1
    registrar(
        "turno_caixa_fechado",
        "turno_caixa",
        turno.id,
        empresa_id,
        {
            "terminal_id": str(turno.terminal_id),
            "operador_id": str(turno.operador_id) if turno.operador_id else None,
            "total_vendas": str(resumo["total_vendas"]),
            "diferencas": {chave: str(valor) for chave, valor in diferencas.items()},
            "fechamento_administrativo": fechando_outro,
            "justificativa": texto_justificativa or None,
        },
        usuario_id=usuario_id,
    )
    from .impressao import enfileirar_evento

    enfileirar_evento(
        empresa_id=empresa_id,
        terminal_id=turno.terminal_id,
        evento="turno_fechado",
        entidade="turno_caixa",
        entidade_id=turno.id,
        payload={
            "turno_id": str(turno.id),
            "terminal": turno.terminal.codigo,
            "operador": turno.operador.nome if turno.operador else "Usuário removido",
            "valor_inicial": str(turno.valor_inicial),
            "total_vendas": str(resumo["total_vendas"]),
            "dinheiro_esperado": str(resumo["dinheiro"]),
            "dinheiro_informado": str(informados["dinheiro"]),
            "diferencas": {chave: str(valor) for chave, valor in diferencas.items()},
        },
        usuario_id=usuario_id,
    )
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise TurnoConflito("O fechamento encontrou um conflito e foi cancelado.") from exc
    return turno
