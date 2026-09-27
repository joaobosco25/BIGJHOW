from __future__ import annotations

import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from ..extensions import db
from ..models import Empresa, Produto, SaldoEstoque


EMPRESAS = [
    (1, "Big Jhow Carnes", "big-jhow-carnes"),
    (2, "Big Jhow Steak House", "big-jhow-steak-house"),
    (3, "Big Jhow Sushi", "big-jhow-sushi"),
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _diretorio() -> Path:
    return Path(__file__).resolve().parents[1] / "dados" / "carga_inicial"


def verificar_integridade() -> dict:
    directory = _diretorio()
    manifest = json.loads((directory / "manifesto.json").read_text(encoding="utf-8"))
    for filename, metadata in manifest["arquivos_carga"].items():
        path = directory / filename
        if not path.is_file() or _sha256(path) != metadata["sha256"]:
            raise RuntimeError(f"Falha de integridade na carga: {filename}")
    return manifest


def garantir_empresas() -> None:
    for company_id, name, slug in EMPRESAS:
        company = db.session.get(Empresa, company_id)
        if company is None:
            db.session.add(Empresa(id=company_id, nome=name, slug=slug, ativa=True))
        elif company.nome != name or company.slug != slug:
            raise RuntimeError(
                f"O ID {company_id} já pertence a outra empresa; carga interrompida com segurança."
            )
    db.session.flush()


def importar() -> dict[str, int]:
    manifest = verificar_integridade()
    garantir_empresas()
    imported = 0
    ignored = 0
    directory = _diretorio()

    for filename in manifest["arquivos_carga"]:
        with (directory / filename).open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                exists = db.session.scalar(
                    db.select(Produto.id).where(
                        Produto.origem_arquivo == row["origem_arquivo"],
                        Produto.origem_linha == int(row["origem_linha"]),
                    )
                )
                if exists:
                    ignored += 1
                    continue
                reasons = json.loads(row["motivos_revisao"])
                product = Produto(
                    empresa_id=int(row["empresa_id"]),
                    sku_interno=row["sku_interno"],
                    codigo_externo=row["codigo_externo"] or None,
                    nome=row["nome"],
                    categoria=row["categoria"],
                    unidade=row["unidade"],
                    pesado=row["pesado"] == "1",
                    preco_venda=Decimal(row["preco_venda"]),
                    revisao_pendente=row["revisao_pendente"] == "1",
                    motivos_revisao=reasons,
                    origem_arquivo=row["origem_arquivo"],
                    origem_linha=int(row["origem_linha"]),
                )
                product.saldo = SaldoEstoque(
                    quantidade=Decimal("0"), aguardando_inventario=True
                )
                db.session.add(product)
                imported += 1

    db.session.commit()
    return {"importados": imported, "ignorados": ignored}

