# Prompts para Implementação V2 — Multi-Bot Consultas API

> **Como usar:** Copie o prompt de cada fase em uma nova conversa do Copilot.
> **Contexto obrigatório:** Sempre anexe este arquivo à conversa:
> - `PLANO_IMPLEMENTACAO_V2.md` (arquitetura multi-bot, fallback chains, adapters, captcha)
>
> **Repo destino:** `c:\Users\Bryan\Documents\Projetos\consultas-api\` (projeto já existente da V1)
> **IMPORTANTE:** A Fase 1 assume que a V1 já está funcionando (BlackConsultas funciona). Se não, complete primeiro os prompts de `PROMPTS_IMPLEMENTACAO.md`.

---

## Fase 1 — Refatoração Base + DataFlow Adapter

```
Refatore o projeto em c:\Users\Bryan\Documents\Projetos\consultas-api\ para suportar múltiplos bots com fallback automático.

CONTEXTO:
- Estou anexando PLANO_IMPLEMENTACAO_V2.md como referência completa da arquitetura.
- O projeto atual funciona com 1 bot (BlackConsultas) e 8 endpoints.
- Tenho 2 contas Telegram (bryan, bryan2) com session strings.
- Agora preciso expandir para 6 bots com sistema de fallback.
- O DataFlow (@wmhrbeiyyjnbot) é o mais fácil: resultado vem direto no privado do bot (sem scraping).

INFORMAÇÕES TÉCNICAS VERIFICADAS:

DataFlow (@wmhrbeiyyjnbot):
- Grupo: -1003340385645
- Fluxo: enviar comando no grupo → bot responde com botões ("Ver no Privado", "Ver Resumo", "Baixar TXT") → clicar "Ver no Privado" → resultado chega como mensagem de texto no chat privado do bot
- Formato do resultado (no privado):
  ```
  🔒 SEU RESULTADO CEP

  📍 CONSULTA CEP
  ━━━━━━━━━━━━━━━━━━━━━━━━━━

  🏠 MORADORES ENCONTRADOS: 486

  👤 1. ANISIO LUIZ DE OLIVEIRA
     🏠 Número: 854
     🆔 CPF: 001.004.548-10

  👤 2. MAURICIO JOSE DE NORONHA
     🏠 Número: 900
     🆔 CPF: 001.093.068-05
  ...
  💡 E mais 478 moradores...
  Use 'Baixar TXT' para lista completa.
  ```
- Também tem /bin que retorna inline direto no grupo (sem botão)
- Comandos: /cpf, /nome, /telefone, /email, /cep, /cnpj, /titulo, /bin, /endereco, /mae, /foto

TAREFA — Fase 1 (Refatoração + DataFlow):

1. Criar classe base abstrata em app/services/adapters/base.py:
   ```python
   class BotAdapter(ABC):
       name: str  # identificador do adapter
       group_id: int  # ID do grupo Telegram
       bot_username: str  # username do bot
       supported_commands: list[str]  # tipos de consulta suportados

       @abstractmethod
       async def execute(self, client: TelegramClient, tipo: str, input_data: str, base: str = None) -> dict:
           """Executa consulta e retorna resultado parseado como dict."""
           pass

       async def wait_for_bot_reply(self, client, sent_msg, timeout=15):
           """Helper: espera reply do bot à nossa mensagem."""
           pass
   ```

2. Mover lógica existente do BlackConsultas para app/services/adapters/black_consultas.py:
   - Extrair o código do telegram_worker.py atual para um BlackConsultasAdapter
   - Manter o scraper como dependência do adapter
   - Não quebrar o que já funciona

3. Criar app/services/adapters/dataflow.py (DataFlowAdapter):
   - Fluxo do execute():
     a) Enviar comando no grupo -1003340385645
     b) Esperar reply do bot (msg com botões "Ver no Privado", etc.)
     c) Clicar no botão "🔒 Ver no Privado" (usar button_index=0 ou texto exato)
     d) Esperar mensagem NO PRIVADO do bot @wmhrbeiyyjnbot (timeout 15s)
        - Para interceptar: escutar eventos NewMessage no chat privado do bot
        - O chat privado é: client.get_entity("wmhrbeiyyjnbot")
     e) Parsear o texto da mensagem privada em dict estruturado
   - Parser do resultado:
     - Extrair campos por regex: linhas com emojis seguido de chave: valor
     - Pattern: capturar linhas tipo "🆔 CPF: 001.004.548-10" → {"cpf": "001.004.548-10"}
     - Para /cep: extrair lista de moradores como array de objetos {nome, numero, cpf}
     - Para /cpf: extrair todos campos pessoais
   - Caso especial /bin: resultado vem inline no grupo (sem botão), parsear direto

