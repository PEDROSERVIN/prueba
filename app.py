import io
import re
import csv
import unicodedata
import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from pypdf import PdfReader
from pdf2image import convert_from_bytes
import pytesseract
from concurrent.futures import ThreadPoolExecutor

st.set_page_config(page_title="Buscador PDF", layout="wide")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# --- BASE DE DATOS FIJA (Para que no falle el scraping) ---
REVISTAS_DB = {
    "El Gráfico": "https://fanpictures.ru/magazines/elgrafico/",
    "Solo Fútbol": "https://fanpictures.ru/magazines/solofutbol/",
    "Goles": "https://fanpictures.ru/magazines/goles/",
    "El Gráfico (Suplementos)": "https://fanpictures.ru/magazines/elgrafico-suplementos/",
    "Stadium": "https://fanpictures.ru/magazines/stadium/"
}

# --- FUNCIONES ---
def normalizar(texto):
    texto = texto.lower()
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))

def extraer_fragmento(texto, palabra, contexto=100):
    idx_norm = normalizar(texto).find(normalizar(palabra))
    if idx_norm == -1: return ""
    inicio = max(0, idx_norm - contexto)
    fin = min(len(texto), idx_norm + len(palabra) + contexto)
    frag = texto[inicio:fin].replace("\n", " ").strip()
    return ("..." if inicio > 0 else "") + frag + ("..." if fin < len(texto) else "")

def resolver_yandex(url):
    try:
        r = requests.get("https://cloud-api.yandex.net/v1/disk/public/resources/download", params={"public_key": url}, headers=HEADERS, timeout=10)
        return r.json().get("href") if r.status_code == 200 else None
    except: return None

@st.cache_data
def obtener_años(revista_url):
    try:
        r = requests.get(revista_url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        años = {}
        for a in soup.select("a[href$='.html']"):
            nombre = a.text.strip()
            if nombre: años[nombre] = urljoin(revista_url, a['href'])
        return años
    except: return {}

def obtener_links_de_tomo(url_tomo):
    enlaces = []
    try:
        r = requests.get(url_tomo, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "disk.yandex" in href or "yadi.sk" in href: enlaces.append((href, "yandex"))
            elif href.endswith(".pdf"): enlaces.append((urljoin(url_tomo, href), "pdf"))
    except: pass
    return enlaces

def procesar_pdf(buf, palabra, usar_ocr):
    try:
        reader = PdfReader(buf)
    except: return []
    resultados = []
    for i, pagina in enumerate(reader.pages, 1):
        texto = pagina.extract_text() or ""
        if not texto.strip() and usar_ocr:
            try:
                imgs = convert_from_bytes(buf.getvalue(), first_page=i, last_page=i, dpi=100)
                if imgs: texto = pytesseract.image_to_string(imgs[0], lang="spa")
            except: pass
        if texto and normalizar(palabra) in normalizar(texto):
            resultados.append((i, extraer_fragmento(texto, palabra)))
    return resultados

# --- UI ---
if "resultados_guardados" not in st.session_state: st.session_state.resultados_guardados = []
if "busqueda_terminada" not in st.session_state: st.session_state.busqueda_terminada = False

st.markdown("## 🔍 Buscador de Documentos")
tab1, tab2, tab3 = st.tabs(["🌐 Búsqueda Guiada", "🌐 Búsqueda Web (Custom)", "📁 Archivos Locales"])

with tab1:
    rev_sel = st.selectbox("Seleccionar Revista:", list(REVISTAS_DB.keys()))
    años = obtener_años(REVISTAS_DB[rev_sel])
    if años:
        año_sel = st.selectbox("Seleccionar Año:", list(años.keys()))
        palabra_guia = st.text_input("Palabra clave:", placeholder="Insertar palabra")
        if st.button("Buscar en Año"):
            st.session_state.resultados_guardados = []
            with st.spinner("Buscando..."):
                links = obtener_links_de_tomo(años[año_sel])
                for link, tipo in links:
                    url = resolver_yandex(link) if tipo == "yandex" else link
                    if url:
                        r = requests.get(url, stream=True, timeout=15)
                        matches = procesar_pdf(io.BytesIO(r.content), palabra_guia, True)
                        if matches: st.session_state.resultados_guardados.append({"link": link, "matches": matches})
            st.session_state.busqueda_terminada = True
            st.rerun()

with tab2:
    url_input = st.text_input("URL Índice:", placeholder="Ej: https://fanpictures.ru/...")
    palabra_input = st.text_input("Palabra clave (Custom):", placeholder="Insertar palabra")
    ocr_custom = st.checkbox("Usar OCR")
    if st.button("Iniciar Búsqueda en URL"):
        st.session_state.resultados_guardados = []
        with st.spinner("Buscando..."):
            links = obtener_links_de_tomo(url_input)
            for link, tipo in links:
                url = resolver_yandex(link) if tipo == "yandex" else link
                if url:
                    r = requests.get(url, stream=True, timeout=15)
                    matches = procesar_pdf(io.BytesIO(r.content), palabra_input, ocr_custom)
                    if matches: st.session_state.resultados_guardados.append({"link": link, "matches": matches})
        st.session_state.busqueda_terminada = True
        st.rerun()

with tab3:
    archivos_subidos = st.file_uploader("Arrastrá tus PDFs acá", type=["pdf"], accept_multiple_files=True)
    palabra_local = st.text_input("Palabra clave (Locales):", placeholder="Insertar palabra")
    ocr_local = st.checkbox("Usar OCR (Locales)")
    if st.button("Buscar en Archivos"):
        st.session_state.resultados_guardados = []
        for archivo in archivos_subidos:
            matches = procesar_pdf(io.BytesIO(archivo.getvalue()), palabra_local, ocr_local)
            if matches: st.session_state.resultados_guardados.append({"link": archivo.name, "matches": matches})
        st.session_state.busqueda_terminada = True
        st.rerun()

if st.session_state.busqueda_terminada:
    st.markdown("---")
    if not st.session_state.resultados_guardados: st.warning("No se encontraron coincidencias.")
    for res in st.session_state.resultados_guardados:
        st.markdown(f"### 📄 {res['link']}")
        for pag, frag in res['matches']:
            st.info(f"**Pág {pag}:** {frag}")
