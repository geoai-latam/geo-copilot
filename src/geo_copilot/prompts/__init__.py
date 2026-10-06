"""Los prompts del LLM como DATOS: un ``<nombre>.md`` por prompt, junto a este módulo.

F1 del plan de calidad (docs/PLAN_CALIDAD_ORQUESTADOR_2026-10-04.md). Un prompt de 200 líneas escrito
dentro de una función la hacía ilegible (``_llm_design_symbology``: 237 líneas, casi todas texto) y
mezclaba en un mismo diff el cambio de lógica con el de las instrucciones. Aquí el texto se revisa
como texto, y el código queda en lo que hace.

Un prompt con huecos usa la sintaxis de ``str.format`` (``{campo}``; las llaves literales van
dobladas) y quien lo usa lo rellena: ``cargar_prompt("planificador").format(...)``.

Cambiar un prompt cambia el juicio del agente: antes de tocar uno, la batería de verdades y
``pytest -m llm`` (el CI corre un subconjunto en los PR que tocan esta carpeta).
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files


@cache
def cargar_prompt(nombre: str) -> str:
    """El texto del prompt ``nombre`` tal como el código lo tenía escrito.

    El archivo termina en un salto de línea (lo exige el hook de fin de archivo) que no es parte del
    prompt; un checkout de Windows con ``autocrlf`` lo trae con CRLF, y Python leía el prompt del
    ``.py`` con LF."""
    texto = files(__name__).joinpath(f"{nombre}.md").read_text(encoding="utf-8")
    texto = texto.replace("\r\n", "\n")
    return texto[:-1] if texto.endswith("\n") else texto
