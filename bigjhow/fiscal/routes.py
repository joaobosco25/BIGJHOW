from __future__ import annotations

import uuid

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import (
    DocumentoFiscal,
    ConfiguracaoDocumentoFiscal,
    DocumentoFiscalXml,
    Empresa,
    EstabelecimentoFiscal,
    InutilizacaoFiscal,
    Produto,
    ProdutoFiscal,
)
from ..security import acesso_empresa_obrigatorio, papel_obrigatorio
from ..services.fiscal import segredos
from ..services.fiscal.cadastro import (
    CadastroFiscalErro,
    aplicar_classificacao_em_lote,
    configuracao_provedor,
    conferir_prontidao,
    credenciais_cadastradas,
    guardar_credencial,
    listar_configuracoes_documento,
    obter_configuracao_documento,
    remover_credencial,
    salvar_classificacao,
    salvar_estabelecimento,
)
from ..services.fiscal.danfe import montar_danfe, qrcode_do_documento
from ..services.fiscal.emissao import (
    EmissaoErro,
    cancelar_documento,
    consultar_e_reconciliar,
    inutilizar_faixa,
    reenviar,
)
from ..services.fiscal.importador import ImportacaoErro, importar_conteudo, importar_pasta
from ..services.fiscal.importador import ResultadoImportacao
from ..services.fiscal.provedores import ProvedorFiscalErro, criar_provedor
from ..services.fiscal.sequencia import (
    agrupar_faixas,
    detectar_lacunas,
    registrar_lacunas,
    resumo_numeracao,
)
from ..services.fiscal.uf import orientacao_autorizador
from .forms import (
    AcaoForm,
    CancelarDocumentoForm,
    ClassificacaoForm,
    ClassificacaoLoteForm,
    CredencialForm,
    EstabelecimentoForm,
    ImportacaoForm,
    InutilizarForm,
)


bp = Blueprint("fiscal", __name__, url_prefix="/empresa/<int:empresa_id>/fiscal")

PARAMETROS_DO_PROVEDOR = (
    "url_autorizacao",
    "url_evento",
    "url_inutilizacao",
    "url_consulta",
    "url_status",
    "certificado_caminho",
    "emissor_host",
    "emissor_porta",
    "pasta_envio",
    "pasta_retorno",
    "padrao_sucesso",
    "url_base",
    "estilo_autenticacao",
)

CAMPOS_DO_ESTABELECIMENTO = (
    "cnpj",
    "razao_social",
    "nome_fantasia",
    "inscricao_estadual",
    "inscricao_municipal",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "municipio",
    "uf",
    "cep",
    "telefone",
    "serie",
    "proximo_numero",
    "natureza_operacao",
    "informacoes_complementares",
    "provedor",
    "csc_id",
    "url_consulta_qrcode",
    "url_consulta_chave",
    "responsavel_cnpj",
    "responsavel_contato",
    "responsavel_email",
    "responsavel_telefone",
    "enderecos_conferidos",
    "permitir_contingencia",
    "emissao_ativa",
)

# Campos numéricos no banco que são listas de seleção no formulário. O WTForms
# compara a opção marcada por igualdade de string, então um inteiro vindo do
# banco deixaria a lista sem nada selecionado.
CAMPOS_DE_SELECAO = ("crt", "modelo", "ambiente")

CAMPOS_CONFIGURACAO_DOCUMENTO = (
    "modelo",
    "ambiente",
    "serie",
    "proximo_numero",
    "natureza_operacao",
    "informacoes_complementares",
    "provedor",
    "csc_id",
    "url_consulta_qrcode",
    "url_consulta_chave",
    "enderecos_conferidos",
    "permitir_contingencia",
    "emissao_ativa",
)


