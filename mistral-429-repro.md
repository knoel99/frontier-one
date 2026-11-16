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

## Raw requests and responses

Re-run on 2026-11-16 at 16:44 UTC, full output of `curl -i` (all response headers, full body). The only line removed is
`HTTP/1.1 200 Connection Established`, which comes from the local HTTPS proxy, not from Mistral. The `Authorization`
header is added by that proxy; outside it, add `-H "Authorization: Bearer $MISTRAL_API_KEY"`.

### Small call

```bash
curl -sS -i https://api.mistral.ai/v1/chat/completions -H 'Content-Type: application/json' -H "Authorization: Bearer $MISTRAL_API_KEY" -d '{"model": "mistral-large-4", "max_tokens": 10, "messages": [{"role": "user", "content": "Dis bonjour."}]}'
```

```
HTTP/1.1 200 OK
Connection: close
Content-Length: 525
Access-Control-Allow-Origin: *
Alt-Svc: h3=":443"; ma=86400
Cf-Cache-Status: DYNAMIC
Cf-Ray: a5b7f5844ae4e627-IAD
Connection: keep-alive
Content-Type: application/json
Date: Mon, 16 Nov 2026 16:44:14 GMT
Mistral-Correlation-Id: 01a1e53e-aeee-74ec-ab6a-1e7c1def8ec5
Server: cloudflare
Set-Cookie: __cf_bm=ymeSFufin1_z6boAYXuOVIM2V9VZRxK1u8yRxK2i2Eg-1794847452.8482158-1.0.1.1-C0UsflKRQKIJOfCFXQw2HDOoACga.2etC_1_1MNp0BiImFq5ToRUo2dJ2ogWjz0vG1odBbLqD29rLAMIrYlpW.0upngGG3KWkJzZQMw5VsIBimLzEJNOWk.saMcyhqWn; HttpOnly; SameSite=None; Partitioned; Secure; Path=/; Domain=mistral.ai; Expires=Mon, 16 Nov 2026 17:14:14 GMT
Strict-Transport-Security: max-age=15552000; includeSubDomains; preload
X-Content-Type-Options: nosniff
X-Envoy-Upstream-Service-Time: 1572
X-Kong-Proxy-Latency: 15
X-Kong-Request-Id: 01a1e53e-aeee-74ec-ab6a-1e7c1def8ec5
X-Kong-Upstream-Latency: 1574
X-Max-Retry-Attempts-Reached: false
X-Ratelimit-Limit-Req-Minute: 2000
X-Ratelimit-Limit-Tokens-Minute: 1000
X-Ratelimit-Remaining-Req-Minute: 1999
X-Ratelimit-Remaining-Tokens-Minute: 971
X-Ratelimit-Tokens-Query-Cost: 29

{"id":"5f2bbd0c322043738d671e1f97b9b3e2","created":1794847453,"model":"mistral-large-4","usage":{"prompt_tokens":19,"total_tokens":29,"completion_tokens":10,"prompt_tokens_details":{"cached_tokens":0},"completion_tokens_details":{"reasoning_tokens":9},"service_tier":"standard"},"object":"chat.completion","choices":[{"index":0,"finish_reason":"length","message":{"role":"assistant","tool_calls":null,"content":[{"type":"thinking","thinking":[{"type":"text","text":"The user said \"Dis bonjour.\" which"}],"closed":true}]}}]}
```

### Large call

```bash
curl -sS -i https://api.mistral.ai/v1/chat/completions -H 'Content-Type: application/json' -H "Authorization: Bearer $MISTRAL_API_KEY" -d @big.json
```

<details><summary>Full content of <code>big.json</code></summary>

```json
{"model": "mistral-large-4", "max_tokens": 10, "messages": [{"role": "user", "content": "R\u00e9sume ce texte. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. Le renard brun saute par-dessus le chien paresseux. "}]}
```

</details>

```
HTTP/1.1 429 Too Many Requests
Connection: close
Content-Length: 121
Access-Control-Allow-Origin: *
Alt-Svc: h3=":443"; ma=86400
Cf-Cache-Status: DYNAMIC
Cf-Ray: a5b7f58fcbaee627-IAD
Connection: keep-alive
Content-Type: application/json
Date: Mon, 16 Nov 2026 16:44:14 GMT
Mistral-Correlation-Id: 01a1e53e-b621-7035-8133-a414cd19f483
Server: cloudflare
Set-Cookie: __cf_bm=utncksXPplTEdmwc.af5tHZ1MqpvKf0DIOvaoWVG3q8-1794847454.6901371-1.0.1.1-v15ulPhxrcW8pMCcFINm2NsRoQ8eLBcJWtx3D26T8emylq3RGMH_qGG1FRkiDN9NKy59UgWQM0GDxcGihAwIpdpW1RmtzpzlOHTUBZjTY6wWEwkzIV5BMtAXu2NOhdoG; HttpOnly; SameSite=None; Partitioned; Secure; Path=/; Domain=mistral.ai; Expires=Mon, 16 Nov 2026 17:14:14 GMT
Strict-Transport-Security: max-age=15552000; includeSubDomains; preload
X-Content-Type-Options: nosniff
X-Envoy-Upstream-Service-Time: 70
X-Kong-Proxy-Latency: 17
X-Kong-Request-Id: 01a1e53e-b621-7035-8133-a414cd19f483
X-Kong-Upstream-Latency: 71
X-Ratelimit-Limit-Tokens-Minute: 1000
X-Ratelimit-Remaining-Tokens-Minute: 971

{"object":"error","message":"Rate limit exceeded","type":"rate_limited","param":null,"code":"1300","raw_status_code":429}
```
