# WebShare TODO

## v0.0 — Repositorio y documentación

- [x] Crear repositorio local en `/home/anto/Proyectos/Webshare`.
- [x] Agregar README.md.
- [x] Agregar .gitignore.
- [x] Primer commit `v0.0`.
- [ ] Definir licencia final.
- [ ] Crear repositorio remoto `braiidev/webshare`.
- [ ] Publicar `install.sh` en `main`.

## v0.1 — Migración del monolito

- [x] Mover `webshare.py` al proyecto.
- [x] Agregar version, rutas y helpers de auto-update/uninstall.
- [x] Tests de paths básicos.
- [ ] Validar instalación en `~/.local/share/webshare`.
- [ ] Reemplazar symlink global mediante `install.sh`.

## v0.2 — Tiempo real en frontend

- [ ] Servir timestamp de arranque.
- [ ] Actualizar contador cada segundo con JavaScript.
- [ ] Fallback si JavaScript falla.

## v0.3 — Seguridad HTTP

- [ ] Content-Security-Policy.
- [ ] X-Content-Type-Options.
- [ ] X-Frame-Options.
- [ ] Referrer-Policy.
- [ ] Cache-Control adecuado.

## v0.4 — Robustez de uploads

- [ ] Límite configurable de tamaño.
- [ ] Parser multipart con streaming o límite estricto previo.
- [ ] Cleanup de archivos temporales.
- [ ] Upload en modo solo lectura desactivado.

## v0.5 — API y operación

- [ ] `/health` en JSON.
- [ ] `--read-only`.
- [ ] Token opcional para escrituras.
- [ ] Logging configurable por verbosidad.

## v1.0 — Estable

- [ ] Suite de tests passing.
- [ ] Documentación completa.
- [ ] Instalador verificado.
- [ ] Primera release etiquetada.