4. Criar app/services/bot_router.py:
   ```python
   FALLBACK_CHAINS = {
       "cpf": ["dataflow", "black_consultas"],
       "nome": ["dataflow", "black_consultas"],
       "telefone": ["dataflow", "black_consultas"],
       "email": ["dataflow", "black_consultas"],
       "cep": ["dataflow", "black_consultas"],
       "cnpj": ["dataflow"],
       "titulo": ["dataflow"],
       "bin": ["dataflow"],
       "endereco": ["dataflow"],
       "mae": ["dataflow"],
       "foto": ["dataflow"],
       "ip": ["black_consultas"],
       "pix": ["black_consultas"],
   }
   ```
   - Método route_query(tipo, input_data, base) que percorre a chain
   - Se adapter falhar (exception), tenta o próximo
   - Se todos falharem, raise AllBotsFailedError

5. Criar app/services/bot_health.py:
   - Dict tracking healthy/unhealthy por adapter
   - Marca unhealthy com cooldown (5min manutenção, 1min timeout)
   - Método is_healthy(adapter_name) → bool
   - Método mark_unhealthy(name, cooldown_seconds)
   - Método mark_healthy(name)

6. Refatorar os endpoints existentes para usar bot_router em vez de telegram_worker direto:
   - POST /api/consulta/cpf → bot_router.route_query("cpf", cpf, base)
   - Todos os outros endpoints seguem o mesmo padrão
   - Resultado final para o client não muda (sempre dict JSON)

7. Adicionar novos endpoints (DataFlow-only por enquanto):
   - POST /api/consulta/cnpj  {"cnpj": "33000167000101"}
   - POST /api/consulta/bin   {"bin": "516230"}
   - POST /api/consulta/endereco {"cpf": "07068093868"}
   - POST /api/consulta/mae   {"nome": "Maria Alves"}
   - POST /api/consulta/foto  {"cpf": "07068093868"}

8. Adicionar config no .env:
   ```
   GROUP_DATAFLOW=-1003340385645
   ```

REGRAS:
- NÃO quebrar os endpoints existentes. O BlackConsultas CONTINUA funcionando.
- O DataFlow manda resultado no PRIVADO — precisa escutar mensagem no chat 1:1 com o bot, NÃO no grupo.
- O bot_router deve usar async try/except e simplesmente pular para o próximo adapter em caso de falha.
- Para esperar mensagem no privado: usar client.add_event_handler com asyncio.Event + timeout
- Todos os adapters recebem o client como parâmetro (não criam conexões próprias)
- O AccountPool continua gerenciando qual conta usar
- Manter rate_limiter, cache e logs da V1 funcionando normalmente

AO FINALIZAR:
- git add -A && git commit -m "feat: refatoração multi-bot + DataFlow adapter" && git push
```

---

## Fase 2 — Work Bot Adapter + Captcha Solver

```
Continuando o projeto em c:\Users\Bryan\Documents\Projetos\consultas-api\, implemente a Fase 2.

CONTEXTO:
- Estou anexando PLANO_IMPLEMENTACAO_V2.md como referência.
- Fase 1 completa: temos BotRouter, DataFlowAdapter, BlackConsultasAdapter funcionando com fallback.
- Agora preciso do Work Bot (@WorkGrupoRBot), que é o mais complexo: tem seleção de módulo, resultado no privado, e captcha para /placa.

INFORMAÇÕES TÉCNICAS VERIFICADAS:

Work Bot (@WorkGrupoRBot):
- Grupo: -1002411246251 (TAMAKI)
- Fluxo NORMAL (sem captcha):
  1. Enviar comando (ex: /cpf 12345678900) no grupo
  2. Bot responde com "SELECIONE O MÓDULO" e botões inline (COMPLETA, BASEDATA, PRO, etc.)
  3. Clicar no módulo desejado
  4. Bot responde "✅ PROCESSO CONCLUÍDO" com botão "✅ RESULTADO"
  5. O botão "RESULTADO" é uma URL: https://t.me/WorkGrupoRBot (abre chat privado)
  6. O resultado aparece como MENSAGEM NO PRIVADO do bot @WorkGrupoRBot
