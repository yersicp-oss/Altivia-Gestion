"""Lanzador local: pide la clave inicial solo cuando no existen usuarios."""
import os
import sys
import sqlite3
import subprocess
import getpass

folder=os.path.dirname(os.path.abspath(__file__))
db=os.getenv('ALTIVIA_DB',os.path.join(folder,'altivia.db'))
users=0
if os.path.isfile(db):
    with sqlite3.connect(db) as conn:
        has_table=conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='users'").fetchone()[0]
        if has_table:users=conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
if not users:
    print('CONFIGURACION INICIAL ALTIVIA')
    print('Se creara el usuario admin. Su contrasena no se mostrara en pantalla.')
    while True:
        p=getpass.getpass('Cree la contrasena del administrador (min. 6 caracteres): ')
        q=getpass.getpass('Repita la contrasena: ')
        if len(p)>=6 and p==q:break
        print('La contrasena debe tener al menos 6 caracteres y coincidir.')
    os.environ['ALTIVIA_ADMIN_PASSWORD']=p
subprocess.run([sys.executable,'-m','streamlit','run',os.path.join(folder,'app.py')],check=False)
