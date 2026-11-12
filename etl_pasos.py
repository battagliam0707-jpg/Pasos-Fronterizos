import csv
import os
import re
from datetime import datetime, timezone, timedelta
import requests
from bs4 import BeautifulSoup

AR_TZ = timezone(timedelta(hours=-3))
HOY = datetime.now(AR_TZ).strftime("%Y-%m-%d")
ANIO = datetime.now(AR_TZ).strftime("%Y")

BASE_URL = "https://www.argentina.gob.ar"
LISTADO_URL = f"{BASE_URL}/seguridad/pasosinternacionales/listado"

ARCHIVO_MAESTRO = "maestro_pasos.csv"
ARCHIVO_HISTORICO = f"historico_diario_{ANIO}.csv"
ARCHIVO_CONSOLIDADO = f"consolidado_pasos_{ANIO}.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MonitorPasosFronterizos/1.0)",
    "Accept": "application/json, text/html;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
}


def cargar_csv(ruta, clave):
    if not os.path.exists(ruta):
        return {}
    with open(ruta, mode="r", encoding="utf-8-sig", newline="") as f:
        return {row[clave]: row for row in csv.DictReader(f)}


def guardar_csv(ruta, filas, columnas):
    with open(ruta, mode="w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columnas)
        writer.writeheader()
        writer.writerows(filas)


def inferir_medio_desde_detalle(url_relativa, nombre):
    if not url_relativa:
        return "Fluvial" if "fluvial" in nombre.lower() else "Vial"

    url_completa = url_relativa if url_relativa.startswith("http") else f"{BASE_URL}{url_relativa}"
    es_rio = "/rio/" in url_completa or "fluvial" in nombre.lower() or "puerto" in nombre.lower()

    try:
        resp = requests.get(url_completa, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            texto = soup.get_text(" ", strip=True)
            match = re.search(r"Medio de paso[:\s]+([^\n|]+)", texto, re.IGNORECASE)
            if match:
                medio_raw = match.group(1).strip()
                if "No hay registro" not in medio_raw and len(medio_raw) < 60:
                    return f"Fluvial ({medio_raw})" if es_rio else medio_raw
    except requests.RequestException:
        pass

    return "Fluvial" if es_rio else "Vial"


def normalizar_estado(estado_raw):
    estado = (estado_raw or "").strip().upper()
    if "ABIERTO" in estado or "HABILITADO" in estado:
        return "ABIERTO"
    if "CERRADO" in estado or "INHABILITADO" in estado:
        return "CERRADO"
    return "SIN_DATO"


def ejecutar_extraccion():
    resp = requests.get(LISTADO_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    pasos = data if isinstance(data, list) else data.get("data") or data.get("pasos") or []
    if pasos:
        print("Claves detectadas en el JSON:", list(pasos[0].keys()))

    maestro = cargar_csv(ARCHIVO_MAESTRO, "id_paso")
    historico = {}

    if os.path.exists(ARCHIVO_HISTORICO):
        with open(ARCHIVO_HISTORICO, mode="r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                historico[(row["fecha"], row["id_paso"])] = row

    for p in pasos:
        id_paso = str(p.get("id") or p.get("nid") or p.get("nombre"))
        nombre = (p.get("nombre") or p.get("title") or "").strip()
        provincia = (p.get("provincia") or "").strip()
        pais = (p.get("pais") or p.get("pais_limitrofe") or "").strip()
        url_detalle = p.get("url") or p.get("alias") or ""
        estado_raw = p.get("estado_prioridad") or p.get("estado") or ""

        if id_paso not in maestro:
            medio = inferir_medio_desde_detalle(url_detalle, nombre)
            maestro[id_paso] = {
                "id_paso": id_paso,
                "nombre_paso": nombre,
                "provincia": provincia,
                "pais": pais,
                "medio": medio,
            }

        historico[(HOY, id_paso)] = {
            "fecha": HOY,
            "id_paso": id_paso,
            "estado": normalizar_estado(estado_raw),
        }

    guardar_csv(
        ARCHIVO_MAESTRO,
        list(maestro.values()),
        ["id_paso", "nombre_paso", "provincia", "pais", "medio"],
    )

    filas_hist = sorted(historico.values(), key=lambda x: (x["fecha"], x["id_paso"]))
    guardar_csv(ARCHIVO_HISTORICO, filas_hist, ["fecha", "id_paso", "estado"])

    conteos = {id_p: {"ABIERTO": 0, "CERRADO": 0, "SIN_DATO": 0} for id_p in maestro}
    for (fecha, id_p), reg in historico.items():
        if id_p in conteos:
            conteos[id_p][reg["estado"]] += 1

    filas_consolidado = []
    for id_p, info in maestro.items():
        filas_consolidado.append({
            "nombre_paso": info["nombre_paso"],
            "provincia": info["provincia"],
            "pais": info["pais"],
            "medio": info["medio"],
            "dias_abierto": conteos[id_p]["ABIERTO"],
            "dias_cerrado": conteos[id_p]["CERRADO"],
            "dias_sin_dato": conteos[id_p]["SIN_DATO"],
        })

    filas_consolidado.sort(key=lambda x: (x["pais"], x["provincia"], x["nombre_paso"]))
    guardar_csv(
        ARCHIVO_CONSOLIDADO,
        filas_consolidado,
        ["nombre_paso", "provincia", "pais", "medio", "dias_abierto", "dias_cerrado", "dias_sin_dato"],
    )
    print(f"[{HOY}] Proceso completado: {len(filas_consolidado)} pasos consolidados.")


if __name__ == "__main__":
    ejecutar_extraccion()