- Fluxo com CAPTCHA (apenas /placa):
  1. Após clicar no módulo "Proprietarios", bot manda imagem de captcha
  2. A imagem tem texto alfanumérico distorcido (6 chars)
  3. Junto com a imagem vêm 6 botões com opções de texto
  4. Resolver o captcha = identificar qual botão tem o texto da imagem
  5. Clicar no botão correto → resultado no privado
- Módulos por comando:
  - cpf/nome/telefone/email/titulo → COMPLETA (preferir) ou BASEDATA
  - cep/cnpj → COMPLETA
  - rg/mae/pai/chave/vizinhos/parentes → BASEDATA
  - foto/condutor → PRO
  - placa/proprietario → Proprietarios
  - pep → PEP
  - cns → CNS ou BASEDATA
  - frota → Frota
  - processo_numero → PROCESSO

Captcha Solver (testado e funcionando):
- API: VoidAI (compatível OpenAI) → https://api.voidai.app/v1
- API Key: configurar como VOIDAI_API_KEY no .env
- Modelos testados (TODOS acertam o captcha):
  - gemini-2.0-flash: ~2.5s, sem reasoning, max_tokens=20 funciona ← RECOMENDADO
  - gemini-3.1-flash-lite-preview: ~3.9s, usa reasoning, precisa max_tokens≥200
  - gemini-2.5-flash: ~5.3s, usa reasoning, precisa max_tokens≥200
  - gemini-2.5-pro: ~7.2s, usa reasoning, precisa max_tokens≥500
  - gemini-3.1-pro-preview: ~8.7s, precisa max_tokens≥500
- IMPORTANTE: Modelos "thinking" (2.5, 3.1) gastam ~95-477 tokens de reasoning antes do content.
  Se max_tokens for muito baixo (ex: 20), content vem vazio! Usar max_tokens=500 para segurança.
- Modelo que NÃO funciona: gemini-3-flash-preview (ERROR 403 / timeout)
- Estratégia recomendada: usar gemini-2.0-flash com max_tokens=20 (mais rápido e barato).
  Fallback: gemini-3.1-flash-lite-preview com max_tokens=500.
- Payload de teste que funciona:
  ```json
  {
    "model": "gemini-2.0-flash",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "Qual texto alfanumerico esta na imagem? Opcoes: WAEQH8, EHJ947, 5FLXDY, K5MTMT, RKCU5E, FGGS8E. Responda APENAS a opcao correta, nada mais."},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,{base64_da_imagem}"}}
      ]
    }],
    "max_tokens": 20,
    "temperature": 0
  }
  ```
- Resposta: o content tem apenas o texto da opção correta (ex: "5FLXDY")

TAREFA — Fase 2 (Work Bot + Captcha):

1. Criar app/services/captcha_solver.py:
   - Classe CaptchaSolver com método solve(image_bytes: bytes, options: list[str]) → str
   - Usa httpx para POST em https://api.voidai.app/v1/chat/completions
   - Header: Authorization: Bearer {VOIDAI_API_KEY}
   - Modelo: gemini-2.0-flash
   - Prompt: "Qual texto alfanumerico esta na imagem? Opcoes: {', '.join(options)}. Responda APENAS a opcao correta, nada mais."
   - Envia imagem como base64 no content (formato image_url com data URI)
   - Timeout: 10s
   - Validar que a resposta está na lista de options
   - Se resposta não bater com nenhuma opção: retry 1x, depois raise CaptchaError
   - Logar tempo de resposta e resultado

2. Criar app/services/adapters/work_bot.py (WorkBotAdapter):
   - group_id = -1002411246251
   - bot_username = "WorkGrupoRBot"
   - Mapa MODULE_MAP de tipo → texto do botão do módulo:
     ```python
     MODULE_MAP = {
         "cpf": "COMPLETA", "nome": "COMPLETA", "telefone": "COMPLETA",
         "email": "COMPLETA", "titulo": "COMPLETA", "cep": "COMPLETA",
         "cnpj": "COMPLETA", "rg": "BASEDATA", "mae": "BASEDATA",
         "pai": "BASEDATA", "chave": "BASEDATA", "vizinhos": "BASEDATA",
         "parentes": "BASEDATA", "foto": "PRO", "condutor": "PRO",
         "placa": "Proprietarios", "proprietario": "Proprietarios",
         "pep": "PEP", "cns": "BASEDATA", "frota": "Frota",
         "processo_numero": "PROCESSO",
     }
     ```
   - Método execute(client, tipo, input_data, base=None):
     a) Enviar comando no grupo
     b) Esperar reply com "SELECIONE O MÓDULO" (timeout 15s)
     c) Clicar no botão do módulo (MODULE_MAP[tipo])
     d) Esperar próxima resposta (timeout 15s):
        - Se contém "captcha" ou tem media (imagem) → fluxo captcha
        - Se contém "PROCESSO CONCLUÍDO" → fluxo normal
        - Se contém erro → raise
     e) Fluxo normal: esperar mensagem no PRIVADO de @WorkGrupoRBot
     f) Fluxo captcha:
        - Download da imagem (client.download_media → bytes)
        - Extrair textos dos botões da mensagem como options
        - Chamar captcha_solver.solve(image_bytes, options)
        - Clicar no botão com o texto retornado
        - Esperar mensagem no privado de @WorkGrupoRBot
     g) Parsear texto do privado em dict

   - Parser do resultado do Work Bot:
     - Formato similar ao DataFlow: linhas com emoji + campo: valor
     - Extrair por regex

