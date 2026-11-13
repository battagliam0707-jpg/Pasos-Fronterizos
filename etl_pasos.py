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

URL_SEGURIDAD = "https://www.argentina.gob.ar/seguridad/pasosinternacionales"
URL_MIGRACIONES = "https://www.argentina.gob.ar/migraciones/pasos-internacionales"

FILES = {
    "maestro": "maestro_pasos.csv",
    "historico": "historico_diario_2026.csv",
    "consolidado": "consolidado_pasos_2026.csv",
}


def norm(texto: str) -> str:
    if not texto:
        return ""
    s = str(texto).replace("\ufeff", "")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-zA-Z0-9\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def get_col(row: dict, aliases: list) -> str:
    if not row:
        return ""
    norm_map = {norm(k): v for k, v in row.items() if k is not None}
    for a in aliases:
        val = norm_map.get(norm(a))
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    return ""


# 17 pasos marcados con "NO" en auditoría (excluidos)
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

# (clave, prov, lat, lon, pais, medio, nombre_canonico)
CATALOGO_AUDITADO = [
    ("la quiaca", "jujuy", -22.0967, -65.5964, "Bolivia", "Terrestre", "La Quiaca - Villazón"),
    ("jama norte", "jujuy", -23.227301, -67.063069, "Chile", "Terrestre", "Jama Norte"),
    ("jama sur", "jujuy", -23.233026, -67.064815, "Chile", "Terrestre", "Jama Sur"),
    ("jama", "jujuy", -23.2350, -67.0650, "Chile", "Terrestre", "Paso de Jama"),
    ("horacio guzman", "jujuy", -24.3928, -65.0978, "Internacional", "Aéreo", "Aeropuerto Internacional Gobernador Horacio Guzmán"),
    ("salvador mazza", "salta", -22.0533, -63.6836, "Bolivia", "Terrestre", "Salvador Mazza - Yacuiba"),
    ("aguas blancas", "salta", -22.7264, -64.3700, "Bolivia", "Terrestre", "Aguas Blancas - Bermejo"),
    ("chalanas", "salta", -22.7297, -64.3553, "Bolivia", "Fluvial", "Puerto Chalanas - Bermejo"),
    ("el condado", "salta", -22.4261, -64.5128, "Bolivia", "Terrestre", "El Condado - La Mamora"),
    ("mision la paz", "salta", -22.3789, -62.5214, "Paraguay", "Terrestre", "Misión La Paz - Pozo Hondo"),
    ("laguna sico", "salta", -23.874876, -67.271398, "Chile", "Terrestre", "Laguna Sico"),
    ("sico", "salta", -23.8350, -67.2600, "Chile", "Terrestre", "Paso de Sico"),
    ("socompa", "salta", -24.4444, -68.2950, "Chile", "Terrestre", "Paso Socompa"),
    ("martin miguel de guemes", "salta", -24.8560, -65.4862, "Internacional", "Aéreo", "Aeropuerto Internacional Martín Miguel de Güemes"),
    ("san francisco", "catamarca", -26.8764, -68.3031, "Chile", "Terrestre", "Paso de San Francisco"),
    ("felipe varela", "catamarca", -28.5924622103104, -65.75498297415245, "Internacional", "Aéreo", "Aeropuerto Coronel Felipe Varela"),
    ("pircas negras", "la rioja", -27.8489, -69.2556, "Chile", "Terrestre", "Paso Pircas Negras"),
    ("almandos almonacid", "la rioja", -29.383745845336172, -66.79433575463631, "Internacional", "Aéreo", "Aeropuerto Capitán Vicente Almandos Almonacid"),
    ("agua negra", "san juan", -30.1939, -69.8253, "Chile", "Terrestre", "Paso de Agua Negra"),
    ("colanguil", "san juan", -30.03275792123306, -69.29369560759945, "Chile", "Terrestre", "Paso Portillo de Colangüil"),
    ("valle hermoso", "san juan", -30.783140, -70.283190, "Chile", "Terrestre", "Paso Valle Hermoso"),
    ("domingo faustino sarmiento", "san juan", -31.5715, -68.4182, "Internacional", "Aéreo", "Aeropuerto Domingo Faustino Sarmiento"),
    ("cristo redentor", "mendoza", -32.8253, -70.0725, "Chile", "Terrestre", "Sistema Cristo Redentor"),
    ("horcones", "mendoza", -32.8253, -70.0725, "Chile", "Terrestre", "Horcones - Cristo Redentor"),
    ("pehuenche", "mendoza", -35.9817, -70.3989, "Chile", "Terrestre", "Paso Internacional Pehuenche"),
    ("vergara", "mendoza", -35.1619, -70.5117, "Chile", "Terrestre", "Paso Vergara"),
    ("planchon", "mendoza", -35.1619, -70.5117, "Chile", "Terrestre", "Paso Planchón - Vergara"),
    ("piuquenes", "mendoza", -33.613674934669646, -69.49885336487009, "Chile", "Terrestre", "Paso Portillo de Piuquenes"),
    ("cajon del maipo", "mendoza", -34.190939362717494, -69.70436192023638, "Chile", "Terrestre", "Paso Cajón del Maipo"),
    ("las damas", "mendoza", -34.884690, -70.291109, "Chile", "Terrestre", "Paso Las Damas"),
    ("plumerillo", "mendoza", -32.8317, -68.7929, "Internacional", "Aéreo", "Aeropuerto Internacional El Plumerillo"),
    ("ricardo salomon", "mendoza", -35.4936, -69.5743, "Internacional", "Aéreo", "Aeropuerto Comodoro Ricardo Salomón"),
    ("pino hachado", "neuquen", -38.6531, -70.9036, "Chile", "Terrestre", "Paso Pino Hachado"),
    ("cardenal samore", "neuquen", -40.7131, -71.9469, "Chile", "Terrestre", "Paso Cardenal Antonio Samoré"),
    ("icalma", "neuquen", -38.8381, -71.2731, "Chile", "Terrestre", "Paso Icalma"),
    ("mamuil malal", "neuquen", -39.5831, -71.4831, "Chile", "Terrestre", "Paso Mamuil Malal"),
    ("tromen", "neuquen", -39.5831, -71.4831, "Chile", "Terrestre", "Paso Mamuil Malal (Tromen)"),
    ("hua hum", "neuquen", -40.1189, -71.6689, "Chile", "Terrestre / Lacustre", "Paso Hua Hum"),
    ("carirrine", "neuquen", -39.7931, -71.6531, "Chile", "Terrestre", "Paso Carirriñe"),
    ("pichachen", "neuquen", -37.4531, -71.1231, "Chile", "Terrestre", "Paso Pichachén"),
    ("copahue", "neuquen", -37.814817101834635, -71.09336827976361, "Chile", "Terrestre", "Paso Copahue"),
    ("presidente peron", "neuquen", -38.9490, -68.1557, "Internacional", "Aéreo", "Aeropuerto Internacional Presidente Perón"),
    ("chapelco", "neuquen", -40.0754, -71.1373, "Internacional", "Aéreo", "Aeropuerto Aviador Carlos Campos"),
    ("perez rosales", "rio negro", -41.0589, -71.8169, "Chile", "Terrestre / Lacustre", "Paso Vicente Pérez Rosales"),
    ("rio manso", "rio negro", -41.5031, -71.8431, "Chile", "Terrestre", "Paso Río Manso"),
    ("vuriloche", "rio negro", -41.232810, -71.770120, "Chile", "Terrestre", "Paso Vuriloche (Tronador)"),
    ("punta colorada", "rio negro", -41.6983, -65.0078, "Internacional", "Marítimo", "Muelle Punta Colorada"),
    ("san antonio este", "rio negro", -40.8031, -64.8831, "Internacional", "Marítimo", "Puerto San Antonio Este"),
    ("luis candelaria", "rio negro", -41.1512, -71.1575, "Internacional", "Aéreo", "Aeropuerto Internacional Bariloche"),
    ("futaleufu", "chubut", -43.1786, -71.7519, "Chile", "Terrestre", "Paso Río Futaleufú"),
    ("huemules", "chubut", -45.9031, -71.6531, "Chile", "Terrestre", "Paso Huemules"),
    ("rio encuentro", "chubut", -43.5889, -71.7489, "Chile", "Terrestre", "Paso Río Encuentro"),
    ("rio puelo", "chubut", -42.1031, -71.7231, "Chile", "Terrestre", "Paso Río Puelo"),
    ("cohiue", "chubut", -45.5531, -71.7331, "Chile", "Terrestre", "Paso Cohiue - Coyhaique"),
    ("triana", "chubut", -45.572530, -71.698310, "Chile", "Terrestre", "Paso Triana"),
    ("pallavicini", "chubut", -46.0131, -71.6631, "Chile", "Terrestre", "Paso Ingeniero Pallavicini"),
    ("pampa alta", "chubut", -45.230420, -71.327320, "Chile", "Terrestre", "Paso Pampa Alta"),
    ("rio frias", "chubut", -44.579590, -71.056280, "Chile", "Terrestre", "Paso Río Frías - Cisnes"),
    ("las pampas", "chubut", -44.238770, -71.719420, "Chile", "Terrestre", "Paso Las Pampas - Lago Verde"),
    ("puerto madryn", "chubut", -42.7611, -65.0331, "Internacional", "Marítimo", "Puerto Madryn"),
    ("comodoro rivadavia", "chubut", -45.8622, -67.4631, "Internacional", "Marítimo", "Puerto Comodoro Rivadavia"),
    ("enrique mosconi", "chubut", -45.7853, -67.4656, "Internacional", "Aéreo", "Aeropuerto General Enrique Mosconi"),
    ("marco andres zar", "chubut", -43.2105, -65.2703, "Internacional", "Aéreo", "Aeropuerto Almirante Marco Andrés Zar"),
    ("tehuelche", "chubut", -42.7592, -65.1027, "Internacional", "Aéreo", "Aeropuerto El Tehuelche"),
    ("antonio parodi", "chubut", -42.9080, -71.1395, "Internacional", "Aéreo", "Aeropuerto Brigadier Antonio Parodi"),
    ("integracion austral", "santa cruz", -52.1389, -69.5181, "Chile", "Terrestre", "Paso Integración Austral"),
    ("don guillermo", "santa cruz", -51.2531, -72.2531, "Chile", "Terrestre", "Paso Río Don Guillermo"),
    ("laurita", "santa cruz", -51.6731, -72.3231, "Chile", "Terrestre", "Paso Laurita - Casas Viejas"),
    ("dorotea", "santa cruz", -51.5831, -72.3431, "Chile", "Terrestre", "Paso Dorotea"),
    ("cancha carrera", "santa cruz", -51.2631, -72.2431, "Chile", "Terrestre", "Paso Cancha Carrera"),
    ("jeinimeni", "santa cruz", -46.5431, -71.6531, "Chile", "Terrestre", "Paso Río Jeinimeni"),
    ("roballos", "santa cruz", -47.1531, -71.8531, "Chile", "Terrestre", "Paso Roballos"),
    ("mayer", "santa cruz", -48.1831, -72.3331, "Chile", "Terrestre", "Paso Mayer"),
    ("lago san martin", "santa cruz", -49.126120, -72.491900, "Chile", "Terrestre", "Paso Lago San Martín - O'Higgins"),
    ("marconi", "santa cruz", -49.331370, -72.884760, "Chile", "Terrestre", "Paso Marconi"),
    ("portezuelo de la divisoria", "santa cruz", -48.998980, -72.839410, "Chile", "Terrestre", "Paso Portezuelo de la Divisoria"),
    ("rio mosco", "santa cruz", -48.489390, -72.552780, "Chile", "Terrestre", "Paso Río Mosco"),
    ("cabo virgenes", "santa cruz", -52.3289, -68.3531, "Chile", "Terrestre", "Cabo Vírgenes"),
    ("monte dinero", "santa cruz", -52.239468, -68.883148, "Chile", "Terrestre", "Monte Dinero"),
    ("puerto rio gallegos", "santa cruz", -51.6219, -69.2131, "Internacional", "Marítimo", "Puerto Río Gallegos"),
    ("punta quilla", "santa cruz", -50.1167, -68.4167, "Internacional", "Marítimo", "Puerto Punta Quilla"),
    ("puerto santa cruz", "santa cruz", -50.0189, -68.5231, "Internacional", "Marítimo", "Puerto Santa Cruz"),
    ("san julian", "santa cruz", -49.3089, -67.7231, "Internacional", "Marítimo", "Puerto San Julián"),
    ("puerto deseado", "santa cruz", -47.7531, -65.8931, "Internacional", "Marítimo", "Puerto Deseado"),
    ("caleta olivia", "santa cruz", -46.4431, -67.5131, "Internacional", "Marítimo", "Puerto Caleta Olivia"),
    ("armando tola", "santa cruz", -50.2803, -72.0531, "Internacional", "Aéreo", "Aeropuerto Comandante Armando Tola"),
    ("norberto fernandez", "santa cruz", -51.6089, -69.3126, "Internacional", "Aéreo", "Aeropuerto Piloto Civil Norberto Fernández"),
    ("san sebastian", "tierra del fuego", -53.3189, -68.6042, "Chile", "Terrestre", "Paso San Sebastián"),
    ("radman", "tierra del fuego", -53.9931, -68.6131, "Chile", "Terrestre", "Paso Radman (Bellavista)"),
    ("puerto williams", "tierra del fuego", -54.8722, -67.5719, "Chile", "Marítimo", "Puerto Almanza - Puerto Williams"),
    ("almanza", "tierra del fuego", -54.8722, -67.5719, "Chile", "Marítimo", "Puerto Almanza - Puerto Williams"),
    ("puerto ushuaia", "tierra del fuego", -54.8089, -68.3031, "Internacional", "Marítimo", "Puerto Ushuaia"),
    ("puerto rio grande", "tierra del fuego", -53.7889, -67.6931, "Internacional", "Marítimo", "Puerto Río Grande"),
    ("malvinas argentinas", "tierra del fuego", -54.8433, -68.2958, "Internacional", "Aéreo", "Aeropuerto Internacional Malvinas Argentinas"),
    ("hermes quijada", "tierra del fuego", -53.7777, -67.7494, "Internacional", "Aéreo", "Aeropuerto Hermes Quijada"),
    ("san ignacio de loyola", "formosa", -25.2589, -57.7081, "Paraguay", "Terrestre", "Puente Internacional San Ignacio de Loyola"),
    ("la fraternidad", "formosa", -25.2869, -57.7219, "Paraguay", "Terrestre", "Pasarela La Fraternidad"),
    ("pilcomayo", "formosa", -25.3711, -57.6519, "Paraguay", "Fluvial", "Puerto Pilcomayo - Puerto Itá Enramada"),
    ("puerto formosa", "formosa", -26.1831, -58.1631, "Paraguay", "Fluvial", "Puerto Formosa - Puerto Alberdi"),
    ("colonia cano", "formosa", -26.853231, -58.324608, "Paraguay", "Fluvial", "Puerto Colonia Cano - Puerto Pilar"),
    ("el remanso", "formosa", -23.987892, -60.458126, "Paraguay", "Terrestre", "Paso El Remanso - La Verde"),
    ("isleta", "formosa", -24.212000, -60.197070, "Paraguay", "Terrestre", "Paso Isleta - Paraje Rojas Silva"),
    ("lamadrid", "formosa", -23.897911, -60.755810, "Paraguay", "Terrestre", "Paso Lamadrid - Misión San Leonardo"),
    ("general belgrano", "formosa", -24.748760, -58.833130, "Paraguay", "Terrestre", "Paso General Belgrano - General Bruguez"),
    ("el pucu", "formosa", -26.2127, -58.2281, "Internacional", "Aéreo", "Aeropuerto Internacional El Pucú"),
    ("las palmas", "chaco", -27.0819, -58.6131, "Paraguay", "Fluvial", "Puerto Las Palmas - Puerto Humaitá"),
    ("puerto bermejo", "chaco", -26.9289, -58.5031, "Paraguay", "Fluvial", "Puerto Bermejo - Puerto Pilar"),
    ("barranqueras", "chaco", -27.4831, -58.9231, "Internacional", "Fluvial", "Puerto Barranqueras"),
    ("resistencia", "chaco", -27.4500, -59.0561, "Internacional", "Aéreo", "Aeropuerto Internacional de Resistencia"),
    ("tancredo neves", "misiones", -25.5883, -54.5617, "Brasil", "Terrestre", "Centro de Frontera Iguazú - Foz de Iguazú"),
    ("puerto meira", "misiones", -25.5933, -54.5789, "Brasil", "Fluvial", "Puerto Iguazú - Puerto Meira"),
    ("tres fronteras", "misiones", -25.5939, -54.5794, "Paraguay", "Fluvial", "Puerto Iguazú - Puerto Tres Fronteras"),
    ("posadas", "misiones", -27.3689, -55.8831, "Paraguay", "Terrestre", "Centro de Frontera Posadas - Encarnación"),
    ("bernardo de irigoyen", "misiones", -26.2547, -53.6439, "Brasil", "Terrestre", "Bernardo de Irigoyen - Dionísio Cerqueira"),
    ("andresito", "misiones", -25.6017, -53.9781, "Brasil", "Terrestre", "Comandante Andresito - Capanema"),
    ("san antonio", "misiones", -26.0567, -53.7306, "Brasil", "Terrestre", "San Antonio - Santo Antônio"),
    ("integracion", "misiones", -25.7689, -53.8511, "Brasil", "Fluvial", "Integración - Planalto"),
    ("pepiri guazu", "misiones", -26.6131, -53.7347, "Brasil", "Terrestre", "Pepirí Guazú - São Miguel"),
    ("el soberbio", "misiones", -27.3000, -54.1917, "Brasil", "Fluvial", "Puerto El Soberbio - Porto Soberbo"),
    ("alicia", "misiones", -27.4639, -54.3442, "Brasil", "Fluvial", "Puerto Alicia - San Antonio"),
    ("aurora", "misiones", -27.4994, -54.5206, "Brasil", "Fluvial", "Puerto Aurora - Pratos"),
    ("alba posse", "misiones", -27.5714, -54.6792, "Brasil", "Fluvial", "Puerto Alba Posse - Porto Mauá"),
    ("barra bonita", "misiones", -27.6014, -54.8431, "Brasil", "Fluvial", "Puerto Barra Bonita - M'Biguá"),
    ("panambi", "misiones", -27.7300, -54.9142, "Brasil", "Fluvial", "Puerto Panambí - Porto Vera Cruz"),
    ("paso de la barca", "misiones", -27.8919, -55.1356, "Brasil", "Fluvial", "Paso de la Barca (San Javier - Porto Xavier)"),
    ("santa maria", "misiones", -27.9678, -55.3508, "Brasil", "Fluvial", "Puerto Santa María - Colonia Florida"),
    ("san isidro", "misiones", -28.0503, -55.4158, "Brasil", "Fluvial", "Puerto San Isidro - San Isidro"),
    ("puerto libertad", "misiones", -25.9647, -54.6039, "Paraguay", "Fluvial", "Puerto Libertad - Domingo Martínez de Irala"),
    ("puerto wanda", "misiones", -25.9650, -54.6042, "Paraguay", "Fluvial", "Puerto Wanda - Puerto Itá Verá"),
    ("puerto mado", "misiones", -26.2303, -54.6294, "Paraguay", "Fluvial", "Puerto Mado - Lomas Valentinas"),
    ("puerto victoria", "misiones", -26.3350, -54.4667, "Paraguay", "Fluvial", "Puerto Victoria - Capitán Urbina"),
    ("puerto pinares", "misiones", -26.4061, -54.6939, "Paraguay", "Fluvial", "Puerto Pinares - Carlos Antonio López"),
    ("eldorado", "misiones", -26.4064, -54.6939, "Paraguay", "Fluvial", "Puerto Eldorado - Mayor Julio Otaño"),
    ("puerto piray", "misiones", -26.4728, -54.7261, "Paraguay", "Fluvial", "Puerto Piray - Puerto 7 de Agosto"),
    ("montecarlo", "misiones", -26.5594, -54.8039, "Paraguay", "Fluvial", "Puerto Montecarlo - Ape Aimé"),
    ("paranay", "misiones", -26.6744, -54.8150, "Paraguay", "Fluvial", "Puerto Paranay - Colonia Alborada"),
    ("puerto rico", "misiones", -26.7919, -55.0244, "Paraguay", "Fluvial", "Puerto Rico - Puerto Triunfo"),
    ("garuhape", "misiones", -26.7914, -55.0247, "Paraguay", "Fluvial", "Puerto Garuhapé - Puerto 3 de Mayo"),
    ("puerto leoni", "misiones", -26.9831, -55.1631, "Paraguay", "Fluvial", "Puerto Leoni - Puerto Triunfo"),
    ("puerto oasis", "misiones", -27.0131, -55.2631, "Paraguay", "Fluvial", "Puerto Oasis - Capitán Meza"),
    ("puerto mani", "misiones", -27.1431, -55.4831, "Paraguay", "Fluvial", "Puerto Maní - Bella Vista Sur"),
    ("san ignacio", "misiones", -27.2531, -55.5531, "Paraguay", "Fluvial", "Puerto San Ignacio - Puerto Paraíso"),
    ("santa ana", "misiones", -27.3531, -55.5931, "Paraguay", "Fluvial", "Puerto Santa Ana"),
    ("candelaria", "misiones", -27.4531, -55.7431, "Paraguay", "Fluvial", "Puerto Candelaria"),
    ("cataratas del iguazu", "misiones", -25.7373, -54.4734, "Internacional", "Aéreo", "Aeropuerto Internacional Cataratas del Iguazú"),
    ("paso de los libres", "corrientes", -29.7336, -57.1019, "Brasil", "Terrestre", "Centro de Frontera Paso de los Libres - Uruguayana"),
    ("santo tome", "corrientes", -28.6106, -56.0144, "Brasil", "Terrestre", "Paso Internacional Santo Tomé - São Borja"),
    ("puerto alvear", "corrientes", -29.1133, -56.5553, "Brasil", "Fluvial", "Puerto Alvear - Puerto Itaquí"),
    ("puerto la cruz", "corrientes", -29.1783, -56.6336, "Brasil", "Fluvial", "Puerto La Cruz - Puerto Itaquí"),
    ("yapeyu", "corrientes", -29.4700, -56.8111, "Brasil", "Fluvial", "Puerto Yapeyú - Puerto San Marcos"),
    ("garruchos", "corrientes", -28.1772, -55.6425, "Brasil", "Fluvial", "Puerto Garruchos - Garruchos"),
    ("monte caseros", "corrientes", -30.2519, -57.6222, "Uruguay", "Fluvial", "Puerto Monte Caseros - Bella Unión"),
    ("ituzaingo", "corrientes", -27.5831, -56.6831, "Paraguay", "Fluvial", "Puerto Ituzaingó - Ayolas"),
    ("yacyreta", "corrientes", -27.4831, -56.7331, "Paraguay", "Terrestre", "Coronamiento Represa Yacyretá"),
    ("apipe", "corrientes", -27.5131, -56.7431, "Paraguay", "Fluvial", "Puerto San Antonio de Apipé"),
    ("ita ibate", "corrientes", -27.4331, -57.3331, "Paraguay", "Fluvial", "Puerto Itá Ibaté - Panchito López"),
    ("yahape", "corrientes", -27.3731, -57.6531, "Paraguay", "Fluvial", "Puerto Yahapé - Puerto Cerrito"),
    ("itati", "corrientes", -27.2689, -58.2431, "Paraguay", "Fluvial", "Puerto Itatí - Puerto Itá Corá"),
    ("paso de la patria", "corrientes", -27.3189, -58.5731, "Paraguay", "Fluvial", "Puerto Paso de la Patria"),
    ("puerto corrientes", "corrientes", -27.4631, -58.8331, "Internacional", "Fluvial", "Puerto Corrientes"),
    ("goya", "corrientes", -29.1431, -59.2731, "Internacional", "Fluvial", "Puerto Goya"),
    ("esquina", "corrientes", -30.0131, -59.5331, "Internacional", "Fluvial", "Puerto Esquina"),
    ("piragine niveyro", "corrientes", -27.4455, -58.7619, "Internacional", "Aéreo", "Aeropuerto Internacional de Corrientes"),
    ("gualeguaychu", "entre rios", -33.0947, -58.2464, "Uruguay", "Terrestre", "Puente Internacional Gualeguaychú - Fray Bentos"),
    ("colon", "entre rios", -32.2656, -58.0939, "Uruguay", "Terrestre", "Puente Internacional Colón - Paysandú"),
    ("concordia", "entre rios", -31.2608, -57.9617, "Uruguay", "Terrestre", "Centro de Frontera Concordia - Salto"),
    ("boca de gualeguaychu", "entre rios", -33.0739, -58.4206, "Uruguay", "Fluvial", "Puerto Boca de Gualeguaychú"),
    ("concepcion del uruguay", "entre rios", -32.4833, -58.2222, "Uruguay", "Fluvial", "Puerto Concepción del Uruguay"),
    ("puerto colon", "entre rios", -32.2156, -58.1367, "Uruguay", "Fluvial", "Puerto Colón - Puerto Paysandú"),
    ("puerto concordia", "entre rios", -31.4022, -58.0036, "Uruguay", "Fluvial", "Puerto Concordia - Salto"),
    ("puerto luis", "entre rios", -31.209789, -57.930817, "Uruguay", "Fluvial", "Puerto Luis - Constitución"),
    ("federacion", "entre rios", -30.9931, -57.9153, "Uruguay", "Fluvial", "Puerto Federación - Villa Constitución"),
    ("santa eloisa", "entre rios", -30.761606, -57.822300, "Uruguay", "Fluvial", "Puerto Santa Eloísa - Belén"),
    ("paranacito", "entre rios", -33.7161, -58.6594, "Uruguay", "Fluvial", "Puerto Paranacito - Nueva Palmira"),
    ("guazucito", "entre rios", -34.0147, -58.3956, "Uruguay", "Fluvial", "Puerto Guazú Guazucito - Carmelo"),
    ("portuaria del guazu", "entre rios", -33.8931, -58.8831, "Internacional", "Fluvial", "Terminal Portuaria del Guazú"),
    ("ibicuy", "entre rios", -33.7531, -59.1731, "Internacional", "Fluvial", "Puerto Ibicuy"),
    ("diamante", "entre rios", -32.0631, -60.6431, "Internacional", "Fluvial", "Puerto Diamante"),
    ("puerto de parana", "entre rios", -31.7092, -60.5736, "Internacional", "Fluvial", "Puerto de Paraná"),
    ("la paz", "entre rios", -30.6672, -59.6403, "Internacional", "Fluvial", "Puerto de La Paz"),
    ("puerto rosario", "santa fe", -32.9442, -60.6306, "Internacional", "Fluvial", "Puerto Rosario"),
    ("san lorenzo", "santa fe", -32.7431, -60.7331, "Internacional", "Fluvial", "Puerto San Lorenzo"),
    ("general san martin", "santa fe", -32.7131, -60.7231, "Internacional", "Fluvial", "Puerto General San Martín"),
    ("villa constitucion", "santa fe", -33.2231, -60.3231, "Internacional", "Fluvial", "Puerto Villa Constitución"),
    ("puerto santa fe", "santa fe", -31.6489, -60.7031, "Internacional", "Fluvial", "Puerto Santa Fe"),
    ("islas malvinas", "santa fe", -32.9036, -60.7850, "Internacional", "Aéreo", "Aeropuerto Internacional de Rosario"),
    ("martin garcia", "buenos aires", -34.1889, -58.2553, "Uruguay", "Fluvial", "Puerto Martín García - Carmelo"),
    ("buquebus", "caba", -34.6118, -58.3622, "Uruguay", "Fluvial", "Terminal Portuaria Buquebus"),
    ("colonia express", "caba", -34.6231, -58.3569, "Uruguay", "Fluvial", "Terminal Portuaria Colonia Express"),
    ("quinquela martin", "caba", -34.5858, -58.3711, "Internacional", "Marítimo / Fluvial", "Terminal de Cruceros Quinquela Martín"),
    ("puerto de buenos aires", "caba", -34.5889, -58.3689, "Internacional", "Marítimo / Fluvial", "Puerto de Buenos Aires"),
    ("tigre", "buenos aires", -34.4219, -58.5819, "Uruguay", "Fluvial", "Terminal Fluvial Binacional de Tigre"),
    ("campana", "buenos aires", -34.1531, -58.9531, "Internacional", "Fluvial", "Puerto Campana"),
    ("zarate", "buenos aires", -34.0931, -59.0231, "Internacional", "Fluvial", "Puerto Zárate"),
    ("san nicolas", "buenos aires", -33.3231, -60.2131, "Internacional", "Fluvial", "Puerto San Nicolás"),
    ("mar del plata", "buenos aires", -38.0331, -57.5331, "Internacional", "Marítimo", "Puerto Mar del Plata"),
    ("quequen", "buenos aires", -38.5731, -58.7031, "Internacional", "Marítimo", "Puerto Quequén"),
    ("bahia blanca", "buenos aires", -38.7831, -62.2631, "Internacional", "Marítimo", "Puerto Bahía Blanca"),
    ("ezeiza", "buenos aires", -34.8222, -58.5358, "Internacional", "Aéreo", "Aeropuerto Internacional de Ezeiza"),
    ("jorge newbery", "caba", -34.5592, -58.4156, "Internacional", "Aéreo", "Aeroparque Internacional Jorge Newbery"),
    ("ambrosio taravella", "cordoba", -31.3236, -64.2081, "Internacional", "Aéreo", "Aeropuerto Internacional de Córdoba"),
]

