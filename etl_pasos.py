#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import csv
import math
import os
import re
import unicodedata
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

PATRON_CERRADO = re.compile(
    r"\b("
    r"CERRADO|CERRADA|"
    r"INHABILITADO|INHABILITADA|DESHABILITADO|DESHABILITADA|SIN HABILITAR|"
    r"NO\s+(?:SE\s+ENCUENTRA\s+|ESTA\s+)?(?:HABILITADO|HABILITADA|ABIERTO|ABIERTA)|"
    r"(?:HABILITADO|HABILITADA|ABIERTO|ABIERTA)\s+NO|"
    r"SUSPENDIDO|SUSPENDIDA|CLAUSURADO|CLAUSURADA|INTRANSITABLE|CORTADO|CORTADA|INTERRUMPIDO|INTERRUMPIDA"
    r")\b"
)

PATRON_ABIERTO = re.compile(r"\b(ABIERTO|ABIERTA|HABILITADO|HABILITADA)\b")


# ==============================================================================
# NORMALIZACIÓN, EXCLUSIONES (17 PASOS "NO") Y REGLAS PROVINCIALES
# ==============================================================================
def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    s = unicodedata.normalize("NFD", str(texto))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^A-Z0-9\s]", " ", s.upper())
    return re.sub(r"\s+", " ", s).strip()


# 17 pasos marcados como "NO" en la auditoría
PASOS_EXCLUIDOS = (
    "HUAYTIQUINA",
    "LA CHAPETONA",
    "CALDERAS",
    "PACHON",
    "SANTA ELENA",
    "LUMABIA",
    "NELLOCAHUI",
    "COLOCO",
    "EDGARDO CASTELLO",
    "VIEDMA",
    "LAGO ROSARIO",
    "CAMARONES",
    "RAWSON",
    "EL BELLO",
    "GLACIAR DOS LAGOS",
    "PUNTA DUNGENESS",
    "PUNTA LOYOLA",
    "MUELLE SAN SEBASTIAN",
    "CRUZ DEL SUR",
)


def es_paso_excluido(nombre: str) -> bool:
    n = normalizar_texto(nombre)
    if not n:
        return True
    return any(ex in n for ex in PASOS_EXCLUIDOS)


def normalizar_provincia(prov: str, nombre_paso: str = "") -> str:
    nom_norm = normalizar_texto(nombre_paso)
    if "PUERTO WILLIAMS" in nom_norm or "PUERTO ALMANZA" in nom_norm:
        return "TIERRA DEL FUEGO"
    if "COLONIA CANO" in nom_norm:
        return "FORMOSA"

    p = normalizar_texto(prov)
    if not p:
        return ""
    if "CIUDAD AUTONOMA" in p or p in ("CABA", "CAPITAL FEDERAL"):
        return "CIUDAD AUTONOMA DE BUENOS AIRES"
    if "BUENOS AIRES" in p:
        return "BUENOS AIRES"
    if "TIERRA DEL FUEGO" in p:
        return "TIERRA DEL FUEGO"
    if "SANTIAGO DEL ESTERO" in p:
        return "SANTIAGO DEL ESTERO"
    if "ENTRE RIOS" in p:
        return "ENTRE RIOS"
    if "RIO NEGRO" in p:
        return "RIO NEGRO"
    if "SANTA CRUZ" in p:
        return "SANTA CRUZ"
    if "SANTA FE" in p:
        return "SANTA FE"
    if "SAN JUAN" in p:
        return "SAN JUAN"
    if "SAN LUIS" in p:
        return "SAN LUIS"
    if "LA RIOJA" in p:
        return "LA RIOJA"
    if "LA PAMPA" in p:
        return "LA PAMPA"
    return p


PERFILES_PROVINCIALES = {
    "JUJUY": {"bbox": (-24.65, -21.75, -67.35, -64.10), "fallback": (-22.0967, -65.5964)},
    "SALTA": {"bbox": (-26.45, -21.95, -68.60, -62.30), "fallback": (-22.7264, -64.3700)},
    "CATAMARCA": {"bbox": (-29.20, -25.10, -69.15, -64.80), "fallback": (-26.8781, -68.3017)},
    "LA RIOJA": {"bbox": (-31.95, -27.70, -69.65, -65.40), "fallback": (-28.0467, -69.3133)},
    "SAN JUAN": {"bbox": (-32.65, -28.35, -70.65, -66.70), "fallback": (-30.1931, -69.8264)},
    "MENDOZA": {"bbox": (-37.60, -31.95, -70.60, -66.50), "fallback": (-32.8253, -70.0708)},
    "NEUQUEN": {"bbox": (-41.15, -36.10, -71.98, -68.00), "fallback": (-38.6631, -70.8964)},
    "RIO NEGRO": {"bbox": (-42.05, -37.55, -71.95, -62.75), "fallback": (-41.0631, -71.8164)},
    "CHUBUT": {"bbox": (-46.05, -41.95, -72.25, -64.95), "fallback": (-43.1803, -71.7519)},
    "SANTA CRUZ": {"bbox": (-52.45, -45.95, -73.65, -65.65), "fallback": (-51.5736, -72.3458)},
    "TIERRA DEL FUEGO": {"bbox": (-55.15, -52.60, -68.65, -63.70), "fallback": (-53.3219, -68.6042)},
    "FORMOSA": {"bbox": (-26.95, -22.40, -62.35, -57.55), "fallback": (-25.2636, -57.7119)},
    "CHACO": {"bbox": (-28.05, -24.05, -63.45, -58.30), "fallback": (-27.4861, -58.9319)},
    "MISIONES": {"bbox": (-28.20, -25.45, -56.08, -53.60), "fallback": (-27.3622, -55.8961)},
    "CORRIENTES": {"bbox": (-30.75, -27.25, -59.70, -55.60), "fallback": (-29.7338, -57.1019)},
    "ENTRE RIOS": {"bbox": (-34.08, -30.15, -60.80, -57.78), "fallback": (-33.0425, -58.2461)},
    "SANTA FE": {"bbox": (-34.40, -28.00, -62.90, -58.85), "fallback": (-32.9468, -60.6393)},
    "BUENOS AIRES": {"bbox": (-41.05, -33.25, -63.40, -56.65), "fallback": (-34.6037, -58.3816)},
    "CIUDAD AUTONOMA DE BUENOS AIRES": {
        "bbox": (-34.72, -34.52, -58.55, -58.32),
        "fallback": (-34.5997, -58.3689),
    },
    "CORDOBA": {"bbox": (-35.05, -29.50, -65.80, -61.75), "fallback": (-31.3100, -64.2083)},
    "TUCUMAN": {"bbox": (-28.05, -26.05, -66.20, -64.45), "fallback": (-26.8408, -65.1047)},
    "SANTIAGO DEL ESTERO": {"bbox": (-30.45, -25.60, -65.20, -61.70), "fallback": (-27.7656, -64.3100)},
    "SAN LUIS": {"bbox": (-36.05, -31.85, -67.35, -64.90), "fallback": (-33.2731, -66.3564)},
    "LA PAMPA": {"bbox": (-39.35, -35.00, -68.30, -63.35), "fallback": (-36.6167, -64.2833)},
}


