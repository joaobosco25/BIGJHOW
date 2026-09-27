"""Perfil EXPLICITO de ensaio integral, restrito a NFC-e 65/2 em 2026.
Nao atribui classificacao fiscal de producao ao cadastro do produto.
"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from lxml import etree

N='http://www.portalfiscal.inf.br/nfe'
D='http://www.w3.org/2000/09/xmldsig#'
def q(s):return '{'+N+'}'+s
def parse(data):
 r=etree.fromstring(data,etree.XMLParser(resolve_entities=False,no_network=True,load_dtd=False))
 if r.getroottree().docinfo.doctype:raise RuntimeError('DTD bloqueado.')
 return r
def text(r,path):return r.findtext('/'.join(q(p) for p in path.split('/')))
def money(v):return Decimal(v).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
def add(r,n,v=None):
 child=etree.SubElement(r,q(n))
 if v is not None:child.text=str(v)
 return child

def insert_rtc(root):
 """Adiciona IBS/CBS apenas ao caso comercial restrito e aprovado para ensaio."""
 inf=root.find(q('infNFe'))
 if inf is None or text(inf,'ide/tpAmb')!='2' or text(inf,'ide/mod')!='65':raise RuntimeError('Ensaio exclusivo de homologacao modelo 65.')
 if datetime.fromisoformat(text(inf,'ide/dhEmi')).year!=2026:raise RuntimeError('Perfil de aliquotas limitado a 2026.')
 if text(inf,'emit/CRT')!='3':raise RuntimeError('Perfil limitado a CRT 3.')
 dets=inf.findall(q('det'))
 if len(dets)!=1:raise RuntimeError('Recuperacao limitada a um item.')
 det=dets[0];prod=det.find(q('prod'));tax=det.find(q('imposto'))
 if prod is None or tax is None:raise RuntimeError('Produto ou imposto ausente.')
 if text(prod,'NCM')!='22021000' or text(prod,'CFOP')!='5405' or text(prod,'vProd')!='5.90':raise RuntimeError('Produto/CFOP/valor diferente do alvo verificado.')
 if det.find(q('vItem')) is not None or tax.find(q('IBSCBS')) is not None:raise RuntimeError('RTC ja existente: nao duplicar.')
 # Nao extrapolar formulas a itens com ST a recolher, desoneracao, servicos etc.
 for tag in ('vDesc','vFrete','vSeg','vOutro'):
  if Decimal(text(prod,tag) or '0')!=0:raise RuntimeError('Encargos/descontos fora do perfil restrito.')
 for element in tax.iter():
  name=etree.QName(element).localname
  if name in {'vICMS','vPIS','vCOFINS','vFCP','vICMSST','vII','vIPI','vICMSDeson','vISSQN','vICMSUFDest','vFCPUFDest','vICMSMono','vFCPST'} and Decimal(element.text or '0')!=0:
   raise RuntimeError('Tributo anterior nao zero; requer calculo especifico de base.')
 if any(tax.find(q(tag)) is not None for tag in ('IS','IPI','II','ISSQN','PISST','COFINSST','ICMSUFDest')):raise RuntimeError('Grupo fiscal fora do perfil restrito.')
 total=inf.find(q('total'))
 if total is None or text(total,'ICMSTot/vNF')!='5.90':raise RuntimeError('Total original divergente.')
 if total.find(q('IBSCBSTot')) is not None or total.find(q('vNFTot')) is not None:raise RuntimeError('Total RTC ja existe.')
 for tag in ('vBC','vICMS','vICMSDeson','vFCP','vBCST','vST','vFCPST','vFrete','vSeg','vDesc','vII','vIPI','vIPIDevol','vPIS','vCOFINS','vOutro'):
  if Decimal(text(total,'ICMSTot/'+tag) or '0')!=0:raise RuntimeError('Total de tributos/encargos fora do perfil restrito.')
 base=money(text(prod,'vProd'));uf=money(base*Decimal('.001'));mun=Decimal('0.00');cbs=money(base*Decimal('.009'))
 rtc=add(tax,'IBSCBS');add(rtc,'CST','000');add(rtc,'cClassTrib','000001')
 g=add(rtc,'gIBSCBS');add(g,'vBC',format(base,'.2f'))
 u=add(g,'gIBSUF');add(u,'pIBSUF','0.1000');add(u,'vIBSUF',format(uf,'.2f'))
 m=add(g,'gIBSMun');add(m,'pIBSMun','0.0000');add(m,'vIBSMun','0.00')
 add(g,'vIBS',format(uf+mun,'.2f'))
 c=add(g,'gCBS');add(c,'pCBS','0.9000');add(c,'vCBS',format(cbs,'.2f'))
 add(det,'vItem','5.90') # Em 2026 IBS/CBS nao se somam ao valor do item.
 t=add(total,'IBSCBSTot');add(t,'vBCIBSCBS',format(base,'.2f'))
 gi=add(t,'gIBS')
 for name,value,vt in [('gIBSUF',uf,'vIBSUF'),('gIBSMun',mun,'vIBSMun')]:
  a=add(gi,name);add(a,'vDif','0.00');add(a,'vDevTrib','0.00');add(a,vt,format(value,'.2f'))
 add(gi,'vIBS',format(uf+mun,'.2f'));add(gi,'vCredPres','0.00');add(gi,'vCredPresCondSus','0.00')
 gc=add(t,'gCBS');add(gc,'vDif','0.00');add(gc,'vDevTrib','0.00');add(gc,'vCBS',format(cbs,'.2f'));add(gc,'vCredPres','0.00');add(gc,'vCredPresCondSus','0.00')
 add(total,'vNFTot','5.90')
 return {'perfil':'ENSAIO_INTEGRAL_2026','CST':'000','cClassTrib':'000001','base':'5.90','IBS_UF':str(uf),'IBS_MUN':'0.00','CBS':str(cbs),'vItem':'5.90','vNFTot':'5.90'}

def schema_validate(raw):
 schema=etree.XMLSchema(etree.parse(str(Path(__file__).parent/'schemas_rtc_ensaio/nfe_v4.00.xsd'),etree.XMLParser(no_network=True,resolve_entities=False)))
 if not schema.validate(parse(raw)):
  # Somente tipo de erro e linha; mensagens XSD podem conter dados do XML.
  error=schema.error_log.last_error
  raise RuntimeError('XSD rejeitou candidato: '+error.type_name+'; linha '+str(error.line))

def preserve_original(original,candidate):
 """Retiradas as adicoes RTC, a unica mudanca permitida e data e assinatura."""
 a=parse(original);b=parse(candidate)
 for root in (a,b):
  for sig in root.findall('{'+D+'}Signature'):root.remove(sig)
 b.find(q('infNFe')+'/'+q('ide')+'/'+q('dhEmi')).text=text(a.find(q('infNFe')),'ide/dhEmi')
 for e in list(b.iter()):
  if etree.QName(e).localname in {'IBSCBS','IBSCBSTot','vItem','vNFTot'}:e.getparent().remove(e)
 if etree.tostring(a)!=etree.tostring(b):raise RuntimeError('Mudanca comercial nao permitida detectada.')


# Perfil explicitamente ativado pela instalacao deste pacote de homologacao.
# Nao e um cadastro tributario de producao.
from .xml_nfce import XmlFiscalErro
import base64,hashlib
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

def selecionar(estabelecimento, dados=None):
    if int(estabelecimento.empresa_id)!=1 or estabelecimento.provedor!='sefaz_direto':
        return False
    if (int(estabelecimento.ambiente),int(estabelecimento.modelo),int(estabelecimento.serie))!=(2,65,9):
        raise XmlFiscalErro('Perfil de ensaio RTC instalado: empresa 1 restrita a homologacao NFC-e serie 9. Producao bloqueada.')
    if estabelecimento.uf!='MG' or int(estabelecimento.crt)!=3:
        raise XmlFiscalErro('Perfil RTC restrito a MG / CRT 3.')
    if dados is not None:
        if (int(dados.ambiente),int(dados.modelo),int(dados.serie),int(dados.tipo_emissao))!=(2,65,9,1):
            raise XmlFiscalErro('Dados divergentes do perfil de ensaio RTC.')
    return True

def conferir_configuracao(configuracao):
    from urllib.parse import urlsplit
    for nome,servico in [('url_autorizacao','NFeAutorizacao4'),('url_consulta','NFeConsultaProtocolo4')]:
        u=urlsplit(configuracao.get(nome,''))
        if u.scheme!='https' or u.hostname!='hnfce.fazenda.mg.gov.br' or u.port not in (None,443) or u.username or u.password or u.query or u.fragment or u.path.rstrip('/')!='/nfce/services/'+servico:
            raise XmlFiscalErro('Ensaio exige endpoint de homologacao MG: '+nome)

def aplicar(root, perfis=None):
    """Aplica RTC somente com perfis fiscais explícitos e integralmente preenchidos.

    O antigo ensaio de uma única Coca fica desativado nesta release. Isso evita
    reutilizar um perfil experimental em outro produto. Toda venda homologada da
    empresa 1 precisa vir do catálogo legado conferido e carregar um perfil RTC
    por item; caso contrário, a emissão é bloqueada antes da transmissão.
    """
    if not perfis or not any(p is not None for p in perfis):
        raise XmlFiscalErro(
            "Produto sem perfil RTC conferido para homologação; emissão bloqueada antes do envio."
        )
    if any(p is None for p in perfis):
        raise XmlFiscalErro("Venda com perfis RTC parcialmente preenchidos.")
    from . import rtc_produtos
    return rtc_produtos.aplicar(root, perfis)

def conferir(raw):
    try:
        schema_validate(raw)
        conferir_assinaturas(raw)
    except XmlFiscalErro:raise
    except Exception as exc:
        raise XmlFiscalErro('Validacao RTC antes do envio falhou: '+type(exc).__name__) from exc

def conferir_assinaturas(raw):
    root=parse(raw)
    for note in root.iter(q('NFe')):
        if note.find('.//'+q('IBSCBS')) is None:continue
        inf=note.find(q('infNFe'));sigs=note.findall('{'+D+'}Signature')
        if inf is None or len(sigs)!=1:raise XmlFiscalErro('Assinatura RTC ausente ou ambigua.')
        sig=sigs[0];si=sig.find('{'+D+'}SignedInfo');refs=si.findall('{'+D+'}Reference')
        if len(refs)!=1 or refs[0].get('URI')!='#'+inf.get('Id',''):raise XmlFiscalErro('Referencia XMLDSig divergente.')
        ref=refs[0]
        def canon(e):return etree.tostring(parse(etree.tostring(e,with_tail=False)),method='c14n',exclusive=False,with_comments=False)
        digest=base64.b64encode(hashlib.sha1(canon(inf)).digest()).decode()
        if ref.findtext('{'+D+'}DigestValue')!=digest:raise XmlFiscalErro('Digest do XML RTC divergente.')
        cert=x509.load_der_x509_certificate(base64.b64decode(sig.findtext('.//{'+D+'}X509Certificate')))
        cert.public_key().verify(base64.b64decode(sig.findtext('{'+D+'}SignatureValue')),canon(si),padding.PKCS1v15(),hashes.SHA1())

def conferir_transporte(raw):
    try:conferir_assinaturas(raw)
    except XmlFiscalErro:raise
    except Exception as exc:raise XmlFiscalErro('Assinatura RTC invalida no SOAP final.') from exc
