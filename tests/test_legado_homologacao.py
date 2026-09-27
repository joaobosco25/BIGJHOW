import json
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
import pytest
from lxml import etree as E
from .test_rtc_pagamentos import scenario, fake_http, sell
from bigjhow.extensions import db
from bigjhow.models import Produto, SaldoEstoque, CodigoProduto, Usuario, DocumentoFiscal
from bigjhow.services.caixa import iniciar_venda, adicionar_item, finalizar_venda
from bigjhow.services.fiscal import emissao,rtc_ensaio,rtc_produtos as rtc,legado_homologacao as legado
from bigjhow.services.fiscal.xml_nfce import XmlFiscalErro


def profile(code='000338',unit='KG',classe='200003'):
 cst='000' if classe=='000001' else '200';red='0' if cst=='000' else '60' if classe=='200034' else '100'
 return dict(codigo_xml=code,ncm='02013000',cest='1708400',cfop='5405',unidade=unit,unidade_tributavel=unit,origem='0',icms_cst='60',icms_aliquota='0',icms_reducao='0',pis_cst='06',pis_aliquota='0',cofins_cst='06',cofins_aliquota='0',rtc_cst=cst,rtc_classe=classe,pIBSUF='0.1',pIBSMun='0',pCBS='0.9',reducao_gIBSUF=red,reducao_gIBSMun=red,reducao_gCBS=red,base_pis_cofins='LIQUIDO_MENOS_ICMS')

def entry(p,prof):return {'conferido_homologacao':True,'identidade':{f:getattr(p,f) for f in ('sku_interno','codigo_externo','nome','unidade')},'perfil':prof}

def write_catalog(app,tmp_path,products):
 app.instance_path=str(tmp_path)
 c={'versao':1,'empresa_id':1,'cnpj':legado.CNPJ,'ambiente':2,'serie':9,'habilitado':True,'produtos':products}
 (tmp_path/legado.FILENAME).write_text(json.dumps(c));return c

@pytest.mark.parametrize('classe,expected',[('000001',('0.10','0.90')),('200003',('0.00','0.00')),('200014',('0.00','0.00')),('200034',('0.04','0.36'))])
def test_rates(classe,expected):
 r=rtc.calcular('100',profile(classe=classe));assert (str(r['uf']),str(r['cbs']))==expected

@pytest.mark.parametrize('change',['classe','cst','reducao','rate','nan'])
def test_profile_fail_closed(change):
 p=profile()
 if change=='classe':p['rtc_classe']='999999'
 if change=='cst':p['rtc_cst']='000'
 if change=='reducao':p['reducao_gCBS']='60'
 if change=='rate':p['pCBS']='1'
 if change=='nan':p['pCBS']='NaN'
 with pytest.raises(XmlFiscalErro):rtc.calcular('100',p)

