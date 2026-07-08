import io
import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from pypdf import PdfReader
from pdf2image import convert_from_bytes
import pytesseract
import unicodedata

# Configuración básica
st.set_page_config(page_title="Buscador", layout="wide")
HEADERS = {"User-Agent": "Mozilla/5.0"}

# Funciones de lógica
def normalizar(texto):
    texto = texto.lower()
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))

def extraer_fragmento(texto, palabra, contexto=100):
    idx = texto.lower().find(palabra.lower())
    if idx == -1: return ""
    inicio = max(0, idx - contexto)
    fin = min(len(texto), idx + len(palabra) + contexto)
    return texto[inicio:fin]

def resolver_yandex(url):
    try:
        r = requests.get("https://cloud-api.yandex.net/v1/disk/public/resources/download", params={"public_key": url}, headers=HEADERS, timeout=10)
        return r.json().get("href") if r.status_code == 200 else None
    except: return None

def obtener_links_web(url_input):
    enlaces = []
    try:
        r = requests.get(url_input, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "disk.yandex" in href or "yadi.sk" in href: enlaces.append((href, "yandex"))
            elif href.endswith(".pdf"): enlaces.append((urljoin(url_input, href), "pdf"))
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

# Inicialización de estado
if "resultados" not in st.session_state:
    st.session_state.resultados = []

st.markdown("## 🔍 Buscador de Documentos")
tab1, tab2 = st.tabs(["🌐 Búsqueda Web", "📁 Archivos Locales"])

# --- LÓGICA DE WEB ---
with tab1:
    url = st.text_input("URL del año/tomo:")
    palabra = st.text_input("Palabra clave:")
    ocr_web = st.checkbox("Usar OCR")
    
    if st.button("Buscar en Web"):
        st.session_state.resultados = [] # Limpiar resultados anteriores
        with st.status("Buscando en la web...", expanded=True) as status:
            links = obtener_links_web(url)
            st.write(f"Enlaces encontrados: {len(links)}")
            
            for idx, (link, tipo) in enumerate(links):
                st.write(f"Procesando {idx+1}/{len(links)}...")
                url_final = resolver_yandex(link) if tipo == "yandex" else link
                if url_final:
                    try:
                        r = requests.get(url_final, stream=True, timeout=15)
                        matches = procesar_pdf(io.BytesIO(r.content), palabra, ocr_web)
                        if matches:
                            st.session_state.resultados.append({"link": link, "matches": matches})
                    except: pass
            status.update(label="Búsqueda completa", state="complete")

# --- LÓGICA DE LOCALES ---
with tab2:
    archivos = st.file_uploader("Subir PDFs", accept_multiple_files=True, type="pdf")
    palabra_local = st.text_input("Palabra clave (Locales):")
    ocr_local = st.checkbox("Usar OCR (Locales)")
    
    if st.button("Buscar en Locales"):
        st.session_state.resultados = []
        for arch in archivos:
            matches = procesar_pdf(io.BytesIO(arch.getvalue()), palabra_local, ocr_local)
            if matches:
                st.session_state.resultados.append({"link": arch.name, "matches": matches})

# --- MOSTRAR RESULTADOS PERSISTENTES ---
if st.session_state.resultados:
    st.markdown("---")
    texto_copiar = ""
    for res in st.session_state.resultados:
        st.markdown(f"### 📄 {res['link']}")
        texto_copiar += f"\nArchivo: {res['link']}\n"
        for pag, frag in res['matches']:
            st.info(f"**Pág {pag}:** {frag}")
            texto_copiar += f"Pág {pag}: {frag}\n"
    
    st.markdown("### 📋 Copiar resultados para compartir")
    st.text_area("Seleccioná y copiá:", value=texto_copiar, height=200)
