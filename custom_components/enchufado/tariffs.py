"""Regulated values and pure cost maths for the total-cost statistic.

No Home Assistant imports here, so it can be unit-tested on its own.

Every table is a list of ``(valid_from, value)`` rows sorted by date; a row applies from
its date until the next row. To update for a new year or a regulatory change, append rows
(never edit old ones, so past hours keep being computed with the value that applied).
Changing any row changes ``signature()``, which makes the integration regenerate the
whole ``enchufado:total_cost`` statistic.

Hours before ``TABLES_START`` have no known values and are not part of the statistic.
"""
import datetime
import hashlib
import json
from zoneinfo import ZoneInfo

MADRID_TZ = ZoneInfo("Europe/Madrid")
D = datetime.date

DAYS_PER_YEAR = 365  # the CNMC comparator and invoices prorate with 365, also in leap years

TABLES_START = D(2026, 1, 1)

# Peaje de transporte y distribución + cargos, tariff 2.0TD, EUR/kW·year.
# Source: values printed by the CNMC comparator (13/10/2026) and on 2026 invoices.
# Legal basis (not re-checked line by line): CNMC peajes 2026 resolution and Orden TED/1524/2025
# (BOE-A-2025-26705) for the cargos.
POWER_P1 = [(D(2026, 1, 1), 27.704413)]  # punta
POWER_P2 = [(D(2026, 1, 1), 0.725423)]  # valle

# Margen de comercialización fijo of the PVPC, EUR/kW·year, applied to power_high only.
# Source: CNMC comparator output and invoices (3,113). Not located in the BOE.
COMMERCIALISATION_MARGIN = [(D(2026, 1, 1), 3.113)]

# Financiación del bono social, EUR per day and supply point.
# - 0,019121 = 6,979247 EUR/CUPS/year / 365: Orden TED/1524/2025 (BOE-A-2025-26705).
# - 0,024688 = 9,011295 EUR/CUPS/year / 365: Orden TED/634/2026, de 17 de junio
#   (BOE-A-2026-13759). The Order says it has effect from 26/06/2026 ("liquidación 7 de
#   2026"), but the observed invoice (27 days at the old value + 5 at the new one for
#   03/06→05/07) applies it from 30/06/2026, which is what is used here.
BONO_SOCIAL = [(D(2026, 1, 1), 0.019121), (D(2026, 6, 30), 0.024688)]

# Alquiler de equipos de medida (contador), EUR per day. 0,81 EUR/month x 12 / 365 for
# single-phase meters (Orden ITC/3860/2007; quoted from memory, matches 2026 invoices).
METER_RENTAL = [(D(2026, 1, 1), 0.026630)]

# Impuesto especial sobre la electricidad (Ley 38/1992, art. 99: 5,11269632 %).
# Real Decreto-ley 7/2026 (BOE-A-2026-6544), art. 40: 0,5 % from its entry into force
# (22/03/2026) until 30/06/2026, with June subject to an IPC test. It was not extended into
# June in practice: the invoice that starts on 06/05/2026 already carries 5,11269632 %, and
# secondary sources give 31/05/2026 as the last day. RDL 18/2026 (BOE-A-2026-14112) allowed
# 0,5 % again in Aug/Sep only if the electricity IPC rose >15 %; invoices up to 05/10/2026
# show it did not. RDL 25/2026 (BOE-A-2026-20265) announces a possible new cut for Q4 tied
# to the September IPC: NOT included, unconfirmed.
IEE_RATE = [
    (D(2026, 1, 1), 0.0511269632),
    (D(2026, 3, 22), 0.005),
    (D(2026, 6, 1), 0.0511269632),
]
# Legal minimum quota of the IEE: 1 EUR/MWh for non-professional use (0,5 for professional).
IEE_MIN_EUR_PER_KWH = 0.001

# IVA on electricity. 10 % (RDL 7/2026 art. 42) only for contracts with contracted power
# <= 10 kW, same validity as the IEE cut above.
VAT_STANDARD = 0.21
VAT_REDUCED_MAX_KW = 10.0
VAT_RATE = [
    (D(2026, 1, 1), VAT_STANDARD),
    (D(2026, 3, 22), 0.10),
    (D(2026, 6, 1), VAT_STANDARD),
]

_TABLES = {
    "power_p1": POWER_P1,
    "power_p2": POWER_P2,
    "margin": COMMERCIALISATION_MARGIN,
    "bono_social": BONO_SOCIAL,
    "meter": METER_RENTAL,
    "iee": IEE_RATE,
    "iee_min": IEE_MIN_EUR_PER_KWH,
    "vat": VAT_RATE,
    "vat_std": VAT_STANDARD,
    "vat_max_kw": VAT_REDUCED_MAX_KW,
    "days_per_year": DAYS_PER_YEAR,
    "start": TABLES_START.isoformat(),
}


