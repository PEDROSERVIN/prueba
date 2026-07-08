import io
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
HEADERS = {"User-Agent": "Mozilla/5.0"}

# --- LÓGICA DE BÚSQUEDA ---
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

# --- RASTREO RECURSIVO (RESTAURADO) ---
def obtener_links_recursivo(url_inicial, max_docs, ui_estado):
    enlaces_pdf = []
    visitados = set([url_inicial])
    por_visitar = [url_inicial]
    dominio = urlparse(url_inicial).netloc

    while por_visitar and len(enlaces_pdf) < max_docs:
        url_actual = por_visitar.pop(0)
        ui_estado.info(f"🔍 Buscando... PDFs encontrados: {len(enlaces_pdf)} | Analizando: {url_actual.split('/')[-2] if '/' in url_actual else '...'}")
        
        try:
            r = requests.get(url_actual, headers=HEADERS, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all("a", href=True):
                if len(enlaces_pdf) >= max_docs: break
                href = a["href"]
                absoluto = urljoin(url_actual, href)
                
                if "disk.yandex" in href or "yadi.sk" in href:
                    if (href, "yandex") not in enlaces_pdf: enlaces_pdf.append((href, "yandex"))
                elif href.endswith(".pdf"):
                    if (absoluto, "pdf") not in enlaces_pdf: enlaces_pdf.append((absoluto, "pdf"))
                elif href.endswith(".html") and urlparse(absoluto).netloc == dominio and absoluto not in visitados:
                    visitados.add(absoluto)
                    por_visitar.append(absoluto)
        except: pass
    return enlaces_pdf

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
if "resultados" not in st.session_state: st.session_state.resultados = []

st.markdown("## 🔍 Buscador de Documentos")
tab1, tab2 = st.tabs(["🌐 Búsqueda Web (Recursiva)", "📁 Archivos Locales"])

with tab1:
    url_input = st.text_input("URL Índice:", placeholder="Ej: https://fanpictures.ru/magazines/elgrafico/1986.html")
    palabra_input = st.text_input("Palabra clave:", placeholder="Ej: Menotti")
    ocr_web = st.checkbox("Usar OCR")
    max_d = st.number_input("Máx documentos:", value=50)

    if st.button("Iniciar Búsqueda"):
        st.session_state.resultados = []
        ui_estado = st.empty()
        links = obtener_links_recursivo(url_input, max_d, ui_estado)
        
        progress = st.progress(0)
        for idx, (link, tipo) in enumerate(links):
            ui_estado.info(f"📥 Procesando documento {idx+1}/{len(links)}...")
            url = resolver_yandex(link) if tipo == "yandex" else link
            try:
                r = requests.get(url, stream=True, timeout=20)
                matches = procesar_pdf(io.BytesIO(r.content), palabra_input, ocr_web)
                if matches: st.session_state.resultados.append({"link": link, "matches": matches})
            except: pass
            progress.progress((idx+1)/len(links))
        ui_estado.empty()
        st.rerun()

with tab2:
    archivos = st.file_uploader("Subí tus PDFs:", type=["pdf"], accept_multiple_files=True)
    palabra_local = st.text_input("Palabra clave (Locales):", placeholder="Ej: Menotti")
    ocr_local = st.checkbox("Usar OCR (Locales)")
    if st.button("Buscar en Archivos"):
        st.session_state.resultados = []
        for archivo in archivos:
            matches = procesar_pdf(io.BytesIO(archivo.getvalue()), palabra_local, ocr_local)
            if matches: st.session_state.resultados.append({"link": archivo.name, "matches": matches})
        st.rerun()

if st.session_state.resultados:
    st.markdown("---")
    for res in st.session_state.resultados:
        st.markdown(f"### 📄 {res['link']}")
        for pag, frag in res['matches']:
            st.info(f"**Pág {pag}:** {frag}")
