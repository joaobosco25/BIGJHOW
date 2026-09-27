from __future__ import annotations

import re
import uuid

from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (
    Empresa,
    Impressora,
    RegraImpressao,
    TerminalCaixa,
    TrabalhoImpressao,
    Venda,
    VendaItem,
    VendaPagamento,
    utcnow,
)
from .auditoria import registrar


_CODIGO_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,39}$")
_EVENTOS = {
    "venda_finalizada",
    "venda_cancelada",
    "turno_fechado",
    "movimento_caixa_aprovado",
}
_TIPOS = {"cupom", "producao", "etiqueta", "relatorio"}


class ImpressaoErro(ValueError):
    pass


class ImpressaoConflito(ImpressaoErro):
    pass


def _texto(valor: str | None, *, minimo: int, maximo: int, rotulo: str) -> str:
    normalizado = " ".join((valor or "").split())
    if not minimo <= len(normalizado) <= maximo:
        raise ImpressaoErro(f"{rotulo} precisa ter entre {minimo} e {maximo} caracteres.")
    return normalizado


def _codigo(valor: str | None) -> str:
    normalizado = (valor or "").strip().upper()
    if not _CODIGO_RE.fullmatch(normalizado):
        raise ImpressaoErro(
            "O código deve ter de 3 a 40 caracteres: letras, números, ponto, hífen ou sublinhado."
        )
    return normalizado


def criar_impressora(
    *,
    empresa_id: int,
    codigo: str,
    nome: str,
    setor: str,
    tipo: str,
    usuario_id: uuid.UUID,
) -> Impressora:
    empresa = db.session.get(Empresa, empresa_id)
    if empresa is None or not empresa.ativa:
        raise ImpressaoErro("Empresa não encontrada ou inativa.")
    codigo_normalizado = _codigo(codigo)
    if tipo not in _TIPOS:
        raise ImpressaoErro("Tipo de impressora inválido.")
    existente = db.session.scalar(
        db.select(Impressora).where(
            Impressora.empresa_id == empresa_id,
            Impressora.codigo == codigo_normalizado,
        )
    )
    if existente is not None:
        raise ImpressaoConflito("Já existe uma impressora com este código na empresa.")
    impressora = Impressora(
        empresa_id=empresa_id,
        codigo=codigo_normalizado,
        nome=_texto(nome, minimo=3, maximo=120, rotulo="O nome"),
        setor=_texto(setor, minimo=2, maximo=80, rotulo="O setor"),
        tipo=tipo,
        modo="simulada",
        criado_por_id=usuario_id,
    )
    db.session.add(impressora)
    db.session.flush()
    registrar(
        "impressora_criada",
        "impressora",
        impressora.id,
        empresa_id,
        {
            "codigo": impressora.codigo,
            "nome": impressora.nome,
            "setor": impressora.setor,
            "tipo": impressora.tipo,
            "modo": impressora.modo,
        },
        usuario_id=usuario_id,
    )
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ImpressaoConflito("A impressora não pôde ser criada por conflito.") from exc
    return impressora


def criar_regra(
    *,
    empresa_id: int,
    terminal_id: uuid.UUID,
    impressora_id: uuid.UUID,
    evento: str,
    copias: int,
    usuario_id: uuid.UUID,
) -> RegraImpressao:
    if evento not in _EVENTOS:
        raise ImpressaoErro("Evento de impressão inválido.")
    if copias not in {1, 2, 3}:
        raise ImpressaoErro("A quantidade de cópias precisa estar entre 1 e 3.")
    terminal = db.session.scalar(
        db.select(TerminalCaixa).where(
            TerminalCaixa.id == terminal_id,
            TerminalCaixa.empresa_id == empresa_id,
        )
    )
    impressora = db.session.scalar(
        db.select(Impressora).where(
            Impressora.id == impressora_id,
            Impressora.empresa_id == empresa_id,
        )
    )
    if terminal is None or impressora is None:
        raise ImpressaoErro("Terminal ou impressora não pertence a esta empresa.")
    existente = db.session.scalar(
        db.select(RegraImpressao).where(
            RegraImpressao.terminal_id == terminal_id,
            RegraImpressao.impressora_id == impressora_id,
            RegraImpressao.evento == evento,
        )
    )
    if existente is not None:
        raise ImpressaoConflito("Esta regra já existe para o terminal e a impressora.")
    regra = RegraImpressao(
        empresa_id=empresa_id,
        terminal_id=terminal_id,
        impressora_id=impressora_id,
        evento=evento,
        copias=copias,
        criado_por_id=usuario_id,
    )
    db.session.add(regra)
    db.session.flush()
    registrar(
        "regra_impressao_criada",
        "regra_impressao",
        regra.id,
        empresa_id,
        {
            "terminal": terminal.codigo,
            "impressora": impressora.codigo,
            "evento": evento,
            "copias": copias,
        },
        usuario_id=usuario_id,
    )
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ImpressaoConflito("A regra não pôde ser criada por conflito.") from exc
    return regra


