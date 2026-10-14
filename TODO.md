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

- [ ] `v0.9.1` — **UI CRUD**: botones y modales para crear carpeta/archivo,
  renombrar y borrar (sin editar contenido), con confirmación escribiendo el
  mismo nombre.
  - estado: `PENDIENTE`
- [ ] `v0.9.2` — **Tests de flujo**: cobertura de borde del formulario y de la
  confirmación desde el navegador.
  - estado: `PENDIENTE`
- [ ] `v0.9.3` — **Documentación operativa**: ejemplos `curl` de cada endpoint CRUD.
  - estado: `PENDIENTE`
- [ ] `v0.10.0` — **Streaming multipart**: parser de subida con límite estricto
  previo y limpieza garantizada de temporales.
  - estado: `PENDIENTE`
- [ ] `v0.10.1` — **Cobertura de symlinks y permisos** en tests de borde.
  - estado: `PENDIENTE`
- [ ] `v1.0.0` — **Release estable**: suite completa en verde, instalador
  verificado de punta a punta, licencia definida y notas de release.
  - estado: `PENDIENTE`

## DOING

> Tarea en curso. Una sola a la vez.

- *(vacío)*

## DONE

> Tareas terminadas, con commit finalizado y tag. Orden: más reciente arriba.

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
