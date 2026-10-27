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
RE_ETAPA_GANADA = re.compile(r'ganad|won|vendid', re.I)

def situacion(r):
    etapa = stage_name(r.get('stage_id')) or ''
    stage_id = r['stage_id'][0] if isinstance(r.get('stage_id'), (list, tuple)) else None
    # Perdido: en el CRM se marca perdido y queda archivado (active=False);
    # date_closed guarda la fecha y lost_reason_id el motivo
    if r.get('active') is False: return 'Perdido'
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
    # Si una corrida anterior ya lo encontro fusionado en otro registro, se busca ese directamente
    if lead.get('crm_id') and lead['crm_id'] != lead['id']:
        recs = je('crm.lead', 'search_read', [[['id', '=', lead['crm_id']], todos]], kw)
        if recs:
            r = recs[0]
            return r, f'El lead original ya no existe (fusionado o eliminado); el cliente esta en #{r["id"]} "{html.unescape(r.get("name") or "")}"'
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

PERDIDOS_DESDE = os.environ.get('PERDIDOS_DESDE', '2026-08-01')
RUTA_CONTACTOS = os.path.join(BASE, 'privado', 'contactos.json')

def perdidos_crm(je):
    """Todos los perdidos del CRM (cualquier origen) por fecha de cierre, como el filtro
    compartido 'Perdidos' del CRM: archivado. Solo lectura; sin telefono ni email."""
    disponibles = je('crm.lead', 'fields_get', [['team_id', 'source_id', 'x_form_origen', 'type']], {'attributes': ['type']})
    campos = ['name', 'contact_name', 'partner_name', 'user_id', 'stage_id', 'lost_reason_id',
              'date_closed', 'create_date'] + [c for c in ('team_id', 'source_id', 'x_form_origen', 'type') if c in disponibles]
    # Mismo dominio que el favorito compartido "Perdidos" del CRM: archivado, agrupado por date_closed
    dominio = [['active', '=', False], ['date_closed', '>=', PERDIDOS_DESDE + ' 00:00:00']]
    salida, offset = [], 0
    while True:
        batch = je('crm.lead', 'search_read', [dominio],
                   {'fields': campos, 'limit': 500, 'offset': offset, 'order': 'date_closed desc',
                    'context': {'active_test': False}})
        for r in batch:
            salida.append({
                'id': r['id'],
                'name': html.unescape(r.get('name') or ''),
                'contacto': html.unescape(r.get('contact_name') or r.get('partner_name') or ''),
                'ejecutivo': (user_name(r.get('user_id')) or '').strip(),
                'etapa': stage_name(r.get('stage_id')),
                'motivo': user_name(r.get('lost_reason_id')) or '',
                'fecha_cierre': a_local(r.get('date_closed')),
                'llegada': a_local(r.get('create_date')),
                'dias': dias_entre(r.get('create_date'), r.get('date_closed')),
                'equipo': user_name(r.get('team_id')) or '',
                'fuente': user_name(r.get('source_id')) or '',
                'origen_form': r.get('x_form_origen') or '',
                'tipo': {'lead': 'Iniciativa', 'opportunity': 'Oportunidad'}.get(r.get('type'), ''),
            })
        offset += len(batch)
        if len(batch) < 500: break
    # Favorito "Perdidos" guardado en el CRM, para comparar su dominio con el usado aqui
    try:
        favoritos = je('ir.filters', 'search_read', [[['model_id', '=', 'crm.lead'], ['name', 'ilike', 'perdid']]],
                       {'fields': ['name', 'domain', 'context', 'user_id']})
    except RuntimeError as e:
        favoritos = [{'error': str(e)[:200]}]
    print('perdidos CRM desde', PERDIDOS_DESDE, ':', len(salida), '| favoritos:', favoritos)
    return salida, favoritos

FLUJO_FAVORITO = os.environ.get('FLUJO_FAVORITO', 'flujo')
FLUJO_MAX = int(os.environ.get('FLUJO_MAX', '3000'))

