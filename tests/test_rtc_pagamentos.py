from decimal import Decimal
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from contextlib import nullcontext
import pytest
from lxml import etree as E
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from bigjhow.extensions import db
from bigjhow.models import EstabelecimentoFiscal,Produto,ProdutoFiscal,SaldoEstoque,DocumentoFiscal,DocumentoFiscalXml,MovimentoEstoque,Usuario
from bigjhow.services.caixa import iniciar_venda,adicionar_item,finalizar_venda
from bigjhow.services.fiscal import emissao,rtc_ensaio as rtc
from bigjhow.services.fiscal.provedores import sefaz_direto
from bigjhow.services.fiscal.assinatura import Certificado

@pytest.fixture
def scenario(app_fiscal,monkeypatch):
 now=datetime(2026,9,22,12,0,tzinfo=timezone.utc)
 key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
 name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'FICTICIO')])
 c=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(2).not_valid_before(now-timedelta(days=365)).not_valid_after(now+timedelta(days=365)).sign(key,hashes.SHA256())
 cred=Certificado(key,c,None,c.not_valid_before_utc,c.not_valid_after_utc,'FICTICIO')
 monkeypatch.setattr(emissao,'carregar_certificado',lambda *a:cred)
 monkeypatch.setattr(emissao,'_agora',lambda *a:now)
 monkeypatch.setattr(sefaz_direto.SefazDireto,'_abrir_certificado',lambda *a:cred)
 monkeypatch.setattr(sefaz_direto,'_CertificadoTemporario',lambda *a:nullcontext(('FICTICIO','FICTICIO')))
 cfg={'certificado_caminho':'FICTICIO','certificado_senha':'FICTICIO','url_autorizacao':'https://hnfce.fazenda.mg.gov.br/nfce/services/NFeAutorizacao4','url_consulta':'https://hnfce.fazenda.mg.gov.br/nfce/services/NFeConsultaProtocolo4'}
 monkeypatch.setattr(emissao,'configuracao_provedor',lambda *a:cfg)
 with app_fiscal.app_context():
  e=db.session.get(EstabelecimentoFiscal,1);e.provedor='sefaz_direto';e.serie=9;e.crt=3;e.uf='MG';e.codigo_municipio=3170206;e.municipio='Uberlandia'
  e.url_consulta_qrcode='https://portalsped.fazenda.mg.gov.br/portalnfce/sistema/qrcode.xhtml';e.url_consulta_chave='https://hportalsped.fazenda.mg.gov.br/portalnfce'
  e.responsavel_cnpj=None
  p=db.session.get(Produto,app_fiscal.config['TEST_PRODUCT_ID']);p.sku_interno='CAR-01620';p.preco_venda=Decimal('5.90')
  f=db.session.scalar(db.select(ProdutoFiscal).where(ProdutoFiscal.produto_id==p.id));f.ncm='22021000';f.cfop='5405';f.icms_csosn=None;f.icms_cst='60'
  saldo=db.session.get(SaldoEstoque,p.id);saldo.quantidade=10;saldo.aguardando_inventario=False
  db.session.commit()
  yield app_fiscal,p,e,cfg

def sell(app, metodo="debito"):
 u=db.session.scalar(db.select(Usuario).where(Usuario.usuario=='operador.carnes'))
 v=iniciar_venda(empresa_id=1,usuario_id=u.id)
 v=adicionar_item(empresa_id=1,venda_id=v.id,versao=v.versao,codigo_digitado='001',quantidade_digitada='1',usuario_id=u.id)
 v,_=finalizar_venda(empresa_id=1,venda_id=v.id,versao=v.versao,pagamentos_digitados={m: (('5.90' if m == metodo else None),None) for m in ('dinheiro','pix','debito','credito')},usuario_id=u.id)
 return v,u

def fake_http(monkeypatch,timeout=False):
 calls=[]
 def post(url,**kw):
  raw=kw['data'];rtc.conferir_transporte(raw)
  r=rtc.parse(raw);n=next(r.iter(rtc.q('NFe')));rtc.conferir(E.tostring(n))
  calls.append(raw)
  if timeout:raise sefaz_direto._requests().Timeout('simulado')
  ret=E.Element(rtc.q('retEnviNFe'),nsmap={None:rtc.N});rtc.add(ret,'cStat','104')
  p=rtc.add(rtc.add(ret,'protNFe'),'infProt')
  for k,v in [('tpAmb','2'),('chNFe',n.find(rtc.q('infNFe')).get('Id')[3:]),('nProt','131260000000001'),('cStat','100'),('xMotivo','Autorizado teste simulado'),('dhRecbto','2026-09-22T12:00:00+00:00')]:rtc.add(p,k,v)
  rtc.add(p,'digVal',n.findtext('.//{'+rtc.D+'}DigestValue'))
  return SimpleNamespace(status_code=200,content=E.tostring(ret))
 import requests
 monkeypatch.setattr(requests,'post',post)
 return calls

