from flask_wtf import FlaskForm
from wtforms import HiddenField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class IniciarVendaForm(FlaskForm):
    submit = SubmitField("Iniciar atendimento")


class LeituraCodigoForm(FlaskForm):
    versao = HiddenField(validators=[DataRequired()])
    codigo = StringField(
        "Código do produto", validators=[DataRequired(), Length(min=3, max=80)]
    )
    quantidade = StringField(
        "Quantidade ou peso", validators=[Optional(), Length(max=32)]
    )
    submit = SubmitField("Adicionar")


class AtualizarItemForm(FlaskForm):
    versao = HiddenField(validators=[DataRequired()])
    quantidade = StringField(
        "Quantidade", validators=[DataRequired(), Length(max=32)]
    )
    submit = SubmitField("Atualizar")


class AcaoVendaForm(FlaskForm):
    versao = HiddenField(validators=[DataRequired()])
    submit = SubmitField("Confirmar")


class FinalizarVendaForm(AcaoVendaForm):
    pagamento_dinheiro = StringField("Dinheiro", validators=[Optional(), Length(max=32)])
    dinheiro_recebido = StringField(
        "Valor recebido", validators=[Optional(), Length(max=32)]
    )
    pagamento_pix = StringField("PIX", validators=[Optional(), Length(max=32)])
    pagamento_debito = StringField(
        "Cartão de débito", validators=[Optional(), Length(max=32)]
    )
    pagamento_credito = StringField(
        "Cartão de crédito", validators=[Optional(), Length(max=32)]
    )
    submit = SubmitField("Finalizar e emitir ticket")


class CancelarVendaForm(AcaoVendaForm):
    motivo = TextAreaField(
        "Motivo do cancelamento",
        validators=[DataRequired(), Length(min=5, max=500)],
    )
    submit = SubmitField("Cancelar venda e devolver estoque")
