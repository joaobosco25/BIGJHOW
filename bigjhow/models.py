from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, Uuid
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Empresa(db.Model):
    __tablename__ = "empresas"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(db.String(120), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(db.String(120), unique=True, nullable=False)
    ativa: Mapped[bool] = mapped_column(default=True, nullable=False)
    criado_em: Mapped[datetime] = mapped_column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class Usuario(UserMixin, db.Model):
    __tablename__ = "usuarios"
    __table_args__ = (
        CheckConstraint(
            "papel IN ('proprietario', 'administrador', 'operador')",
            name="papel_valido",
        ),
        CheckConstraint("usuario = lower(usuario)", name="usuario_minusculo"),
        CheckConstraint("tentativas_falhas >= 0", name="tentativas_falhas_nao_negativas"),
        CheckConstraint("sessao_versao >= 1", name="sessao_versao_positiva"),
        CheckConstraint("versao >= 1", name="versao_positiva"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    nome: Mapped[str] = mapped_column(db.String(120), nullable=False)
    usuario: Mapped[str] = mapped_column(db.String(254), unique=True, nullable=False, index=True)
    senha_hash: Mapped[str] = mapped_column(db.String(512), nullable=False)
    papel: Mapped[str] = mapped_column(db.String(20), nullable=False, default="operador")
    ativo: Mapped[bool] = mapped_column(default=True, nullable=False)
    troca_senha_obrigatoria: Mapped[bool] = mapped_column(default=True, nullable=False)
    sessao_versao: Mapped[int] = mapped_column(default=1, nullable=False)
    versao: Mapped[int] = mapped_column(default=1, nullable=False)
    tentativas_falhas: Mapped[int] = mapped_column(default=0, nullable=False)
    bloqueado_ate: Mapped[datetime | None] = mapped_column(db.DateTime(timezone=True), nullable=True)
    ultimo_login_em: Mapped[datetime | None] = mapped_column(db.DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    atualizado_em: Mapped[datetime] = mapped_column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    empresas: Mapped[list["UsuarioEmpresa"]] = relationship(
        back_populates="usuario", cascade="all, delete-orphan"
    )

    @property
    def is_active(self) -> bool:
        return self.ativo

    def set_senha(self, senha: str) -> None:
        self.senha_hash = generate_password_hash(senha, method="scrypt")

    def verificar_senha(self, senha: str) -> bool:
        return check_password_hash(self.senha_hash, senha)

    def pode_acessar(self, empresa_id: int) -> bool:
        if self.papel == "proprietario":
            return True
        return any(vinculo.empresa_id == empresa_id for vinculo in self.empresas)

    @property
    def papel_rotulo(self) -> str:
        return {
            "proprietario": "Proprietário",
            "administrador": "Administrador",
            "operador": "Operador",
        }.get(self.papel, self.papel)

    @property
    def empresas_ids(self) -> set[int]:
        return {vinculo.empresa_id for vinculo in self.empresas}

    def bloqueio_ativo(self, agora: datetime | None = None) -> bool:
        if not self.bloqueado_ate:
            return False
        agora = agora or utcnow()
        bloqueado_ate = self.bloqueado_ate
        if bloqueado_ate.tzinfo is None:
            bloqueado_ate = bloqueado_ate.replace(tzinfo=timezone.utc)
        return bloqueado_ate > agora


class UsuarioEmpresa(db.Model):
    __tablename__ = "usuarios_empresas"

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True
    )
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="CASCADE"), primary_key=True
    )
    usuario: Mapped[Usuario] = relationship(back_populates="empresas")
    empresa: Mapped[Empresa] = relationship()


class Produto(db.Model):
    __tablename__ = "produtos"
    __table_args__ = (
        UniqueConstraint("empresa_id", "sku_interno", name="empresa_sku_interno"),
        UniqueConstraint("origem_arquivo", "origem_linha", name="produto_origem"),
        CheckConstraint("preco_venda >= 0", name="preco_venda_nao_negativo"),
        CheckConstraint("versao >= 1", name="versao_positiva"),
        Index("ix_produtos_empresa_ativo_nome", "empresa_id", "ativo", "nome"),
        Index("ix_produtos_empresa_revisao", "empresa_id", "revisao_pendente"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sku_interno: Mapped[str] = mapped_column(db.String(40), nullable=False)
    codigo_externo: Mapped[str | None] = mapped_column(db.String(80), nullable=True, index=True)
    nome: Mapped[str] = mapped_column(db.String(240), nullable=False, index=True)
    categoria: Mapped[str] = mapped_column(db.String(160), nullable=False, default="Sem categoria")
    unidade: Mapped[str] = mapped_column(db.String(12), nullable=False, default="UN")
    pesado: Mapped[bool] = mapped_column(default=False, nullable=False)
    preco_venda: Mapped[Decimal] = mapped_column(db.Numeric(14, 2), default=Decimal("0"), nullable=False)
    ativo: Mapped[bool] = mapped_column(default=True, nullable=False)
    revisao_pendente: Mapped[bool] = mapped_column(default=False, nullable=False)
    motivos_revisao: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    origem_arquivo: Mapped[str | None] = mapped_column(db.String(160), nullable=True)
    origem_linha: Mapped[int | None] = mapped_column(nullable=True)
    versao: Mapped[int] = mapped_column(default=1, nullable=False)
    criado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    arquivado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    atualizado_em: Mapped[datetime] = mapped_column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    arquivado_em: Mapped[datetime | None] = mapped_column(db.DateTime(timezone=True), nullable=True)

    empresa: Mapped[Empresa] = relationship()
    saldo: Mapped["SaldoEstoque"] = relationship(
        back_populates="produto", cascade="all, delete-orphan", uselist=False
    )
    codigos: Mapped[list["CodigoProduto"]] = relationship(
        back_populates="produto", order_by="CodigoProduto.criado_em"
    )
    fiscal: Mapped["ProdutoFiscal | None"] = relationship(
        back_populates="produto", cascade="all, delete-orphan", uselist=False
    )
    mgv6: Mapped["IntegracaoMGV6Produto | None"] = relationship(
        back_populates="produto", cascade="all, delete-orphan", uselist=False
    )


class SaldoEstoque(db.Model):
    __tablename__ = "saldos_estoque"
    __table_args__ = (CheckConstraint("quantidade >= 0", name="quantidade_nao_negativa"),)

    produto_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("produtos.id", ondelete="CASCADE"), primary_key=True
    )
    quantidade: Mapped[Decimal] = mapped_column(db.Numeric(15, 3), default=Decimal("0"), nullable=False)
    aguardando_inventario: Mapped[bool] = mapped_column(default=True, nullable=False)
    atualizado_em: Mapped[datetime] = mapped_column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    produto: Mapped[Produto] = relationship(back_populates="saldo")


class MovimentoEstoque(db.Model):
    __tablename__ = "movimentos_estoque"
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('inventario', 'entrada', 'saida', 'ajuste')", name="tipo_valido"
        ),
        CheckConstraint("quantidade_anterior >= 0", name="quantidade_anterior_nao_negativa"),
        CheckConstraint("quantidade_final >= 0", name="quantidade_final_nao_negativa"),
        Index("ix_movimentos_empresa_criado", "empresa_id", "criado_em"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False
    )
    produto_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("produtos.id", ondelete="RESTRICT"), nullable=False
    )
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    tipo: Mapped[str] = mapped_column(db.String(20), nullable=False)
    quantidade_anterior: Mapped[Decimal] = mapped_column(db.Numeric(15, 3), nullable=False)
    variacao: Mapped[Decimal] = mapped_column(db.Numeric(15, 3), nullable=False)
    quantidade_final: Mapped[Decimal] = mapped_column(db.Numeric(15, 3), nullable=False)
    motivo: Mapped[str] = mapped_column(db.String(500), nullable=False)
    criado_em: Mapped[datetime] = mapped_column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    produto: Mapped[Produto] = relationship()


