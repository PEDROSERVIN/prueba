import io
import zipfile
import unicodedata
import urllib.parse
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor

import requests
import streamlit as st
from bs4 import BeautifulSoup
from pypdf import PdfReader

OCR_DISPONIBLE = True
try:
    import pytesseract
    from pdf2image import convert_from_bytes
except ImportError:
    OCR_DISPONIBLE = False

st.set_page_config(page_title="Buscador Táctico en PDFs", layout="wide", page_icon="⚽")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Reemplazá esto por la URL real de tu app en Streamlit Cloud, así los links
# para compartir búsquedas apuntan a donde corresponde.
APP_BASE_URL = "https://buscadorpdf.streamlit.app"


# --------------------------------------------------------------------------
# UTILIDADES DE TEXTO
# --------------------------------------------------------------------------
def normalizar(texto):
    texto = texto.lower()
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def extraer_fragmento(texto, palabra, contexto=100):
    idx = normalizar(texto).find(normalizar(palabra))
    if idx == -1:
        return ""
    inicio = max(0, idx - contexto)
    fin = min(len(texto), idx + len(palabra) + contexto)
    frag = texto[inicio:fin].replace("\n", " ").strip()
    return ("…" if inicio > 0 else "") + frag + ("…" if fin < len(texto) else "")


# --------------------------------------------------------------------------
# RESOLUCIÓN Y CLASIFICACIÓN DE ENLACES
# --------------------------------------------------------------------------
def resolver_yandex(url_publica):
    try:
        r = requests.get(
            "https://cloud-api.yandex.net/v1/disk/public/resources/download",
            params={"public_key": url_publica}, headers=HEADERS, timeout=15,
        )
        if r.status_code == 200:
            return r.json().get("href")
    except Exception:
        pass
    return None


def clasificar_enlace(url):
    host = urlparse(url).netloc.lower()
    if "disk.yandex" in host or "yadi.sk" in host:
        return "yandex"
    if "drive.google.com" in host:
        return "gdrive"
    if url.lower().split("?")[0].endswith(".pdf"):
        return "pdf"
    return None


def obtener_url_directa(url, tipo):
    if tipo == "yandex":
        return resolver_yandex(url)
    if tipo == "gdrive":
        import re
        m = re.search(r"/d/([\w-]+)", url) or re.search(r"[?&]id=([\w-]+)", url)
        if m:
            return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
        return url
    return url


