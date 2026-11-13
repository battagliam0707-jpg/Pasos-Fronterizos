import csv
import io
import json
import math
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

TZ_AR = timezone(timedelta(hours=-3))

URL_PASOS_WEB = "https://www.argentina.gob.ar/seguridad/pasosinternacionales"
URL_MIGRACIONES_WEB = "https://www.argentina.gob.ar/migraciones/pasos-internacionales"

FILES = {
    "maestro": "maestro_pasos.csv",
    "historico": "historico_diario_2026.csv",
    "consolidado": "consolidado_pasos_2026.csv",
}

def norm(texto: str) -> str:
    if not texto:
        return ""
    s = unicodedata.normalize("NFD", str(texto))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-zA-Z0-9\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()

# 17 pasos marcados con "NO" en auditoría (ignorados por no ser reportados)
PASOS_IGNORADOS = {
    "huaytiquina",
    "la chapetona",
    "calderas",
    "pachon",
    "santa elena",
    "lumabia",
    "nellocahui",
    "coloco",
    "edgardo castello",
    "viedma",
    "lago rosario",
    "camarones",
    "rawson",
    "el bello",
    "glaciar dos lagos",
    "punta dungeness",
    "punta loyola",
    "muelle san sebastian",
    "cruz del sur",
}

# Límites geográficos estrictos por provincia (lat_min, lat_max, lon_min, lon_max, lat_def, lon_def)
PERFILES_PROVINCIALES = {
    "jujuy": (-24.65, -21.75, -67.25, -64.10, -23.2350, -67.0650),
    "salta": (-26.45, -21.95, -68.60, -62.30, -22.7264, -64.3700),
    "catamarca": (-29.25, -25.10, -69.10, -64.80, -26.8764, -68.3031),
    "la rioja": (-32.00, -27.70, -69.70, -65.40, -27.8489, -69.2556),
    "san juan": (-32.65, -28.35, -70.65, -66.70, -30.1939, -69.8253),
    "mendoza": (-37.60, -32.00, -70.60, -66.45, -32.8253, -70.0725),
    "neuquen": (-41.15, -36.05, -71.95, -68.00, -38.6531, -70.9036),
    "rio negro": (-42.20, -37.50, -71.95, -62.75, -41.0589, -71.8169),
    "chubut": (-46.05, -41.95, -72.25, -64.90, -43.1786, -71.7519),
    "santa cruz": (-52.45, -45.95, -73.60, -65.70, -52.1389, -69.5181),
    "tierra del fuego": (-55.20, -52.60, -68.70, -63.70, -53.3189, -68.6042),
    "formosa": (-26.95, -22.40, -62.35, -57.60, -25.2589, -57.7081),
    "chaco": (-28.05, -24.05, -63.45, -58.30, -27.0819, -58.6131),
    "misiones": (-28.20, -25.45, -56.10, -53.60, -25.5883, -54.5617),
    "corrientes": (-30.75, -27.20, -59.70, -55.60, -29.7336, -57.1019),
    "entre rios": (-34.10, -30.10, -60.80, -57.75, -33.0947, -58.2464),
    "santa fe": (-34.45, -27.95, -62.90, -58.85, -32.9442, -60.6306),
    "buenos aires": (-41.10, -33.20, -63.40, -56.60, -34.6118, -58.3622),
    "caba": (-34.71, -34.52, -58.54, -58.33, -34.6118, -58.3622),
    "cordoba": (-35.05, -29.45, -65.80, -61.75, -31.3236, -64.2081),
    "tucuman": (-28.05, -26.05, -66.20, -64.45, -26.8408, -65.1047),
    "santiago del estero": (-30.45, -25.60, -65.20, -61.70, -27.4956, -64.9358),
    "san luis": (-36.05, -31.85, -67.40, -64.90, -33.2731, -66.3564),
    "la pampa": (-39.35, -34.95, -68.35, -63.35, -36.6883, -64.2756),
}

