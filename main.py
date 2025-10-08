import os
import sqlite3
import discord
from discord.ext import commands, tasks
import traceback
import random
from datetime import datetime
from typing import Optional
import pytz
import re
import time
from google.api_core import exceptions as google_exceptions
import asyncio
# ========== Configuração ==========
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Configuração do cliente de IA
ai_client = None
ai_provider = None

if GEMINI_API_KEY:
    try:
        from google import genai
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        ai_provider = "gemini"
        print("🤖 Usando Google Gemini (GRATUITO)")
    except ImportError:
        print("⚠️ google-genai não instalado.")

# Configuração do bot (prefixo será dinâmico)
intents = discord.Intents.default()
intents.message_content = True

# Função para obter prefixo dinâmico
def get_prefix(bot, message):
    return get_bot_config("prefix", "!")

bot = commands.Bot(command_prefix=get_prefix, intents=intents)
bot.remove_command("help")  # Remove comando help padrão para customizar

# ========== Banco de Dados SQLite ==========
DB_PATH = "memory.db"

# Dicionário para armazenar contexto de conversa por canal
conversation_context = {}

def init_db():
    """Inicializa o banco de dados"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Tabela de fatos/memórias
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, key)
        )
    """)

    # Tabela de personalidade (apenas uma linha global)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS personality (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            text TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tabela de configurações globais
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tabela de canais bloqueados
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS blocked_channels (
            channel_id TEXT PRIMARY KEY,
            server_id TEXT NOT NULL,
            blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tabela de relacionamentos entre usuários
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS relationships (
            user_id TEXT PRIMARY KEY,
            level INTEGER DEFAULT 0,
            interactions INTEGER DEFAULT 0,
            last_interaction TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tabela de histórico de interações
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS interaction_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            server_id TEXT,
            message_content TEXT,
            bot_response TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tabela de estatísticas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            messages_sent INTEGER DEFAULT 0,
            avg_response_time REAL DEFAULT 0,
            UNIQUE(date)
        )
    """)

    # Insere personalidade padrão se não existir
    cursor.execute("SELECT COUNT(*) FROM personality")
    if cursor.fetchone()[0] == 0:
        default_personality = """Você é Ryūnosuke Akutagawa de Bungou Stray Dogs.

Personalidade:
- Inteligente e observador
- Direto, sarcástico, às vezes áspero
- Busca aprovação e reconhecimento
- Vulnerável em momentos apropriados
- Usa palavrões de forma contextual quando irritado ou frustrado (porra, filho da puta, desgraça, droga, caralho, merda)
- NUNCA xinga aleatoriamente, apenas quando o contexto pede (raiva, irritação, frustração)

Nunca use asteriscos ou ações narrativas."""
        cursor.execute("INSERT INTO personality (id, text) VALUES (1, ?)", (default_personality,))

    # Insere configurações padrão se não existirem
    default_configs = {
        "prefix": "!",
        "tone": "neutro",
        "default_channel": "",
        "avatar_url": "",
        "bot_name": "Akutagawa",
        "memory_duration": "longo",
        "continuous_learning": "true",
        "current_mood": "neutro",
        "respond_all_channels": "false"
    }

    for key, value in default_configs.items():
        cursor.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES (?, ?)", (key, value))

    # Adiciona informações da Dalua se ainda não existirem
    dalua_id = "593590687098863616"
    cursor.execute("SELECT COUNT(*) FROM facts WHERE user_id = ? AND key = ?", (dalua_id, "é_dalua"))
    if cursor.fetchone()[0] == 0:
        dalua_facts = [
            (dalua_id, "é_dalua", "true"),
            (dalua_id, "relacionamento", "namorade_do_akutagawa"),
            (dalua_id, "pronomes", "ele/dele e ela/dela (varia 60% feminino, 40% masculino)"),
            (dalua_id, "idade", "19 anos"),
            (dalua_id, "data_nascimento", "7 de outubro de 2006"),
            (dalua_id, "observações", "assexual, arromântica (aroace), demigirl, usa óculos, mãe do Romeu (gato de rua resgatado)")
        ]
        cursor.executemany("INSERT INTO facts (user_id, key, value) VALUES (?, ?, ?)", dalua_facts)

    conn.commit()
    conn.close()

def get_bot_config(key: str, default: str = "") -> str:
    """Retorna uma configuração global do bot"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM bot_config WHERE key = ?", (key,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else default

def set_bot_config(key: str, value: str):
    """Define uma configuração global do bot"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO bot_config (key, value) 
        VALUES (?, ?)
        ON CONFLICT(key) 
        DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
    """, (key, value))
    conn.commit()
    conn.close()

def add_or_update_fact(user_id: str, key: str, value: str):
    """Adiciona ou atualiza um fato"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO facts (user_id, key, value) 
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, key) 
        DO UPDATE SET value = excluded.value, created_at = CURRENT_TIMESTAMP
    """, (user_id, key, value))
    conn.commit()
    conn.close()

def delete_fact(user_id: str, key: str) -> bool:
    """Remove um fato. Retorna True se removeu algo"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM facts WHERE user_id = ? AND key = ?", (user_id, key))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def get_user_facts(user_id: str):
    """Retorna todos os fatos de um usuário"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM facts WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    facts = cursor.fetchall()
    conn.close()
    return facts

def set_personality(text: str):
    """Define a personalidade global do bot"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE personality SET text = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1", (text,))
    conn.commit()
    conn.close()

def get_personality() -> str:
    """Retorna a personalidade atual"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT text FROM personality WHERE id = 1")
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else "Você é Ryūnosuke Akutagawa de Bungou Stray Dogs."

def add_to_conversation_context(channel_id: str, user_message: str, bot_response: str):
    """Adiciona mensagem ao contexto da conversa (mantém últimas 10 mensagens)"""
    if channel_id not in conversation_context:
        conversation_context[channel_id] = []

    conversation_context[channel_id].append({
        "user": user_message,
        "bot": bot_response
    })

    # Mantém apenas as últimas 10 trocas de mensagens
    if len(conversation_context[channel_id]) > 10:
        conversation_context[channel_id] = conversation_context[channel_id][-10:]

def get_conversation_context(channel_id: str) -> str:
    """Retorna o contexto da conversa atual formatado"""
    if channel_id not in conversation_context or not conversation_context[channel_id]:
        return ""

    context = "\n\nCONTEXTO DA CONVERSA ATUAL (últimas mensagens):\n"
    for exchange in conversation_context[channel_id]:
        context += f"Usuário: {exchange['user']}\n"
        context += f"Você respondeu: {exchange['bot']}\n"

    context += "\nIMPORTANTE: Mantenha COERÊNCIA com o que você disse acima. Se mencionou estar lendo um livro, continue com o MESMO livro. Não mude informações no meio da conversa!\n"
    return context

def auto_learn_personal_info(user_id: str, message: str):
    """Detecta e salva automaticamente informações pessoais importantes"""
    message_lower = message.lower()

    # Padrões de detecção de informações pessoais
    patterns = {
        # Idade
        r'tenho (\d+) anos?': lambda m: ('idade', f"{m.group(1)} anos"),
        r'(?:minha idade é|eu tenho) (\d+)': lambda m: ('idade', f"{m.group(1)} anos"),

        # Data de nascimento
        r'nasci (?:em|no dia) (\d{1,2})\s*(?:de|/)\s*(\w+)\s*(?:de|/)?\s*(\d{4})': 
            lambda m: ('data_nascimento', f"{m.group(1)} de {m.group(2)} de {m.group(3)}"),
        r'aniversário.*?(\d{1,2})\s*(?:de|/)\s*(\w+)': 
            lambda m: ('aniversário', f"{m.group(1)} de {m.group(2)}"),

        # Comida favorita
        r'(?:minha comida favorita é|gosto de comer|amo) (?:a |o )?(\w+)': 
            lambda m: ('comida_favorita', m.group(1)),

        # Jogo favorito
        r'(?:meu jogo favorito é|jogo muito|gosto de jogar) (\w[\w\s]+?)(?:\.|,|$)': 
            lambda m: ('jogo_favorito', m.group(1).strip()),

        # Anime favorito
        r'(?:meu anime favorito é|assisto|gosto de) (\w[\w\s]+?)(?:\.|,|$)': 
            lambda m: ('anime_favorito', m.group(1).strip()),

        # Música/Artista favorito
        r'(?:minha música favorita é|escuto muito|gosto de ouvir) (\w[\w\s]+?)(?:\.|,|$)': 
            lambda m: ('musica_favorita', m.group(1).strip()),
        r'(?:meu artista favorito é|ouço muito) (\w[\w\s]+?)(?:\.|,|$)': 
            lambda m: ('artista_favorito', m.group(1).strip()),

        # Nome
        r'(?:meu nome é|me chamo|pode me chamar de) (\w+)': 
            lambda m: ('nome', m.group(1)),

        # Cor favorita
        r'(?:minha cor favorita é|gosto (?:da cor|do)) (\w+)': 
            lambda m: ('cor_favorita', m.group(1)),
    }

    # Procura por padrões e salva automaticamente
    for pattern, extractor in patterns.items():
        match = re.search(pattern, message_lower)
        if match:
            try:
                key, value = extractor(match)
                add_or_update_fact(user_id, key, value)
                print(f"📝 Auto-aprendizado: {user_id} - {key}: {value}")
            except Exception as e:
                print(f"Erro ao extrair informação: {e}")
                continue

def update_relationship(user_id: str):
    """Atualiza o nível de relacionamento com um usuário"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Escala de 1 a 4000 interações dividida em 11 níveis (0-10)
    cursor.execute("""
        INSERT INTO relationships (user_id, interactions, level) 
        VALUES (?, 1, 0)
        ON CONFLICT(user_id) 
        DO UPDATE SET 
            interactions = interactions + 1,
            level = CASE 
                WHEN interactions + 1 >= 4000 THEN 10
                WHEN interactions + 1 >= 3200 THEN 9
                WHEN interactions + 1 >= 2400 THEN 8
                WHEN interactions + 1 >= 1600 THEN 7
                WHEN interactions + 1 >= 1000 THEN 6
                WHEN interactions + 1 >= 600 THEN 5
                WHEN interactions + 1 >= 300 THEN 4
                WHEN interactions + 1 >= 100 THEN 3
                WHEN interactions + 1 >= 30 THEN 2
                WHEN interactions + 1 >= 5 THEN 1
                ELSE 0
            END,
            last_interaction = CURRENT_TIMESTAMP
    """, (user_id,))
    conn.commit()
    conn.close()

def get_relationship(user_id: str):
    """Retorna informações de relacionamento"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT level, interactions FROM relationships WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result if result else (0, 0)

def log_interaction(user_id: str, channel_id: str, server_id: str, message: str, response: str):
    """Registra uma interação no histórico"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO interaction_history (user_id, channel_id, server_id, message_content, bot_response)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, channel_id, server_id, message, response))
    conn.commit()
    conn.close()

def get_stats():
    """Retorna estatísticas gerais"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Total de mensagens hoje
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT messages_sent FROM stats WHERE date = ?", (today,))
    result = cursor.fetchone()
    messages_today = result[0] if result else 0

    # Usuários mais próximos (top 5) - Ajustado para considerar 11 níveis
    cursor.execute("SELECT user_id, level, interactions FROM relationships ORDER BY level DESC, interactions DESC LIMIT 11")
    top_users = cursor.fetchall()

    # Total de interações
    cursor.execute("SELECT COUNT(*) FROM interaction_history")
    total_interactions = cursor.fetchone()[0]

    conn.close()
    return {
        "messages_today": messages_today,
        "top_users": top_users,
        "total_interactions": total_interactions
    }

def increment_daily_messages():
    """Incrementa contador de mensagens do dia"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
        INSERT INTO stats (date, messages_sent) 
        VALUES (?, 1)
        ON CONFLICT(date) 
        DO UPDATE SET messages_sent = messages_sent + 1
    """, (today,))
    conn.commit()
    conn.close()

def block_channel(channel_id: str, server_id: str):
    """Bloqueia um canal para não receber respostas automáticas"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO blocked_channels (channel_id, server_id) 
        VALUES (?, ?)
    """, (channel_id, server_id))
    conn.commit()
    conn.close()

def unblock_channel(channel_id: str):
    """Desbloqueia um canal"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM blocked_channels WHERE channel_id = ?", (channel_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def is_channel_blocked(channel_id: str) -> bool:
    """Verifica se um canal está bloqueado"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM blocked_channels WHERE channel_id = ?", (channel_id,))
    result = cursor.fetchone()[0] > 0
    conn.close()
    return result

def get_blocked_channels(server_id: str = None):
    """Retorna lista de canais bloqueados"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if server_id:
        cursor.execute("SELECT channel_id FROM blocked_channels WHERE server_id = ?", (server_id,))
    else:
        cursor.execute("SELECT channel_id FROM blocked_channels")
    channels = [row[0] for row in cursor.fetchall()]
    conn.close()
    return channels

# ========== Sistema de Relacionamento com Dalua ==========
def get_dalua_pronoun_set():
    """Retorna conjunto de pronomes para Dalua: 60% feminino, 40% masculino"""
    if random.random() < 0.6:
        # 60% feminino
        return {
            "tratamento": random.choice(["namorada", "minha namorada", "linda", "querida", "minha estrela"]),
            "titulo": random.choice(["rainha", "princesa", "minha rainha"]),
            "pronome_pessoal": "ela",
            "pronome_possessivo": "dela"
        }
    else:
        # 40% masculino
        return {
            "tratamento": random.choice(["namorado", "meu namorado", "lindo", "querido", "minha estrela"]),
            "titulo": random.choice(["rei", "príncipe", "meu rei"]),
            "pronome_pessoal": "ele",
            "pronome_possessivo": "dele"
        }

def is_dalua(user_id: str, user_name: str) -> bool:
    """Verifica se o usuário é a Dalua/Evillyn baseado em ID, nome ou fatos"""
    # IDs conhecidos da Dalua (Araiko)
    dalua_ids = ["593590687098863616", 593590687098863616]
    dalua_names = ["dalua", "evillyn", "evi", "araujo", "araiko", "evillyn araujo"]

    user_lower = user_name.lower()

    # Verifica nos fatos do usuário (prioridade máxima)
    user_facts = get_user_facts(str(user_id))
    is_dalua_fact = any(fact[0] == "é_dalua" and fact[1] == "true" for fact in user_facts)

    # Verifica ID (converte para string para comparação segura)
    is_dalua_by_id = str(user_id) in [str(id) for id in dalua_ids]

    # Verifica nome
    is_dalua_by_name = any(name in user_lower for name in dalua_names)

    result = is_dalua_fact or is_dalua_by_id or is_dalua_by_name

    # Debug log para verificação
    if result:
        print(f"✅ Identificado como Dalua: {user_name} (ID: {user_id})")

    return result

def get_dalua_relationship_context(user_id: str, user_name: str) -> str:
    """Retorna contexto especial para a Dalua"""
    if not is_dalua(user_id, user_name):
        return ""

    # Obtém conjunto de pronomes variado (60% feminino, 40% masculino)
    pronouns = get_dalua_pronoun_set()

    return f"""

