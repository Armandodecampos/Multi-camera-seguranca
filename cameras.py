import cv2
import customtkinter as ctk
from PIL import Image, ImageTk
import json
import os
import threading
import time
import socket
import queue
import requests
from requests.auth import HTTPDigestAuth
# Configuração de baixa latência para OpenCV/FFMPEG
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp;stimeout;5000000;buffer_size;2048000;analyzeduration;100000;probesize;100000;fflags;discardcorrupt;max_delay;500000;reorder_queue_size;16;rtsp_flags;prefer_tcp;reconnect;1;reconnect_streamed;1;reconnect_at_eof;1"
cv2.setNumThreads(1)

# Semáforo global para limitar conexões simultâneas (evita travamentos)
sem_conexao = threading.Semaphore(10)

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

                # Retrieve frame (decodifica)
                ret_ret, frame = self.cap.retrieve()
                if not ret_ret:
                    continue

                last_process_time = now

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

    def __init__(self):
        super().__init__()

        self.title("Sistema de Monitoramento ABI - Full Control V5 + PTZ")
        self.geometry("1200x800")
        ctk.set_appearance_mode("Dark")

        # Credenciais para PTZ
        self.user_ptz = "admin"
        self.pass_ptz = "1357gov@"

        self.protocol("WM_DELETE_WINDOW", self.ao_fechar)

        # Binds de Teclado
        self.bind("<Escape>", lambda event: self.sair_tela_cheia())

        # Binds para PTZ
        self.bind("<KeyPress-Up>", lambda e: self.comando_ptz("UP"))
        self.bind("<KeyPress-Down>", lambda e: self.comando_ptz("DOWN"))
        self.bind("<KeyPress-Left>", lambda e: self.comando_ptz("LEFT"))
        self.bind("<KeyPress-Right>", lambda e: self.comando_ptz("RIGHT"))

        self.bind("<KeyRelease-Up>", lambda e: self.comando_ptz("STOP"))
        self.bind("<KeyRelease-Down>", lambda e: self.comando_ptz("STOP"))
        self.bind("<KeyRelease-Left>", lambda e: self.comando_ptz("STOP"))
        self.bind("<KeyRelease-Right>", lambda e: self.comando_ptz("STOP"))

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

        self.carregar_posicao_janela()
        self.predefinicoes = self.carregar_predefinicoes()
        self.ips_unicos = self.carregar_lista_ips()
        self.dados_cameras = self.carregar_config()
        self.grid_cameras = self.carregar_grid()

        # Cache persistente de CTkImage por slot para evitar "pyimage" explosion
        self.slot_ctk_images = [None] * 20
        # Cache de estado da UI para evitar chamadas redundantes ao Tcl/Tk
        self.cache_ui_text = [None] * 20
        self.cache_ui_image = [None] * 20
        self.cache_ui_size = [None] * 20
        # Imagem 1x1 transparente para resets seguros
        self.img_vazia = ctk.CTkImage(Image.new('RGBA', (1, 1), (0,0,0,0)), size=(1, 1))

        # Controle da Sidebar
        self.sidebar_visible = True

        # --- LAYOUT ATUALIZADO ---
        self.grid_columnconfigure(0, weight=0) # Sidebar fixa
        self.grid_columnconfigure(1, weight=0) # Botão toggle fixo
        self.grid_columnconfigure(2, weight=1) # Main expande
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
        self.btn_config.pack(pady=10, padx=10, fill="x")

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
        self.btn_salvar_predefinicao.pack(fill="x", padx=10, pady=10)

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

        # Grid Frame (Câmeras)
        self.grid_frame = ctk.CTkFrame(self.main_frame, fg_color="#000000")
        self.grid_frame.pack(side="top", expand=True, fill="both", padx=0, pady=0)

        for i in range(4): self.grid_frame.grid_rowconfigure(i, weight=1)
        for i in range(5): self.grid_frame.grid_columnconfigure(i, weight=1)

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
        self.slot_labels = []
        for i in range(20):
            row, col = i // 5, i % 5
            frm = ctk.CTkFrame(self.grid_frame, fg_color=self.BG_SIDEBAR, corner_radius=2, border_width=2, border_color="black")
            frm.grid(row=row, column=col, padx=1, pady=1, sticky="nsew")
            frm.pack_propagate(False)

            lbl = ctk.CTkLabel(frm, text=f"Espaço {i+1}", corner_radius=0)
            lbl.pack(expand=True, fill="both", padx=2, pady=2)

            for widget in [frm, lbl]:
                widget.bind("<Button-1>", lambda e, idx=i: self.ao_pressionar_slot(e, idx))
                widget.bind("<ButtonRelease-1>", lambda e, idx=i: self.ao_soltar_slot(e, idx))

            self.slot_frames.append(frm)
            self.slot_labels.append(lbl)

        self.atualizar_lista_cameras_ui()
        # Restaura estado inicial
        for i, ip in enumerate(self.grid_cameras):
            if ip and ip != "0.0.0.0":
                # O IP é ocultado por padrão se não selecionado
                self.slot_labels[i].configure(text="AGUARDANDO")

        self.selecionar_slot(self.slot_selecionado)
        self.restaurar_grid()

        # Delay inicial: A interface carrega primeiro, as câmeras conectam depois
        self.after(2000, self._iniciar_sistema_conexoes)
        
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

        # Aplica automaticamente o último predefinição se existir
        if self.ultima_predefinicao and self.ultima_predefinicao in self.predefinicoes:
            self.after(500, lambda: self.aplicar_predefinicao(self.ultima_predefinicao))

        self.last_button_state = None
        self.loop_exibicao()

    def _iniciar_sistema_conexoes(self):
        """Inicia a thread de processamento e dispara as conexões iniciais."""
        print("SISTEMA: Iniciando conexões com as câmeras...")
        threading.Thread(target=self._processar_fila_conexoes_pendentes, daemon=True).start()
        self.alternar_todos_streams()

    def _processar_fila_conexoes_pendentes(self):
        while True:
            try:
                if not self.fila_pendente_conexoes.empty():
                    ip, canal = self.fila_pendente_conexoes.get()
                    self.ips_em_fila.discard(ip)

                    # Verifica se o IP ainda está no grid
                    if ip not in self.grid_cameras:
                        if self.camera_handlers.get(ip) == "CONECTANDO":
                            del self.camera_handlers[ip]
                        continue

                    # Se já tiver um handler rodando, não faz nada
                    handler = self.camera_handlers.get(ip)
                    if handler and handler != "CONECTANDO" and getattr(handler, 'rodando', False):
                        continue

                    # Se o estado for "CONECTANDO" mas não tivermos o objeto,
                    # significa que este item da fila é o que deve iniciar a thread.
                    # Mas se por algum motivo já houver uma thread, evitamos duplicar.
                    # (Embora ips_em_fila já ajude a evitar duplicados na fila)

                    # Inicia a conexão real
                    # print(f"LOG: Iniciando thread de conexão para {ip} (Queue size: {self.fila_pendente_conexoes.qsize()})")
                    threading.Thread(target=self._thread_conectar, args=(ip, canal), daemon=True).start()

                    # Pausa maior para evitar picos de CPU/Rede durante trocas de predefinicoes
                    time.sleep(0.05)
                else:
                    time.sleep(0.02)
            except Exception as e:
                print(f"Erro no processador de conexões: {e}")
                time.sleep(1)

    # --- LÓGICA DO TOGGLE DA SIDEBAR ---
    def toggle_sidebar(self):
        if self.sidebar_visible:
            self.sidebar.grid_forget()
            self.btn_toggle_sidebar.configure(text="▶")
            self.sidebar_visible = False
        else:
            self.sidebar.grid(row=0, column=0, sticky="nsew")
            self.btn_toggle_sidebar.configure(text="◀")
            self.sidebar_visible = True

    # --- LÓGICA PTZ ---
    def comando_ptz(self, direcao):
        ip = self.ip_selecionado
        if not ip or ip == "0.0.0.0": return

        if direcao != "STOP":
            if self.tecla_pressionada == direcao: return
            self.tecla_pressionada = direcao
        else:
            self.tecla_pressionada = None

        mapa = {
            "UP": {"pan": 0, "tilt": 100},
            "DOWN": {"pan": 0, "tilt": -100},
            "LEFT": {"pan": -100, "tilt": 0},
            "RIGHT": {"pan": 100, "tilt": 0},
            "STOP": {"pan": 0, "tilt": 0}
        }

        valores = mapa.get(direcao)
        xml_data = f"""<?xml version="1.0" encoding="UTF-8"?>
        <PTZData xmlns="http://www.isapi.org/ver20/XMLSchema">
            <pan>{valores['pan']}</pan>
            <tilt>{valores['tilt']}</tilt>
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

        self.main_frame.grid_configure(column=0, columnspan=3)

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
                    self.ultima_predefinicao = dados.get("last_predefinicao") or dados.get("last_preset")
                    self.slot_selecionado = dados.get("slot_selecionado", 0)
                    self.tamanho_preview = dados.get("tamanho_preview", "Pequeno")
                    if self.tamanho_preview == "Médio":
                        self.tamanho_preview = "Grande"
            except Exception as e: print(f"Erro ao carregar janela: {e}")

    def ao_fechar(self):
        # Para todas as gravações ativas
        for h in self.camera_handlers.values():
            if h != "CONECTANDO":
                h.parar_gravacao()

        try:
            if not self.em_tela_cheia:
                dados = {
                    "geometry": self.geometry(),
                    "active_tab": self.tabview.get(),
                    "last_predefinicao": self.ultima_predefinicao,
                    "slot_selecionado": self.slot_selecionado,
                    "tamanho_preview": self.tamanho_preview
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
                frm.grid_configure(row=0, column=0, rowspan=4, columnspan=5, padx=0, pady=0, sticky="nsew")
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
        self.press_data = {"index": index, "x": event.x_root, "y": event.y_root}

    def ao_soltar_slot(self, event, index):
        if not self.press_data: return
        source_idx = self.press_data.get("index")
        if self.slot_maximized is not None or self.em_tela_cheia:
            self.press_data = None
            return
        try:
            dist = ((event.x_root - self.press_data["x"])**2 + (event.y_root - self.press_data["y"])**2)**0.5
            target_idx = self.encontrar_slot_por_coords(event.x_root, event.y_root)

            # Se for apenas um clique (distância pequena) ou soltou fora
            if dist < 15 or target_idx is None:
                return

            # Se arrastou para o mesmo slot
            if target_idx == source_idx:
                return

            # Lógica de Troca (Swap)
            if 0 <= source_idx < 20 and 0 <= target_idx < 20:
                ip_src = self.grid_cameras[source_idx]
                ip_tgt = self.grid_cameras[target_idx]

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
            row, col = i // 5, i % 5
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

    def selecionar_slot(self, index):
        if not (0 <= index < 20): return

        # Desliga info de todos os handlers antes de trocar
        for ip_h, h in self.camera_handlers.items():
            if h != "CONECTANDO": h.set_exibir_info(False)

        for frm in self.slot_frames: frm.configure(border_color="black", border_width=2)

        ip_anterior = self.ip_selecionado
        self.slot_selecionado = index
        self.slot_frames[index].configure(border_color=self.ACCENT_RED, border_width=2)

        self.title(f"Monitoramento ABI - Espaço {index + 1} selecionado")

        ip_novo = self.grid_cameras[index]
        if ip_novo and ip_novo != "0.0.0.0":
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
            with open(self.arquivo_grid, "w", encoding='utf-8') as f:
                json.dump(self.grid_cameras, f, ensure_ascii=False, indent=4)
        except: pass

    def carregar_grid(self):
        grid = ["0.0.0.0"] * 20
        if os.path.exists(self.arquivo_grid):
            try:
                with open(self.arquivo_grid, "r", encoding='utf-8') as f:
                    dados = json.load(f)
                    if isinstance(dados, list):
                        for i in range(min(len(dados), 20)):
                            if dados[i]: grid[i] = dados[i]
            except: pass
        return grid

    def alternar_todos_streams(self):
        for ip in set(self.grid_cameras):
            if ip and ip != "0.0.0.0" and ip not in self.camera_handlers:
                self.iniciar_conexao_assincrona(ip, 102)

    def atualizar_botoes_controle(self):
        # Decide qual slot deve conter os botões
        idx = self.slot_maximized if self.slot_maximized is not None else self.slot_selecionado

        # Se não houver IP no slot ou slot inválido, esconde botões
        ip_atual = self.grid_cameras[idx] if (idx is not None and 0 <= idx < 20) else "0.0.0.0"

        handler = self.camera_handlers.get(ip_atual)
        is_rec = handler and handler != "CONECTANDO" and getattr(handler, 'gravando', False)
        is_max = self.slot_maximized is not None

        # Cache de estado para evitar flickering por chamadas redundantes
        current_state = (idx, ip_atual, is_rec, is_max)
        if current_state == getattr(self, 'last_button_state', None):
            return
        self.last_button_state = current_state

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
            self.abrir_modal_alerta("Sucesso", "Gravação finalizada e salva em Downloads.")

    def abrir_janela_configuracoes(self):
        modal = ctk.CTkToplevel(self)
        modal.title("Configurações")
        modal.geometry("400x300")
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

    def abrir_menu_opcoes(self):
        if not self.ip_selecionado: return

        nome = self.dados_cameras.get(self.ip_selecionado, "Câmera Sem Nome")
        ip = self.ip_selecionado

        # Cria a janela modal
        modal = ctk.CTkToplevel(self)
        modal.title(f"Opções - {ip}")
        modal.geometry("400x420")
        modal.resizable(False, False)
        modal.attributes("-topmost", True)

        # Tenta centralizar a janela em relação à aplicação
        try:
            self.update_idletasks()
            x = self.winfo_x() + (self.winfo_width() // 2) - 200
            y = self.winfo_y() + (self.winfo_height() // 2) - 210
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

        btn_capturar = ctk.CTkButton(modal, text="Capturar imagem", fg_color=self.GRAY_DARK, hover_color=self.TEXT_S,
                                    corner_radius=0, height=40,
                                    command=lambda: [modal.destroy(), self.capturar_imagem()])
        btn_capturar.pack(fill="x", padx=40, pady=5)

        handler = self.camera_handlers.get(ip)
        texto_gravacao = "Finalizar gravação" if (handler and handler != "CONECTANDO" and handler.gravando) else "Gravar vídeo"
        btn_toggle_rec = ctk.CTkButton(modal, text=texto_gravacao, fg_color=self.GRAY_DARK, hover_color=self.TEXT_S,
                                    corner_radius=0, height=40,
                                    command=lambda: [modal.destroy(), self.toggle_gravacao()])
        btn_toggle_rec.pack(fill="x", padx=40, pady=5)

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

    def abrir_modal_alerta(self, titulo, mensagem):
        modal = ctk.CTkToplevel(self)
        modal.title(titulo)
        modal.geometry("400x180")
        modal.resizable(False, False)
        modal.attributes("-topmost", True)

        try:
            self.update_idletasks()
            x = self.winfo_x() + (self.winfo_width() // 2) - 200
            y = self.winfo_y() + (self.winfo_height() // 2) - 90
            modal.geometry(f"+{x}+{y}")
        except: pass

        ctk.CTkLabel(modal, text=mensagem, font=("Roboto", 14, "bold"), text_color=self.TEXT_P, wraplength=320).pack(pady=(30, 20))

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
            lbl = ctk.CTkLabel(frm, text=f"Espaço {idx+1}", corner_radius=0)
            lbl.pack(expand=True, fill="both", padx=2, pady=2)

            # Re-bind dos eventos
            lbl.bind("<Button-1>", lambda e, x=idx: self.ao_pressionar_slot(e, x))
            lbl.bind("<ButtonRelease-1>", lambda e, x=idx: self.ao_soltar_slot(e, x))

            self.slot_labels[idx] = lbl
            self.slot_ctk_images[idx] = None
            self.cache_ui_text[idx] = None
            self.cache_ui_image[idx] = None
            return lbl
        except Exception as e:
            print(f"ERRO AO RECRIAR LABEL {idx}: {e}")
            return None

    def atribuir_ip_ao_slot(self, idx, ip, atualizar_ui=True, gerenciar_conexoes=True, salvar=True, forcado=False):
        if not (0 <= idx < 20): return

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
        
        # 1. Limpeza visual ultra-robusta
        # Só mostra IP se for o slot selecionado
        if not ip or ip == "0.0.0.0":
            txt = f"Espaço {idx+1}"
        else:
            txt = f"CONECTANDO...\n{ip}" if idx == self.slot_selecionado else "CONECTANDO..."

        try:
            # Tenta configurar o label existente
            self.slot_labels[idx].configure(image=self.img_vazia, text=txt)
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
            if ip_antigo and ip_antigo != "0.0.0.0" and ip_antigo != ip and ip_antigo not in self.grid_cameras:
                if ip_antigo in self.camera_handlers:
                    try: self.camera_handlers[ip_antigo].parar()
                    except: pass
                    del self.camera_handlers[ip_antigo]

            if ip != "0.0.0.0":
                if ip in self.cooldown_conexoes: del self.cooldown_conexoes[ip]
                canal_alvo = self.obter_canal_alvo(ip)
                self.iniciar_conexao_assincrona(ip, canal_alvo)

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
            if agora - ts < 10: return

        # Verifica se já está conectando ou rodando
        if ip in self.camera_handlers:
            handler = self.camera_handlers[ip]
            if handler == "CONECTANDO": return
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
        try:
            # Atualiza botões de controle periodicamente para garantir responsividade e sincronia
            self.atualizar_botoes_controle()

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
            scaling = self._get_window_scaling()
            indices_trabalho = [self.slot_maximized] if self.slot_maximized is not None else range(20)

            # Mapeia quais IPs estão sendo processados para compartilhar frames se possível (IP -> PIL Image)
            current_ips_pil = {}

            for i in range(20):
                ip = self.grid_cameras[i]

                # Verifica se houve timeout na gravação
                handler = self.camera_handlers.get(ip)
                if handler and handler != "CONECTANDO" and handler.timeout_atingido:
                    handler.timeout_atingido = False
                    nome = self.dados_cameras.get(ip, ip)
                    self.abrir_modal_alerta("Gravação Finalizada", f"A gravação da câmera {nome} foi finalizada automaticamente após 10 minutos.")

                # Caso o slot deva estar vazio ou não esteja no foco de atualização
                if not ip or ip == "0.0.0.0" or i not in indices_trabalho:
                    # Segurança: se o slot deveria estar vazio, garante texto e imagem vazia
                    if ip == "0.0.0.0":
                        try:
                            target_text = f"Espaço {i+1}"
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

                    if agora - ts < 10:
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
                    if pil_img is None and handler.novo_frame:
                        pil_img = handler.pegar_frame()
                        if pil_img:
                            current_ips_pil[ip] = pil_img

                    if pil_img:
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


        except Exception as e: print(f"Erro no loop de exibição: {e}")
        finally: self.after(50, self.loop_exibicao) # Ajustado para 50ms para equilibrar fluidez e CPU

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
                widget.bind("<Button-1>", lambda e, x=ip: self.selecionar_camera(x))
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

    def salvar_predefinicao_atual(self):
        def on_name_entered(nome):
            nome = nome.strip()
            if not nome:
                self.abrir_modal_alerta("Erro", "O nome da predefinição não pode ser vazio.")
                return

            if nome in self.predefinicoes:
                self.abrir_modal_confirmacao("Confirmar", f"A predefinição '{nome}' já existe. Deseja sobrescrevê-la?",
                                                lambda: self._salvar_predefinicao(nome))
            else:
                self._salvar_predefinicao(nome)

        self.abrir_modal_input("Salvar Predefinição", "Digite um nome para esta predefinição:", on_name_entered)

    def _salvar_predefinicao(self, nome):
        self.predefinicoes[nome] = list(self.grid_cameras)
        self.ultima_predefinicao = nome
        self.salvar_predefinicoes()
        self.atualizar_lista_predefinicoes_ui()

    def aplicar_predefinicao(self, nome):
        predefinicao = self.predefinicoes.get(nome)
        if not predefinicao: return

        # Limpa o cooldown para permitir reconexão imediata se for um predefinicao
        self.cooldown_conexoes.clear()

        # Gerencia cores na lista de predefinicoes
        if self.ultima_predefinicao:
            self.pintar_predefinicao(self.ultima_predefinicao, self.BG_SIDEBAR)
        self.ultima_predefinicao = nome
        self.pintar_predefinicao(nome, self.ACCENT_WINE)

        # print(f"Aplicando predefinição: {nome}")

        # 1. Fecha TODAS as conexões atuais para começar do zero (conforme solicitado pelo usuário)
        for ip_h in list(self.camera_handlers.keys()):
            h = self.camera_handlers[ip_h]
            if h != "CONECTANDO":
                try: h.parar()
                except: pass
            del self.camera_handlers[ip_h]

        # 2. Limpa filas e estados de conexão
        while not self.fila_pendente_conexoes.empty():
            try: self.fila_pendente_conexoes.get_nowait()
            except: pass
        self.ips_em_fila.clear()

        # 3. Atualiza os dados do grid primeiro (silenciosamente)
        novos_ips = ["0.0.0.0"] * 20
        ips_novos_set = set()
        for i in range(20):
            ip = predefinicao[i] if i < len(predefinicao) else "0.0.0.0"
            novos_ips[i] = ip
            if ip and ip != "0.0.0.0":
                ips_novos_set.add(ip)

            # Atualiza visualmente cada slot de forma segura
            self.atribuir_ip_ao_slot(i, ip, atualizar_ui=False, gerenciar_conexoes=False, salvar=False, forcado=True)

        self.salvar_grid()

        # 4. Inicia conexões para os novos IPs (o staggered cuidará do resto)
        for ip in ips_novos_set:
            self.iniciar_conexao_assincrona(ip, self.obter_canal_alvo(ip))

        # 5. Restaura layout se necessário e seleciona slot
        if self.slot_maximized is not None:
            self.restaurar_grid()

        self.selecionar_slot(self.slot_selecionado)
        self.update_idletasks()
        # print(f"Predefinição '{nome}' aplicada!")

    def sobrescrever_predefinicao(self, nome):
        self.abrir_modal_confirmacao("Confirmar", f"Deseja sobrescrever o predefinição '{nome}' com a configuração atual?",
                                     lambda: self._sobrescrever_predefinicao(nome))

    def _sobrescrever_predefinicao(self, nome):
        self.predefinicoes[nome] = list(self.grid_cameras)
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
            cor = self.ACCENT_WINE if nome == self.ultima_predefinicao else self.BG_SIDEBAR
            frm = ctk.CTkFrame(self.scroll_predefinicoes, fg_color=cor, border_width=0, border_color=self.GRAY_DARK)
            frm.pack(fill="x", pady=2, padx=2)

            # Bind no Frame para facilitar o clique
            frm.bind("<Button-1>", lambda e, n=nome: self.aplicar_predefinicao(n))
            frm.configure(cursor="hand2")

            # Botões de Controle (Ordem: X, ✎, 💾 - pack no lado direito)
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

            # Label de Nome (Expandível)
            wrap_val = max(100, largura_sidebar - 160)
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
    app = CentralMonitoramento()
    app.mainloop()
