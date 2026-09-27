from __future__ import annotations

import re
import uuid
import zipfile
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Font, PatternFill, Protection
from openpyxl.worksheet.datavalidation import DataValidation

from ..models import InventarioLote, InventarioLoteItem


MAXIMO_BYTES_PLANILHA = 2 * 1024 * 1024
MAXIMO_BYTES_DESCOMPACTADOS = 20 * 1024 * 1024
MAXIMO_ARQUIVOS_INTERNOS = 100
_QTD = Decimal("0.001")
_PADRAO_QUANTIDADE = re.compile(r"^\d{1,12}(?:[.,]\d{1,3})?$")
_CABECALHOS = (
    "ID_DO_ITEM",
    "SKU_INTERNO",
    "CODIGO_EXTERNO",
    "PRODUTO",
    "CATEGORIA",
    "UNIDADE",
    "QUANTIDADE_CONTADA",
    "SITUACAO",
)


class PlanilhaInventarioInvalida(ValueError):
    pass


def normalizar_quantidade(valor) -> Decimal | None:
    if valor is None:
        return None
    if isinstance(valor, bool):
        raise PlanilhaInventarioInvalida("Quantidade inválida.")
    if isinstance(valor, (int, float, Decimal)):
        texto = str(valor)
    else:
        texto = str(valor).strip()
        if not texto:
            return None
    if not _PADRAO_QUANTIDADE.fullmatch(texto):
        raise PlanilhaInventarioInvalida(
            "Use somente números não negativos com até três casas decimais."
        )
    try:
        quantidade = Decimal(texto.replace(",", "."))
    except InvalidOperation as exc:
        raise PlanilhaInventarioInvalida("Quantidade inválida.") from exc
    if quantidade < 0 or quantidade > Decimal("999999999999.999"):
        raise PlanilhaInventarioInvalida("Quantidade fora do limite permitido.")
    return quantidade.quantize(_QTD, rounding=ROUND_HALF_UP)


def _texto_seguro(valor: object | None) -> str:
    texto = "" if valor is None else str(valor)
    if texto.startswith(("=", "+", "-", "@")):
        return "'" + texto
    return texto


def gerar_planilha(inventario: InventarioLote, itens: list[InventarioLoteItem]) -> bytes:
    workbook = Workbook()
    instrucoes = workbook.active
    instrucoes.title = "Instrucoes"
    instrucoes.append(["INVENTÁRIO EM LOTE — BIG JHOW"])
    instrucoes.append(
        ["Preencha somente QUANTIDADE_CONTADA na aba Inventario. Não altere IDs nem colunas."]
    )
    instrucoes.append(["Campo vazio mantém o produto como não contado."])
    instrucoes.append(["O valor 0 confirma que o estoque físico contado é zero."])
    instrucoes.append(["São aceitas até três casas decimais, usando vírgula ou ponto."])
    instrucoes.column_dimensions["A"].width = 105
    instrucoes["A1"].font = Font(bold=True, color="F2D58A", size=14)
    instrucoes["A1"].fill = PatternFill("solid", fgColor="541827")

    planilha = workbook.create_sheet("Inventario")
    planilha.append(_CABECALHOS)
    for item in itens:
        produto = item.produto
        quantidade = (
            float(item.quantidade_contada)
            if item.quantidade_contada is not None
            else None
        )
        planilha.append(
            [
                str(item.id),
                _texto_seguro(produto.sku_interno),
                _texto_seguro(produto.codigo_externo),
                _texto_seguro(produto.nome),
                _texto_seguro(produto.categoria),
                _texto_seguro(item.unidade_referencia),
                quantidade,
                "CONTADO" if quantidade is not None else "PENDENTE",
            ]
        )

    cabecalho = PatternFill("solid", fgColor="541827")
    for cell in planilha[1]:
        cell.font = Font(bold=True, color="F2D58A")
        cell.fill = cabecalho
        cell.protection = Protection(locked=True)
    for row in range(2, planilha.max_row + 1):
        for col in range(1, 9):
            planilha.cell(row, col).protection = Protection(locked=col != 7)
        planilha.cell(row, 7).number_format = "0.000"

    planilha.column_dimensions["A"].hidden = True
    planilha.column_dimensions["B"].width = 22
    planilha.column_dimensions["C"].width = 22
    planilha.column_dimensions["D"].width = 48
    planilha.column_dimensions["E"].width = 28
    planilha.column_dimensions["F"].width = 12
    planilha.column_dimensions["G"].width = 24
    planilha.column_dimensions["H"].width = 14
    planilha.freeze_panes = "B2"
    planilha.auto_filter.ref = f"A1:H{planilha.max_row}"
    planilha.sheet_view.showGridLines = False

    validacao = DataValidation(
        type="decimal",
        operator="between",
        formula1="0",
        formula2="999999999999.999",
        allow_blank=True,
    )
    validacao.error = "Informe zero ou uma quantidade positiva com até três casas decimais."
    validacao.errorTitle = "Quantidade inválida"
    validacao.prompt = "Vazio = não contado. Zero = contado e confirmado como zero."
    validacao.promptTitle = "Quantidade física"
    validacao.showErrorMessage = True
    validacao.showInputMessage = True
    planilha.add_data_validation(validacao)
    if planilha.max_row >= 2:
        validacao.add(f"G2:G{planilha.max_row}")
        planilha.conditional_formatting.add(
            f"G2:G{planilha.max_row}",
            FormulaRule(formula=["NOT(ISBLANK(G2))"], fill=PatternFill("solid", fgColor="D9EAD3")),
        )
    planilha.protection.sheet = True
    planilha.protection.selectLockedCells = False
    planilha.protection.selectUnlockedCells = True

    metadados = workbook.create_sheet("_METADADOS")
    metadados.append(["BIG_JHOW_INVENTARIO_V1", str(inventario.id), inventario.versao])
    metadados.sheet_state = "veryHidden"

    saida = BytesIO()
    workbook.save(saida)
    workbook.close()
    return saida.getvalue()


