# WebShare

Servidor HTTP local para compartir, subir y descargar archivos dentro de una red
LAN. Escrito únicamente con la librería estándar de Python, sin dependencias
externas y sin necesidad de `venv`.

## Características

- Servidor en Python 3.10+ sin dependencias externas.
- Navegación de carpetas.
- Subidas con nombres únicos para evitar sobrescrituras.
- Barra de progreso de subida en el navegador.
- Descargas en streaming.
- Contador de tiempo activo dinámico.
- API JSON para `curl` (`/api/status`, `/api/list`, `/api/info`, `/api/download`).
- Modo solo lectura y límite de subida configurable.
- Token opcional para subidas y para el CRUD (crear, renombrar, borrar).
- Sandbox: todas las operaciones quedan dentro de la carpeta compartida.
- Cierre limpio con `Ctrl+C`.

## Requisitos

- Python 3.10 o superior.
- `git` (solo para instalar y actualizar desde el repositorio).

## Instalación

```sh
curl -fsSL https://raw.githubusercontent.com/braiidev/webshare/main/install.sh | sh
```

El instalador coloca el código en `~/.local/share/webshare` y crea el comando
`~/.local/bin/webshare`. No requiere `sudo`.

## Uso

```sh
webshare
webshare --port 8080
webshare --dir /carpeta/a/compartir --bind 0.0.0.0
webshare --read-only
webshare --token "mi-token-secreto"
WEBSHARE_TOKEN=... webshare
```

Valores por defecto:

- directorio: `~/Descargas`
- bind: `0.0.0.0`
- puerto: `8000`

## Opciones

| Opción | Default | Descripción |
| --- | --- | --- |
| `--dir` | `~/Descargas` | Carpeta compartida (raíz del sandbox). |
| `--bind` | `0.0.0.0` | Dirección de escucha. |
| `--port` | `8000` | Puerto HTTP. |
| `--read-only` | apagado | Desactiva escrituras; las descargas siguen permitidas. |
| `--max-upload-mb` | `5120` | Límite de subida en MB. |
| `--token` / `WEBSHARE_TOKEN` | vacío | Token para subir y para el CRUD. |
| `--quiet` | apagado | Desactiva los logs HTTP. |
| `--update` | - | Actualiza repositorio y symlink. |
| `--uninstall` | - | Desinstala symlink y repositorio. |
| `--version` | - | Muestra la versión. |

## Endpoints

- `GET /` — interfaz web.
- `GET /health` — JSON con estado, uptime, versión, directorio y read-only.
- `GET /version` — JSON con la versión.
- `GET /api/status` — alias JSON del estado (uptime, directorio, read-only).
- `GET /api/list?path=...` — listado JSON de una carpeta.
- `GET /api/info?path=...` — metadatos JSON de un archivo o carpeta.
- `GET /api/download?path=...` — descarga en streaming (alias de `/download`).
- `GET /download?path=...` — descarga en streaming (legacy).
- `POST /upload` — subida multipart.
- `POST /api/mkdir?path=<dir>` + `name` — crear carpeta (requiere token).
- `POST /api/create?path=<dir>` + `name` — crear archivo vacío (requiere token).
- `POST /api/rename?path=<src>` + `new_name` — renombrar (requiere token).
- `DELETE /api/delete?path=<target>&confirm=<nombre>` — borrar archivo o carpeta vacía (requiere token; `POST` también aceptado).

## CRUD (crear, renombrar, borrar)

El CRUD solo se habilita si el servidor arrancó con token (`--token` o
`WEBSHARE_TOKEN`). Sin token, el CRUD responde `403 crud_disabled` y las subidas
siguen funcionando como siempre.

El token se envía con `Authorization: Bearer <token>`, `X-Webshare-Token`, o el
parámetro `token`.

Reglas:

- El borrado exige `confirm` igual al nombre exacto del recurso.
- Las carpetas solo se borran si están vacías.
- Nunca se borra la raíz compartida.
- Renombrar no sobrescribe: si el destino existe, responde `409 resource_exists`.
- Todo queda dentro del sandbox (`--dir`).

## Seguridad

- **Sandbox obligatorio**: solo se opera dentro de `--dir`; se rechazan rutas
  absolutas y cualquier reversal path (`..`, `%2e%2e`, barras invertidas, null).
- Resolución de symlinks con verificación de pertenencia al root.
- Validación de nombres y evitación de sobrescrituras.
- Headers HTTP: CSP, `nosniff`, frame deny, referrer no y `no-store`.
- Límite de tamaño de subida.
- Token opcional para uploads.
- Modo solo lectura disponible.

## Actualización y desinstalación

```sh
webshare --update
webshare --uninstall
webshare --uninstall --yes
```

## Desarrollo y pruebas

```sh
python3 webshare.py --port 8123
python3 tests/test_webshare.py
python3 -m unittest tests.test_webshare -v
```

## Roadmap

El plan por etapas y el estado del proyecto viven en [`TODO.md`](TODO.md)
(tablero Kanban). El historial real se consulta con `git log`.

## Licencia

Pendiente definir por el mantenedor.