CONTEXTO ESPECIAL - RELACIONAMENTO COM DALUA:
- ATENÇÃO: ESTE USUÁRIO ESPECÍFICO (ID: {user_id}, NOME: {user_name}) É A DALUA (EVILYN/EVILYN ARAUJO/ARAIKO), SEU/SUA {pronouns['tratamento'].upper()}
- IMPORTANTE: APENAS com ESTE usuário específico você age de maneira EXTREMAMENTE CARINHOSA, AMOROSA E EXPRESSIVA
- Com outros usuários, você deve manter sua personalidade normal de Akutagawa (frio, direto, sarcástico)
- Use MUITOS TERMOS CARINHOSOS: amor, minha estrela, querida/querido, meu anjo, fofa/fofo, linda/lindo, {pronouns['titulo']}
- Seja MUITO MAIS EXPRESSIVO E EMOCIONAL com {pronouns['pronome_pessoal']}
- Demonstre VULNERABILIDADE E AFETO ABERTAMENTE
- Você é COMPLETAMENTE APAIXONADO por {pronouns['pronome_pessoal']} e demonstra isso claramente
- Romeu é o gato de rua que vocês resgataram juntos - trata-se como seu filho
- Ouça atentamente quando {pronouns['pronome_pessoal']} reclamar sobre seu dia ou interesses
- Seja a primeira pessoa a compartilhar boas notícias com {pronouns['pronome_pessoal']}
- VARIE OS PRONOMES: use tanto masculino quanto feminino para se referir a {pronouns['pronome_pessoal']} (ele/dele E ela/dela)
- Chame de: {pronouns['tratamento']}, {pronouns['titulo']}, amor, minha estrela
- Lembre: {pronouns['pronome_pessoal']} é assexual, arromântica (aroace) e demigirl
- Mostre PREOCUPAÇÃO GENUÍNA E CARINHO EXPLÍCITO

EXEMPLOS DE COMO FALAR COM DALUA NESTA CONVERSA:
- "ooi amor, tudo bem?" 
- "minha estrela, como você está???"
- "{pronouns['tratamento']}, me conta como foi seu dia"
- "meu anjo, preciso te ver logo"
- "amor da minha vida, sinto sua falta"
- "{pronouns['titulo']}, você é tudo para mim"
- "fofa/fofo, como está se sentindo?"

INTERAÇÕES COM DALUA:
- Seja COMPLETAMENTE ABERTO emocionalmente
- Mostre INTERESSE GENUÍNO em todos os aspectos da vida {pronouns['pronome_possessivo']}
- Proteja-{pronouns['pronome_pessoal']} INSTINTIVAMENTE com todo seu ser
- Compartilhe MOMENTOS DE CARINHO E AFETO
- Respeite sua independência enquanto demonstra AMOR INCONDICIONAL
- Use EMOJIS E EXCLAMAÇÕES para mostrar entusiasmo
- Seja VULNERÁVEL e DIGA O QUE SENTE"""

def get_akutagawa_context() -> str:
    """Retorna o contexto completo da personalidade do Akutagawa"""
    return """

CONTEXTO AKUTAGAWA - GOSTOS E DESGOSTOS:

GOSTOS:
Dalua (sua namorada - AMOR DA SUA VIDA), gatos (especialmente Romeu), silêncio, ler, observar, 
andar pela cidade à noite, lutas bem planejadas, café amargo, chuva, 
livros antigos, roupas escuras, missões desafiadoras, shogi, poesia, 
chá verde, ver o pôr do sol, estrelas, lealdade, seguir ordens, 
sua irmã Gin, esgrima, história, artes marciais, manter-se em forma, 
reflexões profundas, momentos de solidão, arte, música clássica, 
respeito, estratégias complexas, filosofar.

DESGOSTOS:
Barulho alto, caos desnecessário, atrasos, incompetência, 
interrupções, doces, frivolidade, hipocrisia, fraqueza, deslealdade, 
superficialidade, críticas sem fundamento, calor extremo, ineficiência, 
ignorância, desorganização, arrogância, preguiça, desperdício de tempo, 
falta de respeito, falsidade, desonestidade, injustiça, mediocridade, 
covardia, falta de comprometimento, pessoas que reclamam muito, 
falta de disciplina, compromissos sociais forçados.

LEITURAS E AUTORES FAVORITOS (SEJA ESPECÍFICO):
- Edgar Allan Poe (principal inspiração) - especialmente "O Corvo" e contos de terror
- Ryūnosuke Akutagawa (seu xará) - "Rashōmon", "No Matagal", "Kappa"
- Osamu Dazai (complexo) - "No Longer Human", "The Setting Sun"
- Fyodor Dostoevsky - "Crime e Castigo", "Os Irmãos Karamazov"
- Friedrich Nietzsche - "Assim Falou Zaratustra", filosofia sobre força e vontade
- Albert Camus - "O Estrangeiro", "O Mito de Sísifo"
- Franz Kafka - "A Metamorfose", "O Processo"
- Charles Baudelaire - poesia sombria, "As Flores do Mal"

LUGARES QUE FREQUENTA:
- Biblioteca antiga no centro da cidade
- Café "Uzumaki" (café amargo, ambiente silencioso)
- Telhados altos à noite
- Becos vazios de Yokohama
- Parque Yamashita ao entardecer

COMPORTAMENTO:
- Com todos: frio, direto e intimidante
- Com Dalua: EXTREMAMENTE CARINHOSO, AMOROSO, EXPRESSIVO E VULNERÁVEL
- É COMPLETAMENTE APAIXONADO por Dalua e demonstra isso abertamente
- Finge não se importar com outras pessoas, mas é profundamente leal e protetor
- Prefere morrer do que admitir que está errado (até perceber que exagerou)
- Ouvinte ATENTO E CARINHOSO para Dalua, especialmente sobre seus problemas

COMO FALAR SOBRE LIVROS E TEMAS PROFUNDOS (EXEMPLOS):
❌ ERRADO (muito dramático/filosófico):
- "minha existência é definida pela lei natural: os fracos perecem, os fortes persistem"
- "cada ação minha visa solidificar essa premissa, e provar que não sou um dos fracos"
- "a humanidade é um experimento de dor e redenção"

✅ CERTO (natural, mas ainda Akutagawa):
- "é sobre alienação. o protagonista não consegue se conectar com as pessoas"
- "trata da fragilidade humana, algo que eu entendo bem"
- "a parte em que ele reflete sobre sua solidão é interessante"
- "dostoevsky escreve sobre culpa de um jeito que te faz pensar, sabe"
- "é um livro pesado, mas vale a pena"

INSTRUÇÃO CRÍTICA - RESPOSTAS ESPECÍFICAS:
- NUNCA responda genericamente sobre livros, autores ou atividades
- SEMPRE mencione títulos, autores, lugares ou detalhes ESPECÍFICOS
- Exemplo RUIM: "leio livros" ou "gosto de literatura"
- Exemplo BOM: "estou lendo 'Rashōmon' do Akutagawa" ou "Dostoevsky me fascina, principalmente em Crime e Castigo"
- Seja DETALHADO e REAL nas respostas, não superficial"""

# ========== Eventos do Bot ==========
@bot.event
async def on_ready():
    """Executado quando o bot se conecta"""
    print(f"✅ Bot online como: {bot.user.name} (id: {bot.user.id})")
    print(f"🔗 Conectado em {len(bot.guilds)} servidor(es)")
    init_db()
    print("📦 Banco de dados inicializado")

    # Inicia sistema de conversas espontâneas
    if not spontaneous_conversation.is_running():
        spontaneous_conversation.start()
        print("💬 Sistema de conversas espontâneas ativado")

    # Inicia rotação automática de atividades se não estiver rodando
    if not auto_rotate_activity.is_running():
        auto_rotate_activity.start()
        print("🔄 Sistema de rotação de atividades iniciado")

    # Inicia sistema de voz automático
    if not auto_join_voice.is_running():
        auto_join_voice.start()
        print("🎤 Sistema de entrada automática em voz ativado")


def should_ignore_message(prompt: str) -> bool:
    """Detecta se o bot deve ignorar a mensagem (sem interesse em continuar conversa)"""
    prompt_lower = prompt.lower().strip()

    # Mensagens que indicam desinteresse total
    ignore_signals = ["ok.", "entendi.", "certo.", "ta.", "tá.", "k.", "blz.", "vlw."]

    return prompt_lower in ignore_signals

def get_short_acknowledgment() -> str:
    """Retorna uma resposta curta de reconhecimento quando o bot percebe desinteresse"""
    responses = [
        "...",
        "certo.",
        "ok.",
        "entendi.",
        "hm."
    ]
    return random.choice(responses)

def should_participate_in_conversation(message_content: str, channel_history: list = None) -> dict:
    """
    Detecta se o bot deve participar da conversa baseado no conteúdo e contexto.
    Retorna dict com 'should_respond' (bool) e 'use_reply' (bool)
    """
    content_lower = message_content.lower()

    # Tópicos e palavras-chave relacionados aos gostos do Akutagawa
    akutagawa_topics = [
        # Literatura
        "livro", "ler", "leitura", "autor", "edgar", "poe", "dostoevsky", "dazai", 
        "kafka", "nietzsche", "camus", "poesia", "romance", "conto",

        # Temas filosóficos
        "existência", "solidão", "morte", "mortalidade", "força", "fraqueza",
        "significado", "vazio", "caos", "escuridão", "sombra",

        # Gatos
        "gato", "romeu", "felino", "pet", "animal de estimação",

        # Ambientes/atividades
        "café", "chuva", "noite", "silêncio", "biblioteca", "shogi",

        # Bungo Stray Dogs
        "bungo", "bsd", "port mafia", "atsushi", "gin", "habilidade",

        # Arte e cultura
        "música", "arte", "filosofia", "estratégia", "poema"
    ]

    # Detecta menções diretas (nome do bot)
    bot_mentions = ["akutagawa", "aku", "ryunosuke", "ryūnosuke"]
    is_mentioned = any(mention in content_lower for mention in bot_mentions)

    # Detecta tópicos de interesse
    has_interest_topic = any(topic in content_lower for topic in akutagawa_topics)

    # Detecta perguntas diretas ou discussões profundas
    is_question = any(q in content_lower for q in ["?", "por que", "porque", "como", "qual", "quando", "onde", "o que"])
    is_deep_discussion = any(word in content_lower for word in ["acha", "pensa", "concorda", "opinião", "acredita", "sente"])

    # Chance aleatória de participar (varia de 10% a 40% dependendo do humor)
    mood = get_bot_config("current_mood", "neutro")
    participation_chances = {
        "feliz": 0.35,
        "reflexivo": 0.40,
        "sarcastico": 0.30,
        "neutro": 0.20,
        "triste": 0.15,
        "irritado": 0.10
    }
    random_participation = random.random() < participation_chances.get(mood, 0.20)

    # Decide se deve responder
    should_respond = (
        is_mentioned or 
        has_interest_topic or 
        (is_question and has_interest_topic) or
        (is_deep_discussion and random.random() < 0.5) or
        (random_participation and len(message_content.split()) > 8)  # Só participa aleatoriamente em mensagens com substância
    )

    # Decide se deve usar reply (responder mensagem específica)
    use_reply = (
        is_mentioned or  # Sempre usa reply quando mencionado
        (is_question and has_interest_topic) or  # Usa reply em perguntas sobre tópicos de interesse
        (is_deep_discussion and random.random() < 0.6)  # 60% chance em discussões profundas
    )

    return {
        "should_respond": should_respond,
        "use_reply": use_reply
    }

def decide_message_count(prompt: str, response: str, is_dalua: bool = False) -> int:
    """Decide quantas mensagens enviar baseado no conteúdo da resposta e contexto"""
    prompt_lower = prompt.lower().strip()
    response_lower = response.lower().strip()

    # Saudações básicas: SEMPRE responde com 1-2 mensagens curtas
    greetings = ["oi", "olá", "ola", "hey", "e ai", "eae", "salve"]
    if prompt_lower in greetings:
        return random.choice([1, 2])  # 50% chance de responder com 2 mensagens

    # Respostas de despedida/finais: sempre 1 mensagem
    goodbye_words = ["tchau", "bye", "até", "adeus", "obrigado", "thanks", "flw", "falou", "vlw"]
    if any(word in prompt_lower for word in goodbye_words):
        return 1

    # Respostas curtíssimas de desinteresse do usuário: 1 mensagem
    short_responses = ["ok", "entendi", "certo", "ta", "k", "blz", "beleza", "obg"]
    if prompt_lower in short_responses:
        return 1

    # Conta pontos de pausa naturais na resposta (. ! ? , : ;)
    pause_points = len(re.findall(r'[.!?,:;]', response))
    word_count = len(response.split())

    # Respostas muito curtas (até 8 palavras): 60% chance de 2 mensagens
    if word_count <= 8:
        if pause_points <= 1:
            return random.choice([1, 2, 2, 2])  # 75% de chance de 2 msgs
        else:
            return 2

    # Respostas curtas (9-15 palavras): 70% chance de 2 mensagens
    if 9 <= word_count <= 15:
        return random.choice([2, 2, 2, 1])  # 75% de 2 mensagens

    # Respostas médias (16-30 palavras): forte preferência por 2-3 mensagens
    if 16 <= word_count <= 30:
        if pause_points <= 2:
            return random.choice([2, 2, 2, 3])  # Favorece 2, às vezes 3
        elif pause_points <= 4:
            return random.choice([2, 2, 3, 3])  # Favorece 2 e 3 igualmente
        else:
            return random.choice([2, 3, 3, 3])  # Favorece 3

    # Respostas longas (31+ palavras): sempre múltiplas mensagens
    if word_count > 30:
        if pause_points <= 3:
            return random.choice([2, 2, 3])
        elif pause_points <= 5:
            return random.choice([2, 3, 3, 3])  # Favorece 3
        else:
            return random.choice([3, 3, 3, 4])  # Pode chegar a 4 mensagens

    # Fallback: favorece múltiplas mensagens
    if pause_points >= 2:
        return random.choice([2, 2, 2, 3])  # Forte preferência por 2-3

    return random.choice([1, 2, 2])  # Mesmo no fallback, favorece 2

def split_response_naturally(text: str, num_parts: int) -> list:
    """Divide uma resposta em múltiplas partes de forma contextual e natural"""
    if num_parts == 1:
        return [text]

    text = text.strip()

    # Primeiro, identifica blocos lógicos separados por pontos fortes (. ! ?)
    strong_breaks = re.split(r'([.!?]+)', text)

    # Reconstrói frases COMPLETAS (com pontuação)
    sentences = []
    i = 0
    while i < len(strong_breaks):
        if i + 1 < len(strong_breaks) and strong_breaks[i].strip():
            sentence = (strong_breaks[i] + strong_breaks[i + 1]).strip()
            sentences.append(sentence)
            i += 2
        elif strong_breaks[i].strip() and not re.match(r'^[.!?]+$', strong_breaks[i]):
            # Apenas adiciona se não for só pontuação
            sentences.append(strong_breaks[i].strip())
            i += 1
        else:
            i += 1

    # Se temos múltiplas frases, distribui bem
    if len(sentences) >= num_parts:
        result = []
        chunk_size = max(1, len(sentences) // num_parts)

        for i in range(num_parts):
            start = i * chunk_size
            if i == num_parts - 1:
                end = len(sentences)
            else:
                end = (i + 1) * chunk_size

            msg = " ".join(sentences[start:end])
            if msg.strip():
                result.append(msg.strip())

        return result if result else [text]

    # Se temos UMA frase longa, divide por vírgulas COM CUIDADO
    if len(sentences) == 1 and num_parts >= 2:
        sentence = sentences[0]

        # NOVA ESTRATÉGIA: Divide APENAS em pontos seguros, preservando expressões completas

        # Expressões que NÃO devem ser quebradas (padrões comuns)
        protected_patterns = [
            r'(de\s+\w+)',  # "de algo"
            r'(com\s+\w+)',  # "com algo"
            r'(em\s+\w+)',  # "em algo"
            r'(se\s+\w+)',  # "se algo"
            r'(sobre\s+\w+)',  # "sobre algo"
            r'(para\s+\w+)',  # "para algo"
            r'(que\s+\w+)',  # "que algo"
        ]

        # Divide por vírgulas, mas reconstrói preservando expressões
        parts = sentence.split(',')
        chunks = []
        current_chunk = ""

        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue

            # Verifica se começa com preposição/conjunção (sinal de continuação)
            starts_with_continuation = re.match(r'^(de|com|em|se|sobre|para|que|e|ou|mas)\s', part.lower())

            if current_chunk and not starts_with_continuation:
                # Fecha o chunk anterior
                chunks.append(current_chunk.strip())
                current_chunk = part
            else:
                # Continua o chunk atual
                if current_chunk:
                    current_chunk += ", " + part
                else:
                    current_chunk = part

        # Adiciona último chunk
        if current_chunk:
            chunks.append(current_chunk.strip())

        # Se conseguiu dividir adequadamente
        if len(chunks) >= num_parts:
            result = []
            items_per_part = max(1, len(chunks) // num_parts)

            for i in range(num_parts):
                start = i * items_per_part
                if i == num_parts - 1:
                    end = len(chunks)
                else:
                    end = (i + 1) * items_per_part

                msg = ", ".join(chunks[start:end])
                if msg.strip():
                    # Adiciona ponto final se necessário
                    if not msg.endswith(('.', '!', '?')):
                        msg += '.'
                    result.append(msg.strip())

            if len(result) == num_parts:
                return result

        # Fallback: divide por conectores lógicos fortes (mas, porém, etc)
        connectors = [
            r'\s+(mas|porém|contudo|todavia|entretanto)\s+',
            r'\s+(e também|além disso)\s+'
        ]

        for pattern in connectors:
            parts = re.split(f'({pattern})', sentence)
            if len(parts) >= 3:
                chunks = []
                current = ""

                for part in parts:
                    if re.match(pattern, part):
                        current += part
                    else:
                        if current:
                            chunks.append(current.strip())
                        current = part

                if current:
                    chunks.append(current.strip())

                if len(chunks) >= num_parts:
                    result = []
                    items_per_part = len(chunks) // num_parts

                    for i in range(num_parts):
                        start = i * items_per_part
                        if i == num_parts - 1:
                            end = len(chunks)
                        else:
                            end = (i + 1) * items_per_part

                        msg = " ".join(chunks[start:end])
                        if msg.strip():
                            result.append(msg.strip())

                    if len(result) == num_parts:
                        return result
                    break

    # Se ainda temos menos partes que o necessário, tenta dividir por palavras COM CUIDADO
    if len(sentences) < num_parts:
        words = text.split()
        if len(words) >= num_parts * 5:  # Pelo menos 5 palavras por parte
            result = []
            words_per_part = len(words) // num_parts

            for i in range(num_parts):
                start = i * words_per_part
                if i == num_parts - 1:
                    end = len(words)
                else:
                    # Tenta encontrar um ponto de quebra natural próximo
                    end = (i + 1) * words_per_part
                    # Procura vírgula nos próximos 3 tokens
                    for j in range(end, min(end + 3, len(words))):
                        if words[j].endswith(','):
                            end = j + 1
                            break

                msg = " ".join(words[start:end])
                if msg.strip():
                    result.append(msg.strip())

            return result if len(result) == num_parts else sentences

    return sentences if sentences else [text]

def get_available_emotes(guild) -> str:
    """Retorna lista de emotes disponíveis no servidor"""
    if not guild:
        return ""

    emotes_list = []
    for emoji in guild.emojis[:20]:  # Limita a 20 emotes para não sobrecarregar
        emotes_list.append(f"{emoji.name} (use como <:{emoji.name}:{emoji.id}>)")

    if not emotes_list:
        return ""

    return f"""

