"""RTC por item, para ensaios de 2026. Não classifica produtos nem libera produção.

Implementa operações comuns com ICMS/PIS/COFINS e desconto. Outros grupos
são bloqueados explicitamente. Perfis vêm de cadastro revisado, não do NCM.
Referência: NT 2025.002 v1.51, UB16-10, UB18-10 e VB01-10.
"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from lxml import etree
from .xml_nfce import XmlFiscalErro

N = 'http://www.portalfiscal.inf.br/nfe'
def q(name): return f'{{{N}}}{name}'
def text(node, path): return node.findtext('/'.join(q(p) for p in path.split('/')), default='')
def dec(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise XmlFiscalErro('Valor decimal inválido no perfil RTC.') from exc
    if not result.is_finite(): raise XmlFiscalErro('Valor decimal não finito.')
    return result

def money(value): return dec(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
def add(parent, name, value=None):
    node = etree.SubElement(parent, q(name))
    if value is not None: node.text = str(value)
    return node

def validar_perfil(p):
    # Escopo observado nos XMLs do açougue. Não autoriza uso indiscriminado.
    classes = {'000001': ('000', Decimal('0')), '200003': ('200', Decimal('100')),
               '200014': ('200', Decimal('100')), '200034': ('200', Decimal('60')),
               '200035': ('200', Decimal('60'))}
    if p.get('rtc_classe') not in classes: raise XmlFiscalErro('Classificação RTC não implementada.')
    cst, reduction = classes[p['rtc_classe']]
    if p.get('rtc_cst') != cst: raise XmlFiscalErro('CST e cClassTrib incompatíveis.')
    for group, rate, expected in [('gIBSUF','pIBSUF','0.1'), ('gIBSMun','pIBSMun','0'), ('gCBS','pCBS','0.9')]:
        if dec(p.get(rate)) != Decimal(expected): raise XmlFiscalErro('Alíquota RTC fora do escopo 2026.')
        if dec(p.get('reducao_'+group)) != reduction: raise XmlFiscalErro('Redução RTC divergente do perfil.')
    return cst

def calcular(base, p):
    cst = validar_perfil(p)
    base = money(base)
    if base < 0: raise XmlFiscalErro('Base RTC negativa.')
    result = {'base':base, 'cst':cst, 'classe':p['rtc_classe']}
    for group, rate, name in [('gIBSUF','pIBSUF','uf'), ('gIBSMun','pIBSMun','mun'), ('gCBS','pCBS','cbs')]:
        effective = (dec(p[rate]) * (1 - dec(p['reducao_'+group])/100)).quantize(Decimal('.0001'),rounding=ROUND_HALF_UP)
        result[name] = money(base * effective / 100)
        result[name+'_efetiva'] = effective
    result['ibs'] = result['uf'] + result['mun']
    return result

def aplicar(root, perfis, *, ambiente='2'):
    """Valida e calcula em cópia; só altera a árvore original após sucesso total.

    O default permanece homologação. Produção precisa solicitar
    explicitamente ambiente='1' e passa por guardas adicionais.
    """
    ambiente = str(ambiente)

    if ambiente not in {'1', '2'}:
        raise XmlFiscalErro('Ambiente RTC inválido.')

    candidate = deepcopy(root)
    inf = candidate.find(q('infNFe'))

    if inf is None:
        raise XmlFiscalErro('infNFe ausente.')

    contexto = (
        text(inf,'ide/tpAmb'),
        text(inf,'ide/mod'),
        text(inf,'ide/tpEmis'),
        text(inf,'emit/CRT'),
    )

    if contexto != (ambiente,'65','1','3'):
        raise XmlFiscalErro(
            'RTC fora do ambiente/modelo/tipo de emissão/CRT permitido.'
        )

    if ambiente == '1':
        if text(inf,'ide/serie') != '1':
            raise XmlFiscalErro(
                'RTC de produção restrito à série 1.'
            )

        if text(inf,'ide/cUF') != '31':
            raise XmlFiscalErro(
                'RTC de produção restrito a Minas Gerais.'
            )
    if datetime.fromisoformat(text(inf,'ide/dhEmi')).year != 2026: raise XmlFiscalErro('RTC limitado ao ano 2026.')
    if candidate.find(q('Signature')) is not None or candidate.find('{http://www.w3.org/2000/09/xmldsig#}Signature') is not None:
        raise XmlFiscalErro('RTC deve ser calculado antes da assinatura.')
    dets = inf.findall(q('det'))
    if not dets or len(dets) != len(perfis): raise XmlFiscalErro('Perfis RTC não correspondem aos itens.')
    sums = {k:Decimal('0.00') for k in ('base','uf','mun','ibs','cbs','item')}
    for det,p in zip(dets,perfis):
        prod=det.find(q('prod'));tax=det.find(q('imposto'))
        if prod is None or tax is None: raise XmlFiscalErro('Item sem produto/imposto.')
        for k,tag in [('codigo_xml','cProd'),('ncm','NCM'),('cfop','CFOP'),('unidade','uCom')]:
            if text(prod,tag) != p[k]: raise XmlFiscalErro('Perfil não corresponde ao item: '+tag)
        if tax.find(q('IBSCBS')) is not None or det.find(q('vItem')) is not None: raise XmlFiscalErro('RTC já informado.')
        if any(tax.find(q(tag)) is not None for tag in ('IS','IPI','II','ISSQN','PISST','COFINSST','ICMSUFDest')):
            raise XmlFiscalErro('Grupo tributário fora do escopo RTC implementado.')
        for node in tax.iter():
            if etree.QName(node).localname in {'vICMSST','vFCPST','vICMSDeson','vICMSMono','vICMSMonoReten','vFCP'} and dec(node.text or '0') != 0:
                raise XmlFiscalErro('Tributo especial exige tratamento específico.')
        item = money(text(prod,'vProd')) - money(text(prod,'vDesc') or '0')
        for tag in ('vFrete','vSeg','vOutro'): item += money(text(prod,tag) or '0')
        if item < 0: raise XmlFiscalErro('Desconto superior ao valor do item.')
        subtract = sum((dec(n.text or '0') for n in tax.iter() if etree.QName(n).localname in {'vICMS','vPIS','vCOFINS'}),Decimal('0'))
        result = calcular(item-subtract,p)
        rtc=add(tax,'IBSCBS');add(rtc,'CST',result['cst']);add(rtc,'cClassTrib',result['classe']);g=add(rtc,'gIBSCBS');add(g,'vBC',f"{result['base']:.2f}")
        for group,rate,value,name in [('gIBSUF','pIBSUF','vIBSUF','uf'),('gIBSMun','pIBSMun','vIBSMun','mun'),('gCBS','pCBS','vCBS','cbs')]:
            if name=='cbs':add(g,'vIBS',f"{result['ibs']:.2f}")
            node=add(g,group);add(node,rate,f'{dec(p[rate]):.4f}')
            if result['cst']=='200':
                red=add(node,'gRed');add(red,'pRedAliq',f"{dec(p['reducao_'+group]):.4f}");add(red,'pAliqEfet',f"{result[name+'_efetiva']:.4f}")
            add(node,value,f'{result[name]:.2f}')
        # vItem é posterior a infAdProd/obsItem no schema; append respeita essa ordem.
        add(det,'vItem',f'{item:.2f}')
        for k in ('base','uf','mun','ibs','cbs'):sums[k]+=result[k]
        sums['item']+=item
    total=inf.find(q('total'))
    if total is None or total.find(q('IBSCBSTot')) is not None or total.find(q('vNFTot')) is not None:raise XmlFiscalErro('Total RTC ausente/duplicado.')
    if money(text(total,'ICMSTot/vNF'))!=sums['item']:raise XmlFiscalErro('Total dos itens não corresponde ao vNF.')
    t=add(total,'IBSCBSTot');add(t,'vBCIBSCBS',f"{sums['base']:.2f}");ibs=add(t,'gIBS')
    for group,name,value in [('gIBSUF','uf','vIBSUF'),('gIBSMun','mun','vIBSMun')]:
        node=add(ibs,group);add(node,'vDif','0.00');add(node,'vDevTrib','0.00');add(node,value,f'{sums[name]:.2f}')
    add(ibs,'vIBS',f"{sums['ibs']:.2f}");add(ibs,'vCredPres','0.00');add(ibs,'vCredPresCondSus','0.00')
    cbs=add(t,'gCBS');add(cbs,'vDif','0.00');add(cbs,'vDevTrib','0.00');add(cbs,'vCBS',f"{sums['cbs']:.2f}");add(cbs,'vCredPres','0.00');add(cbs,'vCredPresCondSus','0.00');add(total,'vNFTot',f"{sums['item']:.2f}")
    old=root.find(q('infNFe'));root.replace(old,inf)
    return sums
