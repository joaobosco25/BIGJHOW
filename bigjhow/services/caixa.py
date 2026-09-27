from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import uuid

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (
    CodigoProduto,
    Empresa,
    MovimentoEstoque,
    Produto,
    SaldoEstoque,
    TurnoCaixa,
    Venda,
    VendaItem,
    VendaPagamento,
    utcnow,
)
from .auditoria import registrar
from .balanca import (
    EtiquetaInvalida,
    identificar_etiqueta,
    layout_da_empresa,
    parece_etiqueta,
    quantidade_da_etiqueta,
)
from .codigos import CodigoProdutoInvalido, normalizar_codigo
from .mgv6_integracao import pode_usar_etiqueta


_QTD = Decimal("0.001")
_CENTAVO = Decimal("0.01")
_MAX_QTD = Decimal("999999999999.999")
_MAX_DINHEIRO = Decimal("99999999999999.99")
_QTD_RE = re.compile(r"^\d{1,12}(?:[.,]\d{1,3})?$")
_DINHEIRO_RE = re.compile(r"^\d{1,14}(?:[.,]\d{1,2})?$")
_METODOS = ("dinheiro", "pix", "debito", "credito")


class CaixaErro(ValueError):
    pass


class CaixaConflito(CaixaErro):
    pass


def normalizar_quantidade(valor: str | None, *, pesado: bool) -> Decimal:
    texto = (valor or "").strip()
    if not texto or not _QTD_RE.fullmatch(texto):
        raise CaixaErro("Informe uma quantidade positiva com até três casas decimais.")
    try:
        quantidade = Decimal(texto.replace(",", ".")).quantize(_QTD)
    except InvalidOperation as exc:
        raise CaixaErro("Quantidade inválida.") from exc
    if quantidade <= 0 or quantidade > _MAX_QTD:
        raise CaixaErro("A quantidade precisa ser positiva e estar dentro do limite.")
    if not pesado and quantidade != quantidade.to_integral_value():
        raise CaixaErro("Produtos por unidade aceitam somente quantidades inteiras.")
    return quantidade


def normalizar_dinheiro(valor: str | None, *, obrigatorio: bool = False) -> Decimal | None:
    texto = (valor or "").strip()
    if not texto:
        if obrigatorio:
            raise CaixaErro("Informe os valores de pagamento.")
        return None
    if not _DINHEIRO_RE.fullmatch(texto):
        raise CaixaErro("Use valores monetários positivos com até duas casas decimais.")
    try:
        numero = Decimal(texto.replace(",", ".")).quantize(_CENTAVO)
    except InvalidOperation as exc:
        raise CaixaErro("Valor monetário inválido.") from exc
    if numero <= 0 or numero > _MAX_DINHEIRO:
        raise CaixaErro("O valor monetário precisa ser positivo e estar dentro do limite.")
    return numero


def _subtotal(preco: Decimal, quantidade: Decimal) -> Decimal:
    return (preco * quantidade).quantize(_CENTAVO, rounding=ROUND_HALF_UP)