def coordenada_valida_en_provincia(lat: float, lon: float, prov_norm: str) -> bool:
    if lat is None or lon is None or math.isnan(lat) or math.isnan(lon):
        return False
    if not (-56.0 <= lat <= -21.5 and -74.0 <= lon <= -53.0):
        return False
    perfil = PERFILES_PROVINCIALES.get(prov_norm)
    if not perfil:
        return True
    lat_min, lat_max, lon_min, lon_max = perfil["bbox"]
    return (lat_min <= lat <= lat_max) and (lon_min <= lon <= lon_max)


# ==============================================================================
# CATÁLOGO AUDITADO DE COORDENADAS EXACTAS (WGS84) POR PASO Y PROVINCIA
# ==============================================================================
CATALOGO_AUDITADO = [
    # JUJUY
    ("JUJUY", "LA QUIACA", -22.0967, -65.5964),
    ("JUJUY", "JAMA", -23.2375, -67.0714),
    ("JUJUY", "JAMA NORTE", -23.2234, -67.0557),
    ("JUJUY", "JAMA SUR", -23.2310, -67.0629),
    ("JUJUY", "JUJUY", -24.3928, -65.0978),
    # SALTA
    ("SALTA", "SOCOMPA", -24.4556, -68.2917),
    ("SALTA", "SICO", -23.8419, -67.2608),
    ("SALTA", "AGUAS BLANCAS", -22.7264, -64.3700),
    ("SALTA", "CHALANAS", -22.7311, -64.3622),
    ("SALTA", "SALVADOR MAZZA", -22.0542, -63.6869),
    ("SALTA", "MISION LA PAZ", -22.3789, -62.5206),
    ("SALTA", "EL CONDADO", -22.3833, -64.5667),
    ("SALTA", "LOS TOLDOS", -22.3833, -64.5667),
    ("SALTA", "SANTA VICTORIA", -22.2483, -64.9667),
    ("SALTA", "SALTA", -24.8559, -65.4862),
    # CATAMARCA
    ("CATAMARCA", "SAN FRANCISCO", -26.8781, -68.3017),
    # LA RIOJA
    ("LA RIOJA", "PIRCAS NEGRAS", -28.0467, -69.3133),
    # SAN JUAN
    ("SAN JUAN", "AGUA NEGRA", -30.1931, -69.8264),
    ("SAN JUAN", "SAN JUAN", -31.5714, -68.4182),
    # MENDOZA
    ("MENDOZA", "CRISTO REDENTOR", -32.8253, -70.0708),
    ("MENDOZA", "HORCONES", -32.8253, -70.0708),
    ("MENDOZA", "PEHUENCHE", -35.9839, -70.4025),
    ("MENDOZA", "PLANCHON VERGARA", -35.1636, -70.4919),
    ("MENDOZA", "VERGARA", -35.1636, -70.4919),
    ("MENDOZA", "PORTILLO DE PIUQUENES", -33.6333, -69.8667),
    ("MENDOZA", "CAJON DEL MAIPO", -34.1667, -69.8333),
    ("MENDOZA", "LAS DAMAS", -34.9667, -70.3500),
    ("MENDOZA", "EL PLUMERILLO", -32.8317, -68.7929),
    ("MENDOZA", "SAN RAFAEL", -34.5883, -68.4039),
    # NEUQUÉN
    ("NEUQUEN", "PINO HACHADO", -38.6631, -70.8964),
    ("NEUQUEN", "CARDENAL ANTONIO SAMORE", -40.7131, -71.9439),
    ("NEUQUEN", "CARDENAL SAMORE", -40.7131, -71.9439),
    ("NEUQUEN", "MAMUIL MALAL", -39.5856, -71.4847),
    ("NEUQUEN", "ICALMA", -38.8314, -71.2819),
    ("NEUQUEN", "HUA HUM", -40.1189, -71.6631),
    ("NEUQUEN", "CARIRRINE", -39.7931, -71.6531),
    ("NEUQUEN", "PICHACHEN", -37.4569, -71.1167),
    ("NEUQUEN", "COPAHUE", -37.8167, -71.1667),
    ("NEUQUEN", "PRESIDENTE PERON", -38.9490, -68.1557),
    ("NEUQUEN", "CHAPELCO", -40.0754, -71.1373),
    # RÍO NEGRO
    ("RIO NEGRO", "VICENTE PEREZ ROSALES", -41.0631, -71.8164),
    ("RIO NEGRO", "PEREZ ROSALES", -41.0631, -71.8164),
    ("RIO NEGRO", "RIO MANSO", -41.5036, -71.8492),
    ("RIO NEGRO", "PASO EL LEON", -41.5036, -71.8492),
    ("RIO NEGRO", "VURILOCHE", -41.2250, -71.8333),
    ("RIO NEGRO", "SAN CARLOS DE BARILOCHE", -41.1512, -71.1575),
    ("RIO NEGRO", "BARILOCHE", -41.1512, -71.1575),
    ("RIO NEGRO", "SAN ANTONIO OESTE", -40.7333, -64.9500),
    # CHUBUT
    ("CHUBUT", "FUTALEUFU", -43.1803, -71.7519),
    ("CHUBUT", "HUEMULES", -45.9089, -71.6442),
    ("CHUBUT", "COIHAIQUE", -45.5569, -71.7331),
    ("CHUBUT", "COYHAIQUE", -45.5569, -71.7331),
    ("CHUBUT", "EL TRIANA", -45.6761, -71.7056),
    ("CHUBUT", "TRIANA", -45.6761, -71.7056),
    ("CHUBUT", "RIO ENCUENTRO", -43.5889, -71.7556),
    ("CHUBUT", "PALENA", -43.5889, -71.7556),
    ("CHUBUT", "PAMPA ALTA", -45.2225, -71.6750),
    ("CHUBUT", "RIO FRIAS", -44.5583, -71.7333),
    ("CHUBUT", "LAS PAMPAS", -44.2167, -71.7000),
    ("CHUBUT", "RIO PUELO", -42.1000, -71.7333),
    ("CHUBUT", "PUERTO MADRYN", -42.7667, -65.0333),
    ("CHUBUT", "COMODORO RIVADAVIA", -45.8667, -67.4667),
    ("CHUBUT", "ESQUEL", -42.9078, -71.1394),
    ("CHUBUT", "ALMIRANTE MARCO ANDRES ZAR", -43.2105, -65.2703),
    ("CHUBUT", "TRELEW", -43.2105, -65.2703),
    # SANTA CRUZ
    ("SANTA CRUZ", "INTEGRACION AUSTRAL", -52.1390, -69.5195),
    ("SANTA CRUZ", "MONTE AYMOND", -52.1390, -69.5195),
    ("SANTA CRUZ", "MONTE DINERO", -52.2395, -68.8831),
    ("SANTA CRUZ", "CERRO REDONDO", -52.2234, -68.9463),
    ("SANTA CRUZ", "CONDOR DANIEL POSESION", -52.1626, -69.1507),
    ("SANTA CRUZ", "DANIEL POSESION", -52.1626, -69.1507),
    ("SANTA CRUZ", "CONDOR II", -52.2835, -68.7393),
    ("SANTA CRUZ", "CONDOR", -52.1626, -69.1507),
    ("SANTA CRUZ", "RIO MOSCO", -48.4894, -72.5528),
    ("SANTA CRUZ", "DOROTEA", -51.5736, -72.3458),
    ("SANTA CRUZ", "LAURITA", -51.6833, -72.3167),
    ("SANTA CRUZ", "CASAS VIEJAS", -51.7056, -72.3011),
    ("SANTA CRUZ", "MINA UNO", -51.5500, -72.3167),
    ("SANTA CRUZ", "RIO DON GUILLERMO", -51.2589, -72.3456),
    ("SANTA CRUZ", "CANCHA CARRERA", -51.2589, -72.3456),
    ("SANTA CRUZ", "JEINIMENI", -46.5442, -71.7167),
    ("SANTA CRUZ", "CHILE CHICO", -46.5442, -71.7167),
    ("SANTA CRUZ", "INGENIERO PALLAVICINI", -46.8142, -71.8364),
    ("SANTA CRUZ", "IBANEZ", -46.8142, -71.8364),
    ("SANTA CRUZ", "RODOLFO ROBALLOS", -47.1528, -71.8556),
    ("SANTA CRUZ", "ROBALLOS", -47.1528, -71.8556),
    ("SANTA CRUZ", "RIO MAYER", -48.2089, -72.3450),
    ("SANTA CRUZ", "RIVERA NORTE", -48.2089, -72.3450),
    ("SANTA CRUZ", "LAGO SAN MARTIN", -49.0841, -72.5590),
    ("SANTA CRUZ", "LAGO DEL DESIERTO", -49.0436, -72.8850),
    ("SANTA CRUZ", "HITO IV", -49.0436, -72.8850),
    ("SANTA CRUZ", "PUERTO DESEADO", -47.7550, -65.8933),
    ("SANTA CRUZ", "PUERTO SAN JULIAN", -49.3067, -67.7233),
    ("SANTA CRUZ", "PUERTO SANTA CRUZ", -50.0167, -68.5167),
    ("SANTA CRUZ", "PUERTO RIO GALLEGOS", -51.6167, -69.2167),
    ("SANTA CRUZ", "PILOTO CIVIL NORBERTO FERNANDEZ", -51.6089, -69.3126),
    ("SANTA CRUZ", "RIO GALLEGOS", -51.6089, -69.3126),
    ("SANTA CRUZ", "COMANDANTE ARMANDO TOLA", -50.2803, -72.0531),
    ("SANTA CRUZ", "EL CALAFATE", -50.2803, -72.0531),
    ("SANTA CRUZ", "CALETA OLIVIA", -46.4333, -67.5167),
    # TIERRA DEL FUEGO
    ("TIERRA DEL FUEGO", "SAN SEBASTIAN", -53.3219, -68.6042),
    ("TIERRA DEL FUEGO", "BAHIA SAN SEBASTIAN", -53.3219, -68.6042),
    ("TIERRA DEL FUEGO", "RADMAN", -53.8964, -68.6058),
    ("TIERRA DEL FUEGO", "RIO BELLA VISTA", -53.8964, -68.6058),
    ("TIERRA DEL FUEGO", "BELLA VISTA", -53.8964, -68.6058),
    ("TIERRA DEL FUEGO", "HITO XIX", -53.8964, -68.6058),
    ("TIERRA DEL FUEGO", "ALFA CULLEN", -52.7850, -68.6060),
    ("TIERRA DEL FUEGO", "CANADON ALFA", -52.7120, -68.6060),
    ("TIERRA DEL FUEGO", "CABO ESPIRITU SANTO", -52.6583, -68.6060),
    ("TIERRA DEL FUEGO", "PUERTO ALMANZA", -54.8722, -67.5750),
    ("TIERRA DEL FUEGO", "PUERTO WILLIAMS", -54.8722, -67.5750),
    ("TIERRA DEL FUEGO", "ALMANZA", -54.8722, -67.5750),
    ("TIERRA DEL FUEGO", "PUERTO USHUAIA", -54.8072, -68.3044),
    ("TIERRA DEL FUEGO", "MALVINAS ARGENTINAS", -54.8433, -68.2958),
    ("TIERRA DEL FUEGO", "USHUAIA", -54.8072, -68.3044),
    ("TIERRA DEL FUEGO", "PUERTO RIO GRANDE", -53.7867, -67.7000),
    ("TIERRA DEL FUEGO", "GOBERNADOR RAMON TREJO NOEL", -53.7777, -67.7494),
    ("TIERRA DEL FUEGO", "RIO GRANDE", -53.7867, -67.7000),
    # FORMOSA
    ("FORMOSA", "SAN IGNACIO DE LOYOLA", -25.2636, -57.7119),
    ("FORMOSA", "CLORINDA", -25.2636, -57.7119),
    ("FORMOSA", "PASARELA LA FRATERNIDAD", -25.2897, -57.7172),
    ("FORMOSA", "PUERTO PILCOMAYO", -25.3711, -57.6514),
    ("FORMOSA", "PUERTO FORMOSA", -26.1856, -58.1511),
    ("FORMOSA", "COLONIA CANO", -26.8529, -58.3250),
    ("FORMOSA", "PUERTO CANO", -26.8529, -58.3250),
    ("FORMOSA", "GENERAL BELGRANO", -24.7484, -58.8335),
    ("FORMOSA", "ROJAS SILVA", -24.4000, -60.4000),
    ("FORMOSA", "PASO LAMADRID", -23.9167, -60.5333),
    ("FORMOSA", "EL REMANSO", -23.6333, -60.9667),
    ("FORMOSA", "EL PUCU", -26.2127, -58.2281),
    ("FORMOSA", "FORMOSA", -26.2127, -58.2281),
    # CHACO
    ("CHACO", "PUERTO BARRANQUERAS", -27.4861, -58.9319),
    ("CHACO", "PUERTO BERMEJO", -26.9333, -58.5000),
    ("CHACO", "LAS PALMAS", -27.0667, -58.5500),
    ("CHACO", "ISLA DEL CERRITO", -27.2833, -58.6167),
    ("CHACO", "RESISTENCIA", -27.4500, -59.0561),
    # MISIONES
    ("MISIONES", "SAN ROQUE GONZALEZ DE SANTA CRUZ", -27.3622, -55.8961),
    ("MISIONES", "POSADAS", -27.3622, -55.8961),
    ("MISIONES", "TANCREDO NEVES", -25.5881, -54.5608),
    ("MISIONES", "PUERTO IGUAZU", -25.5933, -54.5788),
    ("MISIONES", "CATARATAS DEL IGUAZU", -25.7373, -54.4734),
    ("MISIONES", "TRES FRONTERAS", -25.5933, -54.5788),
    ("MISIONES", "PUERTO MEIRA", -25.5936, -54.5781),
    ("MISIONES", "BERNARDO DE IRIGOYEN", -26.2547, -53.6441),
    ("MISIONES", "COMANDANTE ANDRESITO", -25.6017, -53.9779),
    ("MISIONES", "ANDRESITO", -25.6017, -53.9779),
    ("MISIONES", "INTEGRACION", -25.7706, -53.8522),
    ("MISIONES", "SAN ANTONIO", -26.0569, -53.7305),
    ("MISIONES", "PEPIRI GUAZU", -26.6120, -53.7369),
    ("MISIONES", "EL SOBERBIO", -27.2982, -54.1937),
    ("MISIONES", "ALICIA", -27.4639, -54.3442),
    ("MISIONES", "COLONIA AURORA", -27.4995, -54.5205),
    ("MISIONES", "AURORA", -27.4995, -54.5205),
    ("MISIONES", "PUERTO LONDERO", -27.5167, -54.4833),
    ("MISIONES", "ALBA POSSE", -27.5713, -54.6792),
    ("MISIONES", "BARRA BONITA", -27.6013, -54.8431),
    ("MISIONES", "PANAMBI", -27.7300, -54.9142),
    ("MISIONES", "SAN JAVIER", -27.8920, -55.1357),
    ("MISIONES", "PASO DE LA BARCA", -27.8920, -55.1357),
    ("MISIONES", "PUERTO SANTA MARIA", -27.9678, -55.3508),
    ("MISIONES", "PUERTO SAN ISIDRO", -28.0502, -55.4157),
    ("MISIONES", "PUERTO AZARA", -28.0667, -55.6667),
    ("MISIONES", "CANDELARIA", -27.4561, -55.7461),
    ("MISIONES", "SANTA ANA", -27.3589, -55.5889),
    ("MISIONES", "SAN IGNACIO", -27.2344, -55.5567),
    ("MISIONES", "PUERTO MANI", -27.1258, -55.5089),
    ("MISIONES", "CORPUS", -27.1261, -55.5089),
    ("MISIONES", "PUERTO OASIS", -26.9708, -55.2486),
    ("MISIONES", "PUERTO LEONI", -26.9500, -55.1333),
    ("MISIONES", "PUERTO RICO", -26.7989, -55.0256),
    ("MISIONES", "PUERTO GARUHAPE", -26.7916, -54.9917),
    ("MISIONES", "PUERTO PARANAY", -26.6748, -54.8154),
    ("MISIONES", "MONTECARLO", -26.5594, -54.8039),
    ("MISIONES", "PUERTO PIRAY", -26.4697, -54.7169),
    ("MISIONES", "PUERTO PINARES", -26.4061, -54.6939),
    ("MISIONES", "ELDORADO", -26.4063, -54.6939),
    ("MISIONES", "PUERTO VICTORIA", -26.3350, -54.6636),
    ("MISIONES", "PUERTO MADO", -26.2303, -54.6294),
    ("MISIONES", "PUERTO ESPERANZA", -26.0167, -54.6333),
    ("MISIONES", "PUERTO WANDA", -25.9739, -54.6064),
    ("MISIONES", "PUERTO LIBERTAD", -25.9648, -54.6039),
    # CORRIENTES
    ("CORRIENTES", "PASO DE LOS LIBRES", -29.7338, -57.1019),
    ("CORRIENTES", "SANTO TOME", -28.6094, -56.0136),
    ("CORRIENTES", "YACYRETA", -27.4861, -56.6972),
    ("CORRIENTES", "ITUZAINGO", -27.4861, -56.6972),
    ("CORRIENTES", "ALVEAR", -29.1061, -56.5511),
    ("CORRIENTES", "MONTE CASEROS", -30.2547, -57.6242),
    ("CORRIENTES", "PUERTO CORRIENTES", -27.4633, -58.8433),
    ("CORRIENTES", "PASO DE LA PATRIA", -27.3167, -58.5667),
    ("CORRIENTES", "ITATI", -27.2667, -58.2333),
    ("CORRIENTES", "YAHAPE", -27.3667, -57.6667),
    ("CORRIENTES", "ITA IBATE", -27.4250, -57.3333),
    ("CORRIENTES", "SAN ANTONIO DE APIPE", -27.5000, -56.7500),
    ("CORRIENTES", "GARRUCHOS", -28.1833, -55.6333),
    ("CORRIENTES", "GARAVI", -28.3167, -55.8167),
    ("CORRIENTES", "YAPEYU", -29.4667, -56.8167),
    ("CORRIENTES", "DOCTOR FERNANDO PIRAGINE NIVEYRO", -27.4455, -58.7619),
    ("CORRIENTES", "CORRIENTES", -27.4455, -58.7619),
    # ENTRE RÍOS
    ("ENTRE RIOS", "GUALEGUAYCHU", -33.0425, -58.2461),
    ("ENTRE RIOS", "LIBERTADOR GENERAL SAN MARTIN", -33.0425, -58.2461),
    ("ENTRE RIOS", "COLON", -32.2647, -58.0992),
    ("ENTRE RIOS", "GENERAL ARTIGAS", -32.2647, -58.0992),
    ("ENTRE RIOS", "CONCORDIA", -31.2731, -57.9347),
    ("ENTRE RIOS", "SALTO GRANDE", -31.2731, -57.9347),
    ("ENTRE RIOS", "CONCEPCION DEL URUGUAY", -32.4861, -58.2231),
    ("ENTRE RIOS", "PUERTO FEDERACION", -30.9833, -57.9167),
    ("ENTRE RIOS", "PUERTO PARANACITO", -33.7167, -58.6500),
    ("ENTRE RIOS", "PUERTO IBICUY", -33.7500, -59.1833),
    ("ENTRE RIOS", "GUAZU GUAZUCITO", -33.9000, -58.5000),
    ("ENTRE RIOS", "GENERAL JUSTO JOSE DE URQUIZA", -31.7948, -60.4804),
    ("ENTRE RIOS", "PARANA", -31.7948, -60.4804),
    # SANTA FE
    ("SANTA FE", "ISLAS MALVINAS", -32.9036, -60.7850),
    ("SANTA FE", "ROSARIO", -32.9036, -60.7850),
    ("SANTA FE", "SAUCE VIEJO", -31.7117, -60.8117),
    ("SANTA FE", "SANTA FE", -31.7117, -60.8117),
    # BUENOS AIRES
    ("BUENOS AIRES", "MINISTRO PISTARINI", -34.8222, -58.5358),
    ("BUENOS AIRES", "EZEIZA", -34.8222, -58.5358),
    ("BUENOS AIRES", "SAN FERNANDO", -34.4532, -58.5896),
    ("BUENOS AIRES", "EL PALOMAR", -34.6099, -58.6126),
    ("BUENOS AIRES", "ASTOR PIAZZOLLA", -37.9342, -57.5733),
    ("BUENOS AIRES", "MAR DEL PLATA", -37.9342, -57.5733),
    ("BUENOS AIRES", "COMANDANTE ESPORA", -38.7250, -62.1693),
    ("BUENOS AIRES", "BAHIA BLANCA", -38.7250, -62.1693),
    ("BUENOS AIRES", "LA PLATA", -34.8667, -57.8833),
    ("BUENOS AIRES", "TIGRE", -34.4192, -58.5789),
    ("BUENOS AIRES", "PUERTO SAN ISIDRO", -34.4550, -58.5083),
    ("BUENOS AIRES", "PUERTO OLIVOS", -34.5067, -58.4767),
    ("BUENOS AIRES", "PUERTO MARTIN GARCIA", -34.1833, -58.2500),
    ("BUENOS AIRES", "PUERTO QUILMES", -34.7067, -58.2233),
    ("BUENOS AIRES", "ESCOBAR", -34.3167, -58.7333),
    ("BUENOS AIRES", "ZARATE", -34.0917, -59.0083),
    ("BUENOS AIRES", "CAMPANA", -34.1583, -58.9583),
    ("BUENOS AIRES", "SAN PEDRO", -33.6767, -59.6433),
    ("BUENOS AIRES", "RAMALLO", -33.4833, -60.0000),
    ("BUENOS AIRES", "SAN NICOLAS", -33.3333, -60.2000),
    ("BUENOS AIRES", "QUEQUEN", -38.5800, -58.7033),
    # CABA
    ("CIUDAD AUTONOMA DE BUENOS AIRES", "JORGE NEWBERY", -34.5592, -58.4156),
    ("CIUDAD AUTONOMA DE BUENOS AIRES", "AEROPARQUE", -34.5592, -58.4156),
    ("CIUDAD AUTONOMA DE BUENOS AIRES", "PUERTO BUENOS AIRES", -34.5997, -58.3689),
    ("CIUDAD AUTONOMA DE BUENOS AIRES", "BUQUEBUS", -34.5997, -58.3689),
    ("CIUDAD AUTONOMA DE BUENOS AIRES", "COLONIA EXPRESS", -34.6025, -58.3611),
    # INTERIOR (AEROPUERTOS INTERNACIONALES)
    ("CORDOBA", "INGENIERO AERONAUTICO AMBROSIO TARAVELLA", -31.3100, -64.2083),
    ("CORDOBA", "TARAVELLA", -31.3100, -64.2083),
    ("CORDOBA", "CORDOBA", -31.3100, -64.2083),
    ("TUCUMAN", "TENIENTE GENERAL BENJAMIN MATIENZO", -26.8408, -65.1047),
    ("TUCUMAN", "MATIENZO", -26.8408, -65.1047),
    ("TUCUMAN", "TUCUMAN", -26.8408, -65.1047),
    ("SANTIAGO DEL ESTERO", "TERMAS DE RIO HONDO", -27.4967, -64.9358),
    ("SANTIAGO DEL ESTERO", "SANTIAGO DEL ESTERO", -27.7656, -64.3100),
    ("SAN LUIS", "VALLE DEL CONLARA", -32.3833, -65.1833),
    ("SAN LUIS", "SAN LUIS", -33.2731, -66.3564),
    ("LA PAMPA", "SANTA ROSA", -36.5883, -64.2756),
]

