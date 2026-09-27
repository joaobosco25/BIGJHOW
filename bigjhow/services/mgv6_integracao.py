from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from flask import current_app

from ..extensions import db
from ..models import IntegracaoMGV6Produto, Produto, utcnow
from .auditoria import registrar
from .mgv6_cliente import ErroMGV6, cliente_de_configuracao


class VinculoMGV6Invalido(ValueError):
    pass


def integracao_ativa() -> bool:
    return bool(current_app.config.get("MGV6_ENABLED"))


def escrita_ativa() -> bool:
    return bool(
        current_app.config.get("MGV6_ENABLED")
        and current_app.config.get("MGV6_WRITE_ENABLED")
    )


def _normalizar_nome(valor: str | None) -> str:
    texto = unicodedata.normalize("NFKD", valor or "")
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", texto.upper()).split())


def _descricao_item(item: dict) -> str:
    for chave in ("Descritivo1aLinha", "Descritivo1Linha", "Descricao", "Descritivo", "Nome"):
        valor = item.get(chave)
        if valor:
            return " ".join(str(valor).split())[:240]
    return ""


def _preco_item(item: dict) -> Decimal:
    try:
        return Decimal(str(item.get("Preco"))).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise VinculoMGV6Invalido("MGV6 não informou um preço válido para o item.") from exc


def _balancas_associadas_item(item: dict):
    # A resposta real do MGV6 1.6.217 usa "ListaDeBalancaAssociadas"
    # (Balanca no singular). Mantemos também a variante plural por
    # compatibilidade com outras builds/documentações.
    for chave in ("ListaDeBalancaAssociadas", "ListaDeBalancasAssociadas"):
        if chave in item:
            return item.get(chave)
    return None


def _codigo_item(item: dict) -> str:
    valor = str(item.get("Codigo") or "").strip()
    if not valor.isdigit():
        raise VinculoMGV6Invalido("MGV6 devolveu um código de item inválido.")
    return str(int(valor))


def _mensagem(linha: str) -> str:
    return " ".join(linha.split())[:500]


def atualizar_vinculo(
    produto: Produto,
    codigo_item: str | None,
    *,
    usuario_id=None,
) -> IntegracaoMGV6Produto | None:
    codigo = (codigo_item or "").strip()
    atual = produto.mgv6
    if not codigo:
        if atual is not None:
            registrar(
                "mgv6_vinculo_removido",
                "integracao_mgv6_produto",
                produto.id,
                produto.empresa_id,
                {"codigo_item": atual.codigo_item},
                usuario_id=usuario_id,
            )
            db.session.delete(atual)
        return None

    if not codigo.isdigit() or not (1 <= len(codigo) <= 6):
        raise VinculoMGV6Invalido("Código MGV6 deve conter de 1 a 6 dígitos.")
    codigo = str(int(codigo))

    duplicado = db.session.scalar(
        db.select(IntegracaoMGV6Produto).where(
            IntegracaoMGV6Produto.empresa_id == produto.empresa_id,
            IntegracaoMGV6Produto.codigo_item == codigo,
            IntegracaoMGV6Produto.produto_id != produto.id,
        )
    )
    if duplicado is not None:
        raise VinculoMGV6Invalido(
            f"O item MGV6 {codigo} já está vinculado a outro produto desta empresa."
        )

    if atual is None:
        atual = IntegracaoMGV6Produto(
            produto=produto,
            empresa_id=produto.empresa_id,
            codigo_item=codigo,
            status="pendente",
            atualizado_por_id=usuario_id,
        )
        db.session.add(atual)
    elif atual.codigo_item != codigo:
        anterior = atual.codigo_item
        atual.codigo_item = codigo
        atual.descricao_mgv = None
        atual.preco_mgv = None
        atual.preco_enviado = None
        atual.numero_importacao = None
        atual.status = "pendente"
        atual.ultima_mensagem = None
        atual.validado_em = None
        atual.sincronizado_mgv_em = None
        atual.carga_solicitada_em = None
        atual.balanca_confirmada_em = None
        atual.versao += 1
        atual.atualizado_por_id = usuario_id
        registrar(
            "mgv6_vinculo_alterado",
            "integracao_mgv6_produto",
            produto.id,
            produto.empresa_id,
            {"antes": anterior, "depois": codigo},
            usuario_id=usuario_id,
        )
    return atual


