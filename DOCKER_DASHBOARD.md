# BULK Strategy Lab Docker

## Servicos

- `dashboard`: serve o dashboard em `http://127.0.0.1:8765`.
- `paper-live`: worker read-only continuo, atualiza Redis, `paper_live/` e `dashboard/live_data.json`.
- `redis`: barramento/cache do ultimo snapshot live.

O dashboard le `/live_data.json` do Redis quando disponivel e cai para o arquivo em `dashboard/live_data.json` se o Redis estiver indisponivel.

## Comandos

Subir tudo:

```powershell
docker compose up -d --build
```

Ver status:

```powershell
docker compose ps
```

Parar tudo:

```powershell
docker compose down
```

Ver logs do paper-live:

```powershell
docker compose logs -f paper-live
```

Ver o ultimo snapshot no Redis:

```powershell
docker compose exec redis redis-cli GET bulk:paper_live:latest
```

## Rotas

- `/`: overview do backtest
- `/token/BNB-USD`: backtest por token
- `/paper-live`: paper-live com bid/ask real
- `/paper-live/token/BNB-USD`: paper-live por token

## Dados Gravados

- `paper_live/signals.csv`
- `paper_live/open_positions.csv`
- `paper_live/closed_trades.csv`
- `paper_live/live_summary.csv`
- `paper_live/book_snapshots.csv`
- `paper_live/live_state.json`
- `paper_live/errors.log`

Nenhum endpoint assinado e usado nessa fase.