def signature(power_high, power_low):
    """Hash of everything the statistic depends on besides consumption and prices."""
    payload = json.dumps([_TABLES, power_high, power_low], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def rate_on(table, day):
    """Value in force on ``day``; None before the first row."""
    value = None
    for valid_from, row_value in table:
        if valid_from > day:
            break
        value = row_value
    return value


def vat_on(day, power_high, power_low):
    rate = rate_on(VAT_RATE, day)
    if rate < VAT_STANDARD and max(power_high, power_low) > VAT_REDUCED_MAX_KW:
        return VAT_STANDARD
    return rate


def daily_power_eur(day, power_high, power_low):
    yearly = power_high * (rate_on(POWER_P1, day) + rate_on(COMMERCIALISATION_MARGIN, day))
    yearly += power_low * rate_on(POWER_P2, day)
    return yearly / DAYS_PER_YEAR


def hour_cost(day, hours_in_day, energy_eur, power_high, power_low):
    """Cost breakdown (EUR) of one hour: energy + prorated fixed terms + taxes."""
    power = daily_power_eur(day, power_high, power_low) / hours_in_day
    bono = rate_on(BONO_SOCIAL, day) / hours_in_day
    meter = rate_on(METER_RENTAL, day) / hours_in_day
    iee_base = energy_eur + power + bono
    iee = iee_base * rate_on(IEE_RATE, day)
    vat = (iee_base + iee + meter) * vat_on(day, power_high, power_low)
    return {
        "energy": energy_eur,
        "power": power,
        "bono": bono,
        "meter": meter,
        "iee": iee,
        "vat": vat,
        "total": iee_base + iee + meter + vat,
    }


def hours_in_day(day):
    """23, 24 or 25 depending on the DST change."""
    start = datetime.datetime.combine(day, datetime.time(), MADRID_TZ)
    end = datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time(), MADRID_TZ)
    return round((end.timestamp() - start.timestamp()) / 3600)


def create_total_cost_series(first_ts, last_ts, consumptions, prices, power_high, power_low,
                             day_cost=0.0):
    """Hourly (timestamp, hour_cost, day_to_date_cost) rows for first_ts..last_ts.

    ``consumptions`` maps timestamp -> {"value": kWh}; missing hours count as 0 kWh (the
    fixed terms are still charged), missing prices as 0. Hours before TABLES_START are
    skipped. ``day_cost`` seeds the day-to-date state when resuming in the middle of a day.
    """
    rows = []
    timestamp = first_ts
    cache_day = None
    n_hours = 24
    while timestamp <= last_ts:
        local = datetime.datetime.fromtimestamp(timestamp, MADRID_TZ)
        day = local.date()
        if day >= TABLES_START:
            if day != cache_day:
                cache_day = day
                n_hours = hours_in_day(day)
            c = consumptions.get(timestamp)
            energy = (c["value"] if c else 0.0) * prices.get(timestamp, 0.0)
            cost = hour_cost(day, n_hours, energy, power_high, power_low)["total"]
            day_cost = cost if local.hour == 0 else day_cost + cost
            rows.append((timestamp, cost, day_cost))
        timestamp += 3600
    return rows


def _round(value):
    return float(f"{value:.2f}")


def invoice_breakdown(start, end, energy_eur, power_high, power_low, kwh=None):
    """Rebuild an invoice from its energy amount (what a supplier prints).

    ``start`` is the first day and ``end`` the invoice's end date, which does NOT count
    as a billed day (``days = end - start``). Taxes use the rates in force on the last
    billed day, as suppliers apply the rate at invoicing time. Each line is rounded to
    cents like on an invoice. ``kwh`` enables the IEE legal minimum.
    """
    days = [start + datetime.timedelta(days=i) for i in range((end - start).days)]
    last = days[-1]
    power = _round(sum(daily_power_eur(d, power_high, power_low) for d in days))
    bono = _round(sum(rate_on(BONO_SOCIAL, d) for d in days))
    meter = _round(sum(rate_on(METER_RENTAL, d) for d in days))
    energy = _round(energy_eur)
    base = energy + power + bono
    iee = base * rate_on(IEE_RATE, last)
    if kwh is not None:
        iee = max(iee, kwh * IEE_MIN_EUR_PER_KWH)
    iee = _round(iee)
    vat_base = base + iee + meter
    vat = _round(vat_base * vat_on(last, power_high, power_low))
    return {
        "days": len(days),
        "power": power,
        "energy": energy,
        "bono": bono,
        "iee": iee,
        "meter": meter,
        "vat": vat,
        "total": _round(energy + power + bono + iee + meter + vat),
    }