def _validar_item_do_mgv(produto: Produto, vinculo: IntegracaoMGV6Produto) -> tuple[dict, Decimal]:
    cliente = cliente_de_configuracao(current_app.config)
    item = cliente.consultar_item(vinculo.codigo_item)
    codigo_recebido = _codigo_item(item)
    if codigo_recebido != str(int(vinculo.codigo_item)):
        raise VinculoMGV6Invalido(
            f"MGV6 respondeu com o item {codigo_recebido}, diferente do vínculo {vinculo.codigo_item}."
        )
    descricao = _descricao_item(item)
    if not descricao:
        raise VinculoMGV6Invalido("MGV6 não informou a descrição do item.")
    if _normalizar_nome(descricao) != _normalizar_nome(produto.nome):
        raise VinculoMGV6Invalido(
            f"O item MGV6 {vinculo.codigo_item} é '{descricao}', não '{produto.nome}'."
        )

    # Produtos pesados do Big Jhow só podem apontar para um tipo de venda
    # compatível com peso no MGV6. Isso impede vincular, por engano, um item
    # vendido por unidade que tenha descrição semelhante.
    if bool(produto.pesado):
        try:
            tipo_venda = int(item.get("TipoVendaInt"))
        except (TypeError, ValueError) as exc:
            raise VinculoMGV6Invalido(
                "MGV6 não informou o tipo de venda do item."
            ) from exc
        if tipo_venda not in {0, 2, 3, 4}:
            raise VinculoMGV6Invalido(
                f"O item MGV6 {vinculo.codigo_item} não está configurado para venda por peso."
            )

        balanca_alvo = current_app.config.get("MGV6_BALANCA")
        associadas = _balancas_associadas_item(item)
        if balanca_alvo is not None:
            if not isinstance(associadas, list):
                raise VinculoMGV6Invalido(
                    "MGV6 não informou as balanças associadas ao item."
                )
            try:
                codigos_balancas = {int(valor) for valor in associadas}
            except (TypeError, ValueError) as exc:
                raise VinculoMGV6Invalido(
                    "MGV6 devolveu uma associação de balança inválida."
                ) from exc
            if int(balanca_alvo) not in codigos_balancas:
                raise VinculoMGV6Invalido(
                    f"O item MGV6 {vinculo.codigo_item} não está associado à balança {balanca_alvo}."
                )

    return item, _preco_item(item)


def validar_vinculo(produto: Produto, *, usuario_id=None) -> IntegracaoMGV6Produto:
    vinculo = produto.mgv6
    if vinculo is None:
        raise VinculoMGV6Invalido("Produto não possui código MGV6 cadastrado.")
    vinculo.ultima_tentativa_em = utcnow()
    try:
        item, preco = _validar_item_do_mgv(produto, vinculo)
    except (ErroMGV6, VinculoMGV6Invalido) as exc:
        vinculo.status = "erro"
        vinculo.ultima_mensagem = _mensagem(str(exc))
        vinculo.atualizado_por_id = usuario_id
        vinculo.versao += 1
        registrar(
            "mgv6_vinculo_falhou",
            "integracao_mgv6_produto",
            produto.id,
            produto.empresa_id,
            {"codigo_item": vinculo.codigo_item, "erro": vinculo.ultima_mensagem},
            usuario_id=usuario_id,
        )
        db.session.commit()
        raise

    vinculo.descricao_mgv = _descricao_item(item)
    vinculo.preco_mgv = preco
    vinculo.validado_em = utcnow()
    preco_bigjhow = Decimal(produto.preco_venda).quantize(Decimal("0.01"))
    precos_iguais = preco == preco_bigjhow
    if precos_iguais and vinculo.balanca_confirmada_em is not None:
        vinculo.status = "mgv_ok"
        vinculo.ultima_mensagem = "Vínculo e preço confirmados; balança já conferida fisicamente."
    elif precos_iguais:
        vinculo.status = "validado"
        vinculo.ultima_mensagem = (
            "Vínculo confirmado e preço igual ao Big Jhow. "
            "Confira o preço fisicamente na Prix antes de liberar etiquetas."
        )
    else:
        vinculo.status = "validado"
        vinculo.balanca_confirmada_em = None
        vinculo.ultima_mensagem = (
            f"Vínculo confirmado; MGV6 está em R$ {preco:.2f} e "
            f"Big Jhow em R$ {preco_bigjhow:.2f}."
        )
    vinculo.atualizado_por_id = usuario_id
    vinculo.versao += 1
    registrar(
        "mgv6_vinculo_validado",
        "integracao_mgv6_produto",
        produto.id,
        produto.empresa_id,
        {
            "codigo_item": vinculo.codigo_item,
            "descricao_mgv": vinculo.descricao_mgv,
            "preco_mgv": str(preco),
            "preco_bigjhow": str(produto.preco_venda),
        },
        usuario_id=usuario_id,
    )
    db.session.commit()
    return vinculo


