#!/usr/bin/env python3
"""Genera el tablero CRM Solicitudes (solo leads desde FECHA_CORTE en adelante)."""
import os, json, re, html, sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get('ODOO_URL', 'https://latinbien.com').rstrip('/')
DB = os.environ.get('ODOO_DB', 'erp_production')
FECHA_CORTE = os.environ.get('FECHA_CORTE', '2026-10-22')

def cred():
    u = os.environ.get('ODOO_USER'); p = os.environ.get('ODOO_PASS')
    if not u or not p:
        ruta = os.path.join(BASE, 'name.env')
        if os.path.isfile(ruta):
            datos = {}
            with open(ruta, encoding='utf-8') as fh:
                for line in fh:
                    if '=' in line and not line.strip().startswith('#'):
                        k, v = line.rstrip('\n').split('=', 1)
                        datos[k.strip()] = v.strip().strip('"').strip("'")
            u = u or datos.get('ODOO_USER')
            p = p or datos.get('ODOO_PASS')
    if not u or not p:
        raise RuntimeError('Faltan credenciales ODOO_USER/ODOO_PASS')
    return u, p

def conectar():
    u, p = cred()
    import requests
    sess = requests.Session()
    r = sess.post(f'{URL}/web/session/authenticate', json={
        'jsonrpc': '2.0', 'method': 'call',
        'params': {'db': DB, 'login': u, 'password': p}, 'id': 1})
    r.raise_for_status()
    j = r.json()
    if 'error' in j: raise RuntimeError(j['error'])
    uid = j['result']['uid']
    def je(model, method, args=None, kwargs=None):
        payload = {'jsonrpc': '2.0', 'method': 'call',
                   'params': {'model': model, 'method': method,
                              'args': args or [], 'kwargs': kwargs or {}}, 'id': 2}
        rr = sess.post(f'{URL}/web/dataset/call_kw', json=payload)
        rr.raise_for_status()
        jj = rr.json()
        if 'error' in jj: raise RuntimeError(jj['error'])
        return jj['result']
    return uid, je

CAMPOS = ['id','name','create_date','contact_name','phone','email_from',
          'state_id','city','x_payment','x_valor_ingreso','x_inicial_disponible_moto',
          'x_tiempo_inicial','x_ingresos_proviene','x_linea_activa','x_operador',
          'x_zona','stage_id','user_id','description','x_form_origen']

def is_solicitud_form(lead):
    name = html.unescape(lead.get('name') or '')
    if re.match(r'^\s*(SLCA\s+)?Solicitud\s*-', name, re.I):
        if re.match(r'^\s*(SLCA\s+)?Solicitud\s+Motos\s*-', name, re.I):
            return False
        return True
    desc = html.unescape(lead.get('description') or '')
    if re.search(r'Categor[a-záéíóú]*:', desc, re.I):
        return True
    return False

def extract_niche(lead):
    desc = html.unescape(lead.get('description') or '')
    m = re.search(r'Categor[a-záéíóú]*:\s*([A-Za-zÁÉÍÓÚáéíóúñÑ ]+?)(?:<br|\n|</|$)', desc, re.I)
    if m:
        val = re.sub(r'[^A-Za-zÁÉÍÓÚáéíóúñÑ ]+$', '', m.group(1)).strip()
        if val: return val.title() if val.islower() or val.isupper() else val
    name = html.unescape(lead.get('name') or '')
    m = re.search(r'(?:SLCA\s+)?Solicitud\s*-\s*([A-Za-zÁÉÍÓÚáéíóúñÑ ]+)', name, re.I)
    if m:
        val = re.sub(r'[^A-Za-zÁÉÍÓÚáéíóúñÑ ]+$', '', m.group(1)).strip()
        if val: return val.title() if val.islower() or val.isupper() else val
    if re.search(r'Solicitud\s+Motos', name, re.I): return 'Motos'
    return 'Otros'

def state_name(v):
    return v[1] if isinstance(v,(list,tuple)) and len(v)>=2 else (v or '')
def stage_name(v):
    return v[1] if isinstance(v,(list,tuple)) and len(v)>=2 else (v or '')
def user_name(v):
    return v[1] if isinstance(v,(list,tuple)) and len(v)>=2 else (v or '')

def main():
    uid, je = conectar()
    print('Conectado uid', uid)
    leads_raw = []
    offset = 0
    while True:
        batch = je('crm.lead', 'search_read',
                   [[['x_form_origen', '=', 'moto_web']]],
                   {'fields': CAMPOS, 'limit': 500, 'offset': offset, 'order': 'id asc'})
        if not batch: break
        leads_raw.extend(batch)
        offset += len(batch)
        if len(batch) < 500: break
    print('origin moto_web:', len(leads_raw))

    leads = []
    for r in leads_raw:
        if not is_solicitud_form(r): continue
        if (r.get('create_date') or '')[:10] < FECHA_CORTE: continue
        niche = extract_niche(r)
        desc = html.unescape(r.get('description') or '')
        m = re.search(r'Producto deseado:\s*(.+?)(?:<br|\n|</|$)', desc, re.I)
        producto = re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else ''
        leads.append({
            'id': r.get('id'),
            'name': html.unescape(r.get('name') or ''),
            'create_date': r.get('create_date'),
            'contact_name': html.unescape(r.get('contact_name') or ''),
            'phone': r.get('phone') or '',
            'email_from': r.get('email_from') or '',
            'estado': state_name(r.get('state_id')),
            'ciudad': html.unescape(r.get('city') or ''),
            'pago': r.get('x_payment') or '',
            'ingreso': r.get('x_valor_ingreso') or 0,
            'inicial': r.get('x_inicial_disponible_moto') or 0,
            'tiempo_inicial': r.get('x_tiempo_inicial') or '',
            'ingresos_proviene': r.get('x_ingresos_proviene') or '',
            'linea_activa': r.get('x_linea_activa') or '',
            'operador': r.get('x_operador') or '',
            'zona': r.get('x_zona') or '',
            'etapa': stage_name(r.get('stage_id')),
            'asignado': user_name(r.get('user_id')),
            'nicho': niche,
            'producto': producto,
        })

    print('leads desde', FECHA_CORTE, ':', len(leads))

    data = {'generado': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'fecha_corte': FECHA_CORTE, 'total': len(leads), 'leads': leads}

    # write data json
    with open(os.path.join(BASE, 'data.json'), 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)

    # build index.html
    template_path = os.path.join(BASE, 'index_template.html')
    with open(template_path, 'r', encoding='utf-8') as fh:
        tpl = fh.read()
    json_str = json.dumps(data, ensure_ascii=False).replace('</script>', '<\\/script>')
    html_out = tpl.replace('__DATA_JSON__', json_str)
    # adjust title
    html_out = html_out.replace('Solicitudes Web - Leads Latinbien',
                                'CRM Solicitudes - Leads desde ' + FECHA_CORTE)
    with open(os.path.join(BASE, 'index.html'), 'w', encoding='utf-8') as fh:
        fh.write(html_out)
    print('Escrito index.html', len(html_out), 'bytes')

if __name__ == '__main__':
    main()