def _dados_do_estabelecimento(
    estabelecimento: EstabelecimentoFiscal,
    configuracao_documento: ConfiguracaoDocumentoFiscal | None = None,
) -> dict:
    """Monta o dicionário que preenche o formulário.

    Identidade do emitente vem do estabelecimento. Os campos que variam por
    modelo/ambiente/série podem vir de uma configuração salva específica.
    """
    dados = {
        campo: getattr(estabelecimento, campo) for campo in CAMPOS_DO_ESTABELECIMENTO
    }
    origem = configuracao_documento or estabelecimento
    for campo in CAMPOS_CONFIGURACAO_DOCUMENTO:
        dados[campo] = getattr(origem, campo)
    dados["crt"] = str(estabelecimento.crt)
    dados["modelo"] = str(origem.modelo)
    dados["ambiente"] = str(origem.ambiente)
    dados["codigo_municipio"] = str(estabelecimento.codigo_municipio)
    parametros = origem.parametros_provedor or {}
    dados.update(
        {campo: parametros.get(campo, "") for campo in PARAMETROS_DO_PROVEDOR}
    )
    return dados


def _empresa(empresa_id: int) -> Empresa:
    empresa = db.session.get(Empresa, empresa_id)
    if empresa is None or not empresa.ativa:
        abort(404)
    return empresa


def _voltar(empresa_id: int, destino: str = "fiscal.painel"):
    return redirect(url_for(destino, empresa_id=empresa_id))


# --------------------------------------------------------------------------
# Painel
# --------------------------------------------------------------------------


@bp.get("")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def painel(empresa_id: int):
    empresa = _empresa(empresa_id)
    prontidao = conferir_prontidao(empresa_id)
    resumo = resumo_numeracao(empresa_id)
    ultimos = db.session.scalars(
        db.select(DocumentoFiscal)
        .where(DocumentoFiscal.empresa_id == empresa_id)
        .order_by(DocumentoFiscal.criado_em.desc())
        .limit(10)
    ).all()
    return render_template(
        "fiscal/painel.html",
        empresa=empresa,
        prontidao=prontidao,
        resumo=resumo,
        ultimos=ultimos,
        chave_configurada=segredos.chave_configurada(),
        acao_form=AcaoForm(),
    )


@bp.post("/status")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def consultar_status(empresa_id: int):
    if not AcaoForm().validate_on_submit():
        abort(400)
    estabelecimento = db.session.get(EstabelecimentoFiscal, empresa_id)
    if estabelecimento is None:
        flash("Configure os dados fiscais antes de consultar o status.", "erro")
        return _voltar(empresa_id)
    try:
        provedor = criar_provedor(
            estabelecimento.provedor, configuracao_provedor(estabelecimento)
        )
        resposta = provedor.status_servico()
    except (ProvedorFiscalErro, segredos.SegredoErro) as exc:
        flash(str(exc), "erro")
        return _voltar(empresa_id)
    flash(
        f"{resposta.codigo_status} — {resposta.mensagem}",
        "sucesso" if resposta.sucesso else "aviso",
    )
    return _voltar(empresa_id)


# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------


@bp.route("/configuracao", methods=["GET", "POST"])
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def configuracao(empresa_id: int):
    empresa = _empresa(empresa_id)
    estabelecimento = db.session.get(EstabelecimentoFiscal, empresa_id)
    configuracoes = listar_configuracoes_documento(empresa_id)
    selecionada = None
    form = EstabelecimentoForm()

    if request.method == "GET" and estabelecimento is not None:
        try:
            modelo_qs = int(request.args.get("modelo", estabelecimento.modelo))
            ambiente_qs = int(request.args.get("ambiente", estabelecimento.ambiente))
            serie_qs = int(request.args.get("serie", estabelecimento.serie))
        except (TypeError, ValueError):
            abort(400)
        selecionada = obter_configuracao_documento(
            empresa_id, modelo=modelo_qs, ambiente=ambiente_qs, serie=serie_qs
        )
        form = EstabelecimentoForm(
            data=_dados_do_estabelecimento(estabelecimento, selecionada)
        )

    if form.validate_on_submit():
        dados = {
            campo: getattr(form, campo).data
            for campo in (
                "cnpj", "razao_social", "nome_fantasia", "inscricao_estadual",
                "inscricao_municipal", "crt", "logradouro", "numero", "complemento",
                "bairro", "codigo_municipio", "municipio", "uf", "cep", "telefone",
                "modelo", "serie", "proximo_numero", "ambiente", "natureza_operacao",
                "informacoes_complementares", "provedor", "csc_id",
                "url_consulta_qrcode", "url_consulta_chave", "responsavel_cnpj",
                "responsavel_contato", "responsavel_email", "responsavel_telefone",
                "enderecos_conferidos", "permitir_contingencia", "emissao_ativa",
            )
        }
        dados["parametros_provedor"] = {
            campo: (getattr(form, campo).data or "").strip()
            for campo in PARAMETROS_DO_PROVEDOR
        }
        try:
            salvar_estabelecimento(
                empresa_id=empresa_id, dados=dados, usuario_id=current_user.id
            )
        except CadastroFiscalErro as exc:
            db.session.rollback()
            flash(str(exc), "erro")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "erro")
        else:
            flash("Configuração fiscal salva.", "sucesso")
            return redirect(
                url_for(
                    "fiscal.configuracao",
                    empresa_id=empresa_id,
                    modelo=form.modelo.data,
                    ambiente=form.ambiente.data,
                    serie=form.serie.data,
                )
            )

    # Recarrega a lista após POST com erro para refletir somente o que foi
    # confirmado no banco.
    configuracoes = listar_configuracoes_documento(empresa_id)
    return render_template(
        "fiscal/configuracao.html",
        empresa=empresa,
        estabelecimento=estabelecimento,
        configuracoes=configuracoes,
        configuracao_selecionada=selecionada,
        form=form,
        credencial_form=CredencialForm(),
        credenciais=credenciais_cadastradas(empresa_id),
        chave_configurada=segredos.chave_configurada(),
        acao_form=AcaoForm(),
        orientacao=(
            orientacao_autorizador(estabelecimento.uf) if estabelecimento else ""
        ),
    )