def _tiene_prefijo_duplicado(partes):
    """Detecta patrones tipo /a/b/a/b/... que indican un link relativo mal armado."""
    n = len(partes)
    for k in range(1, n // 2 + 1):
        if partes[:k] == partes[k:2 * k]:
            return True
    return False


def resolver_absoluto(base_url, href):
    """Como urljoin, pero corrige un bug frecuente en sitios de listados: links
    relativos escritos como si siempre se sirvieran desde la raíz del dominio,
    que al resolverse normalmente duplican el directorio actual."""
    absoluto = urljoin(base_url, href)
    partes = [p for p in urlparse(absoluto).path.split("/") if p]
    if _tiene_prefijo_duplicado(partes):
        parsed = urlparse(base_url)
        raiz = f"{parsed.scheme}://{parsed.netloc}/"
        return urljoin(raiz, href.lstrip("/"))
    return absoluto.split("#")[0]


# --------------------------------------------------------------------------
# CRAWLER RECURSIVO (con profundidad) — arregla el bug de "no encuentra nada"
# --------------------------------------------------------------------------
def rastrear_documentos(url_inicial, profundidad_max, limite_docs, log=None, mismo_directorio=True):
    visitados = set()
    documentos = []
    dominio = urlparse(url_inicial).netloc
    directorio_base = url_inicial.rsplit("/", 1)[0] + "/"

    def visitar(url, prof):
        url = url.split("#")[0]
        if url in visitados or len(documentos) >= limite_docs:
            return
        visitados.add(url)

        tipo = clasificar_enlace(url)
        if tipo:
            documentos.append({"etiqueta": url.rsplit("/", 1)[-1][:50], "url": url, "tipo": tipo})
            if log:
                log(f"Documento encontrado: {url}")
            return

        if prof <= 0:
            return
        if log:
            log(f"Explorando página: {url}")
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
        except Exception as e:
            if log:
                log(f"  No se pudo abrir ({e})")
            return
        if "text/html" not in r.headers.get("Content-Type", ""):
            return

        soup = BeautifulSoup(r.text, "html.parser")
        titulo = soup.title.string.strip() if soup.title and soup.title.string else url

        for i, a in enumerate(soup.find_all("a", href=True)):
            if len(documentos) >= limite_docs:
                break
            absoluto = resolver_absoluto(url, a["href"])
            tipo2 = clasificar_enlace(absoluto)
            if tipo2:
                if absoluto in visitados:
                    continue
                visitados.add(absoluto)
                documentos.append({"etiqueta": f"{titulo} · doc {i+1}", "url": absoluto, "tipo": tipo2})
                if log:
                    log(f"Documento encontrado: {absoluto}")
                continue
            if urlparse(absoluto).netloc != dominio:
                continue
            if mismo_directorio and not absoluto.startswith(directorio_base):
                continue
            visitar(absoluto, prof - 1)

    visitar(url_inicial, profundidad_max)
    return documentos


# --------------------------------------------------------------------------
# NAVEGADOR JERÁRQUICO (para explorar magazines/ -> especiales/décadas -> años -> tomos)
# --------------------------------------------------------------------------
def listar_nivel(url):
    """Devuelve (hijos, es_nivel_de_tomos). hijos = [(texto, url_absoluta)]."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
    except Exception:
        return [], False
    soup = BeautifulSoup(r.text, "html.parser")
    dominio = urlparse(url).netloc
    hijos, vistos = [], set()
    es_tomos = False
    for a in soup.find_all("a", href=True):
        absoluto = resolver_absoluto(url, a["href"])
        if absoluto in vistos:
            continue
        tipo = clasificar_enlace(absoluto)
        if tipo:
            es_tomos = True
            continue
        if urlparse(absoluto).netloc != dominio:
            continue
        texto = a.get_text(strip=True) or absoluto.rsplit("/", 1)[-1]
        vistos.add(absoluto)
        hijos.append((texto, absoluto))
    return hijos, es_tomos


def listar_tomos(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
    except Exception:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    tomos, vistos = [], set()
    for i, a in enumerate(soup.find_all("a", href=True)):
        absoluto = resolver_absoluto(url, a["href"])
        tipo = clasificar_enlace(absoluto)
        if tipo and absoluto not in vistos:
            vistos.add(absoluto)
            texto = a.get_text(strip=True) or f"Tomo {i+1}"
            tomos.append({"etiqueta": texto, "url": absoluto, "tipo": tipo})
    return tomos


# --------------------------------------------------------------------------
# DESCARGA + BÚSQUEDA DENTRO DEL PDF
# --------------------------------------------------------------------------
def descargar_a_memoria(url_directa, tope_mb, log=None, etiqueta=""):
    try:
        with requests.get(url_directa, headers=HEADERS, stream=True, timeout=40) as r:
            r.raise_for_status()
            buf = io.BytesIO()
            limite_bytes = tope_mb * 1024 * 1024
            for chunk in r.iter_content(chunk_size=262144):
                if not chunk:
                    continue
                buf.write(chunk)
                if buf.tell() > limite_bytes:
                    if log:
                        log(f"  Omitido ({etiqueta}): supera el tope de {tope_mb} MB.")
                    return None
            buf.seek(0)
            return buf
    except Exception as e:
        if log:
            log(f"  Error descargando {etiqueta}: {e}")
        return None


def ocr_pagina(pdf_bytes, num_pagina):
    try:
        imgs = convert_from_bytes(pdf_bytes, first_page=num_pagina, last_page=num_pagina, dpi=150)
        if imgs:
            return pytesseract.image_to_string(imgs[0], lang="spa")
    except Exception:
        pass
    return ""


def procesar_pdf(buf, palabra, usar_ocr, log=None, etiqueta=""):
    try:
        reader = PdfReader(buf)
    except Exception as e:
        if log:
            log(f"  No se pudo leer el PDF ({etiqueta}): {e}")
        return []

    textos, paginas_vacias = {}, []
    for i, pagina in enumerate(reader.pages, 1):
        try:
            texto = pagina.extract_text() or ""
        except Exception:
            texto = ""
        textos[i] = texto
        if not texto.strip():
            paginas_vacias.append(i)

    if usar_ocr and OCR_DISPONIBLE and paginas_vacias:
        pdf_bytes = buf.getvalue()
        total = len(paginas_vacias)
        hechas = 0
        # Concurrencia baja a propósito: el free tier de Streamlit Cloud
        # tiene poco CPU/RAM, más hilos no lo hace más rápido, lo cuelga.
        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = {ex.submit(ocr_pagina, pdf_bytes, p): p for p in paginas_vacias}
            for f in futs:
                textos[futs[f]] = f.result()
                hechas += 1
                if log and hechas % 5 == 0:
                    log(f"  OCR {etiqueta}: {hechas}/{total} páginas escaneadas")
    elif paginas_vacias and not usar_ocr and log:
        log(f"  Aviso: {len(paginas_vacias)} página(s) sin texto en {etiqueta} (activá OCR para leerlas)")

    resultados = []
    for i in sorted(textos):
        texto = textos[i]
        if texto and normalizar(palabra) in normalizar(texto):
            resultados.append((i, extraer_fragmento(texto, palabra)))
    return resultados


# --------------------------------------------------------------------------
# LOGGER SIMPLE PARA MOSTRAR EL PROCESO EN VIVO
# --------------------------------------------------------------------------
class RegistroEnVivo:
    def __init__(self, placeholder):
        self.placeholder = placeholder
        self.lineas = []

    def log(self, msg):
        self.lineas.append(msg)
        self.placeholder.text("\n".join(self.lineas[-40:]))


def ejecutar_busqueda(documentos, palabra, usar_ocr, tope_mb, registro):
    resultados_totales = []
    barra = st.progress(0)
    for idx, doc in enumerate(documentos):
        registro.log(f"Procesando: {doc['etiqueta']}")
        url_directa = obtener_url_directa(doc["url"], doc["tipo"])
        if not url_directa:
            registro.log(f"  No se pudo resolver el enlace de {doc['etiqueta']}")
            barra.progress((idx + 1) / len(documentos))
            continue
        buf = descargar_a_memoria(url_directa, tope_mb, registro.log, doc["etiqueta"])
        if buf is None:
            barra.progress((idx + 1) / len(documentos))
            continue
        matches = procesar_pdf(buf, palabra, usar_ocr, registro.log, doc["etiqueta"])
        buf.close()
        if matches:
            registro.log(f"  -> {len(matches)} coincidencia(s) en {doc['etiqueta']}")
            for pagina, frag in matches:
                resultados_totales.append((doc["etiqueta"], pagina, frag, doc["url"]))
        barra.progress((idx + 1) / len(documentos))
    registro.log("Búsqueda finalizada.")
    return resultados_totales


def mostrar_resultados(resultados):
    if not resultados:
        st.warning("Se revisaron los documentos pero no se encontró la palabra.")
        return
    st.success(f"{len(resultados)} coincidencia(s) encontradas.")
    for etiqueta, pagina, frag, url in resultados:
        st.markdown(f"**{etiqueta} — página {pagina}**")
        st.write(frag)
        st.markdown(f"[Abrir documento original]({url})")
        st.divider()


# --------------------------------------------------------------------------
# LINK PARA COMPARTIR UNA BÚSQUEDA
# --------------------------------------------------------------------------
def link_para_compartir(url, palabra, profundidad, max_docs, usar_ocr):
    params = {
        "url": url, "palabra": palabra, "profundidad": str(profundidad),
        "max_docs": str(max_docs), "ocr": "1" if usar_ocr else "0", "autorun": "1",
    }
    return APP_BASE_URL + "/?" + urllib.parse.urlencode(params)


# ==========================================================================
# INTERFAZ
# ==========================================================================
st.title("⚽ Buscador en Archivos y Revistas")
st.caption(
    "Rastrea palabras clave dentro de bibliotecas de PDFs online (Yandex.Disk, "
    "Google Drive, PDF directo) o en archivos que subas vos."
)

qp = st.query_params
tab_url, tab_archivos, tab_explorar = st.tabs(["🔗 Buscar por URL", "📁 Subir archivos", "🧭 Explorar colección"])

# -------------------- TAB 1: BUSCAR POR URL --------------------
with tab_url:
    col1, col2 = st.columns([3, 1])
    with col1:
        url_input = st.text_input(
            "URL índice (una página que lista años/tomos, o el tomo directamente):",
            value=qp.get("url", "https://fanpictures.ru/magazines/elgrafico/1980-89.html"),
        )
    with col2:
        palabra_input = st.text_input("Palabra clave:", value=qp.get("palabra", "Menotti"))

    col3, col4, col5 = st.columns(3)
    with col3:
        profundidad = st.number_input(
            "Profundidad de rastreo:", min_value=0, max_value=6,
            value=int(qp.get("profundidad", 3)),
            help="Cuántos saltos de página sigue antes de rendirse. Si tu URL ya es "
                 "la página con los links de Yandex, dejalo en 0 o 1.",
        )
    with col4:
        max_docs = st.number_input("Máx. documentos a revisar:", min_value=1, value=int(qp.get("max_docs", 50)))
    with col5:
        tope_mb = st.number_input("Tope tamaño por PDF (MB):", min_value=1, value=80)

    usar_ocr = st.checkbox(
        "Usar OCR (lento, para escaneos sin texto)" + ("" if OCR_DISPONIBLE else " — no disponible en este servidor"),
        value=qp.get("ocr", "0") == "1", disabled=not OCR_DISPONIBLE,
    )
    if not OCR_DISPONIBLE:
        st.info("Este servidor no tiene Tesseract/Poppler instalados (faltaría `packages.txt` en el repo).")

    mismo_dir = st.checkbox(
        "Restringir a la misma carpeta de partida (recomendado)", value=True,
        help="Evita que el rastreo se vaya al menú de inicio, mapa del sitio u otras "
             "revistas, y se quede solo dentro de la colección que pusiste arriba.",
    )

    autorun_flag = qp.get("autorun") == "1" and "ya_autoejecutado" not in st.session_state
    if st.button("Iniciar búsqueda", type="primary") or autorun_flag:
        st.session_state["ya_autoejecutado"] = True
        st.info("Rastreando enlaces, no cierres la página...")
        placeholder_log = st.empty()
        registro = RegistroEnVivo(placeholder_log)
        documentos = rastrear_documentos(url_input, profundidad, max_docs, registro.log, mismo_dir)

        if not documentos:
            st.warning("No se encontraron documentos (PDF / Yandex.Disk / Google Drive) en esa URL. "
                       "Probá subir la profundidad de rastreo.")
        else:
            st.success(f"Se encontraron {len(documentos)} documento(s). Analizando...")
            resultados = ejecutar_busqueda(documentos, palabra_input, usar_ocr, tope_mb, registro)
            mostrar_resultados(resultados)

            link = link_para_compartir(url_input, palabra_input, profundidad, max_docs, usar_ocr)
            st.text_input(
                "🔗 Link para compartir esta búsqueda (vuelve a correrla al abrirse):",
                value=link,
            )
            st.caption(
                "Ojo: este link no guarda una foto fija de los resultados, vuelve a "
                "correr la misma búsqueda. Si el contenido de esas revistas no cambió, "
                "el resultado va a ser el mismo."
            )

# -------------------- TAB 2: SUBIR ARCHIVOS --------------------
with tab_archivos:
    st.write("Subí uno o varios PDFs, o un ZIP que contenga PDFs adentro.")
    archivos = st.file_uploader(
        "Archivos (PDF o ZIP)", type=["pdf", "zip"], accept_multiple_files=True,
    )
    palabra_local = st.text_input("Palabra clave:", value="Menotti", key="palabra_local")
    usar_ocr_local = st.checkbox(
        "Usar OCR (lento)" + ("" if OCR_DISPONIBLE else " — no disponible en este servidor"),
        disabled=not OCR_DISPONIBLE, key="ocr_local",
    )

    if st.button("Buscar en los archivos subidos", type="primary"):
        if not archivos:
            st.warning("Subí al menos un archivo antes de buscar.")
        else:
            placeholder_log2 = st.empty()
            registro2 = RegistroEnVivo(placeholder_log2)
            resultados_local = []

            for archivo in archivos:
                nombre = archivo.name
                if nombre.lower().endswith(".pdf"):
                    registro2.log(f"Procesando: {nombre}")
                    buf = io.BytesIO(archivo.read())
                    matches = procesar_pdf(buf, palabra_local, usar_ocr_local, registro2.log, nombre)
                    for pagina, frag in matches:
                        resultados_local.append((nombre, pagina, frag, nombre))
                elif nombre.lower().endswith(".zip"):
                    registro2.log(f"Abriendo ZIP: {nombre}")
                    try:
                        with zipfile.ZipFile(archivo) as z:
                            for info in z.infolist():
                                if not info.filename.lower().endswith(".pdf"):
                                    continue
                                registro2.log(f"  Procesando dentro del ZIP: {info.filename}")
                                try:
                                    data = z.read(info.filename)
                                except Exception as e:
                                    registro2.log(f"    Error leyendo {info.filename}: {e}")
                                    continue
                                buf = io.BytesIO(data)
                                etiqueta = f"{nombre} :: {info.filename}"
                                matches = procesar_pdf(buf, palabra_local, usar_ocr_local, registro2.log, etiqueta)
                                for pagina, frag in matches:
                                    resultados_local.append((etiqueta, pagina, frag, etiqueta))
                    except Exception as e:
                        registro2.log(f"  Error abriendo el ZIP {nombre}: {e}")

            registro2.log("Búsqueda finalizada.")
            mostrar_resultados(resultados_local)

# -------------------- TAB 3: EXPLORAR COLECCIÓN --------------------
with tab_explorar:
    st.write(
        "Navegá la estructura de una colección (especiales → décadas → años → tomos) "
        "sin tener que ir copiando links a mano."
    )
    if "nav_path" not in st.session_state:
        st.session_state.nav_path = []

    raiz = st.text_input(
        "URL raíz de la colección:", value="https://fanpictures.ru/magazines/",
        key="nav_raiz",
    )

    col_a, col_b = st.columns([1, 5])
    with col_a:
        if st.button("⬅️ Volver", disabled=not st.session_state.nav_path):
            st.session_state.nav_path.pop()
            st.rerun()
    with col_b:
        migas = " › ".join([raiz.rstrip("/").rsplit("/", 1)[-1]] + [n[0] for n in st.session_state.nav_path])
        st.write(f"📍 {migas}")

    url_actual = st.session_state.nav_path[-1][1] if st.session_state.nav_path else raiz

    with st.spinner("Cargando..."):
        hijos, es_tomos = listar_nivel(url_actual)

    if es_tomos:
        tomos = listar_tomos(url_actual)
        if not tomos:
            st.warning("Esta página parecía tener tomos pero no pude leerlos. Probá recargar.")
        else:
            st.write(f"**{len(tomos)} tomo(s) disponibles acá:**")
            nombres_tomos = [t["etiqueta"] for t in tomos]
            elegidos = st.multiselect("Elegí uno, varios, o dejalo vacío para buscar en TODOS:", nombres_tomos)
            palabra_nav = st.text_input("Palabra clave:", value="Menotti", key="palabra_nav")
            ocr_nav = st.checkbox(
                "Usar OCR (lento)" + ("" if OCR_DISPONIBLE else " — no disponible en este servidor"),
                disabled=not OCR_DISPONIBLE, key="ocr_nav",
            )
            if st.button("Buscar en estos tomos", type="primary"):
                seleccionados = [t for t in tomos if not elegidos or t["etiqueta"] in elegidos]
                placeholder_log3 = st.empty()
                registro3 = RegistroEnVivo(placeholder_log3)
                resultados_nav = ejecutar_busqueda(seleccionados, palabra_nav, ocr_nav, 80, registro3)
                mostrar_resultados(resultados_nav)
    elif hijos:
        opciones = {texto: url for texto, url in hijos}
        seleccion = st.selectbox("Elegí una opción:", list(opciones.keys()))
        if st.button("Entrar ➡️"):
            st.session_state.nav_path.append((seleccion, opciones[seleccion]))
            st.rerun()
    else:
        st.warning("No encontré más enlaces para navegar acá. Puede que la página use otra estructura.")
