# Proyecto — Webshare

Plan de trabajo por etapas **SemVer**. Cada casilla del tablero es una tarea
atomizada (una sola cosa, verificable y etiquetable).

## Método de trabajo

1. **Atomizar**: dividir el objetivo en la tarea más chica posible.
2. **Etiquetar**: asignarle un `vMAYOR.MENOR.PARCHE` (SemVer).
3. **Planificar**: la tarea nueva entra en **NEXT**.
4. **Ejecutar**: al empezar, se mueve a **DOING**.
5. **Cerrar**: cuando pasa tests y se crea el commit con su tag, se mueve a **DONE**.
6. **Sincronizar**: el tablero se corrige desde el `git log`, nunca al revés.

Estados permitidos: `PENDIENTE`, `EN CURSO`, `HECHO`.

---

## NEXT

> Tareas planificadas, todavía no iniciadas.

- [ ] `v1.0.0` — **Release estable**: suite completa en verde, instalador
  verificado de punta a punta, licencia definida y notas de release.
  - estado: `PENDIENTE`

## DOING

> Tarea en curso. Una sola a la vez.

- *(vacío)*

## DONE

> Tareas terminadas, con commit finalizado y tag. Orden: más reciente arriba.

- [x] `v0.11.0` — **Hardening (auditoría de seguridad)**: cierra los hallazgos de
  una auditoría del backend. (1) El CRUD autentica **antes** de leer el cuerpo y
  topa los formularios en 64 KiB (antes buffereaba un cuerpo arbitrario sin token:
  DoS anónimo). (2) En subidas con token, se valida la cabecera o un campo `token`
  previo al primer archivo: ya no se espoolea nada antes de autenticar. (3) Timeout
  de conexión (30 s) y tope de conexiones simultáneas (64) contra slowloris. (4)
  `clean_filename` descarta NUL y caracteres de control (antes un nombre con NUL
  lanzaba `ValueError` no capturado). (5) `send_info` protege `stat()` (carreras →
  `404 not_found`) y los temporales de subida huérfanos se limpian al arrancar.
  Además, `install.sh --uninstall` funciona vía `curl | bash` (antes hacía
  `exec "$0"` con `$0`=`bash`). 66/66 tests; instalador 18/18.
  - estado: `HECHO` · tag `v0.11.0` (ver `git log`)

- [x] `v0.10.2` — **Higiene de temporales de subida**: el archivo temporal
  `.webshare-upload-*.part` se creaba en la raíz del recurso compartido y, mientras
  la subida estaba en curso, aparecía en el listado y era accesible por la API. Ahora
  está reservado: se filtra de `/api/list`, se rechaza su acceso (`403 path_forbidden`
  en descargar/info/renombrar/borrar) y no se puede crear con ese nombre
  (`400 invalid_name`). Se documenta además que los archivos y carpetas ocultos
  (`.algo`) se listan y gestionan con normalidad. Nueva clase `SpoolHygieneWebShare`.
  60/60 tests.
  - estado: `HECHO` · tag `v0.10.2` (ver `git log`)
- [x] `v0.10.1` — **Cobertura de symlinks y permisos + robustez**: dos clases nuevas
  de tests de borde. `SymlinkWebShare` fija que un symlink que escapa del sandbox no
  se descarga, lista, renombra, borra ni sirve de base para `mkdir`/upload
  (`403 path_forbidden` / `400 symlink_not_allowed`), que un symlink interno sí se
  descarga y que un bucle de symlinks no tumba el servidor. `PermissionsWebShare`
  (se omite como root) verifica que permisos denegados devuelven JSON limpio. Los
  tests destaparon y se corrigieron dos fallos de robustez: listar una carpeta sin
  permiso de lectura y descargar un archivo sin permiso cortaban la conexión; ahora
  responden `500 read_failed` (el archivo se abre antes de enviar cabeceras).
  55/55 tests.
  - estado: `HECHO` · tag `v0.10.1` (ver `git log`)
- [x] `v0.10.0` — **Streaming multipart**: parser incremental (`MultipartScanner` +
  `LimitedReader`) que no carga el cuerpo en memoria; límite verificado con
  `Content-Length` antes de leer y reaplicado durante la lectura; archivos
  espooleados a `.webshare-upload-*.part` y movidos con `os.replace` (fallback
  `EXDEV`), con limpieza garantizada de temporales en todo camino de error. Los
  errores de subida pasan a ser JSON (`invalid_length`, `empty_upload`,
  `invalid_multipart`, `upload_too_large`, `no_files`, `token_invalid`, `read_only`).
  Verificado: 50 MiB con integridad OK y ~28 MiB de pico en el server. 41/41 tests.
  - estado: `HECHO` · tag `v0.10.0` (ver `git log`)
- [x] `v0.9.3` — **Documentación operativa + robustez de arranque**: sección
  "Ejemplos con `curl`" en `README.md` (auth, listar/descargar, mkdir, upload,
  rename, delete; clave por cabecera) y test que fija que un `--dir` inexistente
  se cree al arrancar (incluidas subcarpetas). 32/32 tests.
  - estado: `HECHO` · tag `v0.9.3` (ver `git log`)
- [x] `v0.9.2` — **Tests de flujo (HTTP, sin navegador)**: nueva clase `FlowWebShare`
  que reproduce el recorrido real de la UI por HTTP/JSON — clave (`/api/auth`),
  crear carpeta, subir (multipart + `X-Webshare-Token`), listar, descargar,
  renombrar y borrar — más guardas del shell (sin token embebido), estáticos con
  MIME/`ETag`/`304`, CSP `script-src 'self'` y verificación de "sin emoji" en la UI.
  31/31 tests.
  - estado: `HECHO` · tag `v0.9.2` (ver `git log`)
