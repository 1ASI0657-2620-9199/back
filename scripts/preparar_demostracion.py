"""Prepare isolated demo data through the running APIs, without deleting records.

Run from back/: python scripts/preparar_demostracion.py [--completa]
Each run uses a new demo identity. No third-party packages are required.
"""

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]


def main(base_url, complete):
    now = datetime.now(timezone(timedelta(hours=-5)))
    owner = ("respaldo-" if complete else "presentacion-") + now.strftime("%d%m-%H%M") + "-" + uuid4().hex[:4]
    month = now.strftime("%Y-%m")
    previous_month = (now.replace(day=1).date().toordinal() - 1)
    previous = date.fromordinal(previous_month).strftime("%Y-%m")
    observed = now.astimezone(timezone.utc).isoformat()

    def request(path, payload=None, key=None):
        headers = {"X-User-Id": owner}
        if key:
            headers["Idempotency-Key"] = key
        if payload is not None:
            headers["Content-Type"] = "application/json"
        req = Request(base_url + path, data=json.dumps(payload).encode() if payload is not None else None,
                      headers=headers, method="POST" if payload is not None else "GET")
        try:
            with urlopen(req, timeout=20) as response:
                return json.load(response), response.status
        except HTTPError as error:
            raise RuntimeError(f"HTTP {error.code} en {path}: {error.read().decode()}") from None

    for service in ("financial", "budgets", "notifications", "devices"):
        health, status = request("/services/" + service + "/health")
        assert status == 200 and health["status"] == "ok", service

    def transaction(key, kind, amount, category, timestamp, description):
        body = {"account_id": "cash-account", "type": kind, "amount": amount, "currency": "PEN",
                "category": category, "occurred_at": timestamp, "description": description}
        _, status = request("/api/v1/transactions", body, key)
        assert status == 201

    # Historical data makes the recommendation panel useful during the demo.
    transaction("demo-historico-ingreso", "income", "2000", "salary", previous + "-10T15:00:00Z", "Ingreso del mes anterior")
    for category, amount in (("food", "400"), ("transport", "100"), ("services", "150")):
        transaction("demo-historico-" + category, "expense", amount, category,
                    previous + "-11T15:00:00Z", "Historial para recomendacion")
    transaction("demo-ingreso", "income", "1000", "salary", observed, "Ingreso para la presentacion")

    def eventually(callback, label):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            value = callback()
            if value:
                return value
            time.sleep(0.5)
        raise RuntimeError("No se confirmo: " + label)

    device_id = None
    if complete:
        budget, status = request("/api/v1/budgets", {"category": "food", "month": month,
                                "currency": "PEN", "limit_amount": "100", "alert_threshold": 80})
        assert status == 201
        transaction("demo-compra", "expense", "85", "food", observed, "Compra de la semana")
        transaction("demo-compra-extra", "expense", "20", "food", observed, "Compra adicional")
        device, status = request("/api/v1/devices", {"name": "Medidor de casa", "source": "simulator",
                                 "monthly_limit_kwh": "20", "tariff_per_kwh": "0.75"})
        assert status == 201
        device_id = device["id"]
        reading = {"reading_id": "demo-lectura-1", "value": "15000", "unit": "Wh", "occurred_at": observed}
        path = "/api/v1/devices/" + device_id + "/simulate"
        _, status = request(path, reading)
        assert status == 201
        _, replay_status = request(path, reading)
        assert replay_status == 200
        _, status = request(path, {**reading, "reading_id": "demo-lectura-2", "value": "10000"})
        assert status == 201
        def budget_ready():
            items, _ = request("/api/v1/budgets?month=" + month)
            return items and Decimal(items[0]["spent"]) == Decimal("105")
        eventually(budget_ready, "presupuesto con S/ 105")
        def alerts_ready():
            items, _ = request("/api/v1/notifications")
            return len(items) >= 3 and all(item["status"] == "delivered" for item in items)
        eventually(alerts_ready, "dos alertas financieras y una de consumo")
        consumption, _ = request("/api/v1/devices/" + device_id + "/consumption?month=" + month)
        assert consumption["reading_count"] == 2 and Decimal(consumption["kwh"]) == Decimal("25")
        assert Decimal(consumption["estimated_cost"]) == Decimal("18.75")

    summary, _ = request("/api/v1/summary?month=" + month + "&currency=PEN")
    assert Decimal(summary["balance"]) == Decimal("895" if complete else "1000")
    info = {"created_at": now.isoformat(), "url": base_url, "user_id": owner, "month": month,
            "mode": "respaldo_completo" if complete else "presentacion_en_vivo", "summary": summary,
            "device_id": device_id,
            "instructions": "Abrir Cuenta demo, copiar user_id y elegir Cambiar cuenta demo."}
    output = ROOT / "reports/presentacion"
    output.mkdir(parents=True, exist_ok=True)
    (output / ("respaldo.json" if complete else "presentacion.json")).write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Aplicacion:", base_url)
    print("Cuenta demo:", owner)
    print("Periodo:", month)
    print("Modo:", info["mode"])
    print("Balance inicial:", summary["balance"], "PEN")
    print(info["instructions"])
    print("Datos guardados en", output.name + "/" + ("respaldo.json" if complete else "presentacion.json"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preparar una cuenta nueva para demostrar FinanZen")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--completa", action="store_true", help="Preparar el recorrido terminado como respaldo")
    args = parser.parse_args()
    main(args.base_url.rstrip("/"), args.completa)
