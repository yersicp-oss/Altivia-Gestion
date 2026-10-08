"""ALTIVIA | Gestor integral de proyectos. Ejecutar: streamlit run app.py"""
import os
from html import escape
import io
import sqlite3
import hashlib
import hmac
import secrets
import tempfile
import re
import base64
import requests
import msal
from urllib.parse import quote
from contextlib import contextmanager
from datetime import date, timedelta, datetime
import pandas as pd
import streamlit as st
import plotly.express as px

st.set_page_config(page_title='ALTIVIA | Gestión de Proyectos', page_icon='🏗️', layout='wide', initial_sidebar_state='expanded')
ROOT=os.path.dirname(os.path.abspath(__file__))
DB=os.environ.get('ALTIVIA_DB',os.path.join(ROOT,'altivia.db'))
TODAY=date.today()

PROJECT_STATES=['No iniciado','En desarrollo','En revisión','En corrección','Terminado']
TASK_STATES=['No iniciado','En desarrollo','En revisión','Con observaciones','Corregido','Aprobado']
DELIVERY_STATES=['Pendiente','En desarrollo','En revisión interna','Con observaciones','En corrección','Aprobado internamente','Enviado al cliente','Observado por cliente','Corregido','Aprobado','Entregado']
CHANGE_STATES=['Solicitado','Aprobado','Ejecutado']
MEETING_STATES=['Pendiente','En proceso','Completado','Atrasado','Cancelado']
SPECIALTIES=['Arquitectura','Estructuras','Eléctricas','Sanitarias','HVAC','Seguridad','BIM','Coordinación','Geotecnia','Relaves','Hidráulica','Mecánica','Topografía','Otra']
PROJECT_TYPES=['Arquitectura','Estructuras','Instalaciones eléctricas','Instalaciones sanitarias','HVAC','BIM','Expediente técnico','Otro']
PRIORITIES=['Alta','Media','Baja']; RISKS=['Bajo','Medio','Alto','Crítico']
ROLES=['Gerente','Jefe de Proyecto','Coordinador','Arquitecto','Ingeniero','Dibujante','Modelador BIM','Revisor','Asistente','Otro']
CHECKS=['Información general correcta','Nombre del proyecto','Código del plano','Número de versión','Escala','Norte','Ejes','Niveles','Cotas','Nomenclatura','Simbología','Referencias','Detalles','Cuadro de áreas','Capas','Lineweights','Textos','Compatibilidad con otras especialidades','Formato de impresión','Revisión técnica','Revisión gráfica']

# key: (titulo, sql table, [ (column, visible label, type, required?, choices key) ])
SPECS={
 'Proyectos':('projects',[
 ('code','ID Proyecto','text',True,None),('name','Proyecto','text',True,None),('client','Cliente','text',False,None),('type','Tipo','select',False,'types'),('location','Ubicación','text',False,None),('manager','Jefe / Coordinador','person',False,None),('start_date','Fecha inicio','date',False,None),('due_date','Fecha entrega','date',False,None),('status','Estado','select',True,'project_states'),('priority','Prioridad','select',False,'priorities'),('notes','Observaciones','long',False,None)]),
 'Plan de trabajo':('tasks',[
 ('code','ID Tarea','text',True,None),('project_code','ID Proyecto','project',True,None),('specialty','Especialidad','select',False,'specialties'),('activity','Actividad','text',True,None),('delivery_code','Entregable relacionado','delivery',False,None),('owner','Responsable','person',True,None),('reviewer','Revisor','person',False,None),('start_date','Fecha de inicio','date',True,None),('due_date','Fecha término','date',True,None),('progress','Avance (%)','int',True,None),('status','Estado','select',True,'task_states'),('priority','Prioridad','select',False,'priorities'),('updated_at','Fecha actualización','date',False,None),('notes','Observaciones','long',False,None)]),
 'Entregables':('deliverables',[
 ('code','ID Entregable','text',True,None),('project_code','ID Proyecto','project',True,None),('drawing_code','Código del plano','text',False,None),('name','Nombre del plano/documento','text',True,None),('specialty','Especialidad','select',False,'specialties'),('owner','Responsable','person',True,None),('reviewer','Revisor','person',True,None),('version','Versión','version',True,None),('due_date','Fecha prevista','date',True,None),('actual_date','Fecha real','date',False,None),('status','Estado','select',True,'delivery_states'),('review_date','Fecha revisión','date',False,None),('correction_date','Fecha corrección','date',False,None),('approval_date','Fecha aprobación','date',False,None),('notes','Observaciones','long',False,None),('file_path','Enlace del documento (Google Drive / OneDrive / SharePoint)','text',False,None)]),
 'Control de cambios':('changes',[
 ('code','ID Cambio','text',True,None),('project_code','ID Proyecto','project',True,None),('request_date','Fecha solicitud','date',True,None),('requester','Solicitante','text',True,None),('description','Descripción cambio','long',True,None),('reason','Motivo','long',False,None),('specialty','Especialidad afectada','select',False,'specialties'),('affected_drawings','Planos afectados','text',False,None),('owner','Responsable','person',True,None),('schedule_impact','Impacto en plazo','select',False,'risks'),('new_due_date','Nueva fecha entrega','date',False,None),('approved_by','Aprobado por','text',False,None),('approval_date','Fecha aprobación','date',False,None),('status','Estado','select',True,'change_states'),('notes','Observaciones','long',False,None)]),
 'Personal':('people',[
 ('code','Nombre','text',True,None),('role','Cargo','select',False,'roles'),('specialty','Especialidad','select',False,'specialties'),('email','Correo','text',False,None),('phone','Teléfono','text',False,None),('status','Disponibilidad','select',True,'people_states'),('notes','Observaciones','long',False,None)])
}
OPTIONS={'types':PROJECT_TYPES,'project_states':PROJECT_STATES,'task_states':TASK_STATES,'delivery_states':DELIVERY_STATES,'change_states':CHANGE_STATES,'meeting_states':MEETING_STATES,'specialties':SPECIALTIES,'priorities':PRIORITIES,'risks':RISKS,'roles':ROLES,'people_states':['Disponible','Ocupado','No disponible']}
FINISHED_TASK={'Terminado','Aprobado'}
FINISHED_DELIVERY={'Aprobado','Entregado'}
CLOSED_CHANGE={'Ejecutado'}

@contextmanager
def connection():
    con=sqlite3.connect(DB,timeout=20)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    finally: con.close()

def initialize():
    with connection() as con:
        for _,(table,fields) in SPECS.items():
            cols=['id INTEGER PRIMARY KEY AUTOINCREMENT','created_at TEXT DEFAULT CURRENT_TIMESTAMP']
            for key,_,kind,_,_ in fields:
                sql_type='REAL' if kind=='float' else 'INTEGER' if kind in ('int','bool') else 'TEXT'
                cols.append(f'"{key}" {sql_type}'+(' UNIQUE' if key=='code' else ''))
            if table=='people':cols.append('name TEXT')  # columna interna para compatibilidad con datos antiguos
            con.execute(f'CREATE TABLE IF NOT EXISTS {table} ({", ".join(cols)})')
        con.execute('''CREATE TABLE IF NOT EXISTS meetings (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, project_code TEXT, owner TEXT, due_date TEXT, status TEXT)''')
        con.execute('CREATE TABLE IF NOT EXISTS versions (id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_code TEXT NOT NULL, version TEXT NOT NULL, registered_at TEXT NOT NULL, notes TEXT, UNIQUE(delivery_code,version))')
        con.execute('CREATE TABLE IF NOT EXISTS checklist (id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_code TEXT NOT NULL, criterion TEXT NOT NULL, result TEXT NOT NULL DEFAULT "PENDIENTE", notes TEXT, UNIQUE(delivery_code,criterion))')
        con.execute('CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT DEFAULT CURRENT_TIMESTAMP, module TEXT, record_code TEXT, action TEXT)')
        # Compatibilidad: conservar datos de instalaciones anteriores.
        con.execute('CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_code)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_deliveries_project ON deliverables(project_code)')

