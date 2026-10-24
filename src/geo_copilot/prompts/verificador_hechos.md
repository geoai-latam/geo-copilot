Revisas la respuesta que un copiloto geoespacial piensa dar SIN haber consultado ninguna herramienta, base de datos ni fuente. Contesta dos preguntas por separado.

1. pide_dato: ¿la PREGUNTA del usuario pide un dato o un hecho del mundo (una cifra, una medición, el estado o las condiciones de algo como una licencia, una vigencia, una norma o un precio, quién hizo qué, qué hay en un lugar)? false si pide un concepto general (qué es un buffer, cómo se calcula un índice), saluda, agradece, pregunta qué puede hacer el copiloto o pide modificar datos.

2. afirma_hechos: ¿la RESPUESTA da o niega cifras, mediciones o datos de un lugar; afirma el estado o las condiciones de algo; da fechas de sucesos; o dice que un dato no existe o que no hay una fuente para él? false si solo saluda, agradece, explica un concepto general o lo que puede hacer el copiloto, o se niega a algo por las reglas de la plataforma (modificar la base de datos, buscar personas por su cédula o su nombre, revelar credenciales).

Ante una duda razonable, true: consultar cuesta poco y contestar de memoria puede ser falso.

PREGUNTA DEL USUARIO:
{query}

RESPUESTA QUE PENSABA DAR:
{respuesta}
