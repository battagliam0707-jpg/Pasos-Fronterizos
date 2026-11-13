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


# Catálogo auditado de coordenadas WGS84
CATALOGO_AUDITADO = [
    # --- JUJUY ---
    ("JUJUY", "LA QUIACA", -22.0967, -65.5964),
    ("JUJUY", "VILLAZON", -22.0967, -65.5964),
    ("JUJUY", "JAMA NORTE", -23.227301, -67.063069),
    ("JUJUY", "JAMA SUR", -23.233026, -67.064815),
    ("JUJUY", "JAMA", -23.2372, -67.0725),
    ("JUJUY", "AEROPUERTO JUJUY", -24.3928, -65.0978),
    ("JUJUY", "HORACIO GUZMAN", -24.3928, -65.0978),

    # --- SALTA ---
    ("SALTA", "SALVADOR MAZZA", -22.0533, -63.6836),
    ("SALTA", "YACUIBA", -22.0533, -63.6836),
    ("SALTA", "EL CONDADO", -22.1900, -64.6531),
    ("SALTA", "LA MAMORA", -22.1900, -64.6531),
    ("SALTA", "MISION LA PAZ", -22.3789, -62.5186),
    ("SALTA", "POZO HONDO", -22.3789, -62.5186),
    ("SALTA", "AGUAS BLANCAS", -22.7264, -64.3700),
    ("SALTA", "CHALANAS", -22.7319, -64.3514),
    ("SALTA", "PUERTO CHALANAS", -22.7319, -64.3514),
    ("SALTA", "LAGUNA SICO", -23.874876, -67.271398),
    ("SALTA", "SICO", -23.8350, -67.2533),
    ("SALTA", "SOCOMPA", -24.4483, -68.2889),
    ("SALTA", "AEROPUERTO SALTA", -24.8560, -65.4862),
    ("SALTA", "MARTIN MIGUEL DE GUEMES", -24.8560, -65.4862),

    # --- CATAMARCA ---
    ("CATAMARCA", "SAN FRANCISCO", -26.8781, -68.3017),
    ("CATAMARCA", "AEROPUERTO CATAMARCA", -28.5924622103104, -65.75498297415245),
    ("CATAMARCA", "FELIPE VARELA", -28.5924622103104, -65.75498297415245),

    # --- LA RIOJA ---
    ("LA RIOJA", "PIRCAS NEGRAS", -28.0467, -69.3133),
    ("LA RIOJA", "AEROPUERTO LA RIOJA", -29.383745845336172, -66.79433575463631),
    ("LA RIOJA", "ALMANDOS ALMONACID", -29.383745845336172, -66.79433575463631),

    # --- SAN JUAN ---
    ("SAN JUAN", "PASCUA LAMA", -29.3189, -70.0156),
    ("SAN JUAN", "PORTILLO DE COLANGUIL", -30.03275792123306, -69.29369560759945),
    ("SAN JUAN", "COLANGUIL", -30.03275792123306, -69.29369560759945),
    ("SAN JUAN", "AGUA NEGRA", -30.1931, -69.8264),
    ("SAN JUAN", "MIRANDA", -30.6667, -70.2333),
    ("SAN JUAN", "PORTILLO DEL VENTILLO", -30.7167, -70.2667),
    ("SAN JUAN", "VENTILLO", -30.7167, -70.2667),
    ("SAN JUAN", "GUANA", -30.7333, -70.2700),
    ("SAN JUAN", "PORTILLO", -30.7667, -70.2680),
    ("SAN JUAN", "VALLE HERMOSO", -30.783140, -70.283190),
    ("SAN JUAN", "LOS AZULES", -30.9500, -70.3167),
    ("SAN JUAN", "CALDERON", -31.2667, -70.5333),
    ("SAN JUAN", "EL AZUFRE", -31.3000, -70.5500),
    ("SAN JUAN", "CASA DE PIEDRA", -31.5500, -70.5667),
    ("SAN JUAN", "PUENTECILLAS", -31.6667, -70.5167),
    ("SAN JUAN", "MONDACA", -31.8500, -70.4167),
    ("SAN JUAN", "LAS OJOTAS", -31.9167, -70.2333),
    ("SAN JUAN", "QUEBRADA FRIA", -32.0833, -70.3500),
    ("SAN JUAN", "LAS LLARETAS", -32.1500, -70.3167),
    ("SAN JUAN", "AEROPUERTO SAN JUAN", -31.5715, -68.4182),

    # --- MENDOZA ---
    ("MENDOZA", "CRISTO REDENTOR", -32.8253, -70.0708),
    ("MENDOZA", "LOS LIBERTADORES", -32.8253, -70.0708),
    ("MENDOZA", "HORCONES", -32.8236, -69.9461),
    ("MENDOZA", "USPALLATA", -32.5936, -69.3458),
    ("MENDOZA", "PORTILLO DE PIUQUENES", -33.613674934669646, -69.49885336487009),
    ("MENDOZA", "PIUQUENES", -33.613674934669646, -69.49885336487009),
    ("MENDOZA", "CAJON DEL MAIPO", -34.190939362717494, -69.70436192023638),
    ("MENDOZA", "LAS DAMAS", -34.884690, -70.291109),
    ("MENDOZA", "VERGARA", -35.1833, -70.5167),
    ("MENDOZA", "PLANCHON", -35.1833, -70.5167),
    ("MENDOZA", "PEHUENCHE", -35.9736, -70.3983),
    ("MENDOZA", "AEROPUERTO MENDOZA", -32.8317, -68.7929),
    ("MENDOZA", "EL PLUMERILLO", -32.8317, -68.7929),

    # --- NEUQUÉN ---
    ("NEUQUEN", "BUTA MALLIN", -37.2167, -71.1333),
    ("NEUQUEN", "PICHACHEN", -37.4525, -71.1269),
    ("NEUQUEN", "COPAHUE", -37.814817101834635, -71.09336827976361),
    ("NEUQUEN", "PINO HACHADO", -38.6631, -70.8964),
    ("NEUQUEN", "ICALMA", -38.8333, -71.2667),
    ("NEUQUEN", "REIGOLIL", -39.1167, -71.4167),
    ("NEUQUEN", "MAMUIL MALAL", -39.5833, -71.4833),
    ("NEUQUEN", "CARIRRINE", -39.8000, -71.6500),
    ("NEUQUEN", "HUA HUM FLUVIAL", -40.1190, -71.6620),
    ("NEUQUEN", "HUA HUM", -40.1167, -71.6667),
    ("NEUQUEN", "CARDENAL ANTONIO SAMORE", -40.7131, -71.9436),
    ("NEUQUEN", "SAMORE", -40.7131, -71.9436),
    ("NEUQUEN", "AEROPUERTO NEUQUEN", -38.9490, -68.1557),
    ("NEUQUEN", "CHAPELCO", -40.0754, -71.1373),

    # --- RÍO NEGRO ---
    ("RIO NEGRO", "VICENTE PEREZ ROSALES", -41.0631, -71.8164),
    ("RIO NEGRO", "PEREZ ROSALES", -41.0631, -71.8164),
    ("RIO NEGRO", "VURILOCHE", -41.232810, -71.770120),
    ("RIO NEGRO", "TRONADOR", -41.232810, -71.770120),
    ("RIO NEGRO", "RIO MANSO", -41.5000, -71.8500),
    ("RIO NEGRO", "PUNTA COLORADA", -41.6983, -65.0078),
    ("RIO NEGRO", "SAN ANTONIO ESTE", -40.8031, -64.8819),
    ("RIO NEGRO", "AEROPUERTO BARILOCHE", -41.1512, -71.1575),

    # --- CHUBUT ---
    ("CHUBUT", "RIO PUELO FLUVIAL", -42.1030, -71.7120),
    ("CHUBUT", "RIO PUELO", -42.1000, -71.7167),
    ("CHUBUT", "FUTALEUFU", -43.1803, -71.7519),
    ("CHUBUT", "RIO ENCUENTRO", -43.5833, -71.7333),
    ("CHUBUT", "LAS PAMPAS", -44.238770, -71.719420),
    ("CHUBUT", "RIO FRIAS", -44.579590, -71.056280),
    ("CHUBUT", "PAMPA ALTA", -45.230420, -71.327320),
    ("CHUBUT", "COYHAIQUE", -45.4833, -71.6000),
    ("CHUBUT", "TRIANA", -45.572530, -71.698310),
    ("CHUBUT", "HUEMULES", -45.9167, -71.6500),
    ("CHUBUT", "PUERTO MADRYN", -42.7611, -65.0319),
    ("CHUBUT", "COMODORO RIVADAVIA", -45.8644, -67.4658),
    ("CHUBUT", "AEROPUERTO TRELEW", -43.2105, -65.2703),

    # --- SANTA CRUZ ---
    ("SANTA CRUZ", "INGENIERO PALLAVICINI", -46.2667, -71.7167),
    ("SANTA CRUZ", "PALLAVICINI", -46.2667, -71.7167),
    ("SANTA CRUZ", "JEINIMENI", -46.5442, -71.6361),
    ("SANTA CRUZ", "ROBALLOS", -47.1500, -71.8500),
    ("SANTA CRUZ", "RIO MAYER", -48.1833, -72.3333),
    ("SANTA CRUZ", "RIBERA NORTE", -48.1833, -72.3333),
    ("SANTA CRUZ", "RIO MOSCO", -48.489390, -72.552780),
    ("SANTA CRUZ", "LAGO SAN MARTIN", -49.126120, -72.491900),
    ("SANTA CRUZ", "O HIGGINS", -49.126120, -72.491900),
    ("SANTA CRUZ", "PORTEZUELO DE LA DIVISORIA", -48.998980, -72.839410),
    ("SANTA CRUZ", "MARCONI", -49.331370, -72.884760),
    ("SANTA CRUZ", "RIO DON GUILLERMO", -51.2500, -72.2500),
    ("SANTA CRUZ", "DON GUILLERMO", -51.2500, -72.2500),
    ("SANTA CRUZ", "DOROTEA", -51.5736, -72.3458),
    ("SANTA CRUZ", "LAURITA", -51.6333, -72.3167),
    ("SANTA CRUZ", "CASAS VIEJAS", -51.6333, -72.3167),
    ("SANTA CRUZ", "INTEGRACION AUSTRAL", -52.139030, -69.519470),
    ("SANTA CRUZ", "MONTE AYMOND", -52.139030, -69.519470),
    ("SANTA CRUZ", "CONDOR II", -52.283480, -68.739340),
    ("SANTA CRUZ", "CONDOR DANIEL POSESION", -52.162630, -69.150710),
    ("SANTA CRUZ", "DANIEL POSESION", -52.162630, -69.150710),
    ("SANTA CRUZ", "CONDOR", -52.162630, -69.150710),
    ("SANTA CRUZ", "CERRO REDONDO", -52.223420, -68.946270),
    ("SANTA CRUZ", "MONTE DINERO", -52.239468, -68.883148),
    ("SANTA CRUZ", "PUERTO DESEADO", -47.7528, -65.8936),
    ("SANTA CRUZ", "SAN JULIAN", -49.3083, -67.7250),
    ("SANTA CRUZ", "PUNTA QUILLA", -50.1167, -68.4167),
    ("SANTA CRUZ", "RIO GALLEGOS", -51.6089, -69.3126),
    ("SANTA CRUZ", "EL CALAFATE", -50.2803, -72.0531),

    # --- TIERRA DEL FUEGO ---
    ("TIERRA DEL FUEGO", "ALFA CULLEN", -52.6833, -68.6000),
    ("TIERRA DEL FUEGO", "CANADON ALFA", -52.7167, -68.6000),
    ("TIERRA DEL FUEGO", "SAN SEBASTIAN", -53.3219, -68.6042),
    ("TIERRA DEL FUEGO", "RIO BELLA VISTA", -53.9833, -68.6000),
    ("TIERRA DEL FUEGO", "RADMAN", -53.9833, -68.6000),
    ("TIERRA DEL FUEGO", "PUERTO ALMANZA", -54.8694, -67.5750),
    ("TIERRA DEL FUEGO", "PUERTO WILLIAMS", -54.8694, -67.5750),
    ("TIERRA DEL FUEGO", "PUERTO USHUAIA", -54.8108, -68.2994),
    ("TIERRA DEL FUEGO", "AEROPUERTO USHUAIA", -54.8433, -68.2958),
    ("TIERRA DEL FUEGO", "RIO GRANDE", -53.7861, -67.6989),

    # --- FORMOSA ---
    ("FORMOSA", "PASO EL REMANSO", -23.987892, -60.458126),
    ("FORMOSA", "EL REMANSO", -23.987892, -60.458126),
    ("FORMOSA", "LA VERDE", -23.987892, -60.458126),
    ("FORMOSA", "PASO LAMADRID", -23.897911, -60.755810),
    ("FORMOSA", "LAMADRID", -23.897911, -60.755810),
    ("FORMOSA", "MISION SAN LEONARDO", -23.897911, -60.755810),
    ("FORMOSA", "ISLETA", -24.212000, -60.197070),
    ("FORMOSA", "ROJAS SILVA", -24.212000, -60.197070),
    ("FORMOSA", "COLONIA GENERAL BELGRANO", -24.748760, -58.833130),
    ("FORMOSA", "GENERAL BELGRANO", -24.748760, -58.833130),
    ("FORMOSA", "GENERAL BRUGUEZ", -24.748760, -58.833130),
    ("FORMOSA", "CLORINDA", -25.2636, -57.7119),
    ("FORMOSA", "SAN IGNACIO DE LOYOLA", -25.2636, -57.7119),
    ("FORMOSA", "JOSE FALCON", -25.2636, -57.7119),
    ("FORMOSA", "PASARELA LA FRATERNIDAD", -25.2861, -57.7056),
    ("FORMOSA", "LA FRATERNIDAD", -25.2861, -57.7056),
    ("FORMOSA", "PUERTO PILCOMAYO", -25.3736, -57.6519),
    ("FORMOSA", "ITA ENRAMADA", -25.3736, -57.6519),
    ("FORMOSA", "PUERTO FORMOSA", -26.1836, -58.1636),
    ("FORMOSA", "ALBERDI", -26.1836, -58.1636),
    ("FORMOSA", "PUERTO VELAZ", -26.6667, -58.3167),
    ("FORMOSA", "PUERTO COLONIA CANO", -26.853231, -58.324608),
    ("FORMOSA", "COLONIA CANO", -26.853231, -58.324608),
    ("FORMOSA", "PUERTO PILAR", -26.853231, -58.324608),
    ("FORMOSA", "AEROPUERTO FORMOSA", -26.2127, -58.2281),

    # --- CHACO ---
    ("CHACO", "PUERTO BERMEJO", -26.9264, -58.5083),
    ("CHACO", "LAS PALMAS", -27.0942, -58.6861),
    ("CHACO", "ISLA DEL CERRITO", -27.2958, -58.6236),
    ("CHACO", "BARRANQUERAS", -27.4861, -58.9319),
    ("CHACO", "AEROPUERTO RESISTENCIA", -27.4500, -59.0561),

    # --- MISIONES ---
    ("MISIONES", "IGUAZU FOZ DE IGUAZU", -25.5883, -54.5617),
    ("MISIONES", "TANCREDO NEVES", -25.5883, -54.5617),
    ("MISIONES", "PUERTO IGUAZU PUERTO MEIRA", -25.5933, -54.5789),
    ("MISIONES", "PUERTO MEIRA", -25.5933, -54.5789),
    ("MISIONES", "TRES FRONTERAS", -25.5950, -54.5810),
    ("MISIONES", "ANDRESITO", -25.6017, -53.9781),
    ("MISIONES", "CAPANEMA", -25.6017, -53.9781),
    ("MISIONES", "INTEGRACION", -25.7706, -53.8522),
    ("MISIONES", "PLANALTO", -25.7706, -53.8522),
    ("MISIONES", "PUERTO LIBERTAD", -25.9647, -54.6039),
    ("MISIONES", "PUERTO WANDA", -25.9730, -54.6045),
    ("MISIONES", "SAN ANTONIO SANTO ANTONIO", -26.0569, -53.7306),
    ("MISIONES", "PUERTO MADO", -26.2303, -54.6294),
    ("MISIONES", "BERNARDO DE IRIGOYEN", -26.2547, -53.6439),
    ("MISIONES", "DIONISIO CERQUEIRA", -26.2547, -53.6439),
    ("MISIONES", "PUERTO VICTORIA", -26.3350, -54.6636),
    ("MISIONES", "PUERTO PINARES", -26.3950, -54.6910),
    ("MISIONES", "PUERTO ELDORADO", -26.4064, -54.6939),
    ("MISIONES", "PUERTO PIRAY", -26.4728, -54.7261),
    ("MISIONES", "MONTECARLO", -26.5594, -54.8039),
    ("MISIONES", "PEPIRI GUAZU", -26.6122, -53.7369),
    ("MISIONES", "PASO ROSALES", -26.6122, -53.7369),
    ("MISIONES", "PUERTO PARANAY", -26.6744, -54.8150),
    ("MISIONES", "PUERTO GARUHAPE", -26.7914, -54.9539),
    ("MISIONES", "PUERTO RICO", -26.7919, -55.0244),
    ("MISIONES", "PUERTO LEONI", -26.9764, -55.1742),
    ("MISIONES", "PUERTO OASIS", -27.0650, -55.2880),
    ("MISIONES", "PUERTO MANI", -27.1436, -55.4481),
    ("MISIONES", "CORPUS", -27.1239, -55.5131),
    ("MISIONES", "EL SOBERBIO", -27.2981, -54.1936),
    ("MISIONES", "SAN IGNACIO", -27.2531, -55.5469),
    ("MISIONES", "SANTA ANA", -27.3264, -55.5950),
    ("MISIONES", "CANDELARIA", -27.4564, -55.7481),
    ("MISIONES", "ALICIA", -27.4639, -54.3442),
    ("MISIONES", "AURORA", -27.4994, -54.5206),
    ("MISIONES", "ALBA POSSE", -27.5714, -54.6792),
    ("MISIONES", "BARRA BONITA", -27.6014, -54.8431),
    ("MISIONES", "PANAMBI", -27.7300, -54.9142),
    ("MISIONES", "POSADAS ENCARNACION", -27.3703, -55.8814),
    ("MISIONES", "SAN ROQUE GONZALEZ", -27.3703, -55.8814),
    ("MISIONES", "PUERTO POSADAS", -27.3622, -55.8961),
    ("MISIONES", "PASO DE LA BARCA", -27.8919, -55.1356),
    ("MISIONES", "PORTO XAVIER", -27.8919, -55.1356),
    ("MISIONES", "PUERTO SANTA MARIA", -27.9678, -55.3508),
    ("MISIONES", "PUERTO SAN ISIDRO", -28.0503, -55.4158),
    ("MISIONES", "AEROPUERTO IGUAZU", -25.7373, -54.4734),
    ("MISIONES", "AEROPUERTO POSADAS", -27.3858, -55.9707),

    # --- CORRIENTES ---
    ("CORRIENTES", "PASO DE LA PATRIA", -27.3144, -58.5742),
    ("CORRIENTES", "ITATI", -27.2694, -58.2450),
    ("CORRIENTES", "YAHAPE", -27.3736, -57.6519),
    ("CORRIENTES", "ITA IBATE", -27.4281, -57.3389),
    ("CORRIENTES", "YACYRETA", -27.4833, -56.7333),
    ("CORRIENTES", "ITUZAINGO", -27.5819, -56.6881),
    ("CORRIENTES", "PUERTO CORRIENTES", -27.4631, -58.8364),
    ("CORRIENTES", "GARRUCHOS", -28.1772, -55.6425),
    ("CORRIENTES", "SANTO TOME", -28.5906, -56.0317),
    ("CORRIENTES", "SAO BORJA", -28.5906, -56.0317),
    ("CORRIENTES", "ALVEAR", -29.1128, -56.5556),
    ("CORRIENTES", "PUERTO LA CRUZ", -29.1783, -56.6336),
    ("CORRIENTES", "YAPEYU", -29.4700, -56.8111),
    ("CORRIENTES", "PASO DE LOS LIBRES", -29.7338, -57.1019),
    ("CORRIENTES", "URUGUAYANA", -29.7338, -57.1019),
    ("CORRIENTES", "MONTE CASEROS", -30.2519, -57.6222),
    ("CORRIENTES", "BELLA UNION", -30.2519, -57.6222),

    # --- ENTRE RÍOS ---
    ("ENTRE RIOS", "SANTA ELOISA", -30.761606, -57.822300),
    ("ENTRE RIOS", "PUERTO FEDERACION", -30.9931, -57.9153),
    ("ENTRE RIOS", "PUERTO LUIS", -31.209789, -57.930817),
    ("ENTRE RIOS", "CONCORDIA SALTO", -31.2608, -57.9617),
    ("ENTRE RIOS", "SALTO GRANDE", -31.2608, -57.9617),
    ("ENTRE RIOS", "PUERTO CONCORDIA", -31.4022, -58.0036),
    ("ENTRE RIOS", "PUERTO COLON", -32.2156, -58.1367),
    ("ENTRE RIOS", "COLON PAYSANDU", -32.2656, -58.0939),
    ("ENTRE RIOS", "GENERAL ARTIGAS", -32.2656, -58.0939),
    ("ENTRE RIOS", "CONCEPCION DEL URUGUAY", -32.4833, -58.2222),
    ("ENTRE RIOS", "GUALEGUAYCHU FRAY BENTOS", -33.1114, -58.2464),
    ("ENTRE RIOS", "LIBERTADOR GENERAL SAN MARTIN", -33.1114, -58.2464),
    ("ENTRE RIOS", "PUERTO BOCA GUALEGUAYCHU", -33.0739, -58.4206),
    ("ENTRE RIOS", "PARANACITO", -33.7161, -58.6594),
    ("ENTRE RIOS", "GUAZU GUAZUCITO", -34.0147, -58.3956),
    ("ENTRE RIOS", "IBICUY", -33.7481, -59.1764),
    ("ENTRE RIOS", "DIAMANTE", -32.0631, -60.6464),
    ("ENTRE RIOS", "PUERTO PARANA", -31.7092, -60.5736),
    ("ENTRE RIOS", "PUERTO LA PAZ", -30.6672, -59.6403),

    # --- SANTA FE ---
    ("SANTA FE", "RECONQUISTA", -29.2331, -59.5881),
    ("SANTA FE", "PUERTO SANTA FE", -31.6481, -60.7014),
    ("SANTA FE", "SAN LORENZO", -32.7481, -60.7281),
    ("SANTA FE", "PUERTO GENERAL SAN MARTIN", -32.7131, -60.7231),
    ("SANTA FE", "PUERTO ROSARIO", -32.9481, -60.6314),
    ("SANTA FE", "VILLA CONSTITUCION", -33.2281, -60.3281),
    ("SANTA FE", "AEROPUERTO ROSARIO", -32.9036, -60.7850),

    # --- BUENOS AIRES Y CABA ---
    ("BUENOS AIRES", "SAN NICOLAS", -33.3314, -60.2131),
    ("BUENOS AIRES", "RAMALLO", -33.4831, -60.0014),
    ("BUENOS AIRES", "SAN PEDRO", -33.6781, -59.6581),
    ("BUENOS AIRES", "BARADERO", -33.8031, -59.5014),
    ("BUENOS AIRES", "ZARATE", -34.0931, -59.0181),
    ("BUENOS AIRES", "CAMPANA", -34.1581, -58.9581),
    ("BUENOS AIRES", "ESCOBAR", -34.3431, -58.7881),
    ("BUENOS AIRES", "TIGRE", -34.4214, -58.5814),
    ("BUENOS AIRES", "SAN FERNANDO", -34.4532, -58.5896),
    ("BUENOS AIRES", "SAN ISIDRO", -34.4681, -58.5081),
    ("BUENOS AIRES", "OLIVOS", -34.5081, -58.4781),
    ("BUENOS AIRES", "DOCK SUD", -34.6481, -58.3481),
    ("BUENOS AIRES", "LA PLATA", -34.8581, -57.8814),
    ("BUENOS AIRES", "MARTIN GARCIA", -34.1889, -58.2553),
    ("BUENOS AIRES", "EZEIZA", -34.8222, -58.5358),
    ("BUENOS AIRES", "MINISTRO PISTARINI", -34.8222, -58.5358),
    ("BUENOS AIRES", "MAR DEL PLATA", -38.0381, -57.5314),
    ("BUENOS AIRES", "QUEQUEN", -38.5781, -58.7031),
    ("BUENOS AIRES", "BAHIA BLANCA", -38.7881, -62.2681),
    ("CIUDAD AUTONOMA DE BUENOS AIRES", "BUQUEBUS", -34.5997, -58.3689),
    ("CIUDAD AUTONOMA DE BUENOS AIRES", "DARSENA NORTE", -34.5997, -58.3689),
    ("CIUDAD AUTONOMA DE BUENOS AIRES", "PUERTO BUENOS AIRES", -34.5881, -58.3714),
    ("CIUDAD AUTONOMA DE BUENOS AIRES", "AEROPARQUE", -34.5592, -58.4156),
    ("CIUDAD AUTONOMA DE BUENOS AIRES", "JORGE NEWBERY", -34.5592, -58.4156),
]

