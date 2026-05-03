# Plano de Implementação V2 - Multi-Bot Consultas API

## Visão Geral

Expandir a consultas-api de 1 bot (BlackConsultas) para **6 bots** em **6 grupos**, com sistema de fallback automático e captcha solver. Total de **25+ tipos de consulta FREE**.

---

## Todos os Comandos FREE Disponíveis

| # | Tipo de Consulta | Comando | Bots Disponíveis (ordem de prioridade) |
|---|-----------------|---------|---------------------------------------|
| 1 | CPF | `/cpf` | DataFlow → Work Bot → Unknowrealbot → VoidSearch → BlackConsultas |
| 2 | Nome | `/nome` | DataFlow → Work Bot → Unix Robot → VoidSearch → BlackConsultas |
| 3 | Telefone | `/telefone` | DataFlow → Work Bot → Unknowrealbot → VoidSearch → BlackConsultas |
| 4 | Email | `/email` | DataFlow → Work Bot → Unknowrealbot → BlackConsultas |
| 5 | CEP | `/cep` | Unix Robot (inline!) → DataFlow → Work Bot → VoidSearch → BlackConsultas |
| 6 | CNPJ | `/cnpj` | DataFlow → Work Bot → VoidSearch |
| 7 | Título Eleitor | `/titulo` | DataFlow → Work Bot → Unknowrealbot |
| 8 | BIN | `/bin` | DataFlow (inline) |
| 9 | RG | `/rg` | Work Bot → Unix Robot |
| 10 | Nome da Mãe | `/mae` | Work Bot → DataFlow → Unknowrealbot |
| 11 | Nome do Pai | `/pai` | Work Bot → Unknowrealbot |
| 12 | Foto | `/foto` | Work Bot → DataFlow → Unknowrealbot |
| 13 | Placa | `/placa` | Work Bot (captcha/Gemini) → Unknowrealbot → VoidSearch |
| 14 | Endereço | `/endereco` | DataFlow |
| 15 | IP | `/ip` | Unknowrealbot → VoidSearch → BlackConsultas |
| 16 | DDD | `/ddd` | VoidSearch |
| 17 | CNS | `/cns` | Work Bot |
| 18 | Chave PIX | `/chave` | Work Bot |
| 19 | Vizinhos | `/vizinhos` | Work Bot → BlackConsultas |
| 20 | Parentes | `/parentes` | Work Bot → BlackConsultas |
| 21 | PEP | `/pep` | Work Bot |
| 22 | Condutor | `/condutor` | Work Bot |
| 23 | Frota | `/frota` | Work Bot |
| 24 | Processo | `/processo_numero` | Work Bot |
| 25 | Proprietário | `/proprietario` | Work Bot |
| 26 | PIX | `/pix` | BlackConsultas |
| 27 | Score | `/score` | ❌ Todos pagos (futuro?) |

---

## Arquitetura

```
┌──────────────────────────────────────────────────────┐
│                  FastAPI (consultas-api)              │
├──────────────────────────────────────────────────────┤
│  POST /api/consulta/{tipo}                           │
│       body: { "input": "...", "base": "..." }        │
│                                                      │
│  ┌─────────────────────────────────────────────┐     │
│  │         BotRouter (NOVO)                    │     │
│  │  - Decide qual bot usar baseado no tipo     │     │
│  │  - Gerencia fallback chain                  │     │
│  │  - Health check por bot                     │     │
│  └────────────┬────────────────────────────────┘     │
│               │                                      │
│  ┌────────────▼────────────────────────────────┐     │
│  │         Bot Adapters (NOVO)                 │     │
│  │                                             │     │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐    │     │
│  │  │DataFlow  │ │Work Bot  │ │Unknowreal│    │     │
│  │  │Adapter   │ │Adapter   │ │Adapter   │    │     │
│  │  └──────────┘ └──────────┘ └──────────┘    │     │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐    │     │
│  │  │VoidSearch│ │Unix Robot│ │BlackCons.│    │     │
│  │  │Adapter   │ │Adapter   │ │Adapter   │    │     │
│  │  └──────────┘ └──────────┘ └──────────┘    │     │
│  └─────────────────────────────────────────────┘     │
│               │                                      │
│  ┌────────────▼────────────────────────────────┐     │
│  │         Shared Services                     │     │
│  │  - AccountPool (2 contas Telegram)          │     │
│  │  - CaptchaSolver (Gemini API) ← NOVO        │     │
│  │  - ResultCache (24h TTL)                    │     │
│  │  - Scraper (HTML/JSON extraction)           │     │
│  └─────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────┘
```