# Catálogo auditado: (clave_normalizada, provincia_normalizada, lat, lon, pais_limitrofe, medio)
CATALOGO_AUDITADO = [
    # JUJUY
    ("la quiaca", "jujuy", -22.0967, -65.5964, "Bolivia", "Terrestre"),
    ("jama norte", "jujuy", -23.227301, -67.063069, "Chile", "Terrestre"),
    ("jama sur", "jujuy", -23.233026, -67.064815, "Chile", "Terrestre"),
    ("jama", "jujuy", -23.2350, -67.0650, "Chile", "Terrestre"),
    ("horacio guzman", "jujuy", -24.3928, -65.0978, "Internacional", "Aéreo"),
    # SALTA
    ("salvador mazza", "salta", -22.0533, -63.6836, "Bolivia", "Terrestre"),
    ("aguas blancas", "salta", -22.7264, -64.3700, "Bolivia", "Terrestre"),
    ("chalanas", "salta", -22.7297, -64.3553, "Bolivia", "Fluvial"),
    ("el condado", "salta", -22.4261, -64.5128, "Bolivia", "Terrestre"),
    ("mision la paz", "salta", -22.3789, -62.5214, "Paraguay", "Terrestre"),
    ("laguna sico", "salta", -23.874876, -67.271398, "Chile", "Terrestre"),
    ("sico", "salta", -23.8350, -67.2600, "Chile", "Terrestre"),
    ("socompa", "salta", -24.4444, -68.2950, "Chile", "Terrestre"),
    ("martin miguel de guemes", "salta", -24.8560, -65.4862, "Internacional", "Aéreo"),
    # CATAMARCA
    ("san francisco", "catamarca", -26.8764, -68.3031, "Chile", "Terrestre"),
    ("felipe varela", "catamarca", -28.5924622103104, -65.75498297415245, "Internacional", "Aéreo"),
    # LA RIOJA
    ("pircas negras", "la rioja", -27.8489, -69.2556, "Chile", "Terrestre"),
    ("almandos almonacid", "la rioja", -29.383745845336172, -66.79433575463631, "Internacional", "Aéreo"),
    # SAN JUAN
    ("agua negra", "san juan", -30.1939, -69.8253, "Chile", "Terrestre"),
    ("colanguil", "san juan", -30.03275792123306, -69.29369560759945, "Chile", "Terrestre"),
    ("valle hermoso", "san juan", -30.783140, -70.283190, "Chile", "Terrestre"),
    ("domingo faustino sarmiento", "san juan", -31.5715, -68.4182, "Internacional", "Aéreo"),
    # MENDOZA
    ("cristo redentor", "mendoza", -32.8253, -70.0725, "Chile", "Terrestre"),
    ("horcones", "mendoza", -32.8253, -70.0725, "Chile", "Terrestre"),
    ("pehuenche", "mendoza", -35.9817, -70.3989, "Chile", "Terrestre"),
    ("vergara", "mendoza", -35.1619, -70.5117, "Chile", "Terrestre"),
    ("planchon", "mendoza", -35.1619, -70.5117, "Chile", "Terrestre"),
    ("piuquenes", "mendoza", -33.613674934669646, -69.49885336487009, "Chile", "Terrestre"),
    ("cajon del maipo", "mendoza", -34.190939362717494, -69.70436192023638, "Chile", "Terrestre"),
    ("las damas", "mendoza", -34.884690, -70.291109, "Chile", "Terrestre"),
    ("plumerillo", "mendoza", -32.8317, -68.7929, "Internacional", "Aéreo"),
    ("ricardo salomon", "mendoza", -35.4936, -69.5743, "Internacional", "Aéreo"),
    # NEUQUÉN
    ("pino hachado", "neuquen", -38.6531, -70.9036, "Chile", "Terrestre"),
    ("cardenal samore", "neuquen", -40.7131, -71.9469, "Chile", "Terrestre"),
    ("samore", "neuquen", -40.7131, -71.9469, "Chile", "Terrestre"),
    ("icalma", "neuquen", -38.8381, -71.2731, "Chile", "Terrestre"),
    ("mamuil malal", "neuquen", -39.5831, -71.4831, "Chile", "Terrestre"),
    ("tromen", "neuquen", -39.5831, -71.4831, "Chile", "Terrestre"),
    ("hua hum", "neuquen", -40.1189, -71.6689, "Chile", "Terrestre / Lacustre"),
    ("carirrine", "neuquen", -39.7931, -71.6531, "Chile", "Terrestre"),
    ("pichachen", "neuquen", -37.4531, -71.1231, "Chile", "Terrestre"),
    ("copahue", "neuquen", -37.814817101834635, -71.09336827976361, "Chile", "Terrestre"),
    ("presidente peron", "neuquen", -38.9490, -68.1557, "Internacional", "Aéreo"),
    ("chapelco", "neuquen", -40.0754, -71.1373, "Internacional", "Aéreo"),
    # RÍO NEGRO
    ("perez rosales", "rio negro", -41.0589, -71.8169, "Chile", "Terrestre / Lacustre"),
    ("rio manso", "rio negro", -41.5031, -71.8431, "Chile", "Terrestre"),
    ("vuriloche", "rio negro", -41.232810, -71.770120, "Chile", "Terrestre"),
    ("punta colorada", "rio negro", -41.6983, -65.0078, "Internacional", "Marítimo"),
    ("san antonio este", "rio negro", -40.8031, -64.8831, "Internacional", "Marítimo"),
    ("luis candelaria", "rio negro", -41.1512, -71.1575, "Internacional", "Aéreo"),
    ("bariloche", "rio negro", -41.1512, -71.1575, "Internacional", "Aéreo"),
    # CHUBUT
    ("futaleufu", "chubut", -43.1786, -71.7519, "Chile", "Terrestre"),
    ("huemules", "chubut", -45.9031, -71.6531, "Chile", "Terrestre"),
    ("rio encuentro", "chubut", -43.5889, -71.7489, "Chile", "Terrestre"),
    ("rio puelo", "chubut", -42.1031, -71.7231, "Chile", "Terrestre"),
    ("cohiue", "chubut", -45.5531, -71.7331, "Chile", "Terrestre"),
    ("triana", "chubut", -45.572530, -71.698310, "Chile", "Terrestre"),
    ("pallavicini", "chubut", -46.0131, -71.6631, "Chile", "Terrestre"),
    ("pampa alta", "chubut", -45.230420, -71.327320, "Chile", "Terrestre"),
    ("rio frias", "chubut", -44.579590, -71.056280, "Chile", "Terrestre"),
    ("las pampas", "chubut", -44.238770, -71.719420, "Chile", "Terrestre"),
    ("puerto madryn", "chubut", -42.7611, -65.0331, "Internacional", "Marítimo"),
    ("comodoro rivadavia", "chubut", -45.8622, -67.4631, "Internacional", "Marítimo"),
    ("enrique mosconi", "chubut", -45.7853, -67.4656, "Internacional", "Aéreo"),
    ("marco andres zar", "chubut", -43.2105, -65.2703, "Internacional", "Aéreo"),
    ("tehuelche", "chubut", -42.7592, -65.1027, "Internacional", "Aéreo"),
    ("antonio parodi", "chubut", -42.9080, -71.1395, "Internacional", "Aéreo"),
    # SANTA CRUZ
    ("integracion austral", "santa cruz", -52.1389, -69.5181, "Chile", "Terrestre"),
    ("monte aymond", "santa cruz", -52.1389, -69.5181, "Chile", "Terrestre"),
    ("don guillermo", "santa cruz", -51.2531, -72.2531, "Chile", "Terrestre"),
    ("laurita", "santa cruz", -51.6731, -72.3231, "Chile", "Terrestre"),
    ("dorotea", "santa cruz", -51.5831, -72.3431, "Chile", "Terrestre"),
    ("cancha carrera", "santa cruz", -51.2631, -72.2431, "Chile", "Terrestre"),
    ("jeinimeni", "santa cruz", -46.5431, -71.6531, "Chile", "Terrestre"),
    ("roballos", "santa cruz", -47.1531, -71.8531, "Chile", "Terrestre"),
    ("mayer", "santa cruz", -48.1831, -72.3331, "Chile", "Terrestre"),
    ("lago san martin", "santa cruz", -49.126120, -72.491900, "Chile", "Terrestre"),
    ("marconi", "santa cruz", -49.331370, -72.884760, "Chile", "Terrestre"),
    ("portezuelo de la divisoria", "santa cruz", -48.998980, -72.839410, "Chile", "Terrestre"),
    ("rio mosco", "santa cruz", -48.489390, -72.552780, "Chile", "Terrestre"),
    ("cabo virgenes", "santa cruz", -52.3289, -68.3531, "Chile", "Terrestre"),
    ("monte dinero", "santa cruz", -52.239468, -68.883148, "Chile", "Terrestre"),
    ("puerto rio gallegos", "santa cruz", -51.6219, -69.2131, "Internacional", "Marítimo"),
    ("punta quilla", "santa cruz", -50.1167, -68.4167, "Internacional", "Marítimo"),
    ("puerto santa cruz", "santa cruz", -50.0189, -68.5231, "Internacional", "Marítimo"),
    ("san julian", "santa cruz", -49.3089, -67.7231, "Internacional", "Marítimo"),
    ("puerto deseado", "santa cruz", -47.7531, -65.8931, "Internacional", "Marítimo"),
    ("caleta olivia", "santa cruz", -46.4431, -67.5131, "Internacional", "Marítimo"),
    ("armando tola", "santa cruz", -50.2803, -72.0531, "Internacional", "Aéreo"),
    ("norberto fernandez", "santa cruz", -51.6089, -69.3126, "Internacional", "Aéreo"),
    # TIERRA DEL FUEGO (incluye Puerto Williams / Puerto Almanza)
    ("san sebastian", "tierra del fuego", -53.3189, -68.6042, "Chile", "Terrestre"),
    ("radman", "tierra del fuego", -53.9931, -68.6131, "Chile", "Terrestre"),
    ("bellavista", "tierra del fuego", -53.9931, -68.6131, "Chile", "Terrestre"),
    ("puerto williams", "tierra del fuego", -54.8722, -67.5719, "Chile", "Marítimo"),
    ("almanza", "tierra del fuego", -54.8722, -67.5719, "Chile", "Marítimo"),
    ("puerto ushuaia", "tierra del fuego", -54.8089, -68.3031, "Internacional", "Marítimo"),
    ("puerto rio grande", "tierra del fuego", -53.7889, -67.6931, "Internacional", "Marítimo"),
    ("malvinas argentinas", "tierra del fuego", -54.8433, -68.2958, "Internacional", "Aéreo"),
    ("hermes quijada", "tierra del fuego", -53.7777, -67.7494, "Internacional", "Aéreo"),
    # FORMOSA (incluye Puerto Colonia Cano - Puerto Pilar corregido)
    ("san ignacio de loyola", "formosa", -25.2589, -57.7081, "Paraguay", "Terrestre"),
    ("clorinda", "formosa", -25.2589, -57.7081, "Paraguay", "Terrestre"),
    ("la fraternidad", "formosa", -25.2869, -57.7219, "Paraguay", "Terrestre"),
    ("pilcomayo", "formosa", -25.3711, -57.6519, "Paraguay", "Fluvial"),
    ("puerto formosa", "formosa", -26.1831, -58.1631, "Paraguay", "Fluvial"),
    ("colonia cano", "formosa", -26.853231, -58.324608, "Paraguay", "Fluvial"),
    ("el remanso", "formosa", -23.987892, -60.458126, "Paraguay", "Terrestre"),
    ("isleta", "formosa", -24.212000, -60.197070, "Paraguay", "Terrestre"),
    ("lamadrid", "formosa", -23.897911, -60.755810, "Paraguay", "Terrestre"),
    ("general belgrano", "formosa", -24.748760, -58.833130, "Paraguay", "Terrestre"),
    ("el pucu", "formosa", -26.2127, -58.2281, "Internacional", "Aéreo"),
    # CHACO
    ("las palmas", "chaco", -27.0819, -58.6131, "Paraguay", "Fluvial"),
    ("puerto bermejo", "chaco", -26.9289, -58.5031, "Paraguay", "Fluvial"),
    ("barranqueras", "chaco", -27.4831, -58.9231, "Internacional", "Fluvial"),
    ("resistencia", "chaco", -27.4500, -59.0561, "Internacional", "Aéreo"),
    # MISIONES
    ("tancredo neves", "misiones", -25.5883, -54.5617, "Brasil", "Terrestre"),
    ("foz de iguazu", "misiones", -25.5883, -54.5617, "Brasil", "Terrestre"),
    ("puerto meira", "misiones", -25.5933, -54.5789, "Brasil", "Fluvial"),
    ("tres fronteras", "misiones", -25.5939, -54.5794, "Paraguay", "Fluvial"),
    ("posadas", "misiones", -27.3689, -55.8831, "Paraguay", "Terrestre"),
    ("pacu cua", "misiones", -27.3589, -55.8789, "Paraguay", "Fluvial"),
    ("bernardo de irigoyen", "misiones", -26.2547, -53.6439, "Brasil", "Terrestre"),
    ("andresito", "misiones", -25.6017, -53.9781, "Brasil", "Terrestre"),
    ("san antonio", "misiones", -26.0567, -53.7306, "Brasil", "Terrestre"),
    ("integracion", "misiones", -25.7689, -53.8511, "Brasil", "Fluvial"),
    ("pepiri guazu", "misiones", -26.6131, -53.7347, "Brasil", "Terrestre"),
    ("el soberbio", "misiones", -27.3000, -54.1917, "Brasil", "Fluvial"),
    ("alicia", "misiones", -27.4639, -54.3442, "Brasil", "Fluvial"),
    ("aurora", "misiones", -27.4994, -54.5206, "Brasil", "Fluvial"),
    ("alba posse", "misiones", -27.5714, -54.6792, "Brasil", "Fluvial"),
    ("barra bonita", "misiones", -27.6014, -54.8431, "Brasil", "Fluvial"),
    ("panambi", "misiones", -27.7300, -54.9142, "Brasil", "Fluvial"),
    ("paso de la barca", "misiones", -27.8919, -55.1356, "Brasil", "Fluvial"),
    ("santa maria", "misiones", -27.9678, -55.3508, "Brasil", "Fluvial"),
    ("san isidro", "misiones", -28.0503, -55.4158, "Brasil", "Fluvial"),
    ("puerto libertad", "misiones", -25.9647, -54.6039, "Paraguay", "Fluvial"),
    ("puerto wanda", "misiones", -25.9650, -54.6042, "Paraguay", "Fluvial"),
    ("puerto mado", "misiones", -26.2303, -54.6294, "Paraguay", "Fluvial"),
    ("puerto victoria", "misiones", -26.3350, -54.4667, "Paraguay", "Fluvial"),
    ("puerto pinares", "misiones", -26.4061, -54.6939, "Paraguay", "Fluvial"),
    ("eldorado", "misiones", -26.4064, -54.6939, "Paraguay", "Fluvial"),
    ("puerto piray", "misiones", -26.4728, -54.7261, "Paraguay", "Fluvial"),
    ("montecarlo", "misiones", -26.5594, -54.8039, "Paraguay", "Fluvial"),
    ("paranay", "misiones", -26.6744, -54.8150, "Paraguay", "Fluvial"),
    ("puerto rico", "misiones", -26.7919, -55.0244, "Paraguay", "Fluvial"),
    ("garuhape", "misiones", -26.7914, -55.0247, "Paraguay", "Fluvial"),
    ("puerto leoni", "misiones", -26.9831, -55.1631, "Paraguay", "Fluvial"),
    ("puerto oasis", "misiones", -27.0131, -55.2631, "Paraguay", "Fluvial"),
    ("puerto mani", "misiones", -27.1431, -55.4831, "Paraguay", "Fluvial"),
    ("san ignacio", "misiones", -27.2531, -55.5531, "Paraguay", "Fluvial"),
    ("santa ana", "misiones", -27.3531, -55.5931, "Paraguay", "Fluvial"),
    ("candelaria", "misiones", -27.4531, -55.7431, "Paraguay", "Fluvial"),
    ("cataratas del iguazu", "misiones", -25.7373, -54.4734, "Internacional", "Aéreo"),
    ("libertador general jose de san martin", "misiones", -27.3858, -55.9707, "Internacional", "Aéreo"),
    # CORRIENTES
    ("paso de los libres", "corrientes", -29.7336, -57.1019, "Brasil", "Terrestre"),
    ("santo tome", "corrientes", -28.6106, -56.0144, "Brasil", "Terrestre"),
    ("puerto alvear", "corrientes", -29.1133, -56.5553, "Brasil", "Fluvial"),
    ("puerto la cruz", "corrientes", -29.1783, -56.6336, "Brasil", "Fluvial"),
    ("yapeyu", "corrientes", -29.4700, -56.8111, "Brasil", "Fluvial"),
    ("garruchos", "corrientes", -28.1772, -55.6425, "Brasil", "Fluvial"),
    ("monte caseros", "corrientes", -30.2519, -57.6222, "Uruguay", "Fluvial"),
    ("ituzaingo", "corrientes", -27.5831, -56.6831, "Paraguay", "Fluvial"),
    ("yacyreta", "corrientes", -27.4831, -56.7331, "Paraguay", "Terrestre"),
    ("apipe", "corrientes", -27.5131, -56.7431, "Paraguay", "Fluvial"),
    ("ita ibate", "corrientes", -27.4331, -57.3331, "Paraguay", "Fluvial"),
    ("yahape", "corrientes", -27.3731, -57.6531, "Paraguay", "Fluvial"),
    ("itati", "corrientes", -27.2689, -58.2431, "Paraguay", "Fluvial"),
    ("paso de la patria", "corrientes", -27.3189, -58.5731, "Paraguay", "Fluvial"),
    ("puerto corrientes", "corrientes", -27.4631, -58.8331, "Internacional", "Fluvial"),
    ("goya", "corrientes", -29.1431, -59.2731, "Internacional", "Fluvial"),
    ("esquina", "corrientes", -30.0131, -59.5331, "Internacional", "Fluvial"),
    ("piragine niveyro", "corrientes", -27.4455, -58.7619, "Internacional", "Aéreo"),
    # ENTRE RÍOS
    ("gualeguaychu", "entre rios", -33.0947, -58.2464, "Uruguay", "Terrestre"),
    ("colon", "entre rios", -32.2656, -58.0939, "Uruguay", "Terrestre"),
    ("concordia", "entre rios", -31.2608, -57.9617, "Uruguay", "Terrestre"),
    ("boca de gualeguaychu", "entre rios", -33.0739, -58.4206, "Uruguay", "Fluvial"),
    ("concepcion del uruguay", "entre rios", -32.4833, -58.2222, "Uruguay", "Fluvial"),
    ("puerto colon", "entre rios", -32.2156, -58.1367, "Uruguay", "Fluvial"),
    ("puerto concordia", "entre rios", -31.4022, -58.0036, "Uruguay", "Fluvial"),
    ("puerto luis", "entre rios", -31.209789, -57.930817, "Uruguay", "Fluvial"),
    ("federacion", "entre rios", -30.9931, -57.9153, "Uruguay", "Fluvial"),
    ("santa eloisa", "entre rios", -30.761606, -57.822300, "Uruguay", "Fluvial"),
    ("paranacito", "entre rios", -33.7161, -58.6594, "Uruguay", "Fluvial"),
    ("guazucito", "entre rios", -34.0147, -58.3956, "Uruguay", "Fluvial"),
    ("portuaria del guazu", "entre rios", -33.8931, -58.8831, "Internacional", "Fluvial"),
    ("ibicuy", "entre rios", -33.7531, -59.1731, "Internacional", "Fluvial"),
    ("diamante", "entre rios", -32.0631, -60.6431, "Internacional", "Fluvial"),
    ("puerto de parana", "entre rios", -31.7092, -60.5736, "Internacional", "Fluvial"),
    ("la paz", "entre rios", -30.6672, -59.6403, "Internacional", "Fluvial"),
    ("pierrestegui", "entre rios", -31.2969, -57.9967, "Internacional", "Aéreo"),
    ("justo jose de urquiza", "entre rios", -31.7948, -60.4804, "Internacional", "Aéreo"),
    # SANTA FE
    ("puerto rosario", "santa fe", -32.9442, -60.6306, "Internacional", "Fluvial"),
    ("san lorenzo", "santa fe", -32.7431, -60.7331, "Internacional", "Fluvial"),
    ("general san martin", "santa fe", -32.7131, -60.7231, "Internacional", "Fluvial"),
    ("villa constitucion", "santa fe", -33.2231, -60.3231, "Internacional", "Fluvial"),
    ("puerto santa fe", "santa fe", -31.6489, -60.7031, "Internacional", "Fluvial"),
    ("islas malvinas", "santa fe", -32.9036, -60.7850, "Internacional", "Aéreo"),
    ("sauce viejo", "santa fe", -31.7117, -60.8117, "Internacional", "Aéreo"),
    # BUENOS AIRES Y CABA
    ("martin garcia", "buenos aires", -34.1889, -58.2553, "Uruguay", "Fluvial"),
    ("buquebus", "caba", -34.6118, -58.3622, "Uruguay", "Fluvial"),
    ("colonia express", "caba", -34.6231, -58.3569, "Uruguay", "Fluvial"),
    ("quinquela martin", "caba", -34.5858, -58.3711, "Internacional", "Marítimo / Fluvial"),
    ("puerto de buenos aires", "caba", -34.5889, -58.3689, "Internacional", "Marítimo / Fluvial"),
    ("yacht club argentino", "caba", -34.5931, -58.3653, "Uruguay", "Fluvial"),
    ("tigre", "buenos aires", -34.4219, -58.5819, "Uruguay", "Fluvial"),
    ("san fernando", "buenos aires", -34.4369, -58.5531, "Uruguay", "Fluvial"),
    ("puerto san isidro", "buenos aires", -34.4631, -58.5031, "Uruguay", "Fluvial"),
    ("olivos", "buenos aires", -34.5031, -58.4789, "Uruguay", "Fluvial"),
    ("quilmes", "buenos aires", -34.7031, -58.2231, "Internacional", "Fluvial"),
    ("la plata", "buenos aires", -34.8531, -57.8831, "Internacional", "Fluvial"),
    ("campana", "buenos aires", -34.1531, -58.9531, "Internacional", "Fluvial"),
    ("zarate", "buenos aires", -34.0931, -59.0231, "Internacional", "Fluvial"),
    ("escobar", "buenos aires", -34.2531, -58.7331, "Internacional", "Fluvial"),
    ("san pedro", "buenos aires", -33.6731, -59.6531, "Internacional", "Fluvial"),
    ("ramallo", "buenos aires", -33.4831, -60.0031, "Internacional", "Fluvial"),
    ("san nicolas", "buenos aires", -33.3231, -60.2131, "Internacional", "Fluvial"),
    ("mar del plata", "buenos aires", -38.0331, -57.5331, "Internacional", "Marítimo"),
    ("quequen", "buenos aires", -38.5731, -58.7031, "Internacional", "Marítimo"),
    ("bahia blanca", "buenos aires", -38.7831, -62.2631, "Internacional", "Marítimo"),
    ("ezeiza", "buenos aires", -34.8222, -58.5358, "Internacional", "Aéreo"),
    ("jorge newbery", "caba", -34.5592, -58.4156, "Internacional", "Aéreo"),
    ("aeropuerto internacional san fernando", "buenos aires", -34.4532, -58.5896, "Internacional", "Aéreo"),
    ("palomar", "buenos aires", -34.6099, -58.6126, "Internacional", "Aéreo"),
    ("astor piazzolla", "buenos aires", -37.9342, -57.5733, "Internacional", "Aéreo"),
    ("comandante espora", "buenos aires", -38.7250, -62.1693, "Internacional", "Aéreo"),
    # RESTO INTERIOR AÉREO
    ("ambrosio taravella", "cordoba", -31.3236, -64.2081, "Internacional", "Aéreo"),
    ("benjamin matienzo", "tucuman", -26.8408, -65.1047, "Internacional", "Aéreo"),
    ("termas de rio hondo", "santiago del estero", -27.4956, -64.9358, "Internacional", "Aéreo"),
    ("cesar raul ojeda", "san luis", -33.2731, -66.3564, "Internacional", "Aéreo"),
    ("santa rosa", "la pampa", -36.6883, -64.2756, "Internacional", "Aéreo"),
]