CATALOGO_ORDENADO = sorted(CATALOGO_AUDITADO, key=lambda x: len(x[1]), reverse=True)


def supervisar_coordenada(
    nombre: str,
    provincia: str,
    lat_actual=None,
    lon_actual=None,
    registro_ocupados=None,
):
    nom_norm = normalizar_texto(nombre)
    prov_norm = normalizar_provincia(provincia, nombre)

    lat_res, lon_res = None, None

    if prov_norm:
        for cat_prov, cat_clave, cat_lat, cat_lon in CATALOGO_ORDENADO:
            if cat_prov == prov_norm and re.search(rf"\b{re.escape(cat_clave)}\b", nom_norm):
                lat_res, lon_res = cat_lat, cat_lon
                break

    if lat_res is None:
        try:
            lat_f = float(lat_actual) if lat_actual not in (None, "", "None") else None
            lon_f = float(lon_actual) if lon_actual not in (None, "", "None") else None
        except (ValueError, TypeError):
            lat_f, lon_f = None, None

        if lat_f is not None and lon_f is not None:
            if coordenada_valida_en_provincia(lat_f, lon_f, prov_norm):
                lat_res, lon_res = lat_f, lon_f

    if lat_res is None and not prov_norm:
        for _, cat_clave, cat_lat, cat_lon in CATALOGO_ORDENADO:
            if re.search(rf"\b{re.escape(cat_clave)}\b", nom_norm):
                lat_res, lon_res = cat_lat, cat_lon
                break

    if lat_res is None:
        perfil = PERFILES_PROVINCIALES.get(prov_norm)
        if perfil:
            lat_res, lon_res = perfil["fallback"]
        else:
            lat_res, lon_res = -34.6037, -58.3816

    if registro_ocupados is not None:
        clave_pos = (round(lat_res, 4), round(lon_res, 4))
        count = registro_ocupados.get(clave_pos, 0)
        registro_ocupados[clave_pos] = count + 1
        if count > 0:
            angulo = (count * 137.5) * (math.pi / 180.0)
            radio = 0.0016 * math.ceil(count / 4)
            lat_res += math.sin(angulo) * radio
            lon_res += math.cos(angulo) * radio

    return round(lat_res, 6), round(lon_res, 6)


