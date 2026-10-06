---
name: Reporte de error
about: Algo no funciona como dice la documentación
title: "[BUG] "
labels: bug
assignees: ''
---

<!--
¿Es un fallo de seguridad? No lo escribas acá. Mira SECURITY.md: hay un canal privado.
El sistema ejecuta SQL y Python generados por un LLM, así que esa diferencia importa.
-->

## Qué pasó

<!-- Una o dos frases. Qué hiciste y qué salió. -->

## Qué esperabas que pasara

<!-- Sé concreto: "el mapa debía pintar los lotes de Mosquera" es útil; "debía funcionar" no. -->

## Cómo reproducirlo

1.
2.
3.

**La consulta exacta que escribiste en el chat** (si aplica):

```
```

**El SQL o el código que mostró el panel de aprobación** (si llegó a mostrarse):

```sql
```

## Entorno

| Dato | Valor |
|---|---|
| Versión o commit (`git rev-parse --short HEAD`) | |
| Cómo levantaste el stack | Docker compose / instalación manual |
| Sistema operativo | |
| Python (si es instalación manual) | |
| Navegador (si es del frontend) | |
| `LLM_PROVIDER` | openai / anthropic / azure |
| `LLM_MODEL` | |

Si fue con Docker, el comando exacto:

```bash
docker compose --env-file .env -f docker/docker-compose.yml up --build
```

## Logs

<!--
Pega lo relevante, no todo. Y revísalo antes: los logs guardan prompts, SQL generado
e identificadores de sesión. Nunca pegues claves de API.
-->

```
```

## Contexto extra

<!-- ¿Pasa siempre o a veces? ¿Con datos propios o con el seed de tests? ¿Empezó tras un cambio? -->
