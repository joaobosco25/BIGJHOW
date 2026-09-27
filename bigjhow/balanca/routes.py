from __future__ import annotations

from decimal import Decimal, InvalidOperation
from sqlalchemy import or_

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import CodigoProduto, ConfiguracaoBalanca, Empresa, Produto, utcnow
from ..security import acesso_empresa_obrigatorio, papel_obrigatorio
from ..services.auditoria import registrar
from ..services.mgv6_cliente import ErroMGV6
from ..services.mgv6_integracao import VinculoMGV6Invalido, escrita_ativa, testar_conexao
from ..services.balanca import (
    CAMPOS_CARGA,
    EtiquetaInvalida,
    LayoutEtiqueta,
    gerar_carga,
    identificar_etiqueta,
    layout_da_empresa,
    montar_linhas_carga,
    quantidade_da_etiqueta,
)
from .forms import AcaoForm, ConfiguracaoBalancaForm, TesteEtiquetaForm


bp = Blueprint("balanca", __name__, url_prefix="/empresa/<int:empresa_id>/balanca")


def _empresa(empresa_id: int) -> Empresa:
    empresa = db.session.get(Empresa, empresa_id)
    if empresa is None or not empresa.ativa:
        abort(404)
    return empresa


def _dados_do_formulario(configuracao: ConfiguracaoBalanca) -> dict:
    return {
        "ativo": configuracao.ativo,
        "modelo": configuracao.modelo or "",
        "prefixo": configuracao.prefixo,
        "tamanho_plu": configuracao.tamanho_plu,
        "conteudo": configuracao.conteudo,
        "casas_decimais": configuracao.casas_decimais,
        "tolerancia": f"{Decimal(configuracao.tolerancia):.2f}".replace(".", ","),
        "bloquear_divergencia": configuracao.bloquear_divergencia,
        "separador_carga": configuracao.separador_carga,
        "codificacao_carga": configuracao.codificacao_carga,
        "quebra_linha_carga": configuracao.quebra_linha_carga,
        "campos_carga": ",".join(configuracao.campos_carga or []),
    }


_TERMOS_CARNE_BALANCA = (
    "ACEM",
    "ACÉM",
    "ALCATRA",
    "ALMONDEGA",
    "ALMÔNDEGA",
    "ANCHO",
    "BABY BEEF",
    "BACON",
    "BANANINHA",
    "BARRIGA",
    "BIFE",
    "BISTECA",
    "BRISKET",
    "CALABRESA",
    "CARNE",
    "CONTRA FILE",
    "CONTRA FILÉ",
    "CONTRAFILE",
    "CONTRAFILÉ",
    "CORACAO",
    "CORAÇÃO",
    "CORDEIRO",
    "COSTELA",
    "COSTELINHA",
    "COXA",
    "COXAO",
    "COXÃO",
    "COXINHA DA ASA",
    "CUPIM",
    "DENVER STEAK",
    "FILE DE COSTELA",
    "FILÉ DE COSTELA",
    "FILE DE COXA",
    "FILÉ DE COXA",
    "FILE DE FRANGO",
    "FILÉ DE FRANGO",
    "FILE DE PEITO",
    "FILÉ DE PEITO",
    "FILET MIGNON",
    "FILÉ MIGNON",
    "FLAT BEEF",
    "FLAT IRON",
    "FRANBACON",
    "FRANGO",
    "FRAUDINHA",
    "FRALDINHA",
    "HAMBURGUER",
    "HAMBÚRGUER",
    "KIT FEIJOADA",
    "KAFTA",
    "KIBE",
    "LING ",
    "LINGUICA",
    "LINGUIÇA",
    "LOMBO",
    "MACA DE PEITO",
    "MAÇÃ DE PEITO",
    "MAMINHA",
    "MOELA",
    "NUGGETS",
    "MUSCULO",
    "MÚSCULO",
    "OSSO BUCO",
    "PALETA",
    "PANCETA",
    "PATINHO",
    "PEITO DE FRANGO",
    "PERNIL",
    "PICANHA",
    "PORK",
    "PRESUNTO",
    "PRIME STEAK",
    "RABADA",
    "ROCAMBOLE DE FRANGO",
    "SALSICHA",
    "SHOULDER STEAK",
    "SUAN",
    "SUINO",
    "SUÍNO",
    "T BONE",
    "T-BONE",
    "TORRESMO",
    "TOUCINHO",
    "TULIPA",
    "VACA",
    "VULCAO",
    "VULCÃO",
)