---

## Detalhamento dos Bot Adapters

### 1. DataFlowAdapter
- **Grupo:** -1003340385645
- **Bot:** @wmhrbeiyyjnbot
- **Fluxo:** Comando → Bot responde inline no grupo (texto parseável)
- **Parse:** Regex no texto da mensagem (formato `• Campo: Valor`)
- **Vantagem:** Resultado direto no grupo, sem links, sem clicks
- **Comandos:** cpf, nome, telefone, email, cep, cnpj, titulo, bin, endereco, mae, foto

### 2. WorkBotAdapter
- **Grupo:** -1002411246251 (TAMAKI)
- **Bot:** @WorkGrupoRBot
- **Fluxo:** Comando → "SELECIONE O MÓDULO" (botões) → Click módulo → "PROCESSO CONCLUÍDO" → Resultado no privado OU captcha
- **Parse:** 
  - Mensagem de sucesso: resultado vai pro privado do bot
  - Alternativa: Interceptar mensagem no privado do @WorkGrupoRBot
- **Captcha:** Para /placa → Gemini Vision resolve → click na resposta certa
- **Módulos por comando:**
  ```
  cpf/nome/telefone/email/titulo → COMPLETA (preferir) ou BASEDATA
  cep/cnpj → COMPLETA
  rg/mae/pai/chave/vizinhos/parentes → BASEDATA
  foto/condutor → PRO
  placa/proprietario → Proprietarios
  pep → PEP
  cns → CNS ou BASEDATA
  frota → Frota
  processo_numero → PROCESSO
  ```
- **Comandos:** TODOS 21

### 3. UnknowrealbotAdapter
- **Grupo:** -1002336848941 (DON)
- **Bot:** @Unknowrealbot
- **Fluxo:** Comando → "REQUISIÇÃO REALIZADA COM SUCESSO" + botão → Link temporário (24h) → Scrape
- **Parse:** Scrape do link (igual BlackConsultas, formato similar)
- **Diferencial:** Alguns comandos pedem "Selecione a base" (botão inline)
- **Comandos:** cpf, nome, telefone, cep, ip, cnpj, email, titulo, mae, foto, pai, placa

### 4. VoidSearchAdapter
- **Grupo:** -1002336848941 (DON) ou -1003761336113 (UNEN)
- **Bot:** @VoidSearch03Bot
- **Fluxo:** Comando → "SELECIONE UMA BASE" (botões) → Click → Resultado
- **Limitação:** Maioria é PAGO. Apenas 8 FREE.
- **Comandos FREE:** cpf, nome, telefone, cep, ip, cnpj, placa, ddd

### 5. UnixRobotAdapter
- **Grupo:** -1002336848941 (DON)
- **Bot:** @MkBuscasRBot
- **Fluxo:** Comando → Responde inline OU "precisa iniciar no privado"
- **Vantagem:** /cep retorna resultado INLINE completo sem botão!
- **Parse:** Regex no texto (formato `• Campo: Valor`)
- **Nota:** /placa usa `/placa1` ou `/placa2`
- **Comandos:** cpf, nome, telefone, cep (inline!), rg, email, mae, site

### 6. BlackConsultasAdapter (já existente)
- **Grupo:** -1002396715550
- **Bot:** @BlackConsultaasBot
- **Fluxo:** Comando → Botão base → Link resultado → Scrape
- **Comandos:** cpf, nome, telefone, email, cep, ip, titulo, pix

---

## Sistema de Fallback

