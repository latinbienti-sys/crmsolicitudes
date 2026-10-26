#!/usr/bin/env python3
"""Genera el tablero CRM Solicitudes (solo leads desde FECHA_CORTE en adelante)."""
import os, json, re, html, sys
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get('ODOO_URL', 'https://latinbien.com').rstrip('/')
DB = os.environ.get('ODOO_DB', 'erp_production')
FECHA_CORTE = os.environ.get('FECHA_CORTE', '2026-10-22')
TZ_OFFSET_H = int(os.environ.get('ODOO_TZ_OFFSET', '-4'))

def a_local(fecha_odoo):
    try:
        d = datetime.strptime((fecha_odoo or '')[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return fecha_odoo or ''
    loc = d + timedelta(hours=TZ_OFFSET_H)
    return loc.strftime('%Y-%m-%d %H:%M:%S')

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
# Campos estandar de Odoo para ganados/perdidos; se piden solo si existen en esta version
CAMPOS_CIERRE = ['active','lost_reason_id','probability','date_closed','date_last_stage_update']

def dias_entre(inicio, fin):
    try:
        a = datetime.strptime((inicio or '')[:19], '%Y-%m-%d %H:%M:%S')
        b = datetime.strptime((fin or '')[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None
    return round(max((b - a).total_seconds(), 0) / 86400, 1)

# Ids de etapas marcadas como ganadas en crm.stage (se llena en main)
ETAPAS_GANADAS = set()
RE_ETAPA_PERDIDA = re.compile(r'perdid|lost|descartad|rechazad|no\s+interesad|cancelad', re.I)
RE_ETAPA_GANADA = re.compile(r'ganad|won|vendid|cerrad[oa]\s+ganad', re.I)

def situacion(r):
    etapa = stage_name(r.get('stage_id')) or ''
    stage_id = r['stage_id'][0] if isinstance(r.get('stage_id'), (list, tuple)) else None
    # Perdido: archivado en Odoo (active=False) o en una etapa de perdida
    if r.get('active') is False or RE_ETAPA_PERDIDA.search(etapa): return 'Perdido'
    if stage_id in ETAPAS_GANADAS or (r.get('probability') or 0) >= 100 or RE_ETAPA_GANADA.search(etapa):
        return 'Ganado'
    return 'Abierto'

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

def estado_crm(r, create_local):
    """Etapa, ejecutivo y cierre de un registro crm.lead."""
    sit = situacion(r)
    # Fecha de cierre: date_closed; si Odoo no la lleno, el ultimo cambio de etapa
    cierre = (r.get('date_closed') or r.get('date_last_stage_update') or '') if sit != 'Abierto' else ''
    return {
        'etapa': stage_name(r.get('stage_id')),
        'asignado': user_name(r.get('user_id')),
        'situacion': sit,
        'motivo_perdida': user_name(r.get('lost_reason_id')),
        'fecha_cierre': a_local(cierre) if cierre else '',
        'dias_cierre': dias_entre(create_local, a_local(cierre)) if cierre else None,
        'ultimo_movimiento': a_local(r.get('date_last_stage_update')) if r.get('date_last_stage_update') else '',
    }

def rastrear(je, campos, lead):
    """Busca en el CRM (solo lectura) un lead que ya no sale como solicitud del formulario.
    Devuelve (registro o None, explicacion)."""
    todos = ['active', 'in', [True, False]]
    kw = {'fields': campos, 'limit': 1, 'order': 'id desc', 'context': {'active_test': False}}
    recs = je('crm.lead', 'search_read', [[['id', '=', lead['id']], todos]], kw)
    if recs:
        r = recs[0]
        nombre = html.unescape(r.get('name') or '')
        if r.get('x_form_origen') != 'moto_web':
            return r, f'Sigue en el CRM (#{r["id"]}) pero le cambiaron el origen del formulario'
        return r, f'Sigue en el CRM (#{r["id"]}), renombrado a "{nombre}"'
    email = (lead.get('email_from') or '').strip()
    if email:
        recs = je('crm.lead', 'search_read', [[['email_from', '=ilike', email], ['id', '!=', lead['id']], todos]], kw)
        if recs:
            r = recs[0]
            return r, f'El lead original ya no existe (fusionado o eliminado); el cliente esta en #{r["id"]} "{html.unescape(r.get("name") or "")}" (por email)'
    digitos = re.sub(r'\D', '', lead.get('phone') or '')[-7:]
    if len(digitos) == 7:
        recs = je('crm.lead', 'search_read', [[['phone', 'ilike', digitos], ['id', '!=', lead['id']], todos]], kw)
        if recs:
            r = recs[0]
            return r, f'El lead original ya no existe (fusionado o eliminado); el cliente esta en #{r["id"]} "{html.unescape(r.get("name") or "")}" (por telefono)'
    return None, 'No se encuentra en el CRM (eliminado)'

def main():
    uid, je = conectar()
    print('Conectado uid', uid)
    existentes = je('crm.lead', 'fields_get', [CAMPOS_CIERRE], {'attributes': ['type']})
    campos = CAMPOS + [c for c in CAMPOS_CIERRE if c in existentes]
    print('campos de cierre disponibles:', [c for c in CAMPOS_CIERRE if c in existentes])
    campos_etapa = ['name', 'sequence'] + (['is_won'] if 'is_won' in je('crm.stage', 'fields_get', [['is_won']], {'attributes': ['type']}) else [])
    etapas = je('crm.stage', 'search_read', [[]], {'fields': campos_etapa, 'order': 'sequence asc, id asc'})
    ETAPAS_GANADAS.update(e['id'] for e in etapas if e.get('is_won'))
    print('etapas CRM:', [(e['name'], bool(e.get('is_won'))) for e in etapas])
    leads_raw = []
    offset = 0
    while True:
        # Incluye archivados (perdidos); solo lectura
        batch = je('crm.lead', 'search_read',
                   [[['x_form_origen', '=', 'moto_web'], ['active', 'in', [True, False]]]],
                   {'fields': campos, 'limit': 500, 'offset': offset, 'order': 'id asc',
                    'context': {'active_test': False}})
        if not batch: break
        leads_raw.extend(batch)
        offset += len(batch)
        if len(batch) < 500: break
    print('origin moto_web:', len(leads_raw))

    leads = []
    for r in leads_raw:
        if not is_solicitud_form(r): continue
        fecha_local = a_local(r.get('create_date'))
        if fecha_local[:10] < FECHA_CORTE: continue
        niche = extract_niche(r)
        desc = html.unescape(r.get('description') or '')
        m = re.search(r'Producto deseado:\s*(.+?)(?:<br|\n|</|$)', desc, re.I)
        producto = re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else ''
        leads.append({
            'id': r.get('id'),
            'name': html.unescape(r.get('name') or ''),
            'create_date': fecha_local,
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
            **estado_crm(r, fecha_local),
            'en_formulario': True,
            'rastreo': '',
            'nicho': niche,
            'producto': producto,
        })

    # Historico: las solicitudes que ya salieron antes (data.json previo) y hoy no devuelve
    # la consulta se rastrean en el CRM para no perderlas del tablero
    ruta_data = os.path.join(BASE, 'data.json')
    previos = []
    if os.path.isfile(ruta_data):
        with open(ruta_data, encoding='utf-8') as fh:
            previos = json.load(fh).get('leads', [])
    actuales = {l['id'] for l in leads}
    for p in previos:
        if p['id'] in actuales: continue
        r, nota = rastrear(je, campos, p)
        p = {**p, **(estado_crm(r, p['create_date']) if r else
                     {'situacion': 'No encontrado', 'fecha_cierre': '', 'dias_cierre': None}),
             'en_formulario': False, 'rastreo': nota}
        leads.append(p)
    leads.sort(key=lambda l: l['id'])

    print('leads desde', FECHA_CORTE, ':', len(leads),
          {s: sum(1 for l in leads if l['situacion'] == s) for s in ('Abierto', 'Ganado', 'Perdido', 'No encontrado')},
          'rastreados:', sum(1 for l in leads if not l['en_formulario']))

    data = {'generado': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'fecha_corte': FECHA_CORTE, 'total': len(leads),
            'etapas_crm': [{'nombre': e['name'], 'ganada': bool(e.get('is_won'))} for e in etapas],
            'leads': leads}

    with open(os.path.join(BASE, 'data.json'), 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)

    template_path = os.path.join(BASE, 'index_template.html')
    with open(template_path, 'r', encoding='utf-8') as fh:
        tpl = fh.read()
    json_str = json.dumps(data, ensure_ascii=False).replace('</script>', '<\\/script>')
    html_out = tpl.replace('__DATA_JSON__', json_str)
    html_out = html_out.replace('Solicitudes Web - Leads Latinbien',
                                'CRM Solicitudes - Leads desde ' + FECHA_CORTE)
    with open(os.path.join(BASE, 'index.html'), 'w', encoding='utf-8') as fh:
        fh.write(html_out)
    print('Escrito index.html', len(html_out), 'bytes')

if __name__ == '__main__':
    main()