import io
import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from pypdf import PdfReader

# Configuración básica
st.set_page_config(page_title="Buscador PDF", layout="wide")
HEADERS = {"User-Agent": "Mozilla/5.0"}

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

def buscar_en_pdf(buf, palabra):
    resultados = []
    try:
        reader = PdfReader(buf)
        for i, pagina in enumerate(reader.pages, 1):
            texto = pagina.extract_text() or ""
            if palabra.lower() in texto.lower():
                resultados.append((i, texto[:150] + "..."))
    except: pass
    return resultados

# --- INTERFAZ ---
st.markdown("## ⚽ Buscador de Documentos")
tab_web, tab_local = st.tabs(["🌐 Búsqueda Web", "📁 Archivos Locales"])

with tab_web:
    url = st.text_input("URL:", value="https://fanpictures.ru/magazines/elgrafico/1980-89.html")
    palabra = st.text_input("Palabra clave:")
    if st.button("Buscar en Web"):
        links = obtener_links_web(url)
        st.write(f"Enlaces encontrados: {len(links)}")
        
        for idx, (link, tipo) in enumerate(links):
            st.write(f"Procesando {idx+1}/{len(links)}...")
            url_final = resolver_yandex(link) if tipo == "yandex" else link
            if url_final:
                try:
                    r = requests.get(url_final, stream=True, timeout=15)
                    matches = buscar_en_pdf(io.BytesIO(r.content), palabra)
                    for pag, frag in matches:
                        st.success(f"Encontrado en {link} - Pág {pag}: {frag}")
                except: pass

with tab_local:
    archivos = st.file_uploader("Subir PDFs", accept_multiple_files=True, type="pdf")
    palabra_local = st.text_input("Palabra clave (Locales):")
    if st.button("Buscar en Locales"):
        for arch in archivos:
            matches = buscar_en_pdf(io.BytesIO(arch.getvalue()), palabra_local)
            for pag, frag in matches:
                st.info(f"En {arch.name} - Pág {pag}: {frag}")