# Ordenar claves de mayor a menor longitud para evitar colisiones de subcadenas
CATALOGO_AUDITADO.sort(key=lambda x: len(x[0]), reverse=True)


def es_paso_ignorado(nombre: str) -> bool:
    n = norm(nombre)
    return any(ig in n for ig in PASOS_IGNORADOS)


def resolver_provincia_canonica(prov_raw: str, nombre_raw: str) -> str:
    n_nom = norm(nombre_raw)
    if "puerto williams" in n_nom or "almanza" in n_nom:
        return "tierra del fuego"
    if "colonia cano" in n_nom:
        return "formosa"
    n_prov = norm(prov_raw)
    if "tierra del fuego" in n_prov:
        return "tierra del fuego"
    if "area metropolitana" in n_prov or "caba" in n_prov or "ciudad autonoma" in n_prov:
        return "caba"
    for p_key in PERFILES_PROVINCIALES:
        if p_key in n_prov:
            return p_key
    for clave, prov, _, _, _, _ in CATALOGO_AUDITADO:
        if clave in n_nom:
            return prov
    return n_prov


def dentro_de_provincia(prov_canon: str, lat: float, lon: float) -> bool:
    perfil = PERFILES_PROVINCIALES.get(prov_canon)
    if not perfil:
        return True
    lat_min, lat_max, lon_min, lon_max, _, _ = perfil
    return (lat_min <= lat <= lat_max) and (lon_min <= lon <= lon_max)


