# CRM Solicitudes

Tablero de leads del formulario **https://latinbien.com/solicitud** (datos desde 2026-10-22 en adelante).

## Ver tablero

**https://latinbienti-sys.github.io/crmsolicitudes/**

## Actualizar

```bash
python generar.py
git add index.html data.json
git commit -m "Actualizar tablero"
git push
```

El tablero se actualiza solo cada hora (GitHub Actions, consulta de solo lectura a Odoo) y se publica desde GitHub Pages (rama `main`).