@bp.post("/credenciais")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def salvar_credencial(empresa_id: int):
    form = CredencialForm()
    if not form.validate_on_submit():
        flash("Informe a credencial corretamente.", "erro")
        return _voltar(empresa_id, "fiscal.configuracao")
    try:
        guardar_credencial(
            empresa_id=empresa_id,
            nome=form.nome.data,
            valor=form.valor.data,
            usuario_id=current_user.id,
        )
    except (CadastroFiscalErro, segredos.SegredoErro) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash("Credencial guardada de forma cifrada.", "sucesso")
    return _voltar(empresa_id, "fiscal.configuracao")


@bp.post("/credenciais/<nome>/remover")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def excluir_credencial(empresa_id: int, nome: str):
    if not AcaoForm().validate_on_submit():
        abort(400)
    try:
        removida = remover_credencial(
            empresa_id=empresa_id, nome=nome, usuario_id=current_user.id
        )
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "erro")
        return _voltar(empresa_id, "fiscal.configuracao")
    flash(
        "Credencial removida." if removida else "Essa credencial não estava cadastrada.",
        "sucesso" if removida else "aviso",
    )
    return _voltar(empresa_id, "fiscal.configuracao")


# --------------------------------------------------------------------------
# Classificação fiscal dos produtos
# --------------------------------------------------------------------------


@bp.get("/produtos")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def produtos(empresa_id: int):
    empresa = _empresa(empresa_id)
    somente_pendentes = request.args.get("pendentes", "1") == "1"
    consulta = (
        db.select(Produto, ProdutoFiscal)
        .outerjoin(ProdutoFiscal, ProdutoFiscal.produto_id == Produto.id)
        .where(Produto.empresa_id == empresa_id, Produto.ativo.is_(True))
        .order_by(Produto.nome)
    )
    if somente_pendentes:
        consulta = consulta.where(
            db.or_(
                ProdutoFiscal.produto_id.is_(None),
                ProdutoFiscal.revisao_pendente.is_(True),
            )
        )
    registros = db.session.execute(consulta.limit(400)).all()
    return render_template(
        "fiscal/produtos.html",
        empresa=empresa,
        registros=registros,
        somente_pendentes=somente_pendentes,
        lote_form=ClassificacaoLoteForm(),
    )


