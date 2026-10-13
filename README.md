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

## Actualizar desde una versión < 0.3.0

Desde la 0.3.0 el token de ESIOS-REE ya no va incluido en la integración: cada usuario aporta el suyo. Las entradas creadas antes no lo tienen, así que los precios PVPC dejan de actualizarse y la estadística `enchufado:cost` vale 0 € desde esa fecha.

No hace falta borrar la integración:

1. Pide tu token gratuito en [api.esios.ree.es](https://api.esios.ree.es/) (se solicita por email).
2. Verás un aviso en **Ajustes → Reparaciones**. Ábrelo e introduce el token, o ve a **Ajustes → Dispositivos y servicios → Enchufado → ⋮ → Reconfigurar**.
3. El token se comprueba contra ESIOS-REE, la integración se recarga y, al llegar los precios atrasados, la estadística de coste se regenera sola desde la primera hora afectada.

## Entidades creadas

| Entidad | Tipo | Descripción |
|---------|------|-------------|
| `number.facturas_a_mostrar` | Slider (1–24) | Número de facturas que se incluyen en `enchufado.current_bill` |

## Estadísticas del panel de energía

Ve a **Ajustes → Panel de energía** y añade:

- `enchufado:consumption` — Consumo en kWh
- `enchufado:cost` — Coste de la **energía** en € (consumo × precio PVPC)
- `enchufado:total_cost` — **Coste eléctrico total** en €: energía + potencia + financiación del bono social + alquiler del contador + impuesto eléctrico + IVA

### Coste total en el panel de Energía

`enchufado:cost` solo incluye la energía. Para ver en el panel lo que realmente pagas, ve a **Ajustes → Paneles → Energía → Red eléctrica → tu consumo de red → Usar una estadística** y elige `enchufado:total_cost` en lugar de `enchufado:cost`.

Cómo se calcula cada hora:

- **Energía**: consumo × precio PVPC de esa hora.
- **Potencia**: `potencia P1 × (peaje y cargo P1 + margen de comercialización) + potencia P2 × (peaje y cargo P2)`, en €/kW·año ÷ 365, repartido entre las horas del día (23/24/25 en los cambios de hora).
- **Bono social** y **alquiler del contador**: €/día repartidos entre las horas del día.
- **Impuesto eléctrico** = tipo × (energía + potencia + bono social). **IVA** = tipo × (esa base + impuesto eléctrico + contador).

Los valores regulados (peajes, cargos, bono social, contador, tipos de impuestos) están en `custom_components/enchufado/tariffs.py` en tablas con fecha de inicio de vigencia, con la fuente de cada valor en los comentarios. Cada hora usa el valor vigente ese día; actualizar un año es añadir filas. Cuando cambia la tabla o la potencia contratada, la estadística se regenera entera. Solo hay datos desde el 1 de enero de 2026. El IVA y el impuesto eléctrico se devengan en la fecha de la factura, no por día de consumo (art. 75.Uno.7 de la Ley 37/1992), así que cada hora usa los tipos vigentes el último día de su ciclo de facturación. Indica el **día de inicio del ciclo** (1-28, por defecto 1 = mes natural) al configurar la integración o después en Configurar; Datadis no publica este dato. El ciclo real sigue la fecha de lectura del contador y puede variar ±3 días de un mes a otro, por lo que el día configurado es una aproximación. El mínimo legal del impuesto eléctrico no se aplica hora a hora.

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
