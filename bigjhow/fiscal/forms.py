from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    IntegerField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional


PROVEDORES = [
    ("tpa_bridge", "Ponte com o emissor TPA da loja"),
    ("sefaz_direto", "SEFAZ direto (certificado A1 local)"),
    ("gateway", "Gateway de emissão (API de terceiro)"),
    ("homologacao_interna", "Ensaio interno (sem valor fiscal)"),
]

MODELOS = [("65", "NFC-e (modelo 65)"), ("55", "NF-e (modelo 55)"), ("59", "CF-e SAT (modelo 59)")]
AMBIENTES = [("2", "Homologação (testes)"), ("1", "Produção (valor fiscal)")]
REGIMES = [
    ("1", "Simples Nacional"),
    ("2", "Simples Nacional — excesso de sublimite"),
    ("3", "Regime normal"),
    ("4", "Simples Nacional — MEI"),
]
ORIGENS = [
    ("0", "0 — Nacional"),
    ("1", "1 — Estrangeira, importação direta"),
    ("2", "2 — Estrangeira, adquirida no mercado interno"),
    ("3", "3 — Nacional com mais de 40% de conteúdo importado"),
    ("4", "4 — Nacional, processos produtivos básicos"),
    ("5", "5 — Nacional com até 40% de conteúdo importado"),
    ("6", "6 — Estrangeira, importação direta, sem similar nacional"),
    ("7", "7 — Estrangeira, mercado interno, sem similar nacional"),
    ("8", "8 — Nacional, conteúdo de importação superior a 70%"),
]
CSOSN = [
    ("102", "102 — Tributada pelo Simples, sem permissão de crédito"),
    ("103", "103 — Isenção do ICMS para faixa de receita bruta"),
    ("300", "300 — Imune"),
    ("400", "400 — Não tributada pelo Simples Nacional"),
    ("500", "500 — ICMS cobrado anteriormente por substituição tributária"),
    ("101", "101 — Tributada com permissão de crédito"),
    ("900", "900 — Outros"),
]
CST_ICMS = [
    ("00", "00 — Tributada integralmente"),
    ("20", "20 — Com redução de base de cálculo"),
    ("40", "40 — Isenta"),
    ("41", "41 — Não tributada"),
    ("50", "50 — Suspensão"),
    ("51", "51 — Diferimento"),
    ("60", "60 — ICMS cobrado anteriormente por substituição tributária"),
    ("90", "90 — Outras"),
]
CST_CONTRIBUICAO = [
    ("07", "07 — Operação isenta da contribuição"),
    ("01", "01 — Operação tributável, alíquota básica"),
    ("02", "02 — Operação tributável, alíquota diferenciada"),
    ("04", "04 — Operação tributável, alíquota zero"),
    ("05", "05 — Operação tributável por substituição tributária"),
    ("06", "06 — Operação tributável, alíquota zero"),
    ("08", "08 — Operação sem incidência da contribuição"),
    ("09", "09 — Operação com suspensão da contribuição"),
    ("49", "49 — Outras operações de saída"),
    ("99", "99 — Outras operações"),
]
CREDENCIAIS = [
    ("csc_token", "CSC (token) da NFC-e"),
    ("certificado_senha", "Senha do certificado A1"),
    ("gateway_token", "Token do gateway"),
]


class EstabelecimentoForm(FlaskForm):
    cnpj = StringField("CNPJ", validators=[DataRequired(), Length(min=14, max=18)])
    razao_social = StringField("Razão social", validators=[DataRequired(), Length(min=3, max=60)])
    nome_fantasia = StringField("Nome fantasia", validators=[Optional(), Length(max=60)])
    inscricao_estadual = StringField(
        "Inscrição estadual", validators=[DataRequired(), Length(min=2, max=20)]
    )
    inscricao_municipal = StringField("Inscrição municipal", validators=[Optional(), Length(max=20)])
    crt = SelectField("Regime tributário", choices=REGIMES, default="1")

    logradouro = StringField("Logradouro", validators=[DataRequired(), Length(min=2, max=60)])
    numero = StringField("Número", validators=[Optional(), Length(max=20)])
    complemento = StringField("Complemento", validators=[Optional(), Length(max=60)])
    bairro = StringField("Bairro", validators=[DataRequired(), Length(min=2, max=60)])
    codigo_municipio = StringField(
        "Código IBGE do município", validators=[DataRequired(), Length(min=7, max=7)]
    )
    municipio = StringField("Município", validators=[DataRequired(), Length(min=2, max=60)])
    uf = StringField("UF", validators=[DataRequired(), Length(min=2, max=2)])
    cep = StringField("CEP", validators=[DataRequired(), Length(min=8, max=9)])
    telefone = StringField("Telefone", validators=[Optional(), Length(max=15)])

    modelo = SelectField("Modelo do documento", choices=MODELOS, default="65")
    serie = IntegerField("Série", validators=[NumberRange(min=0, max=999)], default=1)
    proximo_numero = IntegerField(
        "Próximo número", validators=[NumberRange(min=1, max=999_999_999)], default=1
    )
    ambiente = SelectField("Ambiente", choices=AMBIENTES, default="2")
    natureza_operacao = StringField(
        "Natureza da operação", validators=[DataRequired(), Length(min=3, max=60)],
        default="VENDA AO CONSUMIDOR",
    )
    informacoes_complementares = TextAreaField(
        "Informações complementares do cupom", validators=[Optional(), Length(max=500)]
    )

    provedor = SelectField("Rota de emissão", choices=PROVEDORES, default="homologacao_interna")
    csc_id = StringField("idCSC", validators=[Optional(), Length(max=6)])
    url_consulta_qrcode = StringField(
        "URL de consulta do QR Code", validators=[Optional(), Length(max=300)]
    )
    url_consulta_chave = StringField(
        "URL de consulta por chave", validators=[Optional(), Length(max=300)]
    )

    url_autorizacao = StringField("NFeAutorizacao4", validators=[Optional(), Length(max=300)])
    url_evento = StringField("NFeRecepcaoEvento4", validators=[Optional(), Length(max=300)])
    url_inutilizacao = StringField("NFeInutilizacao4", validators=[Optional(), Length(max=300)])
    url_consulta = StringField("NFeConsultaProtocolo4", validators=[Optional(), Length(max=300)])
    url_status = StringField("NFeStatusServico4", validators=[Optional(), Length(max=300)])
    certificado_caminho = StringField(
        "Caminho do certificado A1 no servidor", validators=[Optional(), Length(max=300)]
    )

    emissor_host = StringField("Endereço do emissor TPA", validators=[Optional(), Length(max=120)])
    emissor_porta = StringField("Porta do emissor TPA", validators=[Optional(), Length(max=5)])
    pasta_envio = StringField("Pasta de envio", validators=[Optional(), Length(max=300)])
    pasta_retorno = StringField("Pasta de retorno", validators=[Optional(), Length(max=300)])
    padrao_sucesso = StringField(
        "Padrão de sucesso do emissor", validators=[Optional(), Length(max=120)]
    )

    url_base = StringField("URL base do gateway", validators=[Optional(), Length(max=300)])
    estilo_autenticacao = SelectField(
        "Autenticação do gateway",
        choices=[("basic", "Basic"), ("bearer", "Bearer"), ("token", "Token")],
        default="basic",
    )

    responsavel_cnpj = StringField("CNPJ do responsável técnico", validators=[Optional(), Length(max=18)])
    responsavel_contato = StringField("Contato", validators=[Optional(), Length(max=60)])
    responsavel_email = StringField("E-mail", validators=[Optional(), Length(max=60)])
    responsavel_telefone = StringField("Telefone", validators=[Optional(), Length(max=15)])

    enderecos_conferidos = BooleanField("Confirmo que conferi os endereços com a SEFAZ da UF")
    permitir_contingencia = BooleanField("Permitir concluir venda quando a SEFAZ não responder")
    emissao_ativa = BooleanField("Emitir documento fiscal a cada venda finalizada")
    submit = SubmitField("Salvar configuração fiscal")