def _dados_da_classificacao(produto: Produto, registro: ProdutoFiscal | None) -> dict:
    """Preenche o formulário de classificação, convertendo o que é seleção."""
    if registro is None:
        return {
            "cfop": "5102",
            "origem": "0",
            "icms_csosn": "102",
            "icms_cst": "",
            "pis_cst": "07",
            "cofins_cst": "07",
            "unidade_tributavel": produto.unidade,
            "icms_aliquota": "0",
            "icms_reducao_bc": "0",
            "pis_aliquota": "0",
            "cofins_aliquota": "0",
        }
    return {
        "ncm": registro.ncm,
        "cest": registro.cest or "",
        "cfop": registro.cfop,
        "origem": str(registro.origem),
        "gtin": registro.gtin or "",
        "unidade_tributavel": registro.unidade_tributavel or produto.unidade,
        "icms_csosn": registro.icms_csosn or "",
        "icms_cst": registro.icms_cst or "",
        "icms_aliquota": str(registro.icms_aliquota),
        "icms_reducao_bc": str(registro.icms_reducao_bc),
        "pis_cst": registro.pis_cst,
        "pis_aliquota": str(registro.pis_aliquota),
        "cofins_cst": registro.cofins_cst,
        "cofins_aliquota": str(registro.cofins_aliquota),
        "observacao": registro.observacao or "",
    }


@bp.route("/produtos/<uuid:produto_id>", methods=["GET", "POST"])
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def classificar(empresa_id: int, produto_id: uuid.UUID):
    empresa = _empresa(empresa_id)
    produto = db.session.scalar(
        db.select(Produto).where(Produto.id == produto_id, Produto.empresa_id == empresa_id)
    )
    if produto is None:
        abort(404)
    registro = db.session.get(ProdutoFiscal, produto_id)
    if request.method == "GET":
        form = ClassificacaoForm(data=_dados_da_classificacao(produto, registro))
    else:
        form = ClassificacaoForm()

    if form.validate_on_submit():
        try:
            salvar_classificacao(
                empresa_id=empresa_id,
                produto_id=produto_id,
                dados={campo.name: campo.data for campo in form},
                usuario_id=current_user.id,
            )
        except CadastroFiscalErro as exc:
            db.session.rollback()
            flash(str(exc), "erro")
        else:
            flash(f"Classificação fiscal de {produto.nome} salva.", "sucesso")
            return redirect(url_for("fiscal.produtos", empresa_id=empresa_id))

    return render_template(
        "fiscal/classificar.html",
        empresa=empresa,
        produto=produto,
        registro=registro,
        form=form,
    )


@bp.post("/produtos/lote")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def classificar_em_lote(empresa_id: int):
    form = ClassificacaoLoteForm()
    if not form.validate_on_submit():
        flash("Revise os campos da classificação em lote.", "erro")
        return redirect(url_for("fiscal.produtos", empresa_id=empresa_id))
    if (form.confirmacao.data or "").strip().upper() != "APLICAR":
        flash("Digite APLICAR para confirmar a classificação em lote.", "erro")
        return redirect(url_for("fiscal.produtos", empresa_id=empresa_id))
    try:
        total = aplicar_classificacao_em_lote(
            empresa_id=empresa_id,
            dados={campo.name: campo.data for campo in form},
            usuario_id=current_user.id,
        )
    except CadastroFiscalErro as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash(f"{total} produto(s) classificado(s). Revise as exceções.", "sucesso")
    return redirect(url_for("fiscal.produtos", empresa_id=empresa_id))


# --------------------------------------------------------------------------
# Documentos
# --------------------------------------------------------------------------


def _documento(empresa_id: int, documento_id: uuid.UUID) -> DocumentoFiscal:
    documento = db.session.scalar(
        db.select(DocumentoFiscal)
        .options(
            selectinload(DocumentoFiscal.itens),
            selectinload(DocumentoFiscal.pagamentos),
            selectinload(DocumentoFiscal.eventos),
            selectinload(DocumentoFiscal.arquivos),
        )
        .where(
            DocumentoFiscal.id == documento_id,
            DocumentoFiscal.empresa_id == empresa_id,
        )
    )
    if documento is None:
        abort(404)
    return documento


@bp.get("/documentos")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def documentos(empresa_id: int):
    empresa = _empresa(empresa_id)
    status = (request.args.get("status") or "").strip()
    consulta = (
        db.select(DocumentoFiscal)
        .where(DocumentoFiscal.empresa_id == empresa_id)
        .order_by(DocumentoFiscal.numero.desc())
    )
    if status:
        consulta = consulta.where(DocumentoFiscal.status == status)
    return render_template(
        "fiscal/documentos.html",
        empresa=empresa,
        documentos=db.session.scalars(consulta.limit(200)).all(),
        status=status,
        acao_form=AcaoForm(),
    )