def alterar_status_impressora(
    *, empresa_id: int, impressora_id: uuid.UUID, versao: int, usuario_id: uuid.UUID
) -> Impressora:
    impressora = db.session.scalar(
        db.select(Impressora)
        .where(Impressora.id == impressora_id, Impressora.empresa_id == empresa_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if impressora is None:
        raise ImpressaoErro("Impressora não encontrada nesta empresa.")
    if impressora.versao != versao:
        raise ImpressaoConflito("A impressora foi alterada em outra sessão.")
    impressora.ativo = not impressora.ativo
    impressora.versao += 1
    registrar(
        "impressora_ativada" if impressora.ativo else "impressora_desativada",
        "impressora",
        impressora.id,
        empresa_id,
        {"codigo": impressora.codigo},
        usuario_id=usuario_id,
    )
    db.session.commit()
    return impressora


def alterar_status_regra(
    *, empresa_id: int, regra_id: uuid.UUID, versao: int, usuario_id: uuid.UUID
) -> RegraImpressao:
    regra = db.session.scalar(
        db.select(RegraImpressao)
        .where(RegraImpressao.id == regra_id, RegraImpressao.empresa_id == empresa_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if regra is None:
        raise ImpressaoErro("Regra não encontrada nesta empresa.")
    if regra.versao != versao:
        raise ImpressaoConflito("A regra foi alterada em outra sessão.")
    regra.ativo = not regra.ativo
    regra.versao += 1
    registrar(
        "regra_impressao_ativada" if regra.ativo else "regra_impressao_desativada",
        "regra_impressao",
        regra.id,
        empresa_id,
        {"evento": regra.evento},
        usuario_id=usuario_id,
    )
    db.session.commit()
    return regra


def enfileirar_evento(
    *,
    empresa_id: int,
    terminal_id: uuid.UUID,
    evento: str,
    entidade: str,
    entidade_id: object,
    payload: dict,
    usuario_id: uuid.UUID,
) -> list[TrabalhoImpressao]:
    if evento not in _EVENTOS:
        raise ImpressaoErro("Evento de impressão não permitido.")
    regras = db.session.scalars(
        db.select(RegraImpressao)
        .join(Impressora, Impressora.id == RegraImpressao.impressora_id)
        .where(
            RegraImpressao.empresa_id == empresa_id,
            RegraImpressao.terminal_id == terminal_id,
            RegraImpressao.evento == evento,
            RegraImpressao.ativo.is_(True),
            Impressora.ativo.is_(True),
        )
        .order_by(RegraImpressao.id)
    ).all()
    criados: list[TrabalhoImpressao] = []
    for regra in regras:
        chave = f"{evento}:{entidade}:{entidade_id}:{regra.id}"
        existente = db.session.scalar(
            db.select(TrabalhoImpressao).where(
                TrabalhoImpressao.chave_idempotencia == chave
            )
        )
        if existente is not None:
            criados.append(existente)
            continue
        trabalho = TrabalhoImpressao(
            empresa_id=empresa_id,
            terminal_id=terminal_id,
            impressora_id=regra.impressora_id,
            regra_id=regra.id,
            evento=evento,
            entidade=entidade,
            entidade_id=str(entidade_id),
            chave_idempotencia=chave,
            payload=payload,
            copias=regra.copias,
            solicitado_por_id=usuario_id,
        )
        db.session.add(trabalho)
        criados.append(trabalho)
    if criados:
        db.session.flush()
    return criados


def _payload_venda(venda: Venda) -> dict:
    empresa = db.session.get(Empresa, venda.empresa_id)
    itens = db.session.scalars(
        db.select(VendaItem)
        .where(VendaItem.venda_id == venda.id)
        .order_by(VendaItem.criado_em)
    ).all()
    pagamentos = db.session.scalars(
        db.select(VendaPagamento)
        .where(VendaPagamento.venda_id == venda.id)
        .order_by(VendaPagamento.criado_em)
    ).all()
    from ..models import DocumentoFiscal

    documento_fiscal = db.session.scalar(
        db.select(DocumentoFiscal).where(DocumentoFiscal.venda_id == venda.id)
    )
    tem_valor_fiscal = (
        documento_fiscal is not None
        and documento_fiscal.status == "autorizado"
        and not documento_fiscal.sem_valor_fiscal
        and int(documento_fiscal.ambiente) == 1
    )
    return {
        "documento": (
            "COMPROVANTE DA VENDA — DOCUMENTO FISCAL EM SEPARADO"
            if tem_valor_fiscal
            else "TICKET SEM VALOR FISCAL"
        ),
        "documento_fiscal": (
            {
                "chave": documento_fiscal.chave,
                "numero": documento_fiscal.numero_formatado,
                "serie": documento_fiscal.serie,
                "modelo": documento_fiscal.modelo,
                "status": documento_fiscal.status,
                "protocolo": documento_fiscal.protocolo_autorizacao,
                "tem_valor_fiscal": tem_valor_fiscal,
            }
            if documento_fiscal is not None
            else None
        ),
        "empresa": empresa.nome if empresa else "Empresa removida",
        "venda": venda.numero_formatado,
        "status": venda.status,
        "itens": [
            {
                "produto": item.produto_nome,
                "codigo": item.codigo_lido,
                "quantidade": str(item.quantidade),
                "unidade": item.unidade,
                "preco_unitario": str(item.preco_unitario),
                "subtotal": str(item.subtotal),
            }
            for item in itens
        ],
        "pagamentos": [
            {"metodo": pagamento.metodo, "valor": str(pagamento.valor)}
            for pagamento in pagamentos
        ],
        "total": str(venda.total),
        "troco": str(venda.troco),
        "finalizado_em": venda.finalizado_em.isoformat() if venda.finalizado_em else None,
        "cancelado_em": venda.cancelado_em.isoformat() if venda.cancelado_em else None,
        "motivo_cancelamento": venda.motivo_cancelamento,
    }


def enfileirar_venda(venda: Venda, *, evento: str, usuario_id: uuid.UUID) -> list[TrabalhoImpressao]:
    if venda.turno_id is None or venda.turno is None:
        return []
    db.session.flush()
    return enfileirar_evento(
        empresa_id=venda.empresa_id,
        terminal_id=venda.turno.terminal_id,
        evento=evento,
        entidade="venda",
        entidade_id=venda.id,
        payload=_payload_venda(venda),
        usuario_id=usuario_id,
    )


def _trabalho_bloqueado(empresa_id: int, trabalho_id: uuid.UUID) -> TrabalhoImpressao:
    trabalho = db.session.scalar(
        db.select(TrabalhoImpressao)
        .where(
            TrabalhoImpressao.id == trabalho_id,
            TrabalhoImpressao.empresa_id == empresa_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if trabalho is None:
        raise ImpressaoErro("Trabalho de impressão não encontrado nesta empresa.")
    return trabalho


def simular_impressao(
    *, empresa_id: int, trabalho_id: uuid.UUID, versao: int, usuario_id: uuid.UUID
) -> TrabalhoImpressao:
    trabalho = _trabalho_bloqueado(empresa_id, trabalho_id)
    if trabalho.versao != versao:
        raise ImpressaoConflito("O trabalho foi alterado em outra sessão.")
    if trabalho.status != "pendente":
        raise ImpressaoConflito("Este trabalho não está disponível para impressão.")
    trabalho.status = "processando"
    trabalho.iniciado_em = utcnow()
    trabalho.tentativas += 1
    trabalho.versao += 1
    db.session.flush()
    trabalho.status = "impresso"
    trabalho.impresso_em = utcnow()
    trabalho.erro_codigo = None
    trabalho.erro_mensagem = None
    trabalho.falhou_em = None
    trabalho.versao += 1
    registrar(
        "trabalho_impressao_concluido",
        "trabalho_impressao",
        trabalho.id,
        empresa_id,
        {
            "evento": trabalho.evento,
            "impressora_id": str(trabalho.impressora_id),
            "tentativas": trabalho.tentativas,
            "modo": "simulado",
        },
        usuario_id=usuario_id,
    )
    db.session.commit()
    return trabalho


def simular_falha(
    *, empresa_id: int, trabalho_id: uuid.UUID, versao: int, usuario_id: uuid.UUID
) -> TrabalhoImpressao:
    trabalho = _trabalho_bloqueado(empresa_id, trabalho_id)
    if trabalho.versao != versao:
        raise ImpressaoConflito("O trabalho foi alterado em outra sessão.")
    if trabalho.status != "pendente":
        raise ImpressaoConflito("Somente trabalho pendente pode receber falha simulada.")
    trabalho.status = "falhou"
    trabalho.tentativas += 1
    trabalho.erro_codigo = "SIMULACAO_SEM_PAPEL"
    trabalho.erro_mensagem = "Falha controlada: impressora simulada sem papel."
    trabalho.falhou_em = utcnow()
    trabalho.versao += 1
    registrar(
        "trabalho_impressao_falhou",
        "trabalho_impressao",
        trabalho.id,
        empresa_id,
        {"codigo": trabalho.erro_codigo, "tentativas": trabalho.tentativas},
        usuario_id=usuario_id,
    )
    db.session.commit()
    return trabalho


def reprocessar_trabalho(
    *, empresa_id: int, trabalho_id: uuid.UUID, versao: int, usuario_id: uuid.UUID
) -> TrabalhoImpressao:
    trabalho = _trabalho_bloqueado(empresa_id, trabalho_id)
    if trabalho.versao != versao:
        raise ImpressaoConflito("O trabalho foi alterado em outra sessão.")
    if trabalho.status != "falhou":
        raise ImpressaoConflito("Somente trabalho com falha pode ser reprocessado.")
    trabalho.status = "pendente"
    trabalho.erro_codigo = None
    trabalho.erro_mensagem = None
    trabalho.falhou_em = None
    trabalho.versao += 1
    registrar(
        "trabalho_impressao_reprocessado",
        "trabalho_impressao",
        trabalho.id,
        empresa_id,
        {"tentativas_anteriores": trabalho.tentativas},
        usuario_id=usuario_id,
    )
    db.session.commit()
    return trabalho


def criar_reimpressao(
    *,
    empresa_id: int,
    trabalho_id: uuid.UUID,
    motivo: str,
    versao: int,
    usuario_id: uuid.UUID,
) -> TrabalhoImpressao:
    original = _trabalho_bloqueado(empresa_id, trabalho_id)
    if original.versao != versao:
        raise ImpressaoConflito("O trabalho foi alterado em outra sessão.")
    if original.status != "impresso":
        raise ImpressaoConflito(
            "Somente um trabalho concluído pode originar reimpressão."
        )
    motivo_normalizado = _texto(
        motivo, minimo=5, maximo=500, rotulo="O motivo da reimpressão"
    )
    novo_id = uuid.uuid4()
    trabalho = TrabalhoImpressao(
        id=novo_id,
        empresa_id=empresa_id,
        terminal_id=original.terminal_id,
        impressora_id=original.impressora_id,
        regra_id=original.regra_id,
        reimpressao_de_id=original.id,
        evento=original.evento,
        entidade=original.entidade,
        entidade_id=original.entidade_id,
        chave_idempotencia=f"reimpressao:{original.id}:{novo_id}",
        payload=original.payload,
        copias=original.copias,
        motivo_reimpressao=motivo_normalizado,
        solicitado_por_id=usuario_id,
    )
    db.session.add(trabalho)
    original.versao += 1
    db.session.flush()
    registrar(
        "trabalho_impressao_reimpresso",
        "trabalho_impressao",
        trabalho.id,
        empresa_id,
        {"original_id": str(original.id), "motivo": motivo_normalizado},
        usuario_id=usuario_id,
    )
    db.session.commit()
    return trabalho