def _produtos_para_balanca(empresa_id: int, busca: str) -> list[dict]:
    """Lista apenas produtos que fazem sentido na balança, sem alterar cadastro.

    A busca é deliberadamente somente leitura. Ela consulta nome, código externo,
    SKU, categoria e códigos ativos já cadastrados. Nenhuma regra do caixa, leitura
    de etiqueta ou cálculo de peso passa por aqui.
    """
    plu_ativo = db.select(CodigoProduto.id).where(
        CodigoProduto.produto_id == Produto.id,
        CodigoProduto.empresa_id == empresa_id,
        CodigoProduto.ativo.is_(True),
        CodigoProduto.tipo == "plu",
    ).exists()
    filtro_carne = or_(
        *(Produto.nome.ilike(f"%{termo}%") for termo in _TERMOS_CARNE_BALANCA)
    )
    query = db.select(Produto).where(
        Produto.empresa_id == empresa_id,
        Produto.ativo.is_(True),
        or_(Produto.pesado.is_(True), plu_ativo),
        filtro_carne,
    )
    if busca:
        termo = f"%{busca}%"
        codigo_corresponde = db.select(CodigoProduto.id).where(
            CodigoProduto.produto_id == Produto.id,
            CodigoProduto.empresa_id == empresa_id,
            CodigoProduto.ativo.is_(True),
            or_(
                CodigoProduto.codigo.ilike(termo),
                CodigoProduto.chave_busca.ilike(termo),
            ),
        ).exists()
        query = query.where(
            or_(
                Produto.nome.ilike(termo),
                Produto.codigo_externo.ilike(termo),
                Produto.sku_interno.ilike(termo),
                Produto.categoria.ilike(termo),
                codigo_corresponde,
            )
        )
    produtos = db.session.scalars(query.order_by(Produto.nome.asc()).limit(80)).all()

    resultado: list[dict] = []
    for produto in produtos:
        ativos = [codigo for codigo in produto.codigos if codigo.ativo]
        codigo = (
            next((item for item in ativos if item.tipo == "plu"), None)
            or next((item for item in ativos if item.principal), None)
            or (ativos[0] if ativos else None)
        )
        resultado.append({"produto": produto, "codigo": codigo})
    return resultado


@bp.route("/configuracao", methods=["GET", "POST"])
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def configuracao(empresa_id: int):
    empresa = _empresa(empresa_id)
    registro = db.session.get(ConfiguracaoBalanca, empresa_id)
    form = ConfiguracaoBalancaForm(
        data=_dados_do_formulario(registro) if registro else None
    )
    busca_produto = request.args.get("q", "").strip()[:120]

    if form.validate_on_submit():
        campos = [
            parte.strip()
            for parte in (form.campos_carga.data or "").split(",")
            if parte.strip()
        ]
        desconhecidos = [campo for campo in campos if campo not in CAMPOS_CARGA]
        try:
            tolerancia = Decimal(
                (form.tolerancia.data or "0").replace(",", ".")
            ).quantize(Decimal("0.01"))
        except InvalidOperation:
            tolerancia = None

        layout = LayoutEtiqueta(
            prefixo=(form.prefixo.data or "").strip(),
            tamanho_plu=int(form.tamanho_plu.data or 0),
            conteudo=form.conteudo.data,
            casas_decimais=int(form.casas_decimais.data or 0),
            ativo=bool(form.ativo.data),
        )
        problemas = layout.conferir()
        if desconhecidos:
            problemas.append(f"Coluna desconhecida na carga: {', '.join(desconhecidos)}.")
        if not campos:
            problemas.append("Informe ao menos uma coluna para o arquivo de carga.")
        if tolerancia is None or tolerancia < 0:
            problemas.append("A tolerância precisa ser um valor em reais, como 0,05.")

        if problemas:
            for problema in problemas:
                flash(problema, "erro")
        else:
            if registro is None:
                registro = ConfiguracaoBalanca(empresa_id=empresa_id)
                db.session.add(registro)
            registro.ativo = bool(form.ativo.data)
            registro.modelo = " ".join((form.modelo.data or "").split())[:60] or None
            registro.prefixo = layout.prefixo
            registro.tamanho_plu = layout.tamanho_plu
            registro.conteudo = layout.conteudo
            registro.casas_decimais = layout.casas_decimais
            registro.tolerancia = tolerancia
            registro.bloquear_divergencia = bool(form.bloquear_divergencia.data)
            registro.separador_carga = (form.separador_carga.data or ";")[:3]
            registro.codificacao_carga = form.codificacao_carga.data
            registro.quebra_linha_carga = form.quebra_linha_carga.data
            registro.campos_carga = campos
            registro.versao = int(registro.versao or 0) + 1
            registro.atualizado_por_id = current_user.id
            registrar(
                "balanca_configurada",
                "configuracao_balanca",
                empresa_id,
                empresa_id,
                {
                    "ativo": registro.ativo,
                    "prefixo": registro.prefixo,
                    "tamanho_plu": registro.tamanho_plu,
                    "conteudo": registro.conteudo,
                    "casas_decimais": registro.casas_decimais,
                },
                usuario_id=current_user.id,
            )
            db.session.commit()
            flash("Configuração da balança salva.", "sucesso")
            return redirect(url_for("balanca.configuracao", empresa_id=empresa_id))

    total_plu = len(montar_linhas_carga(empresa_id))
    return render_template(
        "balanca/configuracao.html",
        empresa=empresa,
        registro=registro,
        form=form,
        teste_form=TesteEtiquetaForm(),
        acao_form=AcaoForm(),
        campos_disponiveis=CAMPOS_CARGA,
        total_plu=total_plu,
        layout_ativo=layout_da_empresa(empresa_id).ativo,
        mgv6_ativo=bool(current_app.config.get("MGV6_ENABLED")),
        mgv6_escrita=escrita_ativa(),
        mgv6_url=current_app.config.get("MGV6_BASE_URL", ""),
        mgv6_loja=current_app.config.get("MGV6_LOJA", 1),
        mgv6_balanca=current_app.config.get("MGV6_BALANCA", 1),
        busca_produto=busca_produto,
        produtos_balanca=_produtos_para_balanca(empresa_id, busca_produto),
    )