def _venda_bloqueada(empresa_id: int, venda_id: uuid.UUID) -> Venda:
    venda = db.session.scalar(
        db.select(Venda)
        .where(Venda.id == venda_id, Venda.empresa_id == empresa_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if venda is None:
        raise CaixaErro("Venda não encontrada nesta empresa.")
    return venda


def _exigir_aberta(venda: Venda, versao: int, usuario_id: uuid.UUID) -> TurnoCaixa:
    if venda.status != "aberta":
        raise CaixaConflito("Esta venda não aceita mais alterações.")
    if venda.versao != versao:
        raise CaixaConflito(
            "O caixa foi alterado em outra sessão. Recarregue antes de continuar."
        )
    if venda.turno_id is None:
        raise CaixaConflito(
            "Este atendimento foi aberto antes do controle de turnos. Descarte-o e inicie outro."
        )
    turno = db.session.scalar(
        db.select(TurnoCaixa)
        .where(TurnoCaixa.id == venda.turno_id, TurnoCaixa.empresa_id == venda.empresa_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if turno is None or turno.status != "aberto":
        raise CaixaConflito("O turno deste atendimento não está aberto.")
    if turno.operador_id != usuario_id or venda.operador_id != usuario_id:
        raise CaixaConflito("O atendimento pertence a outro operador ou turno.")
    return turno


def _recalcular_total(venda: Venda) -> None:
    total = db.session.scalar(
        db.select(func.coalesce(func.sum(VendaItem.subtotal), 0)).where(
            VendaItem.venda_id == venda.id
        )
    )
    venda.total = Decimal(total or 0).quantize(_CENTAVO)


def iniciar_venda(*, empresa_id: int, usuario_id: uuid.UUID) -> Venda:
    empresa = db.session.scalar(
        db.select(Empresa)
        .where(Empresa.id == empresa_id, Empresa.ativa.is_(True))
        .with_for_update()
    )
    if empresa is None:
        raise CaixaErro("Empresa não encontrada.")
    turno = db.session.scalar(
        db.select(TurnoCaixa)
        .where(
            TurnoCaixa.empresa_id == empresa_id,
            TurnoCaixa.operador_id == usuario_id,
            TurnoCaixa.status == "aberto",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if turno is None:
        raise CaixaErro("Abra um turno em um terminal antes de iniciar o atendimento.")
    existente = db.session.scalar(
        db.select(Venda).where(
            Venda.empresa_id == empresa_id,
            Venda.operador_id == usuario_id,
            Venda.status == "aberta",
        )
    )
    if existente is not None:
        db.session.rollback()
        return existente

    ultimo = db.session.scalar(
        db.select(func.max(Venda.numero)).where(Venda.empresa_id == empresa_id)
    )
    venda = Venda(
        empresa_id=empresa_id,
        turno_id=turno.id,
        numero=int(ultimo or 0) + 1,
        operador_id=usuario_id,
    )
    db.session.add(venda)
    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        existente = db.session.scalar(
            db.select(Venda).where(
                Venda.empresa_id == empresa_id,
                Venda.operador_id == usuario_id,
                Venda.status == "aberta",
            )
        )
        if existente is not None:
            return existente
        raise CaixaConflito("Não foi possível iniciar a venda. Tente novamente.") from exc
    registrar(
        "venda_iniciada",
        "venda",
        venda.id,
        empresa_id,
        {
            "numero": venda.numero,
            "turno_id": str(turno.id),
            "terminal_id": str(turno.terminal_id),
        },
        usuario_id=usuario_id,
    )
    db.session.commit()
    return venda


def adicionar_item(
    *,
    empresa_id: int,
    venda_id: uuid.UUID,
    versao: int,
    codigo_digitado: str,
    quantidade_digitada: str | None,
    usuario_id: uuid.UUID,
) -> Venda:
    venda = _venda_bloqueada(empresa_id, venda_id)
    _exigir_aberta(venda, versao, usuario_id)
    try:
        codigo_exibicao, chave = normalizar_codigo(codigo_digitado)
    except CodigoProdutoInvalido as exc:
        db.session.rollback()
        raise CaixaErro(str(exc)) from exc

    # Etiqueta de balança muda a cada pesagem: o código lido carrega o PLU e o
    # peso (ou o valor) dentro dele, e nunca vai bater com um código fixo.
    etiqueta = None
    if parece_etiqueta(codigo_exibicao, layout_da_empresa(empresa_id)):
        try:
            codigo_encontrado, etiqueta = identificar_etiqueta(empresa_id, codigo_exibicao)
        except EtiquetaInvalida as exc:
            db.session.rollback()
            raise CaixaErro(str(exc)) from exc
    else:
        codigo_encontrado = db.session.scalar(
            db.select(CodigoProduto)
            .where(
                CodigoProduto.empresa_id == empresa_id,
                CodigoProduto.chave_busca == chave,
                CodigoProduto.ativo.is_(True),
            )
        )
    if codigo_encontrado is None:
        db.session.rollback()
        raise CaixaErro("Código não cadastrado para esta empresa.")
    produto = db.session.scalar(
        db.select(Produto)
        .where(
            Produto.id == codigo_encontrado.produto_id,
            Produto.empresa_id == empresa_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    codigo = db.session.scalar(
        db.select(CodigoProduto)
        .where(
            CodigoProduto.id == codigo_encontrado.id,
            CodigoProduto.empresa_id == empresa_id,
            CodigoProduto.produto_id == codigo_encontrado.produto_id,
            CodigoProduto.ativo.is_(True),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    saldo = db.session.scalar(
        db.select(SaldoEstoque)
        .where(SaldoEstoque.produto_id == codigo_encontrado.produto_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if produto is None or codigo is None or saldo is None or not produto.ativo:
        db.session.rollback()
        raise CaixaErro("O produto deste código está indisponível.")
    if saldo.aguardando_inventario:
        db.session.rollback()
        raise CaixaErro("O produto ainda aguarda inventário e não pode ser vendido.")
    preco = Decimal(produto.preco_venda).quantize(_CENTAVO)
    if preco <= 0:
        db.session.rollback()
        raise CaixaErro("O produto está sem preço de venda válido.")

    leitura_balanca = None
    if etiqueta is not None:
        # Etiqueta de balança representa peso/valor de pesagem. Nunca permita
        # que uma etiqueta converta silenciosamente um produto discreto (UN,
        # CXA etc.) em quantidade fracionada. O cadastro precisa declarar o
        # produto como vendido por peso antes de aceitar a etiqueta.
        if not produto.pesado:
            db.session.rollback()
            raise CaixaErro(
                "Esta etiqueta pertence a um produto que não está marcado como vendido por peso."
            )
        permitido, motivo_bloqueio = pode_usar_etiqueta(produto)
        if not permitido:
            db.session.rollback()
            raise CaixaErro(motivo_bloqueio or "Preço da balança ainda não foi sincronizado.")
        # O peso sai daqui, com o preço já travado — e o que o operador tiver
        # digitado no campo de quantidade é ignorado: quem pesou foi a balança.
        try:
            leitura_balanca = quantidade_da_etiqueta(empresa_id, etiqueta, preco)
        except EtiquetaInvalida as exc:
            db.session.rollback()
            raise CaixaErro(str(exc)) from exc
        quantidade = leitura_balanca.quantidade
    elif produto.pesado:
        quantidade = normalizar_quantidade(quantidade_digitada, pesado=True)
    elif (quantidade_digitada or "").strip():
        quantidade = normalizar_quantidade(quantidade_digitada, pesado=False)
    else:
        quantidade = Decimal("1.000")

    item = db.session.scalar(
        db.select(VendaItem)
        .where(
            VendaItem.venda_id == venda.id,
            VendaItem.produto_id == produto.id,
        )
        .with_for_update()
    )
    quantidade_final = quantidade + (Decimal(item.quantidade) if item else Decimal("0"))
    quantidade_final = quantidade_final.quantize(_QTD)
    if quantidade_final > Decimal(saldo.quantidade).quantize(_QTD):
        db.session.rollback()
        raise CaixaErro("Quantidade maior que o saldo disponível.")

    if item is None:
        item = VendaItem(
            venda_id=venda.id,
            produto_id=produto.id,
            codigo_produto_id=codigo.id,
            produto_nome=produto.nome,
            sku_interno=produto.sku_interno,
            codigo_lido=codigo_exibicao,
            unidade=produto.unidade,
            pesado=produto.pesado,
            preco_unitario=preco,
            quantidade=quantidade_final,
            subtotal=_subtotal(preco, quantidade_final),
        )
        db.session.add(item)
        acao = "venda_item_adicionado"
    else:
        if Decimal(item.preco_unitario).quantize(_CENTAVO) != preco:
            db.session.rollback()
            raise CaixaConflito(
                "O preço mudou desde a leitura anterior. Remova o item e leia novamente."
            )
        item.codigo_produto_id = codigo.id
        item.codigo_lido = codigo_exibicao
        item.quantidade = quantidade_final
        item.subtotal = _subtotal(preco, quantidade_final)
        acao = "venda_item_atualizado"

    db.session.flush()
    _recalcular_total(venda)
    venda.versao += 1
    detalhes = {
        "numero": venda.numero,
        "produto_id": str(produto.id),
        "quantidade": str(quantidade_final),
        "subtotal": str(item.subtotal),
    }
    if leitura_balanca is not None:
        detalhes["balanca"] = {
            "etiqueta": codigo_exibicao,
            "plu": etiqueta.plu,
            "conteudo": etiqueta.conteudo,
            "quantidade_lida": str(leitura_balanca.quantidade),
            "valor_etiqueta": (
                str(leitura_balanca.valor_etiqueta)
                if leitura_balanca.valor_etiqueta is not None
                else None
            ),
            "divergencia": str(leitura_balanca.divergencia),
        }
    registrar(acao, "venda", venda.id, empresa_id, detalhes, usuario_id=usuario_id)
    db.session.commit()
    return venda


def atualizar_item(
    *,
    empresa_id: int,
    venda_id: uuid.UUID,
    item_id: uuid.UUID,
    versao: int,
    quantidade_digitada: str,
    usuario_id: uuid.UUID,
) -> Venda:
    venda = _venda_bloqueada(empresa_id, venda_id)
    _exigir_aberta(venda, versao, usuario_id)
    item = db.session.scalar(
        db.select(VendaItem)
        .where(VendaItem.id == item_id, VendaItem.venda_id == venda.id)
        .with_for_update()
    )
    if item is None:
        db.session.rollback()
        raise CaixaErro("Item não encontrado nesta venda.")
    produto = db.session.scalar(
        db.select(Produto)
        .where(Produto.id == item.produto_id, Produto.empresa_id == empresa_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    saldo = db.session.scalar(
        db.select(SaldoEstoque)
        .where(SaldoEstoque.produto_id == item.produto_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if produto is None or saldo is None or not produto.ativo:
        db.session.rollback()
        raise CaixaErro("Produto indisponível.")
    quantidade = normalizar_quantidade(quantidade_digitada, pesado=item.pesado)
    if quantidade > Decimal(saldo.quantidade).quantize(_QTD):
        db.session.rollback()
        raise CaixaErro("Quantidade maior que o saldo disponível.")
    preco_atual = Decimal(produto.preco_venda).quantize(_CENTAVO)
    if preco_atual != Decimal(item.preco_unitario).quantize(_CENTAVO):
        db.session.rollback()
        raise CaixaConflito(
            "O preço mudou. Remova o item e faça uma nova leitura antes de finalizar."
        )
    item.quantidade = quantidade
    item.subtotal = _subtotal(preco_atual, quantidade)
    _recalcular_total(venda)
    venda.versao += 1
    registrar(
        "venda_item_atualizado",
        "venda",
        venda.id,
        empresa_id,
        {
            "numero": venda.numero,
            "produto_id": str(produto.id),
            "quantidade": str(quantidade),
            "subtotal": str(item.subtotal),
        },
        usuario_id=usuario_id,
    )
    db.session.commit()
    return venda


def remover_item(
    *,
    empresa_id: int,
    venda_id: uuid.UUID,
    item_id: uuid.UUID,
    versao: int,
    usuario_id: uuid.UUID,
) -> Venda:
    venda = _venda_bloqueada(empresa_id, venda_id)
    _exigir_aberta(venda, versao, usuario_id)
    item = db.session.scalar(
        db.select(VendaItem)
        .where(VendaItem.id == item_id, VendaItem.venda_id == venda.id)
        .with_for_update()
    )
    if item is None:
        db.session.rollback()
        raise CaixaErro("Item não encontrado nesta venda.")
    detalhes = {
        "numero": venda.numero,
        "produto_id": str(item.produto_id),
        "quantidade": str(item.quantidade),
    }
    db.session.delete(item)
    db.session.flush()
    _recalcular_total(venda)
    venda.versao += 1
    registrar(
        "venda_item_removido",
        "venda",
        venda.id,
        empresa_id,
        detalhes,
        usuario_id=usuario_id,
    )
    db.session.commit()
    return venda


def _preparar_pagamentos(
    valores: dict[str, tuple[str | None, str | None]], total: Decimal
) -> tuple[list[dict], Decimal]:
    pagamentos = []
    for metodo in _METODOS:
        valor_texto, recebido_texto = valores.get(metodo, (None, None))
        valor = normalizar_dinheiro(valor_texto)
        if valor is None:
            if (recebido_texto or "").strip():
                raise CaixaErro("Informe também o valor pago em dinheiro.")
            continue
        if metodo == "dinheiro":
            recebido = normalizar_dinheiro(recebido_texto) or valor
            if recebido < valor:
                raise CaixaErro("O valor recebido em dinheiro é menor que o valor aplicado.")
        else:
            if (recebido_texto or "").strip():
                raise CaixaErro("Valor recebido adicional é permitido somente em dinheiro.")
            recebido = valor
        pagamentos.append(
            {"metodo": metodo, "valor": valor, "valor_recebido": recebido}
        )
    if not pagamentos:
        raise CaixaErro("Informe ao menos uma forma de pagamento.")
    soma = sum((item["valor"] for item in pagamentos), Decimal("0")).quantize(
        _CENTAVO
    )
    if soma != total:
        raise CaixaErro(
            f"A soma dos pagamentos deve ser exatamente R$ {total:.2f}."
        )
    troco = sum(
        (
            item["valor_recebido"] - item["valor"]
            for item in pagamentos
            if item["metodo"] == "dinheiro"
        ),
        Decimal("0"),
    ).quantize(_CENTAVO)
    return pagamentos, troco


def finalizar_venda(
    *,
    empresa_id: int,
    venda_id: uuid.UUID,
    versao: int,
    pagamentos_digitados: dict[str, tuple[str | None, str | None]],
    usuario_id: uuid.UUID,
) -> tuple[Venda, bool]:
    venda = _venda_bloqueada(empresa_id, venda_id)
    if venda.status == "finalizada":
        db.session.rollback()
        return venda, False
    _exigir_aberta(venda, versao, usuario_id)
    itens = db.session.scalars(
        db.select(VendaItem)
        .where(VendaItem.venda_id == venda.id)
        .order_by(VendaItem.produto_id)
        .with_for_update()
    ).all()
    if not itens:
        db.session.rollback()
        raise CaixaErro("Adicione ao menos um produto antes de finalizar.")
    total_recalculado = sum(
        (Decimal(item.subtotal) for item in itens), Decimal("0")
    ).quantize(_CENTAVO)
    if total_recalculado <= 0 or total_recalculado != Decimal(venda.total).quantize(
        _CENTAVO
    ):
        db.session.rollback()
        raise CaixaConflito("O total da venda está inconsistente. Recarregue o caixa.")
    # ======================================================
    # GATE FISCAL DE PRODUÇÃO — ANTES DO PAGAMENTO.
    #
    # Empresa 1 só pode concluir venda com produtos
    # explicitamente presentes na allowlist fiscal.
    #
    # Esta etapa não transmite NFC-e, não reserva número,
    # não altera estoque e não grava pagamento.
    # ======================================================
    if int(empresa_id) == 1:
        from .fiscal import legado_producao
        from .fiscal.xml_nfce import XmlFiscalErro

        try:
            legado_producao.prevalidar_produtos(
                [item.produto_id for item in itens]
            )
        except XmlFiscalErro as exc:
            db.session.rollback()
            raise CaixaErro(
                "Venda bloqueada antes do pagamento: "
                f"{exc}"
            ) from exc

    pagamentos, troco = _preparar_pagamentos(
        pagamentos_digitados, total_recalculado
    )

    produto_ids = [item.produto_id for item in itens]
    registros = db.session.execute(
        db.select(Produto, SaldoEstoque)
        .join(SaldoEstoque, SaldoEstoque.produto_id == Produto.id)
        .where(Produto.id.in_(produto_ids), Produto.empresa_id == empresa_id)
        .order_by(Produto.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    por_produto = {produto.id: (produto, saldo) for produto, saldo in registros}
    codigos_ids = sorted(
        {item.codigo_produto_id for item in itens if item.codigo_produto_id is not None},
        key=str,
    )
    codigos_ativos = dict(
        db.session.execute(
            db.select(CodigoProduto.id, CodigoProduto.produto_id)
            .where(
                CodigoProduto.id.in_(codigos_ids),
                CodigoProduto.empresa_id == empresa_id,
                CodigoProduto.ativo.is_(True),
            )
            .order_by(CodigoProduto.id)
            .with_for_update()
        ).all()
    )
    conflitos = []
    for item in itens:
        registro = por_produto.get(item.produto_id)
        if (
            registro is None
            or codigos_ativos.get(item.codigo_produto_id) != item.produto_id
        ):
            conflitos.append(f"{item.produto_nome}: produto ou código indisponível")
            continue
        produto, saldo = registro
        quantidade = Decimal(item.quantidade).quantize(_QTD)
        if quantidade <= 0:
            conflitos.append(f"{item.produto_nome}: quantidade inválida")
        elif not bool(item.pesado) and quantidade != quantidade.to_integral_value():
            # Defesa em profundidade: mesmo que um dado antigo/corrompido tenha
            # criado fração para produto por unidade, a venda não pode baixar
            # estoque nem seguir para emissão fiscal.
            conflitos.append(f"{item.produto_nome}: produto por unidade com quantidade fracionada")
        elif not produto.ativo:
            conflitos.append(f"{item.produto_nome}: produto arquivado")
        elif saldo.aguardando_inventario:
            conflitos.append(f"{item.produto_nome}: inventário pendente")
        elif produto.unidade != item.unidade or produto.pesado != item.pesado:
            conflitos.append(f"{item.produto_nome}: cadastro alterado")
        elif Decimal(produto.preco_venda).quantize(_CENTAVO) != Decimal(
            item.preco_unitario
        ).quantize(_CENTAVO):
            conflitos.append(f"{item.produto_nome}: preço alterado")
        elif Decimal(saldo.quantidade).quantize(_QTD) < quantidade:
            conflitos.append(f"{item.produto_nome}: saldo insuficiente")
    if conflitos:
        db.session.rollback()
        resumo = "; ".join(conflitos[:3])
        sufixo = "…" if len(conflitos) > 3 else ""
        raise CaixaConflito(
            f"A venda não foi finalizada: {resumo}{sufixo}. Nenhum saldo foi alterado."
        )

    agora = utcnow()
    for item in itens:
        produto, saldo = por_produto[item.produto_id]
        anterior = Decimal(saldo.quantidade).quantize(_QTD)
        quantidade = Decimal(item.quantidade).quantize(_QTD)
        final = anterior - quantidade
        saldo.quantidade = final
        movimento = MovimentoEstoque(
            empresa_id=empresa_id,
            produto_id=produto.id,
            usuario_id=usuario_id,
            tipo="saida",
            quantidade_anterior=anterior,
            variacao=-quantidade,
            quantidade_final=final,
            motivo=f"Venda não fiscal #{venda.numero_formatado}",
        )
        db.session.add(movimento)
        db.session.flush()
        item.movimento_saida_id = movimento.id

    for pagamento in pagamentos:
        db.session.add(VendaPagamento(venda_id=venda.id, **pagamento))
    venda.status = "finalizada"
    venda.total = total_recalculado
    venda.troco = troco
    venda.finalizado_por_id = usuario_id
    venda.finalizado_em = agora
    venda.versao += 1
    db.session.flush()
    from .impressao import enfileirar_venda

    trabalhos = enfileirar_venda(
        venda, evento="venda_finalizada", usuario_id=usuario_id
    )
    registrar(
        "venda_finalizada",
        "venda",
        venda.id,
        empresa_id,
        {
            "numero": venda.numero,
            "total": str(total_recalculado),
            "troco": str(troco),
            "itens": len(itens),
            "pagamentos": [
                {"metodo": item["metodo"], "valor": str(item["valor"])}
                for item in pagamentos
            ],
            "trabalhos_impressao": len(trabalhos),
        },
        usuario_id=usuario_id,
    )
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise CaixaConflito(
            "A venda não pôde ser finalizada por conflito. Nenhum saldo foi alterado."
        ) from exc
    return venda, True


def descartar_venda(
    *,
    empresa_id: int,
    venda_id: uuid.UUID,
    versao: int,
    usuario_id: uuid.UUID,
) -> tuple[Venda, bool]:
    venda = _venda_bloqueada(empresa_id, venda_id)
    if venda.status == "descartada":
        db.session.rollback()
        return venda, False
    if venda.turno_id is None:
        if venda.status != "aberta" or venda.versao != versao:
            raise CaixaConflito(
                "O atendimento legado foi alterado. Recarregue antes de descartar."
            )
        if venda.operador_id != usuario_id:
            raise CaixaConflito("O atendimento legado pertence a outro operador.")
    else:
        _exigir_aberta(venda, versao, usuario_id)
    venda.status = "descartada"
    venda.descartado_em = utcnow()
    venda.versao += 1
    registrar(
        "venda_descartada",
        "venda",
        venda.id,
        empresa_id,
        {"numero": venda.numero, "itens": len(venda.itens), "total": str(venda.total)},
        usuario_id=usuario_id,
    )
    db.session.commit()
    return venda, True


def cancelar_venda(
    *,
    empresa_id: int,
    venda_id: uuid.UUID,
    versao: int,
    motivo: str,
    usuario_id: uuid.UUID,
) -> tuple[Venda, bool]:
    motivo = " ".join((motivo or "").split())
    if not 5 <= len(motivo) <= 500:
        raise CaixaErro("Informe um motivo de cancelamento entre 5 e 500 caracteres.")
    venda = _venda_bloqueada(empresa_id, venda_id)
    if venda.status == "cancelada":
        db.session.rollback()
        return venda, False
    if venda.status != "finalizada":
        db.session.rollback()
        raise CaixaErro("Somente uma venda finalizada pode ser cancelada.")
    if venda.versao != versao:
        db.session.rollback()
        raise CaixaConflito(
            "A venda foi alterada em outra sessão. Recarregue antes de cancelar."
        )
    if venda.turno_id is not None:
        turno = db.session.scalar(
            db.select(TurnoCaixa)
            .where(
                TurnoCaixa.id == venda.turno_id,
                TurnoCaixa.empresa_id == empresa_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if turno is None or turno.status != "aberto":
            raise CaixaErro(
                "Venda de turno fechado não pode ser cancelada nesta etapa. Registre uma ocorrência administrativa."
            )
    itens = db.session.scalars(
        db.select(VendaItem)
        .where(VendaItem.venda_id == venda.id)
        .order_by(VendaItem.produto_id)
        .with_for_update()
    ).all()
    registros = db.session.execute(
        db.select(Produto, SaldoEstoque)
        .join(SaldoEstoque, SaldoEstoque.produto_id == Produto.id)
        .where(
            Produto.id.in_([item.produto_id for item in itens]),
            Produto.empresa_id == empresa_id,
        )
        .order_by(Produto.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    por_produto = {produto.id: (produto, saldo) for produto, saldo in registros}
    if len(por_produto) != len({item.produto_id for item in itens}):
        db.session.rollback()
        raise CaixaConflito(
            "O cancelamento foi bloqueado por inconsistência de estoque."
        )
    for item in itens:
        produto, saldo = por_produto[item.produto_id]
        anterior = Decimal(saldo.quantidade).quantize(_QTD)
        quantidade = Decimal(item.quantidade).quantize(_QTD)
        final = anterior + quantidade
        saldo.quantidade = final
        movimento = MovimentoEstoque(
            empresa_id=empresa_id,
            produto_id=produto.id,
            usuario_id=usuario_id,
            tipo="entrada",
            quantidade_anterior=anterior,
            variacao=quantidade,
            quantidade_final=final,
            motivo=f"Cancelamento da venda #{venda.numero_formatado}: {motivo}",
        )
        db.session.add(movimento)
        db.session.flush()
        item.movimento_estorno_id = movimento.id
    venda.status = "cancelada"
    venda.cancelado_por_id = usuario_id
    venda.cancelado_em = utcnow()
    venda.motivo_cancelamento = motivo
    venda.versao += 1
    db.session.flush()
    from .impressao import enfileirar_venda

    trabalhos = enfileirar_venda(
        venda, evento="venda_cancelada", usuario_id=usuario_id
    )
    registrar(
        "venda_cancelada",
        "venda",
        venda.id,
        empresa_id,
        {
            "numero": venda.numero,
            "total": str(venda.total),
            "itens_estornados": len(itens),
            "motivo": motivo,
            "trabalhos_impressao": len(trabalhos),
        },
        usuario_id=usuario_id,
    )
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise CaixaConflito(
            "O cancelamento encontrou um conflito. Nenhum saldo foi alterado."
        ) from exc
    return venda, True