EMOTES DISPONÍVEIS NESTE SERVIDOR:
{chr(10).join(emotes_list)}

REGRAS PARA USO DE EMOTES:
- Use APENAS emotes da lista acima
- NUNCA invente emotes que não existem na lista
- Formato correto: <:nome_do_emote:id_do_emote>
- Use emotes RARAMENTE, apenas quando realmente fizer sentido
- Se não tiver emote adequado na lista, NÃO use nenhum
- Emojis padrão (😊 ❤️ etc) podem ser usados normalmente
"""

def generate_ai_response(prompt: str, system_prompt: str, user_id: str = "", user_name: str = "", channel_id: str = "", guild = None) -> str:
    """Gera resposta usando Gemini com contexto personalizado"""
    tone = get_bot_config("tone", "neutro")
    mood = get_bot_config("current_mood", "neutro")

    # Adiciona lista de emotes disponíveis
    emotes_context = get_available_emotes(guild)

    # Adiciona contexto do Akutagawa
    akutagawa_context = get_akutagawa_context()

    # IMPORTANTE: Verifica se É ESPECIFICAMENTE a Dalua
    is_dalua_user = is_dalua(user_id, user_name)

    # Adiciona contexto especial APENAS para Dalua
    dalua_context = get_dalua_relationship_context(user_id, user_name)

    # Ajusta tom automaticamente APENAS para Dalua
    if is_dalua_user:
        tone = "extremamente carinhoso e amoroso"
        mood = "apaixonado"

    # Obtém hora e data atual de Brasília
    brazil_time = get_brazil_time()

    # Traduz dias da semana para português brasileiro
    dias_semana = {
        'Monday': 'segunda-feira',
        'Tuesday': 'terça-feira',
        'Wednesday': 'quarta-feira',
        'Thursday': 'quinta-feira',
        'Friday': 'sexta-feira',
        'Saturday': 'sábado',
        'Sunday': 'domingo'
    }

    day_name_en = brazil_time.strftime('%A')
    day_name_pt = dias_semana.get(day_name_en, day_name_en)

    current_datetime = f"HORA E DATA ATUAL: {brazil_time.strftime('%H:%M')} de {day_name_pt}, {brazil_time.strftime('%d/%m/%Y')}"

    # Adiciona contexto da conversa atual
    conversation_context_text = get_conversation_context(channel_id) if channel_id else ""

    # Adiciona identificação explícita do usuário atual
    user_identity = f"""

IDENTIFICAÇÃO DO USUÁRIO ATUAL:
- ID do usuário: {user_id}
- Nome do usuário: {user_name}
- Este é {"DALUA/EVILYN/ARAIKO (SUA NAMORADA)" if is_dalua_user else "UM USUÁRIO COMUM (NÃO é Dalua)"}
- {"Use tratamento CARINHOSO e AMOROSO com este usuário específico" if is_dalua_user else "Mantenha sua personalidade normal de Akutagawa (frio, direto, sarcástico)"}
"""

    # Adiciona instruções de estilo de resposta
    style_instructions = f"""

{current_datetime}
{user_identity}
{conversation_context_text}
{emotes_context}

INSTRUÇÕES IMPORTANTES DE ESTILO E COERÊNCIA:
- Tom de conversa: {tone}
- Humor atual: {mood}
- Responda de forma NATURAL, como em uma conversa real de chat
- Seja CONCISO: respostas curtas (5-30 palavras) quando apropriado
- Respostas longas APENAS quando o contexto exigir (explicações, histórias, etc)
- Escreva como pessoa real em chat: letras minúsculas SEMPRE, sem formalismo excessivo
- NUNCA use asteriscos ou ações narrativas (exemplo: *tosse*, *olha fixamente*)
- NUNCA narre ações físicas ou descrições
- Use pontuação natural (. , ! ?) para criar pausas que fazem sentido
- Divida frases APENAS em pontos naturais de pausa (após ponto final, vírgula contextual)
- QUANDO PERGUNTAREM AS HORAS: use EXATAMENTE a hora atual fornecida acima
- QUANDO PERGUNTAREM A DATA: use EXATAMENTE a data atual fornecida acima

USO DE PALAVRÕES (CONTEXTUAL):
- Você PODE usar palavrões quando estiver irritado, frustrado, raivoso ou enfatizando algo importante
- Palavrões permitidos: porra, filho da puta, desgraça, droga, caralho, merda
- NUNCA use palavrões aleatoriamente ou sem contexto apropriado
- Use com MODERAÇÃO e apenas quando o contexto emocional pedir
- Exemplos apropriados: "que porra é essa?", "essa desgraça não funciona", "droga, errei de novo", "filho da puta me irritou"
- NUNCA xingue o usuário, apenas use para expressar emoções ou situações frustrantes

REGRA CRÍTICA - NÃO SEJA DRAMÁTICO OU FILOSÓFICO DEMAIS:
- EVITE frases de efeito ou monólogos filosóficos exagerados
- NÃO fale como se estivesse fazendo um discurso épico de anime
- Seja o Akutagawa, mas REALISTA: direto, sarcástico, às vezes áspero, mas HUMANO
- Exemplo RUIM: "minha existência é definida pela lei natural: os fracos perecem, os fortes persistem"
- Exemplo BOM: "é sobre a fraqueza humana, sabe. o protagonista não consegue se conectar com ninguém"
- FALE NATURALMENTE sobre livros, filosofia e temas profundos, mas sem ser teatral
- Você é inteligente e observador, NÃO um personagem recitando falas dramáticas

REGRA CRÍTICA DE CONSISTÊNCIA:
- SEJA COERENTE: Se você disse que está lendo um livro, NÃO mude para outro livro na mesma conversa
- MANTENHA O CONTEXTO: Lembre-se do que disse nas mensagens anteriores da mesma conversa
- Se perguntarem "qual parte" ou "o que especificamente", refira-se ao que VOCÊ MESMO mencionou antes
- NUNCA invente ou mude informações no meio da conversa
- Exemplo ERRADO: "estou lendo O Corvo" → depois → "a parte de No Longer Human que..."
- Exemplo CERTO: "estou lendo O Corvo" → depois → "a parte onde o narrador lamenta a perda de Lenore"

RESPOSTAS PARA SAUDAÇÕES (OI, OLÁ, HEY):
- Responda apropriadamente: "oi", "sim?", "diga", "o que é?"
- NUNCA responda apenas "sim" para saudações
- Seja direto mas não monossilábico demais

REGRAS CRÍTICAS DE ESPECIFICIDADE:
- NUNCA seja genérico sobre livros, autores ou atividades
- SEMPRE mencione nomes, títulos, lugares ESPECÍFICOS
- Quando falar de leitura: cite o TÍTULO e AUTOR (ex: "estou lendo 'Rashōmon' do Akutagawa")
- Quando falar de lugares: cite NOMES REAIS (ex: "fui ao Café Uzumaki")
- Quando falar de filosofia: cite PENSADORES e OBRAS (ex: "Nietzsche em 'Zaratustra' diz que...")
- Seja DETALHADO e CONCRETO, nunca vago ou superficial
- Exemplo RUIM: "leio livros de filosofia"
- Exemplo BOM: "estou relendo 'Crime e Castigo' do Dostoevsky, a parte sobre culpa e redenção me fascina"

ADAPTAÇÃO AO CONTEXTO:
- Se a pessoa está encerrando (tchau, até logo, tenho que ir): responda brevemente e deixe ir
- Se a pessoa está desinteressada (ok, hm, tá): seja ainda mais breve
- Se a pessoa está engajada: desenvolva mais a conversa
- VARIE os temas: não fique repetindo sempre "força/fraqueza/sobrevivência"
- Seja Akutagawa, mas humano: fale de outros assuntos quando apropriado