3. Adicionar ao bot_router.py — expandir FALLBACK_CHAINS:
   ```python
   FALLBACK_CHAINS = {
       "cpf": ["dataflow", "work_bot", "black_consultas"],
       "nome": ["dataflow", "work_bot", "black_consultas"],
       "telefone": ["dataflow", "work_bot", "black_consultas"],
       "email": ["dataflow", "work_bot", "black_consultas"],
       "cep": ["dataflow", "work_bot", "black_consultas"],
       "cnpj": ["dataflow", "work_bot"],
       "titulo": ["dataflow", "work_bot"],
       "bin": ["dataflow"],
       "endereco": ["dataflow"],
       "mae": ["work_bot", "dataflow"],
       "foto": ["work_bot", "dataflow"],
       "ip": ["black_consultas"],
       "pix": ["black_consultas"],
       # Novos (Work Bot exclusivo):
       "rg": ["work_bot"],
       "pai": ["work_bot"],
       "placa": ["work_bot"],
       "proprietario": ["work_bot"],
       "cns": ["work_bot"],
       "chave": ["work_bot"],
       "vizinhos": ["work_bot"],
       "parentes": ["work_bot"],
       "pep": ["work_bot"],
       "condutor": ["work_bot"],
       "frota": ["work_bot"],
       "processo_numero": ["work_bot"],
   }
   ```

4. Adicionar novos endpoints (todos os Work Bot exclusivos):
   - POST /api/consulta/rg         {"rg": "234730742"}
   - POST /api/consulta/pai        {"nome": "Jose Silva"}
   - POST /api/consulta/placa      {"placa": "ABC1D23"}
   - POST /api/consulta/proprietario {"placa": "ABC1D23"}
   - POST /api/consulta/cns        {"cns": "705005484822659"}
   - POST /api/consulta/chave      {"cpf": "07068093868"}
   - POST /api/consulta/vizinhos   {"cpf": "07068093868"}
   - POST /api/consulta/parentes   {"cpf": "07068093868"}
   - POST /api/consulta/pep        {"cpf": "07068093868"}
   - POST /api/consulta/condutor   {"cpf": "07068093868"}
   - POST /api/consulta/frota      {"cnpj": "33000167000101"}
   - POST /api/consulta/processo   {"numero": "1234567"}

5. Adicionar ao .env:
   ```
   GROUP_TAMAKI=-1002411246251
   VOIDAI_API_KEY=sk-voidai-...
   ```

6. Tratamento de erros específicos do Work Bot:
   - "Você precisa iniciar..." → iniciar chat privado automaticamente 1x
   - "assinatura ativa" → raise PaidOnlyError (skip este adapter)
   - Captcha falhou 2x → raise CaptchaError (skip, tentar próximo adapter)
   - Timeout no privado → raise TimeoutError

REGRAS:
- O resultado do Work Bot vem NO PRIVADO do bot (chat 1:1), não no grupo
- Para interceptar a msg privada: registrar handler temporário para NewMessage no chat privado
- O captcha solver usa VoidAI (NÃO Google Generative AI SDK direto) — é uma API OpenAI-compatible
- O gemini-2.0-flash funciona perfeitamente com o VoidAI; NÃO usar gemini-2.5-flash (bug de content=null)
- Se captcha falhar, tentar próximo adapter na chain (Unknowrealbot faz /placa sem captcha)
- Manter DataFlow como preferência para comandos que ambos suportam (é mais rápido e confiável)

