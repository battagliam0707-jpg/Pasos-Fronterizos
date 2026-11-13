#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETL Diario de Pasos Fronterizos Internacionales — República Argentina
---------------------------------------------------------------------
1. Consulta el listado oficial del Ministerio de Seguridad de la Nación
   (argentina.gob.ar/seguridad/pasosinternacionales y /listado) y contrasta
   con la capa geoespacial oficial del IGN cuando está disponible.
2. Supervisa y corrige la georreferenciación (Latitud / Longitud WGS84)
   aplicando envolventes provinciales estrictas (evitando cruces entre
   provincias como Formosa/Entre Ríos), excluyendo los 17 pasos marcados
   como "NO" y aplicando las 28 coordenadas auditadas.
3. Actualiza, acumula diariamente y re-georreferencia los archivos:
   - maestro_pasos.csv
   - historico_diario_2026.csv
   - consolidado_pasos_2026.csv
"""

import csv
import html as html_lib
import io
import json
import math
import os
import re
import ssl
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# ==============================================================================
# CONFIGURACIÓN GENERAL Y FUENTES OFICIALES
# ==============================================================================
URL_MINSEG_MAPA = "https://www.argentina.gob.ar/seguridad/pasosinternacionales"
URL_MINSEG_LISTADO = "https://www.argentina.gob.ar/seguridad/pasosinternacionales/listado"
URL_IGN_WFS = (
    "https://wms.ign.gob.ar/geoserver/ign/ows"
    "?service=WFS&version=1.0.0&request=GetFeature"
    "&typeName=ign:pasos_de_fronteras_internacionales"
    "&outputFormat=application/json"
)

ARCHIVO_MAESTRO = "maestro_pasos.csv"
ARCHIVO_HISTORICO = "historico_diario_2026.csv"
ARCHIVO_CONSOLIDADO = "consolidado_pasos_2026.csv"

TZ_ARGENTINA = timezone(timedelta(hours=-3))


# ==============================================================================
# UTILIDADES DE NORMALIZACIÓN Y VALIDACIÓN ESPACIAL
# ==============================================================================
def normalizar_texto(texto: str) -> str:
    """Convierte texto a mayúsculas sin tildes, signos ni espacios duplicados."""
    if not texto:
        return ""
    s = unicodedata.normalize("NFD", str(texto))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^A-Z0-9\s]", " ", s.upper())
    return re.sub(r"\s+", " ", s).strip()


# 17 pasos marcados como "NO" en el Excel de auditoría (no reportados por MinSeg)
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
    """Devuelve True si el paso fue marcado como NO en la auditoría."""
    n = normalizar_texto(nombre)
    if not n:
        return True
    return any(ex in n for ex in PASOS_EXCLUIDOS)


def normalizar_provincia(prov: str, nombre_paso: str = "") -> str:
    """Estandariza nombres de provincias argentinas y aplica asignaciones forzadas."""
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


# Envolventes geográficas estrictas [lat_min, lat_max, lon_min, lon_max]
# Impiden que un paso de Formosa termine en Entre Ríos o viceversa.
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
    """Verifica que la coordenada sea numérica, esté en Argentina y dentro de la provincia."""
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
# CATÁLOGO AUDITADO DE PASOS INTERNACIONALES (RES. 761/2013, DNM 2083/2011, IGN)
# Estructura: (PROVINCIA_NORM, CLAVE_NORMALIZADA, LATITUD, LONGITUD)
# Incluye las 28 correcciones del Excel de auditoría + Puerto Colonia Cano
# ==============================================================================
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
    ("SANTA CRUZ", "INTEGRACION AUSTRAL", -52.1431, -69.5186),
    ("SANTA CRUZ", "MONTE AYMOND", -52.1431, -69.5186),
    ("SANTA CRUZ", "CONDOR II", -52.1720, -69.2400),
    ("SANTA CRUZ", "CONDOR", -52.1667, -69.2500),
    ("SANTA CRUZ", "CERRO REDONDO", -52.1833, -69.0833),
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
    mapa_ign=None,
    registro_ocupados=None,
):
    """Supervisa y devuelve (lat, lon) verificados para un paso fronterizo."""
    nom_norm = normalizar_texto(nombre)
    prov_norm = normalizar_provincia(provincia, nombre)

    lat_res, lon_res = None, None

    # 1. Búsqueda estricta dentro de la provincia declarada
    if prov_norm:
        for cat_prov, cat_clave, cat_lat, cat_lon in CATALOGO_ORDENADO:
            if cat_prov == prov_norm and cat_clave in nom_norm:
                lat_res, lon_res = cat_lat, cat_lon
                break

    # 2. Si no hubo match provincial, intentar con coordenada oficial/IGN validando bbox
    if lat_res is None:
        try:
            lat_f = float(lat_actual) if lat_actual not in (None, "", "None") else None
            lon_f = float(lon_actual) if lon_actual not in (None, "", "None") else None
        except (ValueError, TypeError):
            lat_f, lon_f = None, None

        if lat_f is not None and lon_f is not None:
            if coordenada_valida_en_provincia(lat_f, lon_f, prov_norm):
                lat_res, lon_res = lat_f, lon_f

    if lat_res is None and mapa_ign:
        for ign_nom, (ign_lat, ign_lon) in mapa_ign.items():
            if (nom_norm in ign_nom or ign_nom in nom_norm) and coordenada_valida_en_provincia(
                ign_lat, ign_lon, prov_norm
            ):
                lat_res, lon_res = ign_lat, ign_lon
                break

    # 3. Si la provincia venía vacía en algún registro histórico, buscar coincidencia única
    if lat_res is None and not prov_norm:
        for _, cat_clave, cat_lat, cat_lon in CATALOGO_ORDENADO:
            if cat_clave in nom_norm:
                lat_res, lon_res = cat_lat, cat_lon
                break

    # 4. Respaldo provincial garantizado dentro de los límites reales de la provincia
    if lat_res is None:
        perfil = PERFILES_PROVINCIALES.get(prov_norm)
        if perfil:
            lat_res, lon_res = perfil["fallback"]
        else:
            lat_res, lon_res = -34.6037, -58.3816

    # 5. Separación determinística mínima si otro paso ocupa el mismo punto exacto
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
# DESCARGA DE DATOS OFICIALES (MINSEG E IGN)
# ==============================================================================
def fetch_text(url: str, timeout: int = 30, accept: str = "*/*") -> str:
    """Realiza petición HTTP GET tolerante a certificados y devuelve texto."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ObservatorioPasos/2.0",
            "Accept": accept,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_json(url: str, timeout: int = 25):
    """Realiza petición HTTP GET y decodifica JSON."""
    raw = fetch_text(url, timeout=timeout, accept="application/json, text/plain, */*")
    return json.loads(raw)


