"""Cadastro fiscal: estabelecimento, credenciais, classificação de produto.

Também concentra a checagem de prontidão — a lista do que ainda falta para a
empresa poder emitir com valor fiscal. Essa lista é o que aparece no painel
fiscal e o que impede a virada para produção.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy.exc import IntegrityError

from ...extensions import db
from ...models import (
    ConfiguracaoDocumentoFiscal,
    CredencialFiscal,
    Empresa,
    EstabelecimentoFiscal,
    Produto,
    ProdutoFiscal,
)
from ..auditoria import registrar
from . import segredos
from .chave import ChaveInvalida, somente_digitos, validar_cnpj
from .provedores import PROVEDORES, criar_provedor
from .uf import (
    AMBIENTE_HOMOLOGACAO,
    AMBIENTE_PRODUCAO,
    CODIGO_UF,
    CRT_SIMPLES_NACIONAL,
    MODELO_CFE_SAT,
    MODELO_NFCE,
    MODELO_NFE,
    codigo_da_uf,
    orientacao_autorizador,
    sigla_valida,
)


NOMES_CREDENCIAIS = ("csc_token", "certificado_senha", "gateway_token")

_CST_ICMS = {"00", "20", "40", "41", "50", "51", "60", "90"}
_CSOSN = {"101", "102", "103", "300", "400", "500", "900"}
_CST_CONTRIBUICAO = {
    "01", "02", "03", "04", "05", "06", "07", "08", "09",
    "49", "50", "51", "52", "53", "54", "55", "56",
    "60", "61", "62", "63", "64", "65", "66", "67",
    "70", "71", "72", "73", "74", "75", "98", "99",
}


class CadastroFiscalErro(ValueError):
    pass


def _texto(valor: str | None, *, minimo: int, maximo: int, rotulo: str) -> str:
    normalizado = " ".join((valor or "").split())
    if not minimo <= len(normalizado) <= maximo:
        raise CadastroFiscalErro(
            f"{rotulo} precisa ter entre {minimo} e {maximo} caracteres."
        )
    return normalizado


def _inteiro(valor, *, minimo: int, maximo: int, rotulo: str) -> int:
    try:
        numero = int(str(valor).strip())
    except (TypeError, ValueError) as exc:
        raise CadastroFiscalErro(f"{rotulo} precisa ser um número inteiro.") from exc
    if not minimo <= numero <= maximo:
        raise CadastroFiscalErro(f"{rotulo} precisa estar entre {minimo} e {maximo}.")
    return numero


def _decimal(valor, *, rotulo: str, maximo: Decimal = Decimal("100")) -> Decimal:
    texto = str(valor or "0").strip().replace(",", ".")
    try:
        numero = Decimal(texto or "0")
    except InvalidOperation as exc:
        raise CadastroFiscalErro(f"{rotulo} precisa ser um número.") from exc
    if numero < 0 or numero > maximo:
        raise CadastroFiscalErro(f"{rotulo} precisa estar entre 0 e {maximo}.")
    return numero.quantize(Decimal("0.0001"))


# --------------------------------------------------------------------------
# Estabelecimento
# --------------------------------------------------------------------------


def obter_estabelecimento(empresa_id: int) -> EstabelecimentoFiscal | None:
    return db.session.get(EstabelecimentoFiscal, empresa_id)


def obter_configuracao_documento(
    empresa_id: int, *, modelo: int, ambiente: int, serie: int
) -> ConfiguracaoDocumentoFiscal | None:
    return db.session.scalar(
        db.select(ConfiguracaoDocumentoFiscal).where(
            ConfiguracaoDocumentoFiscal.empresa_id == empresa_id,
            ConfiguracaoDocumentoFiscal.modelo == int(modelo),
            ConfiguracaoDocumentoFiscal.ambiente == int(ambiente),
            ConfiguracaoDocumentoFiscal.serie == int(serie),
        )
    )


def listar_configuracoes_documento(empresa_id: int) -> list[ConfiguracaoDocumentoFiscal]:
    return list(
        db.session.scalars(
            db.select(ConfiguracaoDocumentoFiscal)
            .where(ConfiguracaoDocumentoFiscal.empresa_id == empresa_id)
            .order_by(
                ConfiguracaoDocumentoFiscal.modelo,
                ConfiguracaoDocumentoFiscal.ambiente,
                ConfiguracaoDocumentoFiscal.serie,
            )
        ).all()
    )


def _sincronizar_configuracao_documento(
    estabelecimento: EstabelecimentoFiscal, *, usuario_id: uuid.UUID
) -> ConfiguracaoDocumentoFiscal:
    """Espelha a seleção atual na tabela multi-configuração.

    ``estabelecimentos_fiscais`` permanece como compatibilidade nesta fase. A
    nova tabela passa a preservar cada combinação separadamente, inclusive a
    sequência.
    """
    config = obter_configuracao_documento(
        estabelecimento.empresa_id,
        modelo=int(estabelecimento.modelo),
        ambiente=int(estabelecimento.ambiente),
        serie=int(estabelecimento.serie),
    )
    if config is None:
        config = ConfiguracaoDocumentoFiscal(
            empresa_id=estabelecimento.empresa_id,
            modelo=int(estabelecimento.modelo),
            ambiente=int(estabelecimento.ambiente),
            serie=int(estabelecimento.serie),
        )
        db.session.add(config)

    config.proximo_numero = int(estabelecimento.proximo_numero)
    config.natureza_operacao = estabelecimento.natureza_operacao
    config.informacoes_complementares = estabelecimento.informacoes_complementares
    config.provedor = estabelecimento.provedor
    config.parametros_provedor = dict(estabelecimento.parametros_provedor or {})
    config.csc_id = estabelecimento.csc_id
    config.url_consulta_qrcode = estabelecimento.url_consulta_qrcode
    config.url_consulta_chave = estabelecimento.url_consulta_chave
    config.enderecos_conferidos = bool(estabelecimento.enderecos_conferidos)
    config.emissao_ativa = bool(estabelecimento.emissao_ativa)
    config.permitir_contingencia = bool(estabelecimento.permitir_contingencia)
    config.versao = (config.versao or 0) + 1
    config.atualizado_por_id = usuario_id
    db.session.flush()
    return config


def salvar_estabelecimento(
    *, empresa_id: int, dados: dict, usuario_id: uuid.UUID
) -> EstabelecimentoFiscal:
    empresa = db.session.get(Empresa, empresa_id)
    if empresa is None or not empresa.ativa:
        raise CadastroFiscalErro("Empresa não encontrada ou inativa.")

    try:
        cnpj = validar_cnpj(dados.get("cnpj"))
    except ChaveInvalida as exc:
        raise CadastroFiscalErro(str(exc)) from exc

    uf = sigla_valida(dados.get("uf"))
    modelo = _inteiro(dados.get("modelo", MODELO_NFCE), minimo=55, maximo=65, rotulo="O modelo")
    if modelo not in (MODELO_NFE, MODELO_NFCE, MODELO_CFE_SAT):
        raise CadastroFiscalErro("Modelo de documento inválido (use 55, 65 ou 59).")
    ambiente = _inteiro(dados.get("ambiente", AMBIENTE_HOMOLOGACAO), minimo=1, maximo=2, rotulo="O ambiente")
    serie = _inteiro(dados.get("serie", 1), minimo=0, maximo=999, rotulo="A série")
    provedor = (dados.get("provedor") or "homologacao_interna").strip()
    if provedor not in PROVEDORES:
        raise CadastroFiscalErro("Provedor de emissão desconhecido.")

    registro = db.session.get(EstabelecimentoFiscal, empresa_id)
    novo = registro is None
    configuracao_existente = obter_configuracao_documento(
        empresa_id, modelo=modelo, ambiente=ambiente, serie=serie
    )
    if novo:
        registro = EstabelecimentoFiscal(empresa_id=empresa_id)
        db.session.add(registro)

    mesma_configuracao_legada = bool(
        not novo
        and int(registro.modelo) == modelo
        and int(registro.ambiente) == ambiente
        and int(registro.serie) == serie
    )
    proximo_padrao = (
        int(configuracao_existente.proximo_numero)
        if configuracao_existente is not None
        else int(registro.proximo_numero) if mesma_configuracao_legada else 1
    )
    proximo = _inteiro(
        dados.get("proximo_numero", proximo_padrao),
        minimo=1,
        maximo=999_999_999,
        rotulo="O próximo número",
    )
    minimo_permitido = (
        int(configuracao_existente.proximo_numero)
        if configuracao_existente is not None
        else int(registro.proximo_numero) if mesma_configuracao_legada else 1
    )
    if proximo < minimo_permitido:
        raise CadastroFiscalErro(
            "A numeração desta combinação de modelo, ambiente e série não pode retroceder."
        )

    registro.cnpj = cnpj
    registro.razao_social = _texto(dados.get("razao_social"), minimo=3, maximo=60, rotulo="A razão social")
    registro.nome_fantasia = " ".join((dados.get("nome_fantasia") or "").split())[:60] or None
    registro.inscricao_estadual = (
        somente_digitos(dados.get("inscricao_estadual")) or "ISENTO"
    )[:20]
    registro.inscricao_municipal = somente_digitos(dados.get("inscricao_municipal")) or None
    registro.crt = _inteiro(dados.get("crt", CRT_SIMPLES_NACIONAL), minimo=1, maximo=4, rotulo="O regime tributário")

    registro.logradouro = _texto(dados.get("logradouro"), minimo=2, maximo=60, rotulo="O logradouro")
    registro.numero = (" ".join((dados.get("numero") or "").split()) or "S/N")[:20]
    registro.complemento = " ".join((dados.get("complemento") or "").split())[:60] or None
    registro.bairro = _texto(dados.get("bairro"), minimo=2, maximo=60, rotulo="O bairro")
    registro.codigo_municipio = _inteiro(
        dados.get("codigo_municipio"), minimo=1_000_000, maximo=9_999_999, rotulo="O código IBGE do município"
    )
    if str(registro.codigo_municipio)[:2] != f"{CODIGO_UF[uf]:02d}":
        raise CadastroFiscalErro(
            f"O código IBGE do município não pertence à UF {uf}. "
            f"Ele precisa começar com {CODIGO_UF[uf]:02d}."
        )
    registro.municipio = _texto(dados.get("municipio"), minimo=2, maximo=60, rotulo="O município")
    registro.uf = uf
    cep = somente_digitos(dados.get("cep"))
    if len(cep) != 8:
        raise CadastroFiscalErro("O CEP precisa ter 8 dígitos.")
    registro.cep = cep
    telefone = somente_digitos(dados.get("telefone"))
    registro.telefone = telefone[:14] or None

    registro.modelo = modelo
    registro.serie = serie
    registro.proximo_numero = proximo
    registro.ambiente = ambiente
    registro.natureza_operacao = _texto(
        dados.get("natureza_operacao") or "VENDA AO CONSUMIDOR",
        minimo=3,
        maximo=60,
        rotulo="A natureza da operação",
    )
    registro.informacoes_complementares = (
        " ".join((dados.get("informacoes_complementares") or "").split())[:500] or None
    )

    registro.provedor = provedor
    parametros = dict(registro.parametros_provedor or {})
    parametros.update(dados.get("parametros_provedor") or {})
    parametros = {
        chave: valor for chave, valor in parametros.items() if str(valor).strip() != ""
    }
    registro.parametros_provedor = parametros

    csc_id = somente_digitos(dados.get("csc_id"))
    registro.csc_id = csc_id[:6] or None
    registro.url_consulta_qrcode = (dados.get("url_consulta_qrcode") or "").strip()[:300] or None
    registro.url_consulta_chave = (dados.get("url_consulta_chave") or "").strip()[:300] or None

    registro.responsavel_cnpj = somente_digitos(dados.get("responsavel_cnpj")) or None
    registro.responsavel_contato = " ".join((dados.get("responsavel_contato") or "").split())[:60] or None
    registro.responsavel_email = (dados.get("responsavel_email") or "").strip()[:60] or None
    registro.responsavel_telefone = somente_digitos(dados.get("responsavel_telefone")) or None

    registro.enderecos_conferidos = bool(dados.get("enderecos_conferidos"))
    registro.permitir_contingencia = bool(dados.get("permitir_contingencia"))

    quer_emitir = bool(dados.get("emissao_ativa"))
    if quer_emitir:
        pendencias = _pendencias(registro)
        if pendencias:
            raise CadastroFiscalErro(
                "A emissão não pode ser ligada enquanto houver pendências: "
                + " ".join(pendencias[:3])
            )
    registro.emissao_ativa = quer_emitir
    registro.versao = (registro.versao or 0) + 1
    registro.atualizado_por_id = usuario_id
    _sincronizar_configuracao_documento(registro, usuario_id=usuario_id)

    registrar(
        "estabelecimento_fiscal_salvo",
        "estabelecimento_fiscal",
        empresa_id,
        empresa_id,
        {
            "cnpj": registro.cnpj,
            "modelo": registro.modelo,
            "serie": registro.serie,
            "ambiente": registro.ambiente,
            "provedor": registro.provedor,
            "emissao_ativa": registro.emissao_ativa,
        },
        usuario_id=usuario_id,
    )
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise CadastroFiscalErro(
            "Já existe outro estabelecimento com este CNPJ, modelo e série."
        ) from exc
    return registro


# --------------------------------------------------------------------------
# Credenciais
# --------------------------------------------------------------------------


def guardar_credencial(
    *, empresa_id: int, nome: str, valor: str, usuario_id: uuid.UUID
) -> CredencialFiscal:
    if nome not in NOMES_CREDENCIAIS:
        raise CadastroFiscalErro("Credencial fiscal desconhecida.")
    limpo = (valor or "").strip()
    if not 4 <= len(limpo) <= 400:
        raise CadastroFiscalErro("A credencial precisa ter entre 4 e 400 caracteres.")
    if not segredos.chave_configurada():
        raise CadastroFiscalErro(
            "Defina FISCAL_SECRET_KEY no servidor antes de guardar credenciais fiscais."
        )
    registro = db.session.scalar(
        db.select(CredencialFiscal).where(
            CredencialFiscal.empresa_id == empresa_id, CredencialFiscal.nome == nome
        )
    )
    if registro is None:
        registro = CredencialFiscal(empresa_id=empresa_id, nome=nome)
        db.session.add(registro)
    registro.valor_cifrado = segredos.cifrar(limpo)
    registro.dica = segredos.mascarar(limpo)
    registro.atualizado_por_id = usuario_id
    registrar(
        "credencial_fiscal_atualizada",
        "credencial_fiscal",
        empresa_id,
        empresa_id,
        {"nome": nome, "dica": registro.dica},
        usuario_id=usuario_id,
    )
    db.session.commit()
    return registro


def remover_credencial(*, empresa_id: int, nome: str, usuario_id: uuid.UUID) -> bool:
    registro = db.session.scalar(
        db.select(CredencialFiscal).where(
            CredencialFiscal.empresa_id == empresa_id, CredencialFiscal.nome == nome
        )
    )
    if registro is None:
        return False
    db.session.delete(registro)
    registrar(
        "credencial_fiscal_removida",
        "credencial_fiscal",
        empresa_id,
        empresa_id,
        {"nome": nome},
        usuario_id=usuario_id,
    )
    db.session.commit()
    return True


def ler_credencial(empresa_id: int, nome: str) -> str | None:
    registro = db.session.scalar(
        db.select(CredencialFiscal).where(
            CredencialFiscal.empresa_id == empresa_id, CredencialFiscal.nome == nome
        )
    )
    if registro is None:
        return None
    return segredos.decifrar(registro.valor_cifrado)


def credenciais_cadastradas(empresa_id: int) -> dict[str, str]:
    registros = db.session.scalars(
        db.select(CredencialFiscal).where(CredencialFiscal.empresa_id == empresa_id)
    ).all()
    return {registro.nome: (registro.dica or "•••") for registro in registros}


# --------------------------------------------------------------------------
# Configuração do provedor e prontidão
# --------------------------------------------------------------------------


def configuracao_provedor(
    estabelecimento: EstabelecimentoFiscal,
    configuracao_documento: ConfiguracaoDocumentoFiscal | None = None,
) -> dict:
    """Junta parâmetros do banco com os segredos decifrados, só em memória.

    Quando uma configuração histórica/específica é informada, os parâmetros
    por modelo/ambiente/série vêm dela; identidade do emitente e credenciais
    continuam pertencendo à empresa.
    """
    origem = configuracao_documento or estabelecimento
    configuracao = dict(origem.parametros_provedor or {})
    configuracao.update(
        {
            "ambiente": int(origem.ambiente),
            "codigo_uf": codigo_da_uf(estabelecimento.uf),
            "modelo": int(origem.modelo),
            "serie": int(origem.serie),
            "cnpj": estabelecimento.cnpj,
        }
    )
    for nome, destino in (
        ("certificado_senha", "certificado_senha"),
        ("gateway_token", "token"),
        ("csc_token", "csc"),
    ):
        try:
            valor = ler_credencial(estabelecimento.empresa_id, nome)
        except segredos.SegredoErro:
            valor = None
        if valor:
            configuracao[destino] = valor
    return configuracao


def _provedor_do_estabelecimento(estabelecimento: EstabelecimentoFiscal):
    return criar_provedor(
        estabelecimento.provedor, configuracao_provedor(estabelecimento)
    )


def _pendencias(estabelecimento: EstabelecimentoFiscal) -> list[str]:
    """Impedimentos reais para ligar a emissão.

    A régua depende da rota escolhida: uma rota que produz documento fiscal de
    verdade precisa de CSC, endereços conferidos e responsável técnico. A rota
    de ensaio não fala com a SEFAZ e não usa nada disso — exigir esses itens
    dela tornaria impossível testar o fluxo antes de as credenciais chegarem,
    que é justamente para o que o ensaio existe.
    """
    pendencias: list[str] = []
    try:
        provedor = _provedor_do_estabelecimento(estabelecimento)
    except Exception as exc:  # configuração incompleta é pendência, não falha
        return [str(exc)]

    pendencias.extend(provedor.conferir_configuracao())
    if not provedor.produz_documento_fiscal:
        return pendencias

    if not segredos.chave_configurada():
        pendencias.append("Defina FISCAL_SECRET_KEY no servidor.")
    if int(estabelecimento.modelo) == MODELO_NFCE:
        if not (estabelecimento.csc_id or "").strip():
            pendencias.append("Informe o idCSC fornecido pela SEFAZ da UF.")
        if "csc_token" not in credenciais_cadastradas(estabelecimento.empresa_id):
            pendencias.append("Cadastre o CSC (token) fornecido pela SEFAZ da UF.")
        if not (estabelecimento.url_consulta_qrcode or "").startswith("https://"):
            pendencias.append(
                "Informe a URL de consulta do QR Code publicada pela SEFAZ da UF."
            )
    if int(estabelecimento.modelo) == MODELO_NFCE and not (estabelecimento.url_consulta_chave or "").strip():
        pendencias.append("Informe a URL de consulta por chave da NFC-e.")
    if not estabelecimento.enderecos_conferidos:
        pendencias.append(
            "Marque que os endereços de serviço foram conferidos com a SEFAZ da UF."
        )
    if not (estabelecimento.responsavel_cnpj or "").strip():
        pendencias.append(
            "Preencha o responsável técnico (obrigatório desde a NT 2018.005)."
        )
    return pendencias


def _avisos(estabelecimento: EstabelecimentoFiscal) -> list[str]:
    """Coisas que a pessoa precisa saber, mas que não impedem a emissão."""
    avisos: list[str] = []
    try:
        provedor = _provedor_do_estabelecimento(estabelecimento)
    except Exception:
        return avisos

    if not provedor.produz_documento_fiscal:
        avisos.append(
            "Rota de ensaio ativa: as vendas geram documento SEM VALOR FISCAL, "
            "sem passar pela SEFAZ. Serve para conferir o fluxo e treinar a equipe."
        )
    elif int(estabelecimento.ambiente) != AMBIENTE_PRODUCAO:
        avisos.append(
            "Ambiente de homologação: os documentos servem para teste e não têm "
            "valor fiscal. Vire para produção só depois de homologar."
        )

    sem_classificacao = produtos_sem_classificacao(estabelecimento.empresa_id)
    if sem_classificacao:
        avisos.append(
            f"{sem_classificacao} produto(s) ativo(s) ainda sem NCM, CFOP e situação "
            "tributária. A venda que incluir um deles fica sem documento fiscal — "
            "a venda em si continua válida."
        )
    aguardando = produtos_aguardando_revisao(estabelecimento.empresa_id)
    if aguardando:
        avisos.append(
            f"{aguardando} produto(s) com classificação aplicada em lote, ainda não "
            "conferida item a item. Eles emitem normalmente, mas o NCM pode estar "
            "errado — feche a lista com o contador antes de virar para produção."
        )
    return avisos


def conferir_prontidao(empresa_id: int) -> dict:
    """Diz se a empresa pode emitir e o que falta caso não possa."""
    estabelecimento = db.session.get(EstabelecimentoFiscal, empresa_id)
    if estabelecimento is None:
        return {
            "configurado": False,
            "pode_emitir": False,
            "com_valor_fiscal": False,
            "pendencias": ["Cadastre os dados fiscais da empresa."],
            "avisos": [],
            "produtos_sem_classificacao": produtos_sem_classificacao(empresa_id),
            "produtos_aguardando_revisao": 0,
            "orientacao": "",
        }

    pendencias = _pendencias(estabelecimento)
    try:
        produz_fiscal = _provedor_do_estabelecimento(estabelecimento).produz_documento_fiscal
    except Exception:
        produz_fiscal = False

    return {
        "configurado": True,
        "pode_emitir": estabelecimento.emissao_ativa and not pendencias,
        "com_valor_fiscal": (
            produz_fiscal and int(estabelecimento.ambiente) == AMBIENTE_PRODUCAO
        ),
        "pendencias": pendencias,
        "avisos": _avisos(estabelecimento),
        "produtos_sem_classificacao": produtos_sem_classificacao(empresa_id),
        "produtos_aguardando_revisao": produtos_aguardando_revisao(empresa_id),
        "orientacao": orientacao_autorizador(estabelecimento.uf),
        "estabelecimento": estabelecimento,
    }


# --------------------------------------------------------------------------
# Classificação fiscal do produto
# --------------------------------------------------------------------------


def produtos_sem_classificacao(empresa_id: int) -> int:
    """Produtos ativos sem nenhuma classificação fiscal — a venda com eles não emite."""
    total = db.session.scalar(
        db.select(db.func.count(Produto.id))
        .outerjoin(ProdutoFiscal, ProdutoFiscal.produto_id == Produto.id)
        .where(
            Produto.empresa_id == empresa_id,
            Produto.ativo.is_(True),
            ProdutoFiscal.produto_id.is_(None),
        )
    )
    return int(total or 0)


def produtos_aguardando_revisao(empresa_id: int) -> int:
    """Produtos classificados em lote que ainda não foram conferidos um a um.

    Eles emitem normalmente — o que falta é alguém responder pelo NCM. Contar
    isto separado evita a armadilha de aplicar uma regra geral em milhares de
    itens e o sistema passar a dizer que está tudo certo.
    """
    total = db.session.scalar(
        db.select(db.func.count(Produto.id))
        .join(ProdutoFiscal, ProdutoFiscal.produto_id == Produto.id)
        .where(
            Produto.empresa_id == empresa_id,
            Produto.ativo.is_(True),
            ProdutoFiscal.revisao_pendente.is_(True),
        )
    )
    return int(total or 0)


def _normalizar_classificacao(dados: dict, *, simples: bool) -> dict:
    """Valida e normaliza os campos da classificação, sem tocar no banco.

    Fica separada da gravação porque a aplicação em lote precisa validar uma
    única vez e gravar milhares de vezes.
    """
    ncm = somente_digitos(dados.get("ncm"))
    if len(ncm) != 8:
        raise CadastroFiscalErro("O NCM precisa ter 8 dígitos.")
    cfop = somente_digitos(dados.get("cfop")) or "5102"
    if len(cfop) != 4:
        raise CadastroFiscalErro("O CFOP precisa ter 4 dígitos.")
    cest = somente_digitos(dados.get("cest"))
    if cest and len(cest) != 7:
        raise CadastroFiscalErro("O CEST, quando informado, precisa ter 7 dígitos.")

    cst = (dados.get("icms_cst") or "").strip()
    csosn = (dados.get("icms_csosn") or "").strip()
    if simples:
        if csosn not in _CSOSN:
            raise CadastroFiscalErro(
                "Empresa no Simples Nacional: informe um CSOSN válido "
                "(101, 102, 103, 300, 400, 500 ou 900)."
            )
        cst = ""
    else:
        if cst not in _CST_ICMS:
            raise CadastroFiscalErro(
                "Empresa no regime normal: informe um CST de ICMS válido "
                "(00, 20, 40, 41, 50, 51, 60 ou 90)."
            )
        csosn = ""

    pis_cst = (dados.get("pis_cst") or "07").strip()
    cofins_cst = (dados.get("cofins_cst") or "07").strip()
    for valor, rotulo in ((pis_cst, "PIS"), (cofins_cst, "COFINS")):
        if valor not in _CST_CONTRIBUICAO:
            raise CadastroFiscalErro(f"CST de {rotulo} inválido.")

    gtin = somente_digitos(dados.get("gtin"))
    if gtin and len(gtin) not in (8, 12, 13, 14):
        raise CadastroFiscalErro("O GTIN precisa ter 8, 12, 13 ou 14 dígitos.")

    return {
        "ncm": ncm,
        "cfop": cfop,
        "cest": cest or None,
        "origem": _inteiro(dados.get("origem", 0), minimo=0, maximo=8, rotulo="A origem"),
        "gtin": gtin or None,
        "icms_cst": cst or None,
        "icms_csosn": csosn or None,
        "icms_aliquota": _decimal(dados.get("icms_aliquota"), rotulo="A alíquota de ICMS"),
        "icms_modalidade_bc": _inteiro(
            dados.get("icms_modalidade_bc", 3), minimo=0, maximo=3, rotulo="A modalidade da base"
        ),
        "icms_reducao_bc": _decimal(dados.get("icms_reducao_bc"), rotulo="A redução da base"),
        "pis_cst": pis_cst,
        "pis_aliquota": _decimal(dados.get("pis_aliquota"), rotulo="A alíquota de PIS"),
        "cofins_cst": cofins_cst,
        "cofins_aliquota": _decimal(dados.get("cofins_aliquota"), rotulo="A alíquota de COFINS"),
        "observacao": " ".join((dados.get("observacao") or "").split())[:500] or None,
        "_unidade_tributavel": (dados.get("unidade_tributavel") or "").strip(),
    }


def _gravar_classificacao(
    registro: ProdutoFiscal,
    produto: Produto,
    valores: dict,
    *,
    usuario_id: uuid.UUID,
    marcar_revisado: bool,
) -> None:
    """Aplica os valores no registro. Não faz commit — quem chama decide."""
    unidade = valores["_unidade_tributavel"] or produto.unidade
    for campo, valor in valores.items():
        if campo.startswith("_"):
            continue
        setattr(registro, campo, valor)
    registro.unidade_tributavel = unidade[:6]
    # Só quem olhou o item confirma a revisão. A aplicação em lote deixa a
    # pendência de pé de propósito.
    registro.revisao_pendente = not marcar_revisado
    registro.versao = (registro.versao or 0) + 1
    registro.atualizado_por_id = usuario_id


def salvar_classificacao(
    *,
    empresa_id: int,
    produto_id: uuid.UUID,
    dados: dict,
    usuario_id: uuid.UUID,
    marcar_revisado: bool = True,
) -> ProdutoFiscal:
    produto = db.session.scalar(
        db.select(Produto).where(Produto.id == produto_id, Produto.empresa_id == empresa_id)
    )
    if produto is None:
        raise CadastroFiscalErro("Produto não encontrado nesta empresa.")

    estabelecimento = db.session.get(EstabelecimentoFiscal, empresa_id)
    simples = estabelecimento is None or int(estabelecimento.crt) in (1, 4)
    valores = _normalizar_classificacao(dados, simples=simples)

    registro = db.session.get(ProdutoFiscal, produto_id)
    if registro is None:
        registro = ProdutoFiscal(produto_id=produto_id, empresa_id=empresa_id)
        db.session.add(registro)
    _gravar_classificacao(
        registro, produto, valores, usuario_id=usuario_id, marcar_revisado=marcar_revisado
    )

    registrar(
        "produto_fiscal_salvo",
        "produto_fiscal",
        produto_id,
        empresa_id,
        {
            "produto": produto.nome,
            "ncm": registro.ncm,
            "cfop": registro.cfop,
            "icms_cst": registro.icms_cst,
            "icms_csosn": registro.icms_csosn,
        },
        usuario_id=usuario_id,
    )
    db.session.commit()
    return registro


def aplicar_classificacao_em_lote(
    *, empresa_id: int, dados: dict, usuario_id: uuid.UUID
) -> int:
    """Classifica de uma vez os produtos ainda sem cadastro fiscal conferido.

    Existe para a virada: um açougue tem centenas de itens e quase todos
    compartilham a mesma classificação. Depois cada exceção é ajustada.

    Tudo acontece em UMA transação, com UM registro de auditoria. A primeira
    versão reaproveitava a função de item único, que faz commit a cada produto
    — com alguns milhares de itens isso estourava o tempo do servidor e
    derrubava a requisição com erro 500.
    """
    from datetime import date

    estabelecimento = db.session.get(EstabelecimentoFiscal, empresa_id)
    simples = estabelecimento is None or int(estabelecimento.crt) in (1, 4)
    valores = _normalizar_classificacao(dados, simples=simples)
    if not valores["observacao"]:
        valores["observacao"] = (
            f"Classificação aplicada em lote em {date.today():%d/%m/%Y}. "
            "Conferir item a item com o contador."
        )

    produtos = db.session.scalars(
        db.select(Produto)
        .outerjoin(ProdutoFiscal, ProdutoFiscal.produto_id == Produto.id)
        .where(
            Produto.empresa_id == empresa_id,
            Produto.ativo.is_(True),
            db.or_(
                ProdutoFiscal.produto_id.is_(None),
                ProdutoFiscal.revisao_pendente.is_(True),
            ),
        )
        .order_by(Produto.nome)
    ).all()
    if not produtos:
        return 0

    # Uma consulta só para os cadastros já existentes, em vez de uma por produto.
    existentes = {
        registro.produto_id: registro
        for registro in db.session.scalars(
            db.select(ProdutoFiscal).where(ProdutoFiscal.empresa_id == empresa_id)
        ).all()
    }

    total = 0
    for produto in produtos:
        registro = existentes.get(produto.id)
        if registro is None:
            registro = ProdutoFiscal(produto_id=produto.id, empresa_id=empresa_id)
            db.session.add(registro)
        _gravar_classificacao(
            registro, produto, valores, usuario_id=usuario_id, marcar_revisado=False
        )
        total += 1

    registrar(
        "produto_fiscal_lote",
        "produto_fiscal",
        f"empresa:{empresa_id}",
        empresa_id,
        {
            "produtos": total,
            "ncm": valores["ncm"],
            "cfop": valores["cfop"],
            "icms_cst": valores["icms_cst"],
            "icms_csosn": valores["icms_csosn"],
            "revisao_pendente": True,
        },
        usuario_id=usuario_id,
    )
    db.session.commit()
    return total