class CodigoProduto(db.Model):
    __tablename__ = "codigos_produto"
    __table_args__ = (
        UniqueConstraint("empresa_id", "chave_busca", name="empresa_codigo_busca"),
        CheckConstraint(
            "tipo IN ('ean', 'interno', 'plu', 'legado', 'outro')",
            name="tipo_valido",
        ),
        CheckConstraint("versao >= 1", name="versao_positiva"),
        CheckConstraint("ativo OR NOT principal", name="principal_precisa_estar_ativo"),
        Index("ix_codigos_produto_empresa_ativo", "empresa_id", "ativo"),
        Index("ix_codigos_produto_produto_ativo", "produto_id", "ativo"),
        Index(
            "uq_codigos_produto_principal_ativo",
            "produto_id",
            unique=True,
            postgresql_where=text("principal AND ativo"),
            sqlite_where=text("principal = 1 AND ativo = 1"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False
    )
    produto_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("produtos.id", ondelete="RESTRICT"), nullable=False
    )
    codigo: Mapped[str] = mapped_column(db.String(80), nullable=False)
    chave_busca: Mapped[str] = mapped_column(db.String(80), nullable=False)
    tipo: Mapped[str] = mapped_column(db.String(20), nullable=False, default="outro")
    principal: Mapped[bool] = mapped_column(default=False, nullable=False)
    ativo: Mapped[bool] = mapped_column(default=True, nullable=False)
    versao: Mapped[int] = mapped_column(default=1, nullable=False)
    criado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    desativado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    desativado_em: Mapped[datetime | None] = mapped_column(
        db.DateTime(timezone=True), nullable=True
    )

    empresa: Mapped[Empresa] = relationship()
    produto: Mapped[Produto] = relationship(back_populates="codigos")
    criado_por: Mapped[Usuario | None] = relationship(foreign_keys=[criado_por_id])
    desativado_por: Mapped[Usuario | None] = relationship(
        foreign_keys=[desativado_por_id]
    )

    @property
    def tipo_rotulo(self) -> str:
        return {
            "ean": "EAN/GTIN",
            "interno": "Código interno",
            "plu": "PLU",
            "legado": "Código importado",
            "outro": "Outro",
        }.get(self.tipo, self.tipo)


class Venda(db.Model):
    __tablename__ = "vendas"
    __table_args__ = (
        UniqueConstraint("empresa_id", "numero", name="empresa_numero_venda"),
        CheckConstraint(
            "status IN ('aberta', 'finalizada', 'cancelada', 'descartada')",
            name="status_valido",
        ),
        CheckConstraint("total >= 0", name="total_nao_negativo"),
        CheckConstraint("troco >= 0", name="troco_nao_negativo"),
        CheckConstraint("versao >= 1", name="versao_positiva"),
        Index("ix_vendas_empresa_criado", "empresa_id", "criado_em"),
        Index("ix_vendas_empresa_status", "empresa_id", "status"),
        Index(
            "uq_vendas_empresa_operador_aberta",
            "empresa_id",
            "operador_id",
            unique=True,
            postgresql_where=text("status = 'aberta'"),
            sqlite_where=text("status = 'aberta'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False
    )
    turno_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        db.ForeignKey("turnos_caixa.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    numero: Mapped[int] = mapped_column(db.BigInteger, nullable=False)
    operador_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    finalizado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    cancelado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        db.String(20), nullable=False, default="aberta"
    )
    total: Mapped[Decimal] = mapped_column(
        db.Numeric(16, 2), nullable=False, default=Decimal("0")
    )
    troco: Mapped[Decimal] = mapped_column(
        db.Numeric(16, 2), nullable=False, default=Decimal("0")
    )
    versao: Mapped[int] = mapped_column(nullable=False, default=1)
    motivo_cancelamento: Mapped[str | None] = mapped_column(
        db.String(500), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    finalizado_em: Mapped[datetime | None] = mapped_column(
        db.DateTime(timezone=True), nullable=True
    )
    cancelado_em: Mapped[datetime | None] = mapped_column(
        db.DateTime(timezone=True), nullable=True
    )
    descartado_em: Mapped[datetime | None] = mapped_column(
        db.DateTime(timezone=True), nullable=True
    )

    empresa: Mapped[Empresa] = relationship()
    turno: Mapped["TurnoCaixa | None"] = relationship(back_populates="vendas")
    operador: Mapped[Usuario | None] = relationship(foreign_keys=[operador_id])
    finalizado_por: Mapped[Usuario | None] = relationship(
        foreign_keys=[finalizado_por_id]
    )
    cancelado_por: Mapped[Usuario | None] = relationship(
        foreign_keys=[cancelado_por_id]
    )
    itens: Mapped[list["VendaItem"]] = relationship(
        back_populates="venda", cascade="all, delete-orphan", order_by="VendaItem.criado_em"
    )
    pagamentos: Mapped[list["VendaPagamento"]] = relationship(
        back_populates="venda", cascade="all, delete-orphan", order_by="VendaPagamento.criado_em"
    )
    documento_fiscal: Mapped["DocumentoFiscal | None"] = relationship(
        back_populates="venda", uselist=False
    )

    @property
    def status_rotulo(self) -> str:
        return {
            "aberta": "Em atendimento",
            "finalizada": "Finalizada",
            "cancelada": "Cancelada",
            "descartada": "Descartada",
        }.get(self.status, self.status)

    @property
    def numero_formatado(self) -> str:
        return f"{self.numero:06d}"


class VendaItem(db.Model):
    __tablename__ = "venda_itens"
    __table_args__ = (
        UniqueConstraint("venda_id", "produto_id", name="venda_produto"),
        UniqueConstraint("movimento_saida_id", name="venda_item_movimento_saida"),
        UniqueConstraint("movimento_estorno_id", name="venda_item_movimento_estorno"),
        CheckConstraint("quantidade > 0", name="quantidade_positiva"),
        CheckConstraint("preco_unitario > 0", name="preco_unitario_positivo"),
        CheckConstraint("subtotal > 0", name="subtotal_positivo"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    venda_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("vendas.id", ondelete="CASCADE"), nullable=False
    )
    produto_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("produtos.id", ondelete="RESTRICT"), nullable=False
    )
    codigo_produto_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("codigos_produto.id", ondelete="SET NULL"), nullable=True
    )
    movimento_saida_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        db.ForeignKey("movimentos_estoque.id", ondelete="RESTRICT"),
        nullable=True,
    )
    movimento_estorno_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        db.ForeignKey("movimentos_estoque.id", ondelete="RESTRICT"),
        nullable=True,
    )
    produto_nome: Mapped[str] = mapped_column(db.String(240), nullable=False)
    sku_interno: Mapped[str] = mapped_column(db.String(40), nullable=False)
    codigo_lido: Mapped[str] = mapped_column(db.String(80), nullable=False)
    unidade: Mapped[str] = mapped_column(db.String(12), nullable=False)
    pesado: Mapped[bool] = mapped_column(nullable=False)
    preco_unitario: Mapped[Decimal] = mapped_column(db.Numeric(14, 2), nullable=False)
    quantidade: Mapped[Decimal] = mapped_column(db.Numeric(15, 3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False)
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    venda: Mapped[Venda] = relationship(back_populates="itens")
    produto: Mapped[Produto] = relationship()
    codigo_produto: Mapped[CodigoProduto | None] = relationship()
    movimento_saida: Mapped[MovimentoEstoque | None] = relationship(
        foreign_keys=[movimento_saida_id]
    )
    movimento_estorno: Mapped[MovimentoEstoque | None] = relationship(
        foreign_keys=[movimento_estorno_id]
    )


class VendaPagamento(db.Model):
    __tablename__ = "venda_pagamentos"
    __table_args__ = (
        UniqueConstraint("venda_id", "metodo", name="venda_metodo_pagamento"),
        CheckConstraint(
            "metodo IN ('dinheiro', 'pix', 'debito', 'credito')",
            name="metodo_valido",
        ),
        CheckConstraint("valor > 0", name="valor_positivo"),
        CheckConstraint("valor_recebido >= valor", name="valor_recebido_suficiente"),
        CheckConstraint(
            "metodo = 'dinheiro' OR valor_recebido = valor",
            name="valor_recebido_apenas_dinheiro",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    venda_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("vendas.id", ondelete="CASCADE"), nullable=False
    )
    metodo: Mapped[str] = mapped_column(db.String(20), nullable=False)
    valor: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False)
    valor_recebido: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False)
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )

    venda: Mapped[Venda] = relationship(back_populates="pagamentos")

    @property
    def metodo_rotulo(self) -> str:
        return {
            "dinheiro": "Dinheiro",
            "pix": "PIX",
            "debito": "Cartão de débito",
            "credito": "Cartão de crédito",
        }.get(self.metodo, self.metodo)