# ==============================================================================
# EXTRACCIÓN ORIGINAL DESDE MINSEG (CON HEADERS AJAX X-Requested-With)
# ==============================================================================
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


def clasificar_medio(texto_completo, url_detalle="", tipo_raw=""):
    txt = f"{texto_completo} {url_detalle} {tipo_raw}".lower()

    if (
        "/rio/" in txt
        or any(
            k in txt
            for k in [
                "fluvial",
                "balsa",
                "lancha",
                "barcaza",
                "ferry",
                "catamarán",
                "catamaran",
                "embarcación",
                "embarcacion",
                "puerto ",
            ]
        )
    ):
        return "Fluvial"

    if any(k in txt for k in ["ferroviario", "ferrocarril", "tren"]):
        return "Ferroviario"

    if re.search(r"cruce de veh[íi]culos[:\s]+no\b", txt) and re.search(
        r"cruce de personas[:\s]+s[íi]\b", txt
    ):
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
            nombre_final = (
                nombre_actual
                if nombre_actual and not nombre_actual.startswith("Paso ")
                else (nombre_ficha or nombre_actual)
            )
            return id_paso, nombre_final, medio
    except requests.RequestException:
        pass

    return id_paso, nombre_actual, clasificar_medio(nombre_actual, url_relativa, tipo_raw)


