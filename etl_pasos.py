import csv
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

AR_TZ = timezone(timedelta(hours=-3))
AHORA = datetime.now(AR_TZ)
HOY = AHORA.strftime("%Y-%m-%d")
ANIO_ACTUAL = AHORA.strftime("%Y")
MES_ACTUAL = AHORA.strftime("%m")

BASE_URL = "https://www.argentina.gob.ar"
LISTADO_URL = f"{BASE_URL}/seguridad/pasosinternacionales/listado"

ARCHIVO_MAESTRO = "maestro_pasos.csv"
ARCHIVO_HISTORICO = f"historico_diario_{ANIO_ACTUAL}.csv"
ARCHIVO_CONSOLIDADO = f"consolidado_pasos_{ANIO_ACTUAL}.csv"

MESES_ES = {
    "01": "01 - Enero",
    "02": "02 - Febrero",
    "03": "03 - Marzo",
    "04": "04 - Abril",
    "05": "05 - Mayo",
    "06": "06 - Junio",
    "07": "07 - Julio",
    "08": "08 - Agosto",
    "09": "09 - Septiembre",
    "10": "10 - Octubre",
    "11": "11 - Noviembre",
    "12": "12 - Diciembre",
}

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
    url = extraer_campo(p, ["url", "alias", "link", "ruta", "path", "url_detalle", "detalle", "view_node"])
    if "<a " in url:
        match = re.search(r'href=["\']([^"\']+)["\']', url)
        if match:
            return match.group(1)
    if url:
        return url

    for val in p.values():
        if isinstance(val, str) and "/seguridad/pasosinternacionales/detalle/" in val:
            match = re.search(r'href=["\']([^"\']+)["\']', val)
            return match.group(1) if match else val.strip()

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


def clasificar_medio(texto_completo, url_detalle
