"""Integração com a balança do açougue: etiqueta pesada e carga de preços.

Como o fluxo funciona numa loja de carnes no Brasil:

1. o atendente pesa a carne na balança do balcão;
2. a balança imprime uma etiqueta com código de barras EAN-13 começando com
   ``2`` — a faixa reservada para uso interno da loja;
3. dentro desse código vai o PLU do produto e **ou o peso, ou o valor total**;
4. no caixa, o operador bipa essa etiqueta.

O código de barras é diferente a cada pesagem, então ele nunca vai bater com um
código fixo cadastrado. É preciso decompor a etiqueta para saber qual produto é
e quanto pesou.

Layout típico (13 dígitos):

    2  PPPPPP  VVVVV  D
    │  │       │      └─ dígito verificador do EAN-13
    │  │       └──────── peso em gramas OU valor em centavos
    │  └──────────────── PLU do produto
    └─────────────────── prefixo de uso interno

O tamanho do PLU e o significado do campo variam de loja para loja, e **errar
isso cobra o valor errado do cliente**. Por isso nada aqui é adivinhado: o
layout é configurado por empresa e, enquanto não estiver configurado, a
etiqueta não é decodificada — o código segue para a busca normal.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


PREFIXOS_INTERNOS = ("2",)
CONTEUDO_PESO = "peso"
CONTEUDO_PRECO = "preco"
CONTEUDOS = {
    CONTEUDO_PESO: "Peso embutido na etiqueta",
    CONTEUDO_PRECO: "Valor total embutido na etiqueta",
}

_QTD = Decimal("0.001")
_CENTAVO = Decimal("0.01")
_SO_DIGITOS = re.compile(r"^\d{13}$")


class EtiquetaInvalida(ValueError):
    pass


@dataclass(slots=True)
class LayoutEtiqueta:
    """Como ler o código de barras impresso pela balança desta loja."""

    prefixo: str = "2"
    tamanho_plu: int = 6
    conteudo: str = CONTEUDO_PESO
    #: casas decimais do campo de valor (3 = gramas para quilo, 2 = centavos)
    casas_decimais: int = 3
    ativo: bool = False

    def conferir(self) -> list[str]:
        problemas: list[str] = []
        if self.prefixo not in PREFIXOS_INTERNOS:
            problemas.append(
                "O prefixo da etiqueta precisa ser 2 — é a faixa reservada para "
                "uso interno da loja."
            )
        if not 4 <= int(self.tamanho_plu) <= 7:
            problemas.append("O PLU precisa ter de 4 a 7 dígitos.")
        if self.conteudo not in CONTEUDOS:
            problemas.append("Escolha se a etiqueta traz o peso ou o valor total.")
        if not 0 <= int(self.casas_decimais) <= 3:
            problemas.append("As casas decimais precisam estar entre 0 e 3.")
        # sobra: 13 - prefixo(1) - plu - dv(1)
        if 13 - 1 - int(self.tamanho_plu) - 1 < 3:
            problemas.append(
                "Com esse tamanho de PLU não sobram dígitos suficientes para o "
                "peso ou o valor."
            )
        return problemas


@dataclass(slots=True)
class EtiquetaLida:
    """O que foi possível extrair de uma etiqueta de balança."""

    codigo: str
    plu: str
    conteudo: str
    peso: Decimal | None = None
    valor_total: Decimal | None = None

    @property
    def traz_peso(self) -> bool:
        return self.conteudo == CONTEUDO_PESO


def digito_verificador_ean13(doze_digitos: str) -> int:
    """DV do EAN-13: pesos 1 e 3 alternados, da esquerda para a direita."""
    if len(doze_digitos) != 12 or not doze_digitos.isdigit():
        raise EtiquetaInvalida("O cálculo do dígito exige 12 dígitos.")
    soma = sum(
        int(digito) * (3 if posicao % 2 else 1)
        for posicao, digito in enumerate(doze_digitos)
    )
    return (10 - soma % 10) % 10


def ean13_valido(codigo: str) -> bool:
    if not _SO_DIGITOS.fullmatch(codigo or ""):
        return False
    return digito_verificador_ean13(codigo[:12]) == int(codigo[12])


def parece_etiqueta(codigo: str | None, layout: LayoutEtiqueta) -> bool:
    """Diz se vale a pena tentar decodificar, sem ainda decidir nada."""
    limpo = (codigo or "").strip()
    if not layout.ativo:
        return False
    return _SO_DIGITOS.fullmatch(limpo) is not None and limpo.startswith(layout.prefixo)


def ler_etiqueta(codigo: str, layout: LayoutEtiqueta) -> EtiquetaLida:
    """Decompõe a etiqueta. Levanta erro quando o código não é uma etiqueta válida."""
    limpo = (codigo or "").strip()
    if not _SO_DIGITOS.fullmatch(limpo):
        raise EtiquetaInvalida("A etiqueta da balança tem 13 dígitos numéricos.")
    if not limpo.startswith(layout.prefixo):
        raise EtiquetaInvalida(
            f"Etiqueta de balança começa com {layout.prefixo}; este código não começa."
        )
    if not ean13_valido(limpo):
        # Etiqueta amassada, suja ou mal impressa cai aqui. Melhor recusar do
        # que cobrar o valor de outro produto.
        raise EtiquetaInvalida(
            "O dígito verificador da etiqueta não confere. Peça outra etiqueta "
            "ou digite o código do produto."
        )

    problemas = layout.conferir()
    if problemas:
        raise EtiquetaInvalida(problemas[0])

    inicio_valor = 1 + int(layout.tamanho_plu)
    plu = limpo[1:inicio_valor]
    bruto = limpo[inicio_valor:12]
    numero = Decimal(bruto) / (Decimal(10) ** int(layout.casas_decimais))

    if layout.conteudo == CONTEUDO_PESO:
        peso = numero.quantize(_QTD, rounding=ROUND_HALF_UP)
        if peso <= 0:
            raise EtiquetaInvalida("A etiqueta veio com peso zerado.")
        return EtiquetaLida(codigo=limpo, plu=plu, conteudo=CONTEUDO_PESO, peso=peso)

    valor = numero.quantize(_CENTAVO, rounding=ROUND_HALF_UP)
    if valor <= 0:
        raise EtiquetaInvalida("A etiqueta veio com valor zerado.")
    return EtiquetaLida(codigo=limpo, plu=plu, conteudo=CONTEUDO_PRECO, valor_total=valor)


def peso_a_partir_do_valor(
    valor_total: Decimal, preco_unitario: Decimal
) -> tuple[Decimal, Decimal]:
    """Converte valor total em peso, usando o preço do sistema.

    Devolve ``(peso, valor recalculado)``. Quando a balança está com um preço
    diferente do sistema, o valor recalculado não bate com o da etiqueta — e é
    isso que denuncia que a carga de preços da balança está atrasada.
    """
    preco = Decimal(preco_unitario)
    if preco <= 0:
        raise EtiquetaInvalida("O produto está sem preço de venda válido.")
    peso = (Decimal(valor_total) / preco).quantize(_QTD, rounding=ROUND_HALF_UP)
    if peso <= 0:
        raise EtiquetaInvalida(
            "O valor da etiqueta é menor que um grama do produto. Confira o preço."
        )
    recalculado = (preco * peso).quantize(_CENTAVO, rounding=ROUND_HALF_UP)
    return peso, recalculado


def montar_etiqueta(
    plu: str, valor: Decimal, layout: LayoutEtiqueta
) -> str:
    """Gera o código que a balança imprimiria. Serve para testar e conferir."""
    problemas = layout.conferir()
    if problemas:
        raise EtiquetaInvalida(problemas[0])
    plu_limpo = re.sub(r"\D", "", plu or "").zfill(int(layout.tamanho_plu))
    if len(plu_limpo) != int(layout.tamanho_plu):
        raise EtiquetaInvalida(
            f"O PLU precisa caber em {layout.tamanho_plu} dígitos."
        )
    largura = 12 - 1 - int(layout.tamanho_plu)
    escala = Decimal(10) ** int(layout.casas_decimais)
    bruto = int((Decimal(valor) * escala).to_integral_value(rounding=ROUND_HALF_UP))
    texto = str(bruto).zfill(largura)
    if len(texto) != largura:
        raise EtiquetaInvalida(
            f"O valor não cabe em {largura} dígitos nesta configuração."
        )
    doze = f"{layout.prefixo}{plu_limpo}{texto}"
    return doze + str(digito_verificador_ean13(doze))


# --------------------------------------------------------------------------
# Ligação com o banco: configuração, busca por PLU e carga de preços
# --------------------------------------------------------------------------

from ..extensions import db  # noqa: E402
from ..models import (  # noqa: E402
    CodigoProduto,
    ConfiguracaoBalanca,
    Produto,
)


#: campos que podem entrar no arquivo de carga da balança
CAMPOS_CARGA = {
    "plu": "Código do produto na balança (PLU)",
    "descricao": "Descrição impressa na etiqueta",
    "descricao_curta": "Descrição abreviada (20 caracteres)",
    "preco": "Preço por quilo ou por unidade",
    "preco_centavos": "Preço sem separador decimal, em centavos",
    "tipo": "1 = pesável, 0 = por unidade",
    "unidade": "Unidade de venda",
    "validade_dias": "Dias de validade impressos na etiqueta",
    "setor": "Setor ou departamento",
    "ean": "Código de barras cadastrado do produto",
}

CAMPOS_CARGA_PADRAO = ("plu", "descricao", "preco", "tipo", "validade_dias")

QUEBRAS = {"crlf": "\r\n", "lf": "\n"}


def layout_da_empresa(empresa_id: int) -> LayoutEtiqueta:
    """Lê o layout configurado. Sem configuração, devolve um layout desligado."""
    configuracao = db.session.get(ConfiguracaoBalanca, empresa_id)
    if configuracao is None:
        return LayoutEtiqueta(ativo=False)
    return LayoutEtiqueta(
        prefixo=configuracao.prefixo,
        tamanho_plu=int(configuracao.tamanho_plu),
        conteudo=configuracao.conteudo,
        casas_decimais=int(configuracao.casas_decimais),
        ativo=bool(configuracao.ativo),
    )


def variantes_de_plu(plu: str) -> list[str]:
    """O PLU na etiqueta vem com zeros à esquerda; no cadastro, nem sempre."""
    limpo = re.sub(r"\D", "", plu or "")
    if not limpo:
        return []
    candidatos = [limpo, limpo.lstrip("0") or "0"]
    for largura in (4, 5, 6, 7):
        candidatos.append(limpo.lstrip("0").zfill(largura))
    vistos: list[str] = []
    for item in candidatos:
        if item and item not in vistos:
            vistos.append(item)
    return vistos


def localizar_por_plu(empresa_id: int, plu: str) -> CodigoProduto | None:
    """Acha o produto pelo PLU da etiqueta, tolerando zeros à esquerda."""
    for candidato in variantes_de_plu(plu):
        codigo = db.session.scalar(
            db.select(CodigoProduto).where(
                CodigoProduto.empresa_id == empresa_id,
                CodigoProduto.chave_busca == candidato,
                CodigoProduto.ativo.is_(True),
            )
        )
        if codigo is not None:
            return codigo
    return None


@dataclass(slots=True)
class QuantidadeDaEtiqueta:
    """Quanto entra na venda e o que isso revelou sobre o preço da balança."""

    quantidade: Decimal
    valor_etiqueta: Decimal | None = None
    valor_recalculado: Decimal | None = None
    divergencia: Decimal = Decimal("0")

    @property
    def tem_divergencia(self) -> bool:
        return self.divergencia != 0


def identificar_etiqueta(empresa_id: int, codigo: str) -> tuple[CodigoProduto, EtiquetaLida]:
    """Decodifica a etiqueta e encontra o produto pelo PLU.

    Separada do cálculo da quantidade de propósito: o caixa precisa travar a
    linha do produto antes de ler o preço, senão o peso sairia de um preço que
    outra sessão pode estar alterando no mesmo instante.
    """
    etiqueta = ler_etiqueta(codigo, layout_da_empresa(empresa_id))
    codigo_produto = localizar_por_plu(empresa_id, etiqueta.plu)
    if codigo_produto is None:
        raise EtiquetaInvalida(
            f"A etiqueta aponta para o PLU {etiqueta.plu}, que não está cadastrado "
            "em nenhum produto desta empresa. Cadastre esse PLU no produto."
        )
    return codigo_produto, etiqueta


def quantidade_da_etiqueta(
    empresa_id: int, etiqueta: EtiquetaLida, preco_unitario: Decimal
) -> QuantidadeDaEtiqueta:
    """Converte a etiqueta em quantidade, já com o preço travado do produto."""
    if etiqueta.traz_peso:
        return QuantidadeDaEtiqueta(quantidade=etiqueta.peso)

    peso, recalculado = peso_a_partir_do_valor(etiqueta.valor_total, preco_unitario)
    divergencia = (recalculado - etiqueta.valor_total).quantize(_CENTAVO)

    configuracao = db.session.get(ConfiguracaoBalanca, empresa_id)
    tolerancia = (
        Decimal(configuracao.tolerancia) if configuracao is not None else Decimal("0.05")
    )
    bloquear = configuracao.bloquear_divergencia if configuracao is not None else True
    if bloquear and abs(divergencia) > tolerancia:
        raise EtiquetaInvalida(
            f"A etiqueta diz R$ {etiqueta.valor_total:.2f}, mas com o preço atual do "
            f"sistema (R$ {Decimal(preco_unitario):.2f}/{'kg'}) o valor seria "
            f"R$ {recalculado:.2f}. A balança está com preço desatualizado: gere a "
            "carga de preços e recarregue a balança antes de vender este item."
        )

    return QuantidadeDaEtiqueta(
        quantidade=peso,
        valor_etiqueta=etiqueta.valor_total,
        valor_recalculado=recalculado,
        divergencia=divergencia,
    )


# --------------------------------------------------------------------------
# Carga de preços para a balança
# --------------------------------------------------------------------------


@dataclass(slots=True)
class LinhaCarga:
    plu: str
    descricao: str
    preco: Decimal
    pesavel: bool
    unidade: str
    ean: str = ""
    validade_dias: int = 0
    setor: str = "1"

    def valor(self, campo: str) -> str:
        if campo == "plu":
            return self.plu
        if campo == "descricao":
            return self.descricao
        if campo == "descricao_curta":
            return self.descricao[:20]
        if campo == "preco":
            return f"{self.preco:.2f}"
        if campo == "preco_centavos":
            return str(int((self.preco * 100).to_integral_value(rounding=ROUND_HALF_UP)))
        if campo == "tipo":
            return "1" if self.pesavel else "0"
        if campo == "unidade":
            return self.unidade
        if campo == "validade_dias":
            return str(int(self.validade_dias))
        if campo == "setor":
            return self.setor
        if campo == "ean":
            return self.ean
        raise EtiquetaInvalida(f"Campo de carga desconhecido: {campo}")


def _texto_balanca(valor: str) -> str:
    """A balança imprime em display de segmentos: sem acento e em maiúsculas."""
    sem_acento = (
        unicodedata.normalize("NFKD", valor or "")
        .encode("ascii", errors="ignore")
        .decode("ascii")
    )
    return " ".join(sem_acento.upper().split())


def montar_linhas_carga(empresa_id: int) -> list[LinhaCarga]:
    """Monta a lista de produtos que vai para a balança.

    Entram os produtos que a balança precisa conhecer:

    * todo produto **pesável** — é o que o balcão vai pesar e etiquetar;
    * e qualquer produto que tenha um código cadastrado como PLU, mesmo não
      pesável, porque alguém marcou explicitamente que ele vive na balança.

    O PLU usado é o código do tipo ``plu``, quando existir; caso contrário, o
    código principal ativo do produto. Não restringir ao tipo ``plu`` é
    deliberado: a carga inicial trouxe os códigos da loja marcados como
    ``legado``, e exigir a retipagem de milhares de itens antes de carregar a
    balança seria travar a operação por burocracia de cadastro.
    """
    produtos = db.session.scalars(
        db.select(Produto)
        .where(Produto.empresa_id == empresa_id, Produto.ativo.is_(True))
        .order_by(Produto.nome)
    ).all()

    linhas: list[LinhaCarga] = []
    vistos: set[str] = set()
    for produto in produtos:
        ativos = [codigo for codigo in produto.codigos if codigo.ativo]
        if not ativos:
            continue
        plu_explicito = next((c for c in ativos if c.tipo == "plu"), None)
        if plu_explicito is None and not produto.pesado:
            continue
        escolhido = (
            plu_explicito
            or next((c for c in ativos if c.principal), None)
            or ativos[0]
        )
        plu = escolhido.chave_busca
        if plu in vistos:
            continue
        vistos.add(plu)
        ean = next(
            (c.codigo for c in ativos if c.tipo == "ean"),
            escolhido.codigo if escolhido.tipo == "ean" else "",
        )
        linhas.append(
            LinhaCarga(
                plu=plu,
                descricao=_texto_balanca(produto.nome),
                preco=Decimal(produto.preco_venda).quantize(_CENTAVO),
                pesavel=bool(produto.pesado),
                unidade=produto.unidade,
                ean=ean,
            )
        )
    return linhas


def gerar_carga(empresa_id: int) -> tuple[bytes, str, int]:
    """Gera o arquivo de carga. Devolve ``(conteúdo, nome sugerido, linhas)``.

    O layout é configurável de propósito: cada versão do gerenciador da balança
    espera uma ordem de campos e uma codificação. Os padrões aqui são os mais
    comuns, mas precisam ser conferidos contra a tela de importação do
    gerenciador antes do primeiro uso.
    """
    configuracao = db.session.get(ConfiguracaoBalanca, empresa_id)
    separador = (configuracao.separador_carga if configuracao else ";") or ";"
    codificacao = (configuracao.codificacao_carga if configuracao else "latin-1") or "latin-1"
    quebra = QUEBRAS.get(
        (configuracao.quebra_linha_carga if configuracao else "crlf"), "\r\n"
    )
    campos = list(
        (configuracao.campos_carga if configuracao else None) or CAMPOS_CARGA_PADRAO
    )
    desconhecidos = [campo for campo in campos if campo not in CAMPOS_CARGA]
    if desconhecidos:
        raise EtiquetaInvalida(
            f"Campo de carga desconhecido na configuração: {', '.join(desconhecidos)}"
        )

    linhas = montar_linhas_carga(empresa_id)
    texto = quebra.join(
        separador.join(linha.valor(campo) for campo in campos) for linha in linhas
    )
    if linhas:
        texto += quebra
    conteudo = texto.encode(codificacao, errors="replace")
    return conteudo, "carga-balanca.txt", len(linhas)
