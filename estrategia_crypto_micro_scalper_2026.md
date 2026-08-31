# Estrategia Crypto Micro Scalper 2026

> Documento de estudo e especificacao inicial para um bot de trading em cripto perps.
> Isto nao e recomendacao financeira. Toda estrategia precisa de backtest, forward test e controle de risco antes de usar dinheiro real.

## Objetivo

Construir e testar um bot intraday para cripto, usando timeframes de 5 minutos e 15 minutos, com capacidade de fazer ate 50 operacoes por dia quando houver liquidez e condicoes favoraveis.

A ideia central nao e usar um indicador magico. O sistema combina:

- leitura de regime no 15m;
- entradas no 5m;
- VWAP, EMAs, RSI, ATR, Bollinger Bands, volume, funding, open interest e premium;
- execucao preferencialmente maker/post-only;
- limites rigidos de perda diaria e semanal;
- aprovacao apenas depois de backtest com taxas, slippage e funding.

## Conclusao Estrategica

Para operar 5m/15m e tentar ate 50 trades por dia, a melhor arquitetura e um conjunto de motores:

1. Maker VWAP Reversion
2. Candle-Turn Momentum
3. Bollinger/ATR Breakout
4. Premium/Funding Reversal

Distribuicao sugerida das operacoes:

| Motor | Peso aproximado | Funcao |
|---|---:|---|
| Maker VWAP Reversion | 50% a 70% | Gerar frequencia com ordens limit/post-only |
| Candle-Turn Momentum | 15% a 25% | Capturar impulso no inicio de candles de 15m |
| Bollinger/ATR Breakout | 10% a 20% | Capturar expansao de volatilidade |
| Premium/Funding Reversal | 5% a 10% | Capturar reversoes em mercado crowded |

## Pares Recomendados

### Pares principais

| Categoria | Pares | Uso |
|---|---|---|
| Majors | BTC-USD, ETH-USD | Base principal do bot |
| Large alts | SOL-USD, BNB-USD | Boa liquidez, mais volatilidade |
| Liquid alts | XRP-USD, DOGE-USD | Secundarios, menor tamanho |

### Pares opcionais

| Categoria | Pares | Uso |
|---|---|---|
| Mid/minors | ZEC-USD, SUI-USD | So operar com volume/OI fortes |
| Meme/alto risco | FARTCOIN-USD | Evitar no inicio |

## Frequencia Esperada

Com os pares BTC, ETH, SOL, BNB, XRP e DOGE:

| Categoria | Pares | Trades/dia esperado |
|---|---|---:|
| Majors | BTC, ETH | 20 a 28 |
| Large alts | SOL, BNB | 12 a 18 |
| Liquid alts | XRP, DOGE | 8 a 12 |
| Minors | ZEC, SUI | 2 a 6 |
| Meme | FARTCOIN | 0 a 2 |

Meta operacional:

- conservador: 10 a 20 trades/dia;
- equilibrado: 20 a 35 trades/dia;
- agressivo: 35 a 50 trades/dia.

Para uma banca inicial de US$1.000, a versao recomendada para teste realista e comecar em 10 a 25 trades/dia e so liberar ate 50 depois de validacao.

## Indicadores

Indicadores principais:

- EMA 9: impulso curto;
- EMA 20: tendencia curta;
- EMA 50: tendencia intermediaria;
- EMA 200: regime;
- VWAP: preco justo intraday;
- RSI 14: exaustao e filtro de entrada;
- ATR 14: stop, alvo e tamanho da posicao;
- Bollinger Bands 20, 2: compressao e breakout;
- Volume SMA 20: confirmacao;
- Funding: custo/crowding em perps;
- Open Interest: confirmacao de pressao;
- Premium: diferenca entre mark price e index price.

Formula de premium:

```txt
premium = (mark_price - index_price) / index_price
```

## Filtro De Regime

Antes de qualquer entrada, o bot deve classificar o mercado.

### Bull intraday

```txt
Preco no 15m acima da EMA 200
EMA 20 acima da EMA 50 no 15m
ADX ou inclinacao das EMAs indicando tendencia
Preferir longs
Shorts somente como reversao rapida
```

### Bear intraday

```txt
Preco no 15m abaixo da EMA 200
EMA 20 abaixo da EMA 50 no 15m
Preferir shorts
Longs somente como reversao rapida
```

### Lateral

```txt
Preco cruzando VWAP varias vezes
Bollinger Bands sem expansao clara
ADX baixo, se usado
Preferir VWAP reversion
Evitar breakouts fracos
```

### Stress

```txt
ATR muito alto
Wicks violentos
Spread alto
Funding extremo
Noticias fortes
Reduzir tamanho ou pausar
```

## Motor 1: Maker VWAP Reversion

Este e o motor principal para alta frequencia.

Objetivo: capturar pequenas reversoes ate a VWAP usando ordens limit post-only, reduzindo custo de taxa.

### Long

Entrar comprado quando:

