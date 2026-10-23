"""Texto de una página HTML pública (Terranova F5, G4 parte 2): normas y sentencias.

Las normas del Gestor Normativo de Función Pública, la CREG o el Normograma DIAN y el texto de las
sentencias de la Corte Constitucional son HTML estático. De una página sale:

- su título y su texto limpio (sin scripts, estilos, menús ni pies), con cuántos caracteres tiene;
- `fragmentos` alrededor de una búsqueda (p. ej. «ARTÍCULO 5»): una norma de 2 MB no viaja entera;
- sus `enlaces` que cumplan un patrón (un índice de resoluciones, p. ej.).

Solo con la biblioteca estándar (`html.parser`): nada que ejecutar, nada que instalar.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

# nunca es contenido: se descarta con todo lo que tenga dentro
_FUERA = {"script", "style", "noscript", "svg", "iframe", "template"}
# menús y pies: fuera, salvo que se coman la página. Un sitio que no cierra su <header> (o que lo
# envuelve todo en él) dejaría el texto vacío; la CREG perdía así sus 125 resoluciones.
# `form` no está: muchos sitios del Estado (ASP.NET) envuelven toda la página en un formulario.
_MENUS = {"nav", "header", "footer"}
_MINIMO = 0.5  # sin menús debe quedar al menos la mitad del texto
# lo que separa bloques de texto: se vuelve salto de línea
_BLOQUE = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table",
           "blockquote", "pre", "dd", "dt"}
MAX_FRAGMENTOS = 20
CONTEXTO = 700


class _Lector(HTMLParser):
    def __init__(self, base: str, excluidos: set[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self._excluidos = excluidos
        self.partes: list[str] = []
        self.titulo = ""
        self.enlaces: list[tuple[str, str]] = []
        self._fuera = 0
        self._en_titulo = False
        self._enlace: str | None = None
        self._texto_enlace: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._excluidos:
            self._fuera += 1
        elif tag == "title":
            self._en_titulo = True
        elif tag == "a" and not self._fuera:
            href = dict(attrs).get("href")
            self._enlace = urljoin(self.base, href) if href else None
            self._texto_enlace = []
        if tag in _BLOQUE:
            self.partes.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._excluidos and self._fuera:
            self._fuera -= 1
        elif tag == "title":
            self._en_titulo = False
        elif tag == "a" and self._enlace:
            self.enlaces.append((" ".join("".join(self._texto_enlace).split()), self._enlace))
            self._enlace = None
        if tag in _BLOQUE:
            self.partes.append("\n")

    def handle_data(self, data: str) -> None:
        if self._en_titulo:
            self.titulo += data
        if self._fuera:
            return
        self.partes.append(data)
        if self._enlace:
            self._texto_enlace.append(data)


def _leer_con(html: str, base: str, excluidos: set[str]) -> tuple[str, str, list[tuple[str, str]]]:
    lector = _Lector(base, excluidos)
    lector.feed(html)
    lector.close()
    # sin la marca de orden de bytes (U+FEFF) que algunos sitios dejan al comienzo del texto
    crudo = "".join(lector.partes).replace("\ufeff", "")
    lineas = (" ".join(linea.split()) for linea in crudo.splitlines())
    texto = "\n".join(linea for linea in lineas if linea)
    return " ".join(lector.titulo.split()), texto, lector.enlaces


def leer(html: str, base: str) -> tuple[str, str, list[tuple[str, str]]]:
    """(título, texto limpio, enlaces) de un HTML: sin menús si quitarlos no se come la página."""
    completo = _leer_con(html, base, _FUERA)
    sin_menus = _leer_con(html, base, _FUERA | _MENUS)
    return sin_menus if len(sin_menus[1]) >= _MINIMO * len(completo[1]) else completo


_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([A-Za-z0-9_-]+)""", re.IGNORECASE)


def decodificar(contenido: bytes, tipo: str) -> str:
    """El HTML como texto: el charset de la cabecera, si no el del <meta>, si no UTF-8.

    La relatoría de la Corte Constitucional declara windows-1252 solo en el <meta>: leída como UTF-8,
    «identificación» salía «identificaci�n»."""
    cabecera = re.search(r"charset=([A-Za-z0-9_-]+)", tipo or "", re.IGNORECASE)
    meta = _CHARSET.search(contenido[:4096])
    for nombre in (cabecera.group(1) if cabecera else None, meta.group(1).decode() if meta else None, "utf-8"):
        if nombre:
            try:
                return contenido.decode(nombre)
            except (LookupError, UnicodeDecodeError):
                continue
    return contenido.decode("utf-8", errors="replace")


def fragmentos(texto: str, buscar: str, contexto: int = CONTEXTO) -> tuple[list[dict], int]:
    """Los pasajes alrededor de cada aparición de `buscar` (sin distinguir mayúsculas), y cuántas hay."""
    patron = re.compile(re.escape(buscar.strip()), re.IGNORECASE)
    apariciones = list(patron.finditer(texto))
    salida = []
    for n, m in enumerate(apariciones[:MAX_FRAGMENTOS], start=1):
        ini = max(0, m.start() - contexto // 4)
        salida.append({"n": n, "posicion": m.start(), "texto": texto[ini:m.start() + contexto].strip()})
    return salida, len(apariciones)


def enlaces_con(enlaces: list[tuple[str, str]], patron: str) -> list[dict]:
    """Los enlaces cuya URL cumple `patron` (expresión regular), sin repetir."""
    regla = re.compile(patron)
    vistos: set[str] = set()
    salida = []
    for texto, url in enlaces:
        if regla.search(url) and url not in vistos:
            vistos.add(url)
            salida.append({"texto": texto, "url": url})
    return salida
