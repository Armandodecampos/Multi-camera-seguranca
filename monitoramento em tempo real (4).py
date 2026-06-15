import os
import re
import csv
import json
import base64
from datetime import datetime
import tkinter as tk  # Mantido para cálculo inicial se necessário
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Configurações do sistema
URL_LOGIN = "http://192.168.7.9:8098/bioLogin.do"
USUARIO = "armando.campos"
SENHA = "armandocampos.1"

# Diretórios para salvar os relatórios e imagens extraídas
def obter_desktop():
    """Retorna o caminho da área de trabalho de forma multiplataforma."""
    home = os.path.expanduser("~")

    # Lista de caminhos possíveis para o Desktop, incluindo OneDrive
    caminhos = [
        os.path.join(home, "Desktop"),
        os.path.join(home, "Área de Trabalho"),
        os.path.join(home, "OneDrive", "Desktop"),
        os.path.join(home, "OneDrive", "Área de Trabalho"),
        os.path.join(home, "OneDrive - Personal", "Desktop"),
        os.path.join(home, "OneDrive - Personal", "Área de Trabalho"),
    ]

    for caminho in caminhos:
        if os.path.exists(caminho):
            return caminho

    return home

DIRETORIO_SAIDA = os.path.join(obter_desktop(), "Relatório de Acessos")
DIRETORIO_FOTOS = os.path.join(DIRETORIO_SAIDA, "fotos")
ARQUIVO_CSV = os.path.join(DIRETORIO_SAIDA, "historico_acessos.csv")
ARQUIVO_HTML = os.path.join(DIRETORIO_SAIDA, "relatorio_visual.html")

# Cria as pastas caso não existam
os.makedirs(DIRETORIO_FOTOS, exist_ok=True)

# Global para sincronizar a data do sistema de monitoramento
DATA_SISTEMA_ATUAL = datetime.now().strftime("%Y-%m-%d")


def normalizar_data(data_str):
    """Garante que a data esteja no formato YYYY-MM-DD HH:MM:SS."""
    global DATA_SISTEMA_ATUAL
    if not data_str:
        return f"{DATA_SISTEMA_ATUAL} {datetime.now().strftime('%H:%M:%S')}"

    # Se já estiver no formato correto, retorna e atualiza a data global
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", data_str):
        DATA_SISTEMA_ATUAL = data_str.split(' ')[0]
        return data_str

    # Se for apenas HH:MM:SS, adiciona a data detectada do sistema
    if re.match(r"^\d{2}:\d{2}:\d{2}$", data_str):
        return f"{DATA_SISTEMA_ATUAL} {data_str}"

    return data_str


def obter_resolucao_tela():
    """Detecta dinamicamente a largura e altura da tela física do usuário."""
    try:
        root = tk.Tk()
        largura = root.winfo_screenwidth()
        altura = root.winfo_screenheight()
        root.destroy()
        return largura, altura
    except Exception as e:
        print(f"[!] Não foi possível detectar a resolução automaticamente ({e}). Usando fallback Full HD.")
        return 1920, 1080