def _a_lista(d):
    return [_a_lista(x) for x in d] if isinstance(d, (list, tuple)) else d

def favorito_flujo(je):
    """Favorito 'flujo AMD EJE ETAPA' (ir.filters). Devuelve (favorito, dominio o None si no es literal)."""
    import ast
    favs = je('ir.filters', 'search_read',
              [[['model_id', '=', 'crm.lead'], ['name', 'ilike', FLUJO_FAVORITO]]],
              {'fields': ['name', 'domain', 'context', 'user_id'], 'order': 'id desc'})
    for f in favs:
        try:
            return f, _a_lista(ast.literal_eval(f.get('domain') or '[]'))
        except (ValueError, SyntaxError):
            pass  # dominio con expresiones (p.ej. context_today()); se usa el de respaldo
    return (favs[0] if favs else None), None

def historial_etapas(je, ids):
    """Cambios de etapa desde el chatter (mail.tracking.value), solo lectura.
    Devuelve ({lead_id: [(fecha_utc, etapa_anterior, etapa_nueva)]}, error)."""
    cambios = {i: [] for i in ids}
    campos = ['old_value_char', 'new_value_char', 'create_date', 'mail_message_id']
    filtros_campo = ([['field_id.name', '=', 'stage_id']], [['field', '=', 'stage_id']])  # Odoo 16+ / anteriores
    for i in range(0, len(ids), 200):
        bloque = ids[i:i + 200]
        base = [['mail_message_id.model', '=', 'crm.lead'], ['mail_message_id.res_id', 'in', bloque]]
        tvs, error = None, ''
        for fc in filtros_campo:
            try:
                tvs = je('mail.tracking.value', 'search_read', [base + fc], {'fields': campos, 'order': 'create_date asc'})
                break
            except RuntimeError as e:
                error = str(e)[:200]
        if tvs is None:
            return None, error
        mids = list({t['mail_message_id'][0] for t in tvs if t.get('mail_message_id')})
        res = {m['id']: m['res_id'] for m in je('mail.message', 'search_read', [[['id', 'in', mids]]], {'fields': ['res_id']})} if mids else {}
        for t in tvs:
            lid = res.get(t['mail_message_id'][0]) if t.get('mail_message_id') else None
            if lid in cambios:
                cambios[lid].append((t['create_date'], t.get('old_value_char') or '', t.get('new_value_char') or ''))
    return cambios, ''

