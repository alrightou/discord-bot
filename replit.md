# Overview

This is a Discord bot application that embodies Ryūnosuke Akutagawa from Bungou Stray Dogs with AI-powered conversational capabilities and persistent memory. The bot can remember user-specific facts, maintain its Akutagawa personality, and interact with users through Discord commands. It uses Google Gemini AI (free, unlimited) for natural language processing and SQLite for data persistence.

# Recent Changes

*08/10/2025** - Sistema completo de controle de presença e rotação automática
- Adicionados comandos de controle de status ao menu !help (nova página 6)
- `!setstatus` - Alterar status do bot (online, ausente, ocupado, invisível)
- `!setactivity` - Definir atividade do bot (jogando, ouvindo, assistindo, transmitindo)
- `!setstatustext` - Definir mensagem de status customizada
- `!autorotate` - Sistema de rotação automática de atividades a cada 50 minutos
- Criadas 30 variações de atividades:
  * 10 músicas/bandas (The Neighbourhood, Arctic Monkeys, Mitski, etc)
  * 10 frases filosóficas/existenciais ("contemplando a existência 🌙", etc)
  * 10 jogos (Genshin Impact, Dark Souls III, Hollow Knight, etc)
- Menu !help expandido de 6 para 7 páginas com seção dedicada a controle de presença

**08/10/2025** - Implementado sistema de variação de pronomes e palavrões contextuais
- Adicionada função `get_dalua_pronoun_set()` para variar pronomes da Dalua (60% feminino, 40% masculino)
- Akutagawa agora varia entre namorada/namorado, rainha/rei, ela/dele ao falar com Dalua
- Implementado sistema de palavrões contextuais (porra, filho da puta, desgraça, droga, etc)
- Palavrões são usados apenas em contextos apropriados (raiva, frustração, irritação)
- Atualizada personalidade padrão para incluir uso contextual de palavrões
- Modificado contexto da Dalua para usar variação dinâmica de pronomes

# User Preferences

- Preferred communication style: Simple, everyday language
- Bot personality: Ryūnosuke Akutagawa from Bungou Stray Dogs (complete character embodiment)
- Response style: Natural, humanized (5-20 words), lowercase when appropriate, NO roleplay/asterisks

# System Architecture

## Application Structure

**Problem:** Need a Discord bot that can maintain context and memory across conversations.

**Solution:** Python-based Discord bot using discord.py library with SQLite for persistence and optional OpenAI integration for AI responses.

**Architecture decisions:**
- **Monolithic design** - Single `main.py` file contains all bot logic, suitable for a lightweight bot with limited complexity
- **Command-based interaction** - Uses Discord's command prefix system (`!`) for user interactions
- **Persistent memory** - SQLite database stores user facts and bot personality without requiring external database infrastructure

## Core Components

### Bot Framework
- **discord.py (v2.3.2)** - Official Discord API wrapper
- **Intents:** Message content intent enabled to read and respond to messages
- **Command prefix:** `!` for triggering bot commands
- Custom help command (default removed for customization)

### Database Layer
- **SQLite3** - Embedded database for zero-configuration persistence
- **Schema design:**
  - `facts` table: Stores user-specific key-value memories (user_id, key, value) with unique constraint
  - `personality` table: Single-row global personality configuration with update tracking
- **Rationale:** SQLite chosen for simplicity, no external database required, suitable for small-to-medium scale bot usage

### AI Integration
- **Google Gemini (v2.5-flash)** - PRIMARY AI provider (free, unlimited)
- **OpenAI API (v1.54.0)** - Fallback integration for conversational AI
- **Priority:** Gemini > OpenAI (Gemini is free, so it's preferred when both are available)
- **Model:** Configurable via environment variable (Gemini: gemini-2.5-flash, OpenAI: gpt-3.5-turbo)
- **Response style:** Humanized, natural chat style (5-20 words per message, no roleplay/asterisks, lowercase when appropriate)
- **Fallback behavior:** Bot operates without AI if no API key provided

### Keep-Alive Mechanism
- **Flask web server** - Simple HTTP endpoint returns "OK - bot online"
- **Threading:** Runs Flask server in daemon thread alongside Discord bot
- **Purpose:** Enables external uptime monitoring services (like UptimeRobot) to ping and keep Replit instance active

**Alternatives considered:**
- **PostgreSQL:** Overkill for this use case, requires external hosting
- **JSON files:** Considered but lacks query capabilities and concurrent access safety
- **Pros of SQLite:** Zero setup, ACID compliance, built-in Python support
- **Cons:** Limited scalability for high-concurrency scenarios (acceptable trade-off for Discord bot usage)

## Deployment Architecture

**Platform:** Replit (Python environment)
- **Runtime:** Python 3.x with dependency management via requirements.txt
- **Always-on strategy:** Optional external ping service to prevent Repl sleep
- **Configuration:** Environment variables for sensitive credentials

# External Dependencies

## Required Services

### Discord Developer Portal
- **Purpose:** Bot token generation and permissions management
- **Configuration:** Bot must have "Message Content Intent" enabled
- **OAuth2 scopes:** `bot` and `applications.commands`
- **Required permissions:** Send Messages, Read Message History, View Channels

### Google Gemini API (Recommended - FREE)
- **Purpose:** AI-powered conversation capabilities (completely free)
- **Model:** Configurable (default: gemini-2.5-flash)
- **Advantages:** Free, unlimited usage, high quality responses
- **Get API key:** https://aistudio.google.com/apikey

### OpenAI API (Optional - Paid)
- **Purpose:** Fallback AI-powered conversation capabilities
- **Model:** Configurable (default: gpt-3.5-turbo)
- **Fallback:** Bot functions without AI if no API key provided

## Python Dependencies

- **discord.py (2.3.2)** - Discord API client library
- **google-genai (0.3.0)** - Google Gemini API client (PRIMARY)
- **openai (1.54.0)** - OpenAI API client (fallback)
- **flask (2.2.5)** - Lightweight web framework for keep-alive endpoint
- **httpx (0.27.2)** - HTTP client (dependency for AI packages)

## Environment Variables

- `DISCORD_BOT_TOKEN` (Required) - Discord bot authentication token
- `GEMINI_API_KEY` (Recommended) - Google Gemini API authentication (FREE)
- `GEMINI_MODEL` (Optional) - Gemini model selection, defaults to gemini-2.5-flash
- `OPENAI_API_KEY` (Optional) - OpenAI API authentication (paid fallback)
- `OPENAI_MODEL` (Optional) - AI model selection, defaults to gpt-3.5-turbo

## External Monitoring (Optional)

- **UptimeRobot or similar** - HTTP monitoring service to ping Flask endpoint and prevent Replit instance sleep
- **Endpoint:** `http://<repl-url>:5000/` returns "OK - bot online"