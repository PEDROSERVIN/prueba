import io
import re
import unicodedata
import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from pypdf import PdfReader
from pdf2image import convert_from_bytes
import pytesseract
from concurrent.futures import ThreadPoolExecutor

st.set_page_config(page_title="Buscador de PDF", layout="wide")
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
    r = requests.get("https://cloud-api.yandex.net/v1/disk/public/resources/download", params={"public_key": url}, headers=HEADERS, timeout=10)
    return r.json().get("href") if r.status_code == 200 else None

def obtener_links(url_inicial, max_docs):
    enlaces_pdf = []
    visitados = set([url_inicial])
    por_visitar = [url_inicial]
    dominio = urlparse(url_inicial).netloc

    while por_visitar and len(enlaces_pdf) < max_docs:
        url_actual = por_visitar.pop(0)
        try:
            r = requests.get(url_actual, headers=HEADERS, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all("a", href=True):
                if len(enlaces_pdf) >= max_docs: break
                href = a["href"]
                absoluto = urljoin(url_actual, href)
                
                if "disk.yandex" in href or "yadi.sk" in href:
                    enlaces_pdf.append((href, "yandex"))
                elif href.endswith(".pdf"):
                    enlaces_pdf.append((absoluto, "pdf"))
                elif href.endswith(".html") and urlparse(absoluto).netloc == dominio and absoluto not in visitados:
                    visitados.add(absoluto)
                    por_visitar.append(absoluto)
        except:
            pass
    return enlaces_pdf

def ocr_pagina(pdf_bytes, num_pagina):
    try:
        imgs = convert_from_bytes(pdf_bytes, first_page=num_pagina, last_page=num_pagina, dpi=150)
        if imgs: return pytesseract.image_to_string(imgs[0], lang="spa")
    except: pass
    return ""

def procesar_pdf(buf, palabra, usar_ocr):
    try:
        reader = PdfReader(buf)
    except: return []

    resultados = []
    textos = {}
    paginas_vacias = []

    for i, pagina in enumerate(reader.pages, 1):
        texto = pagina.extract_text() or ""
        textos[i] = texto
        if not texto.strip(): paginas_vacias.append(i)

    if usar_ocr and paginas_vacias:
        pdf_bytes = buf.getvalue()
        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = {ex.submit(ocr_pagina, pdf_bytes, p): p for p in paginas_vacias}
            for f in futs: textos[futs[f]] = f.result()

    for i, texto in textos.items():
        if texto and normalizar(palabra) in normalizar(texto):
            resultados.append((i, extraer_fragmento(texto, palabra)))
    
    return resultados

# --- INICIALIZAR MEMORIA DE LA APP ---
if "resultados_guardados" not in st.session_state:
    st.session_state.resultados_guardados = []
if "busqueda_terminada" not in st.session_state:
    st.session_state.busqueda_terminada = False

st.title("⚽ Buscador en Archivos y Revistas")
st.markdown("Herramienta para rastrear palabras clave dentro de bibliotecas PDF o enlaces de Yandex alojados en webs.")

tab_web, tab_local = st.tabs(["🌐 Buscar en la Web (Links)", "📁 Buscar en Archivos Locales (Subir PDFs)"])

with tab_web:
    col1, col2 = st.columns([3, 1])
    with col1:
    url_input = st.text_input("URL Índice (donde están los links):", value="", placeholder="Ej: https://fanpictures.ru/...")
    with col2:
    palabra_input = st.text_input("Palabra clave:", value="", placeholder="Ej: táctica, presión, etc.")

    col3, col4, col5 = st.columns(3)
    with col3: max_docs = st.number_input("Máx. documentos a revisar:", min_value=1, value=50)
    with col4: tope_mb = st.number_input("Tope tamaño por PDF (MB):", min_value=1, value=80)
    with col5: usar_ocr = st.checkbox("Usar OCR (Lento, para escaneos sin texto)", value=False)

    if st.button("Iniciar Búsqueda Web", type="primary"):
        st.session_state.resultados_guardados = [] 
        st.session_state.busqueda_terminada = False
        st.info("Buscando enlaces... Por favor, no minimices ni cambies de pestaña para evitar que el navegador corte el proceso.")
        
        links = obtener_links(url_input, max_docs)
        
        if not links:
            st.warning("No se encontraron enlaces a PDFs o Yandex en esa URL.")
        else:
            progress_bar = st.progress(0)
            for idx, (link_origen, tipo) in enumerate(links):
                try:
                    url_directa = resolver_yandex(link_origen) if tipo == "yandex" else link_origen
                    if not url_directa: continue
                    
                    r = requests.get(url_directa, headers=HEADERS, stream=True, timeout=20)
                    if int(r.headers.get("Content-Length", 0)) > tope_mb * 1024 * 1024:
                        continue 
                    
                    buf = io.BytesIO(r.content)
                    matches = procesar_pdf(buf, palabra_input, usar_ocr)
                    
                    if matches:
                        st.session_state.resultados_guardados.append({
                            "link": link_origen,
                            "matches": matches
                        })
                    
                    buf.close()
                except Exception as e:
                    pass
                
                progress_bar.progress((idx + 1) / len(links))
            
            st.session_state.busqueda_terminada = True
            st.rerun()

with tab_local:
    archivos_subidos = st.file_uploader("Arrastrá tus PDFs acá (Ejemplo: Archivos descargados de tu Drive)", type=["pdf"], accept_multiple_files=True)
    palabra_local = st.text_input("Palabra clave para archivos locales:", value="", placeholder="Ej: táctica, presión...", key="palabra_local")
    usar_ocr_local = st.checkbox("Usar OCR (Archivos locales)", value=False, key="ocr_local")
    
    if st.button("Buscar en PDFs subidos", type="primary"):
        st.session_state.resultados_guardados = []
        st.session_state.busqueda_terminada = False
        
        if not archivos_subidos:
            st.warning("Subí al menos un PDF para buscar.")
        else:
            progress_bar_local = st.progress(0)
            for idx, archivo in enumerate(archivos_subidos):
                matches = procesar_pdf(io.BytesIO(archivo.getvalue()), palabra_local, usar_ocr_local)
                if matches:
                    st.session_state.resultados_guardados.append({
                        "link": archivo.name,
                        "matches": matches
                    })
                progress_bar_local.progress((idx + 1) / len(archivos_subidos))
                
            st.session_state.busqueda_terminada = True
            st.rerun()

# --- MOSTRAR RESULTADOS GUARDADOS EN MEMORIA ---
if st.session_state.busqueda_terminada:
    st.markdown("---")
    if len(st.session_state.resultados_guardados) == 0:
        st.warning("Se revisaron los documentos pero no se encontró la palabra.")
    else:
        st.success("¡Búsqueda finalizada con éxito!")
        for res in st.session_state.resultados_guardados:
            st.markdown(f"### Encontrado en: {res['link']}")
            for pag, frag in res['matches']:
                st.info(f"**Pág. {pag}:** {frag}")
                
        if st.button("Limpiar resultados"):
            st.session_state.resultados_guardados = []
            st.session_state.busqueda_terminada = False
            st.rerun()