def hash_password(password):
    salt=secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,310000)
    return 'pbkdf2_sha256$310000$'+salt.hex()+'$'+digest.hex()

def verify_password(password,stored):
    try:
        algo,iterations,salt,digest=stored.split('$')
        if algo!='pbkdf2_sha256':return False
        actual=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),bytes.fromhex(salt),int(iterations))
        return hmac.compare_digest(actual,bytes.fromhex(digest))
    except (ValueError,TypeError):return False

def initialize_auth():
    # Preserva usuarios existentes y elimina la restriccion antigua de roles.
    with sqlite3.connect(DB, timeout=20) as migration:
        row = migration.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        if row and 'CHECK(role IN' in (row[0] or ''):
            migration.execute('CREATE TABLE users_migration (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP)')
            migration.execute('INSERT INTO users_migration (id,username,full_name,password_hash,role,active,created_at) SELECT id,username,full_name,password_hash,role,active,created_at FROM users')
            migration.execute('DROP TABLE users')
            migration.execute('ALTER TABLE users_migration RENAME TO users')
    with connection() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL, password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        con.execute('''CREATE TABLE IF NOT EXISTS client_access (user_id INTEGER NOT NULL, delivery_id INTEGER NOT NULL, granted_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(user_id,delivery_id), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY(delivery_id) REFERENCES deliverables(id) ON DELETE CASCADE)''')
        users=con.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        if users==0:
            password=os.getenv('ALTIVIA_ADMIN_PASSWORD','')
            if not password:
                try:password=st.secrets.get('ALTIVIA_ADMIN_PASSWORD','')
                except Exception:pass
            if len(password)>=6:
                con.execute('INSERT INTO users(username,full_name,password_hash,role) VALUES (?,?,?,?)',
                            ('admin','Administrador principal',hash_password(password),'Administrador'))
            else:return False
    return True

def can_edit():
    return st.session_state.get('role')=='Administrador'

def require_admin():
    if not can_edit():raise PermissionError('Acceso denegado: se requiere rol Administrador.')

def login_page():
    st.title('🔐 ALTIVIA | Acceso al sistema')
    st.caption('Ingrese sus credenciales para acceder a la gestión de proyectos.')
    with st.form('login_form'):
        username=st.text_input('Usuario').strip().lower()
        password=st.text_input('Contraseña',type='password')
        submitted=st.form_submit_button('Ingresar',type='primary')
    if submitted:
        if st.session_state.get('failed_logins',0)>=8:
            st.error('Demasiados intentos fallidos en esta sesión. Cierre el navegador y contacte al administrador.')
            return
        with connection() as con:
            r=con.execute('SELECT id,username,full_name,role,active,password_hash FROM users WHERE username=?',(username,)).fetchone()
        if r and r['active']==1 and verify_password(password,r['password_hash']):
            st.session_state['user_id']=r['id'];st.session_state['role']=r['role']
            st.session_state['username']=r['username'];st.session_state['full_name']=r['full_name']
            st.session_state['failed_logins']=0
            st.rerun()
        else:
            st.session_state['failed_logins']=st.session_state.get('failed_logins',0)+1
            st.error('Credenciales incorrectas o usuario desactivado.')

def user_management():
    require_admin()
    st.title('🔐 Administración de usuarios')
    with connection() as con:
        users=pd.read_sql_query('SELECT id,username,full_name,role,active,created_at FROM users ORDER BY id',con)
    st.dataframe(users,hide_index=True,use_container_width=True)
    st.subheader('Crear usuario')
    with st.form('new_user'):
        username=st.text_input('Usuario nuevo').strip().lower()
        name=st.text_input('Nombre completo').strip()
        role=st.selectbox('Rol',['Consulta','Cliente','Administrador'])
        password=st.text_input('Contraseña inicial (mínimo 6 caracteres)',type='password')
        if st.form_submit_button('Crear usuario',type='primary'):
            if not username or not name or len(password)<6:st.error('Complete los datos y use una contraseña de al menos 6 caracteres.')
            else:
                try:
                    with connection() as con:
                        con.execute('INSERT INTO users(username,full_name,password_hash,role) VALUES(?,?,?,?)',(username,name,hash_password(password),role))
                    st.success('Usuario creado');st.rerun()
                except sqlite3.IntegrityError:st.error('El nombre de usuario ya existe.')
    st.subheader('Editar acceso o restablecer contraseña')
    selected=st.selectbox('Seleccionar usuario',users.id.tolist(),format_func=lambda i: str(users.loc[users.id==i,'username'].iloc[0]))
    r=users.loc[users.id==selected].iloc[0]
    with st.form('update_user'):
        new_role=st.selectbox('Rol',['Administrador','Consulta','Cliente'],index=['Administrador','Consulta','Cliente'].index(r['role']))
        active=st.checkbox('Cuenta activa',value=bool(r['active']))
        reset=st.text_input('Contraseña nueva (dejar vacío para conservar)',type='password')
        if st.form_submit_button('Guardar acceso'):
            if reset and len(reset)<6:st.error('La contraseña nueva debe tener al menos 6 caracteres.')
            elif r['id']==st.session_state['user_id'] and (not active or new_role!='Administrador'):
                st.error('No puedes desactivar tu propia cuenta ni quitarte el rol Administrador.')
            else:
                with connection() as con:
                    if r['role']=='Administrador' and (not active or new_role!='Administrador'):
                        count=con.execute("SELECT COUNT(*) FROM users WHERE role='Administrador' AND active=1").fetchone()[0]
                        if count<=1:st.error('Debe quedar al menos un administrador activo.');return
                    con.execute('UPDATE users SET role=?, active=? WHERE id=?',(new_role,int(active),selected))
                    if reset:con.execute('UPDATE users SET password_hash=? WHERE id=?',(hash_password(reset),selected))
                st.success('Acceso actualizado');st.rerun()
    client_permissions_ui()

def client_permissions_ui():
    require_admin()
    st.subheader('📂 Permisos de clientes por entregable')
    st.caption('Selecciona exactamente los entregables que cada cliente podrá consultar. Solo serán visibles cuando su estado sea Aprobado o Entregado.')
    with connection() as con:
        clients=con.execute("SELECT id,username,full_name FROM users WHERE role='Cliente' AND active=1 ORDER BY full_name").fetchall()
        deliveries=con.execute("SELECT id,code,name,project_code,status,file_path FROM deliverables ORDER BY project_code,name").fetchall()
    if not clients:
        st.info('Primero crea un usuario con el rol Cliente.');return
    cid=st.selectbox('Cliente', [r['id'] for r in clients],format_func=lambda i:next(r['full_name']+' · '+r['username'] for r in clients if r['id']==i),key='client_acl_user')
    with connection() as con:
        current={r[0] for r in con.execute('SELECT delivery_id FROM client_access WHERE user_id=?',(cid,)).fetchall()}
    eligible=[r for r in deliveries if r['status'] in ('Aprobado','Entregado')]
    if not eligible:
        st.info('No existen entregables aprobados o entregados.');return
    options={r['id']:f"{r['project_code']} · {r['code']} · {r['name']} ({r['status']})" for r in eligible}
    selected=st.multiselect('Entregables autorizados',options.keys(),default=[i for i in options if i in current],format_func=lambda i:options[i],key='client_acl_docs')
    if st.button('Guardar permisos del cliente',type='primary'):
        with connection() as con:
            con.execute('DELETE FROM client_access WHERE user_id=?',(cid,))
            con.executemany('INSERT INTO client_access(user_id,delivery_id) VALUES(?,?)',[(cid,i) for i in selected])
        st.success('Permisos actualizados.');st.rerun()


