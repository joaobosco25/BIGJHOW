from __future__ import annotations

import re
import unicodedata
import uuid

from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import CodigoProduto, Produto, utcnow
from .auditoria import registrar


_CODIGO_RE = re.compile(r"^[A-Z0-9._/-]{3,80}$")
_TIPOS = {"ean", "interno", "plu", "outro"}


class CodigoProdutoInvalido(ValueError):
    pass


class CodigoProdutoConflitante(ValueError):
    pass


def normalizar_codigo(valor: str | None) -> tuple[str, str]:
    codigo = unicodedata.normalize("NFKC", valor or "").strip().upper()
    if not _CODIGO_RE.fullmatch(codigo):
        raise CodigoProdutoInvalido(
            "Use de 3 a 80 caracteres: letras, números, ponto, hífen, barra ou sublinhado."
        )
    chave = re.sub(r"[^A-Z0-9]", "", codigo)
    if not 3 <= len(chave) <= 80:
        raise CodigoProdutoInvalido("O código não possui caracteres suficientes.")
    return codigo, chave


def _ean_valido(chave: str) -> bool:
    if not chave.isdigit() or len(chave) not in {8, 12, 13, 14}:
        return False
    digitos = [int(item) for item in chave]
    dados = digitos[:-1]
    total = sum(
        digito * (3 if (len(dados) - indice) % 2 else 1)
        for indice, digito in enumerate(dados)
    )
    return (10 - total % 10) % 10 == digitos[-1]


def validar_tipo(tipo: str, chave: str) -> str:
    tipo = (tipo or "").strip().lower()
    if tipo not in _TIPOS:
        raise CodigoProdutoInvalido("Tipo de código inválido.")
    if tipo == "ean" and not _ean_valido(chave):
        raise CodigoProdutoInvalido(
            "O EAN/GTIN informado possui tamanho ou dígito verificador inválido."
        )
    return tipo


def buscar_codigo(
    empresa_id: int, valor: str | None, *, bloquear: bool = False
) -> CodigoProduto | None:
    _, chave = normalizar_codigo(valor)
    consulta = db.select(CodigoProduto).where(
        CodigoProduto.empresa_id == empresa_id,
        CodigoProduto.chave_busca == chave,
        CodigoProduto.ativo.is_(True),
    )
    if bloquear:
        consulta = consulta.with_for_update().execution_options(populate_existing=True)
    return db.session.scalar(consulta)


def cadastrar_codigo(
    *,
    empresa_id: int,
    produto_id: uuid.UUID,
    valor: str,
    tipo: str,
    principal: bool,
    usuario_id: uuid.UUID,
) -> CodigoProduto:
    produto = db.session.scalar(
        db.select(Produto)
        .where(Produto.id == produto_id, Produto.empresa_id == empresa_id)
        .with_for_update()
    )
    if produto is None:
        raise CodigoProdutoInvalido("Produto não encontrado nesta empresa.")
    if not produto.ativo:
        raise CodigoProdutoInvalido("Não é possível cadastrar código em produto arquivado.")

    codigo, chave = normalizar_codigo(valor)
    tipo = validar_tipo(tipo, chave)
    existente = db.session.scalar(
        db.select(CodigoProduto).where(
            CodigoProduto.empresa_id == empresa_id,
            CodigoProduto.chave_busca == chave,
        )
    )
    if existente is not None:
        raise CodigoProdutoConflitante(
            "Este código já está reservado para um produto desta empresa."
        )

    ativos = db.session.scalars(
        db.select(CodigoProduto)
        .where(
            CodigoProduto.produto_id == produto.id,
            CodigoProduto.ativo.is_(True),
        )
        .with_for_update()
    ).all()
    tornar_principal = principal or not any(item.principal for item in ativos)
    if tornar_principal:
        for item in ativos:
            if item.principal:
                item.principal = False
                item.versao += 1

    novo = CodigoProduto(
        empresa_id=empresa_id,
        produto_id=produto.id,
        codigo=codigo,
        chave_busca=chave,
        tipo=tipo,
        principal=tornar_principal,
        criado_por_id=usuario_id,
    )
    db.session.add(novo)
    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise CodigoProdutoConflitante(
            "Este código acabou de ser cadastrado por outra sessão."
        ) from exc
    registrar(
        "codigo_produto_criado",
        "codigo_produto",
        novo.id,
        empresa_id,
        {
            "produto_id": str(produto.id),
            "codigo": novo.codigo,
            "tipo": novo.tipo,
            "principal": novo.principal,
        },
        usuario_id=usuario_id,
    )
    db.session.commit()
    return novo


def alterar_status_codigo(
    *,
    empresa_id: int,
    produto_id: uuid.UUID,
    codigo_id: uuid.UUID,
    versao: int,
    usuario_id: uuid.UUID,
) -> CodigoProduto:
    produto = db.session.scalar(
        db.select(Produto)
        .where(Produto.id == produto_id, Produto.empresa_id == empresa_id)
        .with_for_update()
    )
    if produto is None:
        raise CodigoProdutoInvalido("Produto não encontrado nesta empresa.")
    codigo = db.session.scalar(
        db.select(CodigoProduto)
        .where(
            CodigoProduto.id == codigo_id,
            CodigoProduto.empresa_id == empresa_id,
            CodigoProduto.produto_id == produto_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if codigo is None:
        raise CodigoProdutoInvalido("Código não encontrado.")
    if codigo.versao != versao:
        raise CodigoProdutoConflitante(
            "Este código foi alterado em outra sessão. Recarregue a página."
        )

    codigo.ativo = not codigo.ativo
    codigo.versao += 1
    if codigo.ativo:
        codigo.desativado_por_id = None
        codigo.desativado_em = None
        principal = db.session.scalar(
            db.select(CodigoProduto.id).where(
                CodigoProduto.produto_id == produto_id,
                CodigoProduto.ativo.is_(True),
                CodigoProduto.principal.is_(True),
                CodigoProduto.id != codigo.id,
            )
        )
        if principal is None:
            codigo.principal = True
    else:
        codigo.desativado_por_id = usuario_id
        codigo.desativado_em = utcnow()
        era_principal = codigo.principal
        codigo.principal = False
        if era_principal:
            substituto = db.session.scalar(
                db.select(CodigoProduto)
                .where(
                    CodigoProduto.produto_id == produto_id,
                    CodigoProduto.ativo.is_(True),
                    CodigoProduto.id != codigo.id,
                )
                .order_by(CodigoProduto.criado_em, CodigoProduto.id)
                .with_for_update()
            )
            if substituto is not None:
                substituto.principal = True
                substituto.versao += 1

    acao = "codigo_produto_ativado" if codigo.ativo else "codigo_produto_desativado"
    registrar(
        acao,
        "codigo_produto",
        codigo.id,
        empresa_id,
        {
            "produto_id": str(produto_id),
            "codigo": codigo.codigo,
            "ativo": codigo.ativo,
        },
        usuario_id=usuario_id,
    )
    db.session.commit()
    return codigo