def sincronizar_preco(produto: Produto, *, usuario_id=None) -> IntegracaoMGV6Produto:
    vinculo = produto.mgv6
    if vinculo is None:
        raise VinculoMGV6Invalido("Produto não possui código MGV6 cadastrado.")
    if not escrita_ativa():
        raise VinculoMGV6Invalido(
            "Escrita no MGV6 está desativada. Ative MGV6_WRITE_ENABLED somente "
            "quando nenhum sistema legado estiver importando preços simultaneamente."
        )

    vinculo.ultima_tentativa_em = utcnow()
    alvo = Decimal(produto.preco_venda).quantize(Decimal("0.01"))
    try:
        # Preflight impede que um código digitado errado altere outro produto.
        _validar_item_do_mgv(produto, vinculo)
        cliente = cliente_de_configuracao(current_app.config)
        resultado = cliente.sincronizar_preco(vinculo.codigo_item, alvo)
    except (ErroMGV6, VinculoMGV6Invalido) as exc:
        vinculo.status = "erro"
        vinculo.preco_enviado = alvo
        vinculo.ultima_mensagem = _mensagem(str(exc))
        vinculo.atualizado_por_id = usuario_id
        vinculo.versao += 1
        registrar(
            "mgv6_preco_falhou",
            "integracao_mgv6_produto",
            produto.id,
            produto.empresa_id,
            {
                "codigo_item": vinculo.codigo_item,
                "preco": str(alvo),
                "erro": vinculo.ultima_mensagem,
            },
            usuario_id=usuario_id,
        )
        db.session.commit()
        raise

    agora = utcnow()
    vinculo.preco_mgv = resultado.preco_confirmado
    vinculo.preco_enviado = alvo
    vinculo.numero_importacao = resultado.numero_importacao
    vinculo.sincronizado_mgv_em = agora
    vinculo.validado_em = vinculo.validado_em or agora
    vinculo.balanca_confirmada_em = None
    vinculo.descricao_mgv = vinculo.descricao_mgv or produto.nome
    vinculo.atualizado_por_id = usuario_id
    vinculo.versao += 1

    if resultado.aviso_carga:
        vinculo.status = "erro"
        vinculo.ultima_mensagem = _mensagem(
            "Preço confirmado no MGV6, mas a carga da balança não foi solicitada: "
            + resultado.aviso_carga
        )
    elif resultado.carga_solicitada:
        vinculo.status = "aguardando_balanca"
        vinculo.carga_solicitada_em = agora
        vinculo.ultima_mensagem = (
            "Preço confirmado no MGV6 e carga de itens alterados solicitada. "
            "Confira a balança antes de liberar novas etiquetas."
        )
    else:
        vinculo.status = "validado"
        vinculo.ultima_mensagem = (
            "Preço confirmado no MGV6. Confira fisicamente a Prix antes de liberar etiquetas."
        )

    registrar(
        "mgv6_preco_sincronizado",
        "integracao_mgv6_produto",
        produto.id,
        produto.empresa_id,
        {
            "codigo_item": vinculo.codigo_item,
            "preco": str(alvo),
            "numero_importacao": resultado.numero_importacao,
            "carga_solicitada": resultado.carga_solicitada,
            "aviso_carga": resultado.aviso_carga,
        },
        usuario_id=usuario_id,
    )
    db.session.commit()
    return vinculo