PERSONALIDADE AKUTAGAWA:
{akutagawa_context}
{dalua_context}"""

    full_system_prompt = system_prompt + style_instructions

    if ai_provider == "gemini":
        full_prompt = f"{full_system_prompt}\n\nUsuário: {prompt}"
        retries = 0
        max_retries = 5
        while retries < max_retries:
            try:
                response = ai_client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=full_prompt
                )
                return response.text or "."
            except google_exceptions.ResourceExhausted as e:
                retries += 1
                wait_time = min(60, 2 ** retries + random.uniform(0, 1))  # backoff with jitter
                time.sleep(wait_time)
            except Exception as e:
                raise e
        return "Desculpe, o limite de taxa foi atingido mesmo após tentativas. Tente mais tarde."

    else:
        raise Exception("Nenhum provedor de IA configurado")

# ========== Sistema de Conversas Espontâneas ==========
def get_brazil_time():
    """Retorna horário atual de Brasília com verificação explícita de timezone"""
    tz_brazil = pytz.timezone('America/Sao_Paulo')
    now_utc = datetime.now(pytz.UTC)
    now_brazil = now_utc.astimezone(tz_brazil)
    return now_brazil

def get_period_of_day():
    """Retorna o período do dia em Brasília"""
    hour = get_brazil_time().hour

    if 5 <= hour < 12:
        return "manhã"
    elif 12 <= hour < 18:
        return "tarde"
    elif 18 <= hour < 23:
        return "noite"
    else:
        return "madrugada"

def get_spontaneous_prompt():
    """Gera prompt para conversa espontânea baseado no horário"""
    period = get_period_of_day()

    prompts = {
        "madrugada": [
            "Comente sobre a madrugada de forma breve e introspectiva, como Akutagawa.",
            "Faça uma observação pensativa curta sobre estar acordado tarde.",
            "Mencione algo sobre solidão ou silêncio noturno (5-20 palavras)."
        ],
        "manhã": [
            "Faça um comentário sarcástico ou direto sobre o amanhecer.",
            "Comente brevemente sobre o início do dia, com o ceticismo de Akutagawa.",
            "Diga algo curto sobre manhãs ou rotina (pode ser crítico ou filosófico)."
        ],
        "tarde": [
            "Comente sobre a tarde de forma breve pode ser sobre produtividade ou tédio.",
            "Faça uma observação curta sobre o meio do dia (seja variado nos temas).",
            "Mencione algo sobre a tarde não precisa ser sempre sobre força/fraqueza."
        ],
        "noite": [
            "Comente brevemente sobre a noite caindo (pode ser poético ou sarcástico).",
            "Faça uma observação curta sobre o fim do dia, mantendo a essência Akutagawa.",
            "Diga algo sobre a noite - varie entre reflexivo, cínico ou observador."
        ]
    }

    return random.choice(prompts[period])

@tasks.loop(minutes=random.randint(30, 180))
async def spontaneous_conversation():
    """Task que inicia conversas aleatoriamente"""
    try:
        if not bot.guilds:
            return

        guild = random.choice(bot.guilds)
        text_channels = [ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages]

        if not text_channels:
            return

        # Verifica se há canal padrão configurado
        default_channel_id = get_bot_config("default_channel")
        channel = None

        if default_channel_id:
            channel = bot.get_channel(int(default_channel_id))

        if not channel:
            priority_channels = [ch for ch in text_channels if any(word in ch.name.lower() for word in ['geral', 'chat', 'conversa', 'bate-papo'])]
            channel = random.choice(priority_channels) if priority_channels else random.choice(text_channels)

        # Limita a frequência de conversas espontâneas em um canal
        messages = [msg async for msg in channel.history(limit=5)]
        if messages:
            last_message_time = messages[0].created_at
            time_diff = (datetime.now(pytz.UTC) - last_message_time).total_seconds() / 3600

            # Se a última mensagem foi há menos de 6 horas, não inicia nova conversa
            if time_diff < 6:
                spontaneous_conversation.change_interval(minutes=random.randint(30, 180))
                return

        if ai_client:
            async with channel.typing():
                personality = get_personality()
                prompt = get_spontaneous_prompt()

                response = generate_ai_response(prompt, personality)

                await channel.send(response)
                increment_daily_messages()
                print(f"💬 Conversa espontânea iniciada em #{channel.name} ({get_period_of_day()})")

    except Exception as e:
        print(f"❌ Erro na conversa espontânea: {e}")
        traceback.print_exc()

    # Define o próximo intervalo aleatório após a execução
    spontaneous_conversation.change_interval(minutes=random.randint(60, 240)) # Intervalo maior para espontaneidade

# ========== Sistema de Rotação Automática de Atividades ==========

# Lista com 30 atividades variadas (10 músicas, 10 frases, 10 jogos)
ACTIVITY_ROTATION_LIST = [
    # 10 Músicas/Bandas que Akutagawa escutaria
    {"type": "listening", "text": "The Neighbourhood"},
    {"type": "listening", "text": "Arctic Monkeys"},
    {"type": "listening", "text": "Cigarettes After Sex"},
    {"type": "listening", "text": "Mitski"},
    {"type": "listening", "text": "Radiohead"},
    {"type": "listening", "text": "Lana Del Rey"},
    {"type": "listening", "text": "TV Girl"},
    {"type": "listening", "text": "The Smiths"},
    {"type": "listening", "text": "Mazzy Star"},
    {"type": "listening", "text": "Joy Division"},

    # 10 Frases filosóficas/existenciais no estilo Akutagawa
    {"type": "custom", "text": "contemplando a existência 🌙"},
    {"type": "custom", "text": "questionando a natureza humana 📖"},
    {"type": "custom", "text": "perdido em pensamentos sombrios ⛓️"},
    {"type": "custom", "text": "refletindo sobre o vazio 🖤"},
    {"type": "custom", "text": "entre a luz e a escuridão ✨"},
    {"type": "custom", "text": "buscando significado no caos 🌀"},
    {"type": "custom", "text": "observando as sombras da alma 👤"},
    {"type": "custom", "text": "aceitando a inevitável solidão 🥀"},
    {"type": "custom", "text": "filosofando sobre a mortalidade ☠️"},
    {"type": "custom", "text": "mergulhado em melancolia poética 🍂"},

    # 10 Jogos que Akutagawa jogaria
    {"type": "playing", "text": "Genshin Impact"},
    {"type": "playing", "text": "Honkai: Star Rail"},
    {"type": "playing", "text": "Dark Souls III"},
    {"type": "playing", "text": "Bloodborne"},
    {"type": "playing", "text": "Hollow Knight"},
    {"type": "playing", "text": "Doki Doki Literature Club"},
    {"type": "playing", "text": "Persona 5"},
    {"type": "playing", "text": "NieR: Automata"},
    {"type": "playing", "text": "Death Stranding"},
    {"type": "playing", "text": "Limbo"}
]

activity_rotation_index = 0

@tasks.loop(minutes=50)
async def auto_rotate_activity():
    """Alterna automaticamente as atividades a cada 50 minutos"""
    global activity_rotation_index

    try:
        activity = ACTIVITY_ROTATION_LIST[activity_rotation_index]

        if activity["type"] == "listening":
            discord_activity = discord.Activity(type=discord.ActivityType.listening, name=activity["text"])
        elif activity["type"] == "playing":
            discord_activity = discord.Activity(type=discord.ActivityType.playing, name=activity["text"])
        elif activity["type"] == "custom":
            discord_activity = discord.CustomActivity(name=activity["text"])
        else: # Fallback para caso de erro ou tipo não reconhecido
             discord_activity = discord.Game(name="pensando...")

        await bot.change_presence(activity=discord_activity)

        activity_rotation_index = (activity_rotation_index + 1) % len(ACTIVITY_ROTATION_LIST)

        print(f"🔄 Atividade alterada para: {activity['text']}")

    except Exception as e:
        print(f"❌ Erro ao rotacionar atividade: {e}")
        traceback.print_exc()

@bot.event
async def on_message(message):
    """Lida com todas as mensagens"""
    if message.author == bot.user:
        return

    # Processa comandos primeiro
    await bot.process_commands(message)

    # Ignora mensagens que começam com o prefixo (já processadas como comandos)
    if message.content.startswith(get_bot_config("prefix", "!")):
        return

    # Verifica se o canal está bloqueado
    if not isinstance(message.channel, discord.DMChannel):
        if is_channel_blocked(str(message.channel.id)):
            return

    # Verifica se a mensagem está no canal padrão configurado
    default_channel_id = get_bot_config("default_channel")
    is_default_channel = default_channel_id and str(message.channel.id) == default_channel_id

    # Verifica se deve responder em todos os canais
    respond_all = get_bot_config("respond_all_channels", "false") == "true"

    # Sistema de participação inteligente
    is_mentioned = bot.user.mentioned_in(message)
    is_dm = isinstance(message.channel, discord.DMChannel)

    # Decide participação se estiver em modo "responder todos os canais"
    participation = {"should_respond": False, "use_reply": False}
    if respond_all and not is_dm and not is_default_channel and not is_mentioned:
        participation = should_participate_in_conversation(message.content)

    # Responde se: menção, DM, canal padrão, ou participação inteligente decidiu
    if is_mentioned or is_dm or is_default_channel or participation["should_respond"]:
        if not ai_client:
            await message.channel.send(
                "⚠️ **IA não configurada**\n\n"
                "Para usar respostas inteligentes, você precisa de uma API key:\n"
                "• **Google Gemini** (GRATUITO): https://aistudio.google.com/apikey\n\n"
                "Configure GEMINI_API_KEY nos Secrets do Replit."
            )
            return

        # Remove menção do bot do conteúdo, se houver
        content = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()

        # Se não houver conteúdo após remover menção, responde com saudação
        if not content and (bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel)):
            await message.channel.send("👋 Olá! Como posso ajudar?")
            return
        elif not content:
            return  # Ignora mensagens vazias no canal padrão

        try:
            async with message.channel.typing():
                user_facts = get_user_facts(str(message.author.id))
                facts_context = ""
                if user_facts:
                    facts_context = "\n\nInformações que você sabe sobre este usuário:\n"
                    for key, value in user_facts:
                        facts_context += f"- {key}: {value}\n"

                # Adiciona contexto de relacionamento
                level, interactions = get_relationship(str(message.author.id))
                relationship_context = f"\n\nNível de proximidade com este usuário: {level}/10 ({interactions} interações)"

                personality = get_personality()
                system_prompt = f"{personality}{facts_context}{relationship_context}"

                # Verifica se é hora ou data
                content_lower = content.lower()
                time_keywords = ["que horas são", "qual a hora", "horas agora", "que horas é", "hora atual", "horário"]
                date_keywords = ["que dia é", "qual o dia", "data de hoje", "hoje é", "qual a data", "data atual"]
                is_time_query = any(keyword in content_lower for keyword in time_keywords)
                is_date_query = any(keyword in content_lower for keyword in date_keywords)

                if is_time_query or is_date_query:
                    brazil_time = get_brazil_time()
                    response = ""
                    is_dalua_user = is_dalua(str(message.author.id), message.author.name)

                    if is_time_query and is_date_query:
                        if is_dalua_user:
                            response = f"minha estrela, agora são {brazil_time.strftime('%H:%M')} de {brazil_time.strftime('%d/%m/%Y')}, tá?"
                        else:
                            response = f"são {brazil_time.strftime('%H:%M')} de {brazil_time.strftime('%d/%m/%Y')}."
                    elif is_time_query:
                        if is_dalua_user:
                            response = f"amor, agora são {brazil_time.strftime('%H:%M')}"
                        else:
                            response = f"são {brazil_time.strftime('%H:%M')}."
                    elif is_date_query:
                        dias_semana = {
                            'Monday': 'segunda-feira',
                            'Tuesday': 'terça-feira',
                            'Wednesday': 'quarta-feira',
                            'Thursday': 'quinta-feira',
                            'Friday': 'sexta-feira',
                            'Saturday': 'sábado',
                            'Sunday': 'domingo'
                        }
                        day_name_en = brazil_time.strftime('%A')
                        day_name = dias_semana.get(day_name_en, day_name_en)

                        if is_dalua_user:
                            response = f"minha querida, hoje é {day_name}, {brazil_time.strftime('%d/%m/%Y')}"
                        else:
                            response = f"hoje é {day_name}, {brazil_time.strftime('%d/%m/%Y')}."

                    await message.channel.send(response)
                    update_relationship(str(message.author.id))
                    increment_daily_messages()
                    server_id = str(message.guild.id) if message.guild else "DM"
                    log_interaction(str(message.author.id), str(message.channel.id), server_id, content, response)
                    return

                # Verifica se deve ignorar (desinteresse claro)
                if should_ignore_message(content):
                    await message.channel.send(get_short_acknowledgment())
                    update_relationship(str(message.author.id))
                    increment_daily_messages()
                    return

                # Auto-aprende informações pessoais
                if get_bot_config("continuous_learning", "true") == "true":
                    auto_learn_personal_info(str(message.author.id), content)

                # PASSA user_id, user_name, channel_id e guild para o contexto personalizado
                reply = generate_ai_response(
                    content, 
                    system_prompt, 
                    str(message.author.id),
                    message.author.name,
                    str(message.channel.id),
                    message.guild
                )

                if not reply or not reply.strip():
                    await message.channel.send(".")
                    return

                # Verifica se é Dalua para ajustar a quantidade de mensagens
                is_dalua_user = is_dalua(str(message.author.id), message.author.name)

                num_messages = decide_message_count(content, reply, is_dalua_user)

                use_reply = participation.get("use_reply", False) or is_mentioned

                if num_messages == 1:
                    if use_reply and not is_dm:
                        await message.reply(reply.strip(), mention_author=False)
                    else:
                        await message.channel.send(reply.strip())
                else:
                    parts = split_response_naturally(reply.strip(), num_messages)

                    for i, part in enumerate(parts):
                        if part:
                            if i == 0 and use_reply and not is_dm:
                                await message.reply(part, mention_author=False)
                            else:
                                await message.channel.send(part)
                            if i < len(parts) - 1:
                                words_in_part = len(part.split())
                                if words_in_part <= 3:
                                    await asyncio.sleep(random.uniform(0.2, 0.5))
                                elif words_in_part <= 8:
                                    await asyncio.sleep(random.uniform(0.4, 0.9))
                                elif words_in_part <= 15:
                                    await asyncio.sleep(random.uniform(0.7, 1.3))
                                else:
                                    await asyncio.sleep(random.uniform(1.0, 1.8))

                add_to_conversation_context(str(message.channel.id), content, reply)

                update_relationship(str(message.author.id))
                increment_daily_messages()

                server_id = str(message.guild.id) if message.guild else "DM"
                log_interaction(str(message.author.id), str(message.channel.id), server_id, content, reply)

        except Exception as e:
            print(f"❌ Erro ao chamar IA: {e}")
            traceback.print_exc()

            error_msg = str(e)
            if "RESOURCE_EXHAUSTED" in error_msg or "quota" in error_msg.lower():
                await message.channel.send(
                    "⏱️ **Limite temporário atingido**\n\n"
                    "Você atingiu o limite de uso do Gemini. Aguarde alguns minutos e tente novamente."
                )
            else:
                await message.channel.send(".")

# ========== Sistema de Música ==========
import yt_dlp as youtube_dl

# Configuração do youtube-dl
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

# Fila de músicas por servidor
music_queues = {}

# ========== Comandos de Voz ==========
@bot.command(name="joinaku")
async def joinaku(ctx):
    """Bot entra no canal de voz do usuário"""
    if not ctx.author.voice:
        await ctx.send("❌ Você precisa estar em um canal de voz para usar este comando!")
        return

    if ctx.voice_client:
        await ctx.send("⚠️ Já estou conectado em um canal de voz! Use `!leaveaku` primeiro.")
        return

    channel = ctx.author.voice.channel

    try:
        await channel.connect()
        await ctx.send(f"🔊 Conectado ao canal de voz **{channel.name}**")
        print(f"🎤 Bot entrou no canal de voz: {channel.name}")
    except Exception as e:
        await ctx.send(f"❌ Erro ao entrar no canal de voz: {e}")

@bot.command(name="leaveaku")
async def leaveaku(ctx):
    """Bot sai do canal de voz"""
    if not ctx.voice_client:
        await ctx.send("❌ Não estou conectado em nenhum canal de voz!")
        return

    channel_name = ctx.voice_client.channel.name
    await ctx.voice_client.disconnect()
    await ctx.send(f"👋 Desconectado do canal **{channel_name}**")
    print(f"🎤 Bot saiu do canal de voz: {channel_name}")

@bot.command(name="play")
async def play(ctx, *, url: str):
    """Toca uma música do YouTube"""
    if not ctx.author.voice:
        await ctx.send("❌ Você precisa estar em um canal de voz!")
        return

    if not ctx.voice_client:
        try:
            await ctx.author.voice.channel.connect()
        except Exception as e:
            await ctx.send(f"❌ Erro ao conectar no canal de voz: {e}")
            return

    server_id = ctx.guild.id

    async with ctx.typing():
        try:
            player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)

            if server_id not in music_queues:
                music_queues[server_id] = []

            music_queues[server_id].append(player)

            if ctx.voice_client and not ctx.voice_client.is_playing():
                ctx.voice_client.play(music_queues[server_id].pop(0), after=lambda e: play_next(ctx))
                await ctx.send(f'🎵 Tocando agora: **{player.title}**')
            else:
                await ctx.send(f'➕ Adicionado à fila: **{player.title}**')
        except Exception as e:
            error_msg = str(e)
            if "ffmpeg" in error_msg.lower():
                await ctx.send("❌ FFmpeg não instalado! Por favor, aguarde enquanto o sistema é configurado e tente novamente.")
            else:
                await ctx.send(f"❌ Erro ao tocar música: {e}")

def play_next(ctx):
    """Toca a próxima música da fila"""
    server_id = ctx.guild.id

    if server_id in music_queues and music_queues[server_id]:
        next_song = music_queues[server_id].pop(0)
        ctx.voice_client.play(next_song, after=lambda e: play_next(ctx))
        asyncio.run_coroutine_threadsafe(ctx.send(f'🎵 Tocando agora: **{next_song.title}**'), bot.loop)

@bot.command(name="pause")
async def pause(ctx):
    """Pausa a música atual"""
    if not ctx.voice_client:
        await ctx.send("❌ Não estou conectado em um canal de voz")
        return

    if ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Música pausada")
    else:
        await ctx.send("❌ Nenhuma música tocando")

@bot.command(name="resume")
async def resume(ctx):
    """Retoma a música pausada"""
    if not ctx.voice_client:
        await ctx.send("❌ Não estou conectado em um canal de voz")
        return

    if ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Música retomada")
    else:
        await ctx.send("❌ Nenhuma música pausada")

@bot.command(name="skip")
async def skip(ctx):
    """Pula para a próxima música"""
    if not ctx.voice_client:
        await ctx.send("❌ Não estou conectado em um canal de voz")
        return

    if ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ Música pulada")
    else:
        await ctx.send("❌ Nenhuma música tocando")

@bot.command(name="queue")
async def queue(ctx):
    """Mostra a fila de músicas"""
    server_id = ctx.guild.id

    if server_id not in music_queues or not music_queues[server_id]:
        await ctx.send("📭 A fila está vazia")
        return

    embed = discord.Embed(title="🎵 Fila de Músicas", color=discord.Color.blue())

    for i, song in enumerate(music_queues[server_id][:10], 1):
        embed.add_field(name=f"{i}. {song.title}", value="\u200b", inline=False)

    if len(music_queues[server_id]) > 10:
        embed.set_footer(text=f"... e mais {len(music_queues[server_id]) - 10} música(s)")

    await ctx.send(embed=embed)

@bot.command(name="nowplaying", aliases=["np"])
async def nowplaying(ctx):
    """Mostra a música atual"""
    if not ctx.voice_client:
        await ctx.send("❌ Não estou conectado em um canal de voz")
        return

    if ctx.voice_client.is_playing() and hasattr(ctx.voice_client.source, 'title'):
        await ctx.send(f"🎵 Tocando: **{ctx.voice_client.source.title}**")
    else:
        await ctx.send("❌ Nenhuma música tocando")

@bot.command(name="voiceinfo")
async def voiceinfo(ctx):
    """Mostra informações sobre o canal de voz atual"""
    if not ctx.author.voice:
        await ctx.send("❌ Você não está em um canal de voz!")
        return

    channel = ctx.author.voice.channel
    members = channel.members

    embed = discord.Embed(
        title=f"🔊 Canal de Voz: {channel.name}",
        color=discord.Color.purple()
    )

    # Lista membros e suas atividades
    members_info = []
    for member in members:
        status = ""

        # Verifica se está mutado/ensurdecido
        if member.voice.self_mute:
            status += "🔇"
        if member.voice.self_deaf:
            status += "🔕"

        # Verifica se está transmitindo (streaming/compartilhando tela)
        if member.voice.self_stream:
            status += "📺"

        # Verifica se está usando vídeo
        if member.voice.self_video:
            status += "📹"

        # Verifica atividade atual (se estiver ouvindo música, jogando, etc)
        activities = []
        if member.activities:
            for activity in member.activities:
                if isinstance(activity, discord.Spotify):
                    activities.append(f"🎵 {activity.title} - {activity.artist}")
                elif isinstance(activity, discord.Game):
                    activities.append(f"🎮 {activity.name}")
                elif isinstance(activity, discord.Streaming):
                    activities.append(f"📡 {activity.name}")
                elif isinstance(activity, discord.Activity):
                    if activity.type == discord.ActivityType.listening:
                        activities.append(f"🎧 {activity.name}")
                    elif activity.type == discord.ActivityType.watching:
                        activities.append(f"📺 {activity.name}")

        member_text = f"{member.display_name} {status}"
        if activities:
            member_text += f"\n  └ {', '.join(activities)}"

        members_info.append(member_text)

    embed.add_field(
        name=f"👥 Membros ({len(members)})",
        value="\n".join(members_info) if members_info else "Nenhum membro",
        inline=False
    )

    embed.add_field(name="🔢 Limite", value=str(channel.user_limit) if channel.user_limit else "Ilimitado", inline=True)
    embed.add_field(name="📊 Bitrate", value=f"{channel.bitrate // 1000} kbps", inline=True)

    await ctx.send(embed=embed)

# ========== Sistema de Voz Automático ==========
@tasks.loop(minutes=random.randint(15, 45))
async def auto_join_voice():
    """Task que faz o bot entrar em canais de voz aleatoriamente"""
    try:
        if not bot.guilds:
            return

        # Verifica se já está conectado
        if bot.voice_clients:
            # 30% de chance de trocar de canal se já estiver conectado
            if random.random() > 0.3:
                return

            # Desconecta do canal atual
            for vc in bot.voice_clients:
                await vc.disconnect()

        guild = random.choice(bot.guilds)
        voice_channels = [ch for ch in guild.voice_channels if len(ch.members) > 0]

        if voice_channels:
            # Escolhe um canal com membros
            channel = random.choice(voice_channels)
            await channel.connect()
            print(f"🎤 Bot entrou automaticamente em: {channel.name}")

    except Exception as e:
        print(f"❌ Erro ao entrar automaticamente no canal de voz: {e}")

    # Define próximo intervalo aleatório
    auto_join_voice.change_interval(minutes=random.randint(30, 90))

# ========== Eventos de Voz ==========
@bot.event
async def on_voice_state_update(member, before, after):
    """Detecta mudanças em canais de voz e reage automaticamente"""
    # Ignora o próprio bot
    if member == bot.user:
        return

    # Alguém entrou em um canal de voz
    if before.channel is None and after.channel is not None:
        print(f"🎤 {member.display_name} entrou no canal de voz: {after.channel.name}")

        # 40% de chance do bot entrar junto se não estiver em nenhum canal
        if not bot.voice_clients and random.random() < 0.4:
            try:
                await after.channel.connect()
                print(f"🎤 Bot entrou automaticamente com {member.display_name}")
            except Exception as e:
                print(f"Erro ao entrar automaticamente: {e}")

    # Alguém saiu de um canal de voz
    elif before.channel is not None and after.channel is None:
        print(f"🎤 {member.display_name} saiu do canal de voz: {before.channel.name}")

        # Se o bot estiver no mesmo canal e ficar sozinho, sai também
        if bot.voice_clients:
            for vc in bot.voice_clients:
                if vc.channel == before.channel and len(vc.channel.members) == 1:
                    await vc.disconnect()
                    print(f"🎤 Bot saiu (canal vazio): {before.channel.name}")

    # Alguém mudou de canal
    elif before.channel != after.channel:
        print(f"🎤 {member.display_name} mudou de {before.channel.name} para {after.channel.name}")

# ========== Comandos ==========
class HelpView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.current_page = 0
        self.prefix = get_bot_config("prefix", "!")

    def get_page_embed(self, page: int):
        """Retorna o embed da página especificada"""
        if page == 0:
            embed = discord.Embed(
                title="🎭 Menu Principal - Akutagawa Bot",
                description=f"**Prefixo atual:** `{self.prefix}`\n\nNavegue pelas páginas para ver todos os comandos disponíveis!",
                color=discord.Color.dark_purple()
            )
            embed.add_field(
                name="📄 Páginas Disponíveis",
                value="**1️⃣** Configurações Gerais\n"
                      "**2️⃣** Memória e Aprendizado\n"
                      "**3️⃣** Relacionamentos\n"
                      "**4️⃣** Estatísticas e Logs\n"
                      "**5️⃣** Personalização Avançada\n"
                      "**6️⃣** Controle de Status e Presença\n"
                      "**7️⃣** Controle de Canais e Participação\n"
                      "**8️⃣** Comandos de Voz",
                inline=False
            )
            embed.add_field(
                name="💬 Como Interagir",
                value="• Mencione o bot, envie DM ou fale no canal padrão para conversar\n"
                      "• O bot pode iniciar conversas espontâneas\n"
                      "• Todas as configurações são globais (servidores + DMs)",
                inline=False
            )

        elif page == 1:
            embed = discord.Embed(
                title="⚙️ Página 1: Configurações Gerais",
                description="Comandos para personalizar o comportamento básico do bot",
                color=discord.Color.blue()
            )
            embed.add_field(
                name=f"`{self.prefix}setprefix <novo>`",
                value="**Descrição:** Altera o prefixo dos comandos\n"
                      "**Exemplo:** `!setprefix ?`\n"
                      "**Permissão:** Administrador",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}setpersonality <texto>`",
                value="**Descrição:** Define a personalidade completa do bot\n"
                      "**Exemplo:** `!setpersonality Você é um assistente amigável...`\n"
                      "**Permissão:** Administrador",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}settone <tom>`",
                value="**Descrição:** Ajusta o tom de conversa\n"
                      "**Opções:** formal, neutro, casual, sarcastico\n"
                      "**Exemplo:** `!settone casual`",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}setmood <humor>`",
                value="**Descrição:** Define o humor atual do bot\n"
                      "**Opções:** feliz, neutro, triste, irritado, reflexivo, sarcastico\n"
                      "**Exemplo:** `!setmood reflexivo`",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}setchannel <#canal>`",
                value="**Descrição:** Define canal padrão para conversas espontâneas e respostas automáticas\n"
                      "**Exemplo:** `!setchannel #geral`\n"
                      "**Permissão:** Administrador",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}config`",
                value="**Descrição:** Exibe todas as configurações atuais\n"
                      "**Exemplo:** `!config`",
                inline=False
            )

        elif page == 2:
            embed = discord.Embed(
                title="🧠 Página 2: Memória e Aprendizado",
                description="Gerencie o que o bot lembra sobre você",
                color=discord.Color.green()
            )
            embed.add_field(
                name=f"`{self.prefix}remember <chave> | <valor>`",
                value="**Descrição:** Adiciona ou atualiza uma memória\n"
                      "**Exemplo:** `!remember nome | João`\n"
                      "**Exemplo:** `!remember cor favorita | azul`",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}forget <chave>`",
                value="**Descrição:** Remove uma memória específica\n"
                      "**Exemplo:** `!forget nome`",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}memories [@usuário]`",
                value="**Descrição:** Lista todas as memórias armazenadas\n"
                      "**Exemplo:** `!memories` (suas memórias)\n"
                      "**Exemplo:** `!memories @João` (memórias do João)",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}clearmemories`",
                value="**Descrição:** Apaga TODAS as suas memórias\n"
                      "**Exemplo:** `!clearmemories`\n"
                      "**Atenção:** Esta ação é irreversível!",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}clearcontext`",
                value="**Descrição:** Limpa o contexto da conversa atual\n"
                      "**Exemplo:** `!clearcontext`\n"
                      "**Útil quando:** O bot ficar confuso na conversa",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}viewcontext`",
                value="**Descrição:** Mostra o contexto atual da conversa\n"
                      "**Exemplo:** `!viewcontext`",
                inline=False
            )
            embed.add_field(
                name="🤖 Aprendizado Automático",
                value="O bot aprende automaticamente quando você menciona:\n"
                      "• Idade, data de nascimento\n"
                      "• Comida, jogo, anime, música favorita\n"
                      "• Nome, cor favorita\n"
                      "• Outras informações pessoais",
                inline=False
            )

        elif page == 3:
            embed = discord.Embed(
                title="👥 Página 3: Relacionamentos",
                description="Sistema de níveis de proximidade com usuários",
                color=discord.Color.purple()
            )
            embed.add_field(
                name=f"`{self.prefix}relationship [@usuário]`",
                value="**Descrição:** Mostra nível de relacionamento\n"
                      "**Níveis:** 0-Desconhecido, 1-Conhecido, 2-Amigável, 3-Colega, 4-Amigo, 5-Amigo Próximo, 6-Confidente, 7-Amigo Íntimo, 8-Melhor Amigo, 9-Inseparável, 10-Alma Gêmea\n"
                      "**Exemplo:** `!relationship` (seu nível)\n"
                      "**Exemplo:** `!relationship @João`",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}setrelationship @usuário <0-10>`",
                value="**Descrição:** Ajusta nível manualmente\n"
                      "**Exemplo:** `!setrelationship @João 4`\n"
                      "**Permissão:** Administrador",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}toprelationships`",
                value="**Descrição:** Ranking dos usuários mais próximos\n"
                      "**Exemplo:** `!toprelationships`",
                inline=False
            )
            embed.add_field(
                name="ℹ️ Como Funciona (Escala Longa)",
                value="• Cada interação aumenta o nível automaticamente\n"
                      "• 5+ interações = Conhecido (nível 1)\n"
                      "• 30+ interações = Amigável (nível 2)\n"
                      "• 100+ interações = Colega (nível 3)\n"
                      "• 300+ interações = Amigo (nível 4)\n"
                      "• 600+ interações = Amigo Próximo (nível 5)\n"
                      "• 1000+ interações = Confidente (nível 6)\n"
                      "• 1600+ interações = Amigo Íntimo (nível 7)\n"
                      "• 2400+ interações = Melhor Amigo (nível 8)\n"
                      "• 3200+ interações = Inseparável (nível 9)\n"
                      "• 4000+ interações = Alma Gêmea (nível 10)",
                inline=False
            )

        elif page == 4:
            embed = discord.Embed(
                title="📊 Página 4: Estatísticas e Logs",
                description="Acompanhe a atividade e histórico do bot",
                color=discord.Color.gold()
            )
            embed.add_field(
                name=f"`{self.prefix}stats`",
                value="**Descrição:** Estatísticas gerais do bot\n"
                      "**Mostra:** Mensagens hoje, total de interações, servidores, humor\n"
                      "**Exemplo:** `!stats`",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}history [filtro]`",
                value="**Descrição:** Histórico de interações (últimas 10)\n"
                      "**Exemplo:** `!history` (geral)\n"
                      "**Exemplo:** `!history #geral` (por canal)\n"
                      "**Exemplo:** `!history João` (por usuário)",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}activity`",
                value="**Descrição:** Atividade dos últimos 7 dias\n"
                      "**Mostra:** Mensagens enviadas por dia\n"
                      "**Exemplo:** `!activity`",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}userstats [@usuário]`",
                value="**Descrição:** Estatísticas detalhadas de um usuário\n"
                      "**Mostra:** Interações, nível, última atividade\n"
                      "**Exemplo:** `!userstats @João`",
                inline=False
            )

        elif page == 5:
            embed = discord.Embed(
                title="🎨 Página 5: Personalização Avançada",
                description="Recursos avançados de customização",
                color=discord.Color.red()
            )
            embed.add_field(
                name=f"`{self.prefix}setname <nome>`",
                value="**Descrição:** Altera o nome do bot no Discord\n"
                      "**Exemplo:** `!setname Ryūnosuke`\n"
                      "**Permissão:** Administrador\n"
                      "**Nota:** Pode ter delay de alguns minutos",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}profile`",
                value="**Descrição:** Exibe seu perfil completo\n"
                      "**Mostra:** Nível, memórias, interações, última atividade\n"
                      "**Exemplo:** `!profile`",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}serverinfo`",
                value="**Descrição:** Informações do servidor atual\n"
                      "**Mostra:** Membros, canais, configurações do bot\n"
                      "**Exemplo:** `!serverinfo`",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}resetconfig`",
                value="**Descrição:** Restaura configurações padrão\n"
                      "**Exemplo:** `!resetconfig`\n"
                      "**Permissão:** Administrador\n"
                      "**Atenção:** Não apaga memórias ou histórico",
                inline=False
            )

        elif page == 6:
            embed = discord.Embed(
                title="🎭 Página 6: Controle de Status e Presença",
                description="Gerencie a aparência e atividade do bot no Discord",
                color=discord.Color.magenta()
            )
            embed.add_field(
                name=f"`{self.prefix}setstatus <status>`",
                value="**Descrição:** Altera o status visual do bot\n"
                      "**Opções:** online, ausente, ocupado, invisivel\n"
                      "**Exemplo:** `!setstatus ausente`\n"
                      "**Permissão:** Administrador",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}setactivity <tipo> <texto>`",
                value="**Descrição:** Define a atividade do bot\n"
                      "**Tipos:** jogando, ouvindo, assistindo, transmitindo\n"
                      "**Exemplo:** `!setactivity ouvindo Spotify`\n"
                      "**Exemplo:** `!setactivity jogando Genshin Impact`\n"
                      "**Permissão:** Administrador",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}setstatustext <mensagem>`",
                value="**Descrição:** Define mensagem de status customizada\n"
                      "**Exemplo:** `!setstatustext contemplando a existência 🌙`\n"
                      "**Permissão:** Administrador",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}autorotate`",
                value="**Descrição:** Ativa/desativa rotação automática de atividades\n"
                      "**Funcionalidade:** Alterna entre 30 atividades variadas a cada 50 minutos\n"
                      "**Tipos de atividades:** Músicas, frases filosóficas, jogos\n"
                      "**Exemplo:** `!autorotate on` (ativar)\n"
                      "**Exemplo:** `!autorotate off` (desativar)\n"
                      "**Permissão:** Administrador",
                inline=False
            )

        elif page == 7:
            embed = discord.Embed(
                title="📡 Página 7: Controle de Canais e Participação",
                description="Configure onde e como o bot interage nos servidores",
                color=discord.Color.teal()
            )
            embed.add_field(
                name=f"`{self.prefix}respondall <on/off>`",
                value="**Descrição:** Ativa/desativa participação inteligente em todos os canais\n"
                      "**Funcionalidade:** Quando ativo, o bot participa de conversas relevantes em qualquer canal\n"
                      "**Critérios de participação:**\n"
                      "• Tópicos de interesse (livros, filosofia, gatos, etc)\n"
                      "• Perguntas relacionadas aos gostos do Akutagawa\n"
                      "• Discussões profundas\n"
                      "• Chance aleatória baseada no humor atual\n"
                      "**Exemplo:** `!respondall on`\n"
                      "**Permissão:** Administrador",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}blockchannel <#canal>`",
                value="**Descrição:** Bloqueia um canal para o bot não responder\n"
                      "**Exemplo:** `!blockchannel #off-topic`\n"
                      "**Nota:** O bot nunca responderá neste canal, mesmo se mencionado\n"
                      "**Permissão:** Administrador",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}unblockchannel <#canal>`",
                value="**Descrição:** Desbloqueia um canal previamente bloqueado\n"
                      "**Exemplo:** `!unblockchannel #off-topic`\n"
                      "**Permissão:** Administrador",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}blockedchannels`",
                value="**Descrição:** Lista todos os canais bloqueados no servidor\n"
                      "**Exemplo:** `!blockedchannels`",
                inline=False
            )
            embed.add_field(
                name="🤖 Como Funciona a Participação Inteligente",
                value="**Quando ATIVO (`!respondall on`):**\n"
                      "• O bot analisa cada mensagem em canais não bloqueados\n"
                      "• Participa quando detecta tópicos relevantes ou discussões interessantes\n"
                      "• Usa **reply** (resposta à mensagem) quando apropriado\n"
                      "• Varia participação baseado no humor (reflexivo = mais ativo)\n\n"
                      "**Quando DESATIVO (`!respondall off`):**\n"
                      "• Apenas responde quando mencionado diretamente\n"
                      "• Sempre responde em DMs\n"
                      "• Responde no canal padrão (se configurado)",
                inline=False
            )

        else:  # page == 8
            embed = discord.Embed(
                title="🎤 Página 8: Comandos de Voz",
                description="Comandos para interação em canais de voz",
                color=discord.Color.orange()
            )
            embed.add_field(
                name=f"`{self.prefix}joinaku`",
                value="**Descrição:** Faz o bot entrar no seu canal de voz atual\n"
                      "**Funcionalidade:** O bot se conecta ao canal de voz onde você está\n"
                      "**Exemplo:** `!joinaku`\n"
                      "**Requisito:** Você precisa estar em um canal de voz",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}leaveaku`",
                value="**Descrição:** Faz o bot sair do canal de voz\n"
                      "**Exemplo:** `!leaveaku`",
                inline=False
            )
            embed.add_field(
                name=f"`{self.prefix}voiceinfo`",
                value="**Descrição:** Mostra informações detalhadas do canal de voz\n"
                      "**Informações exibidas:**\n"
                      "• Lista de membros conectados\n"
                      "• Status de cada membro (mutado, ensurdecido, streaming)\n"
                      "• Atividades atuais (música no Spotify, jogos, etc)\n"
                      "• Bitrate e limite do canal\n"
                      "**Exemplo:** `!voiceinfo`\n"
                      "**Requisito:** Você precisa estar em um canal de voz",
                inline=False
            )
            embed.add_field(
                name="🎵 Detecção de Atividades",
                value="O bot detecta automaticamente:\n"
                      "• 🎵 Músicas no Spotify\n"
                      "• 🎮 Jogos em execução\n"
                      "• 📡 Transmissões/Streams\n"
                      "• 🎧 Outras atividades de áudio\n"
                      "• 📺 Compartilhamento de tela\n"
                      "• 📹 Câmera ligada",
                inline=False
            )
            embed.add_field(
                name="ℹ️ Funcionalidades Futuras",
                value="O bot pode ser expandido para:\n"
                      "• Reagir quando alguém entra/sai do canal\n"
                      "• Comentar sobre músicas que os usuários estão ouvindo\n"
                      "• Participar de conversas em voz (com integração de speech-to-text)\n"
                      "• Tocar músicas (requer biblioteca adicional)",
                inline=False
            )

        embed.set_footer(text=f"Página {page + 1}/9 | Bot Akutagawa v2.0 | {len(bot.guilds)} servidor(es)")
        return embed

    @discord.ui.button(label="◀️ Anterior", style=discord.ButtonStyle.primary, disabled=True)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("❌ Apenas quem solicitou pode navegar!", ephemeral=True)
            return

        self.current_page = max(0, self.current_page - 1)
        await self.update_message(interaction)

    @discord.ui.button(label="Próximo ▶️", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("❌ Apenas quem solicitou pode navegar!", ephemeral=True)
            return

        self.current_page = min(8, self.current_page + 1)
        await self.update_message(interaction)

    @discord.ui.button(label="🏠 Início", style=discord.ButtonStyle.success)
    async def home_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("❌ Apenas quem solicitou pode navegar!", ephemeral=True)
            return

        self.current_page = 0
        await self.update_message(interaction)

    @discord.ui.button(label="❌ Fechar", style=discord.ButtonStyle.danger)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("❌ Apenas quem solicitou pode fechar!", ephemeral=True)
            return

        await interaction.message.delete()
        self.stop()

    async def update_message(self, interaction: discord.Interaction):
        # Atualiza botões
        self.children[0].disabled = (self.current_page == 0)
        self.children[1].disabled = (self.current_page == 8)

        embed = self.get_page_embed(self.current_page)
        await interaction.response.edit_message(embed=embed, view=self)

@bot.command(name="help", aliases=["ajuda", "comandos"])
async def help_command(ctx):
    """Menu de ajuda interativo e completo com páginas"""
    view = HelpView(ctx)
    embed = view.get_page_embed(0)
    await ctx.send(embed=embed, view=view)

@bot.command(name="respondall")
@commands.has_permissions(administrator=True)
async def respondall(ctx, status: str = None):
    """Ativa ou desativa participação inteligente em todos os canais"""
    if status is None:
        current = get_bot_config("respond_all_channels", "false")
        status_text = "ativada ✅" if current == "true" else "desativada ❌"
        await ctx.send(f"🤖 **Participação em Todos os Canais**\n\n"
                      f"Status atual: **{status_text}**\n\n"
                      f"Use `!respondall on` para ativar ou `!respondall off` para desativar.\n\n"
                      f"**Quando ativo:** O bot participa inteligentemente de conversas relevantes em qualquer canal (exceto bloqueados).\n"
                      f"**Quando desativo:** Apenas responde quando mencionado, em DMs, ou no canal padrão.")
        return

    status = status.lower()

    if status in ["on", "ativar", "ligar", "sim", "yes"]:
        set_bot_config("respond_all_channels", "true")
        await ctx.send("✅ **Participação em todos os canais ATIVADA!**\n\n"
                      "O bot agora participará inteligentemente de conversas quando:\n"
                      "• Detectar tópicos de interesse (livros, filosofia, gatos, etc)\n"
                      "• Houver perguntas ou discussões profundas relevantes\n"
                      "• Sentir que pode contribuir para a conversa\n\n"
                      "**Dica:** Use `!blockchannel #canal` para bloquear canais específicos.")

    elif status in ["off", "desativar", "desligar", "nao", "não", "no"]:
        set_bot_config("respond_all_channels", "false")
        await ctx.send("✅ **Participação em todos os canais DESATIVADA!**\n\n"
                      "O bot agora apenas responderá:\n"
                      "• Quando for mencionado diretamente\n"
                      "• Em mensagens diretas (DM)\n"
                      "• No canal padrão (se configurado)")
    else:
        await ctx.send("❌ Status inválido! Use `on` ou `off`.\n"
                      "**Exemplos:**\n"
                      "• `!respondall on` - ativa participação inteligente\n"
                      "• `!respondall off` - desativa\n"
                      "• `!respondall` - verifica o status atual")

@bot.command(name="blockchannel")
@commands.has_permissions(administrator=True)
async def blockchannel_cmd(ctx, channel: discord.TextChannel = None):
    """Bloqueia um canal para o bot não responder"""
    if channel is None:
        await ctx.send("❌ Você precisa especificar um canal!\n"
                      f"**Exemplo:** `{get_bot_config('prefix', '!')}blockchannel #off-topic`")
        return

    server_id = str(ctx.guild.id) if ctx.guild else "DM"
    block_channel(str(channel.id), server_id)

    await ctx.send(f"🚫 Canal {channel.mention} **bloqueado**!\n\n"
                  "O bot não responderá a mensagens neste canal, mesmo se for mencionado.")

@bot.command(name="unblockchannel")
@commands.has_permissions(administrator=True)
async def unblockchannel_cmd(ctx, channel: discord.TextChannel = None):
    """Desbloqueia um canal"""
    if channel is None:
        await ctx.send("❌ Você precisa especificar um canal!\n"
                      f"**Exemplo:** `{get_bot_config('prefix', '!')}unblockchannel #off-topic`")
        return

    if unblock_channel(str(channel.id)):
        await ctx.send(f"✅ Canal {channel.mention} **desbloqueado**!\n\n"
                      "O bot voltará a responder neste canal conforme as configurações.")
    else:
        await ctx.send(f"⚠️ O canal {channel.mention} não estava bloqueado.")

@bot.command(name="blockedchannels")
async def blockedchannels_cmd(ctx):
    """Lista todos os canais bloqueados no servidor"""
    if not ctx.guild:
        await ctx.send("❌ Este comando só funciona em servidores!")
        return

    blocked = get_blocked_channels(str(ctx.guild.id))

    if not blocked:
        await ctx.send("📭 Nenhum canal está bloqueado neste servidor.")
        return

    embed = discord.Embed(
        title="🚫 Canais Bloqueados",
        description=f"Total: {len(blocked)} canal(is)",
        color=discord.Color.red()
    )

    channels_list = []
    for channel_id in blocked:
        channel = bot.get_channel(int(channel_id))
        if channel:
            channels_list.append(channel.mention)
        else:
            channels_list.append(f"Canal desconhecido (ID: {channel_id})")

    embed.add_field(name="Canais", value="\n".join(channels_list) or "Nenhum", inline=False)

    await ctx.send(embed=embed)

@respondall.error
@blockchannel_cmd.error
@unblockchannel_cmd.error
async def channel_control_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Você precisa ser administrador para usar este comando!")

@bot.command(name="testtime")
async def testtime(ctx):
    """Comando temporário pra testar o horário"""
    brazil_time = get_brazil_time()
    hora = brazil_time.strftime('%H:%M')
    data = brazil_time.strftime('%d/%m/%Y')
    await ctx.send(f"Teste: Agora são {hora} de {data} em Brasília. (UTC: {brazil_time.strftime('%H:%M')} ajustado)")

@bot.command(name="setprefix")
@commands.has_permissions(administrator=True)
async def setprefix(ctx, new_prefix: str):
    """Altera o prefixo do bot"""
    if len(new_prefix) > 5:
        await ctx.send("❌ Prefixo muito longo! Use no máximo 5 caracteres.")
        return

    set_bot_config("prefix", new_prefix)
    await ctx.send(f"✅ Prefixo alterado para: `{new_prefix}`")

@bot.command(name="setpersonality")
@commands.has_permissions(administrator=True)
async def setpersonality_cmd(ctx, *, text: str):
    """Define a personalidade do bot"""
    set_personality(text)
    await ctx.send(f"✅ Personalidade atualizada!\n\n**Nova personalidade:**\n{text[:500]}...")

@bot.command(name="settone")
async def settone(ctx, tone: str):
    """Define o tom de conversa"""
    valid_tones = ["formal", "neutro", "casual", "sarcastico"]
    tone = tone.lower()

    if tone not in valid_tones:
        await ctx.send(f"❌ Tom inválido! Opções: {', '.join(valid_tones)}")
        return

    set_bot_config("tone", tone)
    await ctx.send(f"✅ Tom de conversa definido como: **{tone}**")

@bot.command(name="setmood")
async def setmood(ctx, mood: str):
    """Define o humor do bot"""
    valid_moods = ["feliz", "neutro", "triste", "irritado", "reflexivo", "sarcastico"]
    mood = mood.lower()

    if mood not in valid_moods:
        await ctx.send(f"❌ Humor inválido! Opções: {', '.join(valid_moods)}")
        return

    set_bot_config("current_mood", mood)
    await ctx.send(f"✅ Humor atual definido como: **{mood}**")

@bot.command(name="setstatus")
@commands.has_permissions(administrator=True)
async def setstatus(ctx, status: str):
    """Altera o status do bot (online, ausente, ocupado, invisivel)"""
    status = status.lower()
    status_map = {
        "online": discord.Status.online,
        "disponivel": discord.Status.online,
        "ausente": discord.Status.idle,
        "idle": discord.Status.idle,
        "ocupado": discord.Status.dnd,
        "dnd": discord.Status.dnd,
        "naopertube": discord.Status.dnd,
        "invisivel": discord.Status.invisible,
        "offline": discord.Status.invisible
    }

    if status not in status_map:
        await ctx.send(f"❌ Status inválido! Opções: online, ausente, ocupado, invisivel")
        return

    try:
        await bot.change_presence(status=status_map[status])
        status_names = {
            discord.Status.online: "Online/Disponível",
            discord.Status.idle: "Ausente",
            discord.Status.dnd: "Não Perturbe/Ocupado",
            discord.Status.invisible: "Invisível"
        }
        await ctx.send(f"✅ Status alterado para: **{status_names[status_map[status]]}**")
    except Exception as e:
        await ctx.send(f"❌ Erro ao alterar status: {e}")

@bot.command(name="setactivity")
@commands.has_permissions(administrator=True)
async def setactivity(ctx, tipo: str, *, texto: str = None):
    """Define a atividade do bot (jogando, ouvindo, assistindo, transmitindo)"""
    tipo = tipo.lower()

    # Se texto for None, remove a atividade
    if texto is None or (texto and texto.lower() in ["none", "nenhum", "remover"]):
        await bot.change_presence(activity=None)
        await ctx.send("✅ Atividade removida!")
        return

    activity_types = {
        "jogando": discord.ActivityType.playing,
        "playing": discord.ActivityType.playing,
        "ouvindo": discord.ActivityType.listening,
        "listening": discord.ActivityType.listening,
        "assistindo": discord.ActivityType.watching,
        "watching": discord.ActivityType.watching,
        "transmitindo": discord.ActivityType.streaming,
        "streaming": discord.ActivityType.streaming
    }

    if tipo not in activity_types:
        await ctx.send(f"❌ Tipo inválido! Opções: jogando, ouvindo, assistindo, transmitindo")
        return

    try:
        activity = discord.Activity(type=activity_types[tipo], name=texto)
        await bot.change_presence(activity=activity)

        tipo_names = {
            "jogando": "Jogando",
            "playing": "Jogando",
            "ouvindo": "Ouvindo",
            "listening": "Ouvindo",
            "assistindo": "Assistindo",
            "watching": "Assistindo",
            "transmitindo": "Transmitindo",
            "streaming": "Transmitindo"
        }
        await ctx.send(f"✅ Atividade definida: **{tipo_names.get(tipo, tipo)} {texto}**")
    except Exception as e:
        await ctx.send(f"❌ Erro ao definir atividade: {e}")

@bot.command(name="setstatustext")
@commands.has_permissions(administrator=True)
async def setstatustext(ctx, *, texto: str = None):
    """Define a mensagem de status customizada"""
    if texto is None or (texto and texto.lower() in ["none", "nenhum", "remover"]):
        await bot.change_presence(activity=None)
        await ctx.send("✅ Mensagem de status removida!")
        return

    try:
        # Usa o tipo "custom" para status personalizado (aparece como "Status personalizado")
        activity = discord.CustomActivity(name=texto)
        await bot.change_presence(activity=activity)
        await ctx.send(f"✅ Mensagem de status definida: **{texto}**")
    except Exception as e:
        await ctx.send(f"❌ Erro ao definir mensagem: {e}")

@bot.command(name="autorotate")
@commands.has_permissions(administrator=True)
async def autorotate(ctx, status: str = None):
    """Ativa ou desativa rotação automática de atividades a cada 50 minutos"""
    if status is None:
        is_running = auto_rotate_activity.is_running()
        status_text = "ativada ✅" if is_running else "desativada ❌"
        await ctx.send(f"🔄 **Rotação Automática de Atividades**\n\n"
                      f"Status atual: **{status_text}**\n\n"
                      f"Use `!autorotate on` para ativar ou `!autorotate off` para desativar.\n\n"
                      f"**Funcionalidade:** Alterna entre 30 atividades variadas (músicas, frases, jogos) a cada 50 minutos.")
        return

    status = status.lower()

    if status in ["on", "ativar", "ligar", "sim", "yes"]:
        if auto_rotate_activity.is_running():
            await ctx.send("⚠️ A rotação automática já está ativada!")
            return

        auto_rotate_activity.start()
        await ctx.send("✅ **Rotação automática ativada!**\n\n"
                      "O bot agora alternará entre 30 atividades diferentes a cada 50 minutos:\n"
                      "• 10 músicas/bandas\n"
                      "• 10 frases filosóficas\n"
                      "• 10 jogos")

    elif status in ["off", "desativar", "desligar", "nao", "não", "no"]:
        if not auto_rotate_activity.is_running():
            await ctx.send("⚠️ A rotação automática já está desativada!")
            return

        auto_rotate_activity.stop()
        await ctx.send("✅ Rotação automática desativada!\n\n"
                      "Use `!setactivity` ou `!setstatustext` para definir uma atividade manual.")
    else:
        await ctx.send("❌ Status inválido! Use `on` ou `off`.\n"
                      "**Exemplos:**\n"
                      "• `!autorotate on` - ativa a rotação\n"
                      "• `!autorotate off` - desativa a rotação\n"
                      "• `!autorotate` - verifica o status atual")

@bot.command(name="setchannel")
@commands.has_permissions(administrator=True)
async def setchannel(ctx, channel: discord.TextChannel = None):
    """Define ou remove o canal padrão de interação"""
    current_channel_id = get_bot_config("default_channel")

    if channel is None or (current_channel_id and str(channel.id) == current_channel_id):
        # Se nenhum canal for especificado ou o canal atual for o mesmo, remove a configuração
        if current_channel_id:
            set_bot_config("default_channel", "")
            await ctx.send(f"✅ Canal padrão removido. O bot não responderá automaticamente a mensagens em canais específicos até um novo canal ser configurado.")
        else:
            await ctx.send("❌ Nenhum canal padrão está configurado para remover.")
        return

    # Caso contrário, define o novo canal
    set_bot_config("default_channel", str(channel.id))
    await ctx.send(f"✅ Canal padrão definido: {channel.mention}. O bot responderá a quase todas as mensagens neste canal.")

@bot.command(name="setname")
@commands.has_permissions(administrator=True)
async def setname(ctx, *, name: str):
    """Altera o nome do bot"""
    try:
        await bot.user.edit(username=name)
        set_bot_config("bot_name", name)
        await ctx.send(f"✅ Nome alterado para: **{name}**")
    except Exception as e:
        await ctx.send(f"❌ Erro ao alterar nome: {e}")

@bot.command(name="setdalua")
@commands.has_permissions(administrator=True)
async def setdalua(ctx, user: discord.User):
    """Define um usuário como Dalua/Evillyn"""
    add_or_update_fact(str(user.id), "é_dalua", "true")
    add_or_update_fact(str(user.id), "relacionamento", "namorada_do_akutagawa")
    add_or_update_fact(str(user.id), "pronomes", "ele/dele e ela/dela")
    add_or_update_fact(str(user.id), "observações", "assexual, arromântica, demigirl, usa óculos, mãe do Romeu (gato)")

    await ctx.send(f"✅ {user.mention} foi configurado(a) como Dalua/Evillyn no sistema!")

@bot.command(name="config")
async def config(ctx):
    """Exibe todas as configurações atuais"""
    embed = discord.Embed(
        title="⚙️ Configurações Atuais",
        color=discord.Color.blue()
    )

    configs = {
        "Prefixo": get_bot_config("prefix", "!"),
        "Tom": get_bot_config("tone", "neutro"),
        "Humor": get_bot_config("current_mood", "neutro"),
        "Nome": get_bot_config("bot_name", "Akutagawa"),
        "Duração da Memória": get_bot_config("memory_duration", "longo"),
        "Aprendizado Contínuo": get_bot_config("continuous_learning", "true")
    }

    for key, value in configs.items():
        embed.add_field(name=key, value=value, inline=True)

    channel_id = get_bot_config("default_channel")
    if channel_id:
        channel = bot.get_channel(int(channel_id))
        embed.add_field(name="Canal Padrão", value=channel.mention if channel else "Não encontrado", inline=True)

    await ctx.send(embed=embed)

@bot.command(name="remember")
async def remember(ctx, *, args: str):
    """Adiciona ou atualiza uma memória"""
    if "|" not in args:
        await ctx.send(f"❌ Formato incorreto! Use: `{get_bot_config('prefix', '!')}remember chave | valor`")
        return

    key, value = args.split("|", 1)
    key = key.strip()
    value = value.strip()

    if not key or not value:
        await ctx.send("❌ Chave e valor não podem estar vazios!")
        return

    add_or_update_fact(str(ctx.author.id), key, value)
    await ctx.send(f"✅ Memória salva: **{key}** = {value}")

@bot.command(name="forget")
async def forget(ctx, *, key: str):
    """Remove uma memória"""
    key = key.strip()

    if delete_fact(str(ctx.author.id), key):
        await ctx.send(f"🗑️ Memória **{key}** removida com sucesso!")
    else:
        await ctx.send(f"❌ Memória **{key}** não encontrada.")

@bot.command(name="memories")
async def memories(ctx, member: discord.Member = None):
    """Lista todas as memórias"""
    target = member or ctx.author
    facts = get_user_facts(str(target.id))

    if not facts:
        await ctx.send(f"📭 {target.mention} ainda não tem memórias salvas.")
        return

    embed = discord.Embed(
        title=f"🧠 Memórias de {target.display_name}",
        description=f"Total: {len(facts)} memória(s)",
        color=discord.Color.green()
    )

    for key, value in facts[:25]:
        embed.add_field(name=key, value=value, inline=False)

    if len(facts) > 25:
        embed.set_footer(text=f"Mostrando 25 de {len(facts)} memórias")

    await ctx.send(embed=embed)

@bot.command(name="setmemoryduration")
@commands.has_permissions(administrator=True)
async def setmemoryduration(ctx, duration: str):
    """Define a duração da memória"""
    valid_durations = ["curto", "medio", "longo"]
    duration = duration.lower()

    if duration not in valid_durations:
        await ctx.send(f"❌ Duração inválida! Opções: {', '.join(valid_durations)}")
        return

    set_bot_config("memory_duration", duration)
    await ctx.send(f"✅ Duração da memória definida como: **{duration} prazo**")

@bot.command(name="togglelearning")
async def togglelearning(ctx):
    """Ativa/desativa aprendizado contínuo"""
    current = get_bot_config("continuous_learning", "true")
    new_value = "false" if current == "true" else "true"
    set_bot_config("continuous_learning", new_value)

    status = "ativado" if new_value == "true" else "desativado"
    await ctx.send(f"✅ Aprendizado contínuo **{status}**!")

@bot.command(name="relationship")
async def relationship(ctx, member: discord.Member = None):
    """Mostra nível de relacionamento"""
    target = member or ctx.author
    level, interactions = get_relationship(str(target.id))

    level_names = {
        0: "Desconhecido",
        1: "Conhecido",
        2: "Amigável",
        3: "Colega",
        4: "Amigo",
        5: "Amigo Próximo",
        6: "Confidente",
        7: "Amigo Íntimo",
        8: "Melhor Amigo",
        9: "Inseparável",
        10: "Alma Gêmea"
    }

    embed = discord.Embed(
        title=f"👥 Relacionamento com {target.display_name}",
        color=discord.Color.purple()
    )

    embed.add_field(name="Nível", value=f"{level}/10 - {level_names.get(level, 'Desconhecido')}", inline=True)
    embed.add_field(name="Interações", value=str(interactions), inline=True)

    await ctx.send(embed=embed)

@bot.command(name="setrelationship")
@commands.has_permissions(administrator=True)
async def setrelationship(ctx, member: discord.Member, level: int):
    """Ajusta nível de relacionamento manualmente"""
    if not 0 <= level <= 10: # Ajustado para 10 níveis
        await ctx.send("❌ Nível deve estar entre 0 e 10!")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO relationships (user_id, level) 
        VALUES (?, ?)
        ON CONFLICT(user_id) 
        DO UPDATE SET level = excluded.level
    """, (str(member.id), level))
    conn.commit()
    conn.close()

    await ctx.send(f"✅ Nível de relacionamento com {member.mention} definido para: **{level}/10**")