def cargar_capa_ign() -> dict:
    """Obtiene puntos oficiales WGS84 del Instituto Geográfico Nacional (WFS)."""
    mapa = {}
    try:
        data = fetch_json(URL_IGN_WFS, timeout=20)
        for feat in data.get("features", []):
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates") or []
            props = feat.get("properties") or {}
            nombre = props.get("nombre") or props.get("fna") or props.get("nam") or ""
            if len(coords) >= 2 and nombre:
                lon, lat = float(coords[0]), float(coords[1])
                mapa[normalizar_texto(nombre)] = (lat, lon)
    except Exception as exc:
        print(f"[INFO] Capa WFS IGN no disponible en esta corrida ({exc}). Se usa catálogo auditado.")
    return mapa


def normalizar_estado(raw_estado: str) -> str:
    """Unifica el estado operativo del paso (ABIERTO / CERRADO)."""
    est = normalizar_texto(raw_estado)
    if not est:
        return "SIN DATO"
    if "CERRADO" in est or "INHABILITADO" in est or "SUSPENDIDO" in est or "CLAUSURADO" in est:
        return "CERRADO"
    if "ABIERTO" in est or "HABILITADO" in est or "ACTIVO" in est or "OPERATIVO" in est:
        return "ABIERTO"
    return est


def limpiar_html(texto: str) -> str:
    if not texto:
        return ""
    sin_tags = re.sub(r"<[^>]+>", " ", str(texto))
    return re.sub(r"\s+", " ", html_lib.unescape(sin_tags)).strip()


