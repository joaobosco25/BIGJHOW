from __future__ import annotations

import pathlib
import re

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import text

from .extensions import db
from .models import (
    CodigoProduto,
    Empresa,
    Impressora,
    MovimentoCaixa,
    Produto,
    RegraImpressao,
    TerminalCaixa,
    TrabalhoImpressao,
    TurnoCaixa,
    Usuario,
    Venda,
    VendaItem,
    VendaPagamento,
)
from .services.auditoria import registrar
from .services.carga_inicial import garantir_empresas, importar
from .services.credenciais import (
    normalizar_usuario,
    validar_senha_forte,
    validar_usuario_novo,
)


def _revisao_atual() -> str | None:
    return db.session.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one_or_none()


def _historico_de_migracoes() -> dict[str, str | None]:
    """Lê o grafo de revisões direto dos arquivos de migração.

    Poderia usar a API do Alembic, mas ela é interna e muda entre versões — e
    uma falha ali faria estes comandos recusarem um banco que está correto.
    Os arquivos vêm junto com a aplicação e bastam.
    """
    pasta = pathlib.Path(current_app.root_path).parent / "migrations" / "versions"
    historico: dict[str, str | None] = {}
    if not pasta.is_dir():
        return historico
    padrao_revisao = re.compile(r"^revision\s*=\s*[\"']([^\"']+)", re.M)
    padrao_anterior = re.compile(r"^down_revision\s*=\s*(?:[\"']([^\"']+)[\"']|None)", re.M)
    for arquivo in pasta.glob("*.py"):
        try:
            conteudo = arquivo.read_text(encoding="utf-8")
        except OSError:
            continue
        atual = padrao_revisao.search(conteudo)
        if not atual:
            continue
        anterior = padrao_anterior.search(conteudo)
        historico[atual.group(1)] = anterior.group(1) if anterior else None
    return historico


def _migracao_aplicada(exigida: str) -> bool:
    """Diz se a migração exigida já foi aplicada — ela mesma ou uma descendente.

    Comparar por igualdade exata quebraria estes comandos a cada migração nova:
    um banco atualizado até a última revisão deixaria de ser reconhecido como
    tendo passado pela revisão exigida. Aqui a checagem percorre a ancestralidade.
    """
    atual = _revisao_atual()
    if atual is None:
        return False
    historico = _historico_de_migracoes()
    visitadas: set[str] = set()
    while atual is not None and atual not in visitadas:
        if atual == exigida:
            return True
        visitadas.add(atual)
        atual = historico.get(atual)
    return False