class TerminalCaixa(db.Model):
    __tablename__ = "terminais_caixa"
    __table_args__ = (
        UniqueConstraint("empresa_id", "codigo", name="empresa_terminal_codigo"),
        CheckConstraint("versao >= 1", name="versao_positiva"),
        Index("ix_terminais_caixa_empresa_ativo", "empresa_id", "ativo"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False
    )
    codigo: Mapped[str] = mapped_column(db.String(40), nullable=False)
    nome: Mapped[str] = mapped_column(db.String(120), nullable=False)
    ativo: Mapped[bool] = mapped_column(default=True, nullable=False)
    versao: Mapped[int] = mapped_column(default=1, nullable=False)
    criado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    empresa: Mapped[Empresa] = relationship()
    criado_por: Mapped[Usuario | None] = relationship(foreign_keys=[criado_por_id])
    turnos: Mapped[list["TurnoCaixa"]] = relationship(back_populates="terminal")


class TurnoCaixa(db.Model):
    __tablename__ = "turnos_caixa"
    __table_args__ = (
        CheckConstraint("status IN ('aberto', 'fechado')", name="status_valido"),
        CheckConstraint("valor_inicial >= 0", name="valor_inicial_nao_negativo"),
        CheckConstraint("versao >= 1", name="versao_positiva"),
        CheckConstraint(
            "status = 'aberto' OR (fechado_em IS NOT NULL "
            "AND dinheiro_esperado IS NOT NULL AND dinheiro_informado IS NOT NULL "
            "AND dinheiro_diferenca IS NOT NULL AND pix_esperado IS NOT NULL "
            "AND pix_informado IS NOT NULL AND pix_diferenca IS NOT NULL "
            "AND debito_esperado IS NOT NULL AND debito_informado IS NOT NULL "
            "AND debito_diferenca IS NOT NULL AND credito_esperado IS NOT NULL "
            "AND credito_informado IS NOT NULL AND credito_diferenca IS NOT NULL)",
            name="fechamento_completo",
        ),
        Index("ix_turnos_caixa_empresa_aberto", "empresa_id", "status"),
        Index(
            "uq_turnos_caixa_terminal_aberto",
            "terminal_id",
            unique=True,
            postgresql_where=text("status = 'aberto'"),
            sqlite_where=text("status = 'aberto'"),
        ),
        Index(
            "uq_turnos_caixa_operador_aberto",
            "operador_id",
            unique=True,
            postgresql_where=text("status = 'aberto'"),
            sqlite_where=text("status = 'aberto'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False
    )
    terminal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("terminais_caixa.id", ondelete="RESTRICT"), nullable=False
    )
    operador_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    aberto_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    fechado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(db.String(20), default="aberto", nullable=False)
    valor_inicial: Mapped[Decimal] = mapped_column(
        db.Numeric(16, 2), default=Decimal("0"), nullable=False
    )
    dinheiro_esperado: Mapped[Decimal | None] = mapped_column(db.Numeric(16, 2))
    dinheiro_informado: Mapped[Decimal | None] = mapped_column(db.Numeric(16, 2))
    dinheiro_diferenca: Mapped[Decimal | None] = mapped_column(db.Numeric(16, 2))
    pix_esperado: Mapped[Decimal | None] = mapped_column(db.Numeric(16, 2))
    pix_informado: Mapped[Decimal | None] = mapped_column(db.Numeric(16, 2))
    pix_diferenca: Mapped[Decimal | None] = mapped_column(db.Numeric(16, 2))
    debito_esperado: Mapped[Decimal | None] = mapped_column(db.Numeric(16, 2))
    debito_informado: Mapped[Decimal | None] = mapped_column(db.Numeric(16, 2))
    debito_diferenca: Mapped[Decimal | None] = mapped_column(db.Numeric(16, 2))
    credito_esperado: Mapped[Decimal | None] = mapped_column(db.Numeric(16, 2))
    credito_informado: Mapped[Decimal | None] = mapped_column(db.Numeric(16, 2))
    credito_diferenca: Mapped[Decimal | None] = mapped_column(db.Numeric(16, 2))
    total_vendas: Mapped[Decimal | None] = mapped_column(db.Numeric(16, 2))
    total_sangrias: Mapped[Decimal | None] = mapped_column(db.Numeric(16, 2))
    total_suprimentos: Mapped[Decimal | None] = mapped_column(db.Numeric(16, 2))
    justificativa_fechamento: Mapped[str | None] = mapped_column(db.String(500))
    versao: Mapped[int] = mapped_column(default=1, nullable=False)
    aberto_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    fechado_em: Mapped[datetime | None] = mapped_column(
        db.DateTime(timezone=True), nullable=True
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    empresa: Mapped[Empresa] = relationship()
    terminal: Mapped[TerminalCaixa] = relationship(back_populates="turnos")
    operador: Mapped[Usuario | None] = relationship(foreign_keys=[operador_id])
    aberto_por: Mapped[Usuario | None] = relationship(foreign_keys=[aberto_por_id])
    fechado_por: Mapped[Usuario | None] = relationship(foreign_keys=[fechado_por_id])
    vendas: Mapped[list[Venda]] = relationship(back_populates="turno")
    movimentos: Mapped[list["MovimentoCaixa"]] = relationship(
        back_populates="turno", order_by="MovimentoCaixa.criado_em"
    )

    @property
    def status_rotulo(self) -> str:
        return "Aberto" if self.status == "aberto" else "Fechado"


class MovimentoCaixa(db.Model):
    __tablename__ = "movimentos_caixa"
    __table_args__ = (
        CheckConstraint("tipo IN ('sangria', 'suprimento')", name="tipo_valido"),
        CheckConstraint(
            "status IN ('pendente', 'aprovado', 'rejeitado')",
            name="status_valido",
        ),
        CheckConstraint("valor > 0", name="valor_positivo"),
        CheckConstraint("versao >= 1", name="versao_positiva"),
        CheckConstraint(
            "status = 'pendente' OR decidido_em IS NOT NULL",
            name="decisao_registrada",
        ),
        Index("ix_movimentos_caixa_turno_status", "turno_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False
    )
    turno_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("turnos_caixa.id", ondelete="RESTRICT"), nullable=False
    )
    tipo: Mapped[str] = mapped_column(db.String(20), nullable=False)
    valor: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False)
    motivo: Mapped[str] = mapped_column(db.String(500), nullable=False)
    status: Mapped[str] = mapped_column(db.String(20), default="pendente", nullable=False)
    solicitado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    decidido_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    motivo_decisao: Mapped[str | None] = mapped_column(db.String(500))
    versao: Mapped[int] = mapped_column(default=1, nullable=False)
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    decidido_em: Mapped[datetime | None] = mapped_column(
        db.DateTime(timezone=True), nullable=True
    )

    empresa: Mapped[Empresa] = relationship()
    turno: Mapped[TurnoCaixa] = relationship(back_populates="movimentos")
    solicitado_por: Mapped[Usuario | None] = relationship(
        foreign_keys=[solicitado_por_id]
    )
    decidido_por: Mapped[Usuario | None] = relationship(foreign_keys=[decidido_por_id])

    @property
    def tipo_rotulo(self) -> str:
        return "Sangria" if self.tipo == "sangria" else "Suprimento"

    @property
    def status_rotulo(self) -> str:
        return {
            "pendente": "Aguardando autorização",
            "aprovado": "Aprovado",
            "rejeitado": "Rejeitado",
        }.get(self.status, self.status)


class Impressora(db.Model):
    __tablename__ = "impressoras"
    __table_args__ = (
        UniqueConstraint("empresa_id", "codigo", name="empresa_impressora_codigo"),
        CheckConstraint(
            "tipo IN ('cupom', 'producao', 'etiqueta', 'relatorio')",
            name="tipo_valido",
        ),
        CheckConstraint("modo = 'simulada'", name="modo_simulado_base5"),
        CheckConstraint("versao >= 1", name="versao_positiva"),
        Index("ix_impressoras_empresa_ativo", "empresa_id", "ativo"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False
    )
    codigo: Mapped[str] = mapped_column(db.String(40), nullable=False)
    nome: Mapped[str] = mapped_column(db.String(120), nullable=False)
    setor: Mapped[str] = mapped_column(db.String(80), nullable=False)
    tipo: Mapped[str] = mapped_column(db.String(20), nullable=False, default="cupom")
    modo: Mapped[str] = mapped_column(db.String(20), nullable=False, default="simulada")
    ativo: Mapped[bool] = mapped_column(default=True, nullable=False)
    versao: Mapped[int] = mapped_column(default=1, nullable=False)
    criado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    empresa: Mapped[Empresa] = relationship()
    criado_por: Mapped[Usuario | None] = relationship(foreign_keys=[criado_por_id])


class RegraImpressao(db.Model):
    __tablename__ = "regras_impressao"
    __table_args__ = (
        UniqueConstraint(
            "terminal_id", "impressora_id", "evento", name="terminal_impressora_evento"
        ),
        CheckConstraint(
            "evento IN ('venda_finalizada', 'venda_cancelada', 'turno_fechado', "
            "'movimento_caixa_aprovado')",
            name="evento_valido",
        ),
        CheckConstraint("copias BETWEEN 1 AND 3", name="copias_validas"),
        CheckConstraint("versao >= 1", name="versao_positiva"),
        Index("ix_regras_impressao_empresa_evento", "empresa_id", "evento"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False
    )
    terminal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("terminais_caixa.id", ondelete="RESTRICT"), nullable=False
    )
    impressora_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("impressoras.id", ondelete="RESTRICT"), nullable=False
    )
    evento: Mapped[str] = mapped_column(db.String(40), nullable=False)
    copias: Mapped[int] = mapped_column(default=1, nullable=False)
    ativo: Mapped[bool] = mapped_column(default=True, nullable=False)
    versao: Mapped[int] = mapped_column(default=1, nullable=False)
    criado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    empresa: Mapped[Empresa] = relationship()
    terminal: Mapped[TerminalCaixa] = relationship()
    impressora: Mapped[Impressora] = relationship()
    criado_por: Mapped[Usuario | None] = relationship(foreign_keys=[criado_por_id])


class TrabalhoImpressao(db.Model):
    __tablename__ = "trabalhos_impressao"
    __table_args__ = (
        UniqueConstraint("chave_idempotencia", name="chave_idempotencia_unica"),
        CheckConstraint(
            "status IN ('pendente', 'processando', 'impresso', 'falhou', 'cancelado')",
            name="status_valido",
        ),
        CheckConstraint("tentativas >= 0", name="tentativas_nao_negativas"),
        CheckConstraint("copias BETWEEN 1 AND 3", name="copias_validas"),
        CheckConstraint("versao >= 1", name="versao_positiva"),
        Index("ix_trabalhos_impressao_empresa_status", "empresa_id", "status"),
        Index("ix_trabalhos_impressao_terminal_criado", "terminal_id", "criado_em"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False
    )
    terminal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("terminais_caixa.id", ondelete="RESTRICT"), nullable=False
    )
    impressora_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("impressoras.id", ondelete="RESTRICT"), nullable=False
    )
    regra_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("regras_impressao.id", ondelete="SET NULL"), nullable=True
    )
    reimpressao_de_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("trabalhos_impressao.id", ondelete="RESTRICT"), nullable=True
    )
    evento: Mapped[str] = mapped_column(db.String(40), nullable=False)
    entidade: Mapped[str] = mapped_column(db.String(40), nullable=False)
    entidade_id: Mapped[str] = mapped_column(db.String(80), nullable=False)
    chave_idempotencia: Mapped[str] = mapped_column(db.String(180), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    copias: Mapped[int] = mapped_column(default=1, nullable=False)
    status: Mapped[str] = mapped_column(db.String(20), default="pendente", nullable=False)
    tentativas: Mapped[int] = mapped_column(default=0, nullable=False)
    erro_codigo: Mapped[str | None] = mapped_column(db.String(80))
    erro_mensagem: Mapped[str | None] = mapped_column(db.String(240))
    motivo_reimpressao: Mapped[str | None] = mapped_column(db.String(500))
    solicitado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    versao: Mapped[int] = mapped_column(default=1, nullable=False)
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    iniciado_em: Mapped[datetime | None] = mapped_column(db.DateTime(timezone=True))
    impresso_em: Mapped[datetime | None] = mapped_column(db.DateTime(timezone=True))
    falhou_em: Mapped[datetime | None] = mapped_column(db.DateTime(timezone=True))
    atualizado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    empresa: Mapped[Empresa] = relationship()
    terminal: Mapped[TerminalCaixa] = relationship()
    impressora: Mapped[Impressora] = relationship()
    regra: Mapped[RegraImpressao | None] = relationship()
    reimpressao_de: Mapped["TrabalhoImpressao | None"] = relationship(
        remote_side="TrabalhoImpressao.id"
    )
    solicitado_por: Mapped[Usuario | None] = relationship(
        foreign_keys=[solicitado_por_id]
    )

    @property
    def status_rotulo(self) -> str:
        return {
            "pendente": "Pendente",
            "processando": "Processando",
            "impresso": "Impresso",
            "falhou": "Falhou",
            "cancelado": "Cancelado",
        }.get(self.status, self.status)


class InventarioLote(db.Model):
    __tablename__ = "inventarios_lote"
    __table_args__ = (
        CheckConstraint(
            "status IN ('rascunho', 'finalizado', 'cancelado')",
            name="status_valido",
        ),
        CheckConstraint("versao >= 1", name="versao_positiva"),
        Index("ix_inventarios_lote_empresa_criado", "empresa_id", "criado_em"),
        Index(
            "uq_inventarios_lote_empresa_rascunho",
            "empresa_id",
            unique=True,
            postgresql_where=text("status = 'rascunho'"),
            sqlite_where=text("status = 'rascunho'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False
    )
    descricao: Mapped[str] = mapped_column(db.String(160), nullable=False)
    status: Mapped[str] = mapped_column(db.String(20), nullable=False, default="rascunho")
    versao: Mapped[int] = mapped_column(nullable=False, default=1)
    criado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    finalizado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    cancelado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    finalizado_em: Mapped[datetime | None] = mapped_column(
        db.DateTime(timezone=True), nullable=True
    )
    cancelado_em: Mapped[datetime | None] = mapped_column(
        db.DateTime(timezone=True), nullable=True
    )

    empresa: Mapped[Empresa] = relationship()
    criado_por: Mapped[Usuario | None] = relationship(foreign_keys=[criado_por_id])
    finalizado_por: Mapped[Usuario | None] = relationship(foreign_keys=[finalizado_por_id])
    cancelado_por: Mapped[Usuario | None] = relationship(foreign_keys=[cancelado_por_id])
    itens: Mapped[list["InventarioLoteItem"]] = relationship(
        back_populates="inventario", cascade="all, delete-orphan"
    )

    @property
    def status_rotulo(self) -> str:
        return {
            "rascunho": "Rascunho",
            "finalizado": "Finalizado",
            "cancelado": "Cancelado",
        }.get(self.status, self.status)


class InventarioLoteItem(db.Model):
    __tablename__ = "inventarios_lote_itens"
    __table_args__ = (
        UniqueConstraint("inventario_id", "produto_id", name="inventario_produto"),
        CheckConstraint(
            "quantidade_referencia >= 0", name="quantidade_referencia_nao_negativa"
        ),
        CheckConstraint(
            "quantidade_contada IS NULL OR quantidade_contada >= 0",
            name="quantidade_contada_nao_negativa",
        ),
        Index(
            "ix_inventarios_lote_itens_inventario_contado",
            "inventario_id",
            "quantidade_contada",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    inventario_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("inventarios_lote.id", ondelete="CASCADE"), nullable=False
    )
    produto_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("produtos.id", ondelete="RESTRICT"), nullable=False
    )
    quantidade_referencia: Mapped[Decimal] = mapped_column(
        db.Numeric(15, 3), nullable=False
    )
    aguardando_referencia: Mapped[bool] = mapped_column(nullable=False)
    unidade_referencia: Mapped[str] = mapped_column(db.String(12), nullable=False)
    pesado_referencia: Mapped[bool] = mapped_column(nullable=False)
    quantidade_contada: Mapped[Decimal | None] = mapped_column(
        db.Numeric(15, 3), nullable=True
    )
    contado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    contado_em: Mapped[datetime | None] = mapped_column(
        db.DateTime(timezone=True), nullable=True
    )

    inventario: Mapped[InventarioLote] = relationship(back_populates="itens")
    produto: Mapped[Produto] = relationship()
    contado_por: Mapped[Usuario | None] = relationship(foreign_keys=[contado_por_id])


class Auditoria(db.Model):
    __tablename__ = "auditoria"
    __table_args__ = (Index("ix_auditoria_empresa_criado", "empresa_id", "criado_em"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=True
    )
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    acao: Mapped[str] = mapped_column(db.String(80), nullable=False)
    entidade: Mapped[str] = mapped_column(db.String(80), nullable=False)
    entidade_id: Mapped[str | None] = mapped_column(db.String(80), nullable=True)
    detalhes: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    ip: Mapped[str | None] = mapped_column(db.String(64), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    empresa: Mapped[Empresa | None] = relationship()
    usuario: Mapped[Usuario | None] = relationship()


# ==========================================================================
# Módulo fiscal
#
# As entidades abaixo espelham o modelo do sistema legado (TPA Centralizador:
# DocumentoFiscal, DocumentoItem, DocumentoPagamento, DocumentoXml,
# Inutilizacao, Estabelecimento), para que o histórico já existente possa ser
# importado sem perda e para que a emissão nova fale a mesma linguagem fiscal.
# ==========================================================================


class EstabelecimentoFiscal(db.Model):
    """Dados fiscais de uma empresa: quem emite, com que numeração e por onde."""

    __tablename__ = "estabelecimentos_fiscais"
    __table_args__ = (
        UniqueConstraint("cnpj", "modelo", "serie", name="cnpj_modelo_serie"),
        CheckConstraint("modelo IN (55, 65, 59)", name="modelo_valido"),
        CheckConstraint("ambiente IN (1, 2)", name="ambiente_valido"),
        CheckConstraint("crt IN (1, 2, 3, 4)", name="crt_valido"),
        CheckConstraint("serie BETWEEN 0 AND 999", name="serie_valida"),
        CheckConstraint("proximo_numero BETWEEN 1 AND 999999999", name="numero_valido"),
        CheckConstraint("length(cnpj) = 14", name="cnpj_com_quatorze_digitos"),
        CheckConstraint("versao >= 1", name="versao_positiva"),
    )

    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), primary_key=True
    )

    cnpj: Mapped[str] = mapped_column(db.String(14), nullable=False, index=True)
    razao_social: Mapped[str] = mapped_column(db.String(120), nullable=False)
    nome_fantasia: Mapped[str | None] = mapped_column(db.String(120), nullable=True)
    inscricao_estadual: Mapped[str] = mapped_column(db.String(20), nullable=False)
    inscricao_municipal: Mapped[str | None] = mapped_column(db.String(20), nullable=True)
    crt: Mapped[int] = mapped_column(nullable=False, default=1)

    logradouro: Mapped[str] = mapped_column(db.String(120), nullable=False)
    numero: Mapped[str] = mapped_column(db.String(20), nullable=False, default="S/N")
    complemento: Mapped[str | None] = mapped_column(db.String(120), nullable=True)
    bairro: Mapped[str] = mapped_column(db.String(120), nullable=False)
    codigo_municipio: Mapped[int] = mapped_column(nullable=False)
    municipio: Mapped[str] = mapped_column(db.String(120), nullable=False)
    uf: Mapped[str] = mapped_column(db.String(2), nullable=False)
    cep: Mapped[str] = mapped_column(db.String(8), nullable=False)
    telefone: Mapped[str | None] = mapped_column(db.String(14), nullable=True)

    modelo: Mapped[int] = mapped_column(nullable=False, default=65)
    serie: Mapped[int] = mapped_column(nullable=False, default=1)
    proximo_numero: Mapped[int] = mapped_column(db.BigInteger, nullable=False, default=1)
    ambiente: Mapped[int] = mapped_column(nullable=False, default=2)
    natureza_operacao: Mapped[str] = mapped_column(
        db.String(60), nullable=False, default="VENDA AO CONSUMIDOR"
    )
    informacoes_complementares: Mapped[str | None] = mapped_column(db.String(500), nullable=True)

    provedor: Mapped[str] = mapped_column(
        db.String(40), nullable=False, default="homologacao_interna"
    )
    parametros_provedor: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    csc_id: Mapped[str | None] = mapped_column(db.String(6), nullable=True)
    url_consulta_qrcode: Mapped[str | None] = mapped_column(db.String(300), nullable=True)
    url_consulta_chave: Mapped[str | None] = mapped_column(db.String(300), nullable=True)

    responsavel_cnpj: Mapped[str | None] = mapped_column(db.String(14), nullable=True)
    responsavel_contato: Mapped[str | None] = mapped_column(db.String(60), nullable=True)
    responsavel_email: Mapped[str | None] = mapped_column(db.String(60), nullable=True)
    responsavel_telefone: Mapped[str | None] = mapped_column(db.String(14), nullable=True)

    #: marcado por uma pessoa depois de conferir cada endereço com a SEFAZ da UF
    enderecos_conferidos: Mapped[bool] = mapped_column(default=False, nullable=False)
    #: quando falso, o caixa continua vendendo mas não tenta emitir
    emissao_ativa: Mapped[bool] = mapped_column(default=False, nullable=False)
    #: permite que a venda seja concluída mesmo se a SEFAZ não responder
    permitir_contingencia: Mapped[bool] = mapped_column(default=False, nullable=False)

    versao: Mapped[int] = mapped_column(default=1, nullable=False)
    atualizado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    empresa: Mapped[Empresa] = relationship()
    atualizado_por: Mapped[Usuario | None] = relationship(foreign_keys=[atualizado_por_id])

    @property
    def cnpj_formatado(self) -> str:
        digitos = self.cnpj or ""
        if len(digitos) != 14:
            return digitos
        return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}"

    @property
    def ambiente_rotulo(self) -> str:
        return {1: "Produção", 2: "Homologação"}.get(self.ambiente, str(self.ambiente))

    @property
    def modelo_rotulo(self) -> str:
        return {
            55: "NF-e (modelo 55)",
            65: "NFC-e (modelo 65)",
            59: "CF-e SAT (modelo 59)",
        }.get(self.modelo, str(self.modelo))


class ConfiguracaoDocumentoFiscal(db.Model):
    """Configuração por modelo, ambiente e série de uma empresa.

    A tabela de estabelecimento continua guardando a identidade do emitente.
    Esta tabela separa o que muda por documento/ambiente para que NFC-e 65 e
    NF-e 55 não compartilhem sequência, rota ou parâmetros por acidente.
    """

    __tablename__ = "configuracoes_documentos_fiscais"
    __table_args__ = (
        UniqueConstraint(
            "empresa_id", "modelo", "ambiente", "serie",
            name="empresa_modelo_ambiente_serie_fiscal",
        ),
        CheckConstraint("modelo IN (55, 65, 59)", name="modelo_valido"),
        CheckConstraint("ambiente IN (1, 2)", name="ambiente_valido"),
        CheckConstraint("serie BETWEEN 0 AND 999", name="serie_valida"),
        CheckConstraint(
            "proximo_numero BETWEEN 1 AND 999999999", name="numero_valido"
        ),
        CheckConstraint("versao >= 1", name="versao_positiva"),
        Index(
            "ix_config_fiscal_empresa_modelo_ambiente",
            "empresa_id", "modelo", "ambiente",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False
    )
    modelo: Mapped[int] = mapped_column(nullable=False)
    ambiente: Mapped[int] = mapped_column(nullable=False)
    serie: Mapped[int] = mapped_column(nullable=False)
    proximo_numero: Mapped[int] = mapped_column(db.BigInteger, nullable=False, default=1)
    natureza_operacao: Mapped[str] = mapped_column(
        db.String(60), nullable=False, default="VENDA AO CONSUMIDOR"
    )
    informacoes_complementares: Mapped[str | None] = mapped_column(
        db.String(500), nullable=True
    )
    provedor: Mapped[str] = mapped_column(
        db.String(40), nullable=False, default="homologacao_interna"
    )
    parametros_provedor: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    csc_id: Mapped[str | None] = mapped_column(db.String(6), nullable=True)
    url_consulta_qrcode: Mapped[str | None] = mapped_column(db.String(300), nullable=True)
    url_consulta_chave: Mapped[str | None] = mapped_column(db.String(300), nullable=True)
    enderecos_conferidos: Mapped[bool] = mapped_column(default=False, nullable=False)
    emissao_ativa: Mapped[bool] = mapped_column(default=False, nullable=False)
    permitir_contingencia: Mapped[bool] = mapped_column(default=False, nullable=False)
    versao: Mapped[int] = mapped_column(default=1, nullable=False)
    atualizado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    empresa: Mapped[Empresa] = relationship()
    atualizado_por: Mapped[Usuario | None] = relationship(foreign_keys=[atualizado_por_id])

    @property
    def ambiente_rotulo(self) -> str:
        return {1: "Produção", 2: "Homologação"}.get(self.ambiente, str(self.ambiente))

    @property
    def modelo_rotulo(self) -> str:
        return {55: "NF-e (modelo 55)", 65: "NFC-e (modelo 65)", 59: "CF-e SAT (modelo 59)"}.get(
            self.modelo, str(self.modelo)
        )


class CredencialFiscal(db.Model):
    """Segredos do módulo fiscal, cifrados em repouso.

    O valor em claro nunca é gravado: a cifragem usa AES-GCM com chave
    derivada de ``FISCAL_SECRET_KEY``, que fica no ambiente do servidor. Um
    despejo do banco, sozinho, não revela CSC, senha de certificado nem token.
    """

    __tablename__ = "credenciais_fiscais"
    __table_args__ = (
        UniqueConstraint("empresa_id", "nome", name="empresa_credencial"),
        CheckConstraint(
            "nome IN ('csc_token', 'certificado_senha', 'gateway_token')",
            name="nome_valido",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False
    )
    nome: Mapped[str] = mapped_column(db.String(40), nullable=False)
    valor_cifrado: Mapped[str] = mapped_column(db.String(2000), nullable=False)
    dica: Mapped[str | None] = mapped_column(db.String(60), nullable=True)
    atualizado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    atualizado_por: Mapped[Usuario | None] = relationship(foreign_keys=[atualizado_por_id])


class ProdutoFiscal(db.Model):
    """Classificação fiscal do produto — sem ela a nota é rejeitada."""

    __tablename__ = "produtos_fiscais"
    __table_args__ = (
        CheckConstraint("length(ncm) = 8", name="ncm_com_oito_digitos"),
        CheckConstraint("length(cfop) = 4", name="cfop_com_quatro_digitos"),
        CheckConstraint("origem BETWEEN 0 AND 8", name="origem_valida"),
        CheckConstraint("versao >= 1", name="versao_positiva"),
    )

    produto_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("produtos.id", ondelete="CASCADE"), primary_key=True
    )
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    ncm: Mapped[str] = mapped_column(db.String(8), nullable=False)
    cest: Mapped[str | None] = mapped_column(db.String(7), nullable=True)
    cfop: Mapped[str] = mapped_column(db.String(4), nullable=False, default="5102")
    origem: Mapped[int] = mapped_column(nullable=False, default=0)
    gtin: Mapped[str | None] = mapped_column(db.String(14), nullable=True)
    unidade_tributavel: Mapped[str | None] = mapped_column(db.String(6), nullable=True)

    icms_cst: Mapped[str | None] = mapped_column(db.String(3), nullable=True)
    icms_csosn: Mapped[str | None] = mapped_column(db.String(3), nullable=True)
    icms_aliquota: Mapped[Decimal] = mapped_column(
        db.Numeric(7, 4), nullable=False, default=Decimal("0")
    )
    icms_modalidade_bc: Mapped[int] = mapped_column(nullable=False, default=3)
    icms_reducao_bc: Mapped[Decimal] = mapped_column(
        db.Numeric(7, 4), nullable=False, default=Decimal("0")
    )

    pis_cst: Mapped[str] = mapped_column(db.String(2), nullable=False, default="07")
    pis_aliquota: Mapped[Decimal] = mapped_column(
        db.Numeric(7, 4), nullable=False, default=Decimal("0")
    )
    cofins_cst: Mapped[str] = mapped_column(db.String(2), nullable=False, default="07")
    cofins_aliquota: Mapped[Decimal] = mapped_column(
        db.Numeric(7, 4), nullable=False, default=Decimal("0")
    )

    revisao_pendente: Mapped[bool] = mapped_column(default=True, nullable=False)
    observacao: Mapped[str | None] = mapped_column(db.String(500), nullable=True)
    versao: Mapped[int] = mapped_column(default=1, nullable=False)
    atualizado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    produto: Mapped[Produto] = relationship(back_populates="fiscal")
    atualizado_por: Mapped[Usuario | None] = relationship(foreign_keys=[atualizado_por_id])

    @property
    def completo(self) -> bool:
        """Diz se o cadastro tem o mínimo para a nota não ser rejeitada."""
        if len(self.ncm or "") != 8 or len(self.cfop or "") != 4:
            return False
        return bool((self.icms_cst or "").strip() or (self.icms_csosn or "").strip())


class DocumentoFiscal(db.Model):
    """Um documento fiscal eletrônico. Espelha DocumentoFiscal do TPA."""

    __tablename__ = "documentos_fiscais"
    __table_args__ = (
        UniqueConstraint("chave", name="chave_unica"),
        UniqueConstraint("empresa_id", "modelo", "ambiente", "serie", "numero", name="numeracao_unica_ambiente"),
        UniqueConstraint("venda_id", name="documento_por_venda"),
        CheckConstraint(
            "status IN ('pendente', 'processando', 'autorizado', 'rejeitado', "
            "'denegado', 'cancelado', 'contingencia', 'falhou')",
            name="status_valido",
        ),
        CheckConstraint("modelo IN (55, 65, 59)", name="modelo_valido"),
        CheckConstraint("ambiente IN (1, 2)", name="ambiente_valido"),
        CheckConstraint("tentativas >= 0", name="tentativas_nao_negativas"),
        CheckConstraint("versao >= 1", name="versao_positiva"),
        Index("ix_documentos_fiscais_empresa_status", "empresa_id", "status"),
        Index("ix_documentos_fiscais_empresa_emissao", "empresa_id", "data_emissao"),
        Index("ix_documentos_fiscais_serie_numero", "empresa_id", "serie", "numero"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False
    )
    venda_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("vendas.id", ondelete="RESTRICT"), nullable=True
    )

    chave: Mapped[str] = mapped_column(db.String(44), nullable=False, index=True)
    codigo_numerico: Mapped[int] = mapped_column(nullable=False)
    modelo: Mapped[int] = mapped_column(nullable=False, default=65)
    serie: Mapped[int] = mapped_column(nullable=False, default=1)
    numero: Mapped[int] = mapped_column(db.BigInteger, nullable=False)
    ambiente: Mapped[int] = mapped_column(nullable=False, default=2)
    tipo_emissao: Mapped[int] = mapped_column(nullable=False, default=1)

    cnpj_emitente: Mapped[str] = mapped_column(db.String(14), nullable=False, index=True)
    documento_destinatario: Mapped[str | None] = mapped_column(db.String(14), nullable=True)
    nome_destinatario: Mapped[str | None] = mapped_column(db.String(60), nullable=True)

    status: Mapped[str] = mapped_column(db.String(20), nullable=False, default="pendente")
    codigo_status: Mapped[str | None] = mapped_column(db.String(4), nullable=True)
    mensagem_status: Mapped[str | None] = mapped_column(db.String(500), nullable=True)
    protocolo_autorizacao: Mapped[str | None] = mapped_column(db.String(20), nullable=True)
    protocolo_cancelamento: Mapped[str | None] = mapped_column(db.String(20), nullable=True)
    motivo_cancelamento: Mapped[str | None] = mapped_column(db.String(500), nullable=True)

    total_produtos: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False, default=Decimal("0"))
    total_desconto: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False, default=Decimal("0"))
    total_documento: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False, default=Decimal("0"))
    total_base_icms: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False, default=Decimal("0"))
    total_icms: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False, default=Decimal("0"))
    total_pis: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False, default=Decimal("0"))
    total_cofins: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False, default=Decimal("0"))
    troco: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False, default=Decimal("0"))

    qrcode_conteudo: Mapped[str | None] = mapped_column(db.String(1000), nullable=True)
    digest_value: Mapped[str | None] = mapped_column(db.String(60), nullable=True)
    provedor: Mapped[str] = mapped_column(db.String(40), nullable=False, default="homologacao_interna")
    #: verdadeiro quando o documento veio do provedor de ensaio
    sem_valor_fiscal: Mapped[bool] = mapped_column(default=False, nullable=False)
    tentativas: Mapped[int] = mapped_column(default=0, nullable=False)

    data_emissao: Mapped[datetime] = mapped_column(db.DateTime(timezone=True), nullable=False)
    data_autorizacao: Mapped[datetime | None] = mapped_column(db.DateTime(timezone=True), nullable=True)
    data_cancelamento: Mapped[datetime | None] = mapped_column(db.DateTime(timezone=True), nullable=True)

    versao: Mapped[int] = mapped_column(default=1, nullable=False)
    emitido_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    empresa: Mapped[Empresa] = relationship()
    venda: Mapped["Venda | None"] = relationship(back_populates="documento_fiscal")
    emitido_por: Mapped[Usuario | None] = relationship(foreign_keys=[emitido_por_id])
    itens: Mapped[list["DocumentoFiscalItem"]] = relationship(
        back_populates="documento",
        cascade="all, delete-orphan",
        order_by="DocumentoFiscalItem.numero_item",
    )
    pagamentos: Mapped[list["DocumentoFiscalPagamento"]] = relationship(
        back_populates="documento", cascade="all, delete-orphan"
    )
    arquivos: Mapped[list["DocumentoFiscalXml"]] = relationship(
        back_populates="documento", cascade="all, delete-orphan"
    )
    eventos: Mapped[list["DocumentoFiscalEvento"]] = relationship(
        back_populates="documento",
        cascade="all, delete-orphan",
        order_by="DocumentoFiscalEvento.criado_em",
    )

    @property
    def status_rotulo(self) -> str:
        return {
            "pendente": "Aguardando emissão",
            "processando": "Transmitindo",
            "autorizado": "Autorizado",
            "rejeitado": "Rejeitado",
            "denegado": "Denegado",
            "cancelado": "Cancelado",
            "contingencia": "Em contingência",
            "falhou": "Falhou",
        }.get(self.status, self.status)

    @property
    def chave_formatada(self) -> str:
        digitos = self.chave or ""
        return " ".join(digitos[i : i + 4] for i in range(0, len(digitos), 4))

    @property
    def numero_formatado(self) -> str:
        return f"{self.numero:09d}"

    @property
    def vale_como_documento_fiscal(self) -> bool:
        return self.status == "autorizado" and not self.sem_valor_fiscal


class DocumentoFiscalItem(db.Model):
    """Espelha DocumentoItem do TPA Centralizador."""

    __tablename__ = "documentos_fiscais_itens"
    __table_args__ = (
        UniqueConstraint("documento_id", "numero_item", name="documento_item_numero"),
        CheckConstraint("quantidade > 0", name="quantidade_positiva"),
        CheckConstraint("numero_item > 0", name="numero_item_positivo"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    documento_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("documentos_fiscais.id", ondelete="CASCADE"), nullable=False
    )
    produto_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("produtos.id", ondelete="SET NULL"), nullable=True
    )
    numero_item: Mapped[int] = mapped_column(nullable=False)
    codigo_produto: Mapped[str] = mapped_column(db.String(60), nullable=False)
    codigo_ean: Mapped[str | None] = mapped_column(db.String(14), nullable=True)
    descricao: Mapped[str] = mapped_column(db.String(120), nullable=False)
    ncm: Mapped[str] = mapped_column(db.String(8), nullable=False)
    cest: Mapped[str | None] = mapped_column(db.String(7), nullable=True)
    cfop: Mapped[str] = mapped_column(db.String(4), nullable=False)
    unidade: Mapped[str] = mapped_column(db.String(6), nullable=False)
    quantidade: Mapped[Decimal] = mapped_column(db.Numeric(15, 4), nullable=False)
    valor_unitario: Mapped[Decimal] = mapped_column(db.Numeric(21, 10), nullable=False)
    valor_total: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False)
    valor_desconto: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False, default=Decimal("0"))

    icms_origem: Mapped[int] = mapped_column(nullable=False, default=0)
    icms_cst: Mapped[str | None] = mapped_column(db.String(3), nullable=True)
    icms_csosn: Mapped[str | None] = mapped_column(db.String(3), nullable=True)
    icms_base_calculo: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False, default=Decimal("0"))
    icms_aliquota: Mapped[Decimal] = mapped_column(db.Numeric(7, 4), nullable=False, default=Decimal("0"))
    icms_valor: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False, default=Decimal("0"))
    pis_cst: Mapped[str] = mapped_column(db.String(2), nullable=False, default="07")
    pis_valor: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False, default=Decimal("0"))
    cofins_cst: Mapped[str] = mapped_column(db.String(2), nullable=False, default="07")
    cofins_valor: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False, default=Decimal("0"))

    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )

    documento: Mapped[DocumentoFiscal] = relationship(back_populates="itens")