def _extraer_items_desde_objeto(obj, acumulador: list):
    """Recorre estructuras JSON/GeoJSON/Poncho de Drupal buscando pasos."""
    if isinstance(obj, list):
        for el in obj:
            _extraer_items_desde_objeto(el, acumulador)
    elif isinstance(obj, dict):
        props = obj.get("properties") if isinstance(obj.get("properties"), dict) else obj
        geom = obj.get("geometry") if isinstance(obj.get("geometry"), dict) else {}
        coords = geom.get("coordinates") if isinstance(geom.get("coordinates"), list) else []

        nombre = (
            props.get("nombre")
            or props.get("paso")
            or props.get("title")
            or props.get("name")
            or props.get("denominacion")
            or ""
        )
        if isinstance(nombre, str) and nombre.strip():
            desc = limpiar_html(props.get("description") or props.get("observaciones") or "")
            prov = str(props.get("provincia") or props.get("provincia_nombre") or props.get("jurisdiccion") or "").strip()
            pais = str(props.get("pais_limitrofe") or props.get("pais") or props.get("pais_vecino") or "").strip()
            medio = str(props.get("medio") or props.get("tipo") or props.get("via") or "").strip()
            est = str(props.get("estado_prioridad") or props.get("estado") or props.get("situacion") or "").strip()
            hor = str(props.get("horario_seguridad") or props.get("horario") or "").strip()

            # Detectar patrón Poncho: "Puerto Almanza - Puerto Williams, TIERRA DEL FUEGO — Cerrado"
            m_titulo = re.match(
                r"^(.+?),\s*([A-ZÁÉÍÓÚÑ\s]+?)\s*[—\-–]\s*(Abierto|Cerrado|Habilitado|Inhabilitado.*)$",
                limpiar_html(nombre),
                re.I,
            )
            if m_titulo:
                nombre = m_titulo.group(1).strip()
                if not prov:
                    prov = m_titulo.group(2).strip()
                if not est:
                    est = m_titulo.group(3).strip()

            if not est and desc:
                m_est = re.search(r"Estado\s*:?\s*(Abierto|Cerrado|Habilitado|Inhabilitado)", desc, re.I)
                if m_est:
                    est = m_est.group(1)
            if not prov and desc:
                m_prov = re.search(r"Provincia\s*:?\s*([A-Za-zÁÉÍÓÚÑáéíóúñ\s]+)", desc, re.I)
                if m_prov:
                    prov = m_prov.group(1).strip()
            if not pais and desc:
                m_pais = re.search(r"Pa[íi]s(?:\s+lim[íi]trofe)?\s*:?\s*(Bolivia|Brasil|Chile|Paraguay|Uruguay)", desc, re.I)
                if m_pais:
                    pais = m_pais.group(1).strip()

            lat = props.get("latitud") or props.get("lat")
            lon = props.get("longitud") or props.get("lon") or props.get("lng")
            if (lat is None or lon is None) and len(coords) >= 2:
                lon, lat = coords[0], coords[1]

            if est or lat is not None or prov:
                acumulador.append({
                    "nombre": limpiar_html(nombre),
                    "provincia": prov,
                    "pais": pais,
                    "medio": medio or "Vial",
                    "estado": est,
                    "lat": lat,
                    "lon": lon,
                    "horario": hor,
                    "observaciones": desc,
                })

        for v in obj.values():
            if isinstance(v, (dict, list)):
                _extraer_items_desde_objeto(v, acumulador)


