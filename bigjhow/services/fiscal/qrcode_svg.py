"""Gerador de QR Code em SVG, sem dependências externas.

O DANFE NFC-e (modelo 65) exige o QR Code impresso, e o CF-e SAT também.
A Política de Segurança de Conteúdo desta aplicação bloqueia scripts e imagens
de terceiros, então o código precisa ser desenhado no próprio servidor.

Implementação conforme ISO/IEC 18004: modo byte, nível de correção M,
versões 1 a 20 (suficiente para as URLs de consulta da NFC-e, que ficam
na casa dos 200 a 300 caracteres).
"""

from __future__ import annotations

from xml.sax.saxutils import escape


class QRCodeErro(ValueError):
    pass


# --------------------------------------------------------------------------
# Tabelas da norma
# --------------------------------------------------------------------------

# Centros dos padrões de alinhamento por versão (ISO/IEC 18004, Tabela E.1).
_ALINHAMENTO: dict[int, tuple[int, ...]] = {
    1: (),
    2: (6, 18),
    3: (6, 22),
    4: (6, 26),
    5: (6, 30),
    6: (6, 34),
    7: (6, 22, 38),
    8: (6, 24, 42),
    9: (6, 26, 46),
    10: (6, 28, 50),
    11: (6, 30, 54),
    12: (6, 32, 58),
    13: (6, 34, 62),
    14: (6, 26, 46, 66),
    15: (6, 26, 48, 70),
    16: (6, 26, 50, 74),
    17: (6, 30, 54, 78),
    18: (6, 30, 56, 82),
    19: (6, 30, 58, 86),
    20: (6, 34, 62, 90),
}

# Nível M: (codewords de correção por bloco, blocos g1, dados g1, blocos g2, dados g2)
_BLOCOS_M: dict[int, tuple[int, int, int, int, int]] = {
    1: (10, 1, 16, 0, 0),
    2: (16, 1, 28, 0, 0),
    3: (26, 1, 44, 0, 0),
    4: (18, 2, 32, 0, 0),
    5: (24, 2, 43, 0, 0),
    6: (16, 4, 27, 0, 0),
    7: (18, 4, 31, 0, 0),
    8: (22, 2, 38, 2, 39),
    9: (22, 3, 36, 2, 37),
    10: (26, 4, 43, 1, 44),
    11: (30, 1, 50, 4, 51),
    12: (22, 6, 36, 2, 37),
    13: (22, 8, 37, 1, 38),
    14: (24, 4, 40, 5, 41),
    15: (24, 5, 41, 5, 42),
    16: (28, 7, 45, 3, 46),
    17: (28, 10, 46, 1, 47),
    18: (26, 9, 43, 4, 44),
    19: (26, 3, 44, 11, 45),
    20: (26, 3, 41, 13, 42),
}

_INDICADOR_NIVEL_M = 0b00  # ISO/IEC 18004, Tabela 12.
_MASCARA_FORMATO = 0b101010000010010
_GERADOR_FORMATO = 0b10100110111
_GERADOR_VERSAO = 0b1111100100101


def dimensao(versao: int) -> int:
    return 17 + 4 * versao


def _modulos_de_dados(versao: int) -> int:
    """Conta os módulos livres para dados, derivando da geometria da versão.

    Serve também de conferência da tabela ``_BLOCOS_M``: o resultado dividido
    por oito precisa bater com o total de codewords daquela versão.
    """
    lado = dimensao(versao)
    total = lado * lado
    total -= 3 * 64  # três localizadores com seus separadores (8x8 cada)
    total -= 31  # informação de formato (30 módulos) + módulo escuro fixo
    if versao >= 7:
        total -= 36  # informação de versão, duas cópias de 18 módulos
    total -= 2 * (lado - 16)  # padrões de temporização horizontal e vertical
    centros = _ALINHAMENTO[versao]
    if centros:
        quantidade = len(centros) ** 2 - 3  # os três cantos caem sobre localizadores
        sobreposicoes = 2 * (len(centros) - 2) if len(centros) >= 3 else 0
        total -= quantidade * 25 - sobreposicoes * 5
    return total


