# WebShare

Servidor HTTP local para compartir, subir y descargar archivos dentro de una red
LAN. Escrito únicamente con la librería estándar de Python, sin dependencias
externas y sin necesidad de `venv`.

## Características

- Servidor en Python 3.10+ sin dependencias externas.
- Navegación de carpetas.
- Interfaz modular (CSS/JS propios) sobre Bootswatch **Simplex**, servida localmente sin CDN, con tema claro/oscuro.
- Iconografía consistente con Bootstrap Icons (sin emoji).
- Listado renderizado en el cliente desde `/api/list` (el servidor solo sirve el shell).
- Subidas con nombres únicos para evitar sobrescrituras.
- Barra de progreso de subida en el navegador.
- Descargas en streaming.
- Contador de tiempo activo dinámico.
- API JSON para `curl` (`/api/status`, `/api/list`, `/api/info`, `/api/download`).
- Modo solo lectura y límite de subida configurable.
- Subidas seguras por defecto: requieren token; `--allow-anonymous-upload` habilita el modo "drop" en una LAN de confianza.
- Token para subidas y para el CRUD (crear carpeta, renombrar, borrar).
- El token se guarda en `sessionStorage` y viaja por cabecera; **nunca** se embebe en el HTML.
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
| `--allow-anonymous-upload` | apagado | Sin token, permite subir igual (modo "drop" anónimo). |
| `--quiet` | apagado | Desactiva los logs HTTP. |
| `--update` | - | Actualiza repositorio y symlink. |
| `--uninstall` | - | Desinstala symlink y repositorio. |
| `--version` | - | Muestra la versión. |

## Endpoints

- `GET /` — interfaz web (shell; el listado se arma en el cliente).
- `GET /static/<archivo>` — assets locales (CSS, JS, fuentes, vendor).
- `GET /health` — JSON con estado, uptime, versión, directorio, read-only y flags CRUD.
- `GET /version` — JSON con la versión.
- `GET /api/status` — alias JSON del estado (uptime, nombre de la raíz, read-only, `token_required`, `crud_enabled`). No expone la ruta absoluta.
- `GET /api/auth` — valida el token de escritura (`200 ok` si es correcto, `403` si no).
- `GET /api/list?path=...` — listado JSON de una carpeta.
- `GET /api/info?path=...` — metadatos JSON de un archivo o carpeta.
- `GET /api/download?path=...` — descarga en streaming (alias de `/download`).
- `GET /download?path=...` — descarga en streaming (legacy).
- `POST /upload` — subida multipart.
- `POST /api/mkdir?path=<dir>` + `name` — crear carpeta (requiere token).
- `POST /api/rename?path=<src>` + `new_name` — renombrar (requiere token).
- `DELETE /api/delete?path=<target>&confirm=<nombre>` — borrar archivo o carpeta vacía (requiere token; `POST` también aceptado).

> `/api/create` (crear archivo vacío) se eliminó: no hay editor de contenido.

Las subidas se procesan **en streaming**: el cuerpo `multipart` se parsea por
bloques y cada archivo se escribe a un temporal, así que nunca se carga completo
en memoria. El límite (`--max-upload-mb`) se verifica con `Content-Length` antes
de leer y se vuelve a aplicar durante la lectura; ante cualquier error los
temporales se eliminan.

## CRUD (crear, renombrar, borrar)

El CRUD solo se habilita si el servidor arrancó con token (`--token` o
`WEBSHARE_TOKEN`). Sin token, el CRUD responde `403 crud_disabled`. Las subidas
también requieren token; si querés permitir subidas anónimas en una LAN de
confianza, arrancá con `--allow-anonymous-upload` (entonces las subidas no piden
token y el CRUD sigue deshabilitado). En todos los casos la autenticación se
resuelve **antes** de escribir nada en disco.

El token se envía con `Authorization: Bearer <token>`, `X-Webshare-Token`, o el
parámetro `token`. En las subidas `multipart` también se acepta un campo `token`,
pero debe llegar **antes** que los archivos (el archivo no se escribe hasta
autenticar). La interfaz lo guarda en `sessionStorage` (solo esta pestaña) y lo
envía por cabecera; el HTML servido nunca lo contiene.

Reglas:

- Los nombres deben ser un único segmento: se rechazan separadores (`/`, `\`),
  `..`, `:` y caracteres de control (`400 invalid_name`).
- El borrado exige `confirm` igual al nombre exacto del recurso.
- Las carpetas solo se borran si están vacías.
- Nunca se borra la raíz compartida.
- Renombrar no sobrescribe: si el destino existe, responde `409 resource_exists`.
- Se rechazan los enlaces simbólicos en renombrar y borrar (`400 symlink_not_allowed`).
- Un symlink que apunta fuera del sandbox no se descarga, lista ni usa como base
  (`403 path_forbidden`); uno interno sí se resuelve con normalidad.
- Los permisos denegados del sistema de archivos responden con JSON limpio
  (`500 read_failed`, `create_failed`, `delete_failed`) y nunca cortan la conexión.
- Los archivos y carpetas ocultos (`.algo`) se listan y gestionan como cualquier otro.
- Los temporales de subida (`.webshare-upload-*.part`) son internos: no aparecen
  en el listado, su acceso (descargar, info, renombrar, borrar) responde
  `403 path_forbidden` y no se pueden crear con ese nombre (`400 invalid_name`).
- Todo queda dentro del sandbox (`--dir`).

## Ejemplos con `curl`

Sustituí `HOST:PORT` por el servidor de tu LAN (por ejemplo `localhost:8000`) y
`$WEBSHARE_TOKEN` por tu clave.

```sh
# Arrancar con clave; sin ella no se puede subir (403 upload_disabled) ni el CRUD
webshare --port 8000 --token "$WEBSHARE_TOKEN"

# Alternativa "drop": subidas anónimas, sin CRUD
webshare --port 8000 --allow-anonymous-upload

# Estado y validación de la clave
curl -s http://HOST:PORT/api/status
curl -s -H "X-Webshare-Token: $WEBSHARE_TOKEN" http://HOST:PORT/api/auth

# Listar y descargar (no requieren clave)
curl -s "http://HOST:PORT/api/list?path="
curl -sO "http://HOST:PORT/api/download?path=informe.pdf"

# Crear carpeta
curl -s -X POST -H "X-Webshare-Token: $WEBSHARE_TOKEN" \
  "http://HOST:PORT/api/mkdir?path=&name=proyectos"

# Subir un archivo (multipart; la clave va por cabecera)
curl -s -H "X-Webshare-Token: $WEBSHARE_TOKEN" \
  -F current_path=proyectos -F file=@informe.pdf \
  http://HOST:PORT/upload

# Renombrar
curl -s -X POST -H "X-Webshare-Token: $WEBSHARE_TOKEN" \
  "http://HOST:PORT/api/rename?path=proyectos/informe.pdf&new_name=final.pdf"

# Borrar (confirm = nombre exacto; carpeta solo si está vacía)
curl -s -X DELETE -H "X-Webshare-Token: $WEBSHARE_TOKEN" \
  "http://HOST:PORT/api/delete?path=proyectos/final.pdf&confirm=final.pdf"
```

La clave también se acepta con `Authorization: Bearer $WEBSHARE_TOKEN` o el
parámetro `token` (se redacta en los logs, pero queda en el historial del
navegador: menos recomendado).

Si la carpeta de `--dir` no existe, WebShare la crea al arrancar; esto incluye el
valor por defecto (`~/Descargas`).

## Seguridad

- **Sandbox obligatorio**: solo se opera dentro de `--dir`; se rechazan rutas
  absolutas y cualquier reversal path (`..`, `%2e%2e`, barras invertidas, null).
- **Escritura segura por defecto**: las subidas y el CRUD exigen token; sin token
  ambas responden `403` (`upload_disabled` / `crud_disabled`). El modo anónimo
  requiere `--allow-anonymous-upload` explícito.
- Resolución de symlinks con verificación de pertenencia al root; en el CRUD se
  rechazan explícitamente los enlaces simbólicos.
- Validación estricta de nombres (un solo segmento, sin separadores ni control).
- El token de escritura no se embebe en el HTML; vive en `sessionStorage` y se
  transmite por cabecera.
- Headers HTTP: CSP con `script-src 'self'` (scripts externos propios),
  `object-src`/`base-uri`/`frame-ancestors` en `'none'`; `nosniff`, frame deny,
  referrer no. El header `Server` no revela la versión de Python. Assets estáticos
  con `ETag`/`304` y `Cache-Control: no-cache`.
- El token pasado por query se redacta en los logs.
- Límite de tamaño de subida (el cuerpo sobrante se descarta de forma acotada
  antes de responder `413`), incluido el largo del boundary multipart.
- La autenticación del CRUD ocurre **antes** de leer el cuerpo: una petición sin
  token no puede hacer que el servidor bufferice un payload arbitrario.
- Las conexiones ociosas se cierran por timeout (mitiga slowloris) y hay un tope
  de conexiones simultáneas.
- Los temporales de subida huérfanos (por un cierre abrupto) se limpian al
  arrancar.
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

Pendiente definir por el mantenedor. Los recursos de terceros empaquetados en
`static/vendor` (Bootstrap, Bootswatch Simplex, Bootstrap Icons) son MIT; ver
[`static/vendor/LICENSE.txt`](static/vendor/LICENSE.txt).
