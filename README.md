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