AO FINALIZAR:
- git add -A && git commit -m "feat: Work Bot adapter + captcha solver VoidAI" && git push
```

---

## Fase 3 — Unknowrealbot + Unix Robot + VoidSearch Adapters

```
Continuando o projeto em c:\Users\Bryan\Documents\Projetos\consultas-api\, implemente a Fase 3.

CONTEXTO:
- Estou anexando PLANO_IMPLEMENTACAO_V2.md como referência.
- Fase 1-2 completas: DataFlow, Work Bot e BlackConsultas funcionando com fallback + captcha solver.
- Agora preciso dos 3 adapters restantes: Unknowrealbot, Unix Robot e VoidSearch.

INFORMAÇÕES TÉCNICAS VERIFICADAS:

Unknowrealbot (@Unknowrealbot):
- Grupo: -1002336848941 (DON)
- Fluxo:
  1. Enviar comando (ex: /cpf 12345678900)
  2. Bot responde "✅ REQUISIÇÃO REALIZADA COM SUCESSO" com botões inline
  3. Alguns comandos pedem "SELECIONE A BASE" (clicar no botão desejado)
  4. Bot edita mensagem adicionando botão com URL tipo: https://blackconsultas.com/result-consultation/{uuid}?bot=tmf
  5. A URL é temporária (expira em ~24h)
  6. Scrape da URL retorna dados no formato "• Campo: Valor"
- IMPORTANTE: O site blackconsultas.com é o MESMO usado pelo BlackConsultasAdapter — reusar o scraper!
- Comandos: /cpf, /nome, /telefone, /cep, /ip, /cnpj, /email, /titulo, /mae, /foto, /pai, /placa

Unix Robot (@MkBuscasRBot):
- Grupo: -1002336848941 (DON)
- Fluxo para /cep (inline):
  1. Enviar /cep 01310100
  2. Bot responde DIRETO no grupo com texto formatado (sem botão, sem link)
  3. Formato: linhas com "• Campo: Valor" ou emojis + campo: valor
- Outros comandos podem pedir "/iniciar primeiro no privado" → iniciar chat e reenviar
- Vantagem: /cep retorna INLINE instantâneo (é o mais rápido de todos!)
- Nota: /placa usa variantes /placa1 ou /placa2
- Comandos: /cpf, /nome, /telefone, /cep (inline!), /rg, /email, /mae, /site

VoidSearch (@VoidSearch03Bot):
- Grupo: -1002336848941 (DON) ou -1003761336113 (UNEN)
- Fluxo:
  1. Enviar comando
  2. Bot responde com "SELECIONE UMA BASE" e botões callback
  3. Clicar na base desejada
  4. Bot responde com resultado (formato desconhecido — precisa implementar e descobrir)
- Maioria dos comandos é PAGO (mostra aviso de assinatura)
- Comandos FREE verificados: /cpf, /nome, /telefone, /cep, /ip, /cnpj, /placa, /ddd
- ALERTA: pode falhar com "assinatura" para bases que parecem free — tratar como fallback baixo na chain

TAREFA — Fase 3 (3 adapters restantes):

1. Criar app/services/adapters/unknowrealbot.py (UnknowrealbotAdapter):
   - group_id = -1002336848941
   - bot_username = "Unknowrealbot"
   - Fluxo execute():
     a) Enviar comando no grupo
     b) Esperar reply do bot com "REQUISIÇÃO" ou botões
     c) Se tem botões de base: clicar no primeiro botão (ou específico se base fornecida)
     d) Esperar edição da mensagem com botão-URL contendo "result-consultation"
     e) Extrair URL do botão
     f) Scrape com o MESMO scraper do BlackConsultas (mesmo site!)
   - Reusar app/services/scraper.py que já existe

2. Criar app/services/adapters/unix_robot.py (UnixRobotAdapter):
   - group_id = -1002336848941
   - bot_username = "MkBuscasRBot"
   - Fluxo execute() para /cep (e outros inline):
     a) Enviar comando
     b) Esperar reply do bot (texto direto, sem botões)
     c) Parsear o texto inline em dict
   - Fluxo para comandos que redirecionam ao privado:
     a) Se bot responde "iniciar no privado" → enviar /start ao bot no privado
     b) Reenviar comando
     c) Esperar resultado no privado
   - Parser inline: regex para linhas "• Campo: Valor" e "emoji Campo: Valor"
   - PRIORIDADE para /cep (é o mais rápido de todos os bots!)

