"""Estado compartido entre procesos (F7, S7.1): lo que no puede vivir en la memoria de UN worker.

Con varios workers o réplicas detrás de un balanceador, la petición que aprueba un HITL, la que
lanza una consulta y el WebSocket del usuario pueden caer en procesos distintos; y un reinicio
borra la memoria. Aquí viven el **bus** (avisos entre procesos) y el **almacén de aprobaciones**
(solicitudes HITL que sobreviven a un reinicio). En producción son de Redis y obligatorios; en
desarrollo y tests, en memoria del proceso.
"""