@bp.get("/documentos/<uuid:documento_id>")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def documento_detalhe(empresa_id: int, documento_id: uuid.UUID):
    empresa = _empresa(empresa_id)
    documento = _documento(empresa_id, documento_id)
    return render_template(
        "fiscal/documento.html",
        empresa=empresa,
        documento=documento,
        acao_form=AcaoForm(),
        cancelar_form=CancelarDocumentoForm(),
    )


@bp.get("/documentos/<uuid:documento_id>/danfe")
@login_required
@acesso_empresa_obrigatorio
def danfe(empresa_id: int, documento_id: uuid.UUID):
    empresa = _empresa(empresa_id)
    documento = _documento(empresa_id, documento_id)
    if current_user.papel == "operador":
        venda = documento.venda
        if venda is None or venda.operador_id != current_user.id:
            abort(404)
    estabelecimento = db.session.get(EstabelecimentoFiscal, empresa_id)
    return render_template(
        "fiscal/danfe.html",
        empresa=empresa,
        documento=documento,
        dados=montar_danfe(documento, estabelecimento),
        qrcode=qrcode_do_documento(documento),
    )


@bp.get("/documentos/<uuid:documento_id>/xml")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def documento_xml(empresa_id: int, documento_id: uuid.UUID):
    documento = _documento(empresa_id, documento_id)
    tipo = (request.args.get("tipo") or "autorizado").strip()
    arquivo = db.session.scalar(
        db.select(DocumentoFiscalXml).where(
            DocumentoFiscalXml.documento_id == documento.id,
            DocumentoFiscalXml.tipo == tipo,
        )
    )
    if arquivo is None:
        abort(404)
    return Response(
        arquivo.arquivo_xml,
        mimetype="application/xml",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{documento.chave}-{tipo}.xml"'
            ),
            "Cache-Control": "no-store, max-age=0",
        },
    )


@bp.post("/documentos/<uuid:documento_id>/consultar-sefaz")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def consultar_documento_sefaz(empresa_id: int, documento_id: uuid.UUID):
    if not AcaoForm().validate_on_submit():
        abort(400)
    try:
        documento = consultar_e_reconciliar(
            empresa_id=empresa_id, documento_id=documento_id, usuario_id=current_user.id
        )
    except (EmissaoErro, ProvedorFiscalErro, segredos.SegredoErro) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash(
            f"Consulta concluída: {documento.status_rotulo}. "
            f"{documento.codigo_status or ''} {documento.mensagem_status or ''}".strip(),
            "sucesso" if documento.status in {"autorizado", "cancelado"} else "aviso",
        )
    return redirect(
        url_for("fiscal.documento_detalhe", empresa_id=empresa_id, documento_id=documento_id)
    )


@bp.post("/documentos/<uuid:documento_id>/reenviar")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def reenviar_documento(empresa_id: int, documento_id: uuid.UUID):
    if not AcaoForm().validate_on_submit():
        abort(400)
    try:
        documento = reenviar(
            empresa_id=empresa_id, documento_id=documento_id, usuario_id=current_user.id
        )
    except (EmissaoErro, ProvedorFiscalErro, segredos.SegredoErro) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash(
            f"Reenvio concluído: {documento.status_rotulo}. "
            f"{documento.mensagem_status or ''}".strip(),
            "sucesso" if documento.status == "autorizado" else "aviso",
        )
    return redirect(
        url_for("fiscal.documento_detalhe", empresa_id=empresa_id, documento_id=documento_id)
    )


@bp.post("/documentos/<uuid:documento_id>/cancelar")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def cancelar(empresa_id: int, documento_id: uuid.UUID):
    form = CancelarDocumentoForm()
    if not form.validate_on_submit():
        flash("A justificativa precisa ter de 15 a 255 caracteres.", "erro")
        return redirect(
            url_for("fiscal.documento_detalhe", empresa_id=empresa_id, documento_id=documento_id)
        )
    try:
        cancelar_documento(
            empresa_id=empresa_id,
            documento_id=documento_id,
            justificativa=form.justificativa.data,
            usuario_id=current_user.id,
        )
    except (EmissaoErro, ProvedorFiscalErro, segredos.SegredoErro) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash("Documento cancelado na SEFAZ.", "sucesso")
    return redirect(
        url_for("fiscal.documento_detalhe", empresa_id=empresa_id, documento_id=documento_id)
    )


