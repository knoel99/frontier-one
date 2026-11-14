# Cloud infrastructure

- Use the ready-made functions first: `scw_offers`, `scw_create_instance`, `scw_instance`, `scw_delete_instance`, `rp_offers`, `rp_create_pod`, `rp_pod`, `rp_exec`, `rp_logs`, `rp_delete_pod`, `infra_resources`, `infra_cleanup`. They already handle prices, images, polling and leftover volumes, and their output is authoritative: do not re-check it with raw calls.
- For anything else, find the operation in the catalog (`api_docs` search, then show) and call it with `scaleway` or `runpod_mcp`. Do not search the web for the API and do not guess field names.
- Authentication is handled by the tools; never print, write or ask for keys.
- Spending (creating, changing or deleting a resource) is {{spend}}. When it is allowed: give the hourly price before creating anything, take the cheapest offer that does the job, and delete what you created as soon as its results are saved. Never touch a resource this run did not create unless the task names it.
- End with the table of `infra_resources`: what was created, its price, and whether it is deleted.