def confirmar_balanca(produto: Produto, *, usuario_id=None) -> IntegracaoMGV6Produto:
    vinculo = produto.mgv6
    if vinculo is None:
        raise VinculoMGV6Invalido("Produto não possui vínculo MGV6.")
    if vinculo.status not in {"validado", "aguardando_balanca", "mgv_ok"}:
        raise VinculoMGV6Invalido(
            "Valide e sincronize o preço no MGV6 antes de confirmar a balança."
        )

    # Reconsulta o MGV6 no instante da confirmação para não confiar em um valor
    # armazenado anteriormente. A confirmação física só é aceita se o MGV6
    # ainda estiver com o mesmo preço do Big Jhow.
    item, preco_atual = _validar_item_do_mgv(produto, vinculo)
    preco_bigjhow = Decimal(produto.preco_venda).quantize(Decimal("0.01"))
    if preco_atual != preco_bigjhow:
        vinculo.preco_mgv = preco_atual
        vinculo.status = "validado"
        vinculo.balanca_confirmada_em = None
        vinculo.ultima_mensagem = (
            f"MGV6 está em R$ {preco_atual:.2f}; Big Jhow está em R$ {preco_bigjhow:.2f}."
        )
        db.session.commit()
        raise VinculoMGV6Invalido(
            "O preço atual do MGV6 não é igual ao preço do Big Jhow."
        )

    vinculo.descricao_mgv = _descricao_item(item)
    vinculo.preco_mgv = preco_atual
    vinculo.status = "mgv_ok"
    vinculo.balanca_confirmada_em = utcnow()
    vinculo.ultima_mensagem = "Preço conferido fisicamente na balança."
    vinculo.atualizado_por_id = usuario_id
    vinculo.versao += 1
    registrar(
        "mgv6_balanca_confirmada",
        "integracao_mgv6_produto",
        produto.id,
        produto.empresa_id,
        {"codigo_item": vinculo.codigo_item, "preco": str(vinculo.preco_mgv)},
        usuario_id=usuario_id,
    )
    db.session.commit()
    return vinculo


def pode_usar_etiqueta(produto: Produto) -> tuple[bool, str | None]:
    """Proteção contra etiqueta calculada com preço antigo da Prix.

    Produtos sem vínculo continuam no fluxo legado. Assim a integração pode ser
    ativada gradualmente sem parar todos os itens do açougue. Porém, a partir do
    momento em que um produto é vinculado ao MGV6, um preço pendente/erro bloqueia
    a etiqueta até a conferência da balança.
    """
    if not integracao_ativa() or produto.mgv6 is None:
        return True, None
    vinculo = produto.mgv6
    if vinculo.preco_mgv is None or Decimal(vinculo.preco_mgv).quantize(Decimal("0.01")) != Decimal(produto.preco_venda).quantize(Decimal("0.01")):
        return False, (
            "Preço deste produto ainda não foi confirmado no MGV6. "
            "Sincronize a balança antes de vender esta etiqueta."
        )
    if vinculo.status == "aguardando_balanca":
        return False, (
            "O MGV6 recebeu o novo preço, mas a balança ainda não foi confirmada. "
            "Confira o preço no visor e confirme a sincronização."
        )
    if vinculo.status in {"pendente", "validado", "erro"}:
        return False, vinculo.ultima_mensagem or "Integração MGV6 pendente."
    return True, None


def testar_conexao() -> dict:
    cliente = cliente_de_configuracao(current_app.config)
    versao = cliente.versao()
    lojas = cliente.consultar_lojas()
    if cliente.loja not in lojas:
        raise VinculoMGV6Invalido(
            f"Loja MGV6 {cliente.loja} não apareceu em ConsultaLojas."
        )
    detalhes = cliente.detalhes_balancas()
    pendentes = cliente.comunicacoes_pendentes()
    return {
        "versao": versao,
        "loja": cliente.loja,
        "balanca": cliente.balanca,
        "quantidade_balancas": len(detalhes),
        "comunicacoes_pendentes": len(pendentes),
    }