- [x] `v0.9.1-bis` — **fix UI**: `#uptimeBadge` en vivo (antes solo al navegar o
  refrescar) y bordes coherentes en modo oscuro; Simplex fijaba `#eeeeee` en
  card, list-group, nav-tabs, pagination, modal, popover y offcanvas, ahora
  repuntados a `#3c3c3c` en dark.
  - estado: `HECHO` · incluido en el commit/tag `v0.9.1` (ver `git log`)
- [x] `v0.9.1` — **Frontend modular + UI CRUD**: shell estático (`static/index.html`)
  con CSS/JS propios servidos desde `/static`, sobre Bootswatch **Simplex** y
  Bootstrap Icons locales (sin CDN, sin emoji) y toggle claro/oscuro. Listado
  renderizado en el cliente; modales para nueva carpeta, renombrar y borrar (con
  `confirm` por nombre). El token deja de embeberse en el HTML: se guarda en
  `sessionStorage`, se valida con `/api/auth` y viaja por cabecera. Se elimina
  `/api/create`; se endurecen nombres (`400 invalid_name`) y se rechazan symlinks
  (`400 symlink_not_allowed`). `/api/status` expone `token_required`/`crud_enabled`;
  CSP con `script-src 'self'`; assets con `ETag`/`304`. 25/25 tests.
  - estado: `HECHO` · tag `v0.9.1` (commit actual, ver `git log`)
- [x] `v0.9.0` — CRUD backend con token: `mkdir`, `create`, `rename` y `delete`
  (`DELETE`/`POST`), sandbox, confirmación por nombre, carpeta solo vacía y nunca
  la raíz; renombrar no sobrescribe. Upload sigue permitido sin token; CRUD solo
  con token. 19/19 tests.
  - estado: `HECHO` · tag `v0.9.0` (commit actual, ver `git log`)
- [x] `v0.8.2-bis` — fix necesario: eliminar documentación innecesaria
  (`CHANGELOG.md` y `docs/`), dejar solo `README.md` y `TODO.md`.
  - estado: `HECHO` · incluido en el commit/tag `v0.8.2` (ver `git log`)
- [x] `v0.8.2` — docs: tablero Kanban y documentación mínima (solo `README.md` + `TODO.md`).
  - estado: `HECHO` · tag `v0.8.2` (commit actual, ver `git log`)
- [x] `v0.8.1` — docs/security: sandbox, prohibición de reversal path y tests.
  - estado: `HECHO` · commit `9102de0` · tag `v0.8.1`
- [x] `v0.8.0` — api: `/api/status`, `/api/list`, `/api/info` y `/api/download`
  con JSON estructurado.
  - estado: `HECHO` · commit `b954083` · tag `v0.8.0`
- [x] `v0.7.0` — docs/hardening: documentación, opciones y changelog.
  - estado: `HECHO` · commit `028dd09` · tag `v0.7.0`
- [x] `v0.6.0` — hardening: tests de integración (health, headers, traversal, uploads).
  - estado: `HECHO` · commit `a250854` · tag `v0.6.0`
- [x] `v0.5.2` — reliability: update/install tolerantes a cambios locales (stash).
  - estado: `HECHO` · commit `ad72f34` · tag `v0.5.2`
- [x] `v0.5.1` — security: token opcional para uploads y logging `--quiet`.
  - estado: `HECHO` · commit `335b86d` · tag `v0.5.1`
- [x] `v0.5.0` — api: `/health` JSON, `/version` y logging configurable.
  - estado: `HECHO` · commit `e5a9c35` · tag `v0.5.0`
- [x] `v0.4.0` — ux/security: upload con progress en frontend y escritura atómica.
  - estado: `HECHO` · commit `c18676c` · tag `v0.4.0`
- [x] `v0.3.1` — fix: `git pull` explícito a `origin main`.
  - estado: `HECHO` · commit `c4d9012` · tag `v0.3.1`
- [x] `v0.3.0` — security: headers HTTP, modo solo lectura y límite de upload.
  - estado: `HECHO` · commit `8f35c9f` · tag `v0.3.0`
- [x] `v0.2.0` — version bump.
  - estado: `HECHO` · commit `5d05be3` · tag `v0.2.0`
- [x] `v0.2` — ux: contador de tiempo activo dinámico en el frontend.
  - estado: `HECHO` · commit `0ab917e` · tag `v0.2`
- [x] `v0.1.1` — fix: manejo de update con fallback offline.
  - estado: `HECHO` · commits `6789de8`, `1947588` · tag `v0.1.1`
- [x] `v0.1` — refactor: migrar monolito y agregar estructura, `install.sh`
  y update/uninstall.
  - estado: `HECHO` · commit `0905976` · tag `v0.1`
- [x] `v0.0` — docs: documentar WebShare y roadmap.
  - estado: `HECHO` · commit `150bd3e` · tag `v0.0`

---

## NOTA IMPORTANTE

El estado real del proyecto **se lee del log de git**, no del tablero. Antes de
planificar o de mover una tarjeta, verificar siempre con:

```sh
git log --oneline --decorate --all
git tag --sort=-creatordate
git status --short
```

Reglas:

- Una tarea pasa a `DONE` **solo** cuando su commit ya quedó finalizado con tag SemVer.
- Una tarea en `DOING` debe tener su commit en curso, no solo la idea escrita.
- Si el tablero y el `git log` no coinciden, **manda el `git log`** y se corrige el tablero.
