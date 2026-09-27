from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, EqualTo, Length


class LoginForm(FlaskForm):
    usuario = StringField("Usuário", validators=[DataRequired(), Length(max=254)])
    senha = PasswordField("Senha", validators=[DataRequired(), Length(max=128)])
    submit = SubmitField("Entrar com segurança")


class TrocaSenhaForm(FlaskForm):
    senha_atual = PasswordField(
        "Senha atual", validators=[DataRequired(), Length(max=128)]
    )
    nova_senha = PasswordField(
        "Nova senha", validators=[DataRequired(), Length(min=12, max=128)]
    )
    confirmar_senha = PasswordField(
        "Confirmar nova senha",
        validators=[
            DataRequired(),
            Length(max=128),
            EqualTo("nova_senha", message="As senhas não coincidem."),
        ],
    )
    submit = SubmitField("Atualizar senha")
