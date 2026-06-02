import cv2
import numpy as np
import customtkinter as ctk
from PIL import Image, ImageTk, ImageDraw
import json
import os
import re
import csv
import base64
import io
import threading
import time
import socket
import queue
import requests
from requests.auth import HTTPDigestAuth
import subprocess
import platform
from datetime import datetime
from tkinter import filedialog
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Configurações do sistema Biométrico
URL_LOGIN_BIO = "http://192.168.7.9:8098/bioLogin.do"
USUARIO_BIO = "armando.campos"
SENHA_BIO = "armandocampos.1"

# Diretórios para salvar os relatórios e imagens extraídas
DIRETORIO_SAIDA = "relatorio_acessos"
DIRETORIO_FOTOS = os.path.join(DIRETORIO_SAIDA, "fotos")
ARQUIVO_CSV = os.path.join(DIRETORIO_SAIDA, "historico_acessos.csv")
ARQUIVO_HTML = os.path.join(DIRETORIO_SAIDA, "relatorio_visual.html")

# Cria as pastas caso não existam
os.makedirs(DIRETORIO_FOTOS, exist_ok=True)

# Configuração de baixa latência para OpenCV/FFMPEG
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp;stimeout;3000000;buffer_size;2048000;analyzeduration;50000;probesize;50000;fflags;discardcorrupt;max_delay;500000;reorder_queue_size;16;rtsp_flags;prefer_tcp;reconnect;1;reconnect_streamed;1;reconnect_at_eof;1;allowed_media_types;video"
cv2.setNumThreads(1)

# Semáforo global para limitar conexões simultâneas (evita travamentos)
sem_conexao = threading.Semaphore(20)

# Global para sincronizar a data do sistema de monitoramento biométrico
DATA_SISTEMA_ATUAL = datetime.now().strftime("%Y-%m-%d")

def normalizar_data(data_str):
    """Garante que a data esteja no formato YYYY-MM-DD HH:MM:SS."""
    global DATA_SISTEMA_ATUAL
    if not data_str:
        return f"{DATA_SISTEMA_ATUAL} {datetime.now().strftime('%H:%M:%S')}"

    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", data_str):
        DATA_SISTEMA_ATUAL = data_str.split(' ')[0]
        return data_str

    if re.match(r"^\d{2}:\d{2}:\d{2}$", data_str):
        return f"{DATA_SISTEMA_ATUAL} {data_str}"

    return data_str

def salvar_foto_bio(base64_data, id_usuario, data_evento):
    """Decodifica a string Base64 da imagem e salva em arquivo local."""
    if not base64_data: return ""
    if "," in base64_data: base64_data = base64_data.split(",")[1]
    try:
        data_safe = re.sub(r'[^0-9]', '_', data_evento)
        nome_arquivo = f"{id_usuario}_{data_safe}.jpg"
        caminho_completo = os.path.join(DIRETORIO_FOTOS, nome_arquivo)
        with open(caminho_completo, "wb") as f:
            f.write(base64.b64decode(base64_data))
        return caminho_completo
    except Exception as e:
        print(f"[-] BIO: Erro ao salvar foto {id_usuario}: {e}")
        return ""