def obtener_pasos_minseg(mapa_ign: dict) -> list:
    """Descarga y normaliza el listado oficial de pasos internacionales (JSON, Drupal Settings o HTML)."""
    crudos = []

    # Intento 1: Endpoints JSON directos
    for url_json in (URL_MINSEG_LISTADO, f"{URL_MINSEG_LISTADO}?_format=json"):
        try:
            payload = fetch_json(url_json, timeout=25)
            _extraer_items_desde_objeto(payload, crudos)
            if crudos:
                break
        except Exception:
            pass

    # Intento 2: HTML de /listado (tabla HTML o drupal-settings-json)
    if not crudos:
        try:
            html_listado = fetch_text(URL_MINSEG_LISTADO, timeout=30, accept="text/html,*/*")
            for m_js in re.finditer(
                r'<script[^>]*data-drupal-selector="drupal-settings-json"[^>]*>(.*?)</script>',
                html_listado,
                re.S,
            ):
                try:
                    _extraer_items_desde_objeto(json.loads(m_js.group(1)), crudos)
                except Exception:
                    pass
            if not crudos:
                for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html_listado, re.S):
                    celdas = [limpiar_html(td) for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
                    if len(celdas) >= 3 and celdas[0]:
                        crudos.append({
                            "nombre": celdas[0],
                            "provincia": celdas[1] if len(celdas) > 1 else "",
                            "pais": celdas[2] if len(celdas) > 2 else "",
                            "estado": celdas[3] if len(celdas) > 3 else "",
                            "medio": celdas[4] if len(celdas) > 4 else "Vial",
                            "horario": celdas[5] if len(celdas) > 5 else "",
                            "observaciones": celdas[6] if len(celdas) > 6 else "",
                        })
        except Exception as exc:
            print(f"[INFO] Lectura HTML /listado: {exc}")

    # Intento 3: Página principal del mapa Poncho (/pasosinternacionales) + Google Sheets embebido
    if not crudos:
        try:
            html_mapa = fetch_text(URL_MINSEG_MAPA, timeout=30, accept="text/html,*/*")
            for m_js in re.finditer(
                r'<script[^>]*data-drupal-selector="drupal-settings-json"[^>]*>(.*?)</script>',
                html_mapa,
                re.S,
            ):
                try:
                    _extraer_items_desde_objeto(json.loads(m_js.group(1)), crudos)
                except Exception:
                    pass
            if not crudos:
                for sid in dict.fromkeys(re.findall(r"spreadsheets/d/([a-zA-Z0-9-_]{20,})", html_mapa)):
                    try:
                        csv_txt = fetch_text(f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv", timeout=25)
                        for r in csv.DictReader(io.StringIO(csv_txt)):
                            nom = r.get("nombre") or r.get("title") or r.get("Nombre") or ""
                            if nom:
                                crudos.append({
                                    "nombre": nom,
                                    "provincia": r.get("provincia") or r.get("Provincia") or "",
                                    "pais": r.get("pais") or r.get("País") or "",
                                    "estado": r.get("estado") or r.get("Estado") or "",
                                    "medio": r.get("medio") or r.get("tipo") or "Vial",
                                    "lat": r.get("lat") or r.get("latitud"),
                                    "lon": r.get("lon") or r.get("lng") or r.get("longitud"),
                                    "horario": r.get("horario") or "",
                                    "observaciones": r.get("observaciones") or "",
                                })
                    except Exception:
                        pass
        except Exception as exc:
            print(f"[WARN] No se pudo descargar listado en vivo de MinSeg: {exc}")

    registros = []
    ocupados = {}
    vistos = set()

    for item in crudos:
        nombre = str(item.get("nombre") or "").strip()
        if not nombre or es_paso_excluido(nombre):
            continue

        prov_raw = str(item.get("provincia") or "").strip()
        prov_norm = normalizar_provincia(prov_raw, nombre)
        provincia_final = prov_norm if prov_norm else prov_raw

        clave_unica = (normalizar_texto(nombre), provincia_final)
        if clave_unica in vistos:
            continue
        vistos.add(clave_unica)

        estado = normalizar_estado(str(item.get("estado") or ""))
        lat, lon = supervisar_coordenada(
            nombre=nombre,
            provincia=provincia_final,
            lat_actual=item.get("lat"),
            lon_actual=item.get("lon"),
            mapa_ign=mapa_ign,
            registro_ocupados=ocupados,
        )

        registros.append(
            {
                "Nombre del Paso": nombre,
                "Provincia": provincia_final,
                "País Limítrofe": str(item.get("pais") or "").strip(),
                "Medio": str(item.get("medio") or "Vial").strip(),
                "Estado": estado,
                "Latitud": f"{lat:.6f}",
                "Longitud": f"{lon:.6f}",
                "Horario": str(item.get("horario") or "").strip(),
                "Observaciones": str(item.get("observaciones") or "").strip(),
            }
        )

    return registros


# ==============================================================================
# LECTURA, RE-GEORREFERENCIACIÓN Y ESCRITURA DE CSVs
# ==============================================================================
def leer_csv_existente(ruta: str):
    """Lee un CSV existente devolviendo (headers, filas_dict)."""
    if not os.path.exists(ruta):
        return [], []
    with open(ruta, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)
    return headers, rows


def detectar_columnas_clave(headers: list) -> dict:
    """Identifica los nombres reales de columnas en archivos existentes del repo."""
    mapping = {}
    for h in headers:
        hn = normalizar_texto(h)
        if hn in ("NOMBRE DEL PASO", "PASO", "NOMBRE", "DENOMINACION"):
            mapping["nombre"] = h
        elif hn in ("PROVINCIA", "JURISDICCION"):
            mapping["provincia"] = h
        elif hn in ("PAIS LIMITROFE", "PAIS", "PAIS VECINO"):
            mapping["pais"] = h
        elif hn in ("MEDIO", "TIPO", "VIA", "MODALIDAD"):
            mapping["medio"] = h
        elif hn in ("ESTADO", "SITUACION"):
            mapping["estado"] = h
        elif hn in ("LATITUD", "LAT"):
            mapping["lat"] = h
        elif hn in ("LONGITUD", "LON", "LNG"):
            mapping["lon"] = h
        elif hn in ("FECHA", "DATE", "DIA"):
            mapping["fecha"] = h
        elif hn in ("HORARIO", "HORARIOS"):
            mapping["horario"] = h
        elif hn in ("OBSERVACIONES", "OBS", "DETALLE"):
            mapping["obs"] = h
    return mapping


def regeorreferenciar_filas(rows: list, headers: list, mapa_ign: dict):
    """Filtra pasos excluidos ("NO"), corrige provincias forzadas y audita Latitud/Longitud."""
    if not rows:
        return headers, rows
    cols = detectar_columnas_clave(headers)
    col_nom = cols.get("nombre")
    col_prov = cols.get("provincia")
    col_lat = cols.get("lat")
    col_lon = cols.get("lon")
    col_fecha = cols.get("fecha")

    if not col_lat:
        col_lat = "Latitud"
        headers.append(col_lat)
    if not col_lon:
        col_lon = "Longitud"
        headers.append(col_lon)

    if not col_nom:
        return headers, rows

    ocupados_por_fecha = {}
    filas_filtradas = []
    for row in rows:
        nom = row.get(col_nom, "")
        if es_paso_excluido(nom):
            continue

        fecha_key = row.get(col_fecha, "UNICO") if col_fecha else "UNICO"
        ocupados = ocupados_por_fecha.setdefault(fecha_key, {})
        prov_raw = row.get(col_prov, "") if col_prov else ""
        prov_canon = normalizar_provincia(prov_raw, nom)
        if col_prov and prov_canon:
            row[col_prov] = prov_canon

        lat, lon = supervisar_coordenada(
            nombre=nom,
            provincia=prov_canon or prov_raw,
            lat_actual=row.get(col_lat),
            lon_actual=row.get(col_lon),
            mapa_ign=mapa_ign,
            registro_ocupados=ocupados,
        )
        row[col_lat] = f"{lat:.6f}"
        row[col_lon] = f"{lon:.6f}"
        filas_filtradas.append(row)

    return headers, filas_filtradas


def escribir_csv(ruta: str, headers: list, rows: list):
    """Guarda el CSV en UTF-8 con BOM para compatibilidad web y Excel."""
    if not rows:
        print(f"[INFO] Sin filas para escribir en {ruta}; se conserva estado previo.")
        return
    with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def ejecutar_etl():
    hoy_str = datetime.now(TZ_ARGENTINA).strftime("%Y-%m-%d")
    print(f"=== Ejecutando ETL y Supervisión Geográfica ({hoy_str}) ===")

    mapa_ign = cargar_capa_ign()
    nuevos_pasos = obtener_pasos_minseg(mapa_ign)
    print(f"[INFO] Pasos obtenidos desde fuente oficial: {len(nuevos_pasos)}")

    # 1. Actualizar y auditar maestro_pasos.csv
    h_maestro, r_maestro = leer_csv_existente(ARCHIVO_MAESTRO)
    if not h_maestro:
        h_maestro = ["Nombre del Paso", "Provincia", "País Limítrofe", "Medio", "Latitud", "Longitud"]

    if nuevos_pasos:
        cols_m = detectar_columnas_clave(h_maestro)
        c_nom = cols_m.get("nombre", "Nombre del Paso")
        c_prov = cols_m.get("provincia", "Provincia")
        c_pais = cols_m.get("pais", "País Limítrofe")
        c_medio = cols_m.get("medio", "Medio")
        c_lat = cols_m.get("lat", "Latitud")
        c_lon = cols_m.get("lon", "Longitud")
        for col_req in (c_nom, c_prov, c_pais, c_medio, c_lat, c_lon):
            if col_req not in h_maestro:
                h_maestro.append(col_req)

        existentes = {
            (normalizar_texto(r.get(c_nom, "")), normalizar_provincia(r.get(c_prov, ""), r.get(c_nom, ""))): r
            for r in r_maestro
            if not es_paso_excluido(r.get(c_nom, ""))
        }
        for p in nuevos_pasos:
            prov_p = normalizar_provincia(p["Provincia"], p["Nombre del Paso"])
            clave = (normalizar_texto(p["Nombre del Paso"]), prov_p)
            if clave in existentes:
                existentes[clave][c_prov] = prov_p
                existentes[clave][c_lat] = p["Latitud"]
                existentes[clave][c_lon] = p["Longitud"]
                if p["País Limítrofe"]:
                    existentes[clave][c_pais] = p["País Limítrofe"]
                if p["Medio"]:
                    existentes[clave][c_medio] = p["Medio"]
            else:
                r_maestro.append(
                    {
                        c_nom: p["Nombre del Paso"],
                        c_prov: prov_p,
                        c_pais: p["País Limítrofe"],
                        c_medio: p["Medio"],
                        c_lat: p["Latitud"],
                        c_lon: p["Longitud"],
                    }
                )

    h_maestro, r_maestro = regeorreferenciar_filas(r_maestro, h_maestro, mapa_ign)
    escribir_csv(ARCHIVO_MAESTRO, h_maestro, r_maestro)

    # 2. Actualizar y acumular en historico_diario_2026.csv y consolidado_pasos_2026.csv
    headers_base = [
        "Fecha",
        "Nombre del Paso",
        "Provincia",
        "País Limítrofe",
        "Medio",
        "Estado",
        "Latitud",
        "Longitud",
        "Horario",
        "Observaciones",
    ]

    h_hist, r_hist = leer_csv_existente(ARCHIVO_HISTORICO)
    h_cons, r_cons = leer_csv_existente(ARCHIVO_CONSOLIDADO)

    h_target = h_cons or h_hist or list(headers_base)
    cols_t = detectar_columnas_clave(h_target)
    c_fecha = cols_t.get("fecha", "Fecha")
    c_nom = cols_t.get("nombre", "Nombre del Paso")
    c_prov = cols_t.get("provincia", "Provincia")
    c_pais = cols_t.get("pais", "País Limítrofe")
    c_medio = cols_t.get("medio", "Medio")
    c_est = cols_t.get("estado", "Estado")
    c_lat = cols_t.get("lat", "Latitud")
    c_lon = cols_t.get("lon", "Longitud")
    c_hor = cols_t.get("horario", "Horario")
    c_obs = cols_t.get("obs", "Observaciones")

    for col_req in (c_fecha, c_nom, c_prov, c_pais, c_medio, c_est, c_lat, c_lon):
        if col_req not in h_target:
            h_target.append(col_req)

    # Fusionar registros acumulados previos de consolidado + histórico sin duplicar
    acumulado = []
    claves_vistas = set()
    for fuente_rows in (r_cons, r_hist):
        for r in fuente_rows:
            nom_r = r.get(c_nom, "")
            if not nom_r or es_paso_excluido(nom_r):
                continue
            f_r = str(r.get(c_fecha, "")).strip()
            if nuevos_pasos and f_r == hoy_str:
                continue
            key = (f_r, normalizar_texto(nom_r))
            if key not in claves_vistas:
                claves_vistas.add(key)
                acumulado.append(dict(r))

    if nuevos_pasos:
        for p in nuevos_pasos:
            prov_p = normalizar_provincia(p["Provincia"], p["Nombre del Paso"])
            fila = {
                c_fecha: hoy_str,
                c_nom: p["Nombre del Paso"],
                c_prov: prov_p,
                c_pais: p["País Limítrofe"],
                c_medio: p["Medio"],
                c_est: p["Estado"],
                c_lat: p["Latitud"],
                c_lon: p["Longitud"],
            }
            if c_hor in h_target:
                fila[c_hor] = p["Horario"]
            if c_obs in h_target:
                fila[c_obs] = p["Observaciones"]
            acumulado.append(fila)

    h_target, acumulado = regeorreferenciar_filas(acumulado, h_target, mapa_ign)
    escribir_csv(ARCHIVO_HISTORICO, list(h_target), [dict(r) for r in acumulado])
    escribir_csv(ARCHIVO_CONSOLIDADO, list(h_target), [dict(r) for r in acumulado])

    print(
        f"[OK] Archivos actualizados y auditados: "
        f"{ARCHIVO_MAESTRO} ({len(r_maestro)} filas), "
        f"{ARCHIVO_HISTORICO} ({len(acumulado)} filas), "
        f"{ARCHIVO_CONSOLIDADO} ({len(acumulado)} filas)."
    )


if __name__ == "__main__":
    ejecutar_etl()
