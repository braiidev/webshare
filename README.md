# WebShare

Servidor HTTP local para compartir, subir y descargar archivos dentro de una red
LAN. Está escrito únicamente con la librería estándar de Python.

## Características

- Servidor en Python 3.10+ sin dependencias externas.
- Navegación de carpetas.
- Subidas con nombres únicos para evitar sobrescrituras.
- Descargas en streaming.
- `/health`.
- Cierre limpio con `Ctrl+C`.
- Logs de solicitudes, incluyendo User-Agent.
- Sin venv: instalación liviana.

## Uso

```sh
webshare
webshare --port 8080
webshare --dir /carpeta/a/compartir --bind 0.0.0.0
webshare --token "mi-token-secreto"
WEBSHARE_TOKEN=... webshare
```

Valores por defecto:

- directorio: `~/Descargas`
- bind: `0.0.0.0`
- puerto: `8000`

## Actualización y desinstalación

```sh
webshare --update
webshare --uninstall
webshare --uninstall --yes
```

## Seguridad

- No permite path traversal fuera de la carpeta compartida.
- Valida nombres de archivo.
- Evita sobrescribir archivos existentes.
- No expone servidores de modelos ni APIs externas.
- Se recomienda usarlo solo en redes confiables o con control de firewall.
- El servidor de archivos no está pensado como servicio público en Internet.

## Instalación desde fuente

```sh
curl -fsSL https://raw.githubusercontent.com/braiidev/webshare/main/install.sh | sh
```

## Desarrollo

```sh
python3 tests/test_webshare.py
python3 webshare.py --port 8123
```

## Licencia

Pendiente definir por el mantenedor.


## Opciones

| Opción | Default | Descripción |
| --- | --- | --- |
| `--dir` | `~/Descargas` | Carpeta compartida. |
| `--bind` | `0.0.0.0` | Dirección de escucha. |
| `--port` | `8000` | Puerto HTTP. |
| `--read-only` | apagado | Desactiva uploads, descargas sí permitidas. |
| `--max-upload-mb` | `5120` | Límite de subida. |
| `--token` / `WEBSHARE_TOKEN` | vacío | Token requerido para uploads. |
| `--quiet` | apagado | Desactiva logs HTTP. |
| `--update` | - | Actualiza repo y symlink. |
| `--uninstall` | - | Desinstala symlink/repo. |
| `--version` | - | Muestra versión. |

## Endpoints

- `GET /api/status`
- `GET /api/list?path=...`
- `GET /api/info?path=...`
- `GET /api/download?path=...`

- `GET /` interfaz web
- `GET /health` JSON con uptime, versión, directorio y read-only
- `GET /version` JSON con versión
- `GET /download?path=...` descarga streaming
- `POST /upload` multipart

## Seguridad

- Path traversal protegido.
- Validación de nombres.
- Headers: CSP, nosniff, frame deny, referrer no, cache no-store.
- Límites de tamaño de upload.
- Token opcional.
- Modo solo lectura.

## Desarrollo y pruebas
```sh
python3 -m unittest tests.test_webshare -v
```

## Seguridad de Sandbox

Todas las operaciones de lectura, escritura, borrado y listado se restringen a la
carpeta compartida (`--dir`). El servidor aplica:

- rechazo de paths absolutos;
- rechazo de secuencias de reversión (`..`, `%2e%2e`, `%2e%2e%2f`, etc.);
- normalización de barras invertidas y navegación relativa;
- resolución de symlinks con verificación de pertenencia al root;
- protección de carpetas fuera del sandbox;
- evitación de path traversal por query, parámetro `path` o campos multipart.

### Modo solo lectura
```sh
webshare --read-only
```

### Token de escritura
```sh
WEBSHARE_TOKEN=secret webshare
```

### Operaciones destructivas
- borrado de archivos solo exact name match;
- no permite borrar root;
- directorios solo vacíos.

### Logs
- logs HTTP pueden desactivarse con `--quiet`;
- por defecto no se usa request-log explícito para nombres sensibles.
