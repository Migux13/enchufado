"""CNMC bill simulator.

Derived from pvpc_energy by yinyang17 (https://github.com/yinyang17/pvpc_energy).
Posts consumption data to the CNMC electricity comparator and retrieves bill estimates.
"""
import base64
import datetime
import logging
import re
import time

import aiohttp

_LOGGER = logging.getLogger(__name__)

_UPLOAD_URL = "https://comparador.cnmc.gob.es/api/publico/facturaluz/cargar/curvaConsumo"
_BILL_URL = (
    "https://comparador.cnmc.gob.es/api/publico/ofertas/pvpc"
    "?tipoContador=I"
    "&periodoFacturacion={start_date}%2C{end_date}"
    "&codigoPostal={zip_code}"
    "&bonoSocial=false&tipoConsumidor=1&categoria=1&contador=1"
    "&potenciaPrimeraFranja={power_high}"
    "&potenciaSegundaFranja={power_low}"
    "&consumo1=0&consumo2=0&consumo3=0"
    "&curvaConsumo={energy_file}"
    "&vivienda=false&tarifa=4&calculoAntiguo=false&autoconsumo=false"
    "&perfilConsumo=0"
    "&fechaInicio={start_date}&fechaFin={end_date}"
    "&potenciaAutoconsumo=3.5"
    "&inicioPFacturacion={start_timestamp}&finPFacturacion={end_timestamp}"
)

_MSG_OLD_FILE = "Aviso: El fichero de consumo introducido es demasiado antiguo"
_MSG_NO_DATA = "Aviso: No hay datos para el período de facturación"
_MSG_BAD_FILE = "Aviso: El formato del fichero de consumo no es el correcto"

_HEADERS = {"Content-Type": "application/json"}


async def calculate_bill(billing_period: dict, cups: str, consumptions: dict, zip_code: str):
    """Simulate a bill via CNMC.

    consumptions: {unix_timestamp: kwh_value}  (plain floats, not dicts)
    Returns (billing_period, csv_string | None).
    billing_period is mutated in place with cost fields on success.
    """
    _LOGGER.debug(
        "CNMC.calculate_bill: cups=%s periods=%s→%s power=%.1f/%.1f kW zip=%s len=%d",
        cups,
        billing_period["start_date"],
        billing_period["end_date"],
        billing_period["power_high"],
        billing_period["power_low"],
        zip_code,
        len(consumptions),
    )

    timestamps = sorted(consumptions.keys())
    if len(consumptions) <= 24:
        return billing_period, None

    # Billing period must match consumption range exactly
    first_day = datetime.datetime.fromtimestamp(timestamps[0]).date()
    last_day = datetime.datetime.fromtimestamp(timestamps[-1]).date()
    if billing_period["start_date"] != first_day or billing_period["end_date"] != last_day:
        return billing_period, None

    # Check for gaps > 2 hours
    for i in range(1, len(timestamps)):
        if timestamps[i] - timestamps[i - 1] > 7200:
            _LOGGER.info("CNMC: non-consecutive timestamps at %s", timestamps[i - 1])
            return billing_period, None

    # Build CSV
    total_consumption = 0.0
    csv_lines = ["CUPS;Fecha;Hora;Consumo;Metodo_obtencion\r\n"]
    for ts in timestamps:
        dt = datetime.datetime.fromtimestamp(ts)
        kwh = consumptions[ts]
        total_consumption += kwh
        csv_lines.append(
            f"{cups};{dt.strftime('%d/%m/%Y')};{dt.hour + 1};"
            f"{('%.3f' % kwh).replace('.', ',')};R\r\n"
        )
    csv_data = "".join(csv_lines)
    billing_period["total_consumption"] = total_consumption

    encoded = base64.b64encode(csv_data.encode("utf-8")).decode("utf-8")

    async with aiohttp.ClientSession() as session:
        # Step 1: upload consumption curve
        energy_file = None
        async with session.post(
            _UPLOAD_URL,
            headers=_HEADERS,
            json={"file": f"data:text/csv;base64,{encoded}"},
            ssl=False,
        ) as resp:
            response_text = await resp.text()

        if response_text.startswith(_MSG_OLD_FILE):
            billing_period["total_cost"] = "-"
            return billing_period, csv_data
        elif response_text.startswith(_MSG_NO_DATA):
            _LOGGER.info("CNMC: no data for this period")
            return billing_period, csv_data
        elif response_text.startswith(_MSG_BAD_FILE) or response_text.startswith("Aviso:"):
            _LOGGER.info("CNMC: API warning: %s", response_text[:100])
            return billing_period, csv_data

        match = re.search(r"^(\D+\d+)-.*$", response_text)
        if match:
            energy_file = match.group(1)

        if not energy_file:
            _LOGGER.warning("CNMC: could not parse energy file ID from: %s", response_text[:100])
            return billing_period, csv_data

        # Step 2: request bill calculation
        url = _BILL_URL.format(
            zip_code=zip_code,
            power_high=billing_period["power_high"],
            power_low=billing_period["power_low"],
            energy_file=energy_file,
            start_date=(billing_period["start_date"] - datetime.timedelta(days=1)).isoformat(),
            end_date=billing_period["end_date"].isoformat(),
            start_timestamp=int(time.mktime((billing_period["start_date"] - datetime.timedelta(days=1)).timetuple())) * 1000,
            end_timestamp=int(time.mktime(billing_period["end_date"].timetuple())) * 1000,
        )
        async with session.get(url, ssl=False) as resp:
            bill = await resp.json()

        gasto = bill.get("graficoGastoTotalActual")
        if gasto:
            consumo_diario = bill.get("graficaConsumoDiario", {}).get("consumosDiarios", [])
            if consumo_diario:
                billing_period["start_date"] = datetime.datetime.strptime(
                    consumo_diario[0]["fecha"], "%d/%m/%Y"
                ).date()
                billing_period["end_date"] = datetime.datetime.strptime(
                    consumo_diario[-1]["fecha"], "%d/%m/%Y"
                ).date()
            billing_period["total_cost"] = gasto["importeTotal"]
            billing_period["power_cost"] = gasto["importePotencia"]
            billing_period["energy_cost"] = gasto["importeEnergia"]
            billing_period["rent_cost"] = gasto["importeAlquiler"]
            billing_period["tax_cost"] = gasto["importeIVA"]
            _LOGGER.debug("CNMC: bill result %s", gasto)
        else:
            _LOGGER.debug("CNMC: unexpected bill response: %s", str(bill)[:200])

    return billing_period, csv_data