def flujo_etapas(je):
    """Leads abiertos del favorito de flujo: etapa actual, desde cuando y tiempo en cada etapa."""
    fav, dominio = favorito_flujo(je)
    if dominio is None:
        dominio = [['user_id', '!=', False]]
    dominio = dominio + [['active', '=', True]]
    disponibles = je('crm.lead', 'fields_get', [['team_id', 'date_last_stage_update', 'type']], {'attributes': ['type']})
    campos = ['name', 'contact_name', 'partner_name', 'user_id', 'stage_id', 'create_date'] + \
             [c for c in ('team_id', 'date_last_stage_update', 'type') if c in disponibles]
    leads = je('crm.lead', 'search_read', [dominio],
               {'fields': campos, 'limit': FLUJO_MAX, 'order': 'date_last_stage_update asc'})
    cambios, error = historial_etapas(je, [r['id'] for r in leads])
    ahora = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    salida = []
    for r in leads:
        etapa = stage_name(r.get('stage_id'))
        desde = r.get('date_last_stage_update') or r.get('create_date')
        # Segmentos [etapa, inicio local, horas]; el ultimo es la etapa actual (sigue corriendo)
        segs = []
        if cambios is not None:
            inicio, actual = r.get('create_date'), None
            for fecha, vieja, nueva in sorted(cambios.get(r['id'], [])):
                segs.append([vieja or actual or '', a_local(inicio), round((dias_entre(inicio, fecha) or 0) * 24, 1)])
                inicio, actual = fecha, nueva
            if segs:
                desde = inicio
        salida.append({
            'id': r['id'],
            'name': html.unescape(r.get('name') or ''),
            'contacto': html.unescape(r.get('contact_name') or r.get('partner_name') or ''),
            'ejecutivo': (user_name(r.get('user_id')) or '').strip(),
            'equipo': user_name(r.get('team_id')) or '',
            'tipo': {'lead': 'Iniciativa', 'opportunity': 'Oportunidad'}.get(r.get('type'), ''),
            'etapa': etapa,
            'desde': a_local(desde),
            'creado': a_local(r.get('create_date')),
            'historial': segs,
        })
    print('flujo: favorito', fav and fav.get('name'), '| dominio literal:', dominio is not None,
          '| leads abiertos:', len(salida), '| historial:', 'ok' if cambios is not None else 'sin acceso: ' + error)
    return {
        'favorito': ({'name': fav['name'], 'domain': fav.get('domain'), 'context': fav.get('context'),
                      'user': user_name(fav.get('user_id'))} if fav else None),
        'historial_ok': cambios is not None,
        'historial_error': error,
        'calculado_utc': ahora,
        'leads': salida,
    }

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
    # Telefono y email no se publican: se guardan aparte (cache privada de GitHub Actions)
    # solo para rastrear solicitudes que Odoo fusiona
    contactos = {}
    if os.path.isfile(RUTA_CONTACTOS):
        with open(RUTA_CONTACTOS, encoding='utf-8') as fh:
            contactos = json.load(fh)
    for l in previos + leads:
        if l.get('phone') or l.get('email_from'):
            contactos[str(l['id'])] = {'phone': l.get('phone') or '', 'email_from': l.get('email_from') or ''}
    actuales = {l['id'] for l in leads}
    for p in previos:
        if p['id'] in actuales: continue
        r, nota = rastrear(je, campos, {**p, **contactos.get(str(p['id']), {})})
        est = estado_crm(r, p['create_date']) if r else {}
        # Un registro del mismo cliente cerrado ANTES de que llegara la solicitud no es su cierre
        if est.get('fecha_cierre') and est['fecha_cierre'] < p['create_date']:
            nota += f'; ese registro se cerro el {est["fecha_cierre"][:10]}, antes de la solicitud'
            est = {}
        p = {**p, **(est or {'situacion': 'No encontrado', 'fecha_cierre': '', 'dias_cierre': None,
                             'motivo_perdida': ''}),
             'en_formulario': False, 'rastreo': nota, 'crm_id': r['id'] if r else p.get('crm_id')}
        leads.append(p)
    leads.sort(key=lambda l: l['id'])
    for l in leads:
        l.pop('phone', None); l.pop('email_from', None)
    os.makedirs(os.path.dirname(RUTA_CONTACTOS), exist_ok=True)
    with open(RUTA_CONTACTOS, 'w', encoding='utf-8') as fh:
        json.dump(contactos, fh, ensure_ascii=False)

    print('leads desde', FECHA_CORTE, ':', len(leads),
          {s: sum(1 for l in leads if l['situacion'] == s) for s in ('Abierto', 'Ganado', 'Perdido', 'No encontrado')},
          'rastreados:', sum(1 for l in leads if not l['en_formulario']))

    perdidos, favoritos = perdidos_crm(je)
    try:
        flujo = flujo_etapas(je)
    except RuntimeError as e:
        # El flujo es un agregado: si falla, el resto del tablero se publica igual
        flujo = {'error': str(e)[:300], 'leads': []}
        print('flujo: error', flujo['error'])

    data = {'generado': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'perdidos_crm': perdidos,
            'flujo': flujo,
            'favorito_perdidos': [{k: (user_name(v) if k == 'user_id' else v) for k, v in f.items()} for f in favoritos],
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