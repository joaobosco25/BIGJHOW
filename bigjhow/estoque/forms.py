from __future__ import annotations

from decimal import Decimal

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import (
    BooleanField,
    DecimalField,
    HiddenField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, Optional, Regexp


UNIDADES = [
    ("UN", "Unidade (UN)"),
    ("KG", "Quilograma (KG)"),
    ("CXA", "Caixa (CXA)"),
    ("LT", "Litro (LT)"),
    ("PCT", "Pacote (PCT)"),
]


class ProdutoForm(FlaskForm):
    versao = HiddenField(default="1")
    codigo_externo = StringField("Código externo", validators=[Optional(), Length(max=80)])
    mgv6_codigo_item = StringField(
        "Código do item no MGV6",
        validators=[
            Optional(),
            Regexp(r"^\d{1,6}$", message="Use de 1 a 6 dígitos, exatamente como no MGV6."),
        ],
    )
    nome = StringField("Nome do produto", validators=[DataRequired(), Length(min=2, max=240)])
    categoria = StringField("Categoria", validators=[DataRequired(), Length(min=2, max=160)])
    unidade = SelectField("Unidade", choices=UNIDADES, validators=[DataRequired()])
    pesado = BooleanField("Produto vendido por peso")
    preco_venda = DecimalField(
        "Preço de venda",
        places=2,
        rounding=None,
        validators=[InputRequired(), NumberRange(min=Decimal("0"), max=Decimal("999999999999.99"))],
    )
    revisao_pendente = BooleanField("Manter sinalizado para revisão")
    submit = SubmitField("Salvar produto")


class AjusteEstoqueForm(FlaskForm):
    operacao = SelectField(
        "Operação",
        choices=[
            ("inventario", "Inventário — definir saldo contado"),
            ("entrada", "Entrada — somar ao saldo"),
            ("saida", "Saída — retirar do saldo"),
        ],
        validators=[DataRequired()],
    )
    # StringField preserva a vírgula digitada no navegador. DecimalField renderiza
    # um input numérico, cujo tratamento de vírgula varia por navegador/idioma e
    # pode transformar "2,500" em "2500" antes de o servidor receber o valor.
    quantidade = StringField(
        "Quantidade",
        validators=[DataRequired(), Length(max=16)],
    )
    motivo = TextAreaField("Motivo", validators=[DataRequired(), Length(min=5, max=500)])
    submit = SubmitField("Confirmar movimentação")


class AcaoForm(FlaskForm):
    submit = SubmitField("Confirmar")


class NovoInventarioLoteForm(FlaskForm):
    descricao = StringField(
        "Identificação do inventário",
        default="Inventário inicial",
        validators=[DataRequired(), Length(min=5, max=160)],
    )
    submit = SubmitField("Criar rascunho")


class InventarioVersaoForm(FlaskForm):
    versao = HiddenField(validators=[DataRequired()])
    submit = SubmitField("Salvar")


class ImportarInventarioForm(InventarioVersaoForm):
    arquivo = FileField(
        "Planilha preenchida",
        validators=[
            FileRequired(),
            FileAllowed(["xlsx"], "Envie somente a planilha .xlsx gerada pelo sistema."),
        ],
    )
    submit = SubmitField("Importar planilha")


class FinalizarInventarioForm(InventarioVersaoForm):
    confirmacao = BooleanField(
        "Confirmo que revisei as quantidades preenchidas",
        validators=[DataRequired()],
    )
    submit = SubmitField("Finalizar itens contados")