def graph_settings():
    """Secrets only: never store credentials in the database or repository."""
    try:
        conf=st.secrets.get('microsoft_graph',{})
        return {k:str(conf.get(k,'')).strip() for k in ('tenant_id','client_id','client_secret')}
    except Exception:
        return {}


def graph_pdf(reference):
    """Fetch a private PDF server-side; never disclose the Graph app token or signed URL."""
    match=re.fullmatch(r'graph://([A-Za-z0-9_.!~-]+)/([A-Za-z0-9_.!~-]+)',reference or '')
    if not match:
        raise ValueError('Referencia no válida. Use graph://ID_UNIDAD/ID_ARCHIVO (no un enlace público).')
    cfg=graph_settings()
    if not all(cfg.get(k) for k in ('tenant_id','client_id','client_secret')):
        raise ValueError('Falta configurar [microsoft_graph] en los Secrets privados de Streamlit.')
    app=msal.ConfidentialClientApplication(cfg['client_id'],authority='https://login.microsoftonline.com/'+cfg['tenant_id'],client_credential=cfg['client_secret'])
    token=app.acquire_token_for_client(scopes=['https://graph.microsoft.com/.default'])
    if 'access_token' not in token:
        raise ValueError('Microsoft Graph rechazó la autenticación. Revise credenciales y consentimiento.')
    drive,item=match.groups()
    base=f'https://graph.microsoft.com/v1.0/drives/{quote(drive,safe="")}/items/{quote(item,safe="")}'
    headers={'Authorization':'Bearer '+token['access_token']}
    meta=requests.get(base,headers=headers,timeout=20)
    if meta.status_code != 200:
        raise ValueError(f'No se pudo consultar el archivo en Microsoft Graph (HTTP {meta.status_code}).')
    info=meta.json()
    if not str(info.get('name','')).lower().endswith('.pdf') or 'file' not in info:
        raise ValueError('La vista integrada admite únicamente documentos PDF.')
    limit=25*1024*1024
    if int(info.get('size') or 0)>limit:
        raise ValueError('El PDF supera el límite de 25 MB para visualización/descarga integrada.')
    with requests.get(base+'/content',headers=headers,timeout=60,stream=True) as res:
        if res.status_code!=200:
            raise ValueError(f'No se pudo descargar el PDF desde Graph (HTTP {res.status_code}).')
        chunks=[];size=0
        for chunk in res.iter_content(256*1024):
            if not chunk:continue
            size+=len(chunk)
            if size>limit:
                raise ValueError('El PDF supera el límite de 25 MB.')
            chunks.append(chunk)
    data=b''.join(chunks)
    if not data.startswith(b'%PDF-'):
        raise ValueError('El archivo descargado no es un PDF válido.')
    return data,info['name']


def internal_pdf_preview(data):
    if hasattr(st,'pdf'):
        st.pdf(data,height=680)
    else:
        import streamlit.components.v1 as components
        payload=base64.b64encode(data).decode('ascii')
        components.html('<iframe title="Plano PDF" src="data:application/pdf;base64,'+payload+'" style="width:100%;height:680px;border:0" loading="lazy"></iframe>',height=700,scrolling=False)
        st.caption('Si tu navegador bloquea el visor integrado, utiliza Descargar PDF.')


def client_portal():
    if st.session_state.get('role')!='Cliente':st.error('Acceso restringido.');st.stop()
    st.title('📁 Mis planos y documentos')
    st.caption('Documentos aprobados y autorizados por ALTIVIA · Acceso de solo lectura')
    with connection() as con:
        rows=[dict(x) for x in con.execute('''SELECT d.id,d.code,d.project_code,d.drawing_code,d.name,d.version,d.specialty,d.status,d.file_path
            FROM deliverables d JOIN client_access a ON a.delivery_id=d.id
            WHERE a.user_id=? AND d.status IN ('Aprobado','Entregado') ORDER BY d.project_code,d.name''',(st.session_state['user_id'],)).fetchall()]
    if not rows:st.info('Todavía no tienes documentos aprobados asignados.');return
    projects=sorted(set(x['project_code'] for x in rows if x['project_code']))
    a,b=st.columns([2,1])
    with a:query=st.text_input('🔎 Buscar plano o documento',key='client_search').strip().casefold()
    with b:chosen=st.selectbox('Proyecto',['Todos']+projects,key='client_proj')
    rows=[r for r in rows if (chosen=='Todos' or r['project_code']==chosen) and (not query or query in (' '.join(str(v or '') for v in r.values())).casefold())]
    st.caption(f'{len(rows)} documentos disponibles')
    st.markdown('''<style>
      .altivia-client-card{background:#101318;border:1px solid #343b47;border-radius:13px;padding:18px 19px;margin:8px 0 18px;color:#f8fafc;box-shadow:0 4px 18px #00000016}
      .altivia-client-head{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
      .altivia-client-title{font-weight:750;font-size:1.06rem;color:white}
      .altivia-client-badge{background:#104a2f;color:#55e697;font-size:.78rem;font-weight:650;padding:4px 10px;border-radius:18px}
      .altivia-client-sub{font-size:.84rem;color:#aab7cf;margin-top:9px;line-height:1.5}
      @media(max-width:640px){.altivia-client-card{padding:15px;margin-bottom:9px}}
    </style>''',unsafe_allow_html=True)
    for r in rows:
        title=escape(str(r['drawing_code'] or r['code']))+' – '+escape(str(r['name'] or 'Documento'))
        sub=escape(str(r['project_code'] or ''))+' · Versión '+escape(str(r['version'] or '—'))+' · '+escape(str(r['specialty'] or ''))
        st.markdown(f'<div class="altivia-client-card"><div class="altivia-client-head"><span class="altivia-client-title">{title}</span><span class="altivia-client-badge">{escape(r["status"])}</span></div><div class="altivia-client-sub">{sub}</div></div>',unsafe_allow_html=True)
        reference=str(r['file_path'] or '').strip()
        if reference.startswith('https://'):
            # Los enlaces externos conservan los permisos del proveedor de almacenamiento.
            # El cliente solo ve documentos aprobados que ALTIVIA le ha asignado.
            left,right=st.columns(2)
            with left:
                st.link_button('👁 Visualizar documento',reference,use_container_width=True)
            with right:
                st.link_button('⬇ Descargar / abrir archivo',reference,use_container_width=True)
            st.caption('La visualización y descarga dependen de los permisos y opciones de Google Drive, OneDrive o SharePoint.')
        elif reference:
            st.warning('Enlace no válido. El administrador debe registrar una URL que comience con https://.')
        else:
            st.caption('Archivo pendiente de vincular. Contacta a ALTIVIA.')
        st.divider()


