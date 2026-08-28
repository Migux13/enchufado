# Enchufado

Integración para Home Assistant que muestra el consumo eléctrico y su coste PVPC usando **Datadis** como fuente de datos, compatible con **e-distribución** y otras distribuidoras españolas.

Los precios PVPC se obtienen de la API pública de [ESIOS/REE](https://api.esios.ree.es/).

## Créditos

La lógica de estadísticas y persistencia de datos está derivada de [pvpc_energy](https://github.com/yinyang17/pvpc_energy) de [@yinyang17](https://github.com/yinyang17), usada bajo licencia MIT.

### Relación con pvpc_energy

Enchufado comparte con pvpc_energy la arquitectura, los IDs de estadísticas y la persistencia en CSV, pero obtiene el consumo desde **Datadis** en lugar de UFD. Datadis cubre las distribuidoras eléctricas de toda España, mientras que pvpc_energy está limitado a UFD — esa es la diferencia práctica entre ambas integraciones a la hora de elegir una u otra.

## ¿Qué hace?

- Descarga el consumo horario desde **Datadis** (compatible con e-distribución y otras distribuidoras)
- Descarga los precios horarios **PVPC** desde ESIOS/REE
- Crea estadísticas en el **panel de energía** de Home Assistant: consumo (kWh) y coste (€)
- Simula facturas mensuales usando el comparador oficial de la CNMC y publica el resultado como estado `enchufado.current_bill`
- Se actualiza automáticamente cada día a las 6:30

## Requisitos

- Cuenta en [datadis.es](https://datadis.es) (gratuita)
- Token personal de la API de [ESIOS/REE](https://api.esios.ree.es/) (gratuito, se solicita por email)
- Home Assistant 2025.11 o superior

## Instalación vía HACS

1. Añade este repositorio como repositorio personalizado en HACS (categoría: Integración)
2. Instala **Enchufado**
3. Reinicia Home Assistant
4. Ve a **Ajustes → Dispositivos y servicios → Añadir integración → Enchufado**

## Configuración

El proceso de configuración tiene dos pasos:

**Paso 1 — Credenciales Datadis:**

| Campo | Descripción |
|-------|-------------|
| Usuario | Tu NIF/NIE (el mismo que usas en datadis.es) |
| Contraseña | Tu contraseña de datadis.es |
| NIF autorizado | Opcional, si el CUPS está a nombre de otra persona |
| Token API ESIOS-REE | Tu token personal de la API de ESIOS/REE, necesario para el precio PVPC |

**Paso 2 — Selección de suministro:**

Se muestran los suministros encontrados en tu cuenta. Al seleccionar uno, la potencia contratada y el código postal se obtienen automáticamente desde Datadis.

## Entidades creadas

| Entidad | Tipo | Descripción |
|---------|------|-------------|
| `number.facturas_a_mostrar` | Slider (1–24) | Número de facturas que se incluyen en `enchufado.current_bill` |

## Estadísticas del panel de energía

Ve a **Ajustes → Panel de energía** y añade:

- `enchufado:consumption` — Consumo en kWh
- `enchufado:cost` — Coste en €

## Simulación de facturas

La entidad `enchufado.current_bill` contiene el importe de la última factura simulada y un atributo `bills` con el desglose de las N facturas más recientes (N se controla con el slider `Facturas a mostrar`).

Cada factura incluye: importe total, coste de potencia, coste de energía, alquiler de contador e IVA.

## Servicios disponibles

| Servicio | Descripción |
|----------|-------------|
| `enchufado.import_energy_data` | Importa datos nuevos desde Datadis/ESIOS |
| `enchufado.force_import_energy_data` | Fuerza reimportación completa ignorando caché |
| `enchufado.reprocess_energy_data` | Regenera estadísticas desde datos locales |

## Licencia

MIT