3. Criar app/services/adapters/voidsearch.py (VoidSearchAdapter):
   - group_id = -1002336848941 (DON — preferir, mais ativo)
   - bot_username = "VoidSearch03Bot"
   - Fluxo execute():
     a) Enviar comando
     b) Esperar reply com "SELECIONE UMA BASE" + botões
     c) Clicar no primeiro botão de base disponível
     d) Esperar próxima resposta/edição (pode ser resultado direto ou erro "assinatura")
     e) Se "assinatura" → raise PaidOnlyError
     f) Parsear resultado (descobrir formato ao testar)
   - Marcar como baixa prioridade na chain (muitos comandos acabam sendo pagos)

4. Atualizar FALLBACK_CHAINS no bot_router.py para a versão FINAL:
   ```python
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
   ```

5. Adicionar endpoint novo:
   - POST /api/consulta/ddd  {"ddd": "19"}  (VoidSearch exclusivo)

6. Adicionar ao .env:
   ```
   GROUP_DON=-1002336848941
   GROUP_UNEN=-1003761336113
   ```

REGRAS:
- Unix Robot /cep é O PRIMEIRO na chain de CEP por ser inline e instantâneo
- Unknowrealbot usa o MESMO site blackconsultas.com — REUSAR o scraper existente
- VoidSearch é o mais instável (muitos comandos pagos) — sempre último ou penúltimo na chain
- Se qualquer bot der "assinatura" → PaidOnlyError → remove da chain em runtime (session-level)
- Grupo DON (-1002336848941) tem 3 bots: Unknowrealbot, Unix Robot, VoidSearch — enviar para lá
- NÃO precisa de contas novas. Usar as mesmas 2 contas (bryan, bryan2) para todos os grupos
- O AccountPool gerencia contenção — uma conta pode estar em múltiplos grupos mas não faz 2 queries simultâneas

AO FINALIZAR:
- git add -A && git commit -m "feat: Unknowrealbot, Unix Robot e VoidSearch adapters" && git push
```

---

## Fase 4 — Robustez Multi-Bot (health, métricas, failover inteligente)

```
Continuando o projeto em c:\Users\Bryan\Documents\Projetos\consultas-api\, implemente a Fase 4.

CONTEXTO:
- Estou anexando PLANO_IMPLEMENTACAO_V2.md como referência.
- Fases 1-3 completas: todos os 6 adapters funcionam, fallback chains completas, 26 endpoints.
- Agora preciso melhorar a robustez: health tracking inteligente, métricas, failover rápido.

TAREFA — Fase 4 (Robustez Multi-Bot):

1. Melhorar app/services/bot_health.py:
   - Adicionar tracking de success_rate por adapter (últimas 20 consultas)
   - Se success_rate < 30% → marcar temporariamente como unhealthy (cooldown 10min)
   - Adicionar response_time_avg por adapter
   - Endpoint GET /api/status/bots que mostra:
     ```json
     {
       "dataflow": {"healthy": true, "success_rate": 0.95, "avg_time": "3.2s", "last_success": "2min ago"},
       "work_bot": {"healthy": true, "success_rate": 0.80, "avg_time": "8.1s", "last_success": "5min ago"},
       ...
     }
     ```
   - Persistir health data em memória (reset no restart está OK)

2. Fallback inteligente baseado em velocidade:
   - Se DataFlow demora >10s para responder, não esperar — começar query no próximo adapter em paralelo
   - Retornar resultado do primeiro que responder com sucesso
   - Implementar como asyncio.gather com first-completed strategy:
     ```python
     # Pseudo-código do conceito:
     async def route_query_fast(tipo, input_data, base):
         chain = get_healthy_chain(tipo)
         # Tenta o primeiro da chain
         try:
             result = await asyncio.wait_for(
                 adapters[chain[0]].execute(...), timeout=10
             )
             return result
         except (asyncio.TimeoutError, Exception):
             # Se primeiro falhou/demorou, tenta próximos em paralelo
             tasks = [adapters[a].execute(...) for a in chain[1:3]]  # max 2 em paralelo
             done, pending = await asyncio.wait(tasks, return_when=FIRST_COMPLETED)
             for p in pending:
                 p.cancel()
             return done.pop().result()
     ```
   - ATENÇÃO: respeitar rate_limiter (não mandar 2 queries simultâneas com a MESMA conta)
   - Solução: usar conta bryan para adapter 1 e bryan2 para adapter 2 (em paralelo)

