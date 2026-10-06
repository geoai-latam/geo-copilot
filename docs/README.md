# Documentación de GEO_COPILOT

Aquí vive la documentación del sistema. Los registros internos de trabajo (validaciones,
auditorías, planes y backlog) no se publican.

| Qué | Dónde | Para qué |
|---|---|---|
| **Cómo funciona el sistema** | [`sistema/`](sistema/) | 12 documentos verificados **línea por línea contra el código**, defaults incluidos. **Es el canon.** Empieza por [`sistema/README.md`](sistema/README.md) |

Más [`SPEC_C3_IMAGERY_MCP_2026-07-19.md`](SPEC_C3_IMAGERY_MCP_2026-07-19.md),
que se queda en la raíz porque cuatro archivos de código lo citan como la forma
del contrato del servicio `imagery-mcp`. Es un **registro de diseño**: para
operar el servicio, `sistema/`.

## La regla

**Si `docs/sistema/` y cualquier otro documento se contradicen, manda
`docs/sistema/`.** Vale también para los `README` y para los docstrings del
código: la auditoría del 8-sep encontró varios docstrings afirmando lo contrario
de lo que hace su propio archivo.

Los números de tests y de cobertura tienen una sola fuente:
[`sistema/11-como-probar-todo.md` §10](sistema/11-como-probar-todo.md). Llegaron
a convivir diez cifras distintas en este repositorio; si encuentras otra, está
vieja.

## Si vas a tocar despliegue, seguridad o el sandbox

Lee [`sistema/09-configuracion-y-deploy.md`](sistema/09-configuracion-y-deploy.md)
y **nada más**. Hay documentos archivados que describen configuraciones ya
declaradas insuficientes: seguir una al pie de la letra reabre un hallazgo
cerrado.
