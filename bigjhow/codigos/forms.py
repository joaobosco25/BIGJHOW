from flask_wtf import FlaskForm
from wtforms import BooleanField, HiddenField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length


class CodigoProdutoForm(FlaskForm):
    codigo = StringField(
        "Código para leitura", validators=[DataRequired(), Length(min=3, max=80)]
    )
    tipo = SelectField(
        "Tipo",
        choices=[
            ("ean", "EAN/GTIN"),
            ("interno", "Código interno"),
            ("plu", "PLU"),
            ("outro", "Outro"),
        ],
        validators=[DataRequired()],
    )
    principal = BooleanField("Definir como código principal")
    submit = SubmitField("Cadastrar código")


class AcaoCodigoForm(FlaskForm):
    versao = HiddenField(validators=[DataRequired()])
    submit = SubmitField("Confirmar")
