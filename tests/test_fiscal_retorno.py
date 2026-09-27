from types import SimpleNamespace
import pytest
from lxml import etree
from bigjhow.services.fiscal.validacao_retorno import validar_autorizacao
from bigjhow.services.fiscal.provedores.base import RespostaFiscal, ProvedorIndisponivel
from bigjhow.services.fiscal.provedores.gateway import GatewayHttp

CHAVE = '1' * 44


def resposta():
    xml = f'''<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe><infNFe Id="NFe{CHAVE}"><ide><tpAmb>2</tpAmb></ide></infNFe></NFe><protNFe><infProt><chNFe>{CHAVE}</chNFe><tpAmb>2</tpAmb><nProt>123456789012345</nProt><cStat>100</cStat></infProt></protNFe></nfeProc>'''.encode()
    return RespostaFiscal(sucesso=True, codigo_status='100', mensagem='Autorizado', protocolo='123456789012345', chave=CHAVE, xml_autorizado=xml)


def test_identidade_autorizada_consistente():
    validar_autorizacao(resposta(), SimpleNamespace(chave=CHAVE, ambiente=2))


@pytest.mark.parametrize('campo,valor', [('xml_autorizado', None), ('protocolo', None), ('chave', '2'*44), ('codigo_status', '204')])
def test_resposta_incompleta_nao_autoriza(campo, valor):
    r = resposta()
    setattr(r, campo, valor)
    with pytest.raises(ProvedorIndisponivel):
        validar_autorizacao(r, SimpleNamespace(chave=CHAVE, ambiente=2))


def test_xml_outro_ambiente_nao_autoriza():
    r = resposta()
    r.xml_autorizado = r.xml_autorizado.replace(b'<tpAmb>2</tpAmb>', b'<tpAmb>1</tpAmb>')
    with pytest.raises(ProvedorIndisponivel):
        validar_autorizacao(r, SimpleNamespace(chave=CHAVE, ambiente=2))


def test_gateway_status_autorizado_nao_confirma_cancelamento(monkeypatch):
    from tests.test_fiscal_nucleo import _documento
    provedor = GatewayHttp({})
    monkeypatch.setattr(provedor, '_chamar', lambda *args: {'status': 'autorizado'})
    r = provedor.cancelar(chave=_documento().chave, protocolo='123', justificativa='Cancelamento solicitado pelo cliente', cnpj='11222333000181', ambiente=2)
    assert r.sucesso is False


def test_sefaz_direto_consulta_protocolo_autorizado(monkeypatch):
    from datetime import datetime, timezone
    from bigjhow.services.fiscal.chave import montar_chave
    from bigjhow.services.fiscal.provedores.sefaz_direto import SefazDireto

    chave = montar_chave(
        codigo_uf=31,
        emissao=datetime(2026, 9, 18, tzinfo=timezone.utc),
        cnpj='11222333000181',
        modelo=65,
        serie=1,
        numero=123,
        tipo_emissao=1,
        codigo_numerico=12345678,
    )
    xml = f'''<retConsSitNFe xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
      <tpAmb>2</tpAmb><verAplic>TESTE</verAplic><cStat>100</cStat><xMotivo>Autorizado</xMotivo>
      <protNFe versao="4.00"><infProt><tpAmb>2</tpAmb><chNFe>{chave}</chNFe>
      <dhRecbto>2026-09-18T12:00:00-03:00</dhRecbto><nProt>131260000000001</nProt>
      <cStat>100</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo></infProt></protNFe>
    </retConsSitNFe>'''.encode()
    provedor = SefazDireto({'url_consulta': 'https://exemplo.invalid/NFeConsultaProtocolo4'})
    monkeypatch.setattr(provedor, '_enviar', lambda url, corpo: etree.fromstring(xml))

    resposta = provedor.consultar_chave(chave=chave, ambiente=2)

    assert resposta.sucesso is True
    assert resposta.codigo_status == '100'
    assert resposta.protocolo == '131260000000001'
    assert resposta.chave == chave
    assert b'<consSitNFe' in resposta.xml_enviado
    assert b'<chNFe>' + chave.encode() + b'</chNFe>' in resposta.xml_enviado


def test_sefaz_direto_consulta_protocolo_nao_encontrado(monkeypatch):
    from datetime import datetime, timezone
    from bigjhow.services.fiscal.chave import montar_chave
    from bigjhow.services.fiscal.provedores.sefaz_direto import SefazDireto

    chave = montar_chave(
        codigo_uf=31,
        emissao=datetime(2026, 9, 18, tzinfo=timezone.utc),
        cnpj='11222333000181',
        modelo=65,
        serie=1,
        numero=124,
        tipo_emissao=1,
        codigo_numerico=12345679,
    )
    xml = b'''<retConsSitNFe xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
      <tpAmb>2</tpAmb><cStat>217</cStat><xMotivo>NF-e nao consta na base de dados da SEFAZ</xMotivo>
    </retConsSitNFe>'''
    provedor = SefazDireto({'url_consulta': 'https://exemplo.invalid/NFeConsultaProtocolo4'})
    monkeypatch.setattr(provedor, '_enviar', lambda url, corpo: etree.fromstring(xml))

    resposta = provedor.consultar_chave(chave=chave, ambiente=2)

    assert resposta.sucesso is False
    assert resposta.codigo_status == '217'
    assert resposta.protocolo is None
    assert resposta.chave == chave


def test_sefaz_direto_exige_url_consulta(monkeypatch):
    from bigjhow.services.fiscal.provedores.sefaz_direto import SefazDireto

    provedor = SefazDireto({
        'url_autorizacao': 'https://exemplo.invalid/a',
        'url_evento': 'https://exemplo.invalid/e',
        'url_inutilizacao': 'https://exemplo.invalid/i',
        'certificado_caminho': '/arquivo/que/nao/sera/aberto.pfx',
        'certificado_senha': 'segredo',
    })
    monkeypatch.setattr(provedor, '_abrir_certificado', lambda: SimpleNamespace(vencido=False, dias_para_vencer=999))

    pendencias = provedor.conferir_configuracao()

    assert any('NFeConsultaProtocolo4' in item for item in pendencias)