```txt
15m nao esta em queda forte
Preco 5m abaixo da VWAP
RSI 5m entre 25 e 45
Distancia da VWAP >= 0.25 ATR
Spread baixo
Volume nao esta colapsado
Ordem buy limit post-only no bid
```

Saida:

```txt
Take profit: VWAP ou +0.06% a +0.15%
Stop: -0.10% a -0.25%
Tempo maximo na posicao: 5 a 20 minutos
```

### Short

Entrar vendido quando:

```txt
15m nao esta em alta forte
Preco 5m acima da VWAP
RSI 5m entre 55 e 75
Distancia da VWAP >= 0.25 ATR
Spread baixo
Ordem sell limit post-only no ask
```

Saida:

```txt
Take profit: VWAP ou +0.06% a +0.15%
Stop: -0.10% a -0.25%
Tempo maximo na posicao: 5 a 20 minutos
```

Frequencia esperada:

```txt
25 a 35 trades/dia em modo agressivo
```

## Motor 2: Candle-Turn Momentum

Este motor tenta capturar impulso no inicio de candles de 15 minutos.

Janelas:

```txt
00, 15, 30 e 45 de cada hora
```

### Long

Entrar comprado quando:

```txt
Ultimos 2 candles de 5m positivos
Preco acima da VWAP
EMA 9 acima da EMA 21 ou EMA 20
Volume aumentando
Entrada nos primeiros 20 a 40 segundos do novo candle de 15m
```

### Short

Entrar vendido quando:

```txt
Ultimos 2 candles de 5m negativos
Preco abaixo da VWAP
EMA 9 abaixo da EMA 21 ou EMA 20
Volume aumentando
Entrada nos primeiros 20 a 40 segundos do novo candle de 15m
```

Saida:

```txt
Take profit: 0.08% a 0.20%
Stop: 0.06% a 0.14%
Tempo maximo: 5 a 15 minutos
```

Frequencia esperada:

```txt
8 a 15 trades/dia
```

## Motor 3: Bollinger/ATR Breakout

Este motor busca expansao de volatilidade.

### Long

Entrar comprado quando:

```txt
Bollinger Band width estava comprimido
Candle 5m fecha acima da banda superior
Volume > 1.5x media de 20 candles
15m acima da EMA 200
ATR comecando a expandir
```

### Short

Entrar vendido quando:

```txt
Bollinger Band width estava comprimido
Candle 5m fecha abaixo da banda inferior
Volume > 1.5x media de 20 candles
15m abaixo da EMA 200
ATR comecando a expandir
```

Saida:

```txt
Take profit: 0.20% a 0.45%
Stop: 0.12% a 0.25%
Trailing opcional por EMA 20 ou 1 ATR
```

Frequencia esperada:

```txt
5 a 10 trades/dia
```

## Motor 4: Premium/Funding Reversal

Este motor usa sinais especificos de futuros perpetuos.

### Short crowded long

Entrar vendido quando:

```txt
Premium z-score > 2
Funding positivo
Open Interest subindo
Preco perde VWAP no 5m
RSI perde 50 ou mostra divergencia
```

### Long crowded short

Entrar comprado quando:

```txt
Premium z-score < -2
Funding negativo
Open Interest subindo
Preco recupera VWAP no 5m
RSI recupera 50 ou mostra divergencia
```

Saida:

```txt
Take profit: VWAP, EMA 20 ou 1.5R a 2R
Stop: acima/abaixo do ultimo swing ou 1.5 ATR
```

Frequencia esperada:

```txt
2 a 6 trades/dia
```

## Gestao De Risco Para US$1.000

Configuracao inicial:

```txt
Capital: US$1.000
Risco por trade: US$1.50 a US$3.00
Risco maximo por trade agressivo: US$5.00
Stop diario: -US$25
Stop semanal: -US$70
Maximo de perdas seguidas: 3
Maximo de posicoes simultaneas: 3
Alavancagem real: 1x a 3x
Martingale: proibido
```

Regras de pausa:

```txt
Pausar apos 3 perdas seguidas
Pausar se drawdown diario atingir -US$25
Pausar se spread dobrar em relacao a media
Pausar se API/exchange estiver instavel
Pausar durante eventos de noticia forte, se nao houver modulo especifico
```

## Por Que Nao Usar Martingale

Martingale significa aumentar tamanho depois de perda para tentar recuperar.

Em cripto perps isso e perigoso porque:

- wicks podem liquidar antes da reversao;
- funding pode piorar a posicao;
- alavancagem aumenta risco de ruina;
- sequencias de perda acontecem mesmo em estrategias boas;
- 50 trades/dia aumenta a chance de sequencias ruins.

Alternativa recomendada:

```txt
Anti-martingale leve:
Perdeu -> reduz tamanho
Ganhou -> mantem ou aumenta pouco se o dia estiver positivo
```

## Execucao

Para 50 trades/dia, execucao e tao importante quanto sinal.

Regras:

```txt
Usar post-only/maker por padrao
Usar taker apenas em breakout confirmado
Cancelar ordem se nao preencher rapido
Nao perseguir preco depois de 1 ou 2 tentativas
Medir spread antes de entrar
Medir slippage real depois de cada trade
Bloquear par se custo efetivo ficar alto
```

Custo efetivo por trade:

```txt
custo_total = taxa_entrada + taxa_saida + slippage_entrada + slippage_saida + funding_proporcional
```

Uma estrategia so e valida se o lucro medio por trade for maior que o custo total com margem de seguranca.

## Metas Realistas

Com US$1.000 e 50 trades/dia:

Exemplo de volume:

```txt
50 trades/dia x US$400 notional medio = US$20.000 volume diario
```

Se edge liquido for 0.02%:

```txt
US$20.000 x 0.02% = US$4/dia
```

Se edge liquido for 0.05%:

```txt
US$20.000 x 0.05% = US$10/dia
```

Cenario realista para um bot bom:

```txt
Dias bons: +US$4 a +US$15
Dias ruins: -US$10 a -US$30
Mes bom: +5% a +15%
Mes ruim: -5% a -15%
```

Qualquer promessa muito acima disso precisa ser tratada com suspeita ate passar por backtest e forward test.

## Criterios De Aprovacao No Backtest

Uma estrategia so deve ir para paper trading se cumprir:

```txt
Minimo de 2.000 trades
Profit factor > 1.15 liquido
Drawdown maximo < 15%
Win rate compativel com risco/retorno
Taxas reais incluidas
Slippage incluido
Funding incluido
Walk-forward positivo
Resultado continua aceitavel com slippage 2x
Resultado nao depende de apenas 1 ou 2 trades gigantes
```

Metricas obrigatorias:

- lucro liquido;
- drawdown maximo;
- profit factor;
- win rate;
- payoff medio;
- trades por dia;
- sequencia maxima de perdas;
- resultado por par;
- resultado por motor;
- resultado por horario;
- custo total por trade;
- exposicao media;
- tempo medio em posicao.

## Plano De Teste

### Fase 1: Backtest simples com candles

Dados:

```txt
OHLCV 5m
OHLCV 15m
funding
open interest
mark price
index price
```

Pares:

```txt
BTC, ETH, SOL, BNB, XRP, DOGE
```

Periodo minimo:

```txt
6 a 12 meses
```

### Fase 2: Backtest com custos

Adicionar:

```txt
maker fee
taker fee
spread estimado
slippage estimado
funding proporcional
latencia simulada
```

### Fase 3: Walk-forward

Exemplo:

```txt
Treina/ajusta em 60 dias
Testa nos proximos 15 dias
Anda a janela
Repete
```

### Fase 4: Paper trading

Rodar sem dinheiro real:

```txt
Minimo 2 semanas
Ideal 4 semanas
```

Aprovar apenas se o comportamento ao vivo parecer com o backtest.

### Fase 5: Dinheiro real pequeno

Comecar com:

```txt
Risco por trade: US$1
Maximo trades/dia: 10 a 20
Sem escalar ate completar 100 trades reais
```

## Referencias Pesquisadas

Linhas de pesquisa e autores relevantes:

- Avellaneda e Stoikov: market making;
- Cartea, Jaimungal e Penalva: algorithmic trading e market microstructure;
- Marcos Lopez de Prado: overfitting, validacao, Deflated Sharpe Ratio;
- Carol Alexander: cripto derivatives e arbitragem;
- estudos recentes sobre trend following, high-frequency crypto, funding rate, premium/basis e technical analysis.

Temas pesquisados:

- trend following e momentum em cripto;
- indicadores tecnicos em BTC e cripto;
- trading intraday em 5m/15m;
- high-frequency dynamics em BTC/ETH perps;
- funding rate e perpetual futures;
- premium/basis arbitrage;
- market making com limit order book;
- backtest overfitting e custos de transacao.

## Versao Inicial Recomendada

Para comecar de forma pratica:

```txt
Pares: BTC, ETH, SOL
Motores: Maker VWAP Reversion + Bollinger/ATR Breakout
Timeframes: 5m entrada, 15m regime
Trades/dia alvo: 10 a 25
Risco/trade: US$1.50 a US$3
Stop diario: -US$25
Sem martingale
```

Depois de validar:

```txt
Adicionar BNB, XRP, DOGE
Adicionar Candle-Turn Momentum
Adicionar Premium/Funding Reversal
Aumentar limite para 35 a 50 trades/dia
```

## Proxima Etapa

Construir um backtester que consiga:

1. baixar ou importar dados 5m/15m;
2. calcular indicadores;
3. simular entradas e saidas;
4. incluir taxas, slippage e funding;
5. separar resultado por par e por motor;
6. gerar relatorio final com metricas;
7. permitir otimizar parametros sem overfitting.

O primeiro objetivo nao e operar real. O primeiro objetivo e descobrir quais motores sobrevivem depois de custos.