CATALOGO_ORDENADO = sorted(CATALOGO_AUDITADO, key=lambda x: len(x[1]), reverse=True)


def buscar_en_catalogo_auditado(nombre_paso: str, prov_norm: str):
    nom = normalizar_texto(nombre_paso)
    if not nom:
        return None
    for cat_prov, clave, lat, lon in CATALOGO_ORDENADO:
        if prov_norm and cat_prov != prov_norm:
            continue
        if re.search(rf"\b{re.escape(clave)}\b", nom):
            return (lat, lon)
    if not prov_norm:
        for _, clave, lat, lon in CATALOGO_ORDENADO:
            if len(clave) >= 6 and re.search(rf"\b{re.escape(clave)}\b", nom):
                return (lat, lon)
    return None


def supervisar_coordenada(nombre_paso: str, prov_norm: str, lat_raw: str, lon_raw: str):
    coord_cat = buscar_en_catalogo_auditado(nombre_paso, prov_norm)
    if coord_cat:
        return (f"{coord_cat[0]:.6f}", f"{coord_cat[1]:.6f}", "CATALOGO")

    try:
        lat = float(str(lat_raw).replace(",", ".").strip())
        lon = float(str(lon_raw).replace(",", ".").strip())
        if coordenada_valida_en_provincia(lat, lon, prov_norm):
            return (f"{lat:.6f}", f"{lon:.6f}", "API_VALIDA")
    except (ValueError, TypeError):
        pass

    perfil = PERFILES_PROVINCIALES.get(prov_norm)
    if perfil:
        fb_lat, fb_lon = perfil["fallback"]
        return (f"{fb_lat:.6f}", f"{fb_lon:.6f}", "FALLBACK_PROV")

    return ("", "", "SIN_COORD")


