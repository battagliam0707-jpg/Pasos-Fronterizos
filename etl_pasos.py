import csv
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

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


def extraer_campo(diccionario, claves_posibles):
    for clave in claves_posibles:
        val = diccionario.get(clave)
        if isinstance(val, dict):
            val = val.get("nombre") or val.get("name") or val.get("title") or val.get("value")
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def limpiar_html(texto):
    if "<" in texto and ">" in texto:
        return BeautifulSoup(texto, "html.parser").get_text(" ", strip=True)
    return texto.strip()


def extraer_url_detalle(p, id_paso, tipo_raw):
    # 1. Buscar en claves habituales
    url = extraer_campo(p, ["url", "alias", "link", "ruta", "path", "url_detalle", "detalle", "view_node"])
    if "<a " in url:
        match = re.search(r'href=["\']([^"\']+)["\']', url)
        if match:
            return match.group(1)
    if url:
        return url

    # 2. Buscar cualquier enlace a /detalle/ dentro de todos los campos del registro
    for val in p.values():
        if isinstance(val, str) and "/seguridad/pasosinternacionales/detalle/" in val:
            match = re.search(r'href=["\']([^"\']+)["\']', val)
            return match.group(1) if match else val.strip()

    # 3. Reconstruir URL a partir de tipo (ruta/rio) e ID si están disponibles
    tipo_norm = tipo_raw.lower()
    if "rio" in tipo_norm or "fluvial" in tipo_norm:
        return f"/seguridad/pasosinternacionales/detalle/rio/{id_paso}"
    if id_paso.isdigit():
        return f"/seguridad/pasosinternacionales/detalle/ruta/{id_paso}"
    return ""


def nombre_desde_url(url):
    if not url:
        return ""
    partes = [seg for seg in url.rstrip("/").split("/") if seg]
    if partes and not partes[-1].isdigit():
        return unquote(partes[-1]).replace("-", " ").strip()
    return ""


def clasificar_medio(texto_completo, url_detalle="", tipo_raw=""):
    txt = f"{texto_completo} {url_detalle} {tipo_raw}".lower()

    if (
        "/rio/" in txt
        or any(k in txt for k in ["fluvial", "balsa", "lancha", "barcaza", "ferry", "catamarán", "catamaran", "embarcación", "embarcacion", "puerto "])
    ):
        return "Fluvial"

    if any(k in txt for k in ["ferroviario", "ferrocarril", "tren"]):
        return "Ferroviario"

    if re.search(r"cruce de veh[íi]culos[:\s]+no\b", txt) and re.search(r"cruce de personas[:\s]+s[íi]\b", txt):
        return "Peatonal / Sendero"

    return "Vial"


def consultar_ficha_detalle(tarea):
    id_paso, url_relativa, nombre_actual, tipo_raw = tarea
    if not url_relativa:
        return id_paso, nombre_actual, clasificar_medio(nombre_actual, "", tipo_raw)

    url_completa = url_relativa if url_relativa.startswith("http") else f"{BASE_URL}{url_relativa}"
    try:
        resp = requests.get(url_completa, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            h1 = soup.find("h1")
            nombre_ficha = h1.get_text(" ", strip=True) if h1 else ""
            nombre_ficha = re.sub(r"^Paso\s+", "", nombre_ficha, flags=re.IGNORECASE).strip()

            texto_pagina = soup.get_text(" ", strip=True)
            medio = clasificar_medio(f"{nombre_ficha} {texto_pagina}", resp.url, tipo_raw)
            nombre_final = nombre_actual if nombre_actual and not nombre_actual.startswith("Paso ") else (nombre_ficha or nombre_actual)
            return id_paso, nombre_final, medio
    except requests.RequestException:
        pass

    return id_paso, nombre_actual, clasificar_medio(nombre_actual, url_relativa, tipo_raw)


def cargar_csv(ruta, clave):
    if not os.path.exists(ruta):
        return {}
    with open(ruta, mode="r", encoding="utf-8-sig", newline="") as f:
        return {row[clave]: row for row in csv.DictReader(f) if clave in row}


def guardar_csv(ruta, filas, columnas):
    with open(ruta, mode="w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columnas)
        writer.writeheader()
        writer.writerows(filas)


def normalizar_estado(estado_raw):
    estado = limpiar_html(estado_raw).upper()
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
        print("Claves del JSON oficial:", list(pasos[0].keys()))

    maestro = cargar_csv(ARCHIVO_MAESTRO, "id_paso")
    historico = {}

    if os.path.exists(ARCHIVO_HISTORICO):
        with open(ARCHIVO_HISTORICO, mode="r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if "fecha" in row and "id_paso" in row:
                    historico[(row["fecha"], row["id_paso"])] = {
                        "fecha": row["fecha"],
                        "id_paso": row["id_paso"],
                        "nombre_paso": row.get("nombre_paso", ""),
                        "estado": row.get("estado", "SIN_DATO"),
                    }

    tareas_detalle = []
    datos_base = {}

    for p in pasos:
        id_paso = extraer_campo(p, ["id", "nid", "id_paso", "codigo"]) or extraer_campo(
            p, ["nombre", "titulo", "title"]
        )
        tipo_raw = extraer_campo(p, ["tipo", "categoria", "modalidad", "medio", "entorno"])
        url_detalle = extraer_url_detalle(p, id_paso, tipo_raw)

        nombre = limpiar_html(
            extraer_campo(p, ["nombre", "titulo", "nombre_paso", "paso", "title", "name", "label"])
        )
        if not nombre:
            nombre = nombre_desde_url(url_detalle) or f"Paso {id_paso}"

        provincia = limpiar_html(
            extraer_campo(p, ["provincia", "provincia_nombre", "nombre_provincia", "state"])
        )
        pais = limpiar_html(
            extraer_campo(p, ["pais", "pais_limitrofe", "pais_nombre", "nombre_pais", "country"])
        )
        estado_raw = extraer_campo(
            p, ["estado_prioridad", "estado", "estado_paso", "status", "situacion"]
        )

        previo = maestro.get(id_paso, {})
        datos_base[id_paso] = {
            "nombre": nombre or previo.get("nombre_paso", ""),
            "provincia": provincia or previo.get("provincia", ""),
            "pais": pais or previo.get("pais", ""),
            "estado": normalizar_estado(estado_raw),
        }

        # Consultar ficha si nunca se verificó o si falta el nombre/medio
        if previo.get("verificado") != "SI" or not previo.get("medio") or previo.get("nombre_paso", "").startswith("Paso "):
            tareas_detalle.append((id_paso, url_detalle, datos_base[id_paso]["nombre"], tipo_raw))
        else:
            datos_base[id_paso]["medio"] = previo["medio"]

    if tareas_detalle:
        print(f"Verificando ficha individual de {len(tareas_detalle)} pasos...")
        with ThreadPoolExecutor(max_workers=6) as executor:
            for id_p, nombre_ficha, medio_ficha in executor.map(consultar_ficha_detalle, tareas_detalle):
                if nombre_ficha:
                    datos_base[id_p]["nombre"] = nombre_ficha
                datos_base[id_p]["medio"] = medio_ficha

    for id_p, info in datos_base.items():
        maestro[id_p] = {
            "id_paso": id_p,
            "nombre_paso": info["nombre"],
            "provincia": info["provincia"],
            "pais": info["pais"],
            "medio": info.get("medio", "Vial"),
            "verificado": "SI",
        }
        historico[(HOY, id_p)] = {
            "fecha": HOY,
            "id_paso": id_p,
            "nombre_paso": info["nombre"],
            "estado": info["estado"],
        }

    # Actualizar nombres en registros históricos previos
    for key, reg in historico.items():
        id_p = reg["id_paso"]
        if id_p in maestro:
            reg["nombre_paso"] = maestro[id_p]["nombre_paso"]

    guardar_csv(
        ARCHIVO_MAESTRO,
        list(maestro.values()),
        ["id_paso", "nombre_paso", "provincia", "pais", "medio", "verificado"],
    )

    filas_hist = sorted(historico.values(), key=lambda x: (x["fecha"], x.get("nombre_paso", "")))
    guardar_csv(ARCHIVO_HISTORICO, filas_hist, ["fecha", "id_paso", "nombre_paso", "estado"])

    # Calcular acumulados anuales por paso
    conteos = {id_p: {"ABIERTO": 0, "CERRADO": 0, "SIN_DATO": 0} for id_p in maestro}
    for (fecha, id_p), reg in historico.items():
        if id_p in conteos and reg["estado"] in conteos[id_p]:
            conteos[id_p][reg["estado"]] += 1

    filas_consolidado = []
    for id_p, info in maestro.items():
        filas_consolidado.append({
            "Nombre del Paso": info["nombre_paso"],
            "Provincia": info["provincia"],
            "País Limítrofe": info["pais"],
            "Medio": info["medio"],
            "Días Abierto": conteos[id_p]["ABIERTO"],
            "Días Cerrado": conteos[id_p]["CERRADO"],
            "Días Sin Dato": conteos[id_p]["SIN_DATO"],
        })

    filas_consolidado.sort(
        key=lambda x: (x["País Limítrofe"], x["Provincia"], x["Nombre del Paso"])
    )
    guardar_csv(
        ARCHIVO_CONSOLIDADO,
        filas_consolidado,
        [
            "Nombre del Paso",
            "Provincia",
            "País Limítrofe",
            "Medio",
            "Días Abierto",
            "Días Cerrado",
            "Días Sin Dato",
        ],
    )
    print(f"[{HOY}] Consolidado generado en {ARCHIVO_CONSOLIDADO} ({len(filas_consolidado)} pasos).")


if __name__ == "__main__":
    ejecutar_extraccion()
