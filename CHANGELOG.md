# Changelog

Todas as mudanças relevantes deste projeto são documentadas neste arquivo.

O formato segue as recomendações de Keep a Changelog e versionamento semântico.

## [0.4.1] - 2026-05-02

### Fixed

- Corrigida a configuração do `JsonFormatter` para compatibilidade com `python-json-logger` v3.

## [0.4.0] - 2026-05-02

### Added

- Autenticação por `X-API-Key` para todos os endpoints protegidos.
- Suporte a múltiplas chaves via `API_SECRET_KEY` e `API_KEYS`.
- Endpoints de monitoramento `/api/health` e `/api/status` com métricas básicas de runtime.
- Modelos de resposta documentados para health, status e erros.
- Arquivos de empacotamento `Dockerfile`, `docker-compose.yml` e `requirements.txt` derivado do `pyproject.toml`.
- `.env.example` para bootstrap de ambiente.
- Documentação inicial do projeto no `README.md`.

### Changed

- Evolução do `main.py` para incluir middleware de autenticação, CORS, runtime state e graceful shutdown.
- Expansão da rota de consultas com documentação OpenAPI mais detalhada.

## [0.3.0] - 2026-05-02

### Added

- Cache em memória com TTL configurável.
- Rate limiting por conta Telegram.
- Rate limiting por IP na camada HTTP.
- Retry automático entre contas quando a consulta falha por timeout.
- Logs estruturados em JSON.
- Mapeamento explícito de erros de negócio do serviço Telegram para códigos HTTP.

### Changed

- `AccountPool` passou a respeitar locks por conta e seleção mais segura sob concorrência.
- Rotas de consulta passaram a registrar métricas e erros operacionais.
- Worker Telegram foi ajustado para melhorar o tratamento de timeouts e respostas do bot.

## [0.2.0] - 2026-05-02

### Added

- Endpoints específicos para `nome`, `telefone`, `cep`, `email`, `ip`, `titulo` e `pix`.
- Endpoint genérico `POST /api/consulta` para roteamento por `tipo`.
- Endpoint de health check.
- Validação Pydantic mais completa para cada tipo de consulta.

### Changed

- `telegram_worker.py` foi expandido para suportar novos comandos e botões de sub-base.
- `requests.py` passou a concentrar a normalização e validação dos inputs.

## [0.1.0] - 2026-05-02

### Added

- MVP da API com `POST /api/consulta/cpf`.
- Estrutura inicial do projeto FastAPI.
- Configuração via `app/config.py`.
- `AccountPool` para balanceamento entre contas Telegram.
- Worker Telethon para envio de comando, espera de resposta e extração de link.
- Scraper inicial do resultado da consulta.
- Script `test_manual.py` para validação manual do fluxo.