```python
# app/services/bot_router.py

FALLBACK_CHAINS = {
    "cpf": ["dataflow", "work_bot", "unknowrealbot", "voidsearch", "black_consultas"],
    "nome": ["dataflow", "work_bot", "unix_robot", "voidsearch", "black_consultas"],
    "telefone": ["dataflow", "work_bot", "unknowrealbot", "voidsearch", "black_consultas"],
    "email": ["dataflow", "work_bot", "unknowrealbot", "black_consultas"],
    "cep": ["unix_robot", "dataflow", "work_bot", "voidsearch", "black_consultas"],
    "cnpj": ["dataflow", "work_bot", "voidsearch"],
    "titulo": ["dataflow", "work_bot", "unknowrealbot"],
    "bin": ["dataflow"],
    "rg": ["work_bot", "unix_robot"],
    "mae": ["work_bot", "dataflow", "unknowrealbot"],
    "pai": ["work_bot", "unknowrealbot"],
    "foto": ["work_bot", "dataflow", "unknowrealbot"],
    "placa": ["work_bot", "unknowrealbot", "voidsearch"],
    "endereco": ["dataflow"],
    "ip": ["unknowrealbot", "voidsearch", "black_consultas"],
    "ddd": ["voidsearch"],
    "cns": ["work_bot"],
    "chave": ["work_bot"],
    "vizinhos": ["work_bot", "black_consultas"],
    "parentes": ["work_bot", "black_consultas"],
    "pep": ["work_bot"],
    "condutor": ["work_bot"],
    "frota": ["work_bot"],
    "processo_numero": ["work_bot"],
    "proprietario": ["work_bot"],
    "pix": ["black_consultas"],
}

# Health tracking por bot
BOT_HEALTH = {
    "dataflow": {"healthy": True, "last_failure": None, "cooldown_until": None},
    "work_bot": {"healthy": True, ...},
    ...
}

async def route_query(tipo: str, input_data: str, base: str = None):
    chain = FALLBACK_CHAINS.get(tipo, [])
    
    for bot_name in chain:
        if not is_bot_healthy(bot_name):
            continue  # Skip bots em cooldown
        
        adapter = get_adapter(bot_name)
        try:
            result = await adapter.execute(tipo, input_data, base)
            if result:
                mark_healthy(bot_name)
                return result
        except BotMaintenanceError:
            mark_unhealthy(bot_name, cooldown=300)  # 5min cooldown
        except BotTimeoutError:
            mark_unhealthy(bot_name, cooldown=60)   # 1min cooldown
        except BotPaidOnlyError:
            # Remove permanentemente desse chain em runtime
            remove_from_chain(tipo, bot_name)
        except BotNotFoundError:
            continue  # Tenta próximo
    
    raise AllBotsFailedError(f"Nenhum bot respondeu para /{tipo}")
```

---

## Captcha Solver (Work Bot /placa)

```python
# app/services/captcha_solver.py

import google.generativeai as genai
import base64

class CaptchaSolver:
    def __init__(self, gemini_api_key: str):
        genai.configure(api_key=gemini_api_key)
        self.model = genai.GenerativeModel("gemini-2.5-flash")
    
    async def solve_work_bot_captcha(self, image_bytes: bytes, options: list[str]) -> str:
        """
        Resolve captcha do Work Bot.
        
        O captcha mostra uma imagem com texto distorcido e 6 botões com opções.
        Precisa identificar qual botão corresponde ao texto na imagem.
        
        Args:
            image_bytes: Imagem do captcha (download da msg)
            options: Lista de textos dos botões ["D6U8GY", "AX2DP4", "NE7F6T", ...]
        
        Returns:
            Texto do botão correto
        """
        prompt = f"""Analise esta imagem de captcha. Ela contém um texto alfanumérico distorcido.
Identifique o texto exato escrito na imagem.

As opções disponíveis são: {', '.join(options)}

Responda APENAS com a opção que corresponde ao texto na imagem. Sem explicação."""

        response = await self.model.generate_content_async([
            prompt,
            {"mime_type": "image/png", "data": base64.b64encode(image_bytes).decode()}
        ])
        
        answer = response.text.strip()
        
        # Validar que a resposta é uma das opções
        if answer in options:
            return answer
        
        # Fallback: fuzzy match
        for opt in options:
            if opt in answer or answer in opt:
                return opt
        
        raise CaptchaUnsolvableError(f"Gemini respondeu '{answer}' mas não bate com opções")
```

