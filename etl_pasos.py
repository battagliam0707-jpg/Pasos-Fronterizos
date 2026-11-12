import csv
import os
import re
from urllib.parse import unquote
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


def extraer_campo(diccionario, claves_posibles):
    """Busca el primer valor no vacío entre varias claves posibles."""
    for clave in claves_posibles:
        val = diccionario.get(clave)
        if isinstance(val, dict):
            val = val.get("nombre") or val.get("name") or val.get("title") or val.get("value")
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def limpiar_html(texto):
    """Elimina etiquetas HTML si el JSON trae enlaces o formato."""
    if "<" in texto and ">" in texto:
        return BeautifulSoup(texto, "html.parser").get_text(" ", strip=True)
    return texto.strip()


def extraer_url_detalle(p):
    """Encuentra la URL de la ficha individual dentro del objeto JSON."""
    url = extraer_campo(p, ["url", "alias", "link", "ruta", "path", "url_detalle", "detalle"])
    if not url:
        for val in p.values():
            if isinstance(val, str) and "/seguridad/pasosinternacionales/detalle/" in val:
                match = re.search(r'href=["\']([^"\']+)["\']', val)
                return match.group(1) if match else val.strip()
    if "<a " in url:
        match = re.search(r'href=["\']([^"\']+)["\']', url)
        if match:
            return match.group(1)
    return url


def nombre_desde_url(url):
    """Si el JSON no trae el nombre limpio, lo reconstruye desde el slug de la URL."""
    if not url:
        return ""
    slug = url.rstrip("/").split("/")[-1]
    return unquote(slug).replace("-", " ").strip()


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


def inferir_medio_desde_detalle(url_relativa, nombre):
    if not url_relativa:
        return "Fluvial" if "fluvial" in nombre.lower() or "puerto" in nombre.lower() else "Vial"

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
        print("Muestra del primer registro JSON:", pasos[0])

    maestro = cargar_csv(ARCHIVO_MAESTRO, "id_paso")
    historico = {}

    if os.path.exists(ARCHIVO_HISTORICO):
        with open(ARCHIVO_HISTORICO, mode="r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                historico[(row["fecha"], row["id_paso"])] = row

    for p in pasos:
        id_paso = extraer_campo(p, ["id", "nid", "id_paso", "codigo"]) or extraer_campo(
            p, ["nombre", "titulo", "title"]
        )
        url_detalle = extraer_url_detalle(p)

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

        # Si el paso es nuevo o tenía campos vacíos de una corrida anterior, los completa
        registro_previo = maestro.get(id_paso, {})
        medio_actual = registro_previo.get("medio", "")
        if not medio_actual:
            medio_actual = inferir_medio_desde_detalle(url_detalle, nombre)

        maestro[id_paso] = {
            "id_paso": id_paso,
            "nombre_paso": nombre or registro_previo.get("nombre_paso", ""),
            "provincia": provincia or registro_previo.get("provincia", ""),
            "pais": pais or registro_previo.get("pais", ""),
            "medio": medio_actual,
        }

        historico[(HOY, id_paso)] = {
            "fecha": HOY,
            "id_paso": id_paso,
            "nombre_paso": maestro[id_paso]["nombre_paso"],
            "estado": normalizar_estado(estado_raw),
        }

    # 1. Guardar maestro actualizado
    guardar_csv(
        ARCHIVO_MAESTRO,
        list(maestro.values()),
        ["id_paso", "nombre_paso", "provincia", "pais", "medio"],
    )

    # 2. Guardar histórico diario ordenado (incluyendo nombre para fácil lectura)
    filas_hist = sorted(historico.values(), key=lambda x: (x["fecha"], x.get("nombre_paso", "")))
    guardar_csv(ARCHIVO_HISTORICO, filas_hist, ["fecha", "id_paso", "nombre_paso", "estado"])

    # 3. Calcular acumulados anuales por paso
    conteos = {id_p: {"ABIERTO": 0, "CERRADO": 0, "SIN_DATO": 0} for id_p in maestro}
    for (fecha, id_p), reg in historico.items():
        if id_p in conteos:
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
    print(f"[{HOY}] Consolidado actualizado: {len(filas_consolidado)} pasos.")


if __name__ == "__main__":
    ejecutar_extraccion()
