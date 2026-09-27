from flask_wtf import FlaskForm
from wtforms import (
    HiddenField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, Optional


class AbrirTurnoForm(FlaskForm):
    terminal_id = SelectField("Terminal", validators=[DataRequired()])
    valor_inicial = StringField(
        "Fundo inicial em dinheiro",
        validators=[DataRequired(), Length(max=32)],
    )
    submit = SubmitField("Abrir turno")


class MovimentoTurnoForm(FlaskForm):
    turno_id = HiddenField(validators=[DataRequired()])
    tipo = SelectField(
        "Movimento",
        choices=[("sangria", "Sangria"), ("suprimento", "Suprimento")],
        validators=[DataRequired()],
    )
    valor = StringField("Valor", validators=[DataRequired(), Length(max=32)])
    motivo = TextAreaField(
        "Motivo", validators=[DataRequired(), Length(min=5, max=500)]
    )
    submit = SubmitField("Registrar movimento")


class DecisaoMovimentoForm(FlaskForm):
    versao = HiddenField(validators=[DataRequired()])
    motivo = TextAreaField(
        "Justificativa", validators=[DataRequired(), Length(min=5, max=500)]
    )
    submit = SubmitField("Confirmar decisão")


class FecharTurnoForm(FlaskForm):
    versao = HiddenField(validators=[DataRequired()])
    dinheiro = StringField(
        "Dinheiro contado", validators=[DataRequired(), Length(max=32)]
    )
    pix = StringField("PIX apurado", validators=[DataRequired(), Length(max=32)])
    debito = StringField(
        "Débito apurado", validators=[DataRequired(), Length(max=32)]
    )
    credito = StringField(
        "Crédito apurado", validators=[DataRequired(), Length(max=32)]
    )
    justificativa = TextAreaField(
        "Justificativa", validators=[Optional(), Length(max=500)]
    )
    submit = SubmitField("Fechar turno")


class TerminalForm(FlaskForm):
    codigo = StringField(
        "Código", validators=[DataRequired(), Length(min=3, max=40)]
    )
    nome = StringField(
        "Nome do terminal", validators=[DataRequired(), Length(min=3, max=120)]
    )
    submit = SubmitField("Cadastrar terminal")


class ImpressoraForm(FlaskForm):
    codigo = StringField(
        "Código", validators=[DataRequired(), Length(min=3, max=40)]
    )
    nome = StringField(
        "Nome da impressora", validators=[DataRequired(), Length(min=3, max=120)]
    )
    setor = StringField(
        "Setor", validators=[DataRequired(), Length(min=2, max=80)]
    )
    tipo = SelectField(
        "Finalidade",
        choices=[
            ("cupom", "Cupom não fiscal"),
            ("producao", "Produção"),
            ("etiqueta", "Etiqueta"),
            ("relatorio", "Relatório"),
        ],
        validators=[DataRequired()],
    )
    submit = SubmitField("Cadastrar impressora simulada")


class RegraImpressaoForm(FlaskForm):
    terminal_id = SelectField("Terminal", validators=[DataRequired()])
    impressora_id = SelectField("Impressora", validators=[DataRequired()])
    evento = SelectField(
        "Momento da impressão",
        choices=[
            ("venda_finalizada", "Venda finalizada"),
            ("venda_cancelada", "Venda cancelada"),
            ("turno_fechado", "Turno fechado"),
            ("movimento_caixa_aprovado", "Sangria/suprimento aprovado"),
        ],
        validators=[DataRequired()],
    )
    copias = SelectField(
        "Cópias", choices=[("1", "1"), ("2", "2"), ("3", "3")]
    )
    submit = SubmitField("Cadastrar regra")


class AcaoVersaoForm(FlaskForm):
    versao = HiddenField(validators=[DataRequired()])
    submit = SubmitField("Confirmar")


class ReimpressaoForm(FlaskForm):
    versao = HiddenField(validators=[DataRequired()])
    motivo = TextAreaField(
        "Motivo da reimpressão",
        validators=[DataRequired(), Length(min=5, max=500)],
    )
    submit = SubmitField("Criar reimpressão")
