import io
import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from pypdf import PdfReader
from pdf2image import convert_from_bytes
import pytesseract
import unicodedata

st.set_page_config(page_title="Buscador", layout="wide")
HEADERS = {"User-Agent": "Mozilla/5.0"}

def normalizar(texto):
    texto = texto.lower()
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))

def extraer_fragmento(texto, palabra, contexto=100):
    idx = texto.lower().find(palabra.lower())
    if idx == -1: return ""
    return texto[max(0, idx-contexto):min(len(texto), idx+len(palabra)+contexto)]

def resolver_yandex(url):
    try:
        r = requests.get("https://cloud-api.yandex.net/v1/disk/public/resources/download", params={"public_key": url}, headers=HEADERS, timeout=10)
        return r.json().get("href") if r.status_code == 200 else None
    except: return None

# --- RASTREADOR RECURSIVO (EL QUE BUSCA ARCHIVOS ADENTRO DE LOS AÑOS) ---
def obtener_links_recursivos(url_inicial, max_docs=100):
    enlaces = []
    visitados = set([url_inicial])
    por_visitar = [url_inicial]
    dominio = urlparse(url_inicial).netloc
    
    while por_visitar and len(enlaces) < max_docs:
        url_actual = por_visitar.pop(0)
        try:
            r = requests.get(url_actual, headers=HEADERS, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all("a", href=True):
                href = a["href"]
                absoluto = urljoin(url_actual, href)
                if "disk.yandex" in href or "yadi.sk" in href:
                    if (href, "yandex") not in enlaces: enlaces.append((href, "yandex"))
                elif href.endswith(".pdf"):
                    if (absoluto, "pdf") not in enlaces: enlaces.append((absoluto, "pdf"))
                elif href.endswith(".html") and urlparse(absoluto).netloc == dominio and absoluto not in visitados:
                    visitados.add(absoluto)
                    por_visitar.append(absoluto)
        except: pass
    return enlaces

def procesar_pdf(buf, palabra, usar_ocr):
    try:
        reader = PdfReader(buf)
        resultados = []
        for i, pagina in enumerate(reader.pages, 1):
            texto = pagina.extract_text() or ""
            if not texto.strip() and usar_ocr:
                try:
                    imgs = convert_from_bytes(buf.getvalue(), first_page=i, last_page=i, dpi=100)
                    if imgs: texto = pytesseract.image_to_string(imgs[0], lang="spa")
                except: pass
            if texto and palabra.lower() in texto.lower():
                resultados.append((i, extraer_fragmento(texto, palabra)))
        return resultados
    except: return []

# --- UI ---
st.markdown("## 🔍 Buscador de Documentos")
tab1, tab2 = st.tabs(["🌐 Web", "📁 Locales"])

with tab1:
    url_input = st.text_input("URL:")
    palabra_input = st.text_input("Palabra:")
    ocr = st.checkbox("Usar OCR")
    
    if st.button("Buscar"):
        if not url_input or not palabra_input:
            st.warning("Completá URL y Palabra")
        else:
            with st.status("Rastreando y analizando...", expanded=True) as status:
                links = obtener_links_recursivos(url_input)
                st.write(f"Documentos encontrados: {len(links)}")
                
                for idx, (link, tipo) in enumerate(links):
                    st.write(f"Analizando {idx+1}/{len(links)}...")
                    url = resolver_yandex(link) if tipo == "yandex" else link
                    if url:
                        try:
                            r = requests.get(url, stream=True, timeout=15)
                            matches = procesar_pdf(io.BytesIO(r.content), palabra_input, ocr)
                            if matches:
                                st.success(f"Encontrado en: {link}")
                                for pag, frag in matches:
                                    st.info(f"Pág {pag}: {frag}")
                        except: pass
                status.update(label="Búsqueda finalizada", state="complete")

with tab2:
    archivos = st.file_uploader("Subir PDFs", accept_multiple_files=True)
    palabra_local = st.text_input("Palabra clave (Locales)")
    if st.button("Buscar en Locales"):
        for arch in archivos:
            matches = procesar_pdf(io.BytesIO(arch.getvalue()), palabra_local, False)
            if matches:
                st.write(f"📄 {arch.name}")
                for pag, frag in matches: st.info(f"Pág {pag}: {frag}")
