"""Cadastro de ensaio por produto, separado do cadastro de produção.

Arquivo instance/fiscal_legado_homologacao.json. Não muda SKU, estoque, preços
ou documentos antigos. Vinculação explícita por UUID e identidade do produto.
"""
import json
from pathlib import Path
from decimal import Decimal
from flask import current_app
from ...extensions import db
from ...models import Produto
from . import estruturas
from .rtc_produtos import dec, money, validar_perfil
from .xml_nfce import XmlFiscalErro

CNPJ = '11222333000181'
FILENAME = 'fiscal_legado_homologacao.json'

def _sem_chaves_repetidas(pares):
    data = {}
    for key, value in pares:
        if key in data: raise ValueError('Chave JSON duplicada.')
        data[key] = value
    return data

def carregar(estabelecimento):
    if int(estabelecimento.empresa_id)!=1:return None
    path=Path(current_app.instance_path)/FILENAME
    if not path.exists():return None
    try: data=json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_sem_chaves_repetidas)
    except (OSError,ValueError) as exc:raise XmlFiscalErro('Cadastro de homologação ilegível.') from exc
    if (data.get('versao'),data.get('empresa_id'),data.get('cnpj'),data.get('ambiente'),data.get('serie')) not in {(1,1,CNPJ,2,9),(2,1,CNPJ,2,9),(3,1,CNPJ,2,9)}:
        raise XmlFiscalErro('Identificação do cadastro de homologação divergente.')
    if (str(estabelecimento.cnpj),int(estabelecimento.ambiente),int(estabelecimento.modelo),int(estabelecimento.serie),int(estabelecimento.crt),estabelecimento.uf,estabelecimento.provedor)!=(CNPJ,2,65,9,3,'MG','sefaz_direto'):
        raise XmlFiscalErro('Cadastro legado exclusivo de homologação MG/CRT3/série9; produção bloqueada.')
    if data.get('habilitado') is not True:raise XmlFiscalErro('Cadastro legado presente, mas ainda não habilitado para homologação.')
    if not isinstance(data.get('produtos'),dict) or not data['produtos']:raise XmlFiscalErro('Cadastro de homologação sem produtos vinculados.')
    codes = []
    for value in data['produtos'].values():
        if not isinstance(value, dict) or not isinstance(value.get('perfil'), dict):
            raise XmlFiscalErro('Perfil inválido no cadastro de homologação.')
        if value.get('conferido_homologacao') is not True and value.get('classificacao_deterministica') is not True and value.get('classificacao_homologacao_fallback') is not True:
            raise XmlFiscalErro('Produto sem evidência fiscal suficiente nem fallback explícito de homologação.')
        codes.append(value['perfil'].get('codigo_xml'))
    if not all(codes) or len(set(codes)) != len(codes):
        raise XmlFiscalErro('Código XML ausente ou vinculado a vários produtos.')
    return data

def validar_vinculo(entry, produto):
    if entry.get('conferido_homologacao') is not True and entry.get('classificacao_deterministica') is not True and entry.get('classificacao_homologacao_fallback') is not True:
        raise XmlFiscalErro('Vínculo do produto sem evidência fiscal suficiente para homologação.')
    expected=entry.get('identidade',{})
    for field in ('sku_interno','codigo_externo','nome','unidade'):
        if expected.get(field)!=getattr(produto,field):raise XmlFiscalErro('Produto mudou desde a conferência: '+field)
    if produto.empresa_id!=1:raise XmlFiscalErro('Produto pertence a outra empresa.')
    p=entry['perfil'];validar_perfil(p)
    if p.get('unidade')!=produto.unidade or p.get('unidade_tributavel')!=produto.unidade:
        raise XmlFiscalErro('Unidade fiscal diverge da unidade atual do produto.')
    if p.get('icms_cst') not in {'00','40','60'} or p.get('cfop') != ('5405' if p['icms_cst']=='60' else '5102'):
        raise XmlFiscalErro('ICMS fora dos casos ST/T/IS confirmados pelo contador.')
    if dec(p.get('icms_reducao','0'))!=0:raise XmlFiscalErro('Redução de ICMS pendente de implementação.')
    if p.get('pis_cst') not in {'01','06'} or p.get('cofins_cst') not in {'01','06'}:raise XmlFiscalErro('PIS/COFINS fora do escopo conferido.')
    if p.get('origem')!='0':raise XmlFiscalErro('Origem diferente da configuração confirmada.')
    if p.get('base_pis_cofins')!='LIQUIDO_MENOS_ICMS':raise XmlFiscalErro('Regra de base PIS/COFINS não confirmada para o ensaio.')
    if len(p.get('ncm',''))!=8 or not p['ncm'].isdigit():raise XmlFiscalErro('NCM inválido.')
    if not p.get('codigo_xml') or len(p['codigo_xml'])>60:raise XmlFiscalErro('Código XML inválido.')
    for key in ('icms_aliquota','pis_aliquota','cofins_aliquota'):
        if not 0<=dec(p[key])<=100:raise XmlFiscalErro('Alíquota inválida.')
    return p

def montar_item(numero,item,catalogo):
    entry=catalogo['produtos'].get(str(item.produto_id))
    if entry is None:raise XmlFiscalErro('Produto ainda sem classificação RTC segura para homologação: '+item.sku_interno)
    produto=db.session.get(Produto,item.produto_id)
    if produto is None:raise XmlFiscalErro('Produto não encontrado.')
    p=validar_vinculo(entry,produto)
    if item.unidade!=p['unidade']:raise XmlFiscalErro('Unidade da venda diverge do cadastro conferido.')
    total=money(item.subtotal);qty=dec(item.quantidade);price=dec(item.preco_unitario)
    if qty<=0 or total<0 or price<0:raise XmlFiscalErro('Quantidade/preço inválidos.')
    if money(qty*price)!=total:raise XmlFiscalErro('Subtotal exige tratamento de desconto explícito; emissão bloqueada.')
    icms=estruturas.TributoICMS(origem=int(p['origem']),cst=p['icms_cst'],modalidade_bc=3,aliquota=dec(p['icms_aliquota']))
    if p['icms_cst']=='00':
        icms.base_calculo=total;icms.valor=money(total*icms.aliquota/100)
    elif p['icms_cst']=='40':
        icms.base_calculo=Decimal('0');icms.aliquota=Decimal('0');icms.valor=Decimal('0')
    base=total-icms.valor
    def contrib(prefix):
        cst=p[prefix+'_cst'];rate=dec(p[prefix+'_aliquota'])
        if cst=='06' and rate!=0:raise XmlFiscalErro('PIS/COFINS 06 com alíquota diferente de zero.')
        return estruturas.TributoContribuicao(cst=cst,base_calculo=base if cst=='01' else Decimal('0'),aliquota=rate,valor=money(base*rate/100) if cst=='01' else Decimal('0'))
    return estruturas.ItemFiscal(numero=numero,codigo_produto=p['codigo_xml'],descricao=item.produto_nome,ncm=p['ncm'],cest=p.get('cest') or None,cfop=p['cfop'],unidade_comercial=item.unidade,quantidade_comercial=qty,valor_unitario=price,valor_total=total,unidade_tributavel=item.unidade,quantidade_tributavel=qty,valor_unitario_tributavel=price,icms=icms,pis=contrib('pis'),cofins=contrib('cofins'),rtc_perfil=dict(p))