def desagregar_superposicion(nombre: str, lat: float, lon: float, usados: set) -> tuple:
    key = (round(lat, 5), round(lon, 5))
    if key not in usados:
        usados.add(key)
        return round(lat, 6), round(lon, 6)
    h = sum(ord(c) for c in norm(nombre))
    ang = (h % 360) * (math.pi / 180.0)
    r = 0.0022
    new_lat = round(lat + r * math.sin(ang), 6)
    new_lon = round(lon + r * math.cos(ang), 6)
    usados.add((round(new_lat, 5), round(new_lon, 5)))
    return new_lat, new_lon


def georreferenciar_paso(nombre: str, prov_raw: str, lat_actual=None, lon_actual=None, usados=None):
    if usados is None:
        usados = set()
    n_nom = norm(nombre)
    prov_canon = resolver_provincia_canonica(prov_raw, nombre)

    for clave, prov_cat, lat_cat, lon_cat, pais_cat, medio_cat in CATALOGO_AUDITADO:
        if clave in n_nom:
            if not prov_canon or prov_canon == prov_cat or (prov_canon in ("caba", "buenos aires") and prov_cat in ("caba", "buenos aires")):
                lat_f, lon_f = desagregar_superposicion(nombre, lat_cat, lon_cat, usados)
                return lat_f, lon_f, prov_cat, pais_cat, medio_cat, "ALTA"

    try:
        if lat_actual is not None and lon_actual is not None:
            la, lo = float(lat_actual), float(lon_actual)
            if -56.0 <= la <= -21.0 and -74.0 <= lo <= -53.0 and dentro_de_provincia(prov_canon, la, lo):
                lat_f, lon_f = desagregar_superposicion(nombre, la, lo, usados)
                return lat_f, lon_f, prov_canon, "", "", "MEDIA"
    except (ValueError, TypeError):
        pass

    perfil = PERFILES_PROVINCIALES.get(prov_canon)
    if perfil:
        _, _, _, _, lat_def, lon_def = perfil
        lat_f, lon_f = desagregar_superposicion(nombre, lat_def, lon_def, usados)
        return lat_f, lon_f, prov_canon, "", "", "RESPALDO"

    return -34.6037, -58.3816, prov_canon, "", "", "RESPALDO"