class DocumentoFiscalPagamento(db.Model):
    """Espelha DocumentoPagamento do TPA Centralizador."""

    __tablename__ = "documentos_fiscais_pagamentos"
    __table_args__ = (CheckConstraint("valor > 0", name="valor_positivo"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    documento_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("documentos_fiscais.id", ondelete="CASCADE"), nullable=False
    )
    forma: Mapped[str] = mapped_column(db.String(2), nullable=False)
    valor: Mapped[Decimal] = mapped_column(db.Numeric(16, 2), nullable=False)
    cnpj_credenciadora: Mapped[str | None] = mapped_column(db.String(14), nullable=True)
    bandeira: Mapped[str | None] = mapped_column(db.String(2), nullable=True)
    numero_autorizacao: Mapped[str | None] = mapped_column(db.String(20), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )

    documento: Mapped[DocumentoFiscal] = relationship(back_populates="pagamentos")


class DocumentoFiscalXml(db.Model):
    """Espelha DocumentoXml do TPA. Guarda o XML por cinco anos, como exigido."""

    __tablename__ = "documentos_fiscais_xml"
    __table_args__ = (
        UniqueConstraint("documento_id", "tipo", name="documento_tipo_xml"),
        CheckConstraint(
            "tipo IN ('enviado', 'retorno', 'autorizado', 'cancelamento', 'inutilizacao')",
            name="tipo_valido",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    documento_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("documentos_fiscais.id", ondelete="CASCADE"), nullable=False
    )
    tipo: Mapped[str] = mapped_column(db.String(20), nullable=False)
    arquivo_xml: Mapped[str] = mapped_column(db.Text, nullable=False)
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )

    documento: Mapped[DocumentoFiscal] = relationship(back_populates="arquivos")


class DocumentoFiscalEvento(db.Model):
    """Trilha de cada tentativa: o que foi enviado, o que a SEFAZ respondeu."""

    __tablename__ = "documentos_fiscais_eventos"
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('emissao', 'reenvio', 'cancelamento', 'consulta', 'contingencia', 'erro')",
            name="tipo_valido",
        ),
        Index("ix_documentos_fiscais_eventos_documento", "documento_id", "criado_em"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    documento_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("documentos_fiscais.id", ondelete="CASCADE"), nullable=False
    )
    tipo: Mapped[str] = mapped_column(db.String(20), nullable=False)
    provedor: Mapped[str] = mapped_column(db.String(40), nullable=False)
    codigo_status: Mapped[str | None] = mapped_column(db.String(4), nullable=True)
    mensagem: Mapped[str | None] = mapped_column(db.String(1000), nullable=True)
    sucesso: Mapped[bool] = mapped_column(default=False, nullable=False)
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )

    documento: Mapped[DocumentoFiscal] = relationship(back_populates="eventos")
    usuario: Mapped[Usuario | None] = relationship(foreign_keys=[usuario_id])


