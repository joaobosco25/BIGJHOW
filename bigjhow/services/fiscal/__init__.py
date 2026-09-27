"""Módulo fiscal do Big Jhow.

Organização:

``chave``       chave de acesso de 44 dígitos e dígito verificador
``estruturas``  dados do documento, sem dependência de banco
``xml_nfce``    montagem do XML NF-e / NFC-e 4.00
``assinatura``  XMLDSig com certificado A1
``qrcode_nfce`` conteúdo do QR Code (NT 2017.001)
``qrcode_svg``  desenho do QR Code, sem biblioteca externa
``provedores``  as rotas de emissão intercambiáveis
``emissao``     orquestração a partir de uma venda do caixa
``sequencia``   numeração e detecção de lacunas (herdada do TPA)
``importador``  leitura do acervo XML do servidor antigo
``cadastro``    configuração do estabelecimento e classificação de produto
``danfe``       representação impressa do documento
``segredos``    cifragem das credenciais em repouso
"""

from __future__ import annotations

__all__ = [
    "assinatura",
    "cadastro",
    "chave",
    "danfe",
    "emissao",
    "estruturas",
    "importador",
    "provedores",
    "qrcode_nfce",
    "qrcode_svg",
    "segredos",
    "sequencia",
    "uf",
    "xml_nfce",
]