@bp.post("/mgv6/testar")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def mgv6_testar(empresa_id: int):
    if not AcaoForm().validate_on_submit():
        abort(400)
    _empresa(empresa_id)
    if not current_app.config.get("MGV6_ENABLED"):
        flash("Integração MGV6 está desativada no ambiente.", "aviso")
        return redirect(url_for("balanca.configuracao", empresa_id=empresa_id))
    try:
        resultado = testar_conexao()
    except (ErroMGV6, VinculoMGV6Invalido) as exc:
        flash(f"MGV6 não respondeu corretamente: {exc}", "erro")
    else:
        registrar(
            "mgv6_conexao_testada",
            "configuracao_balanca",
            empresa_id,
            empresa_id,
            {
                "versao": resultado["versao"],
                "loja": resultado["loja"],
                "balanca": resultado["balanca"],
                "balancas_retornadas": resultado["quantidade_balancas"],
                "comunicacoes_pendentes": resultado["comunicacoes_pendentes"],
            },
            usuario_id=current_user.id,
        )
        db.session.commit()
        flash(
            f"MGV6 {resultado['versao']} respondeu; loja {resultado['loja']}, "
            f"balança configurada {resultado['balanca']}. "
            f"Comunicações pendentes: {resultado['comunicacoes_pendentes']}.",
            "sucesso",
        )
    return redirect(url_for("balanca.configuracao", empresa_id=empresa_id))


@bp.post("/testar")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def testar(empresa_id: int):
    form = TesteEtiquetaForm()
    if not form.validate_on_submit():
        flash("Informe os 13 dígitos do código de barras da etiqueta.", "erro")
        return redirect(url_for("balanca.configuracao", empresa_id=empresa_id))

    codigo = (form.codigo.data or "").strip()
    try:
        codigo_produto, etiqueta = identificar_etiqueta(empresa_id, codigo)
    except EtiquetaInvalida as exc:
        flash(str(exc), "erro")
        return redirect(url_for("balanca.configuracao", empresa_id=empresa_id))

    produto = db.session.get(Produto, codigo_produto.produto_id)
    try:
        leitura = quantidade_da_etiqueta(
            empresa_id, etiqueta, Decimal(produto.preco_venda)
        )
    except EtiquetaInvalida as exc:
        flash(str(exc), "aviso")
        return redirect(url_for("balanca.configuracao", empresa_id=empresa_id))

    if etiqueta.traz_peso:
        flash(
            f"PLU {etiqueta.plu} → {produto.nome} · {leitura.quantidade} "
            f"{produto.unidade} lidos da etiqueta.",
            "sucesso",
        )
    else:
        flash(
            f"PLU {etiqueta.plu} → {produto.nome} · etiqueta de "
            f"R$ {leitura.valor_etiqueta:.2f} ÷ R$ {Decimal(produto.preco_venda):.2f} = "
            f"{leitura.quantidade} {produto.unidade} "
            f"(divergência R$ {leitura.divergencia:.2f}).",
            "sucesso" if not leitura.tem_divergencia else "aviso",
        )
    return redirect(url_for("balanca.configuracao", empresa_id=empresa_id))


@bp.post("/carga")
@login_required
@acesso_empresa_obrigatorio
@papel_obrigatorio("proprietario", "administrador")
def carga(empresa_id: int):
    if not AcaoForm().validate_on_submit():
        abort(400)
    _empresa(empresa_id)
    try:
        conteudo, nome, linhas = gerar_carga(empresa_id)
    except EtiquetaInvalida as exc:
        flash(str(exc), "erro")
        return redirect(url_for("balanca.configuracao", empresa_id=empresa_id))
    if not linhas:
        flash(
            "Nenhum produto tem código do tipo PLU cadastrado — não há o que "
            "carregar na balança.",
            "aviso",
        )
        return redirect(url_for("balanca.configuracao", empresa_id=empresa_id))

    registrar(
        "balanca_carga_gerada",
        "configuracao_balanca",
        empresa_id,
        empresa_id,
        {"linhas": linhas},
        usuario_id=current_user.id,
    )
    db.session.commit()
    carimbo = utcnow().strftime("%Y%m%d-%H%M")
    return Response(
        conteudo,
        mimetype="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="{carimbo}-{nome}"',
            "Cache-Control": "no-store, max-age=0",
        },
    )