class InutilizacaoFiscal(db.Model):
    """Espelha Inutilizacao do TPA: números de nota que ficaram sem uso."""

    __tablename__ = "inutilizacoes_fiscais"
    __table_args__ = (
        UniqueConstraint("cnpj", "modelo", "ambiente", "serie", "numero", name="inutilizacao_unica_ambiente"),
        CheckConstraint(
            "status IN ('pendente', 'autorizada', 'rejeitada', 'descartada')",
            name="status_valido",
        ),
        CheckConstraint("numero > 0", name="numero_positivo"),
        Index("ix_inutilizacoes_fiscais_empresa_status", "empresa_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False
    )
    cnpj: Mapped[str] = mapped_column(db.String(14), nullable=False, index=True)
    modelo: Mapped[int] = mapped_column(nullable=False, default=65)
    ambiente: Mapped[int] = mapped_column(nullable=False, default=2)
    serie: Mapped[int] = mapped_column(nullable=False)
    numero: Mapped[int] = mapped_column(db.BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(db.String(20), nullable=False, default="pendente")
    justificativa: Mapped[str | None] = mapped_column(db.String(255), nullable=True)
    protocolo: Mapped[str | None] = mapped_column(db.String(20), nullable=True)
    codigo_status: Mapped[str | None] = mapped_column(db.String(4), nullable=True)
    mensagem: Mapped[str | None] = mapped_column(db.String(500), nullable=True)
    origem: Mapped[str] = mapped_column(db.String(20), nullable=False, default="lacuna")
    arquivo_xml: Mapped[str | None] = mapped_column(db.Text, nullable=True)
    detectado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    resolvido_em: Mapped[datetime | None] = mapped_column(db.DateTime(timezone=True), nullable=True)
    resolvido_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )

    empresa: Mapped[Empresa] = relationship()
    resolvido_por: Mapped[Usuario | None] = relationship(foreign_keys=[resolvido_por_id])

    @property
    def status_rotulo(self) -> str:
        return {
            "pendente": "Pendente",
            "autorizada": "Inutilizada",
            "rejeitada": "Rejeitada",
            "descartada": "Descartada",
        }.get(self.status, self.status)


class IntegracaoMGV6Produto(db.Model):
    __tablename__ = "integracoes_mgv6_produtos"
    __table_args__ = (
        UniqueConstraint("empresa_id", "codigo_item", name="empresa_codigo_item_mgv6"),
        CheckConstraint(
            "status IN ('pendente', 'validado', 'mgv_ok', 'aguardando_balanca', 'erro')",
            name="status_valido",
        ),
        CheckConstraint("versao >= 1", name="versao_positiva"),
        Index("ix_integracoes_mgv6_empresa_status", "empresa_id", "status"),
    )

    produto_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, db.ForeignKey("produtos.id", ondelete="CASCADE"), primary_key=True
    )
    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    codigo_item: Mapped[str] = mapped_column(db.String(6), nullable=False)
    descricao_mgv: Mapped[str | None] = mapped_column(db.String(240), nullable=True)
    status: Mapped[str] = mapped_column(db.String(24), nullable=False, default="pendente")
    preco_mgv: Mapped[Decimal | None] = mapped_column(db.Numeric(14, 2), nullable=True)
    preco_enviado: Mapped[Decimal | None] = mapped_column(db.Numeric(14, 2), nullable=True)
    numero_importacao: Mapped[int | None] = mapped_column(nullable=True)
    ultima_mensagem: Mapped[str | None] = mapped_column(db.String(500), nullable=True)
    ultima_tentativa_em: Mapped[datetime | None] = mapped_column(
        db.DateTime(timezone=True), nullable=True
    )
    validado_em: Mapped[datetime | None] = mapped_column(db.DateTime(timezone=True), nullable=True)
    sincronizado_mgv_em: Mapped[datetime | None] = mapped_column(
        db.DateTime(timezone=True), nullable=True
    )
    carga_solicitada_em: Mapped[datetime | None] = mapped_column(
        db.DateTime(timezone=True), nullable=True
    )
    balanca_confirmada_em: Mapped[datetime | None] = mapped_column(
        db.DateTime(timezone=True), nullable=True
    )
    versao: Mapped[int] = mapped_column(default=1, nullable=False)
    atualizado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    produto: Mapped[Produto] = relationship(back_populates="mgv6")
    empresa: Mapped[Empresa] = relationship()
    atualizado_por: Mapped[Usuario | None] = relationship(foreign_keys=[atualizado_por_id])

    @property
    def status_rotulo(self) -> str:
        return {
            "pendente": "Aguardando validação",
            "validado": "Vínculo validado",
            "mgv_ok": "MGV6 + Prix confirmados",
            "aguardando_balanca": "Aguardando balança",
            "erro": "Erro de sincronização",
        }.get(self.status, self.status)

    @property
    def status_badge(self) -> str:
        return {
            "pendente": "warning",
            "validado": "warning",
            "mgv_ok": "success",
            "aguardando_balanca": "warning",
            "erro": "danger",
        }.get(self.status, "muted")


