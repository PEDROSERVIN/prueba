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

st.set_page_config(page_title="Buscador Táctico en PDFs", layout="wide")

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
    try:
        r = requests.get(url_inicial, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.find_all("a", href=True):
            if len(enlaces_pdf) >= max_docs: break
            href = a["href"]
            if "disk.yandex" in href or "yadi.sk" in href:
                enlaces_pdf.append((href, "yandex"))
            elif href.endswith(".pdf"):
                absoluto = urljoin(url_inicial, href)
                enlaces_pdf.append((absoluto, "pdf"))
    except Exception:
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

st.title("⚽ Buscador en Archivos y Revistas")
st.markdown("Herramienta para rastrear palabras clave dentro de bibliotecas PDF o enlaces de Yandex alojados en webs.")

col1, col2 = st.columns([3, 1])
with col1:
    url_input = st.text_input("URL Índice (donde están los links):", value="https://fanpictures.ru/magazines/elgrafico/1980-89.html")
with col2:
    palabra_input = st.text_input("Palabra clave:", value="Menotti")

col3, col4, col5 = st.columns(3)
with col3: max_docs = st.number_input("Máx. documentos a revisar:", min_value=1, value=50)
with col4: tope_mb = st.number_input("Tope tamaño por PDF (MB):", min_value=1, value=80)
with col5: usar_ocr = st.checkbox("Usar OCR (Lento, para escaneos sin texto)", value=False)

if st.button("Iniciar Búsqueda", type="primary"):
    st.info("Buscando enlaces... no cierres la página.")
    links = obtener_links(url_input, max_docs)
    
    if not links:
        st.warning("No se encontraron enlaces a PDFs o Yandex en esa URL.")
    else:
        st.success(f"Se encontraron {len(links)} documentos. Analizando...")
        
        progress_bar = st.progress(0)
        resultados_totales = 0

        for idx, (link_origen, tipo) in enumerate(links):
            try:
                url_directa = resolver_yandex(link_origen) if tipo == "yandex" else link_origen
                if not url_directa: continue
                
                r = requests.get(url_directa, headers=HEADERS, stream=True, timeout=20)
                if int(r.headers.get("Content-Length", 0)) > tope_mb * 1024 * 1024:
                    continue # Salta si es muy pesado
                
                buf = io.BytesIO(r.content)
                matches = procesar_pdf(buf, palabra_input, usar_ocr)
                
                if matches:
                    resultados_totales += len(matches)
                    st.markdown(f"### Encontrado en Documento {idx+1}")
                    st.write(f"🔗 **Enlace original:** [{link_origen}]({link_origen})")
                    for pag, frag in matches:
                        st.success(f"**Pág. {pag}:** {frag}")
                
                buf.close()
            except Exception as e:
                pass
            
            progress_bar.progress((idx + 1) / len(links))
        
        if resultados_totales == 0:
            st.warning("Se revisaron los documentos pero no se encontró la palabra.")
        else:
            st.balloons()