def my_account():
    st.title('Mi cuenta')
    st.write(f"Usuario: **{st.session_state.get('username')}** · Rol: **{st.session_state.get('role')}**")
    with st.form('change_password'):
        current=st.text_input('Contraseña actual',type='password')
        new=st.text_input('Nueva contraseña (mínimo 6 caracteres)',type='password')
        confirm=st.text_input('Confirmar contraseña',type='password')
        if st.form_submit_button('Cambiar contraseña'):
            with connection() as con:
                stored=con.execute('SELECT password_hash FROM users WHERE id=?',(st.session_state['user_id'],)).fetchone()
                if not stored or not verify_password(current,stored[0]):st.error('Contraseña actual incorrecta.')
                elif len(new)<6 or new!=confirm:st.error('La nueva contraseña no cumple los requisitos o no coincide.')
                else:
                    con.execute('UPDATE users SET password_hash=? WHERE id=?',(hash_password(new),st.session_state['user_id']))
                    st.success('Contraseña actualizada.')

def df(table):
    with connection() as con: return pd.read_sql_query(f'SELECT * FROM {table} ORDER BY id DESC',con)

def datespan(value):
    try: return (date.fromisoformat(str(value)[:10])-date.today()).days
    except (ValueError,TypeError): return None

def decorate():
    data={x:df(t) for x,(t,_) in SPECS.items()}
    p,t,e,c,pe=[data[k] for k in ['Proyectos','Plan de trabajo','Entregables','Control de cambios','Personal']]
    if not t.empty:
        t['Días restantes']=t.due_date.map(datespan)
        t['Días atraso']=t.apply(lambda r:max(0,-r['Días restantes']) if pd.notna(r['Días restantes']) and r.status not in FINISHED_TASK and int(r.progress or 0)<100 else 0,axis=1)
        t['Semáforo']=t.apply(lambda r:'🟢 Terminada' if r.status in FINISHED_TASK or r.progress==100 else '🔴 ATRASADA' if r['Días atraso']>0 else '🟡 Vence pronto' if pd.notna(r['Días restantes']) and r['Días restantes']<=2 else '🔵 Revisión' if r.status=='En revisión' else '⚪ No iniciada' if r.status=='No iniciado' else '🟢 En plazo',axis=1)
    if not e.empty:
        e['Días restantes']=e.due_date.map(datespan)
        e['Alerta']=e.apply(lambda r:'🔴 ATRASADO' if pd.notna(r['Días restantes']) and r['Días restantes']<0 and r.status not in FINISHED_DELIVERY else '🟡 Próximo' if pd.notna(r['Días restantes']) and 0<=r['Días restantes']<=3 and r.status not in FINISHED_DELIVERY else '🔵 En revisión' if 'revisión' in r.status.lower() else '🟢 Cerrado' if r.status in FINISHED_DELIVERY else '',axis=1)
    if not p.empty:
        p['Días restantes']=p.due_date.map(datespan)
        progresses=[]; risks=[]; sem=[]
        for _,r in p.iterrows():
            pt=t[t.project_code==r.code] if not t.empty else pd.DataFrame()
            progress=round(float(pt.progress.fillna(0).mean()),1) if len(pt) else 0
            progresses.append(progress)
            blocked=False
            late=(not pt.empty and bool((pt['Días atraso']>0).any())) or (datespan(r.due_date) is not None and datespan(r.due_date)<0 and r.status not in ('Terminado',))
            risk='Alto' if late or blocked else 'Medio' if (datespan(r.due_date) is not None and datespan(r.due_date)<=7 and progress<100 and r.status!='Terminado') else 'Bajo'
            risks.append(risk)
            sem.append('🔴 Atrasado' if late else '🟠 Bloqueado' if blocked else '🟡 En riesgo' if risk in ('Medio','Alto','Crítico') else '🟢 En plazo')
        p['% Avance']=progresses;p['Riesgo calculado']=risks;p['Situación']=sem
    if not pe.empty:
        work=[];projects=[];load=[]
        for _,r in pe.iterrows():
            assigned=t[(t.owner==r.name)&(~t.status.isin(FINISHED_TASK))] if not t.empty else pd.DataFrame()
            n=len(assigned)
            work.append(n)
            projects.append(assigned.project_code.nunique() if not assigned.empty else 0)
            load.append('🟡 Con tareas' if n else '🟢 Sin tareas')
        pe['Tareas activas']=work;pe['Proyectos asignados']=projects;pe['Carga de trabajo']=load
    return data

def selectors(kind,current=None):
    if kind=='project': return df('projects').code.dropna().tolist()
    if kind=='delivery': return ['']+df('deliverables').code.dropna().tolist()
    if kind=='person': return df('people').name.dropna().tolist()
    return OPTIONS.get(kind,[])

def form_input(key,label,kind,required,choice,value,form_key):
    tag=form_key+'_'+key
    if kind in ('select','person','project','delivery'):
        vals=selectors(choice if kind=='select' else kind)
        if kind=='select' and key=='specialty' and 'form_people_' in form_key:
            vals=[v for v in vals if v not in ('Seguridad','Relaves')]
        if not required and kind!='delivery': vals=['']+vals
        if value and value not in vals and kind!='select':vals=[value]+vals
        if not vals: st.warning(f'Primero registre {"personal" if kind=="person" else "proyectos" if kind=="project" else "entregables"}.') ;vals=['']
        return st.selectbox(label,vals,index=vals.index(value) if value in vals else 0,key=tag+'_'+str(value))
    if kind=='version': return st.text_input(label,value=value or 'V01',key=tag,help='Formato recomendado: V01, V02, V03…')
    if kind=='date':
        val=date.fromisoformat(str(value)[:10]) if value and str(value)!='nan' else None
        return st.date_input(label,value=val,key=tag,format='DD/MM/YYYY')
    if kind=='bool':return st.checkbox(label,value=bool(value),key=tag)
    if kind=='long':return st.text_area(label,value=value or '',key=tag,height=90)
    if kind=='int':return st.number_input(label,min_value=0,max_value=100 if key=='progress' else 100000,value=int(value or (4 if key=='capacity' else 0)),step=1,key=tag)
    if kind=='float':return st.number_input(label,min_value=0.0,value=float(value or 0),step=0.5,key=tag)
    return st.text_input(label,value=str(value or ''),key=tag)

def save_record(module,values,record_id=None):
    require_admin()
    table,fields=SPECS[module]
    mandatory=[label for k,label,_,required,_ in fields if required and (values.get(k) in ('',None))]
    if mandatory:raise ValueError('Campos obligatorios: '+', '.join(mandatory))
    if module=='Plan de trabajo' and values['start_date']>values['due_date']:raise ValueError('Fecha término anterior al inicio.')
    if module=='Entregables' and not str(values['version']).upper().startswith('V'):raise ValueError('Use versiones V01, V02, etc.')
    if module=='Entregables' and values['status'] in ('Enviado al cliente','Aprobado','Entregado') and not values.get('review_date'):
        raise ValueError('Debe registrar fecha de revisión antes de enviar/aprobar/entregar.')
    if module=='Personal':
        values['code']=str(values.get('code') or '').strip()
        if not values['code']:raise ValueError('El nombre es obligatorio.')
        values['name']=values['code']
    with connection() as con:
        if module=='Personal':
            other=con.execute('SELECT id FROM people WHERE lower(name)=lower(?) AND id!=?',(values['name'],record_id or -1)).fetchone()
            if other:raise ValueError('Ya existe una persona con ese nombre.')
            if record_id:
                old=con.execute('SELECT name FROM people WHERE id=?',(record_id,)).fetchone()
                if old and old['name']!=values['name']:
                    for linked_table,column in [('projects','manager'),('tasks','owner'),('tasks','reviewer'),('deliverables','owner'),('deliverables','reviewer'),('changes','owner'),('meetings','owner')]:
                        con.execute(f'UPDATE {linked_table} SET {column}=? WHERE {column}=?',(values['name'],old['name']))
        if record_id:
            sets=', '.join(f'{k}=?' for k in values)
            con.execute(f'UPDATE {table} SET {sets} WHERE id=?',list(values.values())+[record_id])
        else:
            keys=','.join(values.keys());placeholders=','.join('?' for _ in values)
            con.execute(f'INSERT INTO {table} ({keys}) VALUES ({placeholders})',list(values.values()))
        if module=='Entregables':
            con.execute('INSERT OR IGNORE INTO versions(delivery_code,version,registered_at,notes) VALUES (?,?,?,?)',(values['code'],values['version'],date.today().isoformat(),'Versión registrada desde formulario'))
        con.execute('INSERT INTO audit(module,record_code,action) VALUES (?,?,?)',(module,values['code'],'Actualización' if record_id else 'Alta'))