def _total_codewords(versao: int) -> int:
    correcao, blocos1, dados1, blocos2, dados2 = _BLOCOS_M[versao]
    return blocos1 * (dados1 + correcao) + blocos2 * (dados2 + correcao)


def _capacidade_dados(versao: int) -> int:
    _, blocos1, dados1, blocos2, dados2 = _BLOCOS_M[versao]
    return blocos1 * dados1 + blocos2 * dados2


# --------------------------------------------------------------------------
# Aritmética em GF(256) para Reed-Solomon
# --------------------------------------------------------------------------

_EXP = [0] * 512
_LOG = [0] * 256


def _preparar_galois() -> None:
    valor = 1
    for expoente in range(255):
        _EXP[expoente] = valor
        _LOG[valor] = expoente
        valor <<= 1
        if valor & 0x100:
            valor ^= 0x11D  # polinômio primitivo x^8 + x^4 + x^3 + x^2 + 1
    for expoente in range(255, 512):
        _EXP[expoente] = _EXP[expoente - 255]


_preparar_galois()


def _multiplicar(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _polinomio_gerador(grau: int) -> list[int]:
    polinomio = [1]
    for indice in range(grau):
        proximo = [0] * (len(polinomio) + 1)
        for posicao, coeficiente in enumerate(polinomio):
            proximo[posicao] ^= coeficiente
            proximo[posicao + 1] ^= _multiplicar(coeficiente, _EXP[indice])
        polinomio = proximo
    return polinomio


def _codewords_correcao(dados: list[int], quantidade: int) -> list[int]:
    gerador = _polinomio_gerador(quantidade)
    resto = list(dados) + [0] * quantidade
    for posicao in range(len(dados)):
        coeficiente = resto[posicao]
        if coeficiente == 0:
            continue
        for deslocamento, fator in enumerate(gerador):
            resto[posicao + deslocamento] ^= _multiplicar(fator, coeficiente)
    return resto[len(dados) :]


# --------------------------------------------------------------------------
# Codificação
# --------------------------------------------------------------------------


def _escolher_versao(tamanho_bytes: int) -> int:
    for versao in sorted(_BLOCOS_M):
        bits_contagem = 8 if versao <= 9 else 16
        necessarios = 4 + bits_contagem + tamanho_bytes * 8
        if necessarios <= _capacidade_dados(versao) * 8:
            return versao
    raise QRCodeErro(
        "Conteúdo grande demais para o QR Code (limite: versão 20, nível M)."
    )


def _fluxo_de_bits(dados: bytes, versao: int) -> list[int]:
    bits: list[int] = []

    def acrescentar(valor: int, quantidade: int) -> None:
        for posicao in range(quantidade - 1, -1, -1):
            bits.append((valor >> posicao) & 1)

    acrescentar(0b0100, 4)  # modo byte
    acrescentar(len(dados), 8 if versao <= 9 else 16)
    for octeto in dados:
        acrescentar(octeto, 8)

    capacidade = _capacidade_dados(versao) * 8
    if len(bits) > capacidade:
        raise QRCodeErro("Conteúdo excede a capacidade da versão escolhida.")
    bits.extend([0] * min(4, capacidade - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    preenchimento = (0xEC, 0x11)
    indice = 0
    while len(bits) < capacidade:
        acrescentar(preenchimento[indice % 2], 8)
        indice += 1
    return bits


def _codewords(dados: bytes, versao: int) -> list[int]:
    bits = _fluxo_de_bits(dados, versao)
    brutos = [
        int("".join(str(bit) for bit in bits[posicao : posicao + 8]), 2)
        for posicao in range(0, len(bits), 8)
    ]

    correcao, blocos1, tamanho1, blocos2, tamanho2 = _BLOCOS_M[versao]
    blocos_dados: list[list[int]] = []
    blocos_correcao: list[list[int]] = []
    cursor = 0
    for _ in range(blocos1):
        bloco = brutos[cursor : cursor + tamanho1]
        cursor += tamanho1
        blocos_dados.append(bloco)
        blocos_correcao.append(_codewords_correcao(bloco, correcao))
    for _ in range(blocos2):
        bloco = brutos[cursor : cursor + tamanho2]
        cursor += tamanho2
        blocos_dados.append(bloco)
        blocos_correcao.append(_codewords_correcao(bloco, correcao))

    resultado: list[int] = []
    maior = max(len(bloco) for bloco in blocos_dados)
    for posicao in range(maior):
        for bloco in blocos_dados:
            if posicao < len(bloco):
                resultado.append(bloco[posicao])
    for posicao in range(correcao):
        for bloco in blocos_correcao:
            resultado.append(bloco[posicao])
    return resultado


# --------------------------------------------------------------------------
# Montagem da matriz
# --------------------------------------------------------------------------


class _Matriz:
    def __init__(self, versao: int) -> None:
        self.versao = versao
        self.lado = dimensao(versao)
        self.modulos = [[0] * self.lado for _ in range(self.lado)]
        self.reservado = [[False] * self.lado for _ in range(self.lado)]

    def definir(self, linha: int, coluna: int, valor: int, *, funcao: bool = True) -> None:
        self.modulos[linha][coluna] = valor
        if funcao:
            self.reservado[linha][coluna] = True


def _desenhar_localizador(matriz: _Matriz, linha: int, coluna: int) -> None:
    for deslocamento_linha in range(-1, 8):
        for deslocamento_coluna in range(-1, 8):
            alvo_linha = linha + deslocamento_linha
            alvo_coluna = coluna + deslocamento_coluna
            if not (0 <= alvo_linha < matriz.lado and 0 <= alvo_coluna < matriz.lado):
                continue
            dentro = (
                0 <= deslocamento_linha <= 6
                and 0 <= deslocamento_coluna <= 6
                and (
                    deslocamento_linha in (0, 6)
                    or deslocamento_coluna in (0, 6)
                    or (2 <= deslocamento_linha <= 4 and 2 <= deslocamento_coluna <= 4)
                )
            )
            matriz.definir(alvo_linha, alvo_coluna, 1 if dentro else 0)


def _desenhar_alinhamento(matriz: _Matriz) -> None:
    centros = _ALINHAMENTO[matriz.versao]
    for linha in centros:
        for coluna in centros:
            if matriz.reservado[linha][coluna]:
                continue
            for deslocamento_linha in range(-2, 3):
                for deslocamento_coluna in range(-2, 3):
                    escuro = (
                        abs(deslocamento_linha) == 2
                        or abs(deslocamento_coluna) == 2
                        or (deslocamento_linha == 0 and deslocamento_coluna == 0)
                    )
                    matriz.definir(
                        linha + deslocamento_linha,
                        coluna + deslocamento_coluna,
                        1 if escuro else 0,
                    )


def _desenhar_temporizacao(matriz: _Matriz) -> None:
    for posicao in range(8, matriz.lado - 8):
        valor = 1 if posicao % 2 == 0 else 0
        matriz.definir(6, posicao, valor)
        matriz.definir(posicao, 6, valor)


def _reservar_formato(matriz: _Matriz) -> None:
    lado = matriz.lado
    for posicao in range(9):
        if posicao != 6:
            matriz.definir(8, posicao, 0)
            matriz.definir(posicao, 8, 0)
    for posicao in range(8):
        matriz.definir(8, lado - 1 - posicao, 0)
        matriz.definir(lado - 1 - posicao, 8, 0)
    matriz.definir(lado - 8, 8, 1)  # módulo escuro fixo


def _reservar_versao(matriz: _Matriz) -> None:
    if matriz.versao < 7:
        return
    lado = matriz.lado
    for posicao in range(18):
        linha = posicao // 3
        coluna = posicao % 3
        matriz.definir(linha, lado - 11 + coluna, 0)
        matriz.definir(lado - 11 + coluna, linha, 0)


def _bch(valor: int, gerador: int, bits_gerador: int) -> int:
    resto = valor
    while resto.bit_length() >= bits_gerador:
        resto ^= gerador << (resto.bit_length() - bits_gerador)
    return resto


def _bits_formato(mascara: int) -> int:
    base = (_INDICADOR_NIVEL_M << 3) | mascara
    resto = _bch(base << 10, _GERADOR_FORMATO, 11)
    return ((base << 10) | resto) ^ _MASCARA_FORMATO


def _bits_versao(versao: int) -> int:
    resto = _bch(versao << 12, _GERADOR_VERSAO, 13)
    return (versao << 12) | resto


def _gravar_formato(matriz: _Matriz, mascara: int) -> None:
    valor = _bits_formato(mascara)
    lado = matriz.lado
    for posicao in range(15):
        bit = (valor >> posicao) & 1
        if posicao < 6:
            matriz.definir(posicao, 8, bit)
        elif posicao == 6:
            matriz.definir(7, 8, bit)
        elif posicao == 7:
            matriz.definir(8, 8, bit)
        elif posicao == 8:
            matriz.definir(8, 7, bit)
        else:
            matriz.definir(8, 14 - posicao, bit)
        if posicao < 8:
            matriz.definir(8, lado - 1 - posicao, bit)
        else:
            matriz.definir(lado - 15 + posicao, 8, bit)
    matriz.definir(lado - 8, 8, 1)


def _gravar_versao(matriz: _Matriz) -> None:
    if matriz.versao < 7:
        return
    valor = _bits_versao(matriz.versao)
    lado = matriz.lado
    for posicao in range(18):
        bit = (valor >> posicao) & 1
        linha = posicao // 3
        coluna = posicao % 3
        matriz.definir(linha, lado - 11 + coluna, bit)
        matriz.definir(lado - 11 + coluna, linha, bit)


def _posicoes_de_dados(matriz: _Matriz):
    lado = matriz.lado
    coluna = lado - 1
    subindo = True
    while coluna > 0:
        if coluna == 6:  # a coluna de temporização vertical não recebe dados
            coluna -= 1
        faixa = range(lado - 1, -1, -1) if subindo else range(lado)
        for linha in faixa:
            for deslocamento in (0, 1):
                alvo = coluna - deslocamento
                if not matriz.reservado[linha][alvo]:
                    yield linha, alvo
        coluna -= 2
        subindo = not subindo


def _preencher_dados(matriz: _Matriz, codewords: list[int]) -> None:
    bits = [(octeto >> posicao) & 1 for octeto in codewords for posicao in range(7, -1, -1)]
    cursor = 0
    for linha, coluna in _posicoes_de_dados(matriz):
        valor = bits[cursor] if cursor < len(bits) else 0
        matriz.modulos[linha][coluna] = valor
        cursor += 1


_MASCARAS = (
    lambda i, j: (i + j) % 2 == 0,
    lambda i, j: i % 2 == 0,
    lambda i, j: j % 3 == 0,
    lambda i, j: (i + j) % 3 == 0,
    lambda i, j: (i // 2 + j // 3) % 2 == 0,
    lambda i, j: (i * j) % 2 + (i * j) % 3 == 0,
    lambda i, j: ((i * j) % 2 + (i * j) % 3) % 2 == 0,
    lambda i, j: ((i + j) % 2 + (i * j) % 3) % 2 == 0,
)


def _aplicar_mascara(matriz: _Matriz, mascara: int) -> list[list[int]]:
    condicao = _MASCARAS[mascara]
    resultado = [linha[:] for linha in matriz.modulos]
    for linha in range(matriz.lado):
        for coluna in range(matriz.lado):
            if not matriz.reservado[linha][coluna] and condicao(linha, coluna):
                resultado[linha][coluna] ^= 1
    return resultado


def _penalidade(modulos: list[list[int]]) -> int:
    lado = len(modulos)
    total = 0

    for eixo in range(2):
        for primeiro in range(lado):
            atual = -1
            repeticoes = 0
            for segundo in range(lado):
                valor = modulos[primeiro][segundo] if eixo == 0 else modulos[segundo][primeiro]
                if valor == atual:
                    repeticoes += 1
                else:
                    if repeticoes >= 5:
                        total += 3 + (repeticoes - 5)
                    atual = valor
                    repeticoes = 1
            if repeticoes >= 5:
                total += 3 + (repeticoes - 5)

    for linha in range(lado - 1):
        for coluna in range(lado - 1):
            bloco = (
                modulos[linha][coluna]
                + modulos[linha][coluna + 1]
                + modulos[linha + 1][coluna]
                + modulos[linha + 1][coluna + 1]
            )
            if bloco in (0, 4):
                total += 3

    padroes = ([1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0], [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1])
    for linha in range(lado):
        for coluna in range(lado - 10):
            trecho_h = modulos[linha][coluna : coluna + 11]
            trecho_v = [modulos[coluna + passo][linha] for passo in range(11)]
            if trecho_h in padroes:
                total += 40
            if trecho_v in padroes:
                total += 40

    escuros = sum(sum(linha) for linha in modulos)
    proporcao = escuros * 100 // (lado * lado)
    total += 10 * (abs(proporcao - 50) // 5)
    return total


def matriz_qrcode(conteudo: str) -> list[list[int]]:
    """Devolve a matriz final (1 = módulo escuro) para o texto informado."""
    dados = conteudo.encode("utf-8")
    versao = _escolher_versao(len(dados))
    codewords = _codewords(dados, versao)

    base = _Matriz(versao)
    _desenhar_localizador(base, 0, 0)
    _desenhar_localizador(base, 0, base.lado - 7)
    _desenhar_localizador(base, base.lado - 7, 0)
    _desenhar_alinhamento(base)
    _desenhar_temporizacao(base)
    _reservar_versao(base)
    _reservar_formato(base)
    _preencher_dados(base, codewords)

    melhor_pontuacao = None
    melhor_modulos = None
    for mascara in range(8):
        candidato = _aplicar_mascara(base, mascara)
        provisorio = _Matriz(versao)
        provisorio.modulos = candidato
        provisorio.reservado = base.reservado
        _gravar_formato(provisorio, mascara)
        _gravar_versao(provisorio)
        pontuacao = _penalidade(provisorio.modulos)
        if melhor_pontuacao is None or pontuacao < melhor_pontuacao:
            melhor_pontuacao = pontuacao
            melhor_modulos = provisorio.modulos
    return melhor_modulos


def qrcode_svg(conteudo: str, *, borda: int = 4, tamanho_alvo: int = 220) -> str:
    """Renderiza o QR Code como SVG embutido, sem imagem externa."""
    modulos = matriz_qrcode(conteudo)
    lado = len(modulos)
    total = lado + borda * 2
    caminho: list[str] = []
    for linha in range(lado):
        coluna = 0
        while coluna < lado:
            if modulos[linha][coluna]:
                inicio = coluna
                while coluna < lado and modulos[linha][coluna]:
                    coluna += 1
                caminho.append(f"M{inicio + borda} {linha + borda}h{coluna - inicio}v1h-{coluna - inicio}z")
            else:
                coluna += 1
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total}" '
        f'width="{tamanho_alvo}" height="{tamanho_alvo}" role="img" '
        f'aria-label="QR Code do documento fiscal" shape-rendering="crispEdges">'
        f'<rect width="{total}" height="{total}" fill="#ffffff"/>'
        f'<path d="{escape("".join(caminho))}" fill="#000000"/>'
        f"</svg>"
    )