@pytest.mark.parametrize('payments',[{'debito':'22.26'},{'credito':'22.26'},{'pix':'22.26'},{'dinheiro':'30.00'},{'dinheiro':'10.00','pix':'12.26'}])
def test_multiple_weight_payment_rounding_and_idempotency(scenario,tmp_path,monkeypatch,payments):
 app,p,e,cfg=scenario;e.cnpj=legado.CNPJ;p.sku_interno='CAR-00033';p.nome='ACEM';p.unidade='KG';p.pesado=True;p.preco_venda=Decimal('27.90')
 b=Produto(empresa_id=1,sku_interno='CAR-BEBIDA',codigo_externo='002',nome='BEBIDA TESTE',unidade='UN',preco_venda=Decimal('5.90'),ativo=True)
 b.saldo=SaldoEstoque(quantidade=10,aguardando_inventario=False);db.session.add(b);db.session.flush();db.session.add(CodigoProduto(empresa_id=1,produto_id=b.id,codigo='002',chave_busca='002',tipo='legado',principal=True));db.session.commit()
 bp=profile('1754156','UN','000001');bp['ncm']='22021000'
 write_catalog(app,tmp_path,{str(p.id):entry(p,profile()),str(b.id):entry(b,bp)})
 calls=fake_http(monkeypatch)
 u=db.session.scalar(db.select(Usuario).where(Usuario.usuario=='operador.carnes'))
 v=iniciar_venda(empresa_id=1,usuario_id=u.id)
 for code,qty in [('001','0.375'),('002','2')]:v=adicionar_item(empresa_id=1,venda_id=v.id,versao=v.versao,codigo_digitado=code,quantidade_digitada=qty,usuario_id=u.id)
 v,_=finalizar_venda(empresa_id=1,venda_id=v.id,versao=v.versao,pagamentos_digitados={m:(('22.26' if m=='dinheiro' and payments.get(m)=='30.00' else payments.get(m)),('30.00' if m=='dinheiro' and payments.get(m)=='30.00' else None)) for m in ('dinheiro','pix','debito','credito')},usuario_id=u.id)
 d,w=emissao.emitir_com_seguranca(empresa_id=1,venda_id=v.id,usuario_id=u.id)
 assert w is None,w
 assert d.status=='autorizado' and len(calls)==1
 n=next(rtc_ensaio.parse(calls[0]).iter(rtc.q('NFe')))
 assert n.findtext('.//'+rtc.q('vNFTot'))=='22.26'
 codes=[x.text for x in n.iter(rtc.q('cProd'))];assert codes==['000338','1754156']
 assert p.sku_interno=='CAR-00033' and db.session.get(SaldoEstoque,p.id).quantidade==Decimal('9.625')
 assert db.session.get(SaldoEstoque,b.id).quantidade==8
 again=emissao.emitir_venda(empresa_id=1,venda_id=v.id,usuario_id=u.id);assert again.id==d.id and len(calls)==1
 assert n.findtext('.//'+rtc.q('vTroco'))==('7.74' if payments=={'dinheiro':'30.00'} else None)

@pytest.mark.parametrize('problem',['missing','identity','production','disabled','unit','unreviewed'])
def test_catalog_blocks_before_number_and_network(scenario,tmp_path,monkeypatch,problem):
 app,p,e,cfg=scenario;e.cnpj=legado.CNPJ
 en=entry(p,profile('123456','UN','000001'));en['perfil']['ncm']='22021000'
 if problem=='identity':en['identidade']['nome']='OUTRO'
 if problem=='unit':en['perfil']['unidade']='KG'
 if problem=='unreviewed':en['conferido_homologacao']=False
 c=write_catalog(app,tmp_path,{str(p.id):en} if problem!='missing' else {'outro':en})
 if problem=='production':e.ambiente=1
 if problem=='disabled':c['habilitado']=False;(tmp_path/legado.FILENAME).write_text(json.dumps(c))
 db.session.commit();before=e.proximo_numero;calls=fake_http(monkeypatch);v,u=sell(app)
 d,w=emissao.emitir_com_seguranca(empresa_id=1,venda_id=v.id,usuario_id=u.id)
 assert d is None and w and not calls and e.proximo_numero==before
 assert not list(db.session.scalars(db.select(DocumentoFiscal)))

def test_pis_base_deducts_icms(scenario,tmp_path):
 app,p,e,cfg=scenario;pr=profile('123456','UN','000001');pr.update(icms_cst='00',cfop='5102',icms_aliquota='18',pis_cst='01',pis_aliquota='.65',cofins_cst='01',cofins_aliquota='3')
 cat={'produtos':{str(p.id):entry(p,pr)}}
 item=SimpleNamespace(produto_id=p.id,sku_interno=p.sku_interno,produto_nome=p.nome,unidade='UN',subtotal=Decimal('100'),quantidade=Decimal('1'),preco_unitario=Decimal('100'))
 fiscal=legado.montar_item(1,item,cat)
 assert fiscal.icms.valor==Decimal('18')
 assert fiscal.pis.base_calculo==Decimal('82') and fiscal.pis.valor==Decimal('.53') and fiscal.cofins.valor==Decimal('2.46')

