"""El ALCANCE del mensaje del usuario: si dejó puesto el chip de su selección o lo quitó.

Es un hecho de la pantalla (qué vio el usuario sobre su cuadro de texto al enviar): qué hacer con él lo
decide el modelo. Salió de `formatters.format_map_context`.
"""

from __future__ import annotations

from typing import Any


def alcance_del_mensaje(map_context: dict[str, Any], layers: list[dict[str, Any]]) -> list[str]:
    """Las líneas de alcance para el bloque del mapa (ninguna si no hay chip)."""
    lineas: list[str] = []
    if map_context.get("alcance_seleccion"):
        con_sel = next((lyr for lyr in layers if (lyr.get("seleccion") or {}).get("ids")
                        or (lyr.get("seleccion") or {}).get("where")), None)
        if con_sel is not None:
            n = (con_sel.get("seleccion") or {}).get("count")
            lineas.append(
                f"ALCANCE QUE EL USUARIO DEJÓ PUESTO AL ENVIAR: sobre su cuadro de texto estaba el chip "
                f"«{n} seleccionados de {con_sel.get('name', '')}» ([seleccion]); podía quitarlo y no lo hizo. "
                f"En la pantalla ese chip se presenta como el alcance del mensaje: lo que pida sin nombrar otra "
                f"cosa es sobre esos {n} (`seleccion`), no sobre la capa entera.")

    excl = map_context.get("seleccion_excluida") or {}
    if excl.get("layer_id"):
        # V5 FH.4 (gpt-5.4, 6/8): «¿y cuánto suman ahora?» repetía la cifra de los 3 seleccionados
        # como si fuera de la capa entera. El hecho: de cuántos era aquella cifra y cuántos son ahora.
        total = next((lyr.get("feature_count") for lyr in layers if lyr.get("id") == excl.get("layer_id")), None)
        entera = f"ENTERA ({total} elementos)" if total else "ENTERA"
        lineas.append(
            f"ALCANCE: el usuario QUITÓ para este mensaje el chip «{excl.get('count')} seleccionados de {excl.get('layer_name')}»; "
            f"la pantalla le mostró «sin selección: {excl.get('layer_name')} entera» ([{excl.get('layer_id')}]). Lo que pida "
            f"sin nombrar otra cosa es sobre esa capa {entera}, aunque la capa activa sea otra. Una cifra dicha antes "
            f"sobre los {excl.get('count')} seleccionados es de esos {excl.get('count')}, no de la capa entera.")
    return lineas
