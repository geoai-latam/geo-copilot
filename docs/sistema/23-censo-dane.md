# 23 · Censo 2018 del DANE (CNPV + MGN) — `dane-mcp`

«¿Cuánta gente vive en Chía?», «¿cuántas personas viven en la localidad de Chapinero?», «¿qué manzanas no
tienen alcantarillado?». `dane-mcp` (`services/dane_mcp/`, puerto 9920) une el **Censo Nacional de Población
y Vivienda 2018** al **Marco Geoestadístico Nacional**.

## Herramientas

| Herramienta | Qué hace | Sale |
|---|---|---|
| `dane_variables(nivel, texto?)` | Variables del censo de un nivel (manzana o municipio) con la descripción del DANE; los nombres cambian por nivel (manzana `TP27_PERSO`, municipio `STP27_PERS`) | hechos |
| `dane_municipios(nombre? \| codigo?, variables?, con_limite?)` | Municipios por nombre (sin tildes; varios pueden coincidir) o código DIVIPOLA, con sus totales OFICIALES y, si se pide, su límite MGN 2025 | hechos (+ capa) |
| `dane_censo_manzanas(variables, area? \| bbox?, max_manzanas?)` | Manzanas del área con las variables del censo 2018 y lo que añade el MGN 2025 (recuento de viviendas, unidades económicas), con las sumas | capa |

Las cifras son de 2018 (población CENSADA), no proyecciones; lo del MGN 2025 se da aparte y con su fuente.

## Cómo se arma (medido el 2026-10-15)

- El REST del censo integrado (`MARCO_INTEGRADO/Serv_DatosCNPV2018_Integrados_MGN2018`) da los atributos por
  nivel pero **suprime la geometría**. El WFS del **MGN 2025** da las manzanas con geometría y el mismo
  código de 22 dígitos: se cruzan por código. Chapinero (rectángulo): 760 de 763 manzanas 2025 con dato 2018.
  El FeatureServer del MGN falla al consultar manzanas; el WFS no.
- Los nombres y algunas claves llegan con la codificación rota (UTF-8 leído como Latin-1): se reparan.
- La población rural DISPERSA no vive en manzanas: la suma de manzanas de Bogotá es 7 136 912; el total del
  municipio, 7 181 469. Por eso «¿cuánta gente vive en X?» va por `dane_municipios`.

## Verdades

- Bogotá 7 181 469 (cifra oficial del CNPV 2018); Chía 129 613.
- Localidad de Chapinero: el agente (V5) suma 150 642 en 1 162 manzanas con dato; el propio servidor del
  DANE, con su geometría de 2018 y el mismo límite, 150 699 en 1 163 (0,04 %).
- `tests/test_dane_mcp_vivo.py` (`pytest -m integration`); V5 `e2e-integration/dane-mcp.spec.ts` 3/3.

## Probar

- Unidad: `pytest tests/test_dane_mcp.py`.
- En vivo: `pytest -m integration tests/test_dane_mcp_vivo.py`.
