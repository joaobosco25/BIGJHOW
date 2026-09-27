from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange, Optional


CONTEUDOS = [
    ("preco", "Valor total embutido (centavos)"),
    ("peso", "Peso embutido (gramas)"),
]
QUEBRAS = [("crlf", "Windows (CRLF)"), ("lf", "Unix (LF)")]
CODIFICACOES = [
    ("latin-1", "Latin-1 / ISO-8859-1 (padrão da maioria das balanças)"),
    ("utf-8", "UTF-8"),
    ("cp850", "CP850"),
]


class ConfiguracaoBalancaForm(FlaskForm):
    ativo = BooleanField("Ler etiqueta de balança no caixa")
    modelo = StringField("Modelo da balança", validators=[Optional(), Length(max=60)])
    prefixo = StringField(
        "Primeiro dígito da etiqueta", validators=[DataRequired(), Length(min=1, max=1)],
        default="2",
    )
    tamanho_plu = IntegerField(
        "Dígitos do PLU", validators=[DataRequired(), NumberRange(min=4, max=7)], default=6
    )
    conteudo = SelectField("O que vem dentro do código", choices=CONTEUDOS, default="preco")
    casas_decimais = IntegerField(
        "Casas decimais do campo", validators=[NumberRange(min=0, max=3)], default=2
    )
    tolerancia = StringField(
        "Divergência aceita (R$)", validators=[Optional(), Length(max=8)], default="0,05"
    )
    bloquear_divergencia = BooleanField(
        "Recusar o item quando a divergência passar da tolerância", default=True
    )

    separador_carga = StringField(
        "Separador do arquivo de carga", validators=[DataRequired(), Length(min=1, max=3)],
        default=";",
    )
    codificacao_carga = SelectField(
        "Codificação do arquivo", choices=CODIFICACOES, default="latin-1"
    )
    quebra_linha_carga = SelectField("Quebra de linha", choices=QUEBRAS, default="crlf")
    campos_carga = StringField(
        "Ordem das colunas", validators=[DataRequired(), Length(max=200)],
        default="plu,descricao,preco,tipo,validade_dias",
    )
    submit = SubmitField("Salvar configuração da balança")


class TesteEtiquetaForm(FlaskForm):
    codigo = StringField(
        "Código de barras da etiqueta",
        validators=[DataRequired(), Length(min=13, max=13)],
    )
    submit = SubmitField("Testar leitura")


class AcaoForm(FlaskForm):
    submit = SubmitField("Confirmar")