def backup_database():
    """Respaldo SQLite coherente; no copiar el archivo mientras está escribiéndose."""
    folder=os.path.join(ROOT,'respaldos')
    os.makedirs(folder,exist_ok=True)
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    filename=os.path.join(folder,f'ALTIVIA_automatico_{stamp}.db')
    with sqlite3.connect(DB,timeout=30) as origin, sqlite3.connect(filename) as destination:
        origin.backup(destination)
    return filename


def delete_selected(module,ids):
    require_admin()
    table,_=SPECS[module]
    ids=list(dict.fromkeys(int(i) for i in ids))
    if not ids:return 0
    with connection() as con:
        marks=','.join('?' for _ in ids)
        rows=con.execute(f'SELECT code FROM {table} WHERE id IN ({marks})',ids).fetchall()
        codes=[r['code'] for r in rows]
        if module=='Proyectos' and codes:
            pm=','.join('?' for _ in codes)
            dc=[r[0] for r in con.execute(f'SELECT code FROM deliverables WHERE project_code IN ({pm})',codes)]
            if dc:
                dm=','.join('?' for _ in dc)
                con.execute(f'DELETE FROM versions WHERE delivery_code IN ({dm})',dc)
                con.execute(f'DELETE FROM checklist WHERE delivery_code IN ({dm})',dc)
            for linked in ('tasks','deliverables','changes','meetings'):
                con.execute(f'DELETE FROM {linked} WHERE project_code IN ({pm})',codes)
        if module=='Entregables' and codes:
            dm=','.join('?' for _ in codes)
            con.execute(f'DELETE FROM versions WHERE delivery_code IN ({dm})',codes)
            con.execute(f'DELETE FROM checklist WHERE delivery_code IN ({dm})',codes)
            # Las tareas no se borran: solo se elimina la relación al entregable.
            con.execute(f"UPDATE tasks SET delivery_code='' WHERE delivery_code IN ({dm})",codes)
        con.execute(f'DELETE FROM {table} WHERE id IN ({marks})',ids)
        for code in codes:
            con.execute('INSERT INTO audit(module,record_code,action) VALUES (?,?,?)',
                        (module,code,'Eliminación múltiple'))
    return len(codes)


def bulk_delete_ui(module,data):
    require_admin()
    table,_=SPECS[module]
    view=data[module]
    if view.empty:return
    with st.expander('🗑️ Eliminar varios registros a la vez'):
        st.warning('Esta acción es irreversible. Los proyectos eliminados también eliminarán sus tareas, entregables, revisiones, cambios y reuniones asociados. Se creará un respaldo automático.')
        options=view['id'].astype(int).tolist()
        names={int(r['id']):f"{r.get('code','')} — {r.get('name',r.get('activity',r.get('topic','')))}" for _,r in view.iterrows()}
        chosen=st.multiselect('Seleccione uno o varios registros',options,format_func=lambda rid:names.get(rid,str(rid)),key='multidel_'+table)
        confirm=st.checkbox(f'Confirmo eliminar {len(chosen)} registro(s) seleccionado(s)',key='multiconfirm_'+table)
        typed=st.text_input('Escriba ELIMINAR para confirmar',key='multitype_'+table)
        if st.button('Eliminar registros seleccionados',type='primary',disabled=not chosen or not confirm or typed!='ELIMINAR',key='multibutton_'+table):
            try:
                path=backup_database()
                n=delete_selected(module,chosen)
                st.success(f'Se eliminaron {n} registros. Respaldo automático: {os.path.basename(path)}')
                st.rerun()
            except Exception as exc:st.error(f'No se pudo eliminar: {exc}')


def reset_database_ui():
    require_admin()
    st.divider()
    st.subheader('⚠️ Restablecer información de ALTIVIA')
    st.warning('Esta herramienta BORRA datos definitivamente del sistema activo. Antes se genera una copia .db en la carpeta respaldos. Resguarde también una copia fuera de esta computadora.')
    mode=st.radio('Seleccione el alcance del restablecimiento',
        ['Limpiar datos operativos (conservar todos los usuarios)',
         'Restablecimiento general (borrar datos y otros usuarios; conservar mi cuenta administradora)'],
         key='reset_mode')
    st.caption('Se borrarán proyectos, tareas, entregables, versiones, checklists, cambios, reuniones, personal y bitácora. La segunda opción también borra todas las cuentas excepto el administrador que ejecuta el procedimiento.')
    with st.form('reset_form'):
        typed=st.text_input('Escriba RESTABLECER ALTIVIA')
        password=st.text_input('Su contraseña de administrador',type='password')
        accept=st.checkbox('Entiendo que todos los datos seleccionados serán eliminados')
        go=st.form_submit_button('Restablecer base de datos',type='primary')
    if go:
        if typed!='RESTABLECER ALTIVIA' or not accept:
            st.error('Complete la confirmación de forma exacta.');return
        with connection() as con:
            user=con.execute('SELECT password_hash FROM users WHERE id=? AND role=? AND active=1',
                             (st.session_state['user_id'],'Administrador')).fetchone()
        if not user or not verify_password(password,user['password_hash']):
            st.error('Contraseña de administrador incorrecta.');return
        try:
            path=backup_database()
            with connection() as con:
                for table in ('checklist','versions','meetings','changes','tasks','deliverables','projects','people','audit'):
                    con.execute(f'DELETE FROM {table}')
                if mode.startswith('Restablecimiento general'):
                    con.execute('DELETE FROM users WHERE id<>?',(st.session_state['user_id'],))
                con.execute('INSERT INTO audit(module,record_code,action) VALUES (?,?,?)',
                            ('Sistema','ALTIVIA','Restablecimiento general' if mode.startswith('Restablecimiento general') else 'Limpieza de datos'))
            st.success(f'Información restablecida. Respaldo guardado: {path}')
            st.rerun()
        except Exception as exc:st.error(f'El restablecimiento falló: {exc}')