def register_commands(app) -> None:
    @app.cli.command("popular-base")
    @with_appcontext
    def popular_base():
        """Importa empresas e produtos da carga inicial de forma idempotente."""
        try:
            result = importar()
        except Exception:
            db.session.rollback()
            raise
        click.echo(
            f"Carga concluída: {result['importados']} importados; "
            f"{result['ignorados']} já existentes."
        )

    @app.cli.command("criar-admin")
    @click.option("--nome", prompt="Nome", help="Nome do proprietário.")
    @click.option("--usuario", prompt="Usuário", help="Nome de usuário do proprietário.")
    @click.password_option(confirmation_prompt=True, prompt="Senha")
    @with_appcontext
    def criar_admin(nome: str, usuario: str, password: str):
        """Cria o primeiro proprietário sem expor a senha no terminal."""
        nome = " ".join(nome.split())
        usuario = normalizar_usuario(usuario)
        if len(nome) < 3 or len(nome) > 120:
            raise click.ClickException("O nome precisa ter de 3 a 120 caracteres.")
        erros_usuario = validar_usuario_novo(usuario)
        if erros_usuario:
            raise click.ClickException(erros_usuario[0])
        erros_senha = validar_senha_forte(password, usuario=usuario, nome=nome)
        if erros_senha:
            raise click.ClickException(" ".join(erros_senha))
        if db.session.scalar(db.select(Usuario).where(Usuario.usuario == usuario)):
            raise click.ClickException("Já existe uma conta com esse usuário.")
        garantir_empresas()
        user = Usuario(
            nome=nome,
            usuario=usuario,
            papel="proprietario",
            ativo=True,
            troca_senha_obrigatoria=False,
        )
        user.set_senha(password)
        db.session.add(user)
        db.session.flush()
        registrar(
            "usuario_criado",
            "usuario",
            user.id,
            usuario_id=user.id,
            detalhes={"usuario": user.usuario, "papel": user.papel},
        )
        db.session.commit()
        click.echo("Proprietário criado com sucesso.")

    @app.cli.command("verificar-base4")
    @with_appcontext
    def verificar_base4():
        """Confere a migration e invariantes essenciais de códigos e vendas."""
        revisao = _revisao_atual()
        if not _migracao_aplicada("f8a72c10d4e6"):
            raise click.ClickException(
                f"Migration inesperada: {revisao or 'nenhuma'}."
            )

        produtos = db.session.scalar(db.select(db.func.count(Produto.id))) or 0
        codigos = db.session.scalar(
            db.select(db.func.count(CodigoProduto.id)).where(
                CodigoProduto.ativo.is_(True)
            )
        ) or 0
        chaves_duplicadas = db.session.scalar(
            db.select(db.func.count()).select_from(
                db.select(CodigoProduto.empresa_id, CodigoProduto.chave_busca)
                .group_by(CodigoProduto.empresa_id, CodigoProduto.chave_busca)
                .having(db.func.count(CodigoProduto.id) > 1)
                .subquery()
            )
        ) or 0
        empresas_inconsistentes = db.session.scalar(
            db.select(db.func.count(CodigoProduto.id))
            .join(Produto, Produto.id == CodigoProduto.produto_id)
            .where(CodigoProduto.empresa_id != Produto.empresa_id)
        ) or 0
        abertos_duplicados = db.session.scalar(
            db.select(db.func.count()).select_from(
                db.select(Venda.empresa_id, Venda.operador_id)
                .where(Venda.status == "aberta")
                .group_by(Venda.empresa_id, Venda.operador_id)
                .having(db.func.count(Venda.id) > 1)
                .subquery()
            )
        ) or 0
        finalizados_sem_saida = db.session.scalar(
            db.select(db.func.count(VendaItem.id))
            .join(Venda, Venda.id == VendaItem.venda_id)
            .where(
                Venda.status.in_(("finalizada", "cancelada")),
                VendaItem.movimento_saida_id.is_(None),
            )
        ) or 0
        cancelados_sem_estorno = db.session.scalar(
            db.select(db.func.count(VendaItem.id))
            .join(Venda, Venda.id == VendaItem.venda_id)
            .where(
                Venda.status == "cancelada",
                VendaItem.movimento_estorno_id.is_(None),
            )
        ) or 0
        encerradas_sem_itens = db.session.scalar(
            db.select(db.func.count(Venda.id)).where(
                Venda.status.in_(("finalizada", "cancelada")),
                ~db.select(VendaItem.id)
                .where(VendaItem.venda_id == Venda.id)
                .exists(),
            )
        ) or 0
        pagamentos_por_venda = (
            db.select(
                VendaPagamento.venda_id.label("venda_id"),
                db.func.sum(VendaPagamento.valor).label("total_pago"),
            )
            .group_by(VendaPagamento.venda_id)
            .subquery()
        )
        encerradas_com_pagamento_inconsistente = db.session.scalar(
            db.select(db.func.count(Venda.id))
            .outerjoin(
                pagamentos_por_venda,
                pagamentos_por_venda.c.venda_id == Venda.id,
            )
            .where(
                Venda.status.in_(("finalizada", "cancelada")),
                db.or_(
                    pagamentos_por_venda.c.total_pago.is_(None),
                    pagamentos_por_venda.c.total_pago != Venda.total,
                ),
            )
        ) or 0

        problemas = {
            "chaves duplicadas": chaves_duplicadas,
            "códigos em empresa divergente": empresas_inconsistentes,
            "caixas abertos duplicados": abertos_duplicados,
            "itens finalizados sem saída": finalizados_sem_saida,
            "itens cancelados sem estorno": cancelados_sem_estorno,
            "vendas encerradas sem itens": encerradas_sem_itens,
            "pagamentos inconsistentes": encerradas_com_pagamento_inconsistente,
        }
        encontrados = {nome: valor for nome, valor in problemas.items() if valor}
        if encontrados:
            resumo = "; ".join(f"{nome}: {valor}" for nome, valor in encontrados.items())
            raise click.ClickException(f"Inconsistência detectada — {resumo}.")
        click.echo(
            f"[OK] Migration {revisao}; {produtos} produto(s); "
            f"{codigos} código(s) ativo(s); invariantes da Base 4 válidos"
        )

    @app.cli.command("preparar-base5")
    @with_appcontext
    def preparar_base5():
        """Cria terminal, impressora e regras simuladas padrão de forma idempotente."""
        if not _migracao_aplicada("a31d7e5c9b42"):
            raise click.ClickException(
                "Aplique a migration a31d7e5c9b42 antes de preparar a Base 5."
            )
        proprietario = db.session.scalar(
            db.select(Usuario)
            .where(Usuario.papel == "proprietario", Usuario.ativo.is_(True))
            .order_by(Usuario.criado_em)
        )
        criados = {"terminais": 0, "impressoras": 0, "regras": 0}
        empresas = db.session.scalars(
            db.select(Empresa).where(Empresa.ativa.is_(True)).order_by(Empresa.id)
        ).all()
        for empresa in empresas:
            terminal = db.session.scalar(
                db.select(TerminalCaixa).where(
                    TerminalCaixa.empresa_id == empresa.id,
                    TerminalCaixa.codigo == "CAIXA-01",
                )
            )
            if terminal is None:
                terminal = TerminalCaixa(
                    empresa_id=empresa.id,
                    codigo="CAIXA-01",
                    nome="Caixa 01",
                    criado_por_id=proprietario.id if proprietario else None,
                )
                db.session.add(terminal)
                db.session.flush()
                criados["terminais"] += 1
                registrar(
                    "terminal_caixa_criado",
                    "terminal_caixa",
                    terminal.id,
                    empresa.id,
                    {"codigo": terminal.codigo, "origem": "preparar-base5"},
                    usuario_id=proprietario.id if proprietario else None,
                )
            impressora = db.session.scalar(
                db.select(Impressora).where(
                    Impressora.empresa_id == empresa.id,
                    Impressora.codigo == "SIM-CUPOM-01",
                )
            )
            if impressora is None:
                impressora = Impressora(
                    empresa_id=empresa.id,
                    codigo="SIM-CUPOM-01",
                    nome="Impressora simulada do caixa",
                    setor="Caixa",
                    tipo="cupom",
                    modo="simulada",
                    criado_por_id=proprietario.id if proprietario else None,
                )
                db.session.add(impressora)
                db.session.flush()
                criados["impressoras"] += 1
                registrar(
                    "impressora_criada",
                    "impressora",
                    impressora.id,
                    empresa.id,
                    {"codigo": impressora.codigo, "modo": "simulada"},
                    usuario_id=proprietario.id if proprietario else None,
                )
            for evento in (
                "venda_finalizada",
                "venda_cancelada",
                "turno_fechado",
                "movimento_caixa_aprovado",
            ):
                regra = db.session.scalar(
                    db.select(RegraImpressao.id).where(
                        RegraImpressao.terminal_id == terminal.id,
                        RegraImpressao.impressora_id == impressora.id,
                        RegraImpressao.evento == evento,
                    )
                )
                if regra is None:
                    nova_regra = RegraImpressao(
                            empresa_id=empresa.id,
                            terminal_id=terminal.id,
                            impressora_id=impressora.id,
                            evento=evento,
                            copias=1,
                            criado_por_id=proprietario.id if proprietario else None,
                        )
                    db.session.add(nova_regra)
                    db.session.flush()
                    registrar(
                        "regra_impressao_criada",
                        "regra_impressao",
                        nova_regra.id,
                        empresa.id,
                        {"evento": evento, "origem": "preparar-base5"},
                        usuario_id=proprietario.id if proprietario else None,
                    )
                    criados["regras"] += 1
        db.session.commit()
        click.echo(
            "[OK] Base 5 preparada: "
            f"{criados['terminais']} terminal(is), "
            f"{criados['impressoras']} impressora(s) e "
            f"{criados['regras']} regra(s) criada(s)"
        )

    @app.cli.command("verificar-base5")
    @with_appcontext
    def verificar_base5():
        """Confere a migration e as invariantes de turnos e impressão."""
        revisao = _revisao_atual()
        if not _migracao_aplicada("a31d7e5c9b42"):
            raise click.ClickException(
                f"Migration inesperada: {revisao or 'nenhuma'}."
            )

        terminais = db.session.scalar(db.select(db.func.count(TerminalCaixa.id))) or 0
        impressoras = db.session.scalar(db.select(db.func.count(Impressora.id))) or 0
        regras = db.session.scalar(db.select(db.func.count(RegraImpressao.id))) or 0
        trabalhos = db.session.scalar(
            db.select(db.func.count(TrabalhoImpressao.id))
        ) or 0

        turnos_empresa_divergente = db.session.scalar(
            db.select(db.func.count(TurnoCaixa.id))
            .join(TerminalCaixa, TerminalCaixa.id == TurnoCaixa.terminal_id)
            .where(TurnoCaixa.empresa_id != TerminalCaixa.empresa_id)
        ) or 0
        vendas_empresa_divergente = db.session.scalar(
            db.select(db.func.count(Venda.id))
            .join(TurnoCaixa, TurnoCaixa.id == Venda.turno_id)
            .where(Venda.empresa_id != TurnoCaixa.empresa_id)
        ) or 0
        movimentos_empresa_divergente = db.session.scalar(
            db.select(db.func.count(MovimentoCaixa.id))
            .join(TurnoCaixa, TurnoCaixa.id == MovimentoCaixa.turno_id)
            .where(MovimentoCaixa.empresa_id != TurnoCaixa.empresa_id)
        ) or 0
        regras_empresa_divergente = db.session.scalar(
            db.select(db.func.count(RegraImpressao.id))
            .join(TerminalCaixa, TerminalCaixa.id == RegraImpressao.terminal_id)
            .join(Impressora, Impressora.id == RegraImpressao.impressora_id)
            .where(
                db.or_(
                    RegraImpressao.empresa_id != TerminalCaixa.empresa_id,
                    RegraImpressao.empresa_id != Impressora.empresa_id,
                )
            )
        ) or 0
        trabalhos_empresa_divergente = db.session.scalar(
            db.select(db.func.count(TrabalhoImpressao.id))
            .join(TerminalCaixa, TerminalCaixa.id == TrabalhoImpressao.terminal_id)
            .join(Impressora, Impressora.id == TrabalhoImpressao.impressora_id)
            .where(
                db.or_(
                    TrabalhoImpressao.empresa_id != TerminalCaixa.empresa_id,
                    TrabalhoImpressao.empresa_id != Impressora.empresa_id,
                )
            )
        ) or 0
        trabalhos_regra_divergente = db.session.scalar(
            db.select(db.func.count(TrabalhoImpressao.id))
            .join(RegraImpressao, RegraImpressao.id == TrabalhoImpressao.regra_id)
            .where(
                db.or_(
                    TrabalhoImpressao.empresa_id != RegraImpressao.empresa_id,
                    TrabalhoImpressao.terminal_id != RegraImpressao.terminal_id,
                    TrabalhoImpressao.impressora_id != RegraImpressao.impressora_id,
                )
            )
        ) or 0
        vendas_operador_divergente = db.session.scalar(
            db.select(db.func.count(Venda.id))
            .join(TurnoCaixa, TurnoCaixa.id == Venda.turno_id)
            .where(Venda.operador_id != TurnoCaixa.operador_id)
        ) or 0
        turnos_terminal_duplicado = db.session.scalar(
            db.select(db.func.count()).select_from(
                db.select(TurnoCaixa.terminal_id)
                .where(TurnoCaixa.status == "aberto")
                .group_by(TurnoCaixa.terminal_id)
                .having(db.func.count(TurnoCaixa.id) > 1)
                .subquery()
            )
        ) or 0
        turnos_operador_duplicado = db.session.scalar(
            db.select(db.func.count()).select_from(
                db.select(TurnoCaixa.operador_id)
                .where(TurnoCaixa.status == "aberto")
                .group_by(TurnoCaixa.operador_id)
                .having(db.func.count(TurnoCaixa.id) > 1)
                .subquery()
            )
        ) or 0
        trabalhos_estado_incompleto = db.session.scalar(
            db.select(db.func.count(TrabalhoImpressao.id)).where(
                db.or_(
                    db.and_(
                        TrabalhoImpressao.status == "impresso",
                        TrabalhoImpressao.impresso_em.is_(None),
                    ),
                    db.and_(
                        TrabalhoImpressao.status == "falhou",
                        db.or_(
                            TrabalhoImpressao.falhou_em.is_(None),
                            TrabalhoImpressao.erro_codigo.is_(None),
                        ),
                    ),
                )
            )
        ) or 0
        chaves_duplicadas = db.session.scalar(
            db.select(db.func.count()).select_from(
                db.select(TrabalhoImpressao.chave_idempotencia)
                .group_by(TrabalhoImpressao.chave_idempotencia)
                .having(db.func.count(TrabalhoImpressao.id) > 1)
                .subquery()
            )
        ) or 0
        terminais_sem_padrao = db.session.scalar(
            db.select(db.func.count(Empresa.id)).where(
                Empresa.ativa.is_(True),
                ~db.select(TerminalCaixa.id)
                .where(
                    TerminalCaixa.empresa_id == Empresa.id,
                    TerminalCaixa.codigo == "CAIXA-01",
                )
                .exists(),
            )
        ) or 0
        impressoras_sem_padrao = db.session.scalar(
            db.select(db.func.count(Empresa.id)).where(
                Empresa.ativa.is_(True),
                ~db.select(Impressora.id)
                .where(
                    Impressora.empresa_id == Empresa.id,
                    Impressora.codigo == "SIM-CUPOM-01",
                )
                .exists(),
            )
        ) or 0
        regras_padrao_ausentes = 0
        for empresa_id in db.session.scalars(
            db.select(Empresa.id).where(Empresa.ativa.is_(True))
        ).all():
            terminal_id = db.session.scalar(
                db.select(TerminalCaixa.id).where(
                    TerminalCaixa.empresa_id == empresa_id,
                    TerminalCaixa.codigo == "CAIXA-01",
                )
            )
            impressora_id = db.session.scalar(
                db.select(Impressora.id).where(
                    Impressora.empresa_id == empresa_id,
                    Impressora.codigo == "SIM-CUPOM-01",
                )
            )
            for evento in (
                "venda_finalizada",
                "venda_cancelada",
                "turno_fechado",
                "movimento_caixa_aprovado",
            ):
                if terminal_id is None or impressora_id is None or db.session.scalar(
                    db.select(RegraImpressao.id).where(
                        RegraImpressao.terminal_id == terminal_id,
                        RegraImpressao.impressora_id == impressora_id,
                        RegraImpressao.evento == evento,
                    )
                ) is None:
                    regras_padrao_ausentes += 1

        problemas = {
            "turnos em empresa divergente": turnos_empresa_divergente,
            "vendas em turno de outra empresa": vendas_empresa_divergente,
            "movimentos em turno de outra empresa": movimentos_empresa_divergente,
            "regras em empresa divergente": regras_empresa_divergente,
            "trabalhos em empresa divergente": trabalhos_empresa_divergente,
            "trabalhos com regra divergente": trabalhos_regra_divergente,
            "vendas com operador divergente do turno": vendas_operador_divergente,
            "terminais com mais de um turno aberto": turnos_terminal_duplicado,
            "operadores com mais de um turno aberto": turnos_operador_duplicado,
            "trabalhos com estado incompleto": trabalhos_estado_incompleto,
            "chaves de impressão duplicadas": chaves_duplicadas,
            "empresas sem terminal padrão": terminais_sem_padrao,
            "empresas sem impressora padrão": impressoras_sem_padrao,
            "regras padrão ausentes": regras_padrao_ausentes,
        }
        encontrados = {nome: valor for nome, valor in problemas.items() if valor}
        if encontrados:
            resumo = "; ".join(f"{nome}: {valor}" for nome, valor in encontrados.items())
            raise click.ClickException(f"Inconsistência detectada — {resumo}.")
        click.echo(
            f"[OK] Migration {revisao}; {terminais} terminal(is); "
            f"{impressoras} impressora(s); {regras} regra(s); "
            f"{trabalhos} trabalho(s); invariantes da Base 5 válidos"
        )

    # ----------------------------------------------------------------------
    # Módulo fiscal
    # ----------------------------------------------------------------------

    @app.cli.command("fiscal-gerar-chave")
    def fiscal_gerar_chave():
        """Gera um valor para FISCAL_SECRET_KEY."""
        import secrets as _secrets

        click.echo(_secrets.token_urlsafe(48))
        click.echo(
            "Coloque em FISCAL_SECRET_KEY no .env e guarde uma cópia fora do "
            "servidor: sem esta chave as credenciais fiscais não abrem mais."
        )

    @app.cli.command("fiscal-status")
    @click.argument("empresa_id", type=int)
    @with_appcontext
    def fiscal_status(empresa_id: int):
        """Mostra se a empresa está apta a emitir e o que falta."""
        from .services.fiscal.cadastro import conferir_prontidao
        from .services.fiscal.sequencia import resumo_numeracao

        prontidao = conferir_prontidao(empresa_id)
        resumo = resumo_numeracao(empresa_id)
        if not prontidao["configurado"]:
            raise click.ClickException("Esta empresa ainda não tem configuração fiscal.")
        click.echo(
            f"Emissão: {'ativa' if prontidao['pode_emitir'] else 'bloqueada'} | "
            f"valor fiscal: {'sim' if prontidao['com_valor_fiscal'] else 'não'} | "
            f"série {resumo['serie']} modelo {resumo['modelo']} | "
            f"próximo número {resumo['proximo_numero']}"
        )
        click.echo(
            f"Autorizados: {resumo['autorizados']} | pendentes: {resumo['pendentes']} | "
            f"rejeitados: {resumo['rejeitados']} | "
            f"inutilizações pendentes: {resumo['inutilizacoes_pendentes']}"
        )
        for pendencia in prontidao["pendencias"]:
            click.echo(f"  - {pendencia}")
        if prontidao["pendencias"]:
            raise SystemExit(1)

    @app.cli.command("fiscal-varrer-numeracao")
    @click.argument("empresa_id", type=int)
    @click.option("--registrar", is_flag=True, help="Grava as lacunas como pendências.")
    @with_appcontext
    def fiscal_varrer_numeracao(empresa_id: int, registrar: bool):
        """Procura números de nota sem documento autorizado."""
        from .services.fiscal.sequencia import (
            agrupar_faixas,
            detectar_lacunas,
            registrar_lacunas,
        )

        lacunas = detectar_lacunas(empresa_id)
        if not lacunas:
            click.echo("[OK] Nenhuma lacuna de numeração encontrada.")
            return
        faixas = agrupar_faixas([item["numero"] for item in lacunas])
        for inicio, fim in faixas:
            if inicio == fim:
                click.echo(f"  nº {inicio}")
            else:
                click.echo(f"  nº {inicio} a {fim} ({fim - inicio + 1} números)")
        if registrar:
            criados = registrar_lacunas(empresa_id)
            db.session.commit()
            click.echo(f"{len(criados)} pendência(s) de inutilização registrada(s).")
        else:
            click.echo(
                f"{len(lacunas)} número(s) sem documento. "
                "Use --registrar para gravar como pendência."
            )

    @app.cli.command("fiscal-importar")
    @click.argument("empresa_id", type=int)
    @click.argument("pasta", type=click.Path(exists=True, file_okay=False))
    @with_appcontext
    def fiscal_importar(empresa_id: int, pasta: str):
        """Importa o acervo de XML do servidor antigo."""
        from .services.fiscal.importador import importar_pasta

        resultado = importar_pasta(pasta, empresa_id=empresa_id)
        db.session.commit()
        click.echo(
            f"Importados: {resultado.importados} | cancelamentos: {resultado.cancelamentos} | "
            f"inutilizações: {resultado.inutilizacoes} | ignorados: {resultado.ignorados}"
        )
        for erro in resultado.erros[:20]:
            click.echo(f"  ! {erro}")
        if resultado.erros:
            click.echo(f"  ({len(resultado.erros)} arquivo(s) com problema)")

    @app.cli.command("fiscal-reenviar-pendentes")
    @click.argument("empresa_id", type=int)
    @click.option("--limite", type=int, default=50, show_default=True)
    @with_appcontext
    def fiscal_reenviar_pendentes(empresa_id: int, limite: int):
        """Reenvia documentos que ficaram pendentes ou em contingência.

        Feito para rodar em tarefa agendada: quando a SEFAZ volta, as notas
        represadas sobem sozinhas.
        """
        from .models import DocumentoFiscal
        from .services.fiscal.emissao import EmissaoErro, reenviar

        pendentes = db.session.scalars(
            db.select(DocumentoFiscal)
            .where(
                DocumentoFiscal.empresa_id == empresa_id,
                DocumentoFiscal.status.in_(("pendente", "contingencia", "falhou")),
            )
            .order_by(DocumentoFiscal.numero)
            .limit(max(1, limite))
        ).all()
        if not pendentes:
            click.echo("[OK] Nenhum documento pendente.")
            return
        autorizados = 0
        for documento in pendentes:
            try:
                resultado = reenviar(
                    empresa_id=empresa_id, documento_id=documento.id
                )
            except (EmissaoErro, ValueError) as exc:
                db.session.rollback()
                click.echo(f"  ! nº {documento.numero}: {exc}")
                continue
            if resultado.status == "autorizado":
                autorizados += 1
            click.echo(f"  nº {documento.numero}: {resultado.status_rotulo}")
        click.echo(f"{autorizados} de {len(pendentes)} documento(s) autorizado(s).")
