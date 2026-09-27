"""Numeração fiscal: reserva do próximo número e detecção de lacunas.

A detecção de lacunas é a tradução, para Python e PostgreSQL, do procedimento
``ValidarSequenciaNotaFiscalTask`` do sistema antigo — aquele bloco T-SQL com
cursor, tabela temporária ``#inutilizacao_amostra`` e CTE recursiva que
alimentava a tabela ``Inutilizacao``.

A regra do original, preservada aqui:

1. por CNPJ e série, considerar só documentos que chegaram a ter protocolo
   de autorização;
2. olhar apenas as últimas ``quantidade_analisada`` notas (não varrer o
   histórico inteiro a cada execução);
3. dentro dessa janela, todo número entre o menor e o maior que não tenha
   documento autorizado é candidato a inutilização;
4. não duplicar candidatos já registrados.

Por que isso importa: número de nota que a SEFAZ nunca autorizou e que também
nunca foi inutilizado vira pendência fiscal. O sistema antigo cuidava disso e
o novo precisa continuar cuidando.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import func

from ...extensions import db
from ...models import (
    ConfiguracaoDocumentoFiscal,
    DocumentoFiscal,
    EstabelecimentoFiscal,
    InutilizacaoFiscal,
)


QUANTIDADE_ANALISADA_PADRAO = 1000
LIMITE_CODIGO_NUMERICO = 99_999_999


class SequenciaErro(RuntimeError):
    pass


@dataclass(slots=True)
class NumeroReservado:
    numero: int
    serie: int
    modelo: int
    codigo_numerico: int


def gerar_codigo_numerico(numero: int) -> int:
    """cNF aleatório. A norma proíbe que ele seja igual ao número da nota."""
    for _ in range(20):
        candidato = secrets.randbelow(LIMITE_CODIGO_NUMERICO + 1)
        if candidato != int(numero):
            return candidato
    return (int(numero) + 1) % (LIMITE_CODIGO_NUMERICO + 1)


def _configuracao_atual(
    estabelecimento: EstabelecimentoFiscal, *, travar: bool = False
) -> ConfiguracaoDocumentoFiscal:
    consulta = db.select(ConfiguracaoDocumentoFiscal).where(
        ConfiguracaoDocumentoFiscal.empresa_id == estabelecimento.empresa_id,
        ConfiguracaoDocumentoFiscal.modelo == int(estabelecimento.modelo),
        ConfiguracaoDocumentoFiscal.ambiente == int(estabelecimento.ambiente),
        ConfiguracaoDocumentoFiscal.serie == int(estabelecimento.serie),
    )
    if travar:
        consulta = consulta.with_for_update()
    config = db.session.scalar(consulta)
    if config is not None:
        return config

    # Compatibilidade para bancos criados por db.create_all nos testes ou para
    # a primeira execução logo após a migration. O estado legado é copiado,
    # nunca descartado.
    config = ConfiguracaoDocumentoFiscal(
        empresa_id=estabelecimento.empresa_id,
        modelo=int(estabelecimento.modelo),
        ambiente=int(estabelecimento.ambiente),
        serie=int(estabelecimento.serie),
        proximo_numero=int(estabelecimento.proximo_numero),
        natureza_operacao=estabelecimento.natureza_operacao,
        informacoes_complementares=estabelecimento.informacoes_complementares,
        provedor=estabelecimento.provedor,
        parametros_provedor=dict(estabelecimento.parametros_provedor or {}),
        csc_id=estabelecimento.csc_id,
        url_consulta_qrcode=estabelecimento.url_consulta_qrcode,
        url_consulta_chave=estabelecimento.url_consulta_chave,
        enderecos_conferidos=bool(estabelecimento.enderecos_conferidos),
        emissao_ativa=bool(estabelecimento.emissao_ativa),
        permitir_contingencia=bool(estabelecimento.permitir_contingencia),
        atualizado_por_id=estabelecimento.atualizado_por_id,
    )
    db.session.add(config)
    db.session.flush()
    return config


def reservar_numero(empresa_id: int) -> NumeroReservado:
    """Toma o próximo número da série, com trava de linha.

    A trava é o que impede dois caixas de pegarem o mesmo número ao mesmo
    tempo — duplicidade de numeração é rejeição certa na SEFAZ.
    """
    estabelecimento = db.session.scalar(
        db.select(EstabelecimentoFiscal)
        .where(EstabelecimentoFiscal.empresa_id == empresa_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if estabelecimento is None:
        raise SequenciaErro("Esta empresa ainda não tem configuração fiscal.")
    config = _configuracao_atual(estabelecimento, travar=True)
    numero = int(config.proximo_numero)
    if not 1 <= numero <= 999_999_999:
        raise SequenciaErro(
            "A numeração fiscal chegou ao limite da série. Abra uma série nova."
        )
    config.proximo_numero = numero + 1
    # Mantém a coluna legada espelhada enquanto a transição multi-configuração
    # não termina. O contador oficial desta fase é o da nova tabela.
    estabelecimento.proximo_numero = config.proximo_numero
    db.session.flush()
    return NumeroReservado(
        numero=numero,
        serie=int(config.serie),
        modelo=int(config.modelo),
        codigo_numerico=gerar_codigo_numerico(numero),
    )


def _janela_analisada(
    empresa_id: int, cnpj: str, modelo: int, ambiente: int, serie: int, quantidade: int
) -> tuple[int, int] | None:
    """Menor e maior número autorizado dentro das últimas ``quantidade`` notas."""
    numeros = db.session.scalars(
        db.select(DocumentoFiscal.numero)
        .where(
            DocumentoFiscal.empresa_id == empresa_id,
            DocumentoFiscal.cnpj_emitente == cnpj,
            DocumentoFiscal.modelo == modelo,
            DocumentoFiscal.ambiente == ambiente,
            DocumentoFiscal.serie == serie,
            DocumentoFiscal.protocolo_autorizacao.is_not(None),
            DocumentoFiscal.status.in_(("autorizado", "cancelado")),
        )
        .order_by(DocumentoFiscal.numero.desc())
        .limit(max(1, int(quantidade)))
    ).all()
    if not numeros:
        return None
    return min(numeros), max(numeros)


def detectar_lacunas(
    empresa_id: int, *, quantidade_analisada: int = QUANTIDADE_ANALISADA_PADRAO
) -> list[dict]:
    """Lista números sem documento autorizado dentro da janela analisada.

    Não grava nada — devolve os candidatos para que uma pessoa confirme.
    """
    estabelecimento = db.session.get(EstabelecimentoFiscal, empresa_id)
    if estabelecimento is None:
        return []
    cnpj = estabelecimento.cnpj
    modelo = int(estabelecimento.modelo)
    serie = int(estabelecimento.serie)
    ambiente = int(estabelecimento.ambiente)

    janela = _janela_analisada(
        empresa_id, cnpj, modelo, ambiente, serie, quantidade_analisada
    )
    if janela is None:
        return []
    inicial, final = janela
    if final <= inicial:
        return []

    autorizados = set(
        db.session.scalars(
            db.select(DocumentoFiscal.numero).where(
                DocumentoFiscal.empresa_id == empresa_id,
                DocumentoFiscal.cnpj_emitente == cnpj,
                DocumentoFiscal.modelo == modelo,
                DocumentoFiscal.ambiente == ambiente,
                DocumentoFiscal.serie == serie,
                DocumentoFiscal.numero.between(inicial, final),
                DocumentoFiscal.protocolo_autorizacao.is_not(None),
            )
        ).all()
    )
    # Um número ainda em transmissão não é lacuna: pode virar nota a qualquer momento.
    em_andamento = set(
        db.session.scalars(
            db.select(DocumentoFiscal.numero).where(
                DocumentoFiscal.empresa_id == empresa_id,
                DocumentoFiscal.cnpj_emitente == cnpj,
                DocumentoFiscal.modelo == modelo,
                DocumentoFiscal.ambiente == ambiente,
                DocumentoFiscal.serie == serie,
                DocumentoFiscal.numero.between(inicial, final),
                DocumentoFiscal.status.in_(("pendente", "processando", "contingencia")),
            )
        ).all()
    )
    ja_registrados = set(
        db.session.scalars(
            db.select(InutilizacaoFiscal.numero).where(
                InutilizacaoFiscal.empresa_id == empresa_id,
                InutilizacaoFiscal.cnpj == cnpj,
                InutilizacaoFiscal.modelo == modelo,
                InutilizacaoFiscal.ambiente == ambiente,
                InutilizacaoFiscal.serie == serie,
                InutilizacaoFiscal.numero.between(inicial, final),
                InutilizacaoFiscal.status.in_(("pendente", "autorizada")),
            )
        ).all()
    )

    faltantes = [
        numero
        for numero in range(inicial, final + 1)
        if numero not in autorizados
        and numero not in em_andamento
        and numero not in ja_registrados
    ]
    return [
        {
            "cnpj": cnpj,
            "modelo": modelo,
            "ambiente": ambiente,
            "serie": serie,
            "numero": numero,
            "faixa_inicial": inicial,
            "faixa_final": final,
        }
        for numero in faltantes
    ]


def agrupar_faixas(numeros: list[int]) -> list[tuple[int, int]]:
    """Junta números seguidos em faixas — a SEFAZ inutiliza faixa, não item."""
    ordenados = sorted(set(int(numero) for numero in numeros))
    if not ordenados:
        return []
    faixas: list[tuple[int, int]] = []
    inicio = anterior = ordenados[0]
    for numero in ordenados[1:]:
        if numero == anterior + 1:
            anterior = numero
            continue
        faixas.append((inicio, anterior))
        inicio = anterior = numero
    faixas.append((inicio, anterior))
    return faixas


def registrar_lacunas(
    empresa_id: int,
    *,
    quantidade_analisada: int = QUANTIDADE_ANALISADA_PADRAO,
    usuario_id: uuid.UUID | None = None,
) -> list[InutilizacaoFiscal]:
    """Grava as lacunas encontradas como pendências de inutilização."""
    registros: list[InutilizacaoFiscal] = []
    for lacuna in detectar_lacunas(empresa_id, quantidade_analisada=quantidade_analisada):
        existente = db.session.scalar(
            db.select(InutilizacaoFiscal).where(
                InutilizacaoFiscal.cnpj == lacuna["cnpj"],
                InutilizacaoFiscal.modelo == lacuna["modelo"],
                InutilizacaoFiscal.ambiente == lacuna["ambiente"],
                InutilizacaoFiscal.serie == lacuna["serie"],
                InutilizacaoFiscal.numero == lacuna["numero"],
            )
        )
        if existente is not None:
            continue
        registro = InutilizacaoFiscal(
            empresa_id=empresa_id,
            cnpj=lacuna["cnpj"],
            modelo=lacuna["modelo"],
            ambiente=lacuna["ambiente"],
            serie=lacuna["serie"],
            numero=lacuna["numero"],
            status="pendente",
            origem="lacuna",
            resolvido_por_id=None,
        )
        db.session.add(registro)
        registros.append(registro)
    if registros:
        db.session.flush()
    return registros


def resumo_numeracao(empresa_id: int) -> dict:
    """Números para o painel fiscal: emitidas, pendências e próxima numeração."""
    estabelecimento = db.session.get(EstabelecimentoFiscal, empresa_id)
    if estabelecimento is None:
        return {
            "configurado": False,
            "autorizados": 0,
            "pendentes": 0,
            "rejeitados": 0,
            "inutilizacoes_pendentes": 0,
            "proximo_numero": None,
        }

    def contar(*status: str) -> int:
        return int(
            db.session.scalar(
                db.select(func.count(DocumentoFiscal.id)).where(
                    DocumentoFiscal.empresa_id == empresa_id,
                    DocumentoFiscal.status.in_(status),
                )
            )
            or 0
        )

    inutilizacoes = int(
        db.session.scalar(
            db.select(func.count(InutilizacaoFiscal.id)).where(
                InutilizacaoFiscal.empresa_id == empresa_id,
                InutilizacaoFiscal.status == "pendente",
            )
        )
        or 0
    )
    return {
        "configurado": True,
        "autorizados": contar("autorizado"),
        "cancelados": contar("cancelado"),
        "pendentes": contar("pendente", "processando", "contingencia"),
        "rejeitados": contar("rejeitado", "denegado", "falhou"),
        "inutilizacoes_pendentes": inutilizacoes,
        "proximo_numero": int(_configuracao_atual(estabelecimento).proximo_numero),
        "serie": int(estabelecimento.serie),
        "modelo": int(estabelecimento.modelo),
        "ambiente": int(estabelecimento.ambiente),
    }