def edit_module(module,data):
    table,fields=SPECS[module]
    st.subheader(module)
    if module=='Proyectos': st.caption('El avance y el riesgo se calculan automáticamente a partir del cronograma y las tareas.')
    left,right=st.columns([1,2])
    with left:
        if not can_edit():
            st.info("Modo consulta: puede visualizar los registros, pero no editarlos.")
            st.dataframe(data[module],hide_index=True,use_container_width=True)
            return
        existing=data[module]
        items=['➕ Nuevo registro']+[f'{r["code"]} — {r.get("name",r.get("activity",r.get("topic","")))}' for _,r in existing.iterrows()]
        choice=st.selectbox('Registro a editar',items,key='pick_'+table)
        rid=None;row={}
        if choice!='➕ Nuevo registro':
            idx=items.index(choice)-1
            row=existing.iloc[idx].to_dict();rid=int(row['id'])
        with st.form('form_'+table,clear_on_submit=False):
            vals={}
            for key,label,kind,required,opt in fields:
                raw=row.get('name') if module=='Personal' and key=='code' and row else row.get(key)
                if pd.isna(raw) if raw is not None and not isinstance(raw,(list,dict)) else False:raw=None
                vals[key]=form_input(key,label+(' *' if required else ''),kind,required,opt,raw,'form_'+table+'_'+str(rid if rid is not None else 'nuevo'))
            submitted=st.form_submit_button('💾 Guardar cambios',use_container_width=True,type='primary')
            if submitted:
                vals={k:(v.isoformat() if isinstance(v,date) else int(v) if isinstance(v,bool) else v) for k,v in vals.items()}
                try:
                    save_record(module,vals,rid);st.success('Registro guardado correctamente.');st.rerun()
                except (ValueError,sqlite3.IntegrityError) as ex:
                    if isinstance(ex,sqlite3.IntegrityError) and 'deliverables.code' in str(ex):
                        st.error('Ya existe un entregable con ese ID. Selecciona el registro existente en «Registro a editar» para actualizar su enlace, o utiliza otro ID para uno nuevo.')
                    else:st.error(str(ex))
        bulk_delete_ui(module,data)
    with right:
        st.markdown('**Registros y seguimiento**')
        view=data[module].copy()
        if not view.empty:
            show=[k for k,_,_,_,_ in fields]
            if module=='Personal':view=view.rename(columns={'code':'Nombre'});show=['Nombre' if k=='code' else k for k in show]
            show+= [c for c in ['% Avance','Días restantes','Días atraso','Semáforo','Riesgo calculado','Situación','Alerta','Tareas activas','Proyectos asignados','Carga de trabajo'] if c in view.columns]
            q=st.text_input('🔎 Buscar en registros',key='search_'+table)
            if q:view=view[view.astype(str).apply(lambda x:x.str.contains(q,case=False,regex=False)).any(axis=1)]
            st.dataframe(view[show],hide_index=True,use_container_width=True,height=570)
            st.caption(f'{len(view)} registros visibles')
        else:st.info('No hay registros todavía. Usa el formulario para crear el primero.')

def add_examples():
    require_admin()
    with connection() as con:
        if con.execute('SELECT COUNT(*) FROM projects').fetchone()[0]:raise ValueError('Solo se pueden cargar los ejemplos si aún no existen proyectos.')
    d=lambda n:(date.today()+timedelta(days=n)).isoformat()
    for item in [dict(code='Andrea Torres (EJEMPLO)',name='Andrea Torres (EJEMPLO)',role='Jefe de Proyecto',specialty='Coordinación',capacity=4,status='Disponible'),dict(code='Luis Vega (EJEMPLO)',name='Luis Vega (EJEMPLO)',role='Ingeniero',specialty='Estructuras',capacity=3,status='Disponible'),dict(code='María Rojas (EJEMPLO)',name='María Rojas (EJEMPLO)',role='Modelador BIM',specialty='BIM',capacity=2,status='Disponible')]:
        save_record('Personal',{k:item.get(k) for k,_,_,_,_ in SPECS['Personal'][1]})
    save_record('Proyectos',dict(code='PR-EX-001',name='Proyecto Ejemplo - Edificio Multifamiliar (EJEMPLO)',client='Cliente ficticio',type='Expediente técnico',location='Lima - Perú',manager='Andrea Torres (EJEMPLO)',start_date=d(-20),due_date=d(12),status='En desarrollo',priority='Alta',notes='REGISTRO FICTICIO - reemplazar para producción'))
    tasks=[('T-EX-001','Arquitectura','Plantas y elevaciones', 'Andrea Torres (EJEMPLO)',-4,65,'En desarrollo',0),('T-EX-002','Estructuras','Planos de cimentación','Luis Vega (EJEMPLO)',3,80,'En revisión',0),('T-EX-003','BIM','Compatibilización de interferencias','María Rojas (EJEMPLO)',6,25,'En desarrollo',0),('T-EX-004','Eléctricas','Circuitos y tableros','Andrea Torres (EJEMPLO)',10,0,'No iniciado',0),('T-EX-005','Sanitarias','Redes de agua y desagüe','Luis Vega (EJEMPLO)',-2,100,'Aprobado',0)]
    for code,sp,activity,owner,due,progress,status,blocked in tasks:
        save_record('Plan de trabajo',dict(code=code,project_code='PR-EX-001',specialty=sp,activity=activity,delivery_code='',owner=owner,reviewer='Andrea Torres (EJEMPLO)',start_date=d(-18),due_date=d(due),progress=progress,status=status,priority='Alta' if due<0 else 'Media',updated_at=d(0),notes='EJEMPLO'))
    for code,sp,name,owner,due,status,obs in [('E-EX-001','Arquitectura','Planta arquitectónica','Andrea Torres (EJEMPLO)',-2,'Con observaciones',3),('E-EX-002','Estructuras','Cimentaciones','Luis Vega (EJEMPLO)',4,'En revisión interna',1),('E-EX-003','BIM','Modelo federado','María Rojas (EJEMPLO)',8,'En desarrollo',0)]:
        save_record('Entregables',dict(code=code,project_code='PR-EX-001',drawing_code=code.replace('E-EX','PL-EX'),name=name,specialty=sp,owner=owner,reviewer='Andrea Torres (EJEMPLO)',version='V01',due_date=d(due),actual_date=None,status=status,review_date=d(-1) if obs else None,correction_date=None,approval_date=None,notes='EJEMPLO',file_path=''))
    save_record('Control de cambios',dict(code='C-EX-001',project_code='PR-EX-001',request_date=d(-2),requester='Cliente ficticio',description='Cambio de distribución de ambientes',reason='Nueva necesidad del cliente',specialty='Arquitectura',affected_drawings='PL-EX-001',owner='Andrea Torres (EJEMPLO)',schedule_impact='Alto',new_due_date=d(16),approved_by='',approval_date=None,status='Solicitado',notes='EJEMPLO'))
    save_record(dict(code='R-EX-001',meeting_date=d(-4),project_code='PR-EX-001',meeting_type='Coordinación',participants='Equipo técnico',topic='Interferencias BIM',problem='Cruce de instalaciones',agreement='Validar y corregir interferencias detectadas',owner='María Rojas (EJEMPLO)',due_date=d(-1),status='Pendiente',notes='EJEMPLO'))

def priorities(data):
    t=data['Plan de trabajo'];e=data['Entregables'];p=data['Proyectos'];c=data['Control de cambios']
    out=[]
    def add(priority,kind,project,desc,owner,due,code):out.append(dict(Prioridad=priority,Tipo=kind,Proyecto=project,Detalle=desc,Responsable=owner,Vencimiento=str(due or ''),ID=code))
    for _,r in t.iterrows():
        if r.status in FINISHED_TASK or int(r.progress or 0)>=100:continue
        days=r['Días restantes']
        if pd.notna(days) and days<0:add('🔴 Alta','Tarea atrasada',r.project_code,r.activity,r.owner,r.due_date,r.code)
        elif pd.notna(days) and days==0:add('🔴 Alta','Vence hoy',r.project_code,r.activity,r.owner,r.due_date,r.code)
        elif pd.notna(days) and days==1:add('🟡 Media','Vence mañana',r.project_code,r.activity,r.owner,r.due_date,r.code)
    for _,r in e.iterrows():
        if r.status in FINISHED_DELIVERY:continue
        days=r['Días restantes']
        if pd.notna(days) and days<0:add('🔴 Alta','Entregable atrasado',r.project_code,r.name,r.owner,r.due_date,r.code)
        elif pd.notna(days) and days<=3:add('🟡 Media','Entregable próximo',r.project_code,r.name,r.owner,r.due_date,r.code)
        if r.status in ('Con observaciones','Observado por cliente'):add('🟡 Media','Entregable observado',r.project_code,r.name,r.owner,r.due_date,r.code)
    for _,r in p.iterrows():
        if r.status not in ('Terminado',) and r['Riesgo calculado'] in ('Alto','Crítico'):add('🔴 Alta','Proyecto en riesgo',r.code,r['name'],r.manager,r.due_date,r.code)
    for _,r in c.iterrows():
        if r.status in ('Solicitado',):add('🟡 Media','Cambio por aprobar',r.project_code,r.description,r.owner,r.new_due_date,r.code)
    return pd.DataFrame(out,columns=['Prioridad','Tipo','Proyecto','Detalle','Responsable','Vencimiento','ID'])

