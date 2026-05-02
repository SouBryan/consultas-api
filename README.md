# Consultas API

API REST em FastAPI para automatizar consultas ao bot @BlackConsultaasBot no Telegram, com balanceamento entre duas contas, cache em memória, retry automático, logs JSON, autenticação por API key e endpoints de monitoramento.

## O que faz

- Recebe requisições HTTP para consultas de CPF, nome, telefone, CEP, e-mail, IP, título de eleitor e PIX.
- Envia o comando correspondente ao bot no grupo configurado.
- Seleciona sub-bases quando necessário.
- Extrai o link do resultado e converte o conteúdo em JSON estruturado.
- Aplica cache, rate limit, retry entre contas e logging estruturado.

## Configuração

1. Copie [.env.example](.env.example) para `.env`.
2. Preencha as credenciais do Telegram e as sessões StringSession.
3. Defina `API_SECRET_KEY` e, se quiser, `API_KEYS` adicionais separadas por vírgula.
4. Ajuste `CORS_ORIGINS` para os domínios permitidos no frontend.

## Rodando localmente

1. Crie e ative um ambiente virtual Python 3.12+.
2. Instale as dependências:

```bash
pip install -r requirements.txt
```

3. Inicie a API:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

4. Acesse a documentação em `http://localhost:8000/docs`.

## Rodando com Docker

```bash
docker compose up --build -d
```

O health check do container usa `http://localhost:8000/api/health`.

## Endpoints disponíveis

### Health check

```bash
curl http://localhost:8000/api/health
```

### Status detalhado

```bash
curl http://localhost:8000/api/status \
  -H "X-API-Key: change-me"
```

### Consulta CPF

```bash
curl -X POST http://localhost:8000/api/consulta/cpf \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me" \
  -d '{"cpf": "12974572936", "base": "completo"}'
```

### Consulta nome

```bash
curl -X POST http://localhost:8000/api/consulta/nome \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me" \
  -d '{"nome": "João da Silva Santos", "base": "nome"}'
```

### Consulta CEP

```bash
curl -X POST http://localhost:8000/api/consulta/cep \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me" \
  -d '{"cep": "87020025"}'
```

### Consulta genérica

```bash
curl -X POST http://localhost:8000/api/consulta \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me" \
  -d '{"tipo": "cpf", "input": "12974572936", "base": "completo"}'
```

## Segurança e operação

- Todos os endpoints `/api/*`, exceto `/api/health`, exigem o header `X-API-Key`.
- A API aceita uma chave principal em `API_SECRET_KEY` e chaves extras em `API_KEYS`.
- O header `X-Cache` indica `HIT` ou `MISS` nas consultas.
- Os logs são emitidos em JSON para stdout.

## Limitações conhecidas

- O comportamento do bot pode mudar sem aviso, inclusive entre editar a mensagem ou responder com uma nova mensagem em reply.
- O módulo PIX, nas contas testadas, atualmente retorna assinatura ativa.
- O cache é apenas em memória; ao reiniciar a API, ele é limpo.
- O rate limit por IP é local ao processo; múltiplas instâncias não compartilham contadores.