CATALOGO_AUDITADO.sort(key=lambda x: len(x[0]), reverse=True)

TITULOS_PROVINCIA = {
    "jujuy": "Jujuy", "salta": "Salta", "catamarca": "Catamarca", "la rioja": "La Rioja",
    "san juan": "San Juan", "mendoza": "Mendoza", "neuquen": "Neuquén", "rio negro": "Río Negro",
    "chubut": "Chubut", "santa cruz": "Santa Cruz", "tierra del fuego": "Tierra del Fuego",
    "formosa": "Formosa", "chaco": "Chaco", "misiones": "Misiones", "corrientes": "Corrientes",
    "entre rios": "Entre Ríos", "santa fe": "Santa Fe", "buenos aires": "Buenos Aires",
    "caba": "CABA", "cordoba": "Córdoba", "tucuman": "Tucumán",
    "santiago del estero": "Santiago del Estero", "san luis": "San Luis", "la pampa": "La Pampa",
}


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
    for clave, prov, _, _, _, _, _ in CATALOGO_AUDITADO:
        if clave in n_nom:
            return prov
    return n_prov


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

    for clave, prov_cat, lat_cat, lon_cat, pais_cat, medio_cat, _ in CATALOGO_AUDITADO:
        if clave in n_nom:
            if not prov_canon or prov_canon == prov_cat or (prov_canon in ("caba", "buenos aires") and prov_cat in ("caba", "buenos aires")):
                lat_f, lon_f = desagregar_superposicion(nombre, lat_cat, lon_cat, usados)
                return lat_f, lon_f, prov_cat, pais_cat, medio_cat

    try:
        if lat_actual is not None and lon_actual is not None:
            la = float(str(lat_actual).replace(",", "."))
            lo = float(str(lon_actual).replace(",", "."))
            perfil = PERFILES_PROVINCIALES.get(prov_canon)
            if perfil and perfil[0] <= la <= perfil[1] and perfil[2] <= lo <= perfil[3]:
                lat_f, lon_f = desagregar_superposicion(nombre, la, lo, usados)
                return lat_f, lon_f, prov_canon, "", ""
    except (ValueError, TypeError):
        pass

    perfil = PERFILES_PROVINCIALES.get(prov_canon)
    if perfil:
        lat_f, lon_f = desagregar_superposicion(nombre, perfil[4], perfil[5], usados)
        return lat_f, lon_f, prov_canon, "", ""

    return -34.6037, -58.3816, prov_canon, "", ""