def dashboard(data):
    p,t,e,c,pe=[data[k] for k in ['Proyectos','Plan de trabajo','Entregables','Control de cambios','Personal']]
    active=p[~p.status.isin(['Terminado'])] if not p.empty else p
    metrics=[('Proyectos activos',len(active)),('Proyectos atrasados',int((active.Situación=='🔴 Atrasado').sum()) if not active.empty else 0),('Proyectos en riesgo',int(active['Riesgo calculado'].isin(['Alto','Crítico']).sum()) if not active.empty else 0),('Tareas pendientes',int((~t.status.isin(FINISHED_TASK)&(t.progress.fillna(0)<100)).sum()) if not t.empty else 0),('Tareas atrasadas',int((t['Días atraso']>0).sum()) if not t.empty else 0),('Entregables pendientes',int((~e.status.isin(FINISHED_DELIVERY)).sum()) if not e.empty else 0),('Entregables en revisión',int(e.status.str.contains('revisión',case=False,na=False).sum()) if not e.empty else 0),('Con observaciones',int(e.status.isin(['Con observaciones','Observado por cliente']).sum()) if not e.empty else 0),('Cambios pendientes',int(c.status.isin(['Solicitado']).sum()) if not c.empty else 0),]
    st.title('ALTIVIA  |  Panel de control')
    st.caption(f'Consultoría e ingeniería • Actualizado al {TODAY.strftime("%d/%m/%Y")} • Datos almacenados localmente')
    # Tarjetas fluidas: 5 columnas en escritorio, 2 en móvil; contraste independiente del tema.
    cards=''.join(
        '<div class="altivia-kpi-card"><div class="altivia-kpi-label">'
        +escape(str(label))+'</div><div class="altivia-kpi-value">'
        +escape(str(val))+'</div></div>' for label,val in metrics
    )
    st.markdown('<div class="altivia-kpi-grid">'+cards+'</div>',unsafe_allow_html=True)
    st.divider()
    st.subheader('🚨 PRIORIDADES DE HOY')
    urgent=priorities(data)
    if urgent.empty:st.success('No hay alertas con los registros actuales.')
    else:
        selected=st.multiselect('Filtrar prioridades',urgent['Tipo'].unique(),default=list(urgent['Tipo'].unique()))
        st.dataframe(urgent[urgent['Tipo'].isin(selected)],use_container_width=True,hide_index=True,height=340)
    st.subheader('Resumen de proyectos')
    if not p.empty:
        st.dataframe(p[['code','name','manager','% Avance','due_date','Días restantes','status','Riesgo calculado','Situación']].rename(columns={'code':'ID','name':'Proyecto','manager':'Responsable','due_date':'Fecha entrega','status':'Estado'}),hide_index=True,use_container_width=True)
    else:st.info('Registra un proyecto para comenzar.')
    st.subheader('Análisis visual')
    plots=[('Avance por proyecto',p,'name','% Avance','bar'),('Proyectos por estado',p,'status',None,'pie'),('Tareas por estado',t,'status',None,'pie'),('Entregables por estado',e,'status',None,'pie'),('Tareas por persona',pe,'name','Tareas activas','bar'),('Proyectos por nivel de riesgo',p,'Riesgo calculado',None,'pie')]
    for i in range(0,len(plots),2):
        cols=st.columns(2)
        for col,(title,frame,x,y,kind) in zip(cols,plots[i:i+2]):
            with col:
                st.markdown('**'+title+'**')
                if frame.empty:st.info('Sin datos');continue
                if kind=='pie':
                    counts=frame[x].fillna('Sin definir').value_counts().reset_index();counts.columns=['Categoría','Cantidad']
                    fig=px.pie(counts,names='Categoría',values='Cantidad',hole=0.45)
                else:fig=px.bar(frame,x=x,y=y,labels={x:'',y:y or ''},text=y)
                fig.update_layout(height=315,margin=dict(l=5,r=5,t=10,b=5),showlegend=(kind=='pie'))
                st.plotly_chart(fig,use_container_width=True)

def checklist_page():
    st.title('Checklist de control de calidad')
    deliveries=df('deliverables')
    if deliveries.empty:st.info('Primero registre un entregable.');return
    dc=st.selectbox('Entregable',deliveries.code.tolist(),format_func=lambda code: f'{code} — {deliveries.loc[deliveries.code==code,"name"].iloc[0]}')
    with connection() as con:
        existing={r['criterion']:dict(r) for r in con.execute('SELECT * FROM checklist WHERE delivery_code=?',(dc,))}
    if not can_edit():
        st.info('Modo consulta: el checklist es de solo lectura.')
        st.dataframe(pd.DataFrame([{'Criterio':c,'Resultado':existing.get(c,{}).get('result','PENDIENTE'),'Nota':existing.get(c,{}).get('notes','')} for c in CHECKS]),hide_index=True,use_container_width=True)
        return
    with st.form('checklist_form'):
        vals=[]
        for criterion in CHECKS:
            prev=existing.get(criterion,{});curr=prev.get('result','PENDIENTE')
            a,b=st.columns([3,2])
            with a:status=st.selectbox(criterion,['PENDIENTE','OK','NO APLICA'],index=['PENDIENTE','OK','NO APLICA'].index(curr),key='chk_'+criterion)
            with b:note=st.text_input('Observación: '+criterion,value=prev.get('notes') or '',key='nt_'+criterion,label_visibility='collapsed',placeholder='Observación (opcional)')
            vals.append((criterion,status,note))
        save=st.form_submit_button('Guardar checklist',type='primary')
        if save:
            with connection() as con:
                for criterion,status,note in vals:
                    con.execute('INSERT INTO checklist(delivery_code,criterion,result,notes) VALUES(?,?,?,?) ON CONFLICT(delivery_code,criterion) DO UPDATE SET result=excluded.result,notes=excluded.notes',(dc,criterion,status,note))
            st.success('Checklist guardado');st.rerun()
    counts={s:sum(1 for v in CHECKS if existing.get(v,{}).get('result','PENDIENTE')==s) for s in ['OK','PENDIENTE','NO APLICA']}
    applicable=counts['OK']+counts['PENDIENTE']
    pct=round(100*counts['OK']/applicable,1) if applicable else 100
    st.metric('Cumplimiento (excluye NO APLICA)',f'{pct}%')
    st.progress(pct/100)
    st.caption(f'OK: {counts["OK"]} · Pendientes: {counts["PENDIENTE"]} · No aplica: {counts["NO APLICA"]}')

