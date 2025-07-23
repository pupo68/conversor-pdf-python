import sys
import asyncio
import io
import os
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from playwright.async_api import async_playwright, Browser

# Variável global para o navegador
browser: Optional[Browser] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia o ciclo de vida da aplicação: inicia e fecha o Playwright."""
    # CORREÇÃO AQUI: Usar WindowsProactorEventLoopPolicy
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    global browser
    print("Iniciando o Playwright...")
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch()
    print("Navegador Chromium iniciado com sucesso.")
    yield
    print("Fechando o navegador...")
    if browser:
        await browser.close()
    await playwright.stop()
    print("Playwright finalizado.")

app = FastAPI(
    title="Conversor de HTML para PDF com FastAPI e Playwright",
    lifespan=lifespan
)

templates = Jinja2Templates(directory="templates")

def build_pdf_options(form_data: dict):
    """Constrói o dicionário de opções para o Playwright a partir dos dados do formulário."""
    return {
        "media_type": 'screen',
        "print_background": True,
        "margin": {
            "top": f"{form_data.get('margin_top', 0)}mm",
            "bottom": f"{form_data.get('margin_bottom', 0)}mm",
            "left": f"{form_data.get('margin_left', 0)}mm",
            "right": f"{form_data.get('margin_right', 0)}mm",
        }
    }

async def process_conversion(page_content_setter, form_data: dict, filename_base: str):
    """Função central que realiza a conversão."""
    if not browser:
        raise Exception("Navegador não iniciado.")

    viewport_width = int(form_data.get('viewport_width', 1280))
    context = await browser.new_context()
    page = await context.new_page()

    try:
        await page.set_viewport_size({"width": viewport_width, "height": 1080})
        await page_content_setter(page)
        await page.evaluate("() => document.fonts.ready")
        await page.add_style_tag(content='html, body { height: auto !important; }')
        
        new_page_height = await page.evaluate("() => document.documentElement.scrollHeight")
        await page.set_viewport_size({"width": viewport_width, "height": new_page_height})

        pdf_options = build_pdf_options(form_data)
        pdf_buffer = await page.pdf(**pdf_options)
        
        pdf_filename = os.path.splitext(filename_base)[0] + '.pdf'

        return StreamingResponse(io.BytesIO(pdf_buffer), media_type="application/pdf", headers={
            "Content-Disposition": f"inline; filename={pdf_filename}"
        })
    finally:
        await context.close()

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/convert/url", response_class=StreamingResponse)
async def convert_url_to_pdf(request: Request):
    form_data = await request.form()
    url = form_data.get("url")
    if not url:
        return HTMLResponse("URL é obrigatória.", status_code=400)
    
    async def page_content_setter(page):
        await page.goto(url, wait_until='load')
        
    filename_base = urlparse(url).hostname or "pagina_web"
    return await process_conversion(page_content_setter, dict(form_data), filename_base)

@app.post("/convert/html", response_class=StreamingResponse)
async def convert_html_to_pdf(request: Request):
    form_data = await request.form()
    html_content = form_data.get("html_content")
    if not html_content:
        return HTMLResponse("Conteúdo HTML é obrigatório.", status_code=400)

    async def page_content_setter(page):
        await page.set_content(html_content, wait_until='load')
        
    return await process_conversion(page_content_setter, dict(form_data), "converted_html.pdf")

@app.post("/convert/file", response_class=StreamingResponse)
async def convert_file_to_pdf(request: Request, htmlfile: UploadFile = File(...)):
    form_data = await request.form()
    if not htmlfile:
        return HTMLResponse("Arquivo HTML é obrigatório.", status_code=400)

    html_content = await htmlfile.read()

    async def page_content_setter(page):
        await page.set_content(html_content.decode('utf-8'), wait_until='load')
        
    return await process_conversion(page_content_setter, dict(form_data), htmlfile.filename)