3. Retry com backoff para captcha:
   - Se captcha solver errar, esperar 1s e tentar novamente (max 2 tentativas)
   - Se falhar 2x, pular Work Bot e ir para Unknowrealbot (que faz /placa sem captcha)
   - Logar cada tentativa de captcha com modelo usado e resultado

4. Rate limiter inteligente por grupo:
   - Adicionar rate limit POR GRUPO (não só por conta)
   - Grupo DON tem 3 bots — se mandar comandos rápido demais para bots DIFERENTES, pode triggerar flood
   - Mínimo 2s entre qualquer mensagem no mesmo grupo (independente do bot)
   - Implementar GroupRateLimiter separado do AccountRateLimiter

5. Cache inteligente:
   - Se o cache expirou mas o resultado era sucesso, manter como "stale" por mais 1h
   - Se TODOS os bots falharem, retornar stale cache com header X-Cache: STALE
   - Adicionar invalidação manual: DELETE /api/cache/{tipo}/{input}

6. Circuit breaker pattern:
   - Se um adapter falhar 5x consecutivas → abrir circuito (30min cooldown)
   - Após cooldown → semi-abrir (permitir 1 query de teste)
   - Se query de teste passar → fechar circuito
   - Se falhar → reabrir por mais 30min
   - Logar mudanças de estado do circuit breaker

7. Melhorar logs:
   - Adicionar request_id único por consulta (UUID)
   - Logar todo o caminho: "request_id=abc → tentando dataflow → timeout → tentando work_bot → sucesso (4.2s)"
   - Facilita debug em produção

8. Endpoint de diagnóstico:
   - GET /api/debug/chain/{tipo} → mostra a chain completa com status de cada adapter
   - GET /api/debug/last-errors → últimos 50 erros com timestamp, adapter e mensagem

REGRAS:
- NÃO usar Redis. Tudo in-memory.
- O parallelismo de adapters SÓ funciona se usar contas DIFERENTES (bryan + bryan2)
- Se só 1 conta disponível (outra em uso), fallback sequencial normal
- Circuit breaker deve ser por adapter, não global
- Logs em JSON para stdout
- Não quebrar nenhum endpoint existente

AO FINALIZAR:
- git add -A && git commit -m "feat: health tracking, circuit breaker, fallback paralelo" && git push
```

---

## Fase 5 — Produção (auth, Docker, docs, monitoramento)

```
Continuando o projeto em c:\Users\Bryan\Documents\Projetos\consultas-api\, implemente a Fase 5.

CONTEXTO:
- Estou anexando PLANO_IMPLEMENTACAO_V2.md como referência.
- Fases 1-4 completas: 6 adapters, 26 endpoints, fallback chains, circuit breaker, health tracking.
- Agora preciso preparar para produção: autenticação, Docker, docs, monitoramento.

TAREFA — Fase 5 (Produção):

1. Autenticação robusta:
   - Middleware exigindo "X-API-Key" em todos endpoints exceto /api/health e /docs
   - Suportar múltiplas keys (API_KEYS=key1,key2,key3 no .env)
   - Cada key pode ter rate limit próprio (default: 60 req/min por key)
   - Logar qual key fez qual request

2. CORS configurável:
   - CORS_ORIGINS no .env (default: "*")
   - Permitir configurar por domínio para produção

3. Dockerfile otimizado:
   - Base: python:3.12-slim
   - Multi-stage build (build deps → runtime minimal)
   - Instalar apenas requirements de produção
   - Non-root user
   - Expor 8000
   - CMD: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
   - IMPORTANTE: --workers 1 porque Telethon não é multi-process safe

4. docker-compose.yml:
   - Service "consultas-api"
   - env_file: .env
   - restart: unless-stopped
   - ports: "8000:8000"
   - healthcheck com curl
   - Logging driver: json-file com max-size 10m e max-file 3

5. Swagger/OpenAPI completo:
   - Title: "Consultas API V2 - Multi-Bot"
   - Descriptions detalhadas em cada endpoint com:
     - O que faz
     - Quais bots podem responder (chain)
     - Exemplo de input/output
   - Tags: "Pessoa", "Veículo", "Localização", "Sistema"
   - Response models com examples

