# Enchufado

Integración para Home Assistant que muestra el consumo eléctrico y su coste PVPC usando **Datadis** como fuente de datos, compatible con **e-distribución** y otras distribuidoras españolas.

Los precios PVPC se obtienen de la API pública de [ESIOS/REE](https://api.esios.ree.es/).

## ¿Qué hace?

- Descarga el consumo horario desde **Datadis** (válido para e-distribución y otras)
- Descarga los precios horarios **PVPC** desde ESIOS/REE
- Crea estadísticas en el **panel de energía** de Home Assistant: consumo (kWh) y coste (€)
- Se actualiza automáticamente cada día a las 6:30

## Requisitos

- Cuenta en [datadis.es](https://datadis.es) (gratuita)
- Tu **CUPS** (aparece en la factura de la luz)

## Instalación vía HACS

1. Añade este repositorio como repositorio personalizado en HACS
2. Instala "Enchufado"
3. Reinicia Home Assistant
4. Ve a **Ajustes → Dispositivos y servicios → Añadir integración → Enchufado**

## Configuración

El proceso de configuración tiene dos pasos:

**Paso 1 — Credenciales Datadis:**

| Campo | Descripción |
|-------|-------------|
| Usuario Datadis | Tu NIF/NIE (el mismo que usas en datadis.es) |
| Contraseña Datadis | Tu contraseña de datadis.es |
| NIF autorizado | Opcional, si el CUPS está a nombre de otra persona |

**Paso 2 — Selección de suministro:**

| Campo | Descripción |
|-------|-------------|
| CUPS | Tu punto de suministro (se carga automáticamente desde Datadis) |
| Potencia P1/P2 (kW) | Potencia contratada en horas punta/llano |
| Potencia P3 (kW) | Potencia contratada en horas valle |
| Código postal | Opcional |
| Facturas a mostrar | Número de facturas en el resumen |

## Panel de energía

Una vez configurado, ve a **Ajustes → Panel de energía** y añade las estadísticas:
- `enchufado:consumption` — Consumo en kWh
- `enchufado:cost` — Coste en €

## Servicios disponibles

- `enchufado.import_energy_data` — Importa datos nuevos
- `enchufado.force_import_energy_data` — Fuerza reimportación completa
- `enchufado.reprocess_energy_data` — Regenera estadísticas desde datos locales

## Licencia

MIT