### Fluxo /placa no Work Bot com Captcha:

```python
async def execute_placa_work_bot(client, placa: str):
    # 1. Envia /placa ABC1234
    sent = await client.send_message(TAMAKI_GROUP, f"/placa {placa}")
    
    # 2. Aguarda "SELECIONE O MÓDULO" com botão "Proprietarios"
    reply = await wait_for_reply(sent.id, timeout=15)
    await reply.click(text="Proprietarios")
    
    # 3. Aguarda resposta - pode ser:
    #    a) "PROCESSO CONCLUÍDO" → sucesso
    #    b) "Resolva o captcha!" → precisa resolver
    response = await wait_for_reply(sent.id, timeout=15)
    
    if "captcha" in response.text.lower():
        # 4. Download da imagem do captcha
        image_bytes = await client.download_media(response.media, bytes)
        
        # 5. Pegar opções dos botões
        buttons = response.buttons  # [[D6U8GY, AX2DP4], [NE7F6T, KWZTUH], [76UCGF, SNR5SN]]
        options = [btn.text for row in buttons for btn in row]
        
        # 6. Resolver com Gemini
        answer = await captcha_solver.solve(image_bytes, options)
        
        # 7. Clicar na resposta
        await response.click(text=answer)
        
        # 8. Aguardar resultado
        final = await wait_for_reply(sent.id, timeout=15)
    else:
        final = response
    
    # 9. Extrair resultado (vai pro privado)
    return await extract_private_result(client)
```

---

## Novos Endpoints da API

```python
# Endpoints NOVOS (não existiam antes)
POST /api/consulta/rg          {"rg": "234730742"}
POST /api/consulta/mae         {"nome": "Maria Alves"}
POST /api/consulta/pai         {"nome": "Jose Alves"}
POST /api/consulta/foto        {"cpf": "07068093868"}
POST /api/consulta/placa       {"placa": "ABC1234"}
POST /api/consulta/endereco    {"cpf": "07068093868"}  # ou endereço
POST /api/consulta/ddd         {"ddd": "19"}
POST /api/consulta/cns         {"cns": "705005484822659"}
POST /api/consulta/chave       {"cpf": "07068093868"}  # chave PIX
POST /api/consulta/vizinhos    {"cpf": "07068093868"}
POST /api/consulta/parentes    {"cpf": "07068093868"}
POST /api/consulta/pep         {"cpf": "07068093868"}
POST /api/consulta/condutor    {"cpf": "07068093868"}
POST /api/consulta/frota       {"cnpj": "33000167000101"}
POST /api/consulta/processo    {"numero": "1234567"}
POST /api/consulta/proprietario {"placa": "ABC1234"}
POST /api/consulta/cnpj        {"cnpj": "33000167000101"}
POST /api/consulta/bin         {"bin": "516230"}

# Endpoints EXISTENTES (mantidos, agora com fallback multi-bot)
POST /api/consulta/cpf         {"cpf": "...", "base": "completo|fotos|..."}
POST /api/consulta/nome        {"nome": "...", "base": "nome|nome_mae"}
POST /api/consulta/telefone    {"telefone": "..."}
POST /api/consulta/email       {"email": "..."}
POST /api/consulta/cep         {"cep": "..."}
POST /api/consulta/ip          {"ip": "..."}
POST /api/consulta/titulo      {"titulo": "..."}
POST /api/consulta/pix         {"pix": "...", "base": "pix|pix2"}
```

---

## Etapas de Implementação

### Fase 1 - Refatoração Base (sem quebrar nada)
1. Criar `app/services/adapters/base.py` - classe abstrata `BotAdapter`
2. Mover lógica atual para `app/services/adapters/black_consultas.py`
3. Criar `app/services/bot_router.py` com fallback chains
4. Criar `app/services/bot_health.py` - tracking de saúde