# ==============================================================================
# EXTRACCIÓN Y NORMALIZACIÓN DE DATOS DEL PORTAL OFICIAL (CON DOBLE CHEQUEO)
# ==============================================================================
def limpiar_html(valor) -> str:
    if valor is None:
        return ""
    texto = str(valor)
    if "<" in texto and ">" in texto:
        return BeautifulSoup(texto, "html.parser").get_text(" ", strip=True)
    return texto.strip()


def extraer_nombre_y_url(valor_nombre):
    raw = str(valor_nombre or "")
    url_detalle = ""
    if "<a " in raw.lower():
        soup = BeautifulSoup(raw, "html.parser")
        a_tag = soup.find("a")
        if a_tag and a_tag.get("href"):
            url_detalle = a_tag.get("href").strip()
        texto = soup.get_text(" ", strip=True)
    else:
        texto = limpiar_html(raw)

    if not texto and url_detalle:
        slug = unquote(url_detalle.rstrip("/").split("/")[-1])
        slug = re.sub(r"^(paso[-_]+)?(internacional[-_]+)?", "", slug, flags=re.IGNORECASE)
        texto = slug.replace("-", " ").replace("_", " ").strip().title()

    return texto, url_detalle


def clasificar_medio(texto_completo: str, url_detalle: str = "", tipo_raw: str = "") -> str:
    fuente = normalizar_texto(f"{tipo_raw} {texto_completo} {url_detalle}")
    if any(
        k in fuente
        for k in (
            "AEROPUERTO",
            "AEREO",
            "AEROPARQUE",
            "PISTARINI",
            "EZEIZA",
            "NEWBERY",
            "EL PALOMAR",
            "TARAVELLA",
            "PLUMERILLO",
            "MALVINAS ARGENTINAS",
            "PILOTO CIVIL",
            "MATIENZO",
        )
    ):
        return "Aéreo"
    if any(k in fuente for k in ("FERROVIARIO", "FERROCARRIL", "TREN")):
        return "Ferroviario"
    if any(k in fuente for k in ("PEATONAL", "SENDERO", "PASARELA")):
        return "Peatonal"
    if any(
        k in fuente
        for k in (
            "FLUVIAL",
            "MARITIMO",
            "PUERTO",
            "BALSA",
            "LANCHA",
            "MUELLE",
            "TERMINAL FLUVIAL",
            "BUQUEBUS",
            "COLONIA EXPRESS",
            "CHALANAS",
            "RIO PARANA",
            "RIO URUGUAY",
            "RIO DE LA PLATA",
            "RIO PILCOMAYO",
            "RIO BERMEJO",
            "CANAL BEAGLE",
            "ESTRECHO DE MAGALLANES",
        )
    ):
        return "Fluvial / Marítimo"
    return "Vial / Terrestre"