def inicializar_arquivos():
    """Garante que o arquivo CSV possua cabeçalho adequado."""
    if not os.path.exists(ARQUIVO_CSV):
        with open(ARQUIVO_CSV, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Data_Registro", "ID", "Nome", "Evento", "Dispositivo", "Leitor", "Data_Evento", "Caminho_Foto"])


def salvar_foto(base64_data, id_usuario, data_evento):
    """Decodifica a string Base64 da imagem e salva em arquivo local."""
    if not base64_data:
        return ""
    
    # Normaliza a string base64 se necessário
    if "," in base64_data:
        base64_data = base64_data.split(",")[1]
        
    try:
        # Sanitiza a data para usar no nome do arquivo de imagem
        data_safe = re.sub(r'[^0-9]', '_', data_evento)
        nome_arquivo = f"{id_usuario}_{data_safe}.jpg"
        caminho_completo = os.path.join(DIRETORIO_FOTOS, nome_arquivo)
        
        # Converte e grava a imagem em disco
        with open(caminho_completo, "wb") as f:
            f.write(base64.b64decode(base64_data))
        return caminho_completo
    except Exception as e:
        print(f"[-] Erro ao salvar a imagem do usuário {id_usuario}: {e}")
        return ""


def registrar_evento(id_usuario, nome, evento, dispositivo, leitor, data_evento, base64_foto, page):
    """Registra as informações capturadas no CSV, no HTML e injeta na lista unificada."""
    caminho_foto_local = ""
    if base64_foto:
        caminho_foto_local = salvar_foto(base64_foto, id_usuario, data_evento)
    
    data_registro = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Lógica de atualização/mesclagem inteligente no arquivo CSV para evitar duplicados
    registros_existentes = []
    atualizado = False
    
    if os.path.exists(ARQUIVO_CSV):
        with open(ARQUIVO_CSV, mode='r', encoding='utf-8') as f:
            reader = csv.reader(f)
            cabecalho = next(reader, None)
            # Verifica se o cabeçalho já tem a coluna 'Leitor' (migração automática)
            tem_coluna_leitor = "Leitor" in cabecalho if cabecalho else False

            for row in reader:
                # Ajusta linha se for de versão antiga sem a coluna 'Leitor'
                if not tem_coluna_leitor and len(row) == 7:
                    row.insert(5, "") # Insere vazio na posição do Leitor

                if len(row) >= 8:
                    # Se encontramos o mesmo registro (ID e Data do Evento idênticos)
                    if row[1] == id_usuario and row[6] == data_evento:
                        # Se o registro antigo não tinha foto e agora temos, mesclamos a foto
                        if not row[7] and caminho_foto_local:
                            row[7] = caminho_foto_local
                        # Atualiza leitor se estiver vazio
                        if not row[5] and leitor:
                            row[5] = leitor
                        # Atualiza dispositivo se estiver vazio ou for "Geral"
                        if (not row[4] or row[4] == "Geral") and dispositivo and dispositivo != "Geral":
                            row[4] = dispositivo
                        atualizado = True
                    registros_existentes.append(row)
                    
    if not atualizado:
        registros_existentes.append([data_registro, id_usuario, nome, evento, dispositivo, leitor, data_evento, caminho_foto_local])
        
    # Reescreve o CSV de forma limpa
    with open(ARQUIVO_CSV, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Data_Registro", "ID", "Nome", "Evento", "Dispositivo", "Leitor", "Data_Evento", "Caminho_Foto"])
        writer.writerows(registros_existentes)
        
    print(f"[+] REGISTRO SINCRO: {id_usuario} - {nome} | Leitor: {leitor} | Evento: {evento} | Data: {data_evento}")
    
    # Atualiza Relatório HTML em disco
    atualizar_relatorio_html()
    
    # Injeta na lista unificada
    injetar_evento_unificado(page, id_usuario, nome, evento, dispositivo, leitor, data_evento, base64_foto)


def atualizar_relatorio_html():
    """Gera ou atualiza o dashboard estático externo em HTML para visualização dos registros."""
    registros = []
    if os.path.exists(ARQUIVO_CSV):
        with open(ARQUIVO_CSV, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            registros = list(reader)
            registros.reverse()  # Mais recentes no topo

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
        if caminho_foto:
            nome_arquivo = os.path.basename(caminho_foto)
            img_tag_src = f"fotos/{nome_arquivo}"
        else:
            img_tag_src = "https://via.placeholder.com/150"
        
        dispositivo_txt = r.get('Dispositivo', '')
        evento_txt = r.get('Evento', '')
        
        disp_html = f'<p class="text-sm text-gray-300 font-medium mt-0.5">🖥️ {dispositivo_txt}</p>' if dispositivo_txt else ''
        leitor_txt = r.get('Leitor', '')
        leitor_html = f'<p class="text-xs text-teal-400 font-semibold mt-0.5">Leitor: {leitor_txt}</p>' if leitor_txt else ''
        evento_html = f'<p class="text-sm text-yellow-400 font-medium mt-1">{evento_txt}</p>' if evento_txt else ''
        
        html_content += f"""
            <div class="bg-gray-800 rounded-xl overflow-hidden shadow-lg border border-gray-700 transition hover:scale-[1.02] duration-300">
                <div class="p-4 flex justify-center bg-gray-950">
                    <img class="h-44 object-contain rounded-lg border border-gray-700" src="{img_tag_src}" alt="Foto de {r['Nome']}" onerror="this.src='https://via.placeholder.com/150'">
                </div>
                <div class="p-4">
                    <span class="inline-block px-2.5 py-1 text-xs font-semibold rounded-full bg-teal-900/50 text-teal-300 mb-2 border border-teal-800">
                        ID: {r['ID']}
                    </span>
                    <h3 class="text-lg font-bold text-white truncate">{r['Nome']}</h3>
                    {evento_html}
                    {leitor_html}
                    {disp_html}
                    
                    <div class="mt-4 pt-3 border-t border-gray-700 text-xs text-gray-400 flex flex-col gap-1">
                        <div><strong class="text-gray-300">Evento em:</strong> {r['Data_Evento']}</div>
                        <div><strong class="text-gray-300">Coletado em:</strong> {r['Data_Registro']}</div>
                    </div>
                </div>
            </div>
        """
        
    html_content += """
        </div>
    </div>
</body>
</html>
    """
    
    with open(ARQUIVO_HTML, "w", encoding="utf-8") as f:
        f.write(html_content)


def criar_estrutura_barra_lateral(page):
    """Injeta o painel lateral de forma sobreposta no canto direito com dois containers verticais."""
    try:
        foi_injetada = page.evaluate("""() => {
            if (document.getElementById('painel-lateral-registro')) return false;

            // 1. Redimensionar de forma limpa o body original sem alterar sua estrutura
            const style = document.createElement('style');
            style.id = 'estilos-barra-lateral-segura';
            style.innerHTML = `
                html, body {
                    width: auto !important;
                    margin-right: 400px !important;
                    box-sizing: border-box !important;
                    transition: margin-right 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
                }
                
                body {
                    overflow-x: auto !important;
                }

                body.somente-registros {
                    margin-right: 0px !important;
                }

                body.somente-registros > :not(#painel-lateral-registro):not(style):not(script) {
                    display: none !important;
                }

                body.somente-registros #painel-lateral-registro {
                    width: 100% !important;
                    border-left: none !important;
                }
            `;
            document.head.appendChild(style);

            // 2. Cria a barra lateral fixada absolutamente no canto direito da tela
            const sidebar = document.createElement('div');
            sidebar.id = 'painel-lateral-registro';
            sidebar.style.position = 'fixed';
            sidebar.style.top = '0';
            sidebar.style.right = '0';
            sidebar.style.width = '400px';
            sidebar.style.height = '100vh';
            sidebar.style.backgroundColor = '#111827';
            sidebar.style.borderLeft = '4px solid #14b8a6';
            sidebar.style.color = '#f3f4f6';
            sidebar.style.fontFamily = 'Segoe UI, Tahoma, Geneva, Verdana, sans-serif';
            sidebar.style.display = 'flex';
            sidebar.style.flexDirection = 'column';
            sidebar.style.boxSizing = 'border-box';
            sidebar.style.zIndex = '2147483647';
            sidebar.style.transition = 'width 0.25s cubic-bezier(0.4, 0, 0.2, 1)';

            // Layout com container único para registros unificados
            sidebar.innerHTML = `
                <!-- CABEÇALHO DO PAINEL GERAL -->
                <div style="padding: 12px 16px; border-bottom: 1px solid #374151; background-color: #1f2937; display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-shrink: 0;">
                    <div style="min-width: 0; flex: 1;">
                        <h2 style="margin: 0; color: #14b8a6; font-size: 15px; font-weight: bold; letter-spacing: 0.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Painel de Controle</h2>
                        <p style="margin: 1px 0 0 0; color: #9ca3af; font-size: 10px;">Monitoramento Inteligente</p>
                    </div>
                    <button id="btn-toggle-exibicao" style="background-color: #14b8a6; color: #111827; border: none; padding: 5px 10px; border-radius: 4px; font-size: 10px; font-weight: bold; cursor: pointer; transition: all 0.2s ease; outline: none; white-space: nowrap; flex-shrink: 0;">
                        Focar Registros
                    </button>
                </div>

                <!-- CONTAINER ÚNICO: HISTÓRICO DE ACESSOS UNIFICADO -->
                <div style="flex: 1; min-height: 0; display: flex; flex-direction: column; background-color: #0b0f19;">
                    <div style="padding: 8px 16px; background-color: #1a202c; border-bottom: 1px solid #2d3748; display: flex; align-items: center; justify-content: space-between; flex-shrink: 0;">
                        <span style="font-size: 12px; font-weight: bold; color: #2dd4bf; text-transform: uppercase; letter-spacing: 0.5px;">Eventos em Tempo Real</span>
                        <span style="font-size: 10px; color: #9ca3af;" id="contador-registros">0 total</span>
                    </div>
                    <div id="lista-eventos-unificada" style="padding: 12px; overflow-y: auto; flex: 1; display: flex; flex-direction: column; gap: 10px; background-color: #111827;">
                        <div id="mensagem-vazia" style="text-align: center; color: #6b7280; padding: 30px 10px; font-size: 12px; font-style: italic;">
                            Aguardando novos eventos biométricos...
                        </div>
                    </div>
                </div>
            `;

            // Adiciona a barra lateral diretamente no body original
            document.body.appendChild(sidebar);

            // 3. Listener do botão para alternar os modos de visualização
            const btnToggle = sidebar.querySelector('#btn-toggle-exibicao');
            btnToggle.addEventListener('click', () => {
                const modoFocoAtivo = document.body.classList.toggle('somente-registros');
                if (modoFocoAtivo) {
                    btnToggle.textContent = 'Mostrar Site';
                    btnToggle.style.backgroundColor = '#f59e0b';
                    btnToggle.style.color = '#ffffff';
                } else {
                    btnToggle.textContent = 'Focar Registros';
                    btnToggle.style.backgroundColor = '#14b8a6';
                    btnToggle.style.color = '#111827';
                }
            });

            return true;
        }""")
        
        if foi_injetada:
            print("[+] Divisão de tela dual-container aplicada com sucesso.")
            
    except Exception as e:
        print(f"[!] Erro ao injetar estrutura da barra lateral segura: {e}")


def injetar_evento_unificado(page, id_usuario, nome, evento, dispositivo, leitor, data_evento, base64_foto=""):
    """Injeta ou atualiza um registro na lista unificada, priorizando fotos."""
    try:
        src_imagem = base64_foto if base64_foto else ""
        
        js_unificado = """
        (dados) => {
            const lista = document.getElementById('lista-eventos-unificada');
            if (!lista) return;

            const idRegistro = `reg-${dados.id_usuario}-${dados.data_evento.replace(/[^0-9]/g, '_')}`;
            let elementoExistente = document.getElementById(idRegistro);
            
            // Se já existe, tenta preservar dados mais específicos (Leitor e Dispositivo) antes de atualizar
            if (elementoExistente) {
                const leitorEl = elementoExistente.querySelector('.leitor-text');
                const dispEl = elementoExistente.querySelector('.disp-text');

                const leitorAtual = leitorEl ? (leitorEl.dataset.leitor || leitorEl.textContent.replace('📍', '').trim()) : "";
                const dispAtual = dispEl ? (dispEl.dataset.dispositivo || dispEl.textContent.replace('🖥️', '').trim()) : "";

                // Preserva o leitor se o atual for preenchido e o novo for vazio
                if (leitorAtual && !dados.leitor) {
                    dados.leitor = leitorAtual;
                }
                // Preserva o dispositivo se o atual for específico e o novo for "Geral" ou vazio
                if (dispAtual && dispAtual !== "Geral" && (!dados.dispositivo || dados.dispositivo === "Geral")) {
                    dados.dispositivo = dispAtual;
                }

                // Se já tem foto, apenas atualiza os textos se necessário e encerra
                if (elementoExistente.dataset.temFoto === "true") {
                    if (leitorEl && (!leitorEl.dataset.leitor || !leitorEl.dataset.leitor.trim()) && dados.leitor) {
                        leitorEl.dataset.leitor = dados.leitor;
                        leitorEl.textContent = `📍 ${dados.leitor}`;
                    }
                    if (dispEl && (!dispEl.dataset.dispositivo || dispEl.dataset.dispositivo === "Geral") && dados.dispositivo && dados.dispositivo !== "Geral") {
                        dispEl.dataset.dispositivo = dados.dispositivo;
                        dispEl.textContent = `🖥️ ${dados.dispositivo}`;
                    }
                    return;
                }
            }

            const msgVazia = document.getElementById('mensagem-vazia');
            if (msgVazia) msgVazia.remove();

            const horaSimplificada = dados.data_evento.split(' ')[1] || dados.data_evento;
            const divEventoHtml = dados.evento ? `<div style="font-size: 10px; color: #facc15; font-weight: 500; margin-top: 1px;">${dados.evento}</div>` : '';
            const spanDisp = `<span class="disp-text" data-dispositivo="${dados.dispositivo || ''}" style="color: #cbd5e1; font-size: 10px; font-weight: 500;">${dados.dispositivo ? '🖥️ ' + dados.dispositivo : ''}</span>`;
            const spanLeitor = `<span class="leitor-text" data-leitor="${dados.leitor || ''}" style="color: #2dd4bf; font-weight: bold; font-size: 10px;">${dados.leitor ? '📍 ' + dados.leitor : ''}</span>`;

            const htmlConteudo = `
                <div style="display: flex; gap: 12px; align-items: center;">
                    ${dados.src_imagem ?
                        `<img src="${dados.src_imagem}" style="width: 50px; height: 50px; border-radius: 4px; object-fit: cover; border: 1px solid #4b5563; background-color: #030712;" onerror="this.src='/images/userImage.gif'">` :
                        `<div style="width: 50px; height: 50px; border-radius: 4px; background-color: #1f2937; border: 1px solid #374151; display: flex; align-items: center; justify-content: center; color: #4b5563; font-size: 20px;">👤</div>`
                    }
                    <div style="flex: 1; min-width: 0;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
                            <div style="font-size: 10px; background-color: rgba(20, 184, 166, 0.15); color: #2dd4bf; display: inline-block; padding: 1px 4px; border-radius: 3px; font-weight: bold;">
                                ID: ${dados.id_usuario}
                            </div>
                            <span style="color: #f59e0b; font-weight: bold; font-size: 10px;">🕒 ${horaSimplificada}</span>
                        </div>
                        <div style="font-size: 12px; font-weight: bold; color: #ffffff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${dados.nome}">
                            ${dados.nome}
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: flex-end; margin-top: 3px;">
                            <div style="display: flex; flex-direction: column;">
                                ${spanLeitor}
                                ${divEventoHtml}
                            </div>
                            ${spanDisp}
                        </div>
                    </div>
                </div>
            `;

            if (elementoExistente) {
                // Atualiza o elemento existente (Upgrade para foto)
                elementoExistente.innerHTML = htmlConteudo;
                elementoExistente.dataset.temFoto = dados.src_imagem ? "true" : "false";
                elementoExistente.style.borderLeft = dados.src_imagem ? '4px solid #14b8a6' : '4px solid #f59e0b';
            } else {
                // Cria novo elemento
                const novoReg = document.createElement('div');
                novoReg.id = idRegistro;
                novoReg.dataset.temFoto = dados.src_imagem ? "true" : "false";
                novoReg.style.backgroundColor = '#1f2937';
                novoReg.style.border = '1px solid #374151';
                novoReg.style.borderLeft = dados.src_imagem ? '4px solid #14b8a6' : '4px solid #f59e0b';
                novoReg.style.borderRadius = '6px';
                novoReg.style.padding = '10px';
                novoReg.style.boxShadow = '0 2px 4px rgba(0,0,0,0.2)';
                novoReg.style.animation = 'fadeIn 0.4s ease';
                novoReg.innerHTML = htmlConteudo;

                lista.insertBefore(novoReg, lista.firstChild);
            }

            while (lista.childNodes.length > 50) {
                lista.removeChild(lista.lastChild);
            }

            const contReg = document.getElementById('contador-registros');
            if (contReg) {
                const total = lista.querySelectorAll('div[id^="reg-"]').length;
                contReg.textContent = `${total} total`;
            }
        }
        """
        page.evaluate(js_unificado, {
            "id_usuario": id_usuario,
            "nome": nome,
            "evento": evento,
            "dispositivo": dispositivo,
            "leitor": leitor,
            "data_evento": data_evento,
            "src_imagem": src_imagem
        })
    except Exception as e:
        print(f"[-] Erro ao atualizar lista unificada: {e}")


def extrair_dados_notificacao(html_interno):
    """Faz o parse do HTML interno do pop-up para extrair as fotos e dados de acesso."""
    soup = BeautifulSoup(html_interno, 'html.parser')
    
    img_tag = soup.find('img')
    base64_foto = ""
    if img_tag and img_tag.get('src'):
        src = img_tag.get('src')
        if "base64," in src:
            base64_foto = src
            
    p_tags = soup.find_all('p')
    if len(p_tags) >= 3:
        identificacao = p_tags[0].get_text(strip=True)
        evento = p_tags[1].get_text(strip=True)
        data_evento = p_tags[2].get_text(strip=True)
        
        match = re.match(r"(\d+)\((.+)\)", identificacao)
        if match:
            id_usuario = match.group(1)
            nome_usuario = match.group(2)
        else:
            id_usuario = "Desconhecido"
            nome_usuario = identificacao
            
        dispositivo = ""
        if len(p_tags) >= 4:
            dispositivo = p_tags[3].get_text(strip=True)
            
        leitor = ""
        if len(p_tags) >= 5:
            leitor = p_tags[4].get_text(strip=True)

        return id_usuario, nome_usuario, evento, dispositivo, data_evento, base64_foto, leitor
        
    return None


def extrair_linhas_tabela_real(frame):
    """Varre e extrai os registros em tempo real diretamente da tabela de monitoramento visível no frame."""
    try:
        # Script JS robusto rodando dentro do iframe que detecta o grid de eventos
        js_tabela = """
        () => {
            const resultados = [];
            const trs = document.querySelectorAll('tr');
            
            for (const tr of trs) {
                const tds = tr.querySelectorAll('td');
                if (tds.length >= 8) {
                    const horario = tds[0].innerText ? tds[0].innerText.trim() : tds[0].textContent.trim();
                    // Garante que o primeiro campo é uma data válida (Formato YYYY-MM-DD HH:MM:SS)
                    if (/^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}$/.test(horario)) {
                        const dispositivo = tds[2].innerText ? tds[2].innerText.trim() : tds[2].textContent.trim();
                        const evento = tds[4].innerText ? tds[4].innerText.trim() : tds[4].textContent.trim();
                        const pessoa = tds[6].innerText ? tds[6].innerText.trim() : tds[6].textContent.trim();
                        const leitor = tds[7].innerText ? tds[7].innerText.trim() : tds[7].textContent.trim();
                        
                        resultados.push({
                            horario: horario,
                            dispositivo: dispositivo,
                            evento: evento,
                            pessoa: pessoa,
                            leitor: leitor
                        });
                    }
                }
            }
            return resultados;
        }
        """
        return frame.evaluate(js_tabela)
    except Exception:
        return []


def executar_monitoramento():
    # Garante que a pasta de saída exista
    os.makedirs(DIRETORIO_FOTOS, exist_ok=True)
    inicializar_arquivos()
    
    largura, altura = obter_resolucao_tela()
    print(f"[*] Resolução física inicial da tela detectada: {largura}x{altura}")
    
    with sync_playwright() as p:
        browser = None
        
        argumentos_navegador = [
            f"--window-size={largura},{altura}",
            "--window-position=0,0",
            "--start-maximized"
        ]
        
        try:
            print("[*] Tentando iniciar com o Chromium nativo do Playwright...")
            browser = p.chromium.launch(headless=False, args=argumentos_navegador)
        except Exception:
            try:
                browser = p.chromium.launch(headless=False, channel="chrome", args=argumentos_navegador)
            except Exception:
                try:
                    browser = p.chromium.launch(headless=False, channel="msedge", args=argumentos_navegador)
                except Exception as e3:
                    print("\n[-] ERRO CRÍTICO: Não foi possível carregar nenhum navegador compatível.")
                    raise e3

        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        
        print(f"[*] Acessando {URL_LOGIN}...")
        page.goto(URL_LOGIN)
        
        page.wait_for_selector("#username", timeout=15000)
        page.wait_for_selector("#password", timeout=15000)
        
        print("[*] Efetuando o login automático...")
        page.fill("#username", USUARIO)
        page.fill("#password", SENHA)
        
        page.wait_for_timeout(500)
        
        btn_login = page.locator("input[type='submit'], button, a.login-btn")
        btn_login.first.click()
        
        print("[*] Aguardando redirecionamento para a página inicial...")
        page.wait_for_url(re.compile(r"(main|dashboard)\.do"), timeout=30000)
        print("[+] Login efetuado com sucesso!")
        
        page.wait_for_timeout(5000)
        
        # Injeta painel de controle
        criar_estrutura_barra_lateral(page)
        
        print("[+] Monitoramento ativo e responsivo.")
        
        # Caches locais para evitar repetição/duplicidade
        ultimos_eventos_processados = set()
        eventos_com_foto_processados = set()
        
        while True:
            try:
                criar_estrutura_barra_lateral(page)
                
                fontes = [page] + page.frames
                
                for fonte in fontes:
                    try:
                        if "192.168.7.9" not in fonte.url and "about:blank" not in fonte.url:
                            continue
                        
                        # --- ORIGEM 1: VARREDURA DIRETA NA TABELA REAL (Eventos sem foto, com 100% de garantia) ---
                        linhas_tabela = extrair_linhas_tabela_real(fonte)
                        for linha in linhas_tabela:
                            pessoa_texto = linha["pessoa"]
                            
                            # Parse da pessoa para isolar o ID e Nome
                            match = re.match(r"(\d+)\((.+)\)", pessoa_texto)
                            if match:
                                id_usuario = match.group(1)
                                nome_usuario = match.group(2)
                            else:
                                id_usuario = "Desconhecido"
                                nome_usuario = pessoa_texto
                                
                            evento = linha["evento"]
                            dispositivo = linha["dispositivo"]
                            leitor = linha["leitor"]
                            data_evento = normalizar_data(linha["horario"])
                            
                            # Sanitização de textos ("Verificação de abertura normal" e "Geral")
                            if "Verificação de abertura normal" in evento:
                                evento = evento.replace("Verificação de abertura normal", "").strip()
                            if dispositivo == "Geral":
                                dispositivo = ""
                                
                            chave_evento = f"{id_usuario}_{data_evento}"
                            
                            if chave_evento not in ultimos_eventos_processados:
                                # Registra e envia à lista unificada sem foto
                                registrar_evento(id_usuario, nome_usuario, evento, dispositivo, leitor, data_evento, "", page)
                                ultimos_eventos_processados.add(chave_evento)
                                
                        # --- ORIGEM 2: CAPTURA DO POP-UP (Para anexar as fotos no painel superior) ---
                        elementos_alvo = fonte.locator("div[style*='text-align: center']").all()
                        for el in elementos_alvo:
                            html_interno = el.inner_html()
                            
                            if "<p>" in html_interno and "</p>" in html_interno:
                                dados = extrair_dados_notificacao(html_interno)
                                if dados:
                                    id_usuario, nome_usuario, evento, dispositivo, data_evento_raw, base64_foto, leitor_popup = dados
                                    data_evento = normalizar_data(data_evento_raw)
                                    
                                    if "Verificação de abertura normal" in evento:
                                        evento = evento.replace("Verificação de abertura normal", "").strip()
                                    if dispositivo == "Geral":
                                        dispositivo = ""
                                        
                                    chave_evento = f"{id_usuario}_{data_evento}"
                                    
                                    # Se detectamos a foto e o popup do usuário
                                    if base64_foto:
                                        if chave_evento not in eventos_com_foto_processados:
                                            # Registra/Sincroniza preenchendo a foto no CSV e atualizando a lista unificada
                                            registrar_evento(id_usuario, nome_usuario, evento, dispositivo, leitor_popup, data_evento, base64_foto, page)

                                            ultimos_eventos_processados.add(chave_evento)
                                            eventos_com_foto_processados.add(chave_evento)
                                        
                    except Exception:
                        continue
                
                # Mantém cache sob controle
                if len(ultimos_eventos_processados) > 1000:
                    ultimos_eventos_processados.clear()
                    eventos_com_foto_processados.clear()
                    
                # Sincronização ágil de 250ms
                page.wait_for_timeout(250)
                
            except Exception as loop_error:
                print(f"[!] Aviso no loop de monitoramento: {loop_error}")
                page.wait_for_timeout(2000)


if __name__ == "__main__":
    try:
        executar_monitoramento()
    except KeyboardInterrupt:
        print("\n[+] Monitoramento interrompido pelo usuário.")