def registrar_evento(id_usuario, nome, evento, dispositivo, leitor, data_evento, base64_foto, queue_ui):
    """Registra as informações capturadas e notifica a UI."""
    caminho_foto_local = ""
    if base64_foto:
        caminho_foto_local = salvar_foto_bio(base64_foto, id_usuario, data_evento)

    data_registro = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    registros_existentes = []
    atualizado = False

    if os.path.exists(ARQUIVO_CSV):
        with open(ARQUIVO_CSV, mode='r', encoding='utf-8') as f:
            reader = csv.reader(f)
            cabecalho = next(reader, None)
            for row in reader:
                if len(row) >= 8:
                    if row[1] == id_usuario and row[6] == data_evento:
                        if not row[7] and caminho_foto_local: row[7] = caminho_foto_local
                        if not row[5] and leitor: row[5] = leitor
                        if (not row[4] or row[4] == "Geral") and dispositivo and dispositivo != "Geral":
                            row[4] = dispositivo
                        atualizado = True
                    registros_existentes.append(row)

    if not atualizado:
        registros_existentes.append([data_registro, id_usuario, nome, evento, dispositivo, leitor, data_evento, caminho_foto_local])

    with open(ARQUIVO_CSV, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Data_Registro", "ID", "Nome", "Evento", "Dispositivo", "Leitor", "Data_Evento", "Caminho_Foto"])
        writer.writerows(registros_existentes)

    print(f"[+] BIO: Evento {id_usuario} - {nome}")
    atualizar_relatorio_html()

    # Notifica a UI
    if queue_ui:
        queue_ui.put({
            "type": "BIO_EVENT",
            "data": {
                "id_usuario": id_usuario,
                "nome": nome,
                "evento": evento,
                "dispositivo": dispositivo,
                "leitor": leitor,
                "data_evento": data_evento,
                "foto": base64_foto
            }
        })

def atualizar_relatorio_html():
    """Gera ou atualiza o dashboard estático externo em HTML."""
    registros = []
    if os.path.exists(ARQUIVO_CSV):
        with open(ARQUIVO_CSV, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            registros = list(reader)
            registros.reverse()

    html_content = f"""<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Relatório de Monitoramento Biométrico</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-900 text-gray-100 font-sans min-h-screen">
    <div class="container mx-auto px-4 py-8">
        <header class="mb-8 border-b border-gray-800 pb-4">
            <h1 class="text-3xl font-bold text-teal-400">Relatório de Monitoramento Biométrico</h1>
            <p class="text-gray-400">Última atualização: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</p>
        </header>
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
    """
    for r in registros:
        caminho_foto = r.get('Caminho_Foto', '')
        img_tag_src = f"fotos/{os.path.basename(caminho_foto)}" if caminho_foto else "https://via.placeholder.com/150"
        disp_html = f'<p class="text-sm text-gray-300 font-medium mt-0.5">🖥️ {r.get("Dispositivo", "")}</p>'
        leitor_html = f'<p class="text-xs text-teal-400 font-semibold mt-0.5">Leitor: {r.get("Leitor", "")}</p>'
        evento_html = f'<p class="text-sm text-yellow-400 font-medium mt-1">{r.get("Evento", "")}</p>'
        html_content += f"""
            <div class="bg-gray-800 rounded-xl overflow-hidden shadow-lg border border-gray-700 p-4">
                <div class="flex justify-center mb-4">
                    <img class="h-44 object-contain rounded-lg" src="{img_tag_src}" onerror="this.src='https://via.placeholder.com/150'">
                </div>
                <span class="inline-block px-2 py-1 text-xs font-semibold rounded-full bg-teal-900 text-teal-300 mb-2">ID: {r['ID']}</span>
                <h3 class="text-lg font-bold text-white truncate">{r['Nome']}</h3>
                {evento_html} {leitor_html} {disp_html}
                <div class="mt-4 pt-3 border-t border-gray-700 text-xs text-gray-400">
                    <div><strong>Evento em:</strong> {r['Data_Evento']}</div>
                </div>
            </div>
        """
    html_content += "</div></div></body></html>"
    with open(ARQUIVO_HTML, "w", encoding="utf-8") as f:
        f.write(html_content)

class BioMonitorThread(threading.Thread):
    def __init__(self, queue_ui):
        super().__init__(daemon=True)
        self.queue_ui = queue_ui
        self.rodando = True
        self.browser = None
        self.context = None
        self.page = None

    def run(self):
        with sync_playwright() as p:
            try:
                # Tenta iniciar navegador (Visível a pedido do usuário)
                try:
                    self.browser = p.chromium.launch(headless=False)
                except:
                    try:
                        self.browser = p.chromium.launch(headless=False, channel="chrome")
                    except:
                        self.browser = p.chromium.launch(headless=False, channel="msedge")

                self.context = self.browser.new_context()
                self.page = self.context.new_page()

                print(f"[*] BIO: Acessando {URL_LOGIN_BIO}...")
                self.page.goto(URL_LOGIN_BIO)

                self.page.wait_for_selector("#username", timeout=15000)
                self.page.wait_for_selector("#password", timeout=15000)

                print("[*] BIO: Efetuando login...")
                self.page.fill("#username", USUARIO_BIO)
                self.page.fill("#password", SENHA_BIO)

                btn_login = self.page.locator("input[type='submit'], button, a.login-btn")
                btn_login.first.click()

                self.page.wait_for_url(re.compile(r"(main|dashboard)\.do"), timeout=30000)
                print("[+] BIO: Login efetuado com sucesso!")

                self.loop_monitoramento()

            except Exception as e:
                print(f"[-] BIO: Erro no monitoramento biométrico: {e}")
            finally:
                if self.browser:
                    try: self.browser.close()
                    except: pass
                if self.queue_ui:
                    self.queue_ui.put({"type": "BIO_STOPPED"})

    def loop_monitoramento(self):
        ultimos_eventos_processados = set()
        eventos_com_foto_processados = set()

        while self.rodando:
            try:
                # Varredura em frames e na página principal
                fontes = [self.page] + (self.page.frames if self.page else [])

                for fonte in fontes:
                    try:
                        # Filtro de URL para evitar processar frames irrelevantes
                        if "192.168.7.9" not in fonte.url and "about:blank" not in fonte.url:
                            continue

                        # --- ORIGEM 1: VARREDURA DIRETA NA TABELA REAL ---
                        linhas_tabela = self.extrair_linhas_tabela_real(fonte)
                        for linha in linhas_tabela:
                            id_usuario, nome_usuario = self.parse_pessoa(linha["pessoa"])
                            evento = linha["evento"]
                            dispositivo = linha["dispositivo"]
                            leitor = linha["leitor"]
                            data_evento = normalizar_data(linha["horario"])

                            # Sanitização
                            if "Verificação de abertura normal" in evento:
                                evento = evento.replace("Verificação de abertura normal", "").strip()
                            if dispositivo == "Geral": dispositivo = ""

                            chave_evento = f"{id_usuario}_{data_evento}"
                            if chave_evento not in ultimos_eventos_processados:
                                registrar_evento(id_usuario, nome_usuario, evento, dispositivo, leitor, data_evento, "", self.queue_ui)
                                ultimos_eventos_processados.add(chave_evento)

                        # --- ORIGEM 2: CAPTURA DO POP-UP ---
                        elementos_alvo = fonte.locator("div[style*='text-align: center']").all()
                        for el in elementos_alvo:
                            html_interno = el.inner_html()
                            if "<p>" in html_interno and "</p>" in html_interno:
                                dados = self.extrair_dados_notificacao(html_interno)
                                if dados:
                                    id_usuario, nome_usuario, evento, dispositivo, data_evento_raw, base64_foto, leitor_popup = dados
                                    data_evento = normalizar_data(data_evento_raw)

                                    if "Verificação de abertura normal" in evento:
                                        evento = evento.replace("Verificação de abertura normal", "").strip()
                                    if dispositivo == "Geral": dispositivo = ""

                                    chave_evento = f"{id_usuario}_{data_evento}"
                                    if base64_foto and chave_evento not in eventos_com_foto_processados:
                                        registrar_evento(id_usuario, nome_usuario, evento, dispositivo, leitor_popup, data_evento, base64_foto, self.queue_ui)
                                        ultimos_eventos_processados.add(chave_evento)
                                        eventos_com_foto_processados.add(chave_evento)
                    except:
                        continue

                if len(ultimos_eventos_processados) > 1000:
                    ultimos_eventos_processados.clear()
                    eventos_com_foto_processados.clear()

                self.page.wait_for_timeout(250)
            except Exception as e:
                print(f"[-] BIO: Erro no loop: {e}")
                time.sleep(2)

    def parse_pessoa(self, pessoa_texto):
        match = re.match(r"(\d+)\((.+)\)", pessoa_texto)
        if match:
            return match.group(1), match.group(2)
        return "Desconhecido", pessoa_texto

    def extrair_linhas_tabela_real(self, frame):
        try:
            js_tabela = """
            () => {
                const resultados = [];
                const trs = document.querySelectorAll('tr');
                for (const tr of trs) {
                    const tds = tr.querySelectorAll('td');
                    if (tds.length >= 8) {
                        const horario = tds[0].innerText ? tds[0].innerText.trim() : tds[0].textContent.trim();
                        if (/^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}$/.test(horario)) {
                            resultados.push({
                                horario: horario,
                                dispositivo: tds[2].innerText ? tds[2].innerText.trim() : tds[2].textContent.trim(),
                                evento: tds[4].innerText ? tds[4].innerText.trim() : tds[4].textContent.trim(),
                                pessoa: tds[6].innerText ? tds[6].innerText.trim() : tds[6].textContent.trim(),
                                leitor: tds[7].innerText ? tds[7].innerText.trim() : tds[7].textContent.trim()
                            });
                        }
                    }
                }
                return resultados;
            }
            """
            return frame.evaluate(js_tabela)
        except:
            return []

    def extrair_dados_notificacao(self, html_interno):
        soup = BeautifulSoup(html_interno, 'html.parser')
        img_tag = soup.find('img')
        base64_foto = ""
        if img_tag and img_tag.get('src') and "base64," in img_tag.get('src'):
            base64_foto = img_tag.get('src')

        p_tags = soup.find_all('p')
        if len(p_tags) >= 3:
            identificacao = p_tags[0].get_text(strip=True)
            evento = p_tags[1].get_text(strip=True)
            data_evento = p_tags[2].get_text(strip=True)

            id_usuario, nome_usuario = self.parse_pessoa(identificacao)
            dispositivo = p_tags[3].get_text(strip=True) if len(p_tags) >= 4 else ""
            leitor = p_tags[4].get_text(strip=True) if len(p_tags) >= 5 else ""
            return id_usuario, nome_usuario, evento, dispositivo, data_evento, base64_foto, leitor
        return None

    def parar(self):
        self.rodando = False

def carregar_dados_sistema():
    """Carrega as configurações do sistema via console antes da interface iniciar."""
    print("="*50)
    print("SISTEMA DE MONITORAMENTO ABI - INICIALIZANDO")
    print("="*50)

    user_dir = os.path.expanduser("~")
    arquivos = {
        "config": os.path.join(user_dir, "config_cameras_abi.json"),
        "grid": os.path.join(user_dir, "grid_config_abi.json"),
        "janela": os.path.join(user_dir, "config_janela_abi.json"),
        "predefinicoes": os.path.join(user_dir, "predefinicoes_grid_abi.json"),
        "ips": os.path.join(user_dir, "lista_ips_abi.json")
    }

    # 0. Janela (Carregada antes para saber num_slots)
    janela_temp = {}
    if os.path.exists(arquivos["janela"]):
        try:
            with open(arquivos["janela"], "r") as f:
                janela_temp = json.load(f)
        except: pass

    num_slots = janela_temp.get("num_slots", 20)

    dados = {
        "config": {},
        "grid": ["0.0.0.0"] * num_slots,
        "janela": janela_temp,
        "predefinicoes": {},
        "ips": []
    }

    # 1. IPs
    print("CMD: Carregando lista de IPs...", end=" ")
    if os.path.exists(arquivos["ips"]):
        try:
            with open(arquivos["ips"], "r", encoding='utf-8') as f:
                dados["ips"] = json.load(f)
            print(f"OK ({len(dados['ips'])} IPs)")
        except: print("ERRO")
    else:
        # Gera lista padrão se não existir
        base = ["192.168.7.2", "192.168.7.3", "192.168.7.4", "192.168.7.20", "192.168.7.21",
                "192.168.7.22", "192.168.7.23", "192.168.7.24", "192.168.7.26", "192.168.7.27",
                "192.168.7.31", "192.168.7.32", "192.168.7.33", "192.168.7.35", "192.168.7.37",
                "192.168.7.39", "192.168.7.43", "192.168.7.78", "192.168.7.79", "192.168.7.81",
                "192.168.7.89", "192.168.7.92", "192.168.7.94", "192.168.7.98", "192.168.7.99"]
        base += [f"192.168.7.{i}" for i in range(100, 216)]
        base += ["192.168.7.237", "192.168.7.246", "192.168.7.247", "192.168.7.248", "192.168.7.249",
                 "192.168.7.250", "192.168.7.251", "192.168.7.252"]
        dados["ips"] = sorted(list(set(base)), key=lambda x: [int(d) for d in x.split('.')])
        print("PADRÃO")

    # 2. Configurações (Nomes)
    print("CMD: Carregando nomes das câmeras...", end=" ")
    if os.path.exists(arquivos["config"]):
        try:
            with open(arquivos["config"], "r", encoding='utf-8') as f:
                dados["config"] = json.load(f)
            print("OK")
        except: print("ERRO")
    else: print("VAZIO")

    # 3. Grid
    print("CMD: Carregando layout do grid...", end=" ")
    if os.path.exists(arquivos["grid"]):
        try:
            with open(arquivos["grid"], "r", encoding='utf-8') as f:
                g = json.load(f)
                if isinstance(g, dict):
                    # Novo formato (Viewport)
                    # Extraímos o grid_cameras (o que era visível no momento do salvamento)
                    if "grid_cameras" in g:
                        dados["grid"] = g["grid_cameras"]
                    dados["grid_full"] = g # Mantém o objeto completo para o __init__
                elif isinstance(g, list):
                    # Legado
                    for i in range(min(len(g), num_slots)): dados["grid"][i] = g[i]
            print("OK")
        except: print("ERRO")
    else: print("PADRÃO")

    # 4. Janela
    print("CMD: Restaurando estado da janela...", end=" ")
    if dados["janela"]:
        print(f"OK ({dados['janela'].get('active_tab', 'Câmeras')})")
    else:
        print("NOVA")

    # 5. Predefinições
    print("CMD: Carregando predefinições...", end=" ")
    if os.path.exists(arquivos["predefinicoes"]):
        try:
            with open(arquivos["predefinicoes"], "r", encoding='utf-8') as f:
                dados["predefinicoes"] = json.load(f)
            print(f"OK ({len(dados['predefinicoes'])} itens)")
        except: print("ERRO")
    else: print("VAZIO")

    print("="*50)
    print("SISTEMA PRONTO. ABRINDO INTERFACE...")
    print("="*50)
    return dados

# --- CLASSE DE VÍDEO OTIMIZADA ---
class CameraHandler:
    def __init__(self, ip, canal=102, user="admin", password="password"):
        self.ip = ip
        self.canal = canal
        self.user = user
        self.password = password
        self.url = self._gerar_url(ip, canal)
        self.cap = None
        self.rodando = False
        self.frame_pil = None
        self.novo_frame = False
        self.lock = threading.Lock()
        self.conectado = False
        self.tamanho_alvo = (640, 480)
        self.interpolation = cv2.INTER_NEAREST
        self.ip_display = ip
        self.nome_display = ""
        self.exibir_info = False
        self.prioridade = False
        self.necessita_reconexao = False
        self.ultimo_erro = None
        self.gravando = False
        self.video_writer = None
        self.caminho_video = None
        self.tempo_inicio_gravacao = 0
        self.timeout_atingido = False
        self.ativo = True
        self.zoom_digital = 1.0
        self.zoom_center = (0.5, 0.5)

    def verificar_alcance(self, timeout=1.0):
        """Verifica se o IP e a porta RTSP (554) estão acessíveis."""
        try:
            with socket.create_connection((self.ip, 554), timeout=timeout):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

    def _gerar_url(self, ip, canal):
        # RTSP String Padrão Hikvision/Intelbras
        import urllib.parse
        encoded_pass = urllib.parse.quote(self.password)
        return f"rtsp://{self.user}:{encoded_pass}@{ip}:554/Streaming/Channels/{canal}"

    def set_prioridade(self, estado):
        with self.lock:
            self.prioridade = estado

    def set_exibir_info(self, estado):
        with self.lock:
            self.exibir_info = estado

    def set_canal(self, novo_canal):
        with self.lock:
            if self.canal != novo_canal:
                self.canal = novo_canal
                self.url = self._gerar_url(self.ip, novo_canal)
                self.necessita_reconexao = True

    def iniciar_gravacao(self, filepath):
        with self.lock:
            self.caminho_video = filepath
            self.gravando = True
            self.tempo_inicio_gravacao = time.time()
            self.timeout_atingido = False
            print(f"Gravação iniciada: {filepath}")

    def parar_gravacao(self):
        with self.lock:
            if self.gravando:
                self.gravando = False
                # O video_writer será fechado pelo loop_leitura para evitar race conditions
                print(f"Sinalizando fim de gravação.")

    def iniciar(self):
        try:
            # 1. Verifica se o dispositivo está na rede
            if not self.verificar_alcance(timeout=0.8):
                self.ultimo_erro = "OFFLINE"
                print(f"Dispositivo offline: {self.ip_display}")
                return False

            print(f"Tentando conectar em: {self.ip_display} (Canal {self.canal})...")

            # 2. Loop de retentativa para abrir o stream
            for tentativa in range(2):
                with sem_conexao:
                    self.cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)

                if hasattr(cv2, 'CAP_PROP_OPEN_TIMEOUT_USEC'):
                    try: self.cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_USEC, 5000000)
                    except: pass

                if hasattr(cv2, 'CAP_PROP_BUFFERSIZE'):
                    try: self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)
                    except: pass

                if self.cap.isOpened():
                    self.rodando = True
                    self.conectado = True
                    self.ultimo_erro = None
                    threading.Thread(target=self.loop_leitura, daemon=True).start()
                    print(f"Conectado com sucesso: {self.ip_display} (Tentativa {tentativa+1})")
                    return True

                print(f"Tentativa {tentativa+1} falhou para {self.ip_display}. Aguardando...")
                time.sleep(0.5)

            self.ultimo_erro = "ERRO RTSP"
            print(f"Falha ao abrir stream após retentativas: {self.ip_display}")
            return False
        except Exception as e:
            self.ultimo_erro = "ERRO DRIVER"
            print(f"Erro driver ({self.ip_display}): {e}")
            return False

    def loop_leitura(self):
        consecutive_failures = 0
        last_process_time = 0

        while self.rodando:
            if self.necessita_reconexao:
                with self.lock:
                    print(f"Alterando canal de {self.ip_display} para {self.canal}...")
                    if self.cap: self.cap.release()
                    with sem_conexao:
                        self.cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
                    if hasattr(cv2, 'CAP_PROP_BUFFERSIZE'):
                        try: self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)
                        except: pass
                    self.necessita_reconexao = False
                    consecutive_failures = 0

            if not self.cap or not self.cap.isOpened():
                time.sleep(0.5)
                continue

            # Grab frame (rápido, não decodifica)
            ret = self.cap.grab()

            if ret:
                consecutive_failures = 0
                now = time.time()

                # Controle de FPS Dinâmico
                target_fps = 25 if (self.prioridade or self.gravando) else 7
                if now - last_process_time < (1.0 / target_fps):
                    continue

                # Se a UI ainda não consumiu o frame anterior, e não é prioridade, podemos pular
                # Se estiver gravando, não pulamos a decodificação
                if self.novo_frame and not self.prioridade and not self.gravando:
                    if now - last_process_time < 0.2:
                        continue

                # Retrieve frame (decodifica) - Mantemos o buffer limpo mesmo se não visível
                ret_ret, frame = self.cap.retrieve()
                if not ret_ret:
                    continue

                last_process_time = now

                # Se a câmera não estiver ativa (visível), pulamos o processamento visual pesado
                # Mas o decoding acima garante que ao voltar, o stream esteja atualizado
                if not self.ativo and not self.gravando:
                    continue

                # Lógica de Gravação
                if self.gravando:
                    elapsed = time.time() - self.tempo_inicio_gravacao

                    # Verifica timeout de 10 minutos (600 segundos) INDEPENDENTE DA UI
                    if elapsed >= 600:
                        self.parar_gravacao()
                        self.timeout_atingido = True
                        # Continua o loop para processar o encerramento do video_writer

                    if self.video_writer is None:
                        try:
                            h_frame, w_frame = frame.shape[:2]
                            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                            self.video_writer = cv2.VideoWriter(self.caminho_video, fourcc, 25.0, (w_frame, h_frame))
                        except Exception as e:
                            print(f"Erro ao iniciar VideoWriter: {e}")
                            self.gravando = False

                    if self.video_writer is not None and self.gravando:
                        self.video_writer.write(frame)

                if not self.gravando:
                    # Se não estiver mais gravando mas o writer ainda existir, fecha-o
                    if self.video_writer is not None:
                        self.video_writer.release()
                        self.video_writer = None
                        print(f"Gravação finalizada no loop.")

                try:
                    w, h = self.tamanho_alvo
                    w, h = int(w), int(h)

                    # Aplicar Zoom Digital (Crop) se necessário
                    if self.zoom_digital > 1.0:
                        h_orig, w_orig = frame.shape[:2]
                        # Calcula o tamanho da janela de crop
                        cw = int(w_orig / self.zoom_digital)
                        ch = int(h_orig / self.zoom_digital)

                        # Usa o centro definido pelo mouse
                        cx, cy = self.zoom_center
                        x_center = int(cx * w_orig)
                        y_center = int(cy * h_orig)

                        # Calcula coordenadas do crop e garante que está dentro da imagem
                        x1 = max(0, min(w_orig - cw, x_center - cw // 2))
                        y1 = max(0, min(h_orig - ch, y_center - ch // 2))

                        frame_cropped = frame[y1:y1+ch, x1:x1+cw]
                        frame_res = cv2.resize(frame_cropped, (w, h), interpolation=self.interpolation)
                    else:
                        if frame.shape[1] != w or frame.shape[0] != h:
                            frame_res = cv2.resize(frame, (w, h), interpolation=self.interpolation)
                        else:
                            frame_res = frame

                    # Adiciona Nome e IP para debug visual apenas se houver espaço e estiver habilitado
                    if h > 50:
                        # Define deslocamento Y se estiver gravando para não sobrepor o indicador REC
                        y_offset_info = 20 if self.gravando else 0

                        if self.exibir_info:
                            # Nome da Câmera (Superior Esquerda)
                            y_nome = 25 + y_offset_info
                            if self.nome_display:
                                cv2.putText(frame_res, self.nome_display, (10, y_nome), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 2)
                                cv2.putText(frame_res, self.nome_display, (10, y_nome), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 1)

                            # IP da Câmera (Linha abaixo)
                            y_ip = 45 + y_offset_info
                            cv2.putText(frame_res, self.ip_display, (10, y_ip), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 2)
                            cv2.putText(frame_res, self.ip_display, (10, y_ip), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 1)

                        # Indicador de Gravação (Sempre visível se estiver gravando)
                        if self.gravando:
                            elapsed = time.time() - self.tempo_inicio_gravacao
                            mins, secs = divmod(int(elapsed), 60)
                            timer_txt = f"REC {mins:02d}:{secs:02d} / 10:00"

                            # Calcula tamanho do texto para posicionamento dinâmico
                            (tw_text, th_text), baseline = cv2.getTextSize(timer_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)

                            # Posicionamento fixo no canto superior esquerdo
                            tx, ty = 10, 25

                            # Adiciona fundo semi-transparente para melhor legibilidade
                            cv2.rectangle(frame_res, (tx - 5, ty - th_text - 5), (tx + tw_text + 5, ty + baseline + 5), (0,0,0), -1)
                            cv2.putText(frame_res, timer_txt, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 2) # Borda branca
                            cv2.putText(frame_res, timer_txt, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,255), 1) # Texto vermelho

                    rgb = cv2.cvtColor(frame_res, cv2.COLOR_BGR2RGB)
                    pil_img = Image.fromarray(rgb)

                    with self.lock:
                        self.frame_pil = pil_img
                        self.novo_frame = True
                except Exception as e:
                    time.sleep(0.01)
            else:
                consecutive_failures += 1
                if consecutive_failures > 100: # Reduzido para 100 para reconectar mais rápido
                    print(f"LOG: Camera {self.ip_display} sem frames. Tentando reconectar...")
                    if self.cap: self.cap.release()
                    with sem_conexao:
                        self.cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
                    consecutive_failures = 0

                # Sleep progressivo em caso de falha para evitar overhead de CPU
                sleep_time = min(0.2, 0.01 * consecutive_failures)
                time.sleep(sleep_time)

        if self.cap:
            self.cap.release()
        if self.video_writer:
            self.video_writer.release()
            self.video_writer = None
        self.rodando = False
        self.conectado = False

    def pegar_frame(self):
        with self.lock:
            self.novo_frame = False
            return self.frame_pil

    def parar(self):
        self.rodando = False
        self.parar_gravacao()
        self.conectado = False

# --- INTERFACE PRINCIPAL ---
class CentralMonitoramento(ctk.CTk):
    def _get_window_scaling(self):
        try:
            return super()._get_window_scaling()
        except:
            return 1.0

    BG_MAIN = "#121212"
    BG_SIDEBAR = "#1A1A1A"
    BG_PANEL = "#1E1E1E"
    BG_LIST = "#252525"
    ACCENT_RED = "#D32F2F"
    ACCENT_WINE = "#7B1010"
    TEXT_P = "#E0E0E0"
    TEXT_S = "#9E9E9E"
    GRAY_DARK = "#424242"

    def configurar_variaveis_grid(self, num_slots):
        """Define as dimensões do grid com base na quantidade de slots."""
        self.num_slots = num_slots
        if num_slots == 40:
            self.grid_rows = 5
            self.grid_cols = 8
        else:
            self.num_slots = 20 # Fallback
            self.grid_rows = 4
            self.grid_cols = 5

    def __init__(self, dados_iniciais=None):
        super().__init__()
        print("SISTEMA: Inicializando interface...")

        # Carrega preferência de num_slots
        janela_config = dados_iniciais.get("janela", {}) if dados_iniciais else {}
        self.configurar_variaveis_grid(janela_config.get("num_slots", 20))

        self.title("Sistema de Monitoramento ABI - Full Control V5 + PTZ")
        self.geometry("1800x1000")
        ctk.set_appearance_mode("Dark")

        # Credenciais para PTZ
        self.user_ptz = "admin"
        self.pass_ptz = "1357gov@"

        self.protocol("WM_DELETE_WINDOW", self.ao_fechar)

        # Binds de Teclado
        self.bind("<Escape>", lambda event: self.sair_tela_cheia())

        # Binds para PTZ
        self.bind("<MouseWheel>", self.ao_scroll_mouse)
        self.bind("<Button-4>", self.ao_scroll_mouse)
        self.bind("<Button-5>", self.ao_scroll_mouse)

        self.bind("<KeyPress-Up>", lambda e: self.ao_tecla_direcional("UP"))
        self.bind("<KeyPress-Down>", lambda e: self.ao_tecla_direcional("DOWN"))
        self.bind("<KeyPress-Left>", lambda e: self.ao_tecla_direcional("LEFT"))
        self.bind("<KeyPress-Right>", lambda e: self.ao_tecla_direcional("RIGHT"))

        self.bind("<KeyRelease-Up>", lambda e: self.ao_tecla_solta("UP"))
        self.bind("<KeyRelease-Down>", lambda e: self.ao_tecla_solta("DOWN"))
        self.bind("<KeyRelease-Left>", lambda e: self.ao_tecla_solta("LEFT"))
        self.bind("<KeyRelease-Right>", lambda e: self.ao_tecla_solta("RIGHT"))

        # Binds para Zoom Digital (Drag)
        self.bind_all("<KeyPress-Control_L>", lambda e: self._atualizar_estado_ctrl(e, True))
        self.bind_all("<KeyPress-Control_R>", lambda e: self._atualizar_estado_ctrl(e, True))
        self.bind_all("<KeyRelease-Control_L>", lambda e: self._atualizar_estado_ctrl(e, False))
        self.bind_all("<KeyRelease-Control_R>", lambda e: self._atualizar_estado_ctrl(e, False))

        # Configurações de Arquivos
        user_dir = os.path.expanduser("~")
        self.arquivo_config = os.path.join(user_dir, "config_cameras_abi.json")
        self.arquivo_grid = os.path.join(user_dir, "grid_config_abi.json")
        self.arquivo_janela = os.path.join(user_dir, "config_janela_abi.json")
        self.arquivo_predefinicoes = os.path.join(user_dir, "predefinicoes_grid_abi.json")
        self.arquivo_ips = os.path.join(user_dir, "lista_ips_abi.json")
        self.diretorio_prints = os.path.join(user_dir, "cameras_prints_abi")
        os.makedirs(self.diretorio_prints, exist_ok=True)

        self.botoes_referencia = {}
        self.ip_selecionado = None
        self.predefinicao_widgets = {}
        self.camera_handlers = {}
        self.em_tela_cheia = False
        self.slot_maximized = None
        self.slot_selecionado = 0
        self.ip_seletor_atual = [192, 168, 7, 0]
        self.octet_entries = []
        self.press_data = None
        self.fila_conexoes = queue.Queue()
        self.fila_pendente_conexoes = queue.Queue()
        self.ips_em_fila = set()
        self.cooldown_conexoes = {}
        self.tecla_pressionada = None
        self.ultima_predefinicao = None
        self.aba_ativa = "Câmeras"
        self.tamanho_preview = "Pequeno"
        self.iconic_state = False
        self.zoom_stop_timer = None
        self.ctrl_pressionado = False
        self.predefinicoes_desbloqueadas = set()
        self.gravando_tudo = False
        self.video_writer_tudo = None
        self.caminho_video_tudo = None
        self.ultimo_frame_tudo_tempo = 0
        self.fps_tudo = 10

        # Grid Virtual e Viewport
        self.virtual_grid = {}
        self.offset_x = 0
        self.offset_y = 0
        self.eventos_bio_cards = {}
        self.queue_bio = queue.Queue()

        if dados_iniciais:
            self.dados_cameras = dados_iniciais.get("config", {})
            self.predefinicoes = dados_iniciais.get("predefinicoes", {})
            self.ips_unicos = dados_iniciais.get("ips", [])

            # Regra de Inicialização ABI:
            # 1. Se houver predefinições, carrega a primeira da lista (ordem alfabética)
            # 2. Se não houver, o grid começa totalmente vazio (sem conexões)
            if self.predefinicoes:
                nomes_ordenados = sorted(self.predefinicoes.keys(), key=str.lower)
                primeira = nomes_ordenados[0]
                dados_primeira = self.predefinicoes[primeira]

                if isinstance(dados_primeira, dict):
                    self.grid_cameras = list(dados_primeira.get("grid_cameras", ["0.0.0.0"] * self.num_slots))
                else:
                    self.grid_cameras = list(dados_primeira)

                # Garante que tenha o número correto de slots
                while len(self.grid_cameras) < self.num_slots: self.grid_cameras.append("0.0.0.0")
                self.ultima_predefinicao = primeira
            else:
                self.grid_cameras = ["0.0.0.0"] * self.num_slots
                self.ultima_predefinicao = None

            janela = dados_iniciais.get("janela", {})
            geom = janela.get("geometry")
            if geom: self.geometry(geom)
            self.aba_ativa = janela.get("active_tab", "Câmeras")
            self.slot_selecionado = janela.get("slot_selecionado", 0)
            self.tamanho_preview = janela.get("tamanho_preview", "Pequeno")
        else:
            print("SISTEMA: Carregando configurações...")
            self.carregar_posicao_janela()
            self.predefinicoes = self.carregar_predefinicoes()
            self.ips_unicos = self.carregar_lista_ips()
            self.dados_cameras = self.carregar_config()

            if self.predefinicoes:
                nomes_ordenados = sorted(self.predefinicoes.keys(), key=str.lower)
                primeira = nomes_ordenados[0]
                dados_primeira = self.predefinicoes[primeira]

                if isinstance(dados_primeira, dict):
                    self.grid_cameras = list(dados_primeira.get("grid_cameras", ["0.0.0.0"] * self.num_slots))
                else:
                    self.grid_cameras = list(dados_primeira)

                while len(self.grid_cameras) < self.num_slots: self.grid_cameras.append("0.0.0.0")
                self.ultima_predefinicao = primeira
            else:
                self.grid_cameras = ["0.0.0.0"] * self.num_slots
                self.ultima_predefinicao = None

        # Se houver dados completos do grid (novo formato), aplica agora
        if dados_iniciais and "grid_full" in dados_iniciais:
            g = dados_iniciais["grid_full"]
            self.offset_x = g.get("offset_x", 0)
            self.offset_y = g.get("offset_y", 0)
            vg_data = g.get("virtual_grid", {})
            self.virtual_grid = {}
            for k, v in vg_data.items():
                try:
                    r, c = map(int, k.split(','))
                    self.virtual_grid[(r, c)] = v
                except: pass
        else:
            # Semeia o grid virtual inicial com base no grid_cameras carregado (Legado ou Sem Predefinição)
            for i, ip in enumerate(self.grid_cameras):
                r, c = i // self.grid_cols, i % self.grid_cols
                self.virtual_grid[(r, c)] = ip

        # Inicializa ícones de navegação
        self._inicializar_icones_navegacao()

        # Cache persistente de CTkImage por slot para evitar "pyimage" explosion
        self.slot_ctk_images = [None] * self.num_slots
        # Cache de estado da UI para evitar chamadas redundantes ao Tcl/Tk
        self.cache_ui_text = [None] * self.num_slots
        self.cache_ui_image = [None] * self.num_slots
        self.cache_ui_size = [None] * self.num_slots
        # Imagem 1x1 transparente para resets seguros
        self.img_vazia = ctk.CTkImage(Image.new('RGBA', (1, 1), (0,0,0,0)), size=(1, 1))

        # Controle da Sidebar
        self.sidebar_visible = True

        # --- LAYOUT ATUALIZADO ---
        self.grid_columnconfigure(0, weight=0) # Sidebar fixa
        self.grid_columnconfigure(1, weight=0) # Botão toggle fixo
        self.grid_columnconfigure(2, weight=1) # Main expande
        self.grid_columnconfigure(3, weight=0) # Botão toggle direita
        self.grid_columnconfigure(4, weight=0, minsize=400) # Sidebar direita
        self.grid_rowconfigure(0, weight=1)

        # 1. Sidebar (Coluna 0)
        self.sidebar = ctk.CTkFrame(self, width=320, corner_radius=0, fg_color=self.BG_SIDEBAR)
        self.sidebar.grid(row=0, column=0, sticky="nsew")

        self.tabview = ctk.CTkTabview(self.sidebar, fg_color="transparent",
                                      segmented_button_selected_color=self.ACCENT_RED,
                                      segmented_button_unselected_hover_color=self.ACCENT_WINE,
                                      text_color=self.TEXT_P)
        self.tabview.pack(expand=True, fill="both", padx=5, pady=5)
        self.tabview.add("Câmeras")
        self.tabview.add("Predefinições")

        # Conteúdo da Sidebar (Câmeras)
        tab_cams = self.tabview.tab("Câmeras")

        # Seletor de IP Manual
        self.criar_seletor_ip(tab_cams)

        # Botão de Configurações
        self.btn_config = ctk.CTkButton(tab_cams, text="⚙ Configurações",
                                         fg_color=self.GRAY_DARK, hover_color=self.ACCENT_RED,
                                         command=self.abrir_janela_configuracoes)
        self.btn_config.pack(pady=(10, 5), padx=10, fill="x")

        self.btn_gravar_tudo = ctk.CTkButton(tab_cams, text="Gravar Tudo",
                                         fg_color=self.GRAY_DARK, hover_color=self.ACCENT_RED,
                                         command=self.toggle_gravacao_tudo)
        self.btn_gravar_tudo.pack(pady=(0, 10), padx=10, fill="x")

        self.frame_busca = ctk.CTkFrame(tab_cams, fg_color="transparent")
        self.frame_busca.pack(fill="x", padx=5, pady=5)

        self.entry_busca = ctk.CTkEntry(self.frame_busca, placeholder_text="Filtrar...")
        self.entry_busca.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.entry_busca.bind("<KeyRelease>", lambda e: self.filtrar_lista())

        self.btn_add_cam = ctk.CTkButton(self.frame_busca, text="+", width=35,
                                          fg_color=self.ACCENT_WINE, hover_color=self.ACCENT_RED,
                                          command=lambda: self.solicitar_senha(self.abrir_modal_adicionar_camera))
        self.btn_add_cam.pack(side="right")

        self.scroll_frame = ctk.CTkScrollableFrame(tab_cams, fg_color=self.BG_LIST)
        self.scroll_frame.pack(expand=True, fill="both", padx=0, pady=5)

        # Conteúdo da Sidebar (Predefinições)
        tab_predefinicoes = self.tabview.tab("Predefinições")
        self.btn_salvar_predefinicao = ctk.CTkButton(tab_predefinicoes, text="Salvar Predefinição Atual",
                                                fg_color=self.ACCENT_WINE, hover_color=self.ACCENT_RED,
                                                command=self.salvar_predefinicao_atual)
        self.btn_salvar_predefinicao.pack(fill="x", padx=10, pady=(10, 5))

        frame_import_export = ctk.CTkFrame(tab_predefinicoes, fg_color="transparent")
        frame_import_export.pack(fill="x", padx=10, pady=(0, 10))

        self.btn_exportar_predefinicoes = ctk.CTkButton(frame_import_export, text="Exportar",
                                                        fg_color=self.GRAY_DARK, hover_color=self.ACCENT_RED,
                                                        command=self.exportar_predefinicoes)
        self.btn_exportar_predefinicoes.pack(side="left", expand=True, fill="x", padx=(0, 2))

        self.btn_importar_predefinicoes = ctk.CTkButton(frame_import_export, text="Importar",
                                                        fg_color=self.GRAY_DARK, hover_color=self.ACCENT_RED,
                                                        command=self.importar_predefinicoes)
        self.btn_importar_predefinicoes.pack(side="left", expand=True, fill="x", padx=(2, 0))

        ctk.CTkLabel(tab_predefinicoes, text="LISTA DE PREDEFINIÇÕES", font=("Roboto", 14, "bold"), text_color=self.TEXT_S).pack(pady=5)
        self.scroll_predefinicoes = ctk.CTkScrollableFrame(tab_predefinicoes, fg_color=self.BG_LIST)
        self.scroll_predefinicoes.pack(expand=True, fill="both", padx=5, pady=5)

        # 2. Container Toggle Sidebar (Coluna 1)
        self.container_toggle = ctk.CTkFrame(self, fg_color=self.BG_PANEL, corner_radius=0)
        self.container_toggle.grid(row=0, column=1, sticky="ns")

        self.lbl_lista_vertical = ctk.CTkLabel(
            self.container_toggle,
            text="L\nI\nS\nT\nA",
            font=("Roboto", 11, "bold"),
            text_color=self.TEXT_S
        )
        self.lbl_lista_vertical.pack(side="left", padx=(2, 0))

        self.btn_toggle_sidebar = ctk.CTkButton(
            self.container_toggle,
            text="◀",
            width=40,
            corner_radius=0,
            font=("Roboto", 24, "bold"),
            fg_color=self.BG_PANEL,
            hover_color=self.ACCENT_WINE,
            text_color=self.ACCENT_RED,
            command=self.toggle_sidebar
        )
        self.btn_toggle_sidebar.pack(side="right", fill="y")


        # 3. Main Frame (Coluna 2)
        self.main_frame = ctk.CTkFrame(self, fg_color=self.BG_MAIN, corner_radius=0)
        self.main_frame.grid(row=0, column=2, sticky="nsew")

        # 4. Container Toggle Sidebar Direita (Coluna 3)
        self.container_toggle_right = ctk.CTkFrame(self, fg_color=self.BG_PANEL, corner_radius=0)
        self.container_toggle_right.grid(row=0, column=3, sticky="ns")

        self.lbl_bio_vertical = ctk.CTkLabel(
            self.container_toggle_right,
            text="B\nI\nO\nM\nE\nT\nR\nI\nA",
            font=("Roboto", 11, "bold"),
            text_color=self.TEXT_S
        )
        self.lbl_bio_vertical.pack(side="right", padx=(0, 2))

        self.btn_toggle_sidebar_right = ctk.CTkButton(
            self.container_toggle_right,
            text="▶",
            width=40,
            corner_radius=0,
            font=("Roboto", 24, "bold"),
            fg_color=self.BG_PANEL,
            hover_color=self.ACCENT_WINE,
            text_color=self.ACCENT_RED,
            command=self.toggle_sidebar_right
        )
        self.btn_toggle_sidebar_right.pack(side="left", fill="y")

        # 5. Sidebar Direita (Coluna 4)
        self.sidebar_right = ctk.CTkFrame(self, width=400, corner_radius=0, fg_color=self.BG_SIDEBAR)
        self.sidebar_right.grid(row=0, column=4, sticky="nsew")
        self.sidebar_right_visible = True

        # Título da Sidebar Direita
        self.header_bio = ctk.CTkFrame(self.sidebar_right, fg_color=self.BG_PANEL, height=110, corner_radius=0)
        self.header_bio.pack(fill="x")
        self.header_bio.pack_propagate(False)

        ctk.CTkLabel(self.header_bio, text="EVENTOS BIOMÉTRICOS", font=("Roboto", 16, "bold"), text_color=self.ACCENT_RED).pack(pady=(5,0))

        container_btns_bio = ctk.CTkFrame(self.header_bio, fg_color="transparent")
        container_btns_bio.pack(pady=5)

        self.btn_iniciar_bio = ctk.CTkButton(container_btns_bio, text="Iniciar", width=100, height=24,
                                             fg_color=self.GRAY_DARK, hover_color=self.ACCENT_RED,
                                             font=("Roboto", 11, "bold"),
                                             command=self._iniciar_monitoramento_bio)
        self.btn_iniciar_bio.pack(side="left", padx=5)

        self.btn_parar_bio = ctk.CTkButton(container_btns_bio, text="Parar", width=100, height=24,
                                           fg_color=self.GRAY_DARK, hover_color=self.ACCENT_RED,
                                           font=("Roboto", 11, "bold"),
                                           state="disabled",
                                           command=self._parar_monitoramento_bio)
        self.btn_parar_bio.pack(side="left", padx=5)

        self.lbl_total_bio = ctk.CTkLabel(self.header_bio, text="0 total", font=("Roboto", 10), text_color=self.TEXT_S)
        self.lbl_total_bio.pack()

        # Lista de Eventos
        self.scroll_eventos = ctk.CTkScrollableFrame(self.sidebar_right, fg_color="#0b0f19")
        self.scroll_eventos.pack(expand=True, fill="both", padx=5, pady=5)

        self.criar_interface_grid()

        print("SISTEMA: Atualizando listas da UI...")
        self.atualizar_lista_cameras_ui()

        # Prepara visual inicial dos slots baseado na regra de predefinição
        for i, ip in enumerate(self.grid_cameras):
            if ip and ip != "0.0.0.0":
                txt = "CONECTANDO..." if i != self.slot_selecionado else f"CONECTANDO...\n{ip}"
                self.slot_labels[i].configure(text=txt)
            else:
                self.slot_labels[i].configure(text="")

        self.selecionar_slot(self.slot_selecionado)
        self.restaurar_grid()

        # Delay inicial: A interface carrega primeiro, as câmeras conectam depois
        # Reduzido para 300ms para uma experiência mais "fluida"
        self.after(300, self._iniciar_sistema_conexoes)
        print("SISTEMA: Pronto.")
        
        def safe_zoom():
            try: self.state("zoomed")
            except: pass
        self.after(200, safe_zoom)

        self.atualizar_lista_predefinicoes_ui()

        # Restaura estado da interface (aba ativa)
        try:
            if self.aba_ativa in ["Câmeras", "Predefinições"]:
                self.tabview.set(self.aba_ativa)
        except: pass

        # A conexão inicial agora é gerenciada exclusivamente por _iniciar_sistema_conexoes

        self.last_button_state = None
        self._window_scaling = self._get_window_scaling()
        self._loop_counter = 0
        self.loop_exibicao()

    def _iniciar_monitoramento_bio(self):
        if hasattr(self, 'bio_thread') and self.bio_thread.is_alive():
            self.abrir_modal_alerta("Aviso", "O monitoramento biométrico já está em execução.")
            return

        print("SISTEMA: Iniciando monitoramento biométrico...")
        self.btn_iniciar_bio.configure(state="disabled", text="Ativo", fg_color=self.ACCENT_RED)
        self.btn_parar_bio.configure(state="normal")
        self.bio_thread = BioMonitorThread(self.queue_bio)
        self.bio_thread.start()

    def _parar_monitoramento_bio(self):
        if hasattr(self, 'bio_thread'):
            print("SISTEMA: Parando monitoramento biométrico...")
            self.btn_parar_bio.configure(state="disabled", text="Parando...")
            self.bio_thread.parar()

    def _processar_queue_bio(self):
        """Processa eventos biométricos vindos da thread de monitoramento."""
        try:
            while not self.queue_bio.empty():
                msg = self.queue_bio.get_nowait()
                if msg.get("type") == "BIO_EVENT":
                    self.adicionar_card_evento(msg["data"])
                elif msg.get("type") == "BIO_STOPPED":
                    self.btn_iniciar_bio.configure(state="normal", text="Iniciar", fg_color=self.GRAY_DARK)
                    self.btn_parar_bio.configure(state="disabled", text="Parar")
        except:
            pass

    def adicionar_card_evento(self, dados):
        id_reg = f"{dados['id_usuario']}_{dados['data_evento'].replace(':', '_')}"

        if id_reg in self.eventos_bio_cards:
            existente = self.eventos_bio_cards[id_reg]
            if existente.get('tem_foto') or not dados.get('foto'):
                return
            existente['frame'].destroy()
            del self.eventos_bio_cards[id_reg]

        # Card de evento (Mais justo possível)
        card = ctk.CTkFrame(self.scroll_eventos, fg_color="#1a1a1a", border_width=1, border_color="#333333")

        filhos = self.scroll_eventos.winfo_children()
        for f in filhos: f.pack_forget()

        card.pack(fill="x", pady=2, padx=2)
        for f in filhos[:49]:
            f.pack(fill="x", pady=2, padx=2)

        if len(filhos) >= 50:
            for f in filhos[49:]:
                for k, v in list(self.eventos_bio_cards.items()):
                    if v['frame'] == f:
                        del self.eventos_bio_cards[k]
                        break
                f.destroy()

        # Borda colorida à esquerda (ACCENT_RED)
        borda_cor = self.ACCENT_RED if dados.get("foto") else "#f59e0b"
        borda_l = ctk.CTkFrame(card, width=3, fg_color=borda_cor)
        borda_l.pack(side="left", fill="y")

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        # Foto e Info (Foto 2x maior: 100x100)
        header_f = ctk.CTkFrame(inner, fg_color="transparent")
        header_f.pack(fill="x")

        tem_foto = False
        if dados.get("foto"):
            try:
                img_data = base64.b64decode(dados["foto"].split(",")[1] if "," in dados["foto"] else dados["foto"])
                img_pil = Image.open(io.BytesIO(img_data))
                img_ctk = ctk.CTkImage(img_pil, size=(100, 100))
                lbl_img = ctk.CTkLabel(header_f, image=img_ctk, text="", width=100, height=100)
                lbl_img.pack(side="left", padx=(0, 6))
                tem_foto = True
            except:
                ctk.CTkLabel(header_f, text="👤", font=("Roboto", 40)).pack(side="left", padx=(0, 6))
        else:
            ctk.CTkLabel(header_f, text="👤", font=("Roboto", 40)).pack(side="left", padx=(0, 6))

        info_f = ctk.CTkFrame(header_f, fg_color="transparent")
        info_f.pack(side="left", fill="both", expand=True)

        ctk.CTkLabel(info_f, text=f"ID: {dados['id_usuario']}", font=("Roboto", 10, "bold"),
                     text_color=self.ACCENT_RED, fg_color="#000000", corner_radius=2).pack(anchor="w")

        # Nome maior e em negrito
        lbl_nome_bio = ctk.CTkLabel(info_f, text=dados["nome"].upper(), font=("Roboto", 12, "bold"), text_color="white",
                                    anchor="w", justify="left", wraplength=240)
        lbl_nome_bio.pack(fill="x", anchor="w", pady=(2, 0))
        try: lbl_nome_bio._label.configure(wraplength=240)
        except: pass

        # Data/Hora logo abaixo do nome
        ctk.CTkLabel(info_f, text=f"🕒 {dados['data_evento']}", font=("Roboto", 10), text_color="#f59e0b").pack(anchor="w", pady=(2, 0))

        # Detalhes (Leitor, Evento, Dispositivo) mais compactos
        detalhes_f = ctk.CTkFrame(inner, fg_color="transparent")
        detalhes_f.pack(fill="x", pady=(4, 0))

        if dados.get("leitor"):
            lbl_leitor_bio = ctk.CTkLabel(detalhes_f, text=f"📍 {dados['leitor']}", font=("Roboto", 10, "bold"), text_color=self.ACCENT_RED,
                                          anchor="w", justify="left", wraplength=350)
            lbl_leitor_bio.pack(fill="x", anchor="w")
            try: lbl_leitor_bio._label.configure(wraplength=350)
            except: pass

        if dados.get("evento"):
            lbl_evento_bio = ctk.CTkLabel(detalhes_f, text=dados["evento"], font=("Roboto", 10), text_color="#facc15",
                                          anchor="w", justify="left", wraplength=350)
            lbl_evento_bio.pack(fill="x", anchor="w")
            try: lbl_evento_bio._label.configure(wraplength=350)
            except: pass

        if dados.get("dispositivo"):
            lbl_disp_bio = ctk.CTkLabel(detalhes_f, text=f"🖥️ {dados['dispositivo']}", font=("Roboto", 10), text_color="#999999",
                                        anchor="w", justify="left", wraplength=350)
            lbl_disp_bio.pack(fill="x", anchor="w")
            try: lbl_disp_bio._label.configure(wraplength=350)
            except: pass

        self.eventos_bio_cards[id_reg] = {'frame': card, 'tem_foto': tem_foto}
        self.lbl_total_bio.configure(text=f"{len(self.eventos_bio_cards)} total")

    def _iniciar_sistema_conexoes(self):
        """Inicia o despachante de conexões sequenciais."""
        print("SISTEMA: Iniciando despachante de conexões...")

        # Se houver uma predefinição selecionada no __init__, inicia as conexões para ela
        if self.ultima_predefinicao:
            print(f"SISTEMA: Disparando conexões para predefinição: {self.ultima_predefinicao}")
            self.aplicar_predefinicao(self.ultima_predefinicao)
        else:
            print("SISTEMA: Nenhuma predefinição encontrada. Grid vazio.")

        self._processar_fila_conexoes()

    def _processar_fila_conexoes(self):
        """Processa a fila de conexões uma a uma para evitar sobrecarga e travamentos."""
        try:
            if not self.fila_pendente_conexoes.empty():
                ip, canal = self.fila_pendente_conexoes.get_nowait()
                self.ips_em_fila.discard(ip)

                # Verifica relevância
                if ip in self.grid_cameras:
                    handler = self.camera_handlers.get(ip)
                    if not (handler and handler != "CONECTANDO" and getattr(handler, 'rodando', False)):
                        # Dispara a thread de conexão real para este IP específico
                        threading.Thread(target=self._thread_conectar, args=(ip, canal), daemon=True).start()
                else:
                    if self.camera_handlers.get(ip) == "CONECTANDO":
                        del self.camera_handlers[ip]

            # Agenda o próximo processamento (sequencial e controlado)
            self.after(50, self._processar_fila_conexoes)
        except Exception as e:
            self.after(500, self._processar_fila_conexoes)

    # --- LÓGICA DO TOGGLE DA SIDEBAR ---
    def exibir_fantasma_drag(self, x_root, y_root, texto):
        """Exibe um label flutuante acompanhando o mouse durante o arrasto."""
        if not hasattr(self, 'fantasma_drag') or self.fantasma_drag is None:
            self.fantasma_drag = ctk.CTkLabel(
                self,
                text=texto,
                fg_color=self.ACCENT_WINE,
                text_color="white",
                corner_radius=5,
                padx=10,
                pady=5,
                font=("Roboto", 12, "bold")
            )

        # Ajusta posição para seguir o mouse com offset
        x = x_root - self.winfo_rootx() + 20
        y = y_root - self.winfo_rooty() + 20
        self.fantasma_drag.place(x=x, y=y)
        self.fantasma_drag.lift()

    def fechar_fantasma_drag(self):
        """Remove o label flutuante de arrasto."""
        if hasattr(self, 'fantasma_drag') and self.fantasma_drag:
            self.fantasma_drag.destroy()
            self.fantasma_drag = None

    def toggle_sidebar(self):
        if self.sidebar_visible:
            self.sidebar.grid_forget()
            self.btn_toggle_sidebar.configure(text="▶")
            self.sidebar_visible = False
        else:
            self.sidebar.grid(row=0, column=0, sticky="nsew")
            self.btn_toggle_sidebar.configure(text="◀")
            self.sidebar_visible = True

    def toggle_sidebar_right(self):
        if self.sidebar_right_visible:
            self.sidebar_right.grid_forget()
            self.grid_columnconfigure(4, minsize=0)
            self.btn_toggle_sidebar_right.configure(text="◀")
            self.sidebar_right_visible = False
        else:
            self.grid_columnconfigure(4, minsize=400)
            self.sidebar_right.grid(row=0, column=4, sticky="nsew")
            self.btn_toggle_sidebar_right.configure(text="▶")
            self.sidebar_right_visible = True

    # --- LÓGICA PTZ ---
    def ao_scroll_mouse(self, event):
        # Verifica se o mouse está sobre a sidebar para rolar as listas
        try:
            if self.sidebar.winfo_viewable():
                sx = self.sidebar.winfo_rootx()
                sy = self.sidebar.winfo_rooty()
                sw = self.sidebar.winfo_width()
                sh = self.sidebar.winfo_height()

                if sx <= event.x_root <= sx + sw and sy <= event.y_root <= sy + sh:
                    aba = self.tabview.get()
                    scroll_obj = self.scroll_frame if aba == "Câmeras" else self.scroll_predefinicoes
                    canvas = getattr(scroll_obj, "_parent_canvas", None) or getattr(scroll_obj, "_canvas", None)
                    if canvas:
                        cur_y = canvas.yview()[0]
                        try:
                            # Calcula o passo baseado na altura de um item (Frame + pady)
                            itens = scroll_obj.winfo_children()
                            if itens:
                                item_h = itens[0].winfo_height() + 4
                                bbox = canvas.bbox("all")
                                total_h = bbox[3] if bbox else 1
                                step = item_h / total_h if total_h > 0 else 0.05
                            else:
                                step = 0.05
                        except:
                            step = 0.05

                        if event.num == 4: # Linux up
                            canvas.yview_moveto(cur_y - step)
                        elif event.num == 5: # Linux down
                            canvas.yview_moveto(cur_y + step)
                        elif event.delta: # Windows/Mac
                            canvas.yview_moveto(cur_y + (step * int(-1 * (event.delta / 120))))
                    return "break"

            # Verifica se o mouse está sobre a sidebar DIREITA (Biometria)
            if self.sidebar_right.winfo_viewable():
                rx = self.sidebar_right.winfo_rootx()
                ry = self.sidebar_right.winfo_rooty()
                rw = self.sidebar_right.winfo_width()
                rh = self.sidebar_right.winfo_height()

                if rx <= event.x_root <= rx + rw and ry <= event.y_root <= ry + rh:
                    scroll_obj = self.scroll_eventos
                    canvas = getattr(scroll_obj, "_parent_canvas", None) or getattr(scroll_obj, "_canvas", None)
                    if canvas:
                        cur_y = canvas.yview()[0]
                        step = 0.05
                        if event.num == 4: # Linux up
                            canvas.yview_moveto(cur_y - step)
                        elif event.num == 5: # Linux down
                            canvas.yview_moveto(cur_y + step)
                        elif event.delta: # Windows/Mac
                            canvas.yview_moveto(cur_y + (step * int(-1 * (event.delta / 120))))
                    return "break"
        except: pass

        direcao = None
        if event.num == 4 or event.delta > 0:
            direcao = "ZOOM_IN"
        elif event.num == 5 or event.delta < 0:
            direcao = "ZOOM_OUT"

        # Zoom Digital com CTRL + Scroll
        if (event.state & 0x0004) or self.ctrl_pressionado: # Control Mask
            if direcao:
                self.executar_zoom_digital(event, direcao)
            return "break"

        if direcao:
            self.comando_ptz(direcao)

            # Cancela timer anterior se houver
            if self.zoom_stop_timer:
                self.after_cancel(self.zoom_stop_timer)
                self.zoom_stop_timer = None

            # Agenda parada do zoom após 300ms de inatividade
            self.zoom_stop_timer = self.after(300, self._parar_zoom_automatico)

        return "break" # Evita propagação de evento e duplicidade

    def _parar_zoom_automatico(self):
        self.comando_ptz("STOP")
        self.zoom_stop_timer = None

    def _atualizar_estado_ctrl(self, event, estado):
        self.ctrl_pressionado = estado
        self.config_cursor_agarrar(estado)

    def config_cursor_agarrar(self, estado):
        cursor = "fleur" if estado else ""
        try: self.configure(cursor=cursor)
        except: pass

    def ao_arrastar_slot(self, event, index):
        if not self.press_data: return

        dx = event.x_root - self.press_data["x"]
        dy = event.y_root - self.press_data["y"]

        # Atualiza posição para o próximo frame de arrasto
        self.press_data["x"] = event.x_root
        self.press_data["y"] = event.y_root

        # Se CTRL estiver pressionado, arrasta o zoom
        if (event.state & 0x0004) or self.ctrl_pressionado:
            self.fechar_fantasma_drag()
            ip = self.grid_cameras[index]
            handler = self.camera_handlers.get(ip)
            if handler and handler != "CONECTANDO" and handler.zoom_digital > 1.0:
                frm = self.slot_frames[index]
                # Normaliza o deslocamento baseado no zoom e tamanho do slot
                # Invertemos o sinal porque arrastar a imagem para a direita move o centro do crop para a esquerda
                shift_x = (dx / frm.winfo_width()) / handler.zoom_digital
                shift_y = (dy / frm.winfo_height()) / handler.zoom_digital

                with handler.lock:
                    nx = max(0.0, min(1.0, handler.zoom_center[0] - shift_x))
                    ny = max(0.0, min(1.0, handler.zoom_center[1] - shift_y))
                    handler.zoom_center = (nx, ny)
        else:
            # Arrasto de câmera para troca de posição
            ip = self.grid_cameras[index]
            if ip and ip != "0.0.0.0":
                nome = self.dados_cameras.get(ip, ip)
                self.exibir_fantasma_drag(event.x_root, event.y_root, nome)

    def executar_zoom_digital(self, event, direcao):
        idx = self.encontrar_slot_por_coords(event.x_root, event.y_root)
        if idx is not None:
            frm = self.slot_frames[idx]

            # Se algum botão do mouse estiver pressionado (1, 2 ou 3), centraliza o zoom
            # Bitmask 0x0700: Button1 (0x0100), Button2 (0x0200), Button3 (0x0400)
            if event.state & 0x0700:
                rel_x, rel_y = 0.5, 0.5
            else:
                # Calcula posição relativa do mouse no slot (0.0 a 1.0)
                fw = frm.winfo_width()
                fh = frm.winfo_height()
                rel_x = (event.x_root - frm.winfo_rootx()) / fw if fw > 0 else 0.5
                rel_y = (event.y_root - frm.winfo_rooty()) / fh if fh > 0 else 0.5

            ip = self.grid_cameras[idx]
            handler = self.camera_handlers.get(ip)
            if handler and handler != "CONECTANDO":
                with handler.lock:
                    handler.zoom_center = (rel_x, rel_y)
                    if direcao == "ZOOM_IN":
                        handler.zoom_digital = min(5.0, handler.zoom_digital + 0.1)
                    else:
                        handler.zoom_digital = max(1.0, handler.zoom_digital - 0.1)

    def comando_ptz(self, direcao):
        ip = self.ip_selecionado
        if not ip or ip == "0.0.0.0": return

        if direcao != "STOP":
            if self.tecla_pressionada == direcao: return
            self.tecla_pressionada = direcao
        else:
            self.tecla_pressionada = None

        mapa = {
            "UP": {"pan": 0, "tilt": 100, "zoom": 0},
            "DOWN": {"pan": 0, "tilt": -100, "zoom": 0},
            "LEFT": {"pan": -100, "tilt": 0, "zoom": 0},
            "RIGHT": {"pan": 100, "tilt": 0, "zoom": 0},
            "ZOOM_IN": {"pan": 0, "tilt": 0, "zoom": 100},
            "ZOOM_OUT": {"pan": 0, "tilt": 0, "zoom": -100},
            "STOP": {"pan": 0, "tilt": 0, "zoom": 0}
        }

        valores = mapa.get(direcao)
        xml_data = f"""<?xml version="1.0" encoding="UTF-8"?>
        <PTZData xmlns="http://www.isapi.org/ver20/XMLSchema">
            <pan>{valores['pan']}</pan>
            <tilt>{valores['tilt']}</tilt>
            <zoom>{valores['zoom']}</zoom>
        </PTZData>"""

        threading.Thread(target=self._enviar_request_ptz, args=(ip, xml_data), daemon=True).start()

    def _enviar_request_ptz(self, ip, xml):
        url = f"http://{ip}/ISAPI/PTZCtrl/channels/1/continuous"
        try:
            requests.put(
                url,
                data=xml,
                auth=HTTPDigestAuth(self.user_ptz, self.pass_ptz),
                timeout=1
            )
        except Exception as e:
            print(f"Erro PTZ {ip}: {e}")

    # --- TELA CHEIA ATUALIZADO ---
    def entrar_tela_cheia(self):
        if self.em_tela_cheia: return
        self.em_tela_cheia = True
        
        self.sidebar.grid_forget()
        self.container_toggle.grid_forget()
        self.sidebar_right.grid_forget()
        self.container_toggle_right.grid_forget()

        self.main_frame.grid_configure(column=0, columnspan=5)

        self.grid_frame.pack_forget()
        self.grid_frame.pack(expand=True, fill="both", padx=0, pady=0)
        
        indices_visiveis = [self.slot_maximized] if self.slot_maximized is not None else range(len(self.slot_frames))
        for i, frm in enumerate(self.slot_frames):
            if i in indices_visiveis:
                frm.grid_configure(padx=0, pady=0, sticky="nsew")
                frm.configure(corner_radius=0)
                for child in frm.winfo_children():
                    child.pack_configure(padx=0, pady=0)
            else:
                frm.grid_forget()

        self.btn_sair_fs = ctk.CTkButton(self.main_frame, text="✖ SAIR", width=100, height=40,
                                         fg_color=self.ACCENT_RED, hover_color=self.ACCENT_WINE, command=self.sair_tela_cheia)
        self.btn_sair_fs.place(relx=0.98, rely=0.02, anchor="ne")
        self.btn_sair_fs.lift()

    def sair_tela_cheia(self):
        if not self.em_tela_cheia: return
        self.em_tela_cheia = False
        if hasattr(self, 'btn_sair_fs'): self.btn_sair_fs.destroy()

        if self.sidebar_visible:
            self.sidebar.grid(row=0, column=0, sticky="nsew")
        
        self.container_toggle.grid(row=0, column=1, sticky="ns")
        self.main_frame.grid_configure(column=2, columnspan=1)

        self.container_toggle_right.grid(row=0, column=3, sticky="ns")
        if self.sidebar_right_visible:
            self.grid_columnconfigure(4, minsize=400)
            self.sidebar_right.grid(row=0, column=4, sticky="nsew")
        else:
            self.grid_columnconfigure(4, minsize=0)
        
        self.grid_frame.pack_forget()
        padx_grid = 0 if self.slot_maximized is not None else 0
        pady_grid = 0 if self.slot_maximized is not None else 0
        self.grid_frame.pack(side="top", expand=True, fill="both", padx=padx_grid, pady=pady_grid)

        indices_visiveis = [self.slot_maximized] if self.slot_maximized is not None else range(len(self.slot_frames))
        for i, frm in enumerate(self.slot_frames):
            if i in indices_visiveis:
                p = 0 if self.slot_maximized is not None else 1
                p_child = 0 if self.slot_maximized is not None else 2
                rad = 0 if self.slot_maximized is not None else 2
                frm.grid_configure(padx=p, pady=p, sticky="nsew")
                frm.configure(corner_radius=rad)
                for child in frm.winfo_children():
                    child.pack_configure(padx=p_child, pady=p_child)
            else:
                frm.grid_forget()

    def carregar_posicao_janela(self):
        if os.path.exists(self.arquivo_janela):
            try:
                with open(self.arquivo_janela, "r") as f:
                    dados = json.load(f)
                    geom = dados.get("geometry")
                    if geom: self.geometry(geom)
                    self.aba_ativa = dados.get("active_tab", "Câmeras")
                    # self.ultima_predefinicao é agora definida pela regra de "primeira predefinição" no __init__
                    self.slot_selecionado = dados.get("slot_selecionado", 0)
                    self.tamanho_preview = dados.get("tamanho_preview", "Pequeno")
                    if self.tamanho_preview == "Médio":
                        self.tamanho_preview = "Grande"
            except Exception as e: print(f"Erro ao carregar janela: {e}")

    def restaurar_layout_total(self):
        """Re-aplica o layout principal para garantir que o Tkinter redesenhe tudo no Windows."""
        # print("LOG: Forçando reconstrução do layout principal...")
        try:
            # Re-grid Sidebar
            if getattr(self, 'sidebar_visible', True):
                self.sidebar.grid(row=0, column=0, sticky="nsew")

            # Re-grid Container Toggle
            self.container_toggle.grid(row=0, column=1, sticky="ns")

            # Re-grid Main Frame
            if self.em_tela_cheia:
                self.main_frame.grid_configure(row=0, column=0, columnspan=5, sticky="nsew")
            else:
                self.main_frame.grid_configure(row=0, column=2, sticky="nsew")

            # Re-grid Right Sidebar
            self.container_toggle_right.grid(row=0, column=3, sticky="ns")
            if getattr(self, 'sidebar_right_visible', True):
                self.grid_columnconfigure(4, minsize=400)
                self.sidebar_right.grid(row=0, column=4, sticky="nsew")
            else:
                self.grid_columnconfigure(4, minsize=0)

            self.update_idletasks()
        except Exception as e:
            print(f"Erro ao restaurar layout total: {e}")

    def recuperar_interface_pos_minimizacao(self):
        """Re-aplica o layout e recria labels para recuperar do erro de tela preta."""
        print("SISTEMA: Executando recuperação total da interface...")
        try:
            self.iconic_state = False
            self.restaurar_layout_total()

            # Garante que as dimensões do grid estejam corretas antes de recriar labels
            if self.slot_maximized is not None:
                self.maximizar_slot(self.slot_maximized)
            else:
                self.restaurar_grid()

            for i in range(self.num_slots):
                # Limpamos TUDO antes de recriar para garantir estado virgem
                self.slot_ctk_images[i] = None
                self.cache_ui_text[i] = None
                self.cache_ui_image[i] = None
                self.cache_ui_size[i] = None
                self.recriar_label_slot(i)

            self.selecionar_slot(self.slot_selecionado)
            self.update_idletasks()
            print("SISTEMA: Recuperação concluída.")
        except Exception as e:
            print(f"Erro na recuperação de interface: {e}")

    def ao_fechar(self):
        # Para todas as gravações ativas
        for h in self.camera_handlers.values():
            if h != "CONECTANDO":
                h.parar_gravacao()

        if self.video_writer_tudo:
            self.video_writer_tudo.release()
            self.video_writer_tudo = None

        # Para monitoramento biométrico
        if hasattr(self, 'bio_thread'):
            self.bio_thread.parar()

        try:
            if not self.em_tela_cheia:
                dados = {
                    "geometry": self.geometry(),
                    "active_tab": self.tabview.get(),
                    "last_predefinicao": self.ultima_predefinicao,
                    "slot_selecionado": self.slot_selecionado,
                    "tamanho_preview": self.tamanho_preview,
                    "num_slots": self.num_slots
                }
                with open(self.arquivo_janela, "w") as f: json.dump(dados, f)
        except Exception as e: print(f"Erro ao salvar janela: {e}")
        self.destroy()
        os._exit(0)

    def obter_canal_alvo(self, ip):
        """Define se deve usar canal 101 (Main) ou 102 (Sub) baseado no estado do sistema."""
        # Se estiver maximizada, o IP maximizado usa 101
        if self.slot_maximized is not None:
            ip_max = self.grid_cameras[self.slot_maximized]
            if ip == ip_max:
                return 101

        return 102

    def maximizar_slot(self, index):
        self.grid_frame.pack_configure(padx=0, pady=0)

        # IP da câmera que será maximizada
        ip_maximized = self.grid_cameras[index] if index < len(self.grid_cameras) else None

        for i, frm in enumerate(self.slot_frames):
            if i == index:
                frm.grid_configure(row=0, column=0, rowspan=self.grid_rows, columnspan=self.grid_cols, padx=0, pady=0, sticky="nsew")
                frm.configure(corner_radius=0)
                for child in frm.winfo_children(): child.pack_configure(padx=0, pady=0)
            else:
                frm.grid_forget()

        self.slot_maximized = index

        # Gerenciamento de Prioridade e Qualidade
        for ip, handler in self.camera_handlers.items():
            if handler == "CONECTANDO": continue
            if ip == ip_maximized:
                handler.set_prioridade(True)
                handler.set_canal(self.obter_canal_alvo(ip))
            else:
                handler.set_prioridade(False)
                handler.set_canal(self.obter_canal_alvo(ip))
        self.atualizar_botoes_controle()

    def ao_pressionar_slot(self, event, index):
        self.selecionar_slot(index)
        self.press_data = {
            "index": index,
            "x": event.x_root,
            "y": event.y_root,
            "x_start": event.x_root,
            "y_start": event.y_root
        }

    def ao_soltar_slot(self, event, index):
        if not self.press_data: return
        self.fechar_fantasma_drag()

        source_idx = self.press_data.get("index")
        if self.slot_maximized is not None or self.em_tela_cheia:
            self.press_data = None
            return
        try:
            # Usa coordenadas iniciais para o cálculo de distância real do arrasto
            x_start = self.press_data.get("x_start", self.press_data["x"])
            y_start = self.press_data.get("y_start", self.press_data["y"])
            dist = ((event.x_root - x_start)**2 + (event.y_root - y_start)**2)**0.5

            target_idx = self.encontrar_slot_por_coords(event.x_root, event.y_root)

            # Se for apenas um clique (distância pequena) ou soltou fora
            if dist < 15 or target_idx is None:
                return

            # Se arrastou para o mesmo slot
            if target_idx == source_idx:
                return

            # Lógica de Troca (Swap)
            if 0 <= source_idx < self.num_slots and 0 <= target_idx < self.num_slots:
                ip_src = self.grid_cameras[source_idx]
                ip_tgt = self.grid_cameras[target_idx]

                # Se ambos os slots estiverem vazios, não faz nada
                if (not ip_src or ip_src == "0.0.0.0") and (not ip_tgt or ip_tgt == "0.0.0.0"):
                    return

                # Agora atualiza visualmente e gerencia handlers
                # Note: 'atribuir_ip_ao_slot' agora gerencia 'ultima_predefinicao' internamente
                self.atribuir_ip_ao_slot(source_idx, ip_tgt, atualizar_ui=False)
                self.atribuir_ip_ao_slot(target_idx, ip_src, atualizar_ui=False)

                self.selecionar_slot(target_idx)
                self.update_idletasks()

        finally:
            self.press_data = None

    def encontrar_slot_por_coords(self, x_root, y_root):
        for i, frm in enumerate(self.slot_frames):
            if not frm.winfo_viewable(): continue
            fx, fy = frm.winfo_rootx(), frm.winfo_rooty()
            fw, fh = frm.winfo_width(), frm.winfo_height()
            if fx <= x_root <= fx + fw and fy <= y_root <= fy + fh: return i
        return None

    def restaurar_grid(self):
        self.grid_frame.pack_configure(padx=0, pady=0)

        # IP que estava focado
        ip_foco = self.grid_cameras[self.slot_maximized] if self.slot_maximized is not None else None

        for i, frm in enumerate(self.slot_frames):
            row, col = i // self.grid_cols, i % self.grid_cols
            frm.grid_configure(row=row, column=col, rowspan=1, columnspan=1, padx=1, pady=1, sticky="nsew")
            frm.configure(corner_radius=2)
            frm.grid()
            for child in frm.winfo_children(): child.pack_configure(padx=2, pady=2)

        # Gerenciamento de Prioridade e Qualidade (Volta tudo ao normal)
        for ip, handler in self.camera_handlers.items():
            if handler == "CONECTANDO": continue
            handler.set_prioridade(False)
            handler.set_canal(self.obter_canal_alvo(ip))

        self.slot_maximized = None
        self.atualizar_botoes_controle()

    def atualizar_viewport_grid(self, salvar=True):
        """Atualiza os slots do grid com base na posição do viewport (offset_x, offset_y)."""
        ips_antes = set(ip for ip in self.grid_cameras if ip and ip != "0.0.0.0")

        # 1. Atualiza IPs no grid_cameras
        novos_ips = []
        for i in range(self.num_slots):
            r, c = i // self.grid_cols, i % self.grid_cols
            ip = self.virtual_grid.get((r + self.offset_y, c + self.offset_x), "0.0.0.0")
            novos_ips.append(ip)
            # Atualiza visual e dados sem disparar conexões ainda
            self.atribuir_ip_ao_slot(i, ip, atualizar_ui=False, gerenciar_conexoes=False, salvar=False, forcado=True)

        # 2. Gerencia conexões baseadas na mudança de visibilidade
        ips_depois = set(ip for ip in novos_ips if ip and ip != "0.0.0.0")

        # Para câmeras que foram removidas do grid (não apenas saíram do viewport)
        for ip in ips_antes:
            if ip not in self.virtual_grid.values():
                handler = self.camera_handlers.get(ip)
                if handler and handler != "CONECTANDO":
                    try: handler.parar()
                    except: pass
                if ip in self.camera_handlers:
                    del self.camera_handlers[ip]

        # Inicia câmeras que entraram no viewport
        for ip in ips_depois:
            if ip not in self.camera_handlers:
                canal = self.obter_canal_alvo(ip)
                self.iniciar_conexao_assincrona(ip, canal)

        if salvar:
            self.salvar_grid()

        self.atualizar_botoes_controle()
        self.update_idletasks()

    def ao_tecla_direcional(self, direcao):
        """Gerencia navegação por teclado: PTZ se maximizado, Grid caso contrário."""
        # Se estiver em um campo de entrada, não navega
        foco = self.focus_get()
        if isinstance(foco, (ctk.CTkEntry, ctk.CTkTextbox)):
            return

        if self.slot_maximized is not None:
            self.comando_ptz(direcao)
        else:
            self.navegar_grid(direcao)

    def ao_tecla_solta(self, direcao):
        """Para o PTZ apenas se estivermos no modo PTZ."""
        if self.slot_maximized is not None:
            self.comando_ptz("STOP")

    def navegar_grid(self, direcao):
        """Move o viewport do grid na direção especificada, se válido."""
        # Se algum slot estiver maximizado, não permite navegar (opcional, mas recomendado)
        if self.slot_maximized is not None: return

        novo_ox = self.offset_x
        novo_oy = self.offset_y

        if direcao == "RIGHT": novo_ox += 1
        elif direcao == "LEFT": novo_ox -= 1
        elif direcao == "UP": novo_oy -= 1
        elif direcao == "DOWN": novo_oy += 1
        else: return

        # Regra de Movimentação ABI:
        # Só é possível movimentar se o novo viewport tiver pelo menos uma câmera
        # OU se for a PRIMEIRA vez entrando no vazio naquela direção.

        # Verifica se o novo viewport tem câmeras
        tem_camera = False
        for r in range(self.grid_rows):
            for c in range(self.grid_cols):
                ip = self.virtual_grid.get((r + novo_oy, c + novo_ox), "0.0.0.0")
                if ip and ip != "0.0.0.0":
                    tem_camera = True
                    break
            if tem_camera: break

        # "Só sera possivel movimentar mais de uma vez se tiver pelo menos uma camera no espaço revelado."
        # Se o viewport ATUAL já está vazio na direção que o usuário está tentando ir, e o NOVO também será vazio, bloqueia.
        # Mas vamos simplificar: se o novo viewport estiver vazio, só permitimos se o ATUAL tivesse pelo menos uma câmera.

        foi_vazio = True
        for r in range(self.grid_rows):
            for c in range(self.grid_cols):
                ip_atual = self.virtual_grid.get((r + self.offset_y, c + self.offset_x), "0.0.0.0")
                if ip_atual and ip_atual != "0.0.0.0":
                    foi_vazio = False
                    break
            if not foi_vazio: break

        if not tem_camera and foi_vazio:
            # Já estávamos no vazio e tentando ir mais fundo no vazio
            return

        # Aplica movimento
        self.offset_x = novo_ox
        self.offset_y = novo_oy
        self.atualizar_viewport_grid()

    def selecionar_slot(self, index):
        if not (0 <= index < self.num_slots): return

        # Desliga info de todos os handlers antes de trocar
        for ip_h, h in self.camera_handlers.items():
            if h != "CONECTANDO": h.set_exibir_info(False)

        for frm in self.slot_frames: frm.configure(border_color="black", border_width=2)

        ip_anterior = self.ip_selecionado
        self.slot_selecionado = index
        self.slot_frames[index].configure(border_color=self.ACCENT_RED, border_width=2)

        ip_novo = self.grid_cameras[index]
        if ip_novo and ip_novo != "0.0.0.0":
            self.title(f"Monitoramento ABI - {ip_novo} selecionado")
            if ip_anterior and ip_anterior != ip_novo: self.pintar_botao(ip_anterior, self.BG_SIDEBAR)
            self.ip_selecionado = ip_novo
            nome = self.dados_cameras.get(ip_novo, "")
            self.pintar_botao(ip_novo, self.ACCENT_WINE)

            # Ativa overlay no handler
            handler = self.camera_handlers.get(ip_novo)
            if handler and handler != "CONECTANDO":
                handler.set_exibir_info(True)

            # Sincroniza o seletor de IP
            self.sincronizar_seletor_com_ip(ip_novo)
        else:
            self.title("Monitoramento ABI")
            if ip_anterior: self.pintar_botao(ip_anterior, self.BG_SIDEBAR)
            self.ip_selecionado = None
        self.atualizar_botoes_controle()

    def limpar_slot_atual(self):
        self.press_data = None
        idx = self.slot_selecionado
        self.atribuir_ip_ao_slot(idx, "0.0.0.0")

        # Limpa predefinição ao remover manualmente
        if self.ultima_predefinicao:
            self.pintar_predefinicao(self.ultima_predefinicao, self.BG_SIDEBAR)
            self.ultima_predefinicao = None

        if self.ip_selecionado:
            self.pintar_botao(self.ip_selecionado, self.BG_SIDEBAR)
            self.ip_selecionado = None
        
        if self.slot_maximized == idx: self.restaurar_grid()
        self.selecionar_slot(idx)

    def salvar_grid(self):
        try:
            # Converte chaves de tupla para strings "r,c" para JSON
            vg_serializable = {f"{r},{c}": ip for (r, c), ip in self.virtual_grid.items()}

            dados = {
                "offset_x": self.offset_x,
                "offset_y": self.offset_y,
                "virtual_grid": vg_serializable,
                "grid_cameras": self.grid_cameras # Mantém por compatibilidade ou redundância
            }

            with open(self.arquivo_grid, "w", encoding='utf-8') as f:
                json.dump(dados, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Erro ao salvar grid: {e}")

    def carregar_grid(self):
        num_slots = getattr(self, 'num_slots', 20)
        grid = ["0.0.0.0"] * num_slots

        if os.path.exists(self.arquivo_grid):
            try:
                with open(self.arquivo_grid, "r", encoding='utf-8') as f:
                    dados = json.load(f)

                    if isinstance(dados, dict):
                        self.offset_x = dados.get("offset_x", 0)
                        self.offset_y = dados.get("offset_y", 0)

                        vg_data = dados.get("virtual_grid", {})
                        self.virtual_grid = {}
                        for k, v in vg_data.items():
                            try:
                                r, c = map(int, k.split(','))
                                self.virtual_grid[(r, c)] = v
                            except: pass

                        # Reconstrói grid_cameras (o viewport atual)
                        for i in range(num_slots):
                            r, c = i // self.grid_cols, i % self.grid_cols
                            grid[i] = self.virtual_grid.get((r + self.offset_y, c + self.offset_x), "0.0.0.0")

                    elif isinstance(dados, list):
                        # Legado
                        for i in range(min(len(dados), num_slots)):
                            if dados[i]: grid[i] = dados[i]
                            r, c = i // self.grid_cols, i % self.grid_cols
                            self.virtual_grid[(r, c)] = grid[i]
            except Exception as e:
                print(f"Erro ao carregar grid: {e}")
        return grid

    def atualizar_botoes_controle(self):
        # Decide qual slot deve conter os botões
        idx = self.slot_maximized if self.slot_maximized is not None else self.slot_selecionado

        # Se não houver IP no slot ou slot inválido, esconde botões
        ip_atual = self.grid_cameras[idx] if (idx is not None and 0 <= idx < self.num_slots) else "0.0.0.0"

        handler = self.camera_handlers.get(ip_atual)
        is_rec = handler and handler != "CONECTANDO" and getattr(handler, 'gravando', False)
        is_max = self.slot_maximized is not None

        # Cache de estado para evitar flickering por chamadas redundantes
        current_state = (idx, ip_atual, is_rec, is_max)
        if current_state == getattr(self, 'last_button_state', None):
            return
        self.last_button_state = current_state

        # Controle de visibilidade dos botões de navegação
        if is_max:
            self.btn_nav_up.place_forget()
            self.btn_nav_down.place_forget()
            self.btn_nav_left.place_forget()
            self.btn_nav_right.place_forget()
        else:
            # Posicionamento circular estratégico
            self.btn_nav_up.place(relx=0.5, rely=0.06, anchor="center")
            self.btn_nav_down.place(relx=0.5, rely=0.94, anchor="center")
            self.btn_nav_left.place(relx=0.03, rely=0.5, anchor="center")
            self.btn_nav_right.place(relx=0.97, rely=0.5, anchor="center")
            self.btn_nav_up.lift()
            self.btn_nav_down.lift()
            self.btn_nav_left.lift()
            self.btn_nav_right.lift()

        if not ip_atual or ip_atual == "0.0.0.0":
            self.btn_expandir.place_forget()
            self.btn_gravar.place_forget()
            self.btn_mais_opcoes.place_forget()
            return

        target_frm = self.slot_frames[idx]

        # Configurações de Texto e Estilo
        txt_exp = "Diminuir" if is_max else "Aumentar"
        txt_rec = "Parar" if is_rec else "Gravar"
        txt_opt = "Mais Opções"

        # Se maximizada, dobra o tamanho (200x60, font 24)
        if is_max:
            f_main = ("Roboto", 24, "bold")
            w_btn = 200
            h_btn = 60
            spc = 10
            x_offset = -20
            y_start = -20
        else:
            f_main = ("Roboto", 12, "bold")
            w_btn = 100
            h_btn = 30
            spc = 5
            x_offset = -10
            y_start = -10

        # Aplica cores baseadas no estado
        color_exp = self.ACCENT_RED if is_max else self.GRAY_DARK
        color_rec = self.ACCENT_RED if is_rec else self.GRAY_DARK
        color_opt = self.GRAY_DARK # "Mais Opções" não tem estado ativo binário simples aqui

        self.btn_expandir.configure(text=txt_exp, fg_color=color_exp, width=w_btn, height=h_btn, font=f_main)
        self.btn_gravar.configure(text=txt_rec, fg_color=color_rec, width=w_btn, height=h_btn, font=f_main)
        self.btn_mais_opcoes.configure(text=txt_opt, fg_color=color_opt, width=w_btn, height=h_btn, font=f_main)

        # Posicionamento Vertical (Stack) a partir da lateral direita inferior
        # Ordem de baixo para cima: Mais Opções, Gravar, Aumentar
        y_opt = y_start
        y_rec = y_opt - h_btn - spc
        y_exp = y_rec - h_btn - spc

        self.btn_mais_opcoes.place(in_=target_frm, relx=1.0, rely=1.0, x=x_offset, y=y_opt, anchor="se")
        self.btn_gravar.place(in_=target_frm, relx=1.0, rely=1.0, x=x_offset, y=y_rec, anchor="se")
        self.btn_expandir.place(in_=target_frm, relx=1.0, rely=1.0, x=x_offset, y=y_exp, anchor="se")

        # Garante que fiquem no topo
        self.btn_expandir.lift()
        self.btn_gravar.lift()
        self.btn_mais_opcoes.lift()

    def toggle_grid_layout(self):
        if self.slot_maximized is not None: self.restaurar_grid()
        else: self.maximizar_slot(self.slot_selecionado)

    def toggle_gravacao(self):
        if not self.ip_selecionado:
            self.abrir_modal_alerta("Aviso", "Nenhuma câmera selecionada.")
            return

        handler = self.camera_handlers.get(self.ip_selecionado)
        if not handler or handler == "CONECTANDO" or not handler.conectado:
            self.abrir_modal_alerta("Erro", "A câmera selecionada não está conectada.")
            return

        if not handler.gravando:
            # Iniciar Gravação
            try:
                downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
                os.makedirs(downloads_dir, exist_ok=True)

                ip_limpo = self.ip_selecionado.replace(".", "_")
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                filename = f"gravacao_{ip_limpo}_{timestamp}.mp4"
                filepath = os.path.join(downloads_dir, filename)

                handler.iniciar_gravacao(filepath)
                self.atualizar_botoes_controle()
            except Exception as e:
                self.abrir_modal_alerta("Erro", f"Não foi possível iniciar a gravação: {e}")
        else:
            # Parar Gravação
            handler.parar_gravacao()
            self.atualizar_botoes_controle()
            self.abrir_modal_alerta("Sucesso", "Gravação finalizada e salva em Downloads.", show_open_folder=True)

    def toggle_gravacao_tudo(self):
        if not self.gravando_tudo:
            # Iniciar Gravação Total
            try:
                downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
                os.makedirs(downloads_dir, exist_ok=True)

                timestamp = time.strftime("%Y%m%d_%H%M%S")
                filename = f"monitoramento_total_{timestamp}.mp4"
                self.caminho_video_tudo = os.path.join(downloads_dir, filename)

                self.gravando_tudo = True
                self.btn_gravar_tudo.configure(text="Parar Gravação Total", fg_color=self.ACCENT_RED)
                print(f"Gravação TOTAL iniciada: {self.caminho_video_tudo}")
            except Exception as e:
                self.abrir_modal_alerta("Erro", f"Não foi possível iniciar a gravação total: {e}")
        else:
            # Parar Gravação Total
            self.gravando_tudo = False
            if self.video_writer_tudo:
                self.video_writer_tudo.release()
                self.video_writer_tudo = None
            self.btn_gravar_tudo.configure(text="Gravar Tudo", fg_color=self.GRAY_DARK)
            self.abrir_modal_alerta("Sucesso", "Gravação total finalizada e salva em Downloads.", show_open_folder=True)

    def abrir_janela_configuracoes(self):
        modal = ctk.CTkToplevel(self)
        modal.title("Configurações")
        modal.geometry("400x420")
        modal.resizable(False, False)
        modal.attributes("-topmost", True)

        try:
            self.update_idletasks()
            x = self.winfo_x() + (self.winfo_width() // 2) - 200
            y = self.winfo_y() + (self.winfo_height() // 2) - 150
            modal.geometry(f"+{x}+{y}")
        except: pass

        ctk.CTkLabel(modal, text="CONFIGURAÇÕES", font=("Roboto", 18, "bold"), text_color=self.TEXT_P).pack(pady=(20, 5))

        # Info de quantidade de câmeras
        total_cams = len(self.ips_unicos)
        ctk.CTkLabel(modal, text=f"Total de câmeras na lista: {total_cams}", font=("Roboto", 12), text_color=self.TEXT_S).pack(pady=(0, 15))

        # Segmented Button para Tamanho da Preview
        ctk.CTkLabel(modal, text="Tamanho da previsualização:", font=("Roboto", 14), text_color=self.TEXT_S).pack(pady=(20, 5))

        def mudar_tamanho(novo_tamanho):
            self.tamanho_preview = novo_tamanho
            self.atualizar_lista_cameras_ui()

        seg_button = ctk.CTkSegmentedButton(modal, values=["Pequeno", "Grande"],
                                            command=mudar_tamanho,
                                            selected_color=self.ACCENT_RED,
                                            unselected_hover_color=self.ACCENT_WINE)
        seg_button.set(self.tamanho_preview)
        seg_button.pack(pady=10, padx=20, fill="x")

        # Segmented Button para Quantidade de Câmeras (Grid)
        ctk.CTkLabel(modal, text="Quantidade de câmeras (Grid):", font=("Roboto", 14), text_color=self.TEXT_S).pack(pady=(20, 5))

        def on_change_grid(nova_qtd_str):
            nova_qtd = int(nova_qtd_str)
            if nova_qtd != self.num_slots:
                self.mudar_quantidade_slots(nova_qtd)

        seg_grid = ctk.CTkSegmentedButton(modal, values=["20", "40"],
                                           command=on_change_grid,
                                           selected_color=self.ACCENT_RED,
                                           unselected_hover_color=self.ACCENT_WINE)
        seg_grid.set(str(self.num_slots))
        seg_grid.pack(pady=10, padx=20, fill="x")

    def mudar_quantidade_slots(self, nova_qtd):
        """Altera a quantidade de slots do grid e reconstrói a interface."""
        # Interrompe gravação global antes de mudar dimensões do mosaico
        if self.gravando_tudo:
            self.toggle_gravacao_tudo()

        print(f"SISTEMA: Mudando para {nova_qtd} câmeras...")

        # 1. Salva nova preferência
        self.num_slots = nova_qtd

        # 2. Para handlers e limpa caches
        for h in self.camera_handlers.values():
            if h != "CONECTANDO":
                try: h.parar()
                except: pass
        self.camera_handlers = {}
        self.ips_em_fila = set()
        while not self.fila_pendente_conexoes.empty():
            try: self.fila_pendente_conexoes.get_nowait()
            except: pass

        self.slot_ctk_images = [None] * self.num_slots
        self.cache_ui_text = [None] * self.num_slots
        self.cache_ui_image = [None] * self.num_slots
        self.cache_ui_size = [None] * self.num_slots
        self.slot_selecionado = 0

        # 3. Destrói grid atual e reconstrói
        if hasattr(self, 'grid_frame'):
            self.grid_frame.destroy()

        self.configurar_variaveis_grid(nova_qtd)
        self.criar_interface_grid()

        # 4. Sincroniza e preenche
        self.grid_cameras = ["0.0.0.0"] * self.num_slots
        self.atualizar_viewport_grid(salvar=True)
        self.selecionar_slot(0)

    def abrir_menu_opcoes(self):
        if not self.ip_selecionado: return

        nome = self.dados_cameras.get(self.ip_selecionado, "Câmera Sem Nome")
        ip = self.ip_selecionado

        # Cria a janela modal
        modal = ctk.CTkToplevel(self)
        modal.title(f"Opções - {ip}")
        modal.geometry("400x330")
        modal.resizable(False, False)
        modal.attributes("-topmost", True)

        # Tenta centralizar a janela em relação à aplicação
        try:
            self.update_idletasks()
            x = self.winfo_x() + (self.winfo_width() // 2) - 200
            y = self.winfo_y() + (self.winfo_height() // 2) - 165
            modal.geometry(f"+{x}+{y}")
        except: pass

        # Conteúdo
        ctk.CTkLabel(modal, text=nome if nome else "Sem Nome", font=("Roboto", 18, "bold"), text_color=self.TEXT_P).pack(pady=(20, 5))
        ctk.CTkLabel(modal, text=ip, font=("Roboto", 14), text_color=self.TEXT_S).pack(pady=(0, 20))

        # Botões com canto quadrado (corner_radius=0)
        btn_renomear = ctk.CTkButton(modal, text="Renomear", fg_color=self.GRAY_DARK, hover_color=self.TEXT_S,
                                    corner_radius=0, height=40,
                                    command=lambda: [modal.destroy(), self.alternar_edicao_nome()])
        btn_renomear.pack(fill="x", padx=40, pady=5)

        btn_desabilitar = ctk.CTkButton(modal, text="Desabilitar", fg_color=self.GRAY_DARK, hover_color=self.TEXT_S,
                                    corner_radius=0, height=40,
                                    command=lambda: [self.limpar_slot_atual(), modal.destroy()])
        btn_desabilitar.pack(fill="x", padx=40, pady=5)

        btn_excluir = ctk.CTkButton(modal, text="Excluir", fg_color=self.ACCENT_RED, hover_color=self.ACCENT_WINE,
                                     corner_radius=0, height=40,
                                     command=lambda: [modal.destroy(), self.solicitar_senha(lambda: self.confirmar_exclusao_camera_da_lista(ip))])
        btn_excluir.pack(fill="x", padx=40, pady=5)

    def solicitar_senha(self, callback):
        def verificar(valor):
            if valor == "passwordadm":
                callback()
            else:
                self.abrir_modal_alerta("Erro", "Senha incorreta.")

        self.abrir_modal_input(titulo="Autenticação", mensagem="Digite a senha de administrador:",
                               callback=verificar, show="*")

    def abrir_modal_input(self, titulo, mensagem, callback, valor_inicial="", show=""):
        modal = ctk.CTkToplevel(self)
        modal.title(titulo)
        modal.geometry("400x250")
        modal.resizable(False, False)
        modal.attributes("-topmost", True)

        try:
            self.update_idletasks()
            x = self.winfo_x() + (self.winfo_width() // 2) - 200
            y = self.winfo_y() + (self.winfo_height() // 2) - 125
            modal.geometry(f"+{x}+{y}")
        except: pass

        ctk.CTkLabel(modal, text=mensagem, font=("Roboto", 14, "bold"), text_color=self.TEXT_P).pack(pady=(20, 10))

        entry = ctk.CTkEntry(modal, width=300, show=show)
        entry.insert(0, valor_inicial)
        entry.pack(pady=10)
        entry.focus_set()

        def confirmar():
            valor = entry.get()
            modal.destroy()
            callback(valor)

        btn_confirmar = ctk.CTkButton(modal, text="Confirmar", fg_color=self.ACCENT_RED, hover_color=self.ACCENT_WINE,
                                      corner_radius=0, height=40, command=confirmar)
        btn_confirmar.pack(fill="x", padx=40, pady=5)

        btn_cancelar = ctk.CTkButton(modal, text="Cancelar", fg_color=self.GRAY_DARK, hover_color=self.TEXT_S,
                                     corner_radius=0, height=40, command=modal.destroy)
        btn_cancelar.pack(fill="x", padx=40, pady=5)

        modal.bind("<Return>", lambda e: confirmar())

    def abrir_modal_confirmacao(self, titulo, mensagem, callback_sim):
        modal = ctk.CTkToplevel(self)
        modal.title(titulo)
        modal.geometry("400x200")
        modal.resizable(False, False)
        modal.attributes("-topmost", True)

        try:
            self.update_idletasks()
            x = self.winfo_x() + (self.winfo_width() // 2) - 200
            y = self.winfo_y() + (self.winfo_height() // 2) - 100
            modal.geometry(f"+{x}+{y}")
        except: pass

        ctk.CTkLabel(modal, text=mensagem, font=("Roboto", 14, "bold"), text_color=self.TEXT_P, wraplength=320).pack(pady=(30, 20))

        frame_btns = ctk.CTkFrame(modal, fg_color="transparent")
        frame_btns.pack(fill="x", padx=40)

        btn_sim = ctk.CTkButton(frame_btns, text="Sim", fg_color=self.ACCENT_RED, hover_color=self.ACCENT_WINE,
                                corner_radius=0, height=40, width=140, command=lambda: [modal.destroy(), callback_sim()])
        btn_sim.pack(side="left", expand=True, padx=5)

        btn_nao = ctk.CTkButton(frame_btns, text="Não", fg_color=self.GRAY_DARK, hover_color=self.TEXT_S,
                                corner_radius=0, height=40, width=140, command=modal.destroy)
        btn_nao.pack(side="right", expand=True, padx=5)

    def abrir_modal_alerta(self, titulo, mensagem, show_open_folder=False):
        modal = ctk.CTkToplevel(self)
        modal.title(titulo)

        altura_modal = 230 if show_open_folder else 180
        modal.geometry(f"400x{altura_modal}")
        modal.resizable(False, False)
        modal.attributes("-topmost", True)

        try:
            self.update_idletasks()
            x = self.winfo_x() + (self.winfo_width() // 2) - 200
            y = self.winfo_y() + (self.winfo_height() // 2) - (altura_modal // 2)
            modal.geometry(f"+{x}+{y}")
        except: pass

        ctk.CTkLabel(modal, text=mensagem, font=("Roboto", 14, "bold"), text_color=self.TEXT_P, wraplength=320).pack(pady=(30, 20))

        if show_open_folder:
            btn_folder = ctk.CTkButton(modal, text="Abrir Local do Arquivo", fg_color=self.ACCENT_RED, hover_color=self.ACCENT_WINE,
                                       corner_radius=0, height=40, command=lambda: [self.abrir_pasta_downloads(), modal.destroy()])
            btn_folder.pack(fill="x", padx=60, pady=(0, 10))

        btn_ok = ctk.CTkButton(modal, text="OK", fg_color=self.GRAY_DARK, hover_color=self.TEXT_S,
                               corner_radius=0, height=40, command=modal.destroy)
        btn_ok.pack(fill="x", padx=60, pady=10)

    def recriar_label_slot(self, idx):
        """Recria o CTkLabel de um slot para limpar estados corrompidos do Tcl/Tkinter."""
        # print(f"LOG: Recriando Label do slot {idx}")
        try:
            # Pega o frame pai
            frm = self.slot_frames[idx]

            # Destrói o label antigo
            if self.slot_labels[idx]:
                try: self.slot_labels[idx].destroy()
                except: pass

            # Cria o novo label
            ip = self.grid_cameras[idx]
            bg_color = "#000000" if not ip or ip == "0.0.0.0" else self.BG_SIDEBAR
            lbl = ctk.CTkLabel(frm, text="", corner_radius=0, fg_color=bg_color)
            lbl.pack(expand=True, fill="both", padx=2, pady=2)
            frm.configure(fg_color=bg_color)

            # Re-bind dos eventos
            lbl.bind("<Button-1>", lambda e, x=idx: self.ao_pressionar_slot(e, x))
            lbl.bind("<ButtonRelease-1>", lambda e, x=idx: self.ao_soltar_slot(e, x))
            lbl.bind("<B1-Motion>", lambda e, idx=idx: self.ao_arrastar_slot(e, idx))
            lbl.bind("<MouseWheel>", self.ao_scroll_mouse)
            lbl.bind("<Button-4>", self.ao_scroll_mouse)
            lbl.bind("<Button-5>", self.ao_scroll_mouse)

            self.slot_labels[idx] = lbl
            # Mantemos o objeto self.slot_ctk_images[idx] para evitar "pyimage" explosion
            # mas limpamos caches de texto e imagem para forçar o redesenho imediato
            self.cache_ui_text[idx] = None
            self.cache_ui_image[idx] = None
            return lbl
        except Exception as e:
            print(f"ERRO AO RECRIAR LABEL {idx}: {e}")
            return None

    def atribuir_ip_ao_slot(self, idx, ip, atualizar_ui=True, gerenciar_conexoes=True, salvar=True, forcado=False):
        if not (0 <= idx < self.num_slots): return

        # Limpa predefinição ao atribuir manualmente (se for uma atribuição direta, não via aplicar_predefinicao)
        # Note: 'aplicar_predefinicao' chama atribuir_ip_ao_slot com gerenciar_conexoes=False
        if gerenciar_conexoes and self.ultima_predefinicao:
            self.pintar_predefinicao(self.ultima_predefinicao, self.BG_SIDEBAR)
            self.ultima_predefinicao = None

        # Otimização: se o IP for o mesmo, não faz nada (a menos que seja 0.0.0.0 ou forçado)
        if not forcado and ip != "0.0.0.0" and self.grid_cameras[idx] == ip:
            return

        ip_antigo = self.grid_cameras[idx]
        self.grid_cameras[idx] = ip
        
        # Sincroniza com o Grid Virtual
        r, c = idx // self.grid_cols, idx % self.grid_cols
        self.virtual_grid[(r + self.offset_y, c + self.offset_x)] = ip

        # 1. Limpeza visual ultra-robusta
        # Só mostra IP se for o slot selecionado
        if not ip or ip == "0.0.0.0":
            txt = ""
            bg_color = "#000000"
        else:
            txt = f"CONECTANDO...\n{ip}" if idx == self.slot_selecionado else "CONECTANDO..."
            bg_color = self.BG_SIDEBAR

        try:
            # Tenta configurar o label existente
            self.slot_frames[idx].configure(fg_color=bg_color)
            self.slot_labels[idx].configure(image=self.img_vazia, text=txt, fg_color=bg_color)
            self.slot_labels[idx].image = self.img_vazia
            self.cache_ui_text[idx] = txt
            self.cache_ui_image[idx] = self.img_vazia
            # Limpa cache do slot para evitar fantasmas ou falhas de sincronia
            self.slot_ctk_images[idx] = None
        except Exception as e:
            print(f"Erro visual ao atualizar texto slot {idx}: {e}")
            lbl = self.recriar_label_slot(idx)
            if lbl:
                try: lbl.configure(text=txt)
                except: pass

        if atualizar_ui:
            self.update_idletasks()

        if salvar:
            self.salvar_grid()

        # 2. Gerenciamento de conexões (se solicitado)
        if gerenciar_conexoes:
            if ip_antigo and ip_antigo != "0.0.0.0" and ip_antigo != ip and ip_antigo not in self.virtual_grid.values():
                if ip_antigo in self.camera_handlers:
                    try: self.camera_handlers[ip_antigo].parar()
                    except: pass
                    del self.camera_handlers[ip_antigo]

            if ip != "0.0.0.0":
                if ip in self.cooldown_conexoes: del self.cooldown_conexoes[ip]
                canal_alvo = self.obter_canal_alvo(ip)
                self.iniciar_conexao_assincrona(ip, canal_alvo)

    def ao_pressionar_sidebar(self, event, ip):
        self.press_data = {
            "ip": ip,
            "x_start": event.x_root,
            "y_start": event.y_root,
            "x": event.x_root,
            "y": event.y_root
        }

    def ao_arrastar_sidebar(self, event, ip):
        if not self.press_data: return
        nome = self.dados_cameras.get(ip, ip)
        self.exibir_fantasma_drag(event.x_root, event.y_root, nome)

    def ao_soltar_sidebar(self, event, ip):
        if not self.press_data: return
        self.fechar_fantasma_drag()

        try:
            x_start = self.press_data.get("x_start", event.x_root)
            y_start = self.press_data.get("y_start", event.y_root)
            dist = ((event.x_root - x_start)**2 + (event.y_root - y_start)**2)**0.5

            # Se for apenas um clique (distância pequena), seleciona a câmera normalmente
            if dist < 15:
                self.selecionar_camera(ip)
                return

            # Se for um arrasto, tenta soltar no slot sob o mouse
            target_idx = self.encontrar_slot_por_coords(event.x_root, event.y_root)
            if target_idx is not None:
                self.atribuir_ip_ao_slot(target_idx, ip)
                self.selecionar_slot(target_idx)
        finally:
            self.press_data = None

    def selecionar_camera(self, ip):
        # Esta função é chamada ao clicar na lista lateral
        if self.slot_selecionado is not None:
            self.atribuir_ip_ao_slot(self.slot_selecionado, ip)
            self.selecionar_slot(self.slot_selecionado)

    def pintar_botao(self, ip, cor):
        if ip and ip in self.botoes_referencia: self.botoes_referencia[ip]['frame'].configure(fg_color=cor)

    def pintar_predefinicao(self, nome, cor):
        if nome and nome in self.predefinicao_widgets:
            self.predefinicao_widgets[nome].configure(fg_color=cor)

    def trocar_qualidade(self, ip, novo_canal):
        if not ip: return
        handler = self.camera_handlers.get(ip)
        if handler and handler != "CONECTANDO":
            if getattr(handler, 'canal', 102) != novo_canal:
                handler.parar()
                del self.camera_handlers[ip]
                self.iniciar_conexao_assincrona(ip, novo_canal)

    def formatar_nome(self, nome, max_chars=100):
        if not nome: return ""
        if len(nome) > max_chars: return nome[:max_chars-3] + "..."
        return nome

    def iniciar_conexao_assincrona(self, ip, canal=102):
        if not ip or ip == "0.0.0.0": return
        agora = time.time()

        # Respeita cooldown de falha
        if ip in self.cooldown_conexoes:
            cooldown_data = self.cooldown_conexoes[ip]
            ts = cooldown_data[0] if isinstance(cooldown_data, tuple) else cooldown_data
            if agora - ts < 60: return

        # Verifica se já está conectando ou rodando
        if ip in self.camera_handlers:
            handler = self.camera_handlers[ip]
            if handler == "CONECTANDO":
                # Se estiver marcado como conectando mas NÃO estiver na fila, re-adiciona (Race Condition fix)
                if ip not in self.ips_em_fila:
                    self.ips_em_fila.add(ip)
                    self.fila_pendente_conexoes.put((ip, canal))
                return
            if getattr(handler, 'rodando', False): return
            del self.camera_handlers[ip]

        # Evita duplicar na fila
        if ip in self.ips_em_fila: return

        self.camera_handlers[ip] = "CONECTANDO"
        self.ips_em_fila.add(ip)
        self.fila_pendente_conexoes.put((ip, canal))

    def _thread_conectar(self, ip, canal):
        try:
            nova_cam = CameraHandler(ip, canal, user=self.user_ptz, password=self.pass_ptz)
            nova_cam.nome_display = self.dados_cameras.get(ip, "")
            sucesso = nova_cam.iniciar()
            # Passa o erro detalhado se houver
            erro = getattr(nova_cam, 'ultimo_erro', None)
            self.fila_conexoes.put((sucesso, nova_cam, ip, erro))
        except Exception as e:
            print(f"Erro crítico na thread de conexão ({ip}): {e}")
            self.fila_conexoes.put((False, None, ip, "ERRO CRITICO"))

    def _pos_conexao(self, sucesso, camera_obj, ip, erro=None):
        if sucesso:
            # print(f"LOG: Conexão bem-sucedida com {ip}")
            self.camera_handlers[ip] = camera_obj
            if ip in self.cooldown_conexoes: del self.cooldown_conexoes[ip]
        else:
            # print(f"LOG: Falha na conexão final com {ip}")
            if ip in self.camera_handlers: del self.camera_handlers[ip]
            self.cooldown_conexoes[ip] = (time.time(), erro)
            for i, grid_ip in enumerate(self.grid_cameras):
                if grid_ip == ip:
                    try:
                        msg = f"{erro}\n{ip}" if erro else f"FALHA CONEXÃO\n{ip}"
                        self.slot_labels[i].configure(image=None, text=msg)
                        self.slot_labels[i].image = None
                        self.slot_ctk_images[i] = None
                    except: pass
        self.atualizar_botoes_controle()

    def loop_exibicao(self):
        delay_proximo_ciclo = 30
        try:
            # Lógica de detecção de restauração (Minimizado -> Normal)
            is_iconic = self.state() == "iconic"
            force_refresh = False

            if is_iconic:
                self.iconic_state = True
                delay_proximo_ciclo = 500
                return

            if getattr(self, 'iconic_state', False):
                # Acabou de restaurar: Agenda recuperação profunda para evitar RecursionError
                self.iconic_state = False
                self.after(200, self.recuperar_interface_pos_minimizacao)
                force_refresh = True

            self.iconic_state = False

            # Atualiza o scaling da janela a cada 50 loops (aprox 1.5s) para economizar chamadas
            self._loop_counter += 1
            if self._loop_counter >= 50:
                self._window_scaling = self._get_window_scaling()
                self._loop_counter = 0

            # Atualiza botões de controle periodicamente para garantir responsividade e sincronia
            self.atualizar_botoes_controle()
            self._processar_queue_bio()

            # Processa novas conexões
            while not self.fila_conexoes.empty():
                try:
                    res = self.fila_conexoes.get_nowait()
                    if len(res) == 4:
                        sucesso, camera_obj, ip, erro = res
                        self._pos_conexao(sucesso, camera_obj, ip, erro)
                    else:
                        sucesso, camera_obj, ip = res
                        self._pos_conexao(sucesso, camera_obj, ip)
                except: pass

            agora = time.time()
            scaling = self._window_scaling
            indices_trabalho = [self.slot_maximized] if self.slot_maximized is not None else range(self.num_slots)

            # Mapeia quais IPs estão sendo processados para compartilhar frames se possível (IP -> PIL Image)
            current_ips_pil = {}

            # Gestão de Atividade dos Handlers
            for h in self.camera_handlers.values():
                if h != "CONECTANDO": h.ativo = False

            # Se estiver gravando tudo, todas as câmeras do viewport atual devem estar ativas
            indices_ativos = range(self.num_slots) if self.gravando_tudo else indices_trabalho

            for idx in indices_ativos:
                ip_work = self.grid_cameras[idx]
                h_work = self.camera_handlers.get(ip_work)
                if h_work and h_work != "CONECTANDO": h_work.ativo = True

            for i in range(self.num_slots):
                ip = self.grid_cameras[i]

                # Se estiver gravando tudo, queremos processar todos os frames,
                # mas não necessariamente atualizar a UI se o slot não estiver em indices_trabalho
                estamos_gravando_este = self.gravando_tudo and ip != "0.0.0.0"

                # Verifica se houve timeout na gravação
                handler = self.camera_handlers.get(ip)
                if handler and handler != "CONECTANDO" and handler.timeout_atingido:
                    handler.timeout_atingido = False
                    nome = self.dados_cameras.get(ip, ip)
                    self.abrir_modal_alerta("Gravação Finalizada", f"A gravação da câmera {nome} foi finalizada automaticamente após 10 minutos.", show_open_folder=True)

                # Caso o slot deva estar vazio ou não esteja no foco de atualização
                if not ip or ip == "0.0.0.0" or (i not in indices_trabalho and not estamos_gravando_este):
                    # Segurança: se o slot deveria estar vazio, garante texto e imagem vazia
                    if ip == "0.0.0.0":
                        try:
                            target_text = ""
                            # Verifica se precisa atualizar para evitar cintilação (usando cache)
                            if (self.cache_ui_text[i] != target_text or
                                self.cache_ui_image[i] != self.img_vazia):
                                self.slot_labels[i].configure(image=self.img_vazia, text=target_text)
                                self.slot_labels[i].image = self.img_vazia
                                self.cache_ui_text[i] = target_text
                                self.cache_ui_image[i] = self.img_vazia
                                self.slot_ctk_images[i] = None
                        except: pass
                    continue

                # Verifica erro de conexão
                if ip in self.cooldown_conexoes:
                    cooldown_data = self.cooldown_conexoes[ip]
                    ts = cooldown_data[0] if isinstance(cooldown_data, tuple) else cooldown_data
                    erro = cooldown_data[1] if isinstance(cooldown_data, tuple) else "FALHA CONEXÃO"

                    if agora - ts < 60:
                        try:
                            target_status = f"{erro}\n{ip}" if i == self.slot_selecionado else erro
                            if self.cache_ui_image[i] != self.img_vazia or self.cache_ui_text[i] != target_status:
                                self.slot_labels[i].configure(image=self.img_vazia, text=target_status)
                                self.slot_labels[i].image = self.img_vazia
                                self.cache_ui_text[i] = target_status
                                self.cache_ui_image[i] = self.img_vazia
                                self.slot_ctk_images[i] = None
                        except: pass
                        continue

                handler = self.camera_handlers.get(ip)
                if handler is None:
                    # Decide canal inicial dependendo se está maximizado ou não
                    canal_alvo = self.obter_canal_alvo(ip)
                    self.iniciar_conexao_assincrona(ip, canal_alvo)
                    continue
                if handler == "CONECTANDO":
                    target_status = f"CONECTANDO...\n{ip}" if i == self.slot_selecionado else "CONECTANDO..."
                    if self.cache_ui_text[i] != target_status:
                        self.slot_labels[i].configure(text=target_status)
                        self.cache_ui_text[i] = target_status
                    continue

                try:
                    # Calcula tamanhos físicos
                    wf = self.slot_frames[i].winfo_width()
                    hf = self.slot_frames[i].winfo_height()
                    wf = int(max(10, wf - 6))
                    hf = int(max(10, hf - 6))

                    # Só atualiza handler se o tamanho mudou (evita locks desnecessários)
                    if self.cache_ui_size[i] != (wf, hf):
                        handler.tamanho_alvo = (wf, hf)
                        self.cache_ui_size[i] = (wf, hf)

                    # Usa LINEAR para maximizada e NEAREST para miniaturas (melhor performance)
                    handler.interpolation = cv2.INTER_LINEAR if self.slot_maximized == i else cv2.INTER_NEAREST

                    # Verifica se já processamos este IP neste loop
                    pil_img = current_ips_pil.get(ip)
                    if pil_img is None:
                        if handler.novo_frame or force_refresh:
                            pil_img = handler.pegar_frame()
                        elif self.gravando_tudo:
                            with handler.lock:
                                pil_img = handler.frame_pil

                        if pil_img:
                            current_ips_pil[ip] = pil_img

                    if pil_img and i in indices_trabalho:
                        wl, hl = wf / scaling, hf / scaling

                        try:
                            # Abordagem de criação direta para garantir atualização (testando se resolve 'dark screen')
                            # Mas mantendo cache para não explodir pyimages
                            if self.slot_ctk_images[i] is None:
                                self.slot_ctk_images[i] = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(wl, hl))
                                # print(f"DEBUG: Slot {i} ({ip}) - Primeiro Frame ({pil_img.size})")
                            else:
                                # Tenta atualizar o objeto existente
                                self.slot_ctk_images[i].configure(light_image=pil_img, dark_image=pil_img, size=(wl, hl))

                            # SEMPRE garante que o label está apontando para o objeto de cache e sem texto
                            if self.cache_ui_image[i] != self.slot_ctk_images[i] or self.cache_ui_text[i] != "":
                                self.slot_labels[i].configure(image=self.slot_ctk_images[i], text="")
                                self.slot_labels[i].image = self.slot_ctk_images[i]
                                self.cache_ui_image[i] = self.slot_ctk_images[i]
                                self.cache_ui_text[i] = ""
                        except Exception as e:
                                # print(f"DEBUG: Erro ao renderizar frame no slot {i}: {e}")
                                # Se falhar muito, tentamos recriar o cache do slot
                                self.slot_ctk_images[i] = None
                    else:
                        # Stream aberto mas sem frames (pode estar carregando ou com erro de codec)
                        # if i % 100 == 0: # Log esparso para não inundar
                        #     print(f"DEBUG: Slot {i} ({ip}) - Aguardando frame válido...")
                        pass

                except Exception as e:
                    # print(f"Erro render slot {i}: {e}")
                    pass

            # Lógica de Gravação em Mosaico (Total)
            if self.gravando_tudo:
                agora_rec = time.time()
                if agora_rec - self.ultimo_frame_tudo_tempo >= (1.0 / self.fps_tudo):
                    # Define resolução do mosaico (320x240 por slot)
                    sw, sh = 320, 240
                    mos_w = self.grid_cols * sw
                    mos_h = self.grid_rows * sh

                    mosaic = np.zeros((mos_h, mos_w, 3), dtype=np.uint8)

                    for i in range(self.num_slots):
                        ip = self.grid_cameras[i]
                        row, col = i // self.grid_cols, i % self.grid_cols

                        img_bgr = None
                        pil_img = current_ips_pil.get(ip)
                        if pil_img:
                            # Converte PIL para BGR para o OpenCV VideoWriter
                            img_np = np.array(pil_img)
                            img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
                            img_bgr = cv2.resize(img_bgr, (sw, sh), interpolation=cv2.INTER_LINEAR)
                        else:
                            # Se não tiver frame mas tiver IP, coloca uma mensagem
                            img_bgr = np.zeros((sh, sw, 3), dtype=np.uint8)
                            if ip and ip != "0.0.0.0":
                                cv2.putText(img_bgr, "SEM SINAL", (20, sh//2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2)

                        mosaic[row*sh:(row+1)*sh, col*sw:(col+1)*sw] = img_bgr

                    # Adiciona Timestamp no mosaico
                    timestamp_str = time.strftime("%d/%m/%Y %H:%M:%S")
                    cv2.putText(mosaic, timestamp_str, (10, mos_h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,0,255), 2)

                    # Inicializa VideoWriter se necessário
                    if self.video_writer_tudo is None:
                        try:
                            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                            self.video_writer_tudo = cv2.VideoWriter(self.caminho_video_tudo, fourcc, self.fps_tudo, (mos_w, mos_h))
                        except Exception as e:
                            print(f"Erro ao iniciar VideoWriter Tudo: {e}")
                            self.gravando_tudo = False

                    if self.video_writer_tudo is not None:
                        self.video_writer_tudo.write(mosaic)

                    self.ultimo_frame_tudo_tempo = agora_rec

        except Exception as e:
            # print(f"Erro no loop de exibicao: {e}")
            delay_proximo_ciclo = 200
        finally:
            # O agendamento ocorre sempre aqui, garantindo que o loop nunca pare e não duplique
            try:
                self.after(delay_proximo_ciclo, self.loop_exibicao)
            except:
                pass

    def filtrar_lista(self):
        termo = self.entry_busca.get().lower()
        for item in self.botoes_referencia.values(): item['frame'].pack_forget()
        for ip in self.obter_ips_ordenados():
            item = self.botoes_referencia.get(ip)
            if not item: continue
            nome = self.dados_cameras.get(ip, "").lower()
            if termo in ip or termo in nome: item['frame'].pack(fill="x", pady=2)
        try:
            if hasattr(self.scroll_frame, "_parent_canvas"): self.scroll_frame._parent_canvas.yview_moveto(0)
        except: pass

    def alternar_edicao_nome(self):
        if not self.ip_selecionado: return
        self.abrir_modal_input("Renomear Câmera", "Digite o novo nome para a câmera:",
                               self.salvar_nome, valor_inicial=self.dados_cameras.get(self.ip_selecionado, ""))

    def salvar_nome(self, novo_nome):
        if self.ip_selecionado:
            self.dados_cameras[self.ip_selecionado] = novo_nome
            with open(self.arquivo_config, "w", encoding='utf-8') as f:
                json.dump(self.dados_cameras, f, ensure_ascii=False, indent=4)

            # Atualiza handler se existir
            handler = self.camera_handlers.get(self.ip_selecionado)
            if handler and handler != "CONECTANDO":
                handler.nome_display = novo_nome

            # Atualiza UI se estiver visível
            if self.ip_selecionado in self.botoes_referencia:
                self.botoes_referencia[self.ip_selecionado]['lbl_nome'].configure(text=novo_nome)
            self.filtrar_lista()

    def abrir_modal_adicionar_camera(self):
        modal = ctk.CTkToplevel(self)
        modal.title("Adicionar Câmera")
        modal.geometry("400x350")
        modal.resizable(False, False)
        modal.attributes("-topmost", True)

        try:
            self.update_idletasks()
            x = self.winfo_x() + (self.winfo_width() // 2) - 200
            y = self.winfo_y() + (self.winfo_height() // 2) - 175
            modal.geometry(f"+{x}+{y}")
        except: pass

        ctk.CTkLabel(modal, text="Adicionar Nova Câmera", font=("Roboto", 16, "bold")).pack(pady=20)

        ctk.CTkLabel(modal, text="IP da Câmera:").pack()
        entry_ip = ctk.CTkEntry(modal, width=300, placeholder_text="Ex: 192.168.7.50")
        entry_ip.pack(pady=5)

        ctk.CTkLabel(modal, text="Nome da Câmera:").pack()
        entry_nome = ctk.CTkEntry(modal, width=300, placeholder_text="Ex: Portão Principal")
        entry_nome.pack(pady=5)

        def confirmar():
            ip = entry_ip.get().strip()
            nome = entry_nome.get().strip()
            if not ip:
                self.abrir_modal_alerta("Erro", "O IP é obrigatório.")
                return
            modal.destroy()
            self.adicionar_camera_confirmado(ip, nome)

        btn_conf = ctk.CTkButton(modal, text="Confirmar", fg_color=self.ACCENT_RED, hover_color=self.ACCENT_WINE,
                                  corner_radius=0, height=40, command=confirmar)
        btn_conf.pack(fill="x", padx=40, pady=20)

        btn_canc = ctk.CTkButton(modal, text="Cancelar", fg_color=self.GRAY_DARK, hover_color=self.TEXT_S,
                                  corner_radius=0, height=40, command=modal.destroy)
        btn_canc.pack(fill="x", padx=40)

        modal.bind("<Return>", lambda e: confirmar())

    def adicionar_camera_confirmado(self, ip, nome):
        if ip in self.ips_unicos:
            self.abrir_modal_alerta("Aviso", "Este IP já existe na lista.")
            return

        self.ips_unicos.append(ip)
        # Ordena a lista
        self.ips_unicos.sort(key=lambda x: [int(d) if d.isdigit() else 0 for d in x.split('.')])

        if nome:
            self.dados_cameras[ip] = nome
            with open(self.arquivo_config, "w", encoding='utf-8') as f:
                json.dump(self.dados_cameras, f, ensure_ascii=False, indent=4)

        self.salvar_lista_ips()
        self.atualizar_lista_cameras_ui()
        self.filtrar_lista()

    def confirmar_exclusao_camera_da_lista(self, ip):
        self.abrir_modal_confirmacao("Excluir Câmera", f"Deseja remover o IP {ip} da lista de câmeras?",
                                     lambda: self.excluir_camera_da_lista(ip))

    def excluir_camera_da_lista(self, ip):
        if ip in self.ips_unicos:
            self.ips_unicos.remove(ip)
            if ip in self.dados_cameras:
                del self.dados_cameras[ip]
                with open(self.arquivo_config, "w", encoding='utf-8') as f:
                    json.dump(self.dados_cameras, f, ensure_ascii=False, indent=4)

            self.salvar_lista_ips()
            self.atualizar_lista_cameras_ui()
            self.filtrar_lista()

    def gerar_lista_ips(self):
        base = ["192.168.7.2", "192.168.7.3", "192.168.7.4", "192.168.7.20", "192.168.7.21",
                "192.168.7.22", "192.168.7.23", "192.168.7.24", "192.168.7.26", "192.168.7.27",
                "192.168.7.31", "192.168.7.32", "192.168.7.33", "192.168.7.35", "192.168.7.37",
                "192.168.7.39", "192.168.7.43", "192.168.7.78", "192.168.7.79", "192.168.7.81",
                "192.168.7.89", "192.168.7.92", "192.168.7.94", "192.168.7.98", "192.168.7.99"]
        base += [f"192.168.7.{i}" for i in range(100, 216)]
        base += ["192.168.7.237", "192.168.7.246", "192.168.7.247", "192.168.7.248", "192.168.7.249",
                 "192.168.7.250", "192.168.7.251", "192.168.7.252"]
        ips = sorted(list(set(base)), key=lambda x: [int(d) for d in x.split('.')])
        return ips

    def carregar_lista_ips(self):
        if os.path.exists(self.arquivo_ips):
            try:
                with open(self.arquivo_ips, "r", encoding='utf-8') as f:
                    return json.load(f)
            except: pass
        ips = self.gerar_lista_ips()
        self.salvar_lista_ips(ips)
        return ips

    def salvar_lista_ips(self, ips=None):
        if ips is None: ips = self.ips_unicos
        try:
            with open(self.arquivo_ips, "w", encoding='utf-8') as f:
                json.dump(ips, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Erro ao salvar lista de IPs: {e}")

    def carregar_config(self):
        if os.path.exists(self.arquivo_config):
            try:
                with open(self.arquivo_config, "r", encoding='utf-8') as f: return json.load(f)
            except: pass
        return {}

    def obter_ips_ordenados(self):
        def chave_ordenacao(ip): return self.dados_cameras.get(ip, f"IP {ip}").lower()
        return sorted(self.ips_unicos, key=chave_ordenacao)

    def _inicializar_icones_navegacao(self):
        """Cria ícones de setas brancas para os botões de navegação."""
        self.nav_icons = {}
        tamanho_canvas = (40, 40)
        cor_flecha = "white"

        # Coordenadas para setas (triângulos simples) centradas em 40x40
        direcoes = {
            "UP": [(20, 10), (10, 30), (30, 30)],
            "DOWN": [(20, 30), (10, 10), (30, 10)],
            "LEFT": [(10, 20), (30, 10), (30, 30)],
            "RIGHT": [(30, 20), (10, 10), (10, 30)]
        }

        for dir_name, pontos in direcoes.items():
            img = Image.new("RGBA", tamanho_canvas, (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.polygon(pontos, fill=cor_flecha)
            # Usamos size=(20, 20) para que o ícone fique bem centrado no botão
            self.nav_icons[dir_name] = ctk.CTkImage(img, size=(20, 20))

    def criar_interface_grid(self):
        """Cria os elementos visuais do grid de câmeras."""
        # Grid Frame (Câmeras)
        self.grid_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.grid_frame.pack(side="top", expand=True, fill="both", padx=0, pady=0)

        for i in range(self.grid_rows): self.grid_frame.grid_rowconfigure(i, weight=1)
        for i in range(self.grid_cols): self.grid_frame.grid_columnconfigure(i, weight=1)

        # Botões de Controle
        self.btn_expandir = ctk.CTkButton(self.grid_frame, text="Aumentar", width=100, height=30,
                                           fg_color=self.GRAY_DARK, hover_color=self.ACCENT_RED,
                                           corner_radius=0, command=self.toggle_grid_layout)

        self.btn_gravar = ctk.CTkButton(self.grid_frame, text="Gravar", width=100, height=30,
                                         fg_color=self.GRAY_DARK, hover_color=self.ACCENT_RED,
                                         corner_radius=0, command=self.toggle_gravacao)

        self.btn_mais_opcoes = ctk.CTkButton(self.grid_frame, text="Mais Opções", width=100, height=30,
                                              fg_color=self.GRAY_DARK, hover_color=self.ACCENT_RED,
                                              corner_radius=0, command=self.abrir_menu_opcoes)

        self.slot_frames = []
        # Botões de Navegação do Grid (Usando CTkButton com corner_radius=0 para fundo quadrado)
        self.btn_nav_up = ctk.CTkButton(self.grid_frame, text="", width=40, height=40, corner_radius=0,
                                         fg_color=self.ACCENT_RED, hover_color=self.ACCENT_WINE,
                                         image=self.nav_icons["UP"], command=lambda: self.navegar_grid("UP"))

        self.btn_nav_down = ctk.CTkButton(self.grid_frame, text="", width=40, height=40, corner_radius=0,
                                           fg_color=self.ACCENT_RED, hover_color=self.ACCENT_WINE,
                                           image=self.nav_icons["DOWN"], command=lambda: self.navegar_grid("DOWN"))

        self.btn_nav_left = ctk.CTkButton(self.grid_frame, text="", width=40, height=40, corner_radius=0,
                                           fg_color=self.ACCENT_RED, hover_color=self.ACCENT_WINE,
                                           image=self.nav_icons["LEFT"], command=lambda: self.navegar_grid("LEFT"))

        self.btn_nav_right = ctk.CTkButton(self.grid_frame, text="", width=40, height=40, corner_radius=0,
                                            fg_color=self.ACCENT_RED, hover_color=self.ACCENT_WINE,
                                            image=self.nav_icons["RIGHT"], command=lambda: self.navegar_grid("RIGHT"))

        self.slot_labels = []
        for i in range(self.num_slots):
            row, col = i // self.grid_cols, i % self.grid_cols
            frm = ctk.CTkFrame(self.grid_frame, fg_color=self.BG_SIDEBAR, corner_radius=2, border_width=2, border_color="black")
            frm.grid(row=row, column=col, padx=1, pady=1, sticky="nsew")
            frm.pack_propagate(False)

            lbl = ctk.CTkLabel(frm, text="", corner_radius=0)
            lbl.pack(expand=True, fill="both", padx=2, pady=2)

            for widget in [frm, lbl]:
                widget.bind("<Button-1>", lambda e, idx=i: self.ao_pressionar_slot(e, idx))
                widget.bind("<ButtonRelease-1>", lambda e, idx=i: self.ao_soltar_slot(e, idx))
                widget.bind("<B1-Motion>", lambda e, idx=i: self.ao_arrastar_slot(e, idx))
                widget.bind("<MouseWheel>", self.ao_scroll_mouse)
                widget.bind("<Button-4>", self.ao_scroll_mouse)
                widget.bind("<Button-5>", self.ao_scroll_mouse)

            self.slot_frames.append(frm)
            self.slot_labels.append(lbl)

    def criar_seletor_ip(self, parent):
        frame_seletor = ctk.CTkFrame(parent, fg_color="transparent")
        frame_seletor.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(frame_seletor, text="SELETOR DE IP", font=("Roboto", 12, "bold"), text_color=self.TEXT_S).pack(pady=(0, 5))

        container_octetos = ctk.CTkFrame(frame_seletor, fg_color="transparent")
        container_octetos.pack()

        self.octet_entries = []
        for i in range(4):
            col = ctk.CTkFrame(container_octetos, fg_color="transparent")
            col.pack(side="left")

            btn_up = ctk.CTkButton(col, text="▲", width=35, height=25, fg_color=self.GRAY_DARK, hover_color=self.ACCENT_RED,
                                   corner_radius=4, command=lambda idx=i: self.alterar_octeto(idx, 1))
            btn_up.pack(pady=2)

            ent = ctk.CTkEntry(col, width=45, font=("Roboto", 14, "bold"), justify="center")
            ent.insert(0, str(self.ip_seletor_atual[i]))
            ent.pack(pady=2)
            ent.bind("<KeyRelease>", lambda e, idx=i: self.ao_digitar_octeto(e, idx))
            ent.bind("<Return>", lambda e, idx=i: self.confirmar_digitacao_octeto(idx))
            self.octet_entries.append(ent)

            btn_down = ctk.CTkButton(col, text="▼", width=35, height=25, fg_color=self.GRAY_DARK, hover_color=self.ACCENT_RED,
                                     corner_radius=4, command=lambda idx=i: self.alterar_octeto(idx, -1))
            btn_down.pack(pady=2)

            if i < 3:
                ctk.CTkLabel(container_octetos, text=".", font=("Roboto", 20, "bold")).pack(side="left", padx=2, pady=(25, 0))

    def alterar_octeto(self, idx, delta):
        self.ip_seletor_atual[idx] = (self.ip_seletor_atual[idx] + delta) % 256
        self.atualizar_labels_seletor()

        # Se houver um slot selecionado, atualiza o IP dele
        if self.slot_selecionado is not None:
            novo_ip = ".".join(map(str, self.ip_seletor_atual))
            self.atribuir_ip_ao_slot(self.slot_selecionado, novo_ip)

    def ao_digitar_octeto(self, event, idx):
        val_str = self.octet_entries[idx].get()
        if val_str.isdigit():
            val = int(val_str)
            if 0 <= val <= 255:
                self.ip_seletor_atual[idx] = val
                if self.slot_selecionado is not None:
                    novo_ip = ".".join(map(str, self.ip_seletor_atual))
                    self.atribuir_ip_ao_slot(self.slot_selecionado, novo_ip, salvar=False) # Não salva em cada tecla

    def confirmar_digitacao_octeto(self, idx):
        val_str = self.octet_entries[idx].get()
        if val_str.isdigit():
            val = int(val_str)
            if 0 <= val <= 255:
                self.ip_seletor_atual[idx] = val
                if self.slot_selecionado is not None:
                    novo_ip = ".".join(map(str, self.ip_seletor_atual))
                    self.atribuir_ip_ao_slot(self.slot_selecionado, novo_ip, salvar=True)
        self.atualizar_labels_seletor()

    def atualizar_labels_seletor(self):
        for i, val in enumerate(self.ip_seletor_atual):
            if i < len(self.octet_entries):
                self.octet_entries[i].delete(0, "end")
                self.octet_entries[i].insert(0, str(val))

    def sincronizar_seletor_com_ip(self, ip):
        if not ip or ip == "0.0.0.0":
            return

        try:
            partes = ip.split('.')
            if len(partes) == 4:
                self.ip_seletor_atual = [int(p) for p in partes]
                self.atualizar_labels_seletor()
        except:
            pass

    def atualizar_lista_cameras_ui(self):
        self.update_idletasks()
        # Pequeno delay para garantir que o scroll_frame e sidebar tenham dimensões reais
        self.after(10, self._atualizar_lista_cameras_ui_impl)

    def _atualizar_lista_cameras_ui_impl(self):
        for child in self.scroll_frame.winfo_children():
            child.destroy()
        self.botoes_referencia = {}

        largura_sidebar = self.sidebar.winfo_width()
        if largura_sidebar <= 1:
            largura_sidebar = 320

        # Configurações de tamanho baseadas na preferência
        if self.tamanho_preview == "Grande":
            thumb_size = (200, 140)
            pack_side = "top"
            wrap_val = max(100, largura_sidebar - 40)
        else: # Pequeno
            thumb_size = (100, 70)
            pack_side = "left"
            wrap_val = max(100, largura_sidebar - 210)

        for ip in self.obter_ips_ordenados():
            lbl_thumb = None
            nome = self.dados_cameras.get(ip, f"IP {ip}")
            cor = self.ACCENT_WINE if ip == self.ip_selecionado else self.BG_SIDEBAR
            frm = ctk.CTkFrame(self.scroll_frame, fg_color=cor, border_width=0, border_color=self.GRAY_DARK)
            frm.pack(fill="x", pady=2)

            # Miniatura (Thumbnail)
            caminho_print = os.path.join(self.diretorio_prints, f"{ip.replace('.', '_')}.png")
            if os.path.exists(caminho_print):
                try:
                    img_pil = Image.open(caminho_print)
                    img_ctk = ctk.CTkImage(img_pil, size=thumb_size)
                    lbl_thumb = ctk.CTkLabel(frm, image=img_ctk, text="", width=thumb_size[0])
                    lbl_thumb.pack(side=pack_side, padx=2, pady=2)
                except: pass

            # Container para o texto (Label)
            txt_container = ctk.CTkFrame(frm, fg_color="transparent")
            txt_container.pack(side=pack_side, fill="both", expand=True, pady=5)

            # Cálculo aproximado de wraplength baseado na largura da sidebar
            # Reduzido para garantir que não corte e forçar o wrap mais cedo
            lbl_nome = ctk.CTkLabel(txt_container, text=nome, font=("Roboto", 12, "bold"),
                                    text_color=self.TEXT_P, anchor="w", justify="left",
                                    wraplength=wrap_val, width=wrap_val, height=0)
            lbl_nome.pack(fill="x", padx=10, pady=(2, 2))

            # Força wraplength no label interno do tkinter (customtkinter às vezes não aplica corretamente)
            try:
                lbl_nome.update_idletasks()
                lbl_nome._label.configure(wraplength=wrap_val)
            except: pass
            lbl_ip = ctk.CTkLabel(txt_container, text=ip, font=("Roboto", 11), text_color=self.TEXT_S, anchor="w")
            lbl_ip.pack(fill="x", padx=10, pady=(0, 4))


            widgets_para_bind = [txt_container, lbl_nome, lbl_ip]
            if lbl_thumb:
                widgets_para_bind.append(lbl_thumb)

            for widget in widgets_para_bind:
                widget.bind("<Button-1>", lambda e, x=ip: self.ao_pressionar_sidebar(e, x))
                widget.bind("<B1-Motion>", lambda e, x=ip: self.ao_arrastar_sidebar(e, x))
                widget.bind("<ButtonRelease-1>", lambda e, x=ip: self.ao_soltar_sidebar(e, x))
                widget.configure(cursor="hand2")

            self.botoes_referencia[ip] = {'frame': frm, 'lbl_nome': lbl_nome, 'lbl_ip': lbl_ip}

        self.filtrar_lista()

        # Força o scroll frame a recalcular sua região interna para evitar cortes
        # Realiza múltiplas tentativas para garantir que o layout foi processado
        def fix_scroll():
            try:
                self.update_idletasks()
                canvas = getattr(self.scroll_frame, "_parent_canvas", None) or getattr(self.scroll_frame, "_canvas", None)
                if canvas:
                    canvas.configure(scrollregion=canvas.bbox("all"))
            except: pass

        self.after(100, fix_scroll)
        self.after(300, fix_scroll)
        self.after(600, fix_scroll)

    # --- MÉTODOS DE PREDEFINIÇÕES ---
    def carregar_predefinicoes(self):
        if os.path.exists(self.arquivo_predefinicoes):
            try:
                with open(self.arquivo_predefinicoes, "r", encoding='utf-8') as f:
                    return json.load(f)
            except: pass

        # Migração de legado
        user_dir = os.path.expanduser("~")
        arquivo_legado = os.path.join(user_dir, "presets_grid_abi.json")
        if os.path.exists(arquivo_legado):
            try:
                with open(arquivo_legado, "r", encoding='utf-8') as f:
                    dados = json.load(f)
                    # Salva no novo local imediatamente
                    with open(self.arquivo_predefinicoes, "w", encoding='utf-8') as f_new:
                        json.dump(dados, f_new, ensure_ascii=False, indent=4)
                    return dados
            except: pass

        return {}

    def salvar_predefinicoes(self):
        try:
            with open(self.arquivo_predefinicoes, "w", encoding='utf-8') as f:
                json.dump(self.predefinicoes, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Erro ao salvar predefinicoes: {e}")

    def exportar_predefinicoes(self):
        caminho = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("Arquivos JSON", "*.json")],
            initialfile="predefinicoes_abi.json",
            title="Exportar Predefinições"
        )
        if caminho:
            try:
                with open(caminho, "w", encoding='utf-8') as f:
                    json.dump(self.predefinicoes, f, ensure_ascii=False, indent=4)
                self.abrir_modal_alerta("Sucesso", "Predefinições exportadas com sucesso!")
            except Exception as e:
                self.abrir_modal_alerta("Erro", f"Falha ao exportar predefinições: {e}")

    def importar_predefinicoes(self):
        caminho = filedialog.askopenfilename(
            filetypes=[("Arquivos JSON", "*.json")],
            title="Importar Predefinições"
        )
        if caminho:
            try:
                with open(caminho, "r", encoding='utf-8') as f:
                    importados = json.load(f)

                if not isinstance(importados, dict):
                    self.abrir_modal_alerta("Erro", "O arquivo selecionado não é uma predefinição válida.")
                    return

                self.predefinicoes.update(importados)
                self.salvar_predefinicoes()
                self.atualizar_lista_predefinicoes_ui()
                self.abrir_modal_alerta("Sucesso", f"{len(importados)} predefinições importadas com sucesso!")
            except json.JSONDecodeError:
                self.abrir_modal_alerta("Erro", "O arquivo selecionado contém um JSON inválido.")
            except Exception as e:
                self.abrir_modal_alerta("Erro", f"Falha ao importar predefinições: {e}")

    def toggle_lock_predefinicao(self, nome):
        if nome in self.predefinicoes_desbloqueadas:
            self.predefinicoes_desbloqueadas.remove(nome)
            self.atualizar_lista_predefinicoes_ui()
        else:
            def on_success():
                self.predefinicoes_desbloqueadas.add(nome)
                self.atualizar_lista_predefinicoes_ui()
            self.solicitar_senha(on_success)

    def abrir_modal_salvar_predefinicao(self, callback):
        modal = ctk.CTkToplevel(self)
        modal.title("Salvar Predefinição")
        modal.geometry("400x300")
        modal.resizable(False, False)
        modal.attributes("-topmost", True)

        try:
            self.update_idletasks()
            x = self.winfo_x() + (self.winfo_width() // 2) - 200
            y = self.winfo_y() + (self.winfo_height() // 2) - 150
            modal.geometry(f"+{x}+{y}")
        except: pass

        ctk.CTkLabel(modal, text="Salvar Nova Predefinição", font=("Roboto", 16, "bold")).pack(pady=20)

        ctk.CTkLabel(modal, text="Nome da Predefinição:").pack()
        entry_nome = ctk.CTkEntry(modal, width=300, placeholder_text="Ex: Turno Manhã")
        entry_nome.pack(pady=5)
        entry_nome.focus_set()

        check_adm = ctk.CTkCheckBox(modal, text="Predefinição do Adm")
        check_adm.pack(pady=10)

        def confirmar():
            nome = entry_nome.get().strip()
            is_adm = check_adm.get() == 1
            if not nome:
                self.abrir_modal_alerta("Erro", "O nome é obrigatório.")
                return
            modal.destroy()
            callback(nome, is_adm)

        btn_conf = ctk.CTkButton(modal, text="Confirmar", fg_color=self.ACCENT_RED, hover_color=self.ACCENT_WINE,
                                  corner_radius=0, height=40, command=confirmar)
        btn_conf.pack(fill="x", padx=40, pady=10)

        btn_canc = ctk.CTkButton(modal, text="Cancelar", fg_color=self.GRAY_DARK, hover_color=self.TEXT_S,
                                  corner_radius=0, height=40, command=modal.destroy)
        btn_canc.pack(fill="x", padx=40)

        modal.bind("<Return>", lambda e: confirmar())

    def salvar_predefinicao_atual(self):
        def on_confirmed(nome, is_adm):
            if nome in self.predefinicoes:
                dados_existentes = self.predefinicoes[nome]
                if isinstance(dados_existentes, dict) and dados_existentes.get("is_adm", False) and nome not in self.predefinicoes_desbloqueadas:
                     self.abrir_modal_alerta("Erro", "Esta predefinição do Adm está bloqueada.")
                     return

                self.abrir_modal_confirmacao("Confirmar", f"A predefinição '{nome}' já existe. Deseja sobrescrevê-la?",
                                                lambda: self._salvar_predefinicao(nome, is_adm))
            else:
                self._salvar_predefinicao(nome, is_adm)

        self.abrir_modal_salvar_predefinicao(on_confirmed)

    def _salvar_predefinicao(self, nome, is_adm=False):
        # Converte chaves de tupla para strings "r,c" para salvar no JSON da predefinição
        vg_serializable = {f"{r},{c}": ip for (r, c), ip in self.virtual_grid.items()}

        # Salva o estado completo do grid virtual
        dados_predefinicao = {
            "grid_cameras": list(self.grid_cameras),
            "virtual_grid": vg_serializable,
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
            "is_adm": is_adm
        }

        self.predefinicoes[nome] = dados_predefinicao
        self.ultima_predefinicao = nome
        self.salvar_predefinicoes()
        self.atualizar_lista_predefinicoes_ui()

    def aplicar_predefinicao(self, nome):
        # Interrompe gravação global se houver troca de predefinição,
        # pois pode mudar o viewport e confundir o mosaico esperado
        if self.gravando_tudo:
            self.toggle_gravacao_tudo()

        dados = self.predefinicoes.get(nome)
        if not dados: return

        # Limpa o cooldown para permitir reconexão imediata se for uma predefinicao
        self.cooldown_conexoes.clear()

        if isinstance(dados, dict):
            # Novo formato (Estado Completo)
            self.offset_x = dados.get("offset_x", 0)
            self.offset_y = dados.get("offset_y", 0)

            vg_data = dados.get("virtual_grid", {})
            self.virtual_grid = {}
            for k, v in vg_data.items():
                try:
                    r, c = map(int, k.split(','))
                    self.virtual_grid[(r, c)] = v
                except: pass

            # Reconstrói grid_cameras (o viewport salvo)
            predefinicao_ips = []
            for i in range(self.num_slots):
                r, c = i // self.grid_cols, i % self.grid_cols
                ip = self.virtual_grid.get((r + self.offset_y, c + self.offset_x), "0.0.0.0")
                predefinicao_ips.append(ip)
        else:
            # Legado (Apenas Lista de IPs)
            self.offset_x = 0
            self.offset_y = 0
            self.virtual_grid = {}
            predefinicao_ips = list(dados)
            # Semeia virtual_grid legado
            for i, ip in enumerate(predefinicao_ips):
                r, c = i // self.grid_cols, i % self.grid_cols
                self.virtual_grid[(r, c)] = ip

        # Gerencia cores na lista de predefinicoes
        if self.ultima_predefinicao:
            self.pintar_predefinicao(self.ultima_predefinicao, self.BG_SIDEBAR)
        self.ultima_predefinicao = nome
        self.pintar_predefinicao(nome, self.ACCENT_WINE)

        # 1. Identifica quais IPs devem ser mantidos e quais devem ser fechados
        # ABI Rule: Todas as câmeras da predefinição devem ficar ativas
        ips_novos_set = set(ip for ip in self.virtual_grid.values() if ip and ip != "0.0.0.0")

        # Fecha handlers de câmeras que NÃO estão no novo grid virtual
        for ip_h in list(self.camera_handlers.keys()):
            if ip_h not in ips_novos_set:
                h = self.camera_handlers[ip_h]
                if h != "CONECTANDO":
                    try: h.parar()
                    except: pass
                del self.camera_handlers[ip_h]

        # 2. Limpa filas e estados de conexão pendente
        while not self.fila_pendente_conexoes.empty():
            try: self.fila_pendente_conexoes.get_nowait()
            except: pass
        self.ips_em_fila.clear()

        # 3. Atualiza os dados do grid e UI chamando atualizar_viewport_grid
        # Isso garante que grid_cameras e virtual_grid fiquem perfeitamente sincronizados
        self.atualizar_viewport_grid(salvar=True)

        # 4. Inicia conexões para os novos IPs que ainda não estão no handler ou estão "travados"
        for ip in ips_novos_set:
            status = self.camera_handlers.get(ip)

            if not status:
                # Novo IP, dispara conexão
                self.iniciar_conexao_assincrona(ip, self.obter_canal_alvo(ip))
            elif status == "CONECTANDO":
                # Já estava tentando, mas como limpamos a fila acima, precisamos re-enfileirar
                self.iniciar_conexao_assincrona(ip, self.obter_canal_alvo(ip))
            else:
                # Handler já existe e está rodando
                status.set_canal(self.obter_canal_alvo(ip))

        # 5. Restaura layout se necessário e seleciona slot
        if self.slot_maximized is not None:
            self.restaurar_grid()

        self.selecionar_slot(self.slot_selecionado)
        self.update_idletasks()

    def sobrescrever_predefinicao(self, nome):
        self.abrir_modal_confirmacao("Confirmar", f"Deseja sobrescrever o predefinição '{nome}' com a configuração atual?",
                                     lambda: self._sobrescrever_predefinicao(nome))

    def _sobrescrever_predefinicao(self, nome):
        # Converte chaves de tupla para strings "r,c" para salvar no JSON da predefinição
        vg_serializable = {f"{r},{c}": ip for (r, c), ip in self.virtual_grid.items()}

        # Preserva o flag is_adm se já existir
        is_adm = False
        dados_antigos = self.predefinicoes.get(nome)
        if isinstance(dados_antigos, dict):
            is_adm = dados_antigos.get("is_adm", False)

        # Salva o estado completo do grid virtual
        dados_predefinicao = {
            "grid_cameras": list(self.grid_cameras),
            "virtual_grid": vg_serializable,
            "offset_x": self.offset_x,
            "offset_y": self.offset_y,
            "is_adm": is_adm
        }

        self.predefinicoes[nome] = dados_predefinicao
        self.salvar_predefinicoes()
        self.ultima_predefinicao = nome
        self.atualizar_lista_predefinicoes_ui()

    def deletar_predefinicao(self, nome):
        self.abrir_modal_confirmacao("Confirmar", f"Deseja realmente excluir o predefinição '{nome}'?",
                                     lambda: self._deletar_predefinicao(nome))

    def _deletar_predefinicao(self, nome):
        if nome in self.predefinicoes:
            del self.predefinicoes[nome]
            if self.ultima_predefinicao == nome:
                self.ultima_predefinicao = None
            if nome in self.predefinicoes_desbloqueadas:
                self.predefinicoes_desbloqueadas.remove(nome)
            self.salvar_predefinicoes()
            self.atualizar_lista_predefinicoes_ui()

    def renomear_predefinicao(self, nome_antigo):
        def on_name_entered(novo_nome):
            novo_nome = novo_nome.strip()
            if not novo_nome:
                self.abrir_modal_alerta("Erro", "O nome da predefinição não pode ser vazio.")
                return

            if novo_nome and novo_nome != nome_antigo:
                if novo_nome in self.predefinicoes:
                    self.abrir_modal_alerta("Erro", "Já existe uma predefinição com este nome.")
                    return

                if nome_antigo in self.predefinicoes:
                    self.predefinicoes[novo_nome] = self.predefinicoes.pop(nome_antigo)
                    if self.ultima_predefinicao == nome_antigo:
                        self.ultima_predefinicao = novo_nome
                    if nome_antigo in self.predefinicoes_desbloqueadas:
                        self.predefinicoes_desbloqueadas.remove(nome_antigo)
                        self.predefinicoes_desbloqueadas.add(novo_nome)
                    self.salvar_predefinicoes()
                    self.atualizar_lista_predefinicoes_ui()

        self.abrir_modal_input("Renomear Predefinição", f"Novo nome para '{nome_antigo}':",
                               on_name_entered, valor_inicial=nome_antigo)

    def atualizar_lista_predefinicoes_ui(self):
        self.update_idletasks()
        self.after(10, self._atualizar_lista_predefinicoes_ui_impl)

    def _atualizar_lista_predefinicoes_ui_impl(self):
        for child in self.scroll_predefinicoes.winfo_children():
            child.destroy()
        self.predefinicao_widgets = {}

        largura_sidebar = self.sidebar.winfo_width()
        if largura_sidebar <= 1:
            largura_sidebar = 320

        for nome in sorted(self.predefinicoes.keys(), key=str.lower):
            dados = self.predefinicoes.get(nome, {})
            is_adm = False
            if isinstance(dados, dict):
                is_adm = dados.get("is_adm", False)

            cor = self.ACCENT_WINE if nome == self.ultima_predefinicao else self.BG_SIDEBAR
            frm = ctk.CTkFrame(self.scroll_predefinicoes, fg_color=cor, border_width=0, border_color=self.GRAY_DARK)
            frm.pack(fill="x", pady=2, padx=2)

            # Bind no Frame para facilitar o clique
            frm.bind("<Button-1>", lambda e, n=nome: self.aplicar_predefinicao(n))
            frm.configure(cursor="hand2")

            is_desbloqueada = nome in self.predefinicoes_desbloqueadas

            # Botões de Controle (Ordem: X, ✎, 💾 - pack no lado direito)
            if not is_adm or is_desbloqueada:
                btn_del = ctk.CTkButton(frm, text="X", width=30, height=30, fg_color="transparent",
                                         text_color=self.TEXT_S, hover_color=self.ACCENT_RED,
                                         command=lambda n=nome: self.deletar_predefinicao(n))
                btn_del.pack(side="right", padx=5)

                btn_ren = ctk.CTkButton(frm, text="✎", width=30, height=30, fg_color="transparent",
                                         text_color=self.TEXT_S, hover_color=self.GRAY_DARK,
                                         command=lambda n=nome: self.renomear_predefinicao(n))
                btn_ren.pack(side="right", padx=2)

                btn_save = ctk.CTkButton(frm, text="💾", width=30, height=30, fg_color="transparent",
                                          text_color=self.TEXT_S, hover_color=self.GRAY_DARK,
                                          command=lambda n=nome: self.sobrescrever_predefinicao(n))
                btn_save.pack(side="right", padx=2)

            # Ícone de Cadeado para Predefinições Adm
            if is_adm:
                cadeado_icon = "🔓" if is_desbloqueada else "🔒"
                btn_lock = ctk.CTkButton(frm, text=cadeado_icon, width=30, height=30, fg_color="transparent",
                                          text_color=self.TEXT_S, hover_color=self.GRAY_DARK,
                                          command=lambda n=nome: self.toggle_lock_predefinicao(n))
                btn_lock.pack(side="left", padx=(5, 0))

            # Label de Nome (Expandível)
            offset_wrap = 190 if is_adm else 160
            wrap_val = max(100, largura_sidebar - offset_wrap)
            lbl = ctk.CTkLabel(frm, text=nome, font=("Roboto", 12, "bold"), text_color=self.TEXT_P,
                               anchor="w", cursor="hand2", wraplength=wrap_val, width=wrap_val, justify="left", height=0)
            lbl.pack(side="left", expand=True, fill="both", padx=10, pady=10)

            # Força wraplength no label interno
            try:
                lbl.update_idletasks()
                lbl._label.configure(wraplength=wrap_val)
            except: pass
            lbl.bind("<Button-1>", lambda e, n=nome: self.aplicar_predefinicao(n))

            self.predefinicao_widgets[nome] = frm

    def abrir_pasta_downloads(self):
        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        sistema = platform.system()
        try:
            if sistema == "Windows":
                os.startfile(downloads_dir)
            elif sistema == "Darwin":  # macOS
                subprocess.Popen(["open", downloads_dir])
            else:  # Linux e outros
                subprocess.Popen(["xdg-open", downloads_dir])
        except Exception as e:
            print(f"Erro ao abrir pasta de downloads: {e}")

    # --- MÉTODOS DE SCREENSHOT ---
    def capturar_imagem(self):
        if not self.ip_selecionado:
            self.abrir_modal_alerta("Aviso", "Nenhuma câmera selecionada.")
            return

        handler = self.camera_handlers.get(self.ip_selecionado)
        if not handler or handler == "CONECTANDO" or not handler.conectado:
            self.abrir_modal_alerta("Erro", "A câmera selecionada não está conectada.")
            return

        # Pega o frame atual
        with handler.lock:
            frame_pil = handler.frame_pil

        if frame_pil:
            try:
                ip_limpo = self.ip_selecionado.replace(".", "_")
                timestamp = time.strftime("%Y%m%d_%H%M%S")

                # 1. Salva para o histórico (timestamp)
                caminho_hist = os.path.join(self.diretorio_prints, f"{ip_limpo}_{timestamp}.png")
                frame_pil.save(caminho_hist)

                # 2. Salva como thumbnail (sobrescreve o padrão do IP)
                caminho_thumb = os.path.join(self.diretorio_prints, f"{ip_limpo}.png")
                frame_pil.save(caminho_thumb)

                self.abrir_modal_alerta("Sucesso", f"Imagem capturada com sucesso!\nSalva em: {os.path.basename(caminho_hist)}")

                # Atualiza a lista lateral para refletir a nova miniatura
                self.atualizar_lista_cameras_ui()
            except Exception as e:
                self.abrir_modal_alerta("Erro", f"Falha ao salvar imagem: {e}")
        else:
            self.abrir_modal_alerta("Erro", "Não foi possível obter um frame válido da câmera.")

if __name__ == "__main__":
    dados_sistema = carregar_dados_sistema()
    app = CentralMonitoramento(dados_iniciais=dados_sistema)
    app.mainloop()
