from flask_wtf import FlaskForm
from wtforms import (
    HiddenField,
    PasswordField,
    SelectField,
    SelectMultipleField,
    StringField,
    SubmitField,
    widgets,
)
from wtforms.validators import DataRequired, EqualTo, Length


class CampoMultiplasOpcoes(SelectMultipleField):
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()


class CadastroUsuarioForm(FlaskForm):
    nome = StringField("Nome completo", validators=[DataRequired(), Length(min=3, max=120)])
    usuario = StringField("Usuário", validators=[DataRequired(), Length(min=3, max=32)])
    papel = SelectField("Perfil", validators=[DataRequired()])
    empresas = CampoMultiplasOpcoes("Empresas autorizadas", coerce=int)
    senha_temporaria = PasswordField(
        "Senha temporária", validators=[DataRequired(), Length(min=12, max=128)]
    )
    confirmar_senha = PasswordField(
        "Confirmar senha temporária",
        validators=[
            DataRequired(),
            Length(max=128),
            EqualTo("senha_temporaria", message="As senhas não coincidem."),
        ],
    )
    submit = SubmitField("Criar usuário")


class EdicaoUsuarioForm(FlaskForm):
    versao = HiddenField(validators=[DataRequired()])
    nome = StringField("Nome completo", validators=[DataRequired(), Length(min=3, max=120)])
    usuario = StringField("Usuário", validators=[DataRequired(), Length(max=254)])
    papel = SelectField("Perfil", validators=[DataRequired()])
    empresas = CampoMultiplasOpcoes("Empresas autorizadas", coerce=int)
    submit = SubmitField("Salvar alterações")


class RedefinirSenhaForm(FlaskForm):
    versao = HiddenField(validators=[DataRequired()])
    senha_temporaria = PasswordField(
        "Nova senha temporária", validators=[DataRequired(), Length(min=12, max=128)]
    )
    confirmar_senha = PasswordField(
        "Confirmar senha temporária",
        validators=[
            DataRequired(),
            Length(max=128),
            EqualTo("senha_temporaria", message="As senhas não coincidem."),
        ],
    )
    submit = SubmitField("Redefinir senha")


class AcaoUsuarioForm(FlaskForm):
    submit = SubmitField("Confirmar")