def normalizar_estado(estado_raw):
    texto_limpio = limpiar_html(str(estado_raw or ""))
    if not texto_limpio and "<" in str(estado_raw):
        soup = BeautifulSoup(str(estado_raw), "html.parser")
        attrs = []
        for tag in soup.find_all(True):
            for attr in ("title", "alt", "aria-label"):
                if tag.get(attr):
                    attrs.append(str(tag.get(attr)))
        texto_limpio = " ".join(attrs)

    estado = normalizar_texto(texto_limpio)
    if not estado or estado in ("SIN DATO", "SIN DATOS", "N A", "NONE", "NAN"):
        return "SIN_DATO"

    if PATRON_CERRADO.search(estado):
        return "CERRADO"

    if PATRON_ABIERTO.search(estado):
        return "ABIERTO"

    return "SIN_DATO"


def extraer_estado_desde_html_ficha(soup) -> str:
    """
    Lee el estado operativo real directamente desde la ficha individual HTML
    de cada paso en argentina.gob.ar, evitando falsos positivos por textos de horarios.
    """
    contenedor = (
        soup.find("main")
        or soup.find("article")
        or soup.find(class_=re.compile(r"region-content|node-paso"))
        or soup
    )

    for el in contenedor.find_all(["span", "div", "p", "strong", "li", "td", "dd", "h2", "h3", "h4"]):
        txt = normalizar_texto(el.get_text(" ", strip=True))
        if not txt or len(txt) > 90:
            continue
        clases_str = " ".join(el.get("class") or [])
        es_badge_alerta = bool(
            re.search(r"(danger|cerrad|inhabilit|alert|label|badge|estado|status)", clases_str, re.I)
        )
        es_etiqueta_estado = bool(
            re.match(
                r"^(ESTADO|SITUACION|TRANSITABILIDAD|"
                r"PASO\s+(?:CERRADO|ABIERTO|HABILITADO|INHABILITADO)|"
                r"CERRADO|CERRADA|ABIERTO|ABIERTA|HABILITADO|HABILITADA|INHABILITADO|NO\s+HABILITADO)\b",
                txt,
            )
        )
        if es_badge_alerta or es_etiqueta_estado:
            if PATRON_CERRADO.search(txt):
                return "CERRADO"
            if PATRON_ABIERTO.search(txt):
                return "ABIERTO"

    texto_main = normalizar_texto(contenedor.get_text(" ", strip=True))
    if re.search(
        r"\b(?:ESTADO(?:\s+DEL\s+PASO)?|SITUACION|PASO(?:\s+FRONTERIZO|\s+INTERNACIONAL)?)\s*:?\s*"
        r"(?:SE\s+ENCUENTRA\s+|ESTA\s+)?"
        r"(?:CERRADO|CERRADA|INHABILITADO|INHABILITADA|NO\s+HABILITADO|NO\s+ABIERTO|SUSPENDIDO|CLAUSURADO|INTRANSITABLE)\b",
        texto_main,
    ):
        return "CERRADO"

    if re.search(
        r"\b(?:ESTADO(?:\s+DEL\s+PASO)?|SITUACION|PASO(?:\s+FRONTERIZO|\s+INTERNACIONAL)?)\s*:?\s*"
        r"(?:SE\s+ENCUENTRA\s+|ESTA\s+)?"
        r"(?:ABIERTO|ABIERTA|HABILITADO|HABILITADA)\b",
        texto_main,
    ):
        return "ABIERTO"

    return ""