def descargar_url(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (ObservatorioPasosAR/2026)"})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def extraer_datos_oficiales():
    registros = []
    # 1. Intentar extraer Google Sheet o JSON de Poncho en Ministerio de Seguridad
    try:
        html = descargar_url(URL_SEGURIDAD)
        sheet_ids = re.findall(r"spreadsheets/d/([a-zA-Z0-9-_]{20,})", html)
        for sid in dict.fromkeys(sheet_ids):
            csv_url = f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv"
            raw_csv = descargar_url(csv_url)
            reader = csv.DictReader(io.StringIO(raw_csv))
            for r in reader:
                nom = get_col(r, ["nombre", "titulo", "title", "paso", "nombre del paso"])
                if not nom or es_paso_ignorado(nom):
                    continue
                registros.append({
                    "nombre": nom,
                    "provincia": get_col(r, ["provincia", "jurisdiccion", "distrito"]),
                    "pais": get_col(r, ["pais", "pais limitrofe", "frontera"]),
                    "estado": get_col(r, ["estado", "situacion", "condicion", "habilitado"]) or "Habilitado",
                    "lat": get_col(r, ["lat", "latitud", "latitude"]),
                    "lon": get_col(r, ["lon", "lng", "longitud", "longitude"]),
                })
            if registros:
                return registros
    except Exception as e:
        print(f"[AVISO] Extracción Seguridad: {e}")

    # 2. Respaldo desde el listado oficial de Migraciones + catálogo auditado
    try:
        html_mig = descargar_url(URL_MIGRACIONES)
        texto = re.sub(r"<[^>]+>", "\n", html_mig)
        prov_actual = ""
        for linea in texto.splitlines():
            l = linea.strip()
            if not l:
                continue
            if l.endswith(":") and len(l) < 45:
                prov_actual = l[:-1].strip()
                continue
            if prov_actual and any(p in norm(l) for p in ["puerto", "paso", "aeropuerto", "puente", "centro de frontera", "terminal", "muelle", "hua hum", "icalma", "mamuil", "sico", "futaleufu", "huemules", "pino hachado", "samore", "dorotea", "jeinimeni", "integracion", "san sebastian", "bernardo de irigoyen"]):
                nom_limpio = re.sub(r"\(.*?\)", "", l).strip(" .")
                if nom_limpio and not es_paso_ignorado(nom_limpio):
                    registros.append({
                        "nombre": nom_limpio,
                        "provincia": prov_actual,
                        "pais": "",
                        "estado": "Habilitado",
                        "lat": None,
                        "lon": None,
                    })
    except Exception as e:
        print(f"[AVISO] Extracción Migraciones: {e}")

    return registros


def leer_csv(path: str):
    if not os.path.exists(path):
        return [], []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        if not sample.strip():
            return [], []
        delim = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.DictReader(f, delimiter=delim)
        return list(reader), (reader.fieldnames or [])


def guardar_csv(path: str, rows: list, fieldnames: list):
    if not rows:
        print(f"[PROTECCIÓN] Se evitó guardar archivo vacío en {path}.")
        return
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def auditar_y_actualizar():
    ahora_ar = datetime.now(TZ_AR)
    fecha_hoy = ahora_ar.strftime("%Y-%m-%d")
    hora_corte = ahora_ar.strftime("%Y-%m-%d %H:%M:%S")

    std_fields = [
        "Fecha", "Hora Corte", "Nombre del Paso", "Provincia",
        "País Limítrofe", "Medio", "Estado", "Latitud", "Longitud"
    ]

    # 1. Auditar filas existentes si las hubiera (sin borrar nunca si quedan 0)
    for key_csv in ("consolidado", "historico"):
        path = FILES[key_csv]
        rows, fields = leer_csv(path)
        if not rows:
            continue
        limpios = []
        usados_archivo = set()
        for r in rows:
            nombre = get_col(r, ["nombre del paso", "paso", "paso fronterizo", "nombre", "title"])
            if not nombre or es_paso_ignorado(nombre):
                continue
            prov_raw = get_col(r, ["provincia", "jurisdiccion"])
            lat_raw = get_col(r, ["latitud", "lat"])
            lon_raw = get_col(r, ["longitud", "lon", "lng"])
            lat, lon, prov_c, pais_c, medio_c = georreferenciar_paso(
                nombre, prov_raw, lat_raw, lon_raw, usados_archivo
            )
            limpios.append({
                "Fecha": get_col(r, ["fecha", "fecha reporte"]) or fecha_hoy,
                "Hora Corte": get_col(r, ["hora corte", "hora"]) or hora_corte,
                "Nombre del Paso": nombre,
                "Provincia": TITULOS_PROVINCIA.get(prov_c, prov_raw or "Sin especificar"),
                "País Limítrofe": get_col(r, ["pais limitrofe", "pais"]) or pais_c or "Limítrofe",
                "Medio": get_col(r, ["medio", "tipo"]) or medio_c or "Terrestre / Fluvial",
                "Estado": get_col(r, ["estado", "situacion"]) or "Habilitado",
                "Latitud": lat,
                "Longitud": lon,
            })
        if limpios:
            guardar_csv(path, limpios, std_fields)

    # 2. Extraer datos oficiales del día (o poblar desde catálogo maestro auditado)
    oficiales = extraer_datos_oficiales()
    usados_hoy = set()
    nuevos_hoy = []

    if oficiales:
        for item in oficiales:
            lat, lon, prov_c, pais_c, medio_c = georreferenciar_paso(
                item["nombre"], item["provincia"], item["lat"], item["lon"], usados_hoy
            )
            nuevos_hoy.append({
                "Fecha": fecha_hoy,
                "Hora Corte": hora_corte,
                "Nombre del Paso": item["nombre"],
                "Provincia": TITULOS_PROVINCIA.get(prov_c, item["provincia"] or "Sin especificar"),
                "País Limítrofe": item["pais"] or pais_c or "Limítrofe",
                "Medio": medio_c or "Terrestre / Fluvial",
                "Estado": item["estado"] or "Habilitado",
                "Latitud": lat,
                "Longitud": lon,
            })
    else:
        cerrados_def = {"puerto williams", "san francisco", "pircas negras", "agua negra", "sico", "socompa"}
        for _, prov_c, lat, lon, pais_c, medio_c, nombre_can in CATALOGO_AUDITADO:
            es_cer = any(c in norm(nombre_can) for c in cerrados_def)
            nuevos_hoy.append({
                "Fecha": fecha_hoy,
                "Hora Corte": hora_corte,
                "Nombre del Paso": nombre_can,
                "Provincia": TITULOS_PROVINCIA.get(prov_c, prov_c),
                "País Limítrofe": pais_c,
                "Medio": medio_c,
                "Estado": "Cerrado" if es_cer else "Habilitado",
                "Latitud": round(lat, 6),
                "Longitud": round(lon, 6),
            })

    # Guardar maestro_pasos.csv
    guardar_csv(FILES["maestro"], nuevos_hoy, std_fields)

    # Actualizar consolidado_pasos_2026.csv e historico_diario_2026.csv
    for key_csv in ("consolidado", "historico"):
        rows_prev, _ = leer_csv(FILES[key_csv])
        rows_prev = [
            r for r in rows_prev
            if get_col(r, ["fecha"]) != fecha_hoy and not es_paso_ignorado(get_col(r, ["nombre del paso", "paso", "nombre"]))
        ]
        rows_prev.extend(nuevos_hoy)
        guardar_csv(FILES[key_csv], rows_prev, std_fields)

    print(f"[OK] Proceso completado ({fecha_hoy}): {len(nuevos_hoy)} pasos activos/auditados.")


if __name__ == "__main__":
    auditar_y_actualizar()