class ConfiguracaoBalanca(db.Model):
    """Como ler a etiqueta da balança e como gerar a carga de preços.

    Nada aqui tem padrão adivinhado: o layout da etiqueta é o que decide quanto
    o cliente paga e quanto sai do estoque. Enquanto ``ativo`` for falso, o
    código lido segue direto para a busca normal, sem decodificação.
    """

    __tablename__ = "configuracoes_balanca"
    __table_args__ = (
        CheckConstraint("conteudo IN ('peso', 'preco')", name="conteudo_valido"),
        CheckConstraint("tamanho_plu BETWEEN 4 AND 7", name="tamanho_plu_valido"),
        CheckConstraint("casas_decimais BETWEEN 0 AND 3", name="casas_decimais_validas"),
        CheckConstraint("tolerancia >= 0", name="tolerancia_nao_negativa"),
        CheckConstraint("versao >= 1", name="versao_positiva"),
    )

    empresa_id: Mapped[int] = mapped_column(
        db.ForeignKey("empresas.id", ondelete="RESTRICT"), primary_key=True
    )
    ativo: Mapped[bool] = mapped_column(default=False, nullable=False)
    modelo: Mapped[str | None] = mapped_column(db.String(60), nullable=True)

    prefixo: Mapped[str] = mapped_column(db.String(1), nullable=False, default="2")
    tamanho_plu: Mapped[int] = mapped_column(nullable=False, default=6)
    conteudo: Mapped[str] = mapped_column(db.String(10), nullable=False, default="preco")
    casas_decimais: Mapped[int] = mapped_column(nullable=False, default=2)

    #: diferença aceita entre o total da etiqueta e o total recalculado
    tolerancia: Mapped[Decimal] = mapped_column(
        db.Numeric(6, 2), nullable=False, default=Decimal("0.05")
    )
    #: quando verdadeiro, divergência acima da tolerância recusa o item
    bloquear_divergencia: Mapped[bool] = mapped_column(default=True, nullable=False)

    separador_carga: Mapped[str] = mapped_column(db.String(3), nullable=False, default=";")
    codificacao_carga: Mapped[str] = mapped_column(db.String(20), nullable=False, default="latin-1")
    quebra_linha_carga: Mapped[str] = mapped_column(db.String(8), nullable=False, default="crlf")
    campos_carga: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )

    versao: Mapped[int] = mapped_column(default=1, nullable=False)
    atualizado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, nullable=False
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    empresa: Mapped[Empresa] = relationship()
    atualizado_por: Mapped[Usuario | None] = relationship(foreign_keys=[atualizado_por_id])

    @property
    def conteudo_rotulo(self) -> str:
        return {
            "peso": "Peso embutido (gramas)",
            "preco": "Valor total embutido (centavos)",
        }.get(self.conteudo, self.conteudo)
