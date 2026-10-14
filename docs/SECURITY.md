# Security Model

## Sandbox
- Root is resolved from `--dir`.
- All relative paths are canonicalized and checked against root.
- Absolute or traversal strings return `403 {"error":"path_forbidden"}`.
- Symlinks escaping the sandbox are rejected.
- Default route is read-only unless an write or upload is permitted.

## Tokens
- `--token` or `WEBSHARE_TOKEN`
- Required for uploads if configured.
- Token validation checks multipart field or `Authorization: Bearer`.
- Invalid token no longer saved.

## Path reversal
- `..`, `%2e%2e`, backslashes and null are rejected.
- Source code validates all entry types.