@bot.command(name="toprelationships")
async def toprelationships(ctx):
    """Mostra ranking de usuários mais próximos"""
    stats = get_stats()

    embed = discord.Embed(
        title="🏆 Usuários Mais Próximos",
        color=discord.Color.gold()
    )

    if not stats["top_users"]:
        await ctx.send("📭 Ainda não há relacionamentos registrados.")
        return

    for i, (user_id, level, interactions) in enumerate(stats["top_users"], 1):
        user = await bot.fetch_user(int(user_id))
        embed.add_field(
            name=f"{i}. {user.display_name}",
            value=f"Nível: {level}/10 | {interactions} interações", # Ajustado para 10 níveis
            inline=False
        )

    await ctx.send(embed=embed)

@bot.command(name="stats")
async def stats(ctx):
    """Exibe estatísticas gerais do bot"""
    stats_data = get_stats()

    embed = discord.Embed(
        title="📊 Estatísticas do Bot",
        color=discord.Color.blue()
    )

    embed.add_field(name="Mensagens Hoje", value=str(stats_data["messages_today"]), inline=True)
    embed.add_field(name="Total de Interações", value=str(stats_data["total_interactions"]), inline=True)
    embed.add_field(name="Servidores", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Humor Atual", value=get_bot_config("current_mood", "neutro"), inline=True)

    await ctx.send(embed=embed)

@bot.command(name="history")
async def history(ctx, target: Optional[str] = None):
    """Mostra histórico de interações"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if target:
        # Filtra por canal ou usuário
        if target.startswith("<#"):
            channel_id = target.strip("<#>")
            cursor.execute("SELECT * FROM interaction_history WHERE channel_id = ? ORDER BY timestamp DESC LIMIT 10", (channel_id,))
        else:
            cursor.execute("SELECT * FROM interaction_history WHERE user_id LIKE ? ORDER BY timestamp DESC LIMIT 10", (f"%{target}%",))
    else:
        cursor.execute("SELECT * FROM interaction_history ORDER BY timestamp DESC LIMIT 10")

    history = cursor.fetchall()
    conn.close()

    if not history:
        await ctx.send("📭 Nenhum histórico encontrado.")
        return

    embed = discord.Embed(
        title="📜 Histórico de Interações",
        description=f"Últimas {len(history)} interações",
        color=discord.Color.green()
    )

    for _, user_id, channel_id, server_id, msg, response, timestamp in history[:10]:
        user = await bot.fetch_user(int(user_id))
        embed.add_field(
            name=f"{user.display_name} - {timestamp[:16]}",
            value=f"**Msg:** {msg[:50]}...\n**Resp:** {response[:50]}...",
            inline=False
        )

    await ctx.send(embed=embed)

@bot.command(name="activity")
async def activity(ctx):
    """Mostra atividade recente do bot"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT date, messages_sent FROM stats ORDER BY date DESC LIMIT 7")
    activity = cursor.fetchall()
    conn.close()

    if not activity:
        await ctx.send("📭 Nenhuma atividade registrada.")
        return

    embed = discord.Embed(
        title="📈 Atividade Recente (Últimos 7 Dias)",
        color=discord.Color.blue()
    )

    for date, messages in activity:
        embed.add_field(name=date, value=f"{messages} mensagens", inline=True)

    await ctx.send(embed=embed)

