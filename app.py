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

st.set_page_config(page_title="Buscador de PDFs", layout="wide")
HEADERS = {"User-Agent": "Mozilla/5.0"}

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

# --- RASTREADOR RECURSIVO ---
def obtener_links(url_inicial, max_docs):
    enlaces_pdf = []
    # Usamos una lista para visitar páginas (decadas -> años -> PDFs)
    visitados = set([url_inicial])
    por_visitar = [url_inicial]
    
    while por_visitar and len(enlaces_pdf) < max_docs:
        url_actual = por_visitar.pop(0)
        try:
            r = requests.get(url_actual, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all("a", href=True):
                href = a["href"]
                absoluto = urljoin(url_actual, href)
                
                if "disk.yandex" in href or "yadi.sk" in href:
                    if (href, "yandex") not in enlaces_pdf: enlaces_pdf.append((href, "yandex"))
                elif href.endswith(".pdf"):
                    if (absoluto, "pdf") not in enlaces_pdf: enlaces_pdf.append((absoluto, "pdf"))
                elif href.endswith(".html") and "fanpictures" in absoluto and absoluto not in visitados:
                    visitados.add(absoluto)
                    por_visitar.append(absoluto)
        except: pass
    return enlaces_pdf

def procesar_pdf(buf, palabra, usar_ocr):
    try:
        reader = PdfReader(buf)
    except: return []
    resultados = []
    # Procesar texto
    for i, pagina in enumerate(reader.pages, 1):
        texto = pagina.extract_text() or ""
        if texto and normalizar(palabra) in normalizar(texto):
            resultados.append((i, extraer_fragmento(texto, palabra)))
    return resultados

# --- UI ---
st.markdown("## ⚽ Buscador en Archivos y Revistas")
tab_web, tab_local = st.tabs(["🌐 Búsqueda Web", "📁 Archivos Locales"])

with tab_web:
    col1, col2 = st.columns([3, 1])
    with col1:
        url_input = st.text_input("URL Índice:", value="https://fanpictures.ru/magazines/elgrafico/1980-89.html")
    with col2:
        palabra_input = st.text_input("Palabra clave:", value="Menotti")
    
    col3, col4, col5 = st.columns(3)
    with col3: max_docs = st.number_input("Máx. documentos:", min_value=1, value=100)
    with col4: tope_mb = st.number_input("Tope tamaño (MB):", min_value=1, value=80)
    with col5: usar_ocr = st.checkbox("Usar OCR", value=True)

    if st.button("Iniciar Búsqueda Web"):
        links = obtener_links(url_input, max_docs)
        if not links:
            st.warning("No se encontraron enlaces.")
        else:
            ui_status = st.empty()
            ui_status.info(f"Se encontraron {len(links)} documentos. Analizando...")
            progress_bar = st.progress(0)
            
            for idx, (link_origen, tipo) in enumerate(links):
                ui_status.info(f"Procesando {idx+1}/{len(links)}: {link_origen.split('/')[-1]}")
                url_directa = resolver_yandex(link_origen) if tipo == "yandex" else link_origen
                try:
                    r = requests.get(url_directa, headers=HEADERS, stream=True, timeout=20)
                    matches = procesar_pdf(io.BytesIO(r.content), palabra_input, usar_ocr)
                    if matches:
                        st.markdown(f"### Encontrado en Documento {idx+1}")
                        st.write(f"🔗 {link_origen}")
                        for pag, frag in matches: st.success(f"**Pág. {pag}:** {frag}")
                except: pass
                progress_bar.progress((idx + 1) / len(links))
            ui_status.success("Búsqueda finalizada")

with tab_local:
    archivos = st.file_uploader("Subir PDFs", accept_multiple_files=True, type=["pdf"])
    palabra_local = st.text_input("Palabra clave (Local):")
    if st.button("Buscar en Locales"):
        for arch in archivos:
            matches = procesar_pdf(io.BytesIO(arch.getvalue()), palabra_local, False)
            if matches:
                st.markdown(f"### 📄 {arch.name}")
                for pag, frag in matches: st.info(f"Pág {pag}: {frag}")