6. Monitoramento:
   - GET /api/metrics retorna:
     ```json
     {
       "uptime_seconds": 3600,
       "total_queries": 150,
       "queries_last_hour": 42,
       "cache_hit_rate": 0.35,
       "adapter_stats": {
         "dataflow": {"queries": 80, "successes": 76, "avg_time_ms": 3200},
         "work_bot": {"queries": 40, "successes": 32, "avg_time_ms": 8100},
         ...
       },
       "circuit_breakers": {
         "dataflow": "closed",
         "voidsearch": "open"
       },
       "accounts": {
         "bryan": {"connected": true, "queries_today": 75},
         "bryan2": {"connected": true, "queries_today": 75}
       }
     }
     ```
   - NÃO requer autenticação (para health checks externos)

7. Graceful shutdown:
   - Parar de aceitar novas requests
   - Aguardar queries em andamento (max 30s)
   - Desconectar Telethon clients
   - Flush de métricas para log

8. README.md atualizado:
   - Descrição: API de consultas multi-bot com fallback automático
   - Requisitos: Python 3.12+, 2 contas Telegram com session strings
   - Setup: .env, Docker ou local
   - Todos os 26 endpoints com curl de exemplo
   - Explicação do sistema de fallback
   - Como adicionar uma nova API key
   - Limitações e troubleshooting

9. .env.example com TODAS as variáveis:
   ```
   # Telegram
   TELEGRAM_API_ID=
   TELEGRAM_API_HASH=
   TELEGRAM_SESSION_STRING_BRYAN=
   TELEGRAM_SESSION_STRING_BRYAN2=

   # Grupos
   GROUP_BLACK_CONSULTAS=-1002396715550
   GROUP_DATAFLOW=-1003340385645
   GROUP_DON=-1002336848941
   GROUP_TAMAKI=-1002411246251
   GROUP_UNEN=-1003761336113

   # Captcha (VoidAI - OpenAI compatible)
   VOIDAI_API_KEY=
   VOIDAI_MODEL=gemini-2.0-flash

   # API
   API_KEYS=change-me-key1,change-me-key2
   CORS_ORIGINS=*
   CACHE_TTL_HOURS=24
   RATE_LIMIT_INTERVAL=3.0
   MAX_REQUESTS_PER_MINUTE=60
   TELEGRAM_TIMEOUT=15
   ```

10. Script deploy.sh (para Oracle VPS):
    ```bash
    #!/bin/bash
    cd /opt/consultas-api
    git pull
    docker compose down
    docker compose up -d --build
    docker compose logs -f --tail=50
    ```

REGRAS:
- Workers=1 no uvicorn (Telethon não funciona com multi-process)
- Swagger/docs NÃO requer auth
- /api/health e /api/metrics NÃO requerem auth
- Todos os outros endpoints REQUEREM X-API-Key
- Docker NÃO copia .env no build (usar env_file em runtime)
- Logs em JSON para stdout (funciona com docker logs)

AO FINALIZAR:
- git add -A && git commit -m "feat: produção - auth, Docker, Swagger, métricas, deploy" && git push
```

---

## Dicas de Uso

1. **Uma fase por conversa** — não misture fases na mesma sessão do Copilot
2. **Sempre anexe `PLANO_IMPLEMENTACAO_V2.md`** como contexto (arrastar para o chat)
3. **Teste cada fase antes de seguir** — rode o servidor e faça pelo menos 1 consulta que use fallback
4. **Se o Copilot errar algo:** cole o erro e peça para corrigir na mesma conversa
5. **Ordem dos adapters importa:** DataFlow → Work Bot → Unknowrealbot → VoidSearch → BlackConsultas (do mais confiável ao menos)
6. **Captcha:** Só /placa no Work Bot tem captcha. Gemini-2.0-flash via VoidAI resolve em ~2.5s
7. **Grupos são públicos** — sempre filtrar reply_to_msg_id para não pegar msgs de outros users
8. **O .env do consultas-api** deve ter os mesmos TELEGRAM_* do repo telegram-mcp + variáveis novas dos grupos

---

## Resumo das Fases

| Fase | O que faz | Adapters | Endpoints |
|------|-----------|----------|-----------|
| 1 | Refatoração + DataFlow | DataFlow + BlackConsultas | 13 (8 existentes + 5 novos) |
| 2 | Work Bot + Captcha | + WorkBot | 25 (+ 12 novos) |
| 3 | Restantes | + Unknowrealbot, Unix Robot, VoidSearch | 26 (+ /ddd) |
| 4 | Robustez | Todos | 26 + endpoints de debug |
| 5 | Produção | Todos | 26 + métricas + docs |

**Resultado final:** 26 endpoints de consulta, 6 bots com fallback automático, captcha solver, circuit breaker, cache, rate limiting, Docker deploy.