@pytest.mark.parametrize("metodo,codigo", [("dinheiro","01"),("pix","17"),("debito","04"),("credito","03")])
def test_caixa_assina_autoriza_e_nao_duplica(scenario,monkeypatch,metodo,codigo):
 app,p,e,cfg=scenario;calls=fake_http(monkeypatch)
 v,u=sell(app,metodo)
 d,warning=emissao.emitir_com_seguranca(empresa_id=1,venda_id=v.id,usuario_id=u.id)
 assert warning is None,warning
 assert d.status=='autorizado'
 assert d.serie==9 and d.ambiente==2
 assert len(calls)==1
 saldo=db.session.get(SaldoEstoque,p.id);assert saldo.quantidade==9
 assert len(list(db.session.scalars(db.select(MovimentoEstoque))))==1
 same=emissao.emitir_venda(empresa_id=1,venda_id=v.id,usuario_id=u.id)
 assert same.id==d.id and len(calls)==1 and saldo.quantidade==9
 assert db.session.scalar(db.select(DocumentoFiscalXml).where(DocumentoFiscalXml.documento_id==d.id,DocumentoFiscalXml.tipo=='autorizado')) is not None
 n=next(rtc.parse(calls[0]).iter(rtc.q('NFe')))
 assert n.findtext('.//'+rtc.q('tPag'))==codigo
 assert (n.find('.//'+rtc.q('card')) is not None)==(codigo != '01')
 if codigo != '01': assert n.findtext('.//'+rtc.q('tpIntegra'))=='2'
 assert n.findtext('.//'+rtc.q('vCBS'))=='0.05'
 assert n.findtext('.//'+rtc.q('vNFTot'))=='5.90'

@pytest.mark.parametrize('change',['production','sku','quantity_profile','endpoint'])
def test_fora_perfil_nao_envia_nem_consume_numero(scenario,monkeypatch,change):
 app,p,e,cfg=scenario;calls=fake_http(monkeypatch)
 if change=='production':e.ambiente=1
 elif change=='sku':p.sku_interno='OUTRO'
 elif change=='quantity_profile':
  f=db.session.scalar(db.select(ProdutoFiscal).where(ProdutoFiscal.produto_id==p.id));f.ncm='02013000'
 else:cfg['url_autorizacao']='https://nfce.fazenda.mg.gov.br/nfce/services/NFeAutorizacao4'
 db.session.commit();before=e.proximo_numero
 v,u=sell(app);d,warning=emissao.emitir_com_seguranca(empresa_id=1,venda_id=v.id,usuario_id=u.id)
 assert d is None and warning
 assert calls==[] and e.proximo_numero==before
 assert not list(db.session.scalars(db.select(DocumentoFiscal)))
 assert db.session.get(SaldoEstoque,p.id).quantidade==9

def test_timeout_preserva_xml_e_nao_repete(scenario,monkeypatch):
 app,p,e,cfg=scenario;calls=fake_http(monkeypatch,timeout=True);v,u=sell(app)
 d,warning=emissao.emitir_com_seguranca(empresa_id=1,venda_id=v.id,usuario_id=u.id)
 assert d.status=='pendente' and warning and len(calls)==1
 assert db.session.scalar(db.select(DocumentoFiscalXml).where(DocumentoFiscalXml.documento_id==d.id,DocumentoFiscalXml.tipo=='enviado')) is not None
 same=emissao.emitir_venda(empresa_id=1,venda_id=v.id,usuario_id=u.id)
 assert same.id==d.id and len(calls)==1 and db.session.get(SaldoEstoque,p.id).quantidade==9

def test_segunda_venda_preserva_primeira_autorizada(scenario,monkeypatch):
 app,p,e,cfg=scenario;calls=fake_http(monkeypatch)
 v,u=sell(app);first,_=emissao.emitir_com_seguranca(empresa_id=1,venda_id=v.id,usuario_id=u.id)
 firstid=first.id
 oldxml=db.session.scalar(db.select(DocumentoFiscalXml).where(DocumentoFiscalXml.documento_id==firstid,DocumentoFiscalXml.tipo=='autorizado')).arquivo_xml
 oldkey=first.chave;oldprotocol=first.protocolo_autorizacao
 v2,u=sell(app);second,warning=emissao.emitir_com_seguranca(empresa_id=1,venda_id=v2.id,usuario_id=u.id)
 assert warning is None and second.status=='autorizado'
 assert first.numero==1 and second.numero==2 and first.id!=second.id
 assert first.chave==oldkey and first.protocolo_autorizacao==oldprotocol
 assert oldxml==db.session.scalar(db.select(DocumentoFiscalXml).where(DocumentoFiscalXml.documento_id==firstid,DocumentoFiscalXml.tipo=='autorizado')).arquivo_xml
 assert len(calls)==2 and db.session.get(SaldoEstoque,p.id).quantidade==8

def test_soap_adulterado_bloqueado_antes_do_http(scenario,monkeypatch):
 app,p,e,cfg=scenario;calls=fake_http(monkeypatch);v,u=sell(app)
 d,_=emissao.emitir_com_seguranca(empresa_id=1,venda_id=v.id,usuario_id=u.id)
 n=next(rtc.parse(calls[0]).iter(rtc.q('NFe')))
 bad=E.tostring(n).replace(b'<vProd>5.90</vProd>',b'<vProd>9.90</vProd>',1)
 provider=sefaz_direto.SefazDireto(cfg)
 with pytest.raises(Exception,match='Digest'):
  provider.autorizar(dados=SimpleNamespace(numero=d.numero,chave=d.chave,ambiente=2),xml_assinado=bad)
 assert len(calls)==1


def test_comprovante_exibe_aviso_e_escapa_html(scenario):
 from flask import flash, render_template
 from flask_login import login_user
 from bigjhow.models import Empresa
 app,p,e,cfg=scenario
 v,u=sell(app)
 with app.test_request_context('/'):
  login_user(u)
  flash('Bloqueio fiscal <script>NAO_EXECUTAR</script>', 'aviso')
  html=render_template('caixa/ticket.html', empresa=db.session.get(Empresa,1),venda=v,documento=None,emissao_ativa=True,iniciar_form=SimpleNamespace(hidden_tag=lambda:''))
  assert 'Bloqueio fiscal &lt;script&gt;' in html
  assert '<script>NAO_EXECUTAR' not in html
  assert 'não gerou documento fiscal' in html