def generar_id_paso(item: dict, nombre: str, provincia: str) -> str:
    for clave in ("nid", "id", "id_paso", "codigo"):
        val = limpiar_html(item.get(clave, ""))
        if val:
            return val
    base = normalizar_texto(f"{nombre}_{provincia}").replace(" ", "_")
    return base[:80] if base else "SIN_ID"


def extraer_campo(item: dict, claves_posibles: list) -> str:
    for k in claves_posibles:
        if k in item and item[k] not in (None, ""):
            return limpiar_html(item[k])
    item_lower = {str(k).lower(): v for k, v in item.items()}
    for k in claves_posibles:
        if k.lower() in item_lower and item_lower[k.lower()] not in (None, ""):
            return limpiar_html(item_lower[k.lower()])
    return ""


def consultar_ficha_detalle(tarea):
    id_paso, url_relativa, nombre_actual, tipo_raw = tarea
    if not url_relativa:
        return id_paso, nombre_actual, clasificar_medio(nombre_actual, "", tipo_raw), ""

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
            estado_ficha = extraer_estado_desde_html_ficha(soup)
            nombre_final = (
                nombre_actual
                if nombre_actual and not nombre_actual.startswith("Paso ")
                else (nombre_ficha or nombre_actual)
            )
            return id_paso, nombre_final, medio, estado_ficha
    except requests.RequestException:
        pass

    return id_paso, nombre_actual, clasificar_medio(nombre_actual, url_relativa, tipo_raw), ""


