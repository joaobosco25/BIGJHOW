"""Orquestração da emissão fiscal a partir de uma venda do caixa.

Princípio que guia este módulo: **a venda já aconteceu**. O dinheiro entrou,
o estoque baixou, o cliente está na frente do balcão. Se a SEFAZ estiver fora
do ar, o caixa não pode travar — o documento fica pendente, o número já
consumido é rastreado, e o painel fiscal mostra o que precisa ser resolvido.

Por isso a emissão roda depois que a venda foi confirmada, em transação
própria, e nenhuma falha aqui desfaz a venda.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy.exc import IntegrityError

from ...extensions import db
from ...models import (
    DocumentoFiscal,
    DocumentoFiscalEvento,
    DocumentoFiscalItem,
    DocumentoFiscalPagamento,
    DocumentoFiscalXml,
    EstabelecimentoFiscal,
    InutilizacaoFiscal,
    ProdutoFiscal,
    Venda,
    VendaItem,
    VendaPagamento,
    utcnow,
)
from ..auditoria import registrar
from . import estruturas
from .assinatura import AssinaturaErro, assinar_documento, carregar_certificado, extrair_digest
from .cadastro import (
    configuracao_provedor,
    ler_credencial,
    obter_configuracao_documento,
)
from .chave import montar_chave
from .qrcode_nfce import QRCodeFiscalErro, montar_qrcode
from .provedores import (
    ProvedorFiscalErro,
    ProvedorIndisponivel,
    RespostaFiscal,
    criar_provedor,
    justificativa_valida,
)
from . import rtc_operacao as rtc_ensaio, legado_homologacao, legado_producao
from .segredos import SegredoErro
from .sequencia import SequenciaErro, reservar_numero
from .uf import (
    TIPO_EMISSAO_CONTINGENCIA_OFFLINE,
    TIPO_EMISSAO_NORMAL,
    codigo_da_uf,
)
from .xml_nfce import (
    FORMA_POR_METODO_CAIXA,
    NAMESPACE,
    XmlFiscalErro,
    montar_nfe,
    incluir_suplemento,
    serializar,
)


class EmissaoErro(RuntimeError):
    pass


class EmissaoBloqueada(EmissaoErro):
    """A empresa não está apta a emitir. A venda segue sem documento fiscal."""


def _agora(estabelecimento: EstabelecimentoFiscal) -> datetime:
    try:
        fuso = ZoneInfo(current_app.config.get("APP_TIMEZONE", "America/Sao_Paulo"))
    except Exception:
        fuso = timezone.utc
    return datetime.now(timezone.utc).astimezone(fuso)


# --------------------------------------------------------------------------
# Montagem dos dados fiscais a partir da venda
# --------------------------------------------------------------------------


def _emitente(estabelecimento: EstabelecimentoFiscal) -> estruturas.Emitente:
    return estruturas.Emitente(
        cnpj=estabelecimento.cnpj,
        razao_social=estabelecimento.razao_social,
        nome_fantasia=estabelecimento.nome_fantasia,
        inscricao_estadual=estabelecimento.inscricao_estadual,
        inscricao_municipal=estabelecimento.inscricao_municipal,
        crt=int(estabelecimento.crt),
        endereco=estruturas.Endereco(
            logradouro=estabelecimento.logradouro,
            numero=estabelecimento.numero,
            complemento=estabelecimento.complemento,
            bairro=estabelecimento.bairro,
            codigo_municipio=int(estabelecimento.codigo_municipio),
            municipio=estabelecimento.municipio,
            uf=estabelecimento.uf,
            cep=estabelecimento.cep,
            telefone=estabelecimento.telefone,
        ),
    )


def _item_fiscal(numero: int, item: VendaItem, fiscal: ProdutoFiscal) -> estruturas.ItemFiscal:
    quantidade = Decimal(item.quantidade)
    valor_total = Decimal(item.subtotal)
    icms = estruturas.TributoICMS(
        origem=int(fiscal.origem),
        cst=fiscal.icms_cst,
        csosn=fiscal.icms_csosn,
        modalidade_bc=int(fiscal.icms_modalidade_bc),
        aliquota=Decimal(fiscal.icms_aliquota),
        percentual_reducao_bc=Decimal(fiscal.icms_reducao_bc),
    )
    # Base e valor só existem quando a situação tributária os exige.
    if (fiscal.icms_cst or "") in {"00", "20", "51", "90"} or (fiscal.icms_csosn or "") == "900":
        base = valor_total
        if Decimal(fiscal.icms_reducao_bc) > 0:
            base = (base * (Decimal("100") - Decimal(fiscal.icms_reducao_bc)) / Decimal("100"))
        icms.base_calculo = base.quantize(Decimal("0.01"))
        icms.valor = (icms.base_calculo * Decimal(fiscal.icms_aliquota) / Decimal("100")).quantize(
            Decimal("0.01")
        )

    def contribuicao(cst: str, aliquota: Decimal) -> estruturas.TributoContribuicao:
        tributo = estruturas.TributoContribuicao(cst=cst)
        if cst in {"01", "02"} or (cst not in {"04", "05", "06", "07", "08", "09"}):
            tributo.base_calculo = valor_total
            tributo.aliquota = Decimal(aliquota)
            tributo.valor = (valor_total * Decimal(aliquota) / Decimal("100")).quantize(
                Decimal("0.01")
            )
        return tributo

    return estruturas.ItemFiscal(
        numero=numero,
        codigo_produto=item.sku_interno,
        descricao=item.produto_nome,
        ncm=fiscal.ncm,
        cest=fiscal.cest,
        cfop=fiscal.cfop,
        unidade_comercial=item.unidade,
        quantidade_comercial=quantidade,
        valor_unitario=Decimal(item.preco_unitario),
        valor_total=valor_total,
        ean=fiscal.gtin,
        ean_tributavel=fiscal.gtin,
        unidade_tributavel=fiscal.unidade_tributavel or item.unidade,
        quantidade_tributavel=quantidade,
        valor_unitario_tributavel=Decimal(item.preco_unitario),
        icms=icms,
        pis=contribuicao(fiscal.pis_cst, fiscal.pis_aliquota),
        cofins=contribuicao(fiscal.cofins_cst, fiscal.cofins_aliquota),
    )


def montar_dados(
    venda: Venda,
    estabelecimento: EstabelecimentoFiscal,
    *,
    numero: int,
    serie: int,
    modelo: int,
    codigo_numerico: int,
    tipo_emissao: int,
) -> estruturas.DocumentoDados:
    itens_venda = db.session.scalars(
        db.select(VendaItem)
        .where(VendaItem.venda_id == venda.id)
        .order_by(VendaItem.criado_em)
    ).all()
    if not itens_venda:
        raise EmissaoErro("A venda não tem itens para emitir.")

    # ==========================================================
    # ROTA FISCAL CONTROLADA
    #
    # Empresa 1 em produção NÃO pode cair no cadastro fiscal
    # genérico nem no fallback de homologação.
    #
    # O catálogo de produção trabalha fail-closed:
    # - trava global fechada => bloqueia;
    # - produto fora da allowlist => bloqueia;
    # - revisão pendente => bloqueia.
    # ==========================================================
    if (
        int(estabelecimento.empresa_id) == 1
        and int(estabelecimento.ambiente) == 1
    ):
        try:
            catalogo_producao = legado_producao.carregar(estabelecimento)

            if catalogo_producao is None:
                raise XmlFiscalErro(
                    "Catálogo fiscal de produção indisponível."
                )

            # Valida TODOS os itens antes de montar qualquer item fiscal.
            # Um único produto não liberado bloqueia o documento inteiro.
            for item in itens_venda:
                legado_producao.produto_liberado(
                    catalogo_producao,
                    item.produto_id,
                )

            # Reutiliza o construtor fiscal já testado em homologação.
            # A autorização para usar cada entrada já foi verificada acima.
            itens = [
                legado_producao.montar_item(
                    posicao,
                    item,
                    catalogo_producao,
                )
                for posicao, item
                in enumerate(itens_venda, start=1)
            ]

        except XmlFiscalErro as exc:
            raise EmissaoBloqueada(
                f"Venda bloqueada pela proteção fiscal de produção: {exc}"
            ) from exc

    else:
        catalogo_legado = legado_homologacao.carregar(estabelecimento)
        if catalogo_legado is not None:
            itens = [legado_homologacao.montar_item(posicao, item, catalogo_legado)
                     for posicao, item in enumerate(itens_venda, start=1)]
        else:
            classificacoes = {
                registro.produto_id: registro
                for registro in db.session.scalars(
                    db.select(ProdutoFiscal).where(
                        ProdutoFiscal.produto_id.in_([item.produto_id for item in itens_venda])
                    )
                ).all()
            }
            faltando = [
                item.produto_nome
                for item in itens_venda
                if item.produto_id not in classificacoes
                or not classificacoes[item.produto_id].completo
            ]
            if faltando:
                resumo = ", ".join(faltando[:3])
                sufixo = "…" if len(faltando) > 3 else ""
                raise EmissaoBloqueada(
                    f"Sem classificação fiscal (NCM/CFOP/situação tributária): {resumo}{sufixo}."
                )

            itens = [
                _item_fiscal(posicao, item, classificacoes[item.produto_id])
                for posicao, item in enumerate(itens_venda, start=1)
            ]


    pagamentos_venda = db.session.scalars(
        db.select(VendaPagamento)
        .where(VendaPagamento.venda_id == venda.id)
        .order_by(VendaPagamento.criado_em)
    ).all()
    pagamentos = []
    for pagamento in pagamentos_venda:
        forma = FORMA_POR_METODO_CAIXA.get(pagamento.metodo)
        if forma is None:
            raise EmissaoErro(
                f"A forma de pagamento '{pagamento.metodo}' não tem código fiscal definido."
            )
        pagamentos.append(
            estruturas.PagamentoFiscal(forma=forma, valor=Decimal(pagamento.valor_recebido))
        )
    if not pagamentos:
        raise EmissaoErro("A venda não tem pagamentos registrados.")

    emissao = _agora(estabelecimento)
    chave = montar_chave(
        codigo_uf=codigo_da_uf(estabelecimento.uf),
        emissao=emissao,
        cnpj=estabelecimento.cnpj,
        modelo=modelo,
        serie=serie,
        numero=numero,
        tipo_emissao=tipo_emissao,
        codigo_numerico=codigo_numerico,
    )
    responsavel = None
    if estabelecimento.responsavel_cnpj:
        responsavel = estruturas.ResponsavelTecnico(
            cnpj=estabelecimento.responsavel_cnpj,
            contato=estabelecimento.responsavel_contato or "Responsável técnico",
            email=estabelecimento.responsavel_email or "",
            telefone=estabelecimento.responsavel_telefone or "",
        )
    return estruturas.DocumentoDados(
        chave=chave,
        codigo_numerico=codigo_numerico,
        modelo=modelo,
        serie=serie,
        numero=numero,
        emissao=emissao,
        ambiente=int(estabelecimento.ambiente),
        tipo_emissao=tipo_emissao,
        natureza_operacao=estabelecimento.natureza_operacao,
        emitente=_emitente(estabelecimento),
        itens=itens,
        pagamentos=pagamentos,
        destinatario=None,
        responsavel_tecnico=responsavel,
        troco=Decimal(venda.troco or 0),
        informacoes_complementares=estabelecimento.informacoes_complementares,
    )


# --------------------------------------------------------------------------
# Persistência
# --------------------------------------------------------------------------


def _gravar_documento(
    venda: Venda,
    estabelecimento: EstabelecimentoFiscal,
    dados: estruturas.DocumentoDados,
    *,
    provedor_nome: str,
    usuario_id: uuid.UUID | None,
) -> DocumentoFiscal:
    documento = DocumentoFiscal(
        empresa_id=venda.empresa_id,
        venda_id=venda.id,
        chave=dados.chave,
        codigo_numerico=dados.codigo_numerico,
        modelo=dados.modelo,
        serie=dados.serie,
        numero=dados.numero,
        ambiente=dados.ambiente,
        tipo_emissao=dados.tipo_emissao,
        cnpj_emitente=estabelecimento.cnpj,
        status="pendente",
        total_produtos=dados.total_produtos,
        total_desconto=dados.total_desconto,
        total_documento=dados.total_documento,
        total_base_icms=dados.total_base_icms,
        total_icms=dados.total_icms,
        total_pis=dados.total_pis,
        total_cofins=dados.total_cofins,
        troco=dados.troco,
        provedor=provedor_nome,
        data_emissao=dados.emissao,
        emitido_por_id=usuario_id,
    )
    db.session.add(documento)
    db.session.flush()

    for item in dados.itens:
        db.session.add(
            DocumentoFiscalItem(
                documento_id=documento.id,
                numero_item=item.numero,
                codigo_produto=item.codigo_produto,
                codigo_ean=item.ean,
                descricao=item.descricao,
                ncm=item.ncm,
                cest=item.cest,
                cfop=item.cfop,
                unidade=item.unidade_comercial,
                quantidade=item.quantidade_comercial,
                valor_unitario=item.valor_unitario,
                valor_total=item.valor_total,
                valor_desconto=item.valor_desconto,
                icms_origem=item.icms.origem,
                icms_cst=item.icms.cst,
                icms_csosn=item.icms.csosn,
                icms_base_calculo=item.icms.base_calculo,
                icms_aliquota=item.icms.aliquota,
                icms_valor=item.icms.valor,
                pis_cst=item.pis.cst,
                pis_valor=item.pis.valor,
                cofins_cst=item.cofins.cst,
                cofins_valor=item.cofins.valor,
            )
        )
    for pagamento in dados.pagamentos:
        db.session.add(
            DocumentoFiscalPagamento(
                documento_id=documento.id,
                forma=pagamento.forma,
                valor=pagamento.valor,
            )
        )
    db.session.flush()
    return documento


def _guardar_xml(documento: DocumentoFiscal, tipo: str, conteudo: bytes | None) -> None:
    if not conteudo:
        return
    existente = db.session.scalar(
        db.select(DocumentoFiscalXml).where(
            DocumentoFiscalXml.documento_id == documento.id,
            DocumentoFiscalXml.tipo == tipo,
        )
    )
    texto = conteudo.decode("utf-8", errors="replace")
    if existente is not None:
        existente.arquivo_xml = texto
        return
    db.session.add(
        DocumentoFiscalXml(documento_id=documento.id, tipo=tipo, arquivo_xml=texto)
    )


def _registrar_evento(
    documento: DocumentoFiscal,
    *,
    tipo: str,
    resposta: RespostaFiscal | None = None,
    mensagem: str | None = None,
    sucesso: bool = False,
    usuario_id: uuid.UUID | None = None,
) -> None:
    db.session.add(
        DocumentoFiscalEvento(
            documento_id=documento.id,
            tipo=tipo,
            provedor=documento.provedor,
            codigo_status=(resposta.codigo_status if resposta else None),
            mensagem=((resposta.mensagem if resposta else mensagem) or "")[:1000] or None,
            sucesso=sucesso or bool(resposta and resposta.sucesso),
            usuario_id=usuario_id,
        )
    )


# --------------------------------------------------------------------------
# Assinatura e QR Code
# --------------------------------------------------------------------------


def _assinar(dados: estruturas.DocumentoDados, configuracao: dict, *, ensaio_rtc: bool = False) -> tuple[bytes, str | None]:
    raiz = montar_nfe(dados)
    if ensaio_rtc:
        rtc_ensaio.aplicar(raiz, perfis=[i.rtc_perfil for i in dados.itens])
    inf = raiz.find(f"{{{NAMESPACE}}}infNFe")
    certificado_caminho = (configuracao.get("certificado_caminho") or "").strip()
    if not certificado_caminho:
        raise EmissaoBloqueada(
            "O certificado digital A1 não está configurado para esta empresa."
        )
    certificado = carregar_certificado(
        certificado_caminho, configuracao.get("certificado_senha", "")
    )
    if certificado.vencido:
        raise EmissaoBloqueada("O certificado digital está vencido.")
    assinar_documento(raiz, elemento_referenciado=inf, certificado=certificado)
    return serializar(raiz), extrair_digest(raiz)


def _qrcode(
    dados: estruturas.DocumentoDados,
    estabelecimento: EstabelecimentoFiscal,
    *,
    digest: str | None,
) -> str | None:
    if int(dados.modelo) != 65:
        return None

    if not estabelecimento.url_consulta_qrcode:
        return None

    contingencia = (
        int(dados.tipo_emissao)
        == TIPO_EMISSAO_CONTINGENCIA_OFFLINE
    )

    if contingencia:
        raise EmissaoBloqueada(
            "A contingência offline da NFC-e está bloqueada "
            "até a implementação e validação do QR Code v3."
        )

    try:
        return montar_qrcode(
            url_consulta_qrcode=estabelecimento.url_consulta_qrcode,
            chave=dados.chave,
            ambiente=int(dados.ambiente),
            contingencia=False,
        )
    except QRCodeFiscalErro:
        return None


# --------------------------------------------------------------------------
# Emissão
# --------------------------------------------------------------------------


def emitir_venda(
    *, empresa_id: int, venda_id: uuid.UUID, usuario_id: uuid.UUID | None = None
) -> DocumentoFiscal | None:
    """Emite o documento fiscal de uma venda finalizada.

    Devolve ``None`` quando a empresa ainda não está configurada para emitir —
    nesse caso a venda continua válida, apenas sem documento fiscal.
    """
    venda = db.session.scalar(
        db.select(Venda).where(Venda.id == venda_id, Venda.empresa_id == empresa_id)
    )
    if venda is None:
        raise EmissaoErro("Venda não encontrada nesta empresa.")
    if venda.status != "finalizada":
        raise EmissaoErro("Somente venda finalizada gera documento fiscal.")

    existente = db.session.scalar(
        db.select(DocumentoFiscal).where(DocumentoFiscal.venda_id == venda.id)
    )
    if existente is not None:
        return existente

    estabelecimento = db.session.get(EstabelecimentoFiscal, empresa_id)
    if estabelecimento is None or not estabelecimento.emissao_ativa:
        return None
    if int(estabelecimento.modelo) != 65:
        raise EmissaoBloqueada(
            "O caixa automático está liberado apenas para NFC-e modelo 65. "
            "A NF-e modelo 55 terá um fluxo próprio com destinatário completo."
        )

    configuracao = configuracao_provedor(estabelecimento)
    provedor = criar_provedor(estabelecimento.provedor, configuracao)

    try:
        ensaio_rtc = rtc_ensaio.selecionar(estabelecimento)
        if ensaio_rtc:
            rtc_ensaio.conferir_configuracao(configuracao)
    except XmlFiscalErro as exc:
        raise EmissaoBloqueada(str(exc)) from exc

    reserva = reservar_numero(empresa_id)
    dados = montar_dados(
        venda,
        estabelecimento,
        numero=reserva.numero,
        serie=reserva.serie,
        modelo=reserva.modelo,
        codigo_numerico=reserva.codigo_numerico,
        tipo_emissao=TIPO_EMISSAO_NORMAL,
    )
    if ensaio_rtc:
        try:
            rtc_ensaio.selecionar(estabelecimento, dados)
            rtc_ensaio.aplicar(montar_nfe(dados), perfis=[i.rtc_perfil for i in dados.itens])
        except XmlFiscalErro as exc:
            raise EmissaoBloqueada(str(exc)) from exc

    documento = _gravar_documento(
        venda,
        estabelecimento,
        dados,
        provedor_nome=provedor.nome,
        usuario_id=usuario_id,
    )
    # Confirma o consumo do número ANTES de transmitir: se a transmissão falhar,
    # o número fica registrado como pendente e entra na varredura de lacunas,
    # em vez de sumir sem rastro.
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise EmissaoErro(
            "Conflito de numeração fiscal. Tente emitir novamente."
        ) from exc

    return _transmitir(
        documento,
        dados,
        estabelecimento,
        provedor,
        configuracao,
        usuario_id=usuario_id,
        tipo_evento="emissao",
    )


def _transmitir(
    documento: DocumentoFiscal,
    dados: estruturas.DocumentoDados,
    estabelecimento: EstabelecimentoFiscal,
    provedor,
    configuracao: dict,
    *,
    usuario_id: uuid.UUID | None,
    tipo_evento: str,
) -> DocumentoFiscal:
    documento.status = "processando"
    documento.tentativas += 1
    db.session.commit()

    try:
        ensaio_rtc = rtc_ensaio.selecionar(estabelecimento, dados)
        if ensaio_rtc:
            rtc_ensaio.conferir_configuracao(configuracao)
        if provedor.exige_assinatura_local:
            xml_assinado, digest = _assinar(dados, configuracao, ensaio_rtc=ensaio_rtc)
        else:
            xml_assinado, digest = serializar(montar_nfe(dados)), None
        documento.digest_value = digest
        documento.qrcode_conteudo = _qrcode(dados, estabelecimento, digest=digest)
        if int(dados.modelo) == 65 and provedor.exige_assinatura_local:
            xml_assinado = incluir_suplemento(
                xml_assinado, qrcode=documento.qrcode_conteudo,
                url_chave=estabelecimento.url_consulta_chave,
            )
        if ensaio_rtc:
            rtc_ensaio.conferir(xml_assinado)
        # Persiste os bytes ANTES da chamada: timeout não pode apagar a evidência.
        _guardar_xml(documento, "enviado", xml_assinado)
        db.session.commit()
        resposta = provedor.autorizar(dados=dados, xml_assinado=xml_assinado)
        _guardar_xml(documento, "retorno", resposta.xml_retorno)
        if provedor.produz_documento_fiscal and resposta.sucesso:
            from .validacao_retorno import validar_autorizacao
            validar_autorizacao(resposta, dados)

    except ProvedorIndisponivel as exc:
        # Resultado desconhecido não é uma emissão offline tpEmis=9.
        documento.status = "pendente"
        documento.mensagem_status = str(exc)[:500]
        _registrar_evento(
            documento, tipo="erro", mensagem=str(exc), usuario_id=usuario_id
        )
        db.session.commit()
        return documento
    except (AssinaturaErro, XmlFiscalErro, ProvedorFiscalErro, EmissaoBloqueada) as exc:
        documento.status = "falhou"
        documento.mensagem_status = str(exc)[:500]
        _registrar_evento(
            documento, tipo="erro", mensagem=str(exc), usuario_id=usuario_id
        )
        db.session.commit()
        return documento

    documento.codigo_status = (resposta.codigo_status or "")[:4] or None
    documento.mensagem_status = (resposta.mensagem or "")[:500] or None
    documento.sem_valor_fiscal = bool(resposta.sem_valor_fiscal)
    _guardar_xml(documento, "enviado", resposta.xml_enviado or xml_assinado)
    _guardar_xml(documento, "retorno", resposta.xml_retorno)

    if resposta.sucesso:
        documento.status = "autorizado"
        documento.protocolo_autorizacao = (resposta.protocolo or "")[:20] or None
        documento.data_autorizacao = resposta.recebido_em or utcnow()
        _guardar_xml(documento, "autorizado", resposta.xml_autorizado)
    elif resposta.denegado:
        documento.status = "denegado"
    else:
        documento.status = "rejeitado"

    _registrar_evento(
        documento, tipo=tipo_evento, resposta=resposta, usuario_id=usuario_id
    )
    registrar(
        "documento_fiscal_emitido" if resposta.sucesso else "documento_fiscal_rejeitado",
        "documento_fiscal",
        documento.id,
        documento.empresa_id,
        {
            "chave": documento.chave,
            "numero": documento.numero,
            "serie": documento.serie,
            "status": documento.status,
            "codigo_status": documento.codigo_status,
            "provedor": documento.provedor,
            "sem_valor_fiscal": documento.sem_valor_fiscal,
        },
        usuario_id=usuario_id,
    )
    documento.versao += 1
    db.session.commit()

    if documento.status == "autorizado":
        _enfileirar_danfe(documento, estabelecimento, usuario_id=usuario_id)
    return documento


def _enfileirar_danfe(
    documento: DocumentoFiscal,
    estabelecimento: EstabelecimentoFiscal,
    *,
    usuario_id: uuid.UUID | None,
) -> None:
    """Manda o DANFE para a fila de impressão. Falha aqui não desfaz a emissão."""
    if documento.venda is None or documento.venda.turno is None:
        return
    try:
        from .danfe import montar_danfe
        from ..impressao import enfileirar_evento

        enfileirar_evento(
            empresa_id=documento.empresa_id,
            terminal_id=documento.venda.turno.terminal_id,
            evento="venda_finalizada",
            entidade="documento_fiscal",
            entidade_id=documento.id,
            payload=montar_danfe(documento, estabelecimento),
            usuario_id=usuario_id or documento.emitido_por_id,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()



def consultar_e_reconciliar(
    *, empresa_id: int, documento_id: uuid.UUID, usuario_id: uuid.UUID | None = None
) -> DocumentoFiscal:
    """Consulta a chave no autorizador e reconcilia um resultado desconhecido.

    Nunca retransmite a NF-e/NFC-e. A autorização só é gravada quando a SEFAZ
    devolve protocolo coerente com a chave/ambiente e o XML originalmente
    persistido antes da transmissão pode ser combinado ao ``protNFe``.
    """
    documento = db.session.scalar(
        db.select(DocumentoFiscal)
        .where(
            DocumentoFiscal.id == documento_id,
            DocumentoFiscal.empresa_id == empresa_id,
        )
        .with_for_update()
    )
    if documento is None:
        raise EmissaoErro("Documento fiscal não encontrado nesta empresa.")
    if documento.status in {"autorizado", "cancelado"}:
        return documento
    if documento.status not in {"pendente", "processando", "contingencia", "falhou"}:
        raise EmissaoErro(
            f"Documento com status '{documento.status_rotulo}' não pode ser reconciliado."
        )

    estabelecimento = db.session.get(EstabelecimentoFiscal, empresa_id)
    if estabelecimento is None:
        raise EmissaoErro("Configuração fiscal ausente.")
    config_documento = obter_configuracao_documento(
        empresa_id,
        modelo=int(documento.modelo),
        ambiente=int(documento.ambiente),
        serie=int(documento.serie),
    )
    if config_documento is None:
        raise EmissaoErro(
            "A configuração original deste modelo/ambiente/série não foi encontrada."
        )
    configuracao = configuracao_provedor(estabelecimento, config_documento)
    provedor = criar_provedor(config_documento.provedor, configuracao)
    if documento.provedor != config_documento.provedor:
        raise EmissaoErro(
            "A rota fiscal desta configuração mudou. Reconcilie usando a rota original do documento."
        )

    try:
        resposta = provedor.consultar_chave(
            chave=documento.chave, ambiente=int(documento.ambiente)
        )
    except (ProvedorFiscalErro, AssinaturaErro) as exc:
        _registrar_evento(
            documento, tipo="consulta", mensagem=str(exc), usuario_id=usuario_id
        )
        db.session.commit()
        raise EmissaoErro(str(exc)) from exc

    documento.codigo_status = (resposta.codigo_status or "")[:4] or None
    documento.mensagem_status = (resposta.mensagem or "")[:500] or None
    _guardar_xml(documento, "consulta", resposta.xml_retorno)

    if resposta.sucesso:
        arquivo_enviado = db.session.scalar(
            db.select(DocumentoFiscalXml).where(
                DocumentoFiscalXml.documento_id == documento.id,
                DocumentoFiscalXml.tipo == "enviado",
            )
        )
        if arquivo_enviado is None or not arquivo_enviado.arquivo_xml:
            documento.status = "pendente"
            documento.mensagem_status = (
                "A SEFAZ confirma autorização, mas o XML original enviado não está disponível. "
                "Não foi possível montar o nfeProc com segurança."
            )
            _registrar_evento(
                documento,
                tipo="consulta",
                resposta=resposta,
                mensagem=documento.mensagem_status,
                usuario_id=usuario_id,
            )
            db.session.commit()
            return documento

        try:
            from lxml import etree
            from types import SimpleNamespace
            from .provedores.sefaz_direto import montar_nfe_proc
            from .validacao_retorno import validar_autorizacao

            nfe = etree.fromstring(arquivo_enviado.arquivo_xml.encode("utf-8"))
            retorno = etree.fromstring(resposta.xml_retorno or b"")
            protocolo = retorno.find(f".//{{{NAMESPACE}}}protNFe")
            if protocolo is None:
                raise ProvedorIndisponivel(
                    "A consulta indicou autorização, mas não devolveu protNFe."
                )
            resposta.xml_autorizado = montar_nfe_proc(nfe, protocolo)
            validar_autorizacao(
                resposta,
                SimpleNamespace(chave=documento.chave, ambiente=int(documento.ambiente)),
            )
        except (ValueError, etree.XMLSyntaxError, ProvedorFiscalErro) as exc:
            documento.status = "pendente"
            documento.mensagem_status = f"Retorno de consulta inconsistente: {exc}"[:500]
            _registrar_evento(
                documento, tipo="consulta", mensagem=documento.mensagem_status, usuario_id=usuario_id
            )
            db.session.commit()
            return documento

        documento.status = "autorizado"
        documento.protocolo_autorizacao = (resposta.protocolo or "")[:20] or None
        documento.data_autorizacao = resposta.recebido_em or utcnow()
        documento.sem_valor_fiscal = False
        _guardar_xml(documento, "autorizado", resposta.xml_autorizado)
        _registrar_evento(
            documento, tipo="consulta", resposta=resposta, sucesso=True, usuario_id=usuario_id
        )
        registrar(
            "documento_fiscal_reconciliado",
            "documento_fiscal",
            documento.id,
            documento.empresa_id,
            {
                "chave": documento.chave,
                "numero": documento.numero,
                "serie": documento.serie,
                "codigo_status": documento.codigo_status,
                "provedor": documento.provedor,
            },
            usuario_id=usuario_id,
        )
        documento.versao += 1
        db.session.commit()
        _enfileirar_danfe(documento, estabelecimento, usuario_id=usuario_id)
        return documento

    # Consulta não conclusiva/rejeitada não autoriza reenvio automático.
    documento.status = "pendente"
    _registrar_evento(
        documento, tipo="consulta", resposta=resposta, usuario_id=usuario_id
    )
    documento.versao += 1
    db.session.commit()
    return documento

def reenviar(
    *, empresa_id: int, documento_id: uuid.UUID, usuario_id: uuid.UUID | None = None
) -> DocumentoFiscal:
    """Tenta de novo um documento que ficou pendente, em contingência ou falhou."""
    documento = db.session.scalar(
        db.select(DocumentoFiscal).where(
            DocumentoFiscal.id == documento_id, DocumentoFiscal.empresa_id == empresa_id
        )
    )
    if documento is None:
        raise EmissaoErro("Documento fiscal não encontrado nesta empresa.")
    if documento.status not in {"pendente", "contingencia", "falhou", "processando"}:
        raise EmissaoErro(
            f"Documento com status '{documento.status_rotulo}' não pode ser reenviado."
        )
    if documento.venda is None:
        raise EmissaoErro("Documento sem venda vinculada não pode ser reenviado aqui.")

    estabelecimento = db.session.get(EstabelecimentoFiscal, empresa_id)
    if estabelecimento is None:
        raise EmissaoErro("Configuração fiscal ausente.")
    config_documento = obter_configuracao_documento(
        empresa_id,
        modelo=int(documento.modelo),
        ambiente=int(documento.ambiente),
        serie=int(documento.serie),
    )
    if config_documento is None:
        raise EmissaoErro("Configuração fiscal original ausente.")
    configuracao = configuracao_provedor(estabelecimento, config_documento)
    provedor = criar_provedor(config_documento.provedor, configuracao)

    if documento.provedor != provedor.nome:
        raise EmissaoErro("Provedor mudou. Reconcilie o documento original antes de continuar.")
    if provedor.produz_documento_fiscal:
        raise EmissaoErro(
            "Reenvio automático indisponível: consulte a chave no autorizador e "
            "reconcilie o XML original. Recriar a nota pode alterar dados ou duplicar emissão."
        )

    # Reaproveita a MESMA numeração e a MESMA chave: reenviar não é emitir de novo.
    dados = montar_dados(
        documento.venda,
        estabelecimento,
        numero=int(documento.numero),
        serie=int(documento.serie),
        modelo=int(documento.modelo),
        codigo_numerico=int(documento.codigo_numerico),
        tipo_emissao=int(documento.tipo_emissao),
    )
    dados.emissao = documento.data_emissao
    dados.chave = documento.chave
    return _transmitir(
        documento,
        dados,
        estabelecimento,
        provedor,
        configuracao,
        usuario_id=usuario_id,
        tipo_evento="reenvio",
    )


def cancelar_documento(
    *,
    empresa_id: int,
    documento_id: uuid.UUID,
    justificativa: str,
    usuario_id: uuid.UUID | None = None,
) -> DocumentoFiscal:
    """Registra o evento de cancelamento junto à SEFAZ."""
    motivo = justificativa_valida(justificativa)
    documento = db.session.scalar(
        db.select(DocumentoFiscal)
        .where(DocumentoFiscal.id == documento_id, DocumentoFiscal.empresa_id == empresa_id)
        .with_for_update()
    )
    if documento is None:
        raise EmissaoErro("Documento fiscal não encontrado nesta empresa.")
    if documento.status == "cancelado":
        return documento
    if documento.status != "autorizado" or not documento.protocolo_autorizacao:
        raise EmissaoErro("Somente um documento autorizado pode ser cancelado.")

    estabelecimento = db.session.get(EstabelecimentoFiscal, empresa_id)
    if estabelecimento is None:
        raise EmissaoErro("Configuração fiscal ausente.")
    config_documento = obter_configuracao_documento(
        empresa_id,
        modelo=int(documento.modelo),
        ambiente=int(documento.ambiente),
        serie=int(documento.serie),
    )
    if config_documento is None:
        raise EmissaoErro("Configuração fiscal original ausente.")
    provedor = criar_provedor(
        config_documento.provedor,
        configuracao_provedor(estabelecimento, config_documento),
    )

    try:
        resposta = provedor.cancelar(
            chave=documento.chave,
            protocolo=documento.protocolo_autorizacao,
            justificativa=motivo,
            cnpj=documento.cnpj_emitente,
            ambiente=int(documento.ambiente),
        )
    except (ProvedorFiscalErro, AssinaturaErro) as exc:
        _registrar_evento(
            documento, tipo="erro", mensagem=str(exc), usuario_id=usuario_id
        )
        db.session.commit()
        raise EmissaoErro(str(exc)) from exc

    documento.codigo_status = (resposta.codigo_status or "")[:4] or None
    documento.mensagem_status = (resposta.mensagem or "")[:500] or None
    _guardar_xml(documento, "cancelamento", resposta.xml_retorno)
    if resposta.sucesso:
        documento.status = "cancelado"
        documento.protocolo_cancelamento = (resposta.protocolo or "")[:20] or None
        documento.motivo_cancelamento = motivo
        documento.data_cancelamento = resposta.recebido_em or utcnow()
    _registrar_evento(
        documento, tipo="cancelamento", resposta=resposta, usuario_id=usuario_id
    )
    registrar(
        "documento_fiscal_cancelado" if resposta.sucesso else "documento_fiscal_cancelamento_recusado",
        "documento_fiscal",
        documento.id,
        empresa_id,
        {
            "chave": documento.chave,
            "codigo_status": documento.codigo_status,
            "mensagem": documento.mensagem_status,
            "justificativa": motivo,
        },
        usuario_id=usuario_id,
    )
    documento.versao += 1
    db.session.commit()
    if not resposta.sucesso:
        raise EmissaoErro(
            f"A SEFAZ recusou o cancelamento ({documento.codigo_status}): "
            f"{documento.mensagem_status}"
        )
    return documento


def inutilizar_faixa(
    *,
    empresa_id: int,
    numero_inicial: int,
    numero_final: int,
    justificativa: str,
    usuario_id: uuid.UUID | None = None,
) -> RespostaFiscal:
    """Inutiliza uma faixa de numeração e atualiza as pendências."""
    motivo = justificativa_valida(justificativa)
    estabelecimento = db.session.get(EstabelecimentoFiscal, empresa_id)
    if estabelecimento is None:
        raise EmissaoErro("Configuração fiscal ausente.")
    config_documento = obter_configuracao_documento(
        empresa_id,
        modelo=int(estabelecimento.modelo),
        ambiente=int(estabelecimento.ambiente),
        serie=int(estabelecimento.serie),
    )
    if config_documento is None:
        raise EmissaoErro("Configuração do modelo/ambiente/série atual ausente.")
    if numero_final < numero_inicial:
        raise EmissaoErro("A faixa de inutilização está invertida.")
    if numero_final - numero_inicial > 999:
        raise EmissaoErro("Inutilize no máximo mil números por vez.")

    emitidos = db.session.scalars(
        db.select(DocumentoFiscal.numero).where(
            DocumentoFiscal.empresa_id == empresa_id,
            DocumentoFiscal.serie == config_documento.serie,
            DocumentoFiscal.modelo == config_documento.modelo,
            DocumentoFiscal.ambiente == config_documento.ambiente,
            DocumentoFiscal.numero.between(numero_inicial, numero_final),
            DocumentoFiscal.protocolo_autorizacao.is_not(None),
        )
    ).all()
    if emitidos:
        raise EmissaoErro(
            "A faixa contém número já autorizado pela SEFAZ "
            f"({min(emitidos)}). Inutilização recusada."
        )

    provedor = criar_provedor(
        config_documento.provedor,
        configuracao_provedor(estabelecimento, config_documento),
    )
    resposta = provedor.inutilizar(
        cnpj=estabelecimento.cnpj,
        serie=int(config_documento.serie),
        numero_inicial=int(numero_inicial),
        numero_final=int(numero_final),
        justificativa=motivo,
        ambiente=int(config_documento.ambiente),
        modelo=int(config_documento.modelo),
        codigo_uf=codigo_da_uf(estabelecimento.uf),
        ano=_agora(estabelecimento).year,
    )

    for numero in range(int(numero_inicial), int(numero_final) + 1):
        registro = db.session.scalar(
            db.select(InutilizacaoFiscal).where(
                InutilizacaoFiscal.cnpj == estabelecimento.cnpj,
                InutilizacaoFiscal.modelo == config_documento.modelo,
                InutilizacaoFiscal.ambiente == config_documento.ambiente,
                InutilizacaoFiscal.serie == config_documento.serie,
                InutilizacaoFiscal.numero == numero,
            )
        )
        if registro is None:
            registro = InutilizacaoFiscal(
                empresa_id=empresa_id,
                cnpj=estabelecimento.cnpj,
                modelo=int(config_documento.modelo),
                ambiente=int(config_documento.ambiente),
                serie=int(config_documento.serie),
                numero=numero,
                origem="manual",
            )
            db.session.add(registro)
        registro.status = "autorizada" if resposta.sucesso else "rejeitada"
        registro.justificativa = motivo
        registro.protocolo = (resposta.protocolo or "")[:20] or None
        registro.codigo_status = (resposta.codigo_status or "")[:4] or None
        registro.mensagem = (resposta.mensagem or "")[:500] or None
        registro.resolvido_em = utcnow() if resposta.sucesso else None
        registro.resolvido_por_id = usuario_id
        if resposta.xml_retorno:
            registro.arquivo_xml = resposta.xml_retorno.decode("utf-8", errors="replace")

    registrar(
        "inutilizacao_fiscal" if resposta.sucesso else "inutilizacao_fiscal_recusada",
        "inutilizacao_fiscal",
        f"{estabelecimento.serie}:{numero_inicial}-{numero_final}",
        empresa_id,
        {
            "serie": config_documento.serie,
            "ambiente": config_documento.ambiente,
            "numero_inicial": numero_inicial,
            "numero_final": numero_final,
            "codigo_status": resposta.codigo_status,
            "mensagem": resposta.mensagem,
            "justificativa": motivo,
        },
        usuario_id=usuario_id,
    )
    db.session.commit()
    return resposta


def emitir_com_seguranca(
    *, empresa_id: int, venda_id: uuid.UUID, usuario_id: uuid.UUID | None = None
) -> tuple[DocumentoFiscal | None, str | None]:
    """Emite sem deixar erro algum escapar para o caixa.

    Devolve ``(documento, aviso)``. O aviso é o texto a mostrar ao operador
    quando algo não saiu como esperado — a venda em si continua válida.
    """
    try:
        documento = emitir_venda(
            empresa_id=empresa_id, venda_id=venda_id, usuario_id=usuario_id
        )
    except EmissaoBloqueada as exc:
        db.session.rollback()
        return None, f"Venda registrada, mas sem documento fiscal: {exc}"
    except (EmissaoErro, SequenciaErro, ProvedorFiscalErro, SegredoErro) as exc:
        db.session.rollback()
        return None, f"Venda registrada, mas a emissão fiscal falhou: {exc}"
    except Exception as exc:  # nada aqui pode derrubar o caixa
        db.session.rollback()
        current_app.logger.exception("Falha inesperada na emissão fiscal")
        return None, f"Venda registrada, mas a emissão fiscal falhou: {exc}"

    if documento is None:
        return None, None
    if documento.status == "autorizado" and documento.sem_valor_fiscal:
        return documento, (
            "Documento gerado em modo de ensaio — NÃO tem valor fiscal."
        )
    if documento.status == "autorizado":
        return documento, None
    return documento, (
        f"Venda registrada. Documento fiscal {documento.status_rotulo.lower()}: "
        f"{documento.mensagem_status or 'sem detalhe da SEFAZ'}."
    )