def _validar_conteiner_xlsx(conteudo: bytes) -> None:
    if not conteudo or len(conteudo) > MAXIMO_BYTES_PLANILHA:
        raise PlanilhaInventarioInvalida("A planilha está vazia ou excede 2 MB.")
    fluxo = BytesIO(conteudo)
    if not zipfile.is_zipfile(fluxo):
        raise PlanilhaInventarioInvalida("O arquivo não é uma planilha XLSX válida.")
    fluxo.seek(0)
    try:
        with zipfile.ZipFile(fluxo) as pacote:
            arquivos = pacote.infolist()
            if len(arquivos) > MAXIMO_ARQUIVOS_INTERNOS:
                raise PlanilhaInventarioInvalida("A estrutura da planilha é inválida.")
            total = 0
            for arquivo in arquivos:
                nome = arquivo.filename.replace("\\", "/")
                partes = nome.split("/")
                if nome.startswith("/") or ".." in partes:
                    raise PlanilhaInventarioInvalida("A estrutura da planilha é inválida.")
                total += arquivo.file_size
                if total > MAXIMO_BYTES_DESCOMPACTADOS:
                    raise PlanilhaInventarioInvalida("A planilha descompactada excede o limite.")
    except (zipfile.BadZipFile, OSError) as exc:
        raise PlanilhaInventarioInvalida("Não foi possível validar a planilha.") from exc


def ler_planilha(
    conteudo: bytes,
    inventario_id: uuid.UUID,
    versao: int,
    itens_esperados: set[uuid.UUID],
) -> dict[uuid.UUID, Decimal | None]:
    _validar_conteiner_xlsx(conteudo)
    try:
        workbook = load_workbook(
            BytesIO(conteudo), read_only=True, data_only=False, keep_links=False
        )
    except Exception as exc:
        raise PlanilhaInventarioInvalida("Não foi possível abrir a planilha XLSX.") from exc

    try:
        if "Inventario" not in workbook.sheetnames or "_METADADOS" not in workbook.sheetnames:
            raise PlanilhaInventarioInvalida("Use a planilha gerada por este inventário.")
        metadados = workbook["_METADADOS"]
        valores_meta = [metadados.cell(1, coluna).value for coluna in range(1, 4)]
        if any(metadados.cell(1, coluna).data_type == "f" for coluna in range(1, 4)):
            raise PlanilhaInventarioInvalida("Os metadados da planilha foram alterados.")
        try:
            meta_id = uuid.UUID(str(valores_meta[1]))
            meta_versao = int(valores_meta[2])
        except (TypeError, ValueError, AttributeError) as exc:
            raise PlanilhaInventarioInvalida("Os metadados da planilha são inválidos.") from exc
        if valores_meta[0] != "BIG_JHOW_INVENTARIO_V1" or meta_id != inventario_id:
            raise PlanilhaInventarioInvalida("A planilha pertence a outro inventário.")
        if meta_versao != versao:
            raise PlanilhaInventarioInvalida(
                "Esta planilha está desatualizada. Baixe uma nova versão antes de importar."
            )

        planilha = workbook["Inventario"]
        cabecalhos = tuple(planilha.cell(1, coluna).value for coluna in range(1, 9))
        if cabecalhos != _CABECALHOS:
            raise PlanilhaInventarioInvalida("As colunas obrigatórias foram alteradas.")

        resultado: dict[uuid.UUID, Decimal | None] = {}
        for numero_linha, linha in enumerate(
            planilha.iter_rows(min_row=2, max_col=8), start=2
        ):
            if all(celula.value is None for celula in linha):
                continue
            if any(celula.data_type == "f" for celula in linha):
                raise PlanilhaInventarioInvalida(
                    f"A linha {numero_linha} contém fórmula, o que não é permitido."
                )
            try:
                item_id = uuid.UUID(str(linha[0].value))
            except (ValueError, TypeError, AttributeError) as exc:
                raise PlanilhaInventarioInvalida(
                    f"O identificador da linha {numero_linha} é inválido."
                ) from exc
            if item_id in resultado:
                raise PlanilhaInventarioInvalida(
                    f"O item da linha {numero_linha} está duplicado."
                )
            if item_id not in itens_esperados:
                raise PlanilhaInventarioInvalida(
                    f"A linha {numero_linha} não pertence a este inventário."
                )
            try:
                resultado[item_id] = normalizar_quantidade(linha[6].value)
            except PlanilhaInventarioInvalida as exc:
                raise PlanilhaInventarioInvalida(
                    f"Quantidade inválida na linha {numero_linha}: {exc}"
                ) from exc

        if set(resultado) != itens_esperados:
            raise PlanilhaInventarioInvalida(
                "A planilha está incompleta. Não remova nem acrescente linhas; deixe a quantidade vazia."
            )
        return resultado
    finally:
        workbook.close()