def minimal_root(p,total='100.00',discount='0.00',icms='0.00',pis='0.00',cofins='0.00'):
 root=E.Element(rtc.q('NFe'),nsmap={None:rtc.N});inf=rtc.add(root,'infNFe');ide=rtc.add(inf,'ide')
 for n,v in [('tpAmb','2'),('mod','65'),('tpEmis','1'),('dhEmi','2026-09-23T12:00:00-03:00')]:rtc.add(ide,n,v)
 rtc.add(rtc.add(inf,'emit'),'CRT','3');det=rtc.add(inf,'det');prod=rtc.add(det,'prod')
 for n,v in [('cProd',p['codigo_xml']),('NCM',p['ncm']),('CFOP',p['cfop']),('uCom',p['unidade']),('vProd',total),('vDesc',discount)]:rtc.add(prod,n,v)
 tax=rtc.add(det,'imposto')
 for n,v in [('vICMS',icms),('vPIS',pis),('vCOFINS',cofins)]:rtc.add(tax,n,v)
 t=rtc.add(inf,'total');rtc.add(rtc.add(t,'ICMSTot'),'vNF',str(Decimal(total)-Decimal(discount)))
 return root

def test_discount_and_net_rtc_base():
 p=profile(classe='000001');root=minimal_root(p,discount='10.00',icms='16.20',pis='0.48',cofins='2.21')
 sums=rtc.aplicar(root,[p]);assert sums['base']==Decimal('71.11');assert sums['item']==Decimal('90.00');assert sums['uf']==Decimal('.07');assert sums['cbs']==Decimal('.64')

@pytest.mark.parametrize('bad',['year','prod','base','count','special'])
def test_engine_atomic_failure(bad):
 p=profile();root=minimal_root(p)
 if bad=='year':root.find('.//'+rtc.q('dhEmi')).text='2027-01-01T12:00:00-03:00'
 if bad=='prod':p['codigo_xml']='OUTRO'
 if bad=='base':root.find('.//'+rtc.q('vPIS')).text='101.00'
 if bad=='special':rtc.add(root.find('.//'+rtc.q('imposto')),'IPI')
 before=E.tostring(root)
 with pytest.raises(XmlFiscalErro):rtc.aplicar(root,[] if bad=='count' else [p])
 assert E.tostring(root)==before

def test_independent_xmlsec_validation(scenario,tmp_path,monkeypatch):
 xmlsec=pytest.importorskip('xmlsec')
 app,p,e,cfg=scenario;e.cnpj=legado.CNPJ
 pr=profile('123456','UN','200034');pr['ncm']='22021000'
 write_catalog(app,tmp_path,{str(p.id):entry(p,pr)});db.session.commit();calls=fake_http(monkeypatch)
 v,u=sell(app);d,w=emissao.emitir_com_seguranca(empresa_id=1,venda_id=v.id,usuario_id=u.id);assert w is None
 root=E.fromstring(calls[0]);xmlsec.tree.add_ids(root,['Id'])
 sig=xmlsec.tree.find_node(root,xmlsec.constants.NodeSignature)
 import base64
 cert=base64.b64decode(sig.findtext('.//{'+rtc_ensaio.D+'}X509Certificate'))
 ctx=xmlsec.SignatureContext();ctx.key=xmlsec.Key.from_memory(cert,xmlsec.constants.KeyDataFormatCertDer,None);ctx.verify(sig)
 root.find('.//'+rtc.q('vProd')).text='99.00'
 ctx2=xmlsec.SignatureContext();ctx2.key=xmlsec.Key.from_memory(cert,xmlsec.constants.KeyDataFormatCertDer,None)
 with pytest.raises(xmlsec.VerificationError):ctx2.verify(sig)