class CredencialForm(FlaskForm):
    nome = SelectField("Credencial", choices=CREDENCIAIS)
    valor = StringField("Valor", validators=[DataRequired(), Length(min=4, max=400)])
    submit = SubmitField("Guardar com segurança")


class AcaoForm(FlaskForm):
    submit = SubmitField("Confirmar")


class ClassificacaoForm(FlaskForm):
    ncm = StringField("NCM", validators=[DataRequired(), Length(min=8, max=10)])
    cest = StringField("CEST", validators=[Optional(), Length(max=9)])
    cfop = StringField("CFOP", validators=[DataRequired(), Length(min=4, max=4)], default="5102")
    origem = SelectField("Origem da mercadoria", choices=ORIGENS, default="0")
    gtin = StringField("GTIN / código de barras", validators=[Optional(), Length(max=14)])
    unidade_tributavel = StringField("Unidade tributável", validators=[Optional(), Length(max=6)])
    icms_csosn = SelectField("CSOSN (Simples Nacional)", choices=[("", "—")] + CSOSN, default="102")
    icms_cst = SelectField("CST de ICMS (regime normal)", choices=[("", "—")] + CST_ICMS, default="")
    icms_aliquota = StringField("Alíquota de ICMS (%)", validators=[Optional(), Length(max=8)], default="0")
    icms_reducao_bc = StringField("Redução da base (%)", validators=[Optional(), Length(max=8)], default="0")
    pis_cst = SelectField("CST de PIS", choices=CST_CONTRIBUICAO, default="07")
    pis_aliquota = StringField("Alíquota de PIS (%)", validators=[Optional(), Length(max=8)], default="0")
    cofins_cst = SelectField("CST de COFINS", choices=CST_CONTRIBUICAO, default="07")
    cofins_aliquota = StringField("Alíquota de COFINS (%)", validators=[Optional(), Length(max=8)], default="0")
    observacao = TextAreaField("Observação", validators=[Optional(), Length(max=500)])
    submit = SubmitField("Salvar classificação")


class ClassificacaoLoteForm(ClassificacaoForm):
    confirmacao = StringField(
        "Digite APLICAR para confirmar", validators=[DataRequired(), Length(min=7, max=7)]
    )
    submit = SubmitField("Aplicar a todos os produtos sem classificação")


class CancelarDocumentoForm(FlaskForm):
    justificativa = TextAreaField(
        "Justificativa (de 15 a 255 caracteres, exigência da SEFAZ)",
        validators=[DataRequired(), Length(min=15, max=255)],
    )
    submit = SubmitField("Cancelar documento na SEFAZ")


class InutilizarForm(FlaskForm):
    numero_inicial = IntegerField(
        "Número inicial", validators=[DataRequired(), NumberRange(min=1, max=999_999_999)]
    )
    numero_final = IntegerField(
        "Número final", validators=[DataRequired(), NumberRange(min=1, max=999_999_999)]
    )
    justificativa = TextAreaField(
        "Justificativa (de 15 a 255 caracteres)",
        validators=[DataRequired(), Length(min=15, max=255)],
    )
    submit = SubmitField("Inutilizar faixa")


class ImportacaoForm(FlaskForm):
    pasta = StringField(
        "Pasta de XML no servidor", validators=[Optional(), Length(max=300)]
    )
    submit = SubmitField("Importar acervo")