### Fase 2 - DataFlow Adapter (mais fácil, inline)
5. Criar `app/services/adapters/dataflow.py`
6. Parser de resultado inline (regex `• Campo: Valor`)
7. Testar cpf, nome, telefone, email, cep, cnpj, titulo, bin, endereco, mae, foto

### Fase 3 - Unix Robot Adapter (inline p/ cep)
8. Criar `app/services/adapters/unix_robot.py`
9. Parser inline para /cep
10. Grupo: DON (-1002336848941)

### Fase 4 - Work Bot Adapter (mais complexo)
11. Criar `app/services/adapters/work_bot.py`
12. Lógica de seleção de módulo (click inline button)
13. Interceptação de resultado no privado do bot
14. Criar `app/services/captcha_solver.py` (Gemini)
15. Integrar captcha no fluxo /placa
16. Grupo: TAMAKI (-1002411246251)

### Fase 5 - Unknowrealbot Adapter
17. Criar `app/services/adapters/unknowrealbot.py`
18. Lógica de click + scrape link temporário
19. Grupo: DON (-1002336848941)

### Fase 6 - VoidSearch Adapter
20. Criar `app/services/adapters/voidsearch.py`
21. Lógica de "SELECIONE UMA BASE" + click
22. Grupo: DON (-1002336848941) ou UNEN (-1003761336113)

### Fase 7 - Novos Endpoints + Testes
23. Adicionar todos os 18 novos endpoints
24. Testes de integração por adapter
25. Teste de fallback chain

### Fase 8 - Deploy
26. Atualizar docker-compose com GEMINI_API_KEY
27. Atualizar .env com novos GROUP_IDs
28. Deploy no Oracle VPS

---

## Config Final (.env)

```env
# Telegram
TELEGRAM_API_ID=23328857
TELEGRAM_API_HASH=007ad1bcef9191e97fe58fd1525b2b60
TELEGRAM_SESSION_STRING_BRYAN=...
TELEGRAM_SESSION_STRING_BRYAN2=...

# Grupos
GROUP_BLACK_CONSULTAS=-1002396715550
GROUP_DATAFLOW=-1003340385645
GROUP_DON=-1002336848941
GROUP_TAMAKI=-1002411246251
GROUP_UNEN=-1003761336113

# Gemini (captcha solver)
GEMINI_API_KEY=...

# API
API_KEYS=key1,key2,key3
CACHE_TTL_HOURS=24
RATE_LIMIT_INTERVAL=3.0
MAX_REQUESTS_PER_MINUTE=20
```

---

## Tratamento de Erros por Bot

| Mensagem do Bot | Ação | Código HTTP |
|----------------|------|-------------|
| "NÃO ENCONTRADO" | Tenta próximo bot no chain | (interno) |
| "NÃO ENCONTRADO" em TODOS | Retorna ao client | 404 |
| "assinatura ativa" / "Planos Privados" | Skip bot, marca como PAID | (interno) |
| "em manutenção" | Cooldown 5min | (interno) |
| Timeout 15s | Cooldown 1min, tenta próximo | (interno) |
| Todos falharam | Retorna ao client | 503 |
| Captcha falhou 3x | Skip Work Bot p/ /placa | (interno) |
| "Inicie o chat no privado" | Iniciar chat automaticamente 1x | (interno) |

---

## Resultado Final

**De 8 endpoints** (antes) → **26 endpoints** (depois)

**Novos tipos de consulta:**
- RG, Mãe, Pai, Foto, Placa, Endereço, DDD, CNS, Chave PIX
- Vizinhos, Parentes, PEP, Condutor, Frota, Processo, Proprietário, CNPJ, BIN

**Resiliência:** Cada consulta tem 2-5 bots de fallback. Se DataFlow cair, Work Bot assume. Se Work Bot cair, Unknowrealbot assume. A API NUNCA fica sem resposta enquanto pelo menos 1 bot funcionar.