def cargar_csv(ruta, clave):
    if not os.path.exists(ruta):
        return {}
    with open(ruta, mode="r", encoding="utf-8-sig", newline="") as f:
        return {row[clave]: row for row in csv.DictReader(f) if clave in row and row[clave]}


def guardar_csv(ruta, filas, columnas):
    with open(ruta, mode="w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columnas, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(filas)


def normalizar_estado(estado_raw):
    texto_limpio = limpiar_html(estado_raw)
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

    if re.search(
        r"\b("
        r"CERRADO|CERRADA|"
        r"INHABILITADO|INHABILITADA|DESHABILITADO|DESHABILITADA|SIN HABILITAR|"
        r"NO\s+(?:SE\s+ENCUENTRA\s+|ESTA\s+)?(?:HABILITADO|HABILITADA|ABIERTO|ABIERTA)|"
        r"(?:HABILITADO|HABILITADA|ABIERTO|ABIERTA)\s+NO|"
        r"SUSPENDIDO|SUSPENDIDA|CLAUSURADO|CLAUSURADA|INTRANSITABLE|CORTADO|CORTADA|INTERRUMPIDO|INTERRUMPIDA"
        r")\b",
        estado,
    ):
        return "CERRADO"

    if re.search(r"\b(ABIERTO|ABIERTA|HABILITADO|HABILITADA)\b", estado):
        return "ABIERTO"

    return "SIN_DATO"


def ejecutar_extraccion():
    resp = requests.get(LISTADO_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    pasos = data if isinstance(data, list) else data.get("data") or data.get("pasos") or []
    print(f"[{HOY}] Registros recibidos desde MinSeg: {len(pasos)}")

    maestro_raw = cargar_csv(ARCHIVO_MAESTRO, "id_paso")
    maestro = {
        k: v
        for k, v in maestro_raw.items()
        if not es_paso_excluido(v.get("nombre_paso") or v.get("Nombre del Paso") or "")
    }

    historico = {}
    if os.path.exists(ARCHIVO_HISTORICO):
        with open(ARCHIVO_HISTORICO, mode="r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                fecha_reg = row.get("fecha") or row.get("Fecha") or ""
                id_reg = row.get("id_paso") or ""
                nom_reg = row.get("nombre_paso") or row.get("Nombre del Paso") or ""
                if not fecha_reg or not id_reg or es_paso_excluido(nom_reg):
                    continue
                anio_reg = fecha_reg[:4] if len(fecha_reg) >= 7 else ANIO_ACTUAL
                mes_num = fecha_reg[5:7] if len(fecha_reg) >= 7 else MES_ACTUAL
                historico[(fecha_reg, id_reg)] = {
                    "fecha": fecha_reg,
                    "anio": anio_reg,
                    "mes": MESES_ES.get(mes_num, mes_num),
                    "id_paso": id_reg,
                    "nombre_paso": nom_reg,
                    "provincia": normalizar_provincia(row.get("provincia") or row.get("Provincia") or "", nom_reg),
                    "pais": row.get("pais") or row.get("País Limítrofe") or "",
                    "medio": row.get("medio") or row.get("Medio") or "Vial",
                    "estado": row.get("estado") or row.get("Estado") or "SIN_DATO",
                    "Latitud": row.get("Latitud") or row.get("latitud") or "",
                    "Longitud": row.get("Longitud") or row.get("longitud") or "",
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

        if es_paso_excluido(nombre):
            continue

        provincia_raw = limpiar_html(
            extraer_campo(p, ["provincia", "provincia_nombre", "nombre_provincia", "state"])
        )
        pais = limpiar_html(
            extraer_campo(p, ["pais", "pais_limitrofe", "pais_nombre", "nombre_pais", "country"])
        )
        estados_candidatos = [
            extraer_campo(p, [clave])
            for clave in ("estado_prioridad", "estado", "estado_paso", "status", "situacion", "habilitado")
        ]
        estado_raw = " | ".join(val for val in estados_candidatos if val)
        lat_raw = extraer_campo(p, ["latitud", "lat"])
        lon_raw = extraer_campo(p, ["longitud", "lon", "lng"])

        previo = maestro.get(id_paso, {})
        nom_final = nombre or previo.get("nombre_paso", "")
        prov_final = normalizar_provincia(provincia_raw or previo.get("provincia", ""), nom_final)

        datos_base[id_paso] = {
            "nombre": nom_final,
            "provincia": prov_final,
            "pais": pais or previo.get("pais", ""),
            "estado": normalizar_estado(estado_raw),
            "lat_raw": lat_raw or previo.get("Latitud", ""),
            "lon_raw": lon_raw or previo.get("Longitud", ""),
        }

        if (
            previo.get("verificado") != "SI"
            or not previo.get("medio")
            or previo.get("nombre_paso", "").startswith("Paso ")
        ):
            tareas_detalle.append((id_paso, url_detalle, datos_base[id_paso]["nombre"], tipo_raw))
        else:
            datos_base[id_paso]["medio"] = previo["medio"]

    if tareas_detalle:
        print(f"Verificando ficha individual de {len(tareas_detalle)} pasos...")
        with ThreadPoolExecutor(max_workers=6) as executor:
            for id_p, nombre_ficha, medio_ficha in executor.map(
                consultar_ficha_detalle, tareas_detalle
            ):
                if nombre_ficha:
                    datos_base[id_p]["nombre"] = nombre_ficha
                    datos_base[id_p]["provincia"] = normalizar_provincia(
                        datos_base[id_p]["provincia"], nombre_ficha
                    )
                datos_base[id_p]["medio"] = medio_ficha

    datos_base = {
        id_p: info
        for id_p, info in datos_base.items()
        if not es_paso_excluido(info["nombre"])
    }

    ocupados_hoy = {}
    for id_p, info in datos_base.items():
        lat, lon = supervisar_coordenada(
            nombre=info["nombre"],
            provincia=info["provincia"],
            lat_actual=info.get("lat_raw"),
            lon_actual=info.get("lon_raw"),
            registro_ocupados=ocupados_hoy,
        )
        lat_s, lon_s = f"{lat:.6f}", f"{lon:.6f}"

        maestro[id_p] = {
            "id_paso": id_p,
            "nombre_paso": info["nombre"],
            "provincia": info["provincia"],
            "pais": info["pais"],
            "medio": info.get("medio", "Vial"),
            "verificado": "SI",
            "Latitud": lat_s,
            "Longitud": lon_s,
        }
        historico[(HOY, id_p)] = {
            "fecha": HOY,
            "anio": ANIO_ACTUAL,
            "mes": MESES_ES.get(MES_ACTUAL, MES_ACTUAL),
            "id_paso": id_p,
            "nombre_paso": info["nombre"],
            "provincia": info["provincia"],
            "pais": info["pais"],
            "medio": info.get("medio", "Vial"),
            "estado": info["estado"],
            "Latitud": lat_s,
            "Longitud": lon_s,
        }

    ocupados_m = {}
    for id_p, info in maestro.items():
        info["provincia"] = normalizar_provincia(info.get("provincia", ""), info.get("nombre_paso", ""))
        lat, lon = supervisar_coordenada(
            nombre=info.get("nombre_paso", ""),
            provincia=info["provincia"],
            lat_actual=info.get("Latitud"),
            lon_actual=info.get("Longitud"),
            registro_ocupados=ocupados_m,
        )
        info["Latitud"] = f"{lat:.6f}"
        info["Longitud"] = f"{lon:.6f}"

    for reg in historico.values():
        id_p = reg["id_paso"]
        if id_p in maestro:
            reg["nombre_paso"] = maestro[id_p]["nombre_paso"]
            reg["provincia"] = maestro[id_p]["provincia"]
            reg["pais"] = maestro[id_p]["pais"]
            reg["medio"] = maestro[id_p]["medio"]
            reg["Latitud"] = maestro[id_p]["Latitud"]
            reg["Longitud"] = maestro[id_p]["Longitud"]

    guardar_csv(
        ARCHIVO_MAESTRO,
        list(maestro.values()),
        ["id_paso", "nombre_paso", "provincia", "pais", "medio", "verificado", "Latitud", "Longitud"],
    )

    filas_hist = sorted(historico.values(), key=lambda x: (x["fecha"], x.get("nombre_paso", "")))
    guardar_csv(
        ARCHIVO_HISTORICO,
        filas_hist,
        ["fecha", "anio", "mes", "id_paso", "nombre_paso", "provincia", "pais", "medio", "estado", "Latitud", "Longitud"],
    )

    periodos = sorted({(reg["anio"], reg["mes"]) for reg in historico.values()})
    if not periodos:
        periodos = [(ANIO_ACTUAL, MESES_ES.get(MES_ACTUAL, MES_ACTUAL))]

    conteos = {
        (anio, mes, id_p): {"ABIERTO": 0, "CERRADO": 0, "SIN_DATO": 0}
        for anio, mes in periodos
        for id_p in maestro
    }

    for reg in historico.values():
        clave = (reg["anio"], reg["mes"], reg["id_paso"])
        if clave in conteos and reg["estado"] in conteos[clave]:
            conteos[clave][reg["estado"]] += 1

    filas_consolidado = []
    for anio, mes in periodos:
        for id_p, info in maestro.items():
            c = conteos[(anio, mes, id_p)]
            filas_consolidado.append({
                "Año": anio,
                "Mes": mes,
                "Nombre del Paso": info["nombre_paso"],
                "Provincia": info["provincia"],
                "País Limítrofe": info["pais"],
                "Medio": info["medio"],
                "Días Abierto": c["ABIERTO"],
                "Días Cerrado": c["CERRADO"],
                "Días Sin Dato": c["SIN_DATO"],
                "Latitud": info["Latitud"],
                "Longitud": info["Longitud"],
            })

    filas_consolidado.sort(
        key=lambda x: (x["Año"], x["Mes"], x["País Limítrofe"], x["Provincia"], x["Nombre del Paso"])
    )
    guardar_csv(
        ARCHIVO_CONSOLIDADO,
        filas_consolidado,
        [
            "Año",
            "Mes",
            "Nombre del Paso",
            "Provincia",
            "País Limítrofe",
            "Medio",
            "Días Abierto",
            "Días Cerrado",
            "Días Sin Dato",
            "Latitud",
            "Longitud",
        ],
    )
    print(f"[{HOY}] Consolidado actualizado con Año y Mes ({len(filas_consolidado)} filas).")


if __name__ == "__main__":
    ejecutar_extraccion()