# ==============================================================================
# PERSISTENCIA EN CSV (CON SUPERVISIÓN INTEGRADA)
# ==============================================================================
def cargar_maestro() -> dict:
    maestro = {}
    if os.path.exists(ARCHIVO_MAESTRO):
        with open(ARCHIVO_MAESTRO, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                id_p = row.get("id_paso", "").strip()
                if not id_p:
                    continue
                nom = row.get("nombre_paso", "").strip()
                if es_paso_excluido(nom):
                    continue
                prov = normalizar_provincia(row.get("provincia", ""), nom)
                lat_sup, lon_sup, _ = supervisar_coordenada(
                    nom, prov, row.get("Latitud", ""), row.get("Longitud", "")
                )
                row["provincia"] = prov
                row["Latitud"] = lat_sup
                row["Longitud"] = lon_sup
                maestro[id_p] = row
    return maestro


def guardar_maestro(maestro: dict):
    campos = ["id_paso", "nombre_paso", "provincia", "pais", "medio", "Latitud", "Longitud", "verificado"]
    with open(ARCHIVO_MAESTRO, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        for id_p in sorted(maestro.keys(), key=lambda x: (maestro[x]["provincia"], maestro[x]["nombre_paso"])):
            writer.writerow(maestro[id_p])


def ejecutar_extraccion():
    print(f"[{AHORA.strftime('%Y-%m-%d %H:%M:%S')}] Consultando API oficial de Pasos Fronterizos...")
    resp = requests.get(LISTADO_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("results") or payload.get("data") or payload.get("pasos") or []
    else:
        raise ValueError("Formato de respuesta inesperado en el endpoint oficial.")

    print(f"Registros recibidos del portal: {len(items)}")

    maestro = cargar_maestro()
    datos_base = {}
    tareas_detalle = []

    for p in items:
        if not isinstance(p, dict):
            continue

        raw_nom = p.get("nombre") or p.get("title") or p.get("paso") or p.get("nombre_paso") or ""
        nombre, url_detalle = extraer_nombre_y_url(raw_nom)
        if not url_detalle:
            url_detalle = extraer_campo(p, ["url", "path", "link", "view_node"])

        if es_paso_excluido(nombre):
            continue

        provincia_raw = extraer_campo(p, ["provincia", "jurisdiccion", "distrito"])
        pais = extraer_campo(p, ["pais", "pais_limitrofe", "limitrofe", "frontera"])
        tipo_raw = extraer_campo(p, ["tipo", "medio", "via", "categoria", "modalidad"])

        estados_candidatos = [
            extraer_campo(p, [clave])
            for clave in (
                "estado_prioridad",
                "estado",
                "estado_paso",
                "status",
                "situacion",
                "habilitado",
                "observaciones",
                "detalle",
                "alerta",
                "novedades",
                "condicion",
                "transitabilidad",
            )
        ]
        estado_raw = " | ".join(val for val in estados_candidatos if val)
        estado_json = normalizar_estado(estado_raw)

        lat_raw = extraer_campo(p, ["latitud", "lat"])
        lon_raw = extraer_campo(p, ["longitud", "lon", "lng"])

        id_paso = generar_id_paso(p, nombre, provincia_raw)
        previo = maestro.get(id_paso, {})

        nom_final = nombre or previo.get("nombre_paso", "")
        prov_final = normalizar_provincia(provincia_raw or previo.get("provincia", ""), nom_final)

        datos_base[id_paso] = {
            "nombre": nom_final,
            "provincia": prov_final,
            "pais": pais or previo.get("pais", ""),
            "estado": estado_json,
            "medio": previo.get("medio", ""),
            "lat_raw": lat_raw or previo.get("Latitud", ""),
            "lon_raw": lon_raw or previo.get("Longitud", ""),
        }

        # Doble chequeo en ficha individual HTML para TODOS los pasos
        tareas_detalle.append((id_paso, url_detalle, datos_base[id_paso]["nombre"], tipo_raw))

    if tareas_detalle:
        print(f"Ejecutando doble chequeo en ficha individual HTML para los {len(tareas_detalle)} pasos...")
        corregidos_detalle = 0
        with ThreadPoolExecutor(max_workers=10) as executor:
            for id_p, nombre_ficha, medio_ficha, estado_ficha in executor.map(
                consultar_ficha_detalle, tareas_detalle
            ):
                if nombre_ficha:
                    datos_base[id_p]["nombre"] = nombre_ficha
                    datos_base[id_p]["provincia"] = normalizar_provincia(
                        datos_base[id_p]["provincia"], nombre_ficha
                    )
                if medio_ficha:
                    datos_base[id_p]["medio"] = medio_ficha

                estado_anterior = datos_base[id_p]["estado"]
                if estado_ficha == "CERRADO" or estado_anterior == "CERRADO":
                    estado_final = "CERRADO"
                elif estado_ficha in ("ABIERTO", "CERRADO"):
                    estado_final = estado_ficha
                else:
                    estado_final = estado_anterior

                if estado_final != estado_anterior:
                    corregidos_detalle += 1
                    print(
                        f"  [DOBLE CHEQUEO] {datos_base[id_p]['nombre']} ({datos_base[id_p]['provincia']}): "
                        f"JSON={estado_anterior} -> FICHA={estado_ficha} => FINAL={estado_final}"
                    )
                datos_base[id_p]["estado"] = estado_final

        print(f"Total de estados corregidos tras doble chequeo en ficha: {corregidos_detalle}")

    registros_hoy = {}
    stats_coord = {"CATALOGO": 0, "API_VALIDA": 0, "FALLBACK_PROV": 0, "SIN_COORD": 0}

    for id_p, info in datos_base.items():
        if es_paso_excluido(info["nombre"]):
            continue

        medio_final = info["medio"] or clasificar_medio(info["nombre"], "", "")
        lat_sup, lon_sup, origen_coord = supervisar_coordenada(
            info["nombre"], info["provincia"], info["lat_raw"], info["lon_raw"]
        )
        stats_coord[origen_coord] = stats_coord.get(origen_coord, 0) + 1

        maestro[id_p] = {
            "id_paso": id_p,
            "nombre_paso": info["nombre"],
            "provincia": info["provincia"],
            "pais": info["pais"],
            "medio": medio_final,
            "Latitud": lat_sup,
            "Longitud": lon_sup,
            "verificado": "SI",
        }
        registros_hoy[id_p] = {
            "fecha": HOY,
            "anio": ANIO_ACTUAL,
            "mes": MESES_ES[MES_ACTUAL],
            "id_paso": id_p,
            "nombre_paso": info["nombre"],
            "provincia": info["provincia"],
            "pais": info["pais"],
            "medio": medio_final,
            "Latitud": lat_sup,
            "Longitud": lon_sup,
            "estado": info["estado"],
        }

    guardar_maestro(maestro)
    print(f"Supervisión geográfica -> {stats_coord}")

    # Actualizar histórico diario (filtrando excluidos y supervisando coordenadas en filas previas)
    historico = {}
    if os.path.exists(ARCHIVO_HISTORICO):
        with open(ARCHIVO_HISTORICO, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                nom = row.get("nombre_paso", "")
                if es_paso_excluido(nom):
                    continue
                id_p = row["id_paso"]
                m_info = maestro.get(id_p, {})
                prov = normalizar_provincia(m_info.get("provincia") or row.get("provincia", ""), nom)
                lat_sup, lon_sup, _ = supervisar_coordenada(
                    nom, prov, m_info.get("Latitud") or row.get("Latitud", ""), m_info.get("Longitud") or row.get("Longitud", "")
                )
                row["nombre_paso"] = m_info.get("nombre_paso") or nom
                row["provincia"] = prov
                row["pais"] = m_info.get("pais") or row.get("pais", "")
                row["medio"] = m_info.get("medio") or row.get("medio", "")
                row["Latitud"] = lat_sup
                row["Longitud"] = lon_sup
                historico[(row["fecha"], id_p)] = row

    for id_p, reg in registros_hoy.items():
        historico[(HOY, id_p)] = reg

    campos_hist = [
        "fecha",
        "anio",
        "mes",
        "id_paso",
        "nombre_paso",
        "provincia",
        "pais",
        "medio",
        "Latitud",
        "Longitud",
        "estado",
    ]
    with open(ARCHIVO_HISTORICO, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=campos_hist)
        writer.writeheader()
        for key in sorted(historico.keys()):
            writer.writerow(historico[key])

    # Generar consolidado mensual anual
    resumen = {}
    for (_, id_p), row in historico.items():
        if es_paso_excluido(row.get("nombre_paso", "")):
            continue
        clave = (row["anio"], row["mes"], id_p)
        if clave not in resumen:
            m_info = maestro.get(id_p, {})
            resumen[clave] = {
                "Año": row["anio"],
                "Mes": row["mes"],
                "Nombre del Paso": m_info.get("nombre_paso") or row["nombre_paso"],
                "Provincia": m_info.get("provincia") or row["provincia"],
                "País Limítrofe": m_info.get("pais") or row["pais"],
                "Medio": m_info.get("medio") or row["medio"],
                "Latitud": m_info.get("Latitud") or row.get("Latitud", ""),
                "Longitud": m_info.get("Longitud") or row.get("Longitud", ""),
                "Días Abierto": 0,
                "Días Cerrado": 0,
                "Días Sin Dato": 0,
            }
        est = row["estado"]
        if est == "ABIERTO":
            resumen[clave]["Días Abierto"] += 1
        elif est == "CERRADO":
            resumen[clave]["Días Cerrado"] += 1
        else:
            resumen[clave]["Días Sin Dato"] += 1

    campos_cons = [
        "Año",
        "Mes",
        "Nombre del Paso",
        "Provincia",
        "País Limítrofe",
        "Medio",
        "Latitud",
        "Longitud",
        "Días Abierto",
        "Días Cerrado",
        "Días Sin Dato",
    ]
    with open(ARCHIVO_CONSOLIDADO, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=campos_cons)
        writer.writeheader()
        for clave in sorted(resumen.keys(), key=lambda x: (x[0], x[1], resumen[x]["Provincia"], resumen[x]["Nombre del Paso"])):
            writer.writerow(resumen[clave])

    print(f"ETL finalizado OK. Pasos válidos procesados hoy ({HOY}): {len(registros_hoy)}")


if __name__ == "__main__":
    ejecutar_extraccion()