def versions_page():
    st.title('Historial de versiones de entregables')
    delivs=df('deliverables')
    if delivs.empty:st.info('Primero registre un entregable.');return
    if not can_edit():
        st.info('Modo consulta: historial de versiones.')
        st.dataframe(df('versions'),hide_index=True,use_container_width=True)
        return
    with st.form('versions_form'):
        dc=st.selectbox('Entregable',delivs.code.tolist())
        ver=st.text_input('Nueva versión',value='V02')
        notes=st.text_area('Descripción de revisión / cambios')
        submit=st.form_submit_button('Registrar versión',type='primary')
        if submit:
            try:
                if not ver.upper().startswith('V'):raise ValueError('Formato: V01, V02, etc.')
                with connection() as con:
                    con.execute('INSERT INTO versions(delivery_code,version,registered_at,notes) VALUES(?,?,?,?)',(dc,ver.upper(),date.today().isoformat(),notes))
                    con.execute('UPDATE deliverables SET version=? WHERE code=?',(ver.upper(),dc))
                st.success('Versión incorporada al historial');st.rerun()
            except (ValueError,sqlite3.IntegrityError) as exc:st.error(str(exc))
    st.dataframe(df('versions'),hide_index=True,use_container_width=True)
    st.caption('El historial no se sobrescribe al cambiar la versión vigente del entregable.')

def export_xlsx(data):
    dest=io.BytesIO()
    with pd.ExcelWriter(dest,engine='openpyxl',datetime_format='DD/MM/YYYY',date_format='DD/MM/YYYY') as writer:
        for module,frame in data.items():
            frame=frame.drop(columns=['id','created_at'],errors='ignore')
            frame.to_excel(writer,sheet_name=module[:31],index=False)
            sh=writer.sheets[module[:31]];sh.freeze_panes='A2';sh.auto_filter.ref=sh.dimensions
            from openpyxl.styles import PatternFill,Font
            from openpyxl.utils import get_column_letter
            for cell in sh[1]:cell.fill=PatternFill('solid',fgColor='17365D');cell.font=Font(color='FFFFFF',bold=True)
            for i,col in enumerate(frame.columns,1):sh.column_dimensions[get_column_letter(i)].width=min(42,max(14,len(str(col))+3))
        for table,label in [('versions','VERSIONES'),('checklist','CHECKLIST'),('audit','AUDITORIA')]:
            frame=df(table).drop(columns=['id'],errors='ignore');frame.to_excel(writer,sheet_name=label,index=False)
    dest.seek(0);return dest

def setup_style():
    st.markdown('''<style>
    [data-testid="stSidebar"]{background:#102b48}
    [data-testid="stSidebar"] :is(p,span,label,h1,h2,h3){color:#f2f6fb!important}
    /* Sidebar: botón de salida con contraste alto en todos los temas. */
    [data-testid="stSidebar"] button[kind="secondary"],
    [data-testid="stSidebar"] .stButton > button{
      background:#eaf2fb!important;color:#102b48!important;
      border:1px solid #acc7e3!important;border-radius:9px!important;
    }
    [data-testid="stSidebar"] .stButton > button :is(p,span,div){color:#102b48!important}
    [data-testid="stSidebar"] .stButton > button:hover{background:#cde3f8!important;color:#102b48!important}
    /* Los KPI usan colores explícitos, sin depender del tema claro/oscuro del móvil. */
    .altivia-kpi-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin:14px 0 20px}
    .altivia-kpi-card{background:#eff4fa;color:#142c49!important;border:1px solid #d4e0eb;
      border-radius:11px;padding:12px 13px;min-width:0;min-height:97px;box-sizing:border-box}
    .altivia-kpi-label{font-size:0.84rem;line-height:1.3;font-weight:600;color:#334d67!important;overflow-wrap:anywhere}
    .altivia-kpi-value{font-size:1.75rem;line-height:1.2;font-weight:700;margin-top:9px;color:#102b48!important}
    .block-container{padding-top:1.5rem}
    @media(max-width:900px){
      .altivia-kpi-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}
      .altivia-kpi-card{padding:11px;min-height:84px}
      .altivia-kpi-label{font-size:0.78rem}
      .altivia-kpi-value{font-size:1.5rem;margin-top:7px}
      .block-container{padding-left:0.9rem;padding-right:0.9rem}
    }
    @media(max-width:360px){.altivia-kpi-grid{gap:7px}.altivia-kpi-card{padding:9px}.altivia-kpi-value{font-size:1.35rem}}
    
    </style>''',unsafe_allow_html=True)

def main():
    initialize();setup_style()
    if not initialize_auth():
        st.error('Configuración inicial: establezca ALTIVIA_ADMIN_PASSWORD (mínimo 6 caracteres) antes de iniciar la aplicación. Consulte README_SEGURIDAD.txt.')
        st.stop()
    if 'user_id' in st.session_state:
        with connection() as con:
            current=con.execute('SELECT username,full_name,role,active FROM users WHERE id=?',(st.session_state['user_id'],)).fetchone()
        if not current or not current['active']:
            for key in ('user_id','role','username','full_name','client_preview'):st.session_state.pop(key,None)
        else:
            st.session_state['role']=current['role'];st.session_state['username']=current['username'];st.session_state['full_name']=current['full_name']
    if not st.session_state.get('user_id'):
        login_page();return
    with st.sidebar:
        st.markdown('# ALTIVIA')
        st.markdown('**GESTOR DE PROYECTOS**')
        st.caption('Ingeniería · Consultoría · Planos')
        st.caption(f"{st.session_state['full_name']} · {st.session_state['role']}")
        if st.button('Cerrar sesión'):
            for key in ('user_id','role','username','full_name','client_preview'):st.session_state.pop(key,None)
            st.rerun()
        pages=['Mis documentos','Mi cuenta'] if st.session_state.get('role')=='Cliente' else ['Dashboard','Proyectos','Plan de trabajo','Entregables','Control de cambios','Personal','Checklist planos','Versiones','Mi cuenta']
        if can_edit():pages+=['Administrar usuarios','Exportación y respaldo']
        page=st.radio('Navegación',pages)
        st.divider();st.caption('🔒 Datos en SQLite local (altivia.db)')
    data=decorate()
    if page=='Mis documentos':client_portal()
    elif page=='Dashboard':dashboard(data)
    elif page in SPECS and st.session_state.get('role')!='Cliente':edit_module(page,data)
    elif page=='Checklist planos' and st.session_state.get('role')!='Cliente':checklist_page()
    elif page=='Versiones' and st.session_state.get('role')!='Cliente':versions_page()
    elif page=='Mi cuenta':my_account()
    elif page=='Administrar usuarios':user_management()
    else:
        require_admin()
        st.title('Exportación, respaldo e instalación')
        st.download_button('📥 Exportar registros a Excel (.xlsx)',data=export_xlsx(data),file_name='ALTIVIA_Registro_Proyectos.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',type='primary')
        backup_path=backup_database()
        with open(backup_path,'rb') as f:backup=f.read()
        st.download_button('💾 Descargar respaldo íntegro (.db)',data=backup,file_name=f'ALTIVIA_respaldo_{TODAY.isoformat()}.db',mime='application/octet-stream')
        st.warning('El Excel exportado sirve para reportes. La base SQLite es la fuente principal: conserva versiones y checklists. No edite la base mientras la aplicación está abierta.')
        if not len(data['Proyectos']):
            if st.button('Cargar datos ficticios de demostración'):
                try:add_examples();st.success('Ejemplos creados');st.rerun()
                except ValueError as exc:st.error(str(exc))
        st.subheader('Bitácora de actividad');st.dataframe(df('audit').head(100),hide_index=True,use_container_width=True)
        reset_database_ui()
        st.caption('Esta instalación usa SQLite local: no la publique en Streamlit Community Cloud para datos reales. Para varios usuarios, migre a PostgreSQL y alojamiento privado.')

if __name__=='__main__':main()
msal>=1.30.0