# --------------------------------------------------------------------------
# Inutilização
# --------------------------------------------------------------------------


@bp.get("/inutilizacoes")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def inutilizacoes(empresa_id: int):
    empresa = _empresa(empresa_id)
    registros = db.session.scalars(
        db.select(InutilizacaoFiscal)
        .where(InutilizacaoFiscal.empresa_id == empresa_id)
        .order_by(InutilizacaoFiscal.serie, InutilizacaoFiscal.numero)
        .limit(500)
    ).all()
    pendentes = [
        registro.numero for registro in registros if registro.status == "pendente"
    ]
    return render_template(
        "fiscal/inutilizacoes.html",
        empresa=empresa,
        registros=registros,
        faixas=agrupar_faixas(pendentes),
        acao_form=AcaoForm(),
        inutilizar_form=InutilizarForm(),
    )


@bp.post("/inutilizacoes/detectar")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def detectar(empresa_id: int):
    if not AcaoForm().validate_on_submit():
        abort(400)
    lacunas = detectar_lacunas(empresa_id)
    if not lacunas:
        flash("Nenhuma lacuna de numeração encontrada.", "sucesso")
        return _voltar(empresa_id, "fiscal.inutilizacoes")
    registros = registrar_lacunas(empresa_id, usuario_id=current_user.id)
    db.session.commit()
    flash(
        f"{len(registros)} número(s) sem documento autorizado registrados como pendência.",
        "aviso",
    )
    return _voltar(empresa_id, "fiscal.inutilizacoes")


@bp.post("/inutilizacoes/executar")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def executar_inutilizacao(empresa_id: int):
    form = InutilizarForm()
    if not form.validate_on_submit():
        flash("Revise a faixa e a justificativa.", "erro")
        return _voltar(empresa_id, "fiscal.inutilizacoes")
    try:
        resposta = inutilizar_faixa(
            empresa_id=empresa_id,
            numero_inicial=form.numero_inicial.data,
            numero_final=form.numero_final.data,
            justificativa=form.justificativa.data,
            usuario_id=current_user.id,
        )
    except (EmissaoErro, ProvedorFiscalErro, segredos.SegredoErro) as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    else:
        flash(
            f"{resposta.codigo_status} — {resposta.mensagem}",
            "sucesso" if resposta.sucesso else "erro",
        )
    return _voltar(empresa_id, "fiscal.inutilizacoes")


# --------------------------------------------------------------------------
# Importação do acervo antigo
# --------------------------------------------------------------------------


@bp.route("/importar", methods=["GET", "POST"])
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario")
def importar(empresa_id: int):
    empresa = _empresa(empresa_id)
    form = ImportacaoForm()
    resultado: ResultadoImportacao | None = None

    if form.validate_on_submit():
        enviados = request.files.getlist("arquivos")
        pasta = (form.pasta.data or "").strip()
        if enviados and any(arquivo.filename for arquivo in enviados):
            resultado = ResultadoImportacao()
            for arquivo in enviados:
                if not arquivo.filename:
                    continue
                try:
                    importar_conteudo(
                        arquivo.read(), empresa_id=empresa_id, resultado=resultado
                    )
                except (ImportacaoErro, ValueError) as exc:
                    resultado.erros.append(f"{arquivo.filename}: {exc}")
            db.session.commit()
        elif pasta:
            resultado = importar_pasta(pasta, empresa_id=empresa_id)
            db.session.commit()
        else:
            flash("Envie arquivos XML ou informe uma pasta do servidor.", "erro")

        if resultado is not None:
            flash(
                f"{resultado.importados} documento(s) importado(s), "
                f"{resultado.cancelamentos} cancelamento(s), "
                f"{resultado.inutilizacoes} inutilização(ões), "
                f"{resultado.ignorados} ignorado(s).",
                "sucesso" if not resultado.erros else "aviso",
            )

    return render_template(
        "fiscal/importar.html", empresa=empresa, form=form, resultado=resultado
    )