def formatear_provincia(prov_canon: str, prov_original: str = "") -> str:
    titulos = {
        "jujuy": "Jujuy",
        "salta": "Salta",
        "catamarca": "Catamarca",
        "la rioja": "La Rioja",
        "san juan": "San Juan",
        "mendoza": "Mendoza",
        "neuquen": "Neuquén",
        "rio negro": "Río Negro",
        "chubut": "Chubut",
        "santa cruz": "Santa Cruz",
        "tierra del fuego": "Tierra del Fuego",
        "formosa": "Formosa",
        "chaco": "Chaco",
        "misiones": "Misiones",
        "corrientes": "Corrientes",
        "entre rios": "Entre Ríos",
        "santa fe": "Santa Fe",
        "buenos aires": "Buenos Aires",
        "caba": "CABA",
        "cordoba": "Córdoba",
        "tucuman": "Tucumán",
        "santiago del estero": "Santiago del Estero",
        "san luis": "San Luis",
        "la pampa": "La Pampa",
    }
    return titulos.get(prov_canon, prov_original.strip() or "Sin especificar")


def descargar_html(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (ObservatorioPasosAR/2026)"})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def extraer_datos_oficiales():
    registros = []
    try:
        html = descargar_html(URL_PASOS_WEB)
        # Extraer bloques JSON embebidos si existen en el mapa oficial
        matches = re.findall(r'\{[^{}]*"title"\s*:\s*"([^"]+)"[^{}]*\}', html)
        for m in re.finditer(r'\{[^{}]*"title"\s*:\s*"([^"]+)"[^{}]*\}', html):
            bloque = m.group(0)
            titulo = m.group(1).strip()
            if es_paso_ignorado(titulo):
                continue
            lat_m = re.search(r'"lat(?:itude)?"\s*:\s*"?(-?\d+\.\d+)"?', bloque)
            lon_m = re.search(r'"(?:lon|lng|longitude)"\s*:\s*"?(-?\d+\.\d+)"?', bloque)
            prov_m = re.search(r'"(?:provincia|province)"\s*:\s*"([^"]+)"', bloque)
            est_m = re.search(r'"(?:estado|status)"\s*:\s*"([^"]+)"', bloque)
            lat_v = float(lat_m.group(1)) if lat_m else None
            lon_v = float(lon_m.group(1)) if lon_m else None
            prov_v = prov_m.group(1).strip() if prov_m else ""
            est_v = est_m.group(1).strip() if est_m else "Habilitado"
            registros.append({
                "nombre": titulo,
                "provincia": prov_v,
                "estado": est_v,
                "lat": lat_v,
                "lon": lon_v,
            })
    except Exception as e:
        print(f"[AVISO] No se pudo extraer mapa dinámico: {e}")
    return registros


def leer_csv(path: str):
    if not os.path.exists(path):
        return [], []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        delim = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.DictReader(f, delimiter=delim)
        return list(reader), (reader.fieldnames or [])


def guardar_csv(path: str, rows: list, fieldnames: list):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def auditar_y_actualizar():
    ahora_ar = datetime.now(TZ_AR)
    fecha_hoy = ahora_ar.strftime("%Y-%m-%d")
    hora_corte = ahora_ar.strftime("%Y-%m-%d %H:%M:%S")
    usados = set()

    # 1. Auditar y depurar consolidado_pasos_2026.csv y historico_diario_2026.csv
    for key_csv in ("consolidado", "historico"):
        path = FILES[key_csv]
        rows, fields = leer_csv(path)
        if not rows:
            continue
        limpios = []
        usados_archivo = set()
        for r in rows:
            nombre = r.get("Nombre del Paso") or r.get("Paso") or r.get("nombre") or ""
            if not nombre or es_paso_ignorado(nombre):
                continue
            prov_raw = r.get("Provincia") or r.get("provincia") or ""
            lat_raw = r.get("Latitud") or r.get("lat")
            lon_raw = r.get("Longitud") or r.get("lon")
            lat, lon, prov_c, pais_c, medio_c, conf = georreferenciar_paso(
                nombre, prov_raw, lat_raw, lon_raw, usados_archivo
            )
            r["Nombre del Paso"] = nombre.strip()
            r["Provincia"] = formatear_provincia(prov_c, prov_raw)
            r["Latitud"] = lat
            r["Longitud"] = lon
            if pais_c and not r.get("País Limítrofe"):
                r["País Limítrofe"] = pais_c
            if medio_c and not r.get("Medio"):
                r["Medio"] = medio_c
            limpios.append(r)

        if "Latitud" not in fields:
            fields.extend(["Latitud", "Longitud"])
        guardar_csv(path, limpios, fields)
        print(f"[OK] Auditado {path}: {len(limpios)} registros válidos.")

    # 2. Actualizar con datos oficiales del día si están disponibles
    oficiales = extraer_datos_oficiales()
    if oficiales:
        rows_cons, fields_cons = leer_csv(FILES["consolidado"])
        if not fields_cons:
            fields_cons = [
                "Fecha", "Hora Corte", "Nombre del Paso", "Provincia",
                "País Limítrofe", "Medio", "Estado", "Latitud", "Longitud"
            ]
        rows_cons = [r for r in rows_cons if r.get("Fecha") != fecha_hoy and not es_paso_ignorado(r.get("Nombre del Paso", ""))]
        usados_hoy = set()
        nuevos_hoy = []
        for item in oficiales:
            lat, lon, prov_c, pais_c, medio_c, _ = georreferenciar_paso(
                item["nombre"], item["provincia"], item["lat"], item["lon"], usados_hoy
            )
            nuevos_hoy.append({
                "Fecha": fecha_hoy,
                "Hora Corte": hora_corte,
                "Nombre del Paso": item["nombre"],
                "Provincia": formatear_provincia(prov_c, item["provincia"]),
                "País Limítrofe": pais_c or "Limítrofe",
                "Medio": medio_c or "Terrestre / Fluvial",
                "Estado": item["estado"],
                "Latitud": lat,
                "Longitud": lon,
            })
        rows_cons.extend(nuevos_hoy)
        guardar_csv(FILES["consolidado"], rows_cons, fields_cons)
        print(f"[OK] Actualizados {len(nuevos_hoy)} pasos para {fecha_hoy}.")


if __name__ == "__main__":
    auditar_y_actualizar()
