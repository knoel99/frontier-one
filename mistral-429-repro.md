# Mistral API: 429 / code 1300 on mistral-large-4 (1,000 TPM cap)

Run on 2026-11-16 at 16:40 UTC. Two calls to `mistral-large-4`, one small and one large.

| | Small call (19 prompt tokens) | Large call (~2,500 prompt tokens) |
|---|---|---|
| Time (UTC) | 16:40:45 | 16:40:49 |
| Status | 200 OK | **429 Too Many Requests** |
| `X-Ratelimit-Limit-Tokens-Minute` | 1000 | 1000 |
| `X-Ratelimit-Remaining-Tokens-Minute` | 971 (cost 29) | 971 |
| Request ID (`X-Kong-Request-Id`) | `01a1e53b-8806-7bea-9bbb-9afec944c538` | **`01a1e53b-9529-7307-a74a-754750ca70af`** |
| `Cf-Ray` | `a5b7f0793f1e28a2-IAD` | `a5b7f08e4de528a2-IAD` |

## 1. Small call (succeeds)

```bash
curl -sS -D - https://api.mistral.ai/v1/chat/completions -H "Authorization: Bearer $MISTRAL_API_KEY" -H 'Content-Type: application/json' -d '{"model":"mistral-large-4","max_tokens":10,"messages":[{"role":"user","content":"Dis bonjour."}]}'
```

Response:

```
HTTP/1.1 200 OK
Cf-Ray: a5b7f0793f1e28a2-IAD
Date: Mon, 16 Nov 2026 16:40:49 GMT
Mistral-Correlation-Id: 01a1e53b-8806-7bea-9bbb-9afec944c538
X-Kong-Proxy-Latency: 17
X-Kong-Request-Id: 01a1e53b-8806-7bea-9bbb-9afec944c538
X-Kong-Upstream-Latency: 3087
X-Ratelimit-Limit-Req-Minute: 2000
X-Ratelimit-Limit-Tokens-Minute: 1000
X-Ratelimit-Remaining-Req-Minute: 1999
X-Ratelimit-Remaining-Tokens-Minute: 971
X-Ratelimit-Tokens-Query-Cost: 29

{"id":"ba31b782456c47a48842f8ed8e8e9f91","created":1794847246,"model":"mistral-large-4","usage":{"prompt_tokens":19,"total_tokens":29,"completion_tokens":10,"prompt_tokens_details":{"cached_tokens":0},"completion_tokens_details":{"reasoning_tokens":9},"service_tier":"standard"},"object":"chat.completion","choices":[{"index":0,"finish_reason":"length","message":{"role":"assistant","tool_calls":null …
```

## 2. Large call (fails)

Build the payload (the sentence repeated 250 times):

```bash
python3 -c 'import json; print(json.dumps({"model":"mistral-large-4","max_tokens":10,"messages":[{"role":"user","content":"Résume ce texte. " + "Le renard brun saute par-dessus le chien paresseux. " * 250}]}))' > big.json
```

```bash
curl -sS -D - https://api.mistral.ai/v1/chat/completions -H "Authorization: Bearer $MISTRAL_API_KEY" -H 'Content-Type: application/json' -d @big.json
```

Response:

```
HTTP/1.1 429 Too Many Requests
Cf-Ray: a5b7f08e4de528a2-IAD
Date: Mon, 16 Nov 2026 16:40:49 GMT
Mistral-Correlation-Id: 01a1e53b-9529-7307-a74a-754750ca70af
X-Kong-Proxy-Latency: 20
X-Kong-Request-Id: 01a1e53b-9529-7307-a74a-754750ca70af
X-Kong-Upstream-Latency: 30
X-Ratelimit-Limit-Tokens-Minute: 1000
X-Ratelimit-Remaining-Tokens-Minute: 971

{"object":"error","message":"Rate limit exceeded","type":"rate_limited","param":null,"code":"1300","raw_status_code":429}
```

## Notes

- Headers shown are filtered to `HTTP`, `X-Ratelimit-*`, `X-Kong-*`, `Mistral-*`, `Cf-Ray` and `Date`. The 200 body is cut at 400 characters.
- The token limit is 1,000 TPM on both calls, while the console Rate limits page shows 10,000,000 TPM for mistral-large-4.