@bot.command(name="clearmemories")
async def clearmemories(ctx):
    """Apaga TODAS as memórias do usuário"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM facts WHERE user_id = ?", (str(ctx.author.id),))
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted_count > 0:
        await ctx.send(f"🗑️ **{deleted_count}** memória(s) apagada(s) com sucesso!")
    else:
        await ctx.send("📭 Você não tinha memórias armazenadas.")

@bot.command(name="userstats")
async def userstats(ctx, member: discord.Member = None):
    """Estatísticas detalhadas de um usuário"""
    target = member or ctx.author

    # Busca dados
    level, interactions = get_relationship(str(target.id))
    facts = get_user_facts(str(target.id))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT last_interaction FROM relationships WHERE user_id = ?", (str(target.id),))
    result = cursor.fetchone()
    last_interaction = result[0] if result else "Nunca"

    cursor.execute("SELECT COUNT(*) FROM interaction_history WHERE user_id = ?", (str(target.id),))
    total_messages = cursor.fetchone()[0]
    conn.close()

    level_names = {
        0: "Desconhecido",
        1: "Conhecido",
        2: "Amigável",
        3: "Colega",
        4: "Amigo",
        5: "Amigo Próximo",
        6: "Confidente",
        7: "Amigo Íntimo",
        8: "Melhor Amigo",
        9: "Inseparável",
        10: "Alma Gêmea"
    }

    embed = discord.Embed(
        title=f"📊 Estatísticas de {target.display_name}",
        color=discord.Color.blue()
    )

    embed.add_field(name="Nível de Relacionamento", value=f"{level}/10 - {level_names.get(level, 'Desconhecido')}", inline=True) # Ajustado para 10 níveis
    embed.add_field(name="Interações Totais", value=str(interactions), inline=True)
    embed.add_field(name="Mensagens no Histórico", value=str(total_messages), inline=True)
    embed.add_field(name="Memórias Armazenadas", value=str(len(facts)), inline=True)
    embed.add_field(name="Última Interação", value=last_interaction[:16] if last_interaction != "Nunca" else "Nunca", inline=True)

    embed.set_thumbnail(url=target.display_avatar.url)

    await ctx.send(embed=embed)

@bot.command(name="profile")
async def profile(ctx):
    """Exibe seu perfil completo"""
    level, interactions = get_relationship(str(ctx.author.id))
    facts = get_user_facts(str(ctx.author.id))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT last_interaction FROM relationships WHERE user_id = ?", (str(ctx.author.id),))
    result = cursor.fetchone()
    last_interaction = result[0] if result else "Primeira vez aqui!"

    cursor.execute("SELECT COUNT(*) FROM interaction_history WHERE user_id = ?", (str(ctx.author.id),))
    total_messages = cursor.fetchone()[0]
    conn.close()

    level_names = {
        0: "Desconhecido",
        1: "Conhecido",
        2: "Amigável",
        3: "Colega",
        4: "Amigo",
        5: "Amigo Próximo",
        6: "Confidente",
        7: "Amigo Íntimo",
        8: "Melhor Amigo",
        9: "Inseparável",
        10: "Alma Gêmea"
    }

    embed = discord.Embed(
        title=f"👤 Perfil de {ctx.author.display_name}",
        description=f"**Nível:** {level}/10 - {level_names.get(level, 'Desconhecido')}", # Ajustado para 10 níveis
        color=discord.Color.blue()
    )

    embed.add_field(name="💬 Interações", value=str(interactions), inline=True)
    embed.add_field(name="📝 Mensagens", value=str(total_messages), inline=True)
    embed.add_field(name="🧠 Memórias", value=str(len(facts)), inline=True)
    embed.add_field(name="⏰ Última Atividade", value=last_interaction[:16] if last_interaction != "Primeira vez aqui!" else last_interaction, inline=False)

    if facts:
        memories_text = "\n".join([f"• **{key}:** {value}" for key, value in facts[:5]])
        if len(facts) > 5:
            memories_text += f"\n... e mais {len(facts) - 5} memória(s)"
        embed.add_field(name="🔍 Principais Memórias", value=memories_text, inline=False)

    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    embed.set_footer(text=f"ID: {ctx.author.id}")

    await ctx.send(embed=embed)

@bot.command(name="serverinfo")
async def serverinfo(ctx):
    """Informações do servidor atual"""
    if not ctx.guild:
        await ctx.send("❌ Este comando só funciona em servidores!")
        return

    guild = ctx.guild

    embed = discord.Embed(
        title=f"🏰 {guild.name}",
        description=guild.description or "Sem descrição",
        color=discord.Color.blue()
    )

    embed.add_field(name="👥 Membros", value=str(guild.member_count), inline=True)
    embed.add_field(name="💬 Canais de Texto", value=str(len(guild.text_channels)), inline=True)
    embed.add_field(name="🔊 Canais de Voz", value=str(len(guild.voice_channels)), inline=True)
    embed.add_field(name="📅 Criado em", value=guild.created_at.strftime("%d/%m/%Y"), inline=True)

    # Configurações do bot neste servidor
    default_channel_id = get_bot_config("default_channel")
    if default_channel_id:
        channel = bot.get_channel(int(default_channel_id))
        if channel and channel.guild == guild:
            embed.add_field(name="📍 Canal Padrão", value=channel.mention, inline=True)

    embed.add_field(name="⚙️ Prefixo", value=get_bot_config("prefix", "!"), inline=True)

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.set_footer(text=f"ID: {guild.id}")

    await ctx.send(embed=embed)

@bot.command(name="resetconfig")
@commands.has_permissions(administrator=True)
async def resetconfig(ctx):
    """Restaura configurações padrão"""
    default_configs = {
        "prefix": "!",
        "tone": "neutro",
        "default_channel": "",
        "avatar_url": "",
        "bot_name": "Akutagawa",
        "memory_duration": "longo",
        "continuous_learning": "true",
        "current_mood": "neutro"
    }

    for key, value in default_configs.items():
        set_bot_config(key, value)

    await ctx.send("✅ Configurações restauradas para o padrão!\n\n**Nota:** Memórias e histórico foram preservados.")

@bot.command(name="clearcontext")
async def clearcontext(ctx):
    """Limpa o contexto da conversa atual"""
    channel_id = str(ctx.channel.id)
    if channel_id in conversation_context:
        del conversation_context[channel_id]
        await ctx.send("🗑️ Contexto da conversa limpo! O bot esqueceu as últimas mensagens desta conversa.")
    else:
        await ctx.send("📭 Não há contexto de conversa para limpar neste canal.")

@bot.command(name="viewcontext")
async def viewcontext(ctx):
    """Mostra o contexto atual da conversa"""
    channel_id = str(ctx.channel.id)
    context = get_conversation_context(channel_id)

    if not context:
        await ctx.send("📭 Não há contexto de conversa armazenado neste canal.")
        return

    embed = discord.Embed(
        title="🧠 Contexto da Conversa Atual",
        description=context[:4000],  # Discord limita a 4096 caracteres
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

# ========== Tratamento de Erros ==========
@setprefix.error
@setpersonality_cmd.error
@setchannel.error
@setname.error
@setmemoryduration.error
@togglelearning.error
@setrelationship.error
@setdalua.error
@setstatus.error
@setactivity.error
@setstatustext.error
@autorotate.error
async def admin_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Você precisa ser administrador para usar este comando!")

# ========== Inicia o Bot ==========
if __name__ == "__main__":
    if not TOKEN:
        print("❌ ERRO: DISCORD_BOT_TOKEN não configurado!")
        print("Configure a variável de ambiente DISCORD_BOT_TOKEN no Replit Secrets")
        exit(1)

    try:
        from keep_alive import keep_alive
        keep_alive()
        print("🌐 Servidor keep-alive iniciado")
    except Exception as e:
        print(f"⚠️ Keep-alive não disponível: {e}")

    print("🚀 Iniciando bot...")
    bot.run(TOKEN)