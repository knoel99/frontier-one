# Scaleway: quick guide for agents

Written for this framework on 2026-11-14 and checked with read-only calls that day. The ready-made functions
(`scw_offers`, `scw_create_instance`, `scw_instance`, `scw_list_instances`, `scw_delete_instance`) implement the
flows below; this guide is for what they do not cover. Signatures of every operation: `api_docs doc=scaleway/...`.

## Calls

- Base URL `https://api.scaleway.com`; header `X-Auth-Token: <secret key>`. The `scaleway` tool adds it (from
  `SCW_SECRET_KEY`, or the network proxy injects it) and fills `{zone}`, `{region}`, `{project_id}` in the path from
  `SCW_DEFAULT_ZONE` (default `fr-par-2`), `SCW_DEFAULT_REGION`, `SCW_DEFAULT_PROJECT_ID`.
- Zones: `fr-par-1/2/3`, `nl-ams-1/2/3`, `pl-waw-1/2/3`, `it-mil-1`. GPU Instances are mostly in `fr-par-2`.
- Lists are paginated: Instance API `per_page` (max 100) and `page`; newer APIs `page_size` and `page`. Read
  `total_count` and the `x-total-count` header.
- Errors come back as JSON with `type` and `message` (e.g. `quotas_exceeded`, `out_of_stock`).

## Prices: read them from the Product Catalog, not from the Instance API

`hourly_price` in `GET /instance/v1/zones/{zone}/products/servers` is `0.0` for every type (seen 2026-11-14).
The price list is the public Product Catalog (not in the bundled specs; checked live):

```
GET /product-catalog/v2alpha1/public-catalog/products?product_types=instance&zone=fr-par-2&page_size=500
```

Each product has `product` (the commercial type), `status` (`general_availability`, `retired`, ...),
`price.retail_price` (`units` + `nanos`/1e9, EUR) and `unit_of_measure`. **GPU types are priced per minute**
(e.g. `H100-1-80G`: 0.047775 €/minute = 2.87 €/hour; `L4-1-24G` is also per minute); CPU types per hour.
Always multiply by 60 for a per-minute unit before reporting an hourly price.

## Create a GPU Instance, run, delete

1. Type and stock: `GET /instance/v1/zones/{zone}/products/servers?per_page=100` (types, `gpu`, `gpu_info`, `ram`)
   and `GET /instance/v1/zones/{zone}/products/servers/availability` (`available`, `scarce`, `shortage`).
   GPU types seen in fr-par-2: `L4-1/2/4/8-24G`, `L40S-1/2/4/8-48G`, `H100-1/2-80G`, `H100-SXM-2/4/8-80G`,
   `B300-SXM-8-288G`, `RENDER-S`.
2. Image: `GET /marketplace/v2/local-images?image_label=ubuntu_noble_gpu_os_12&zone=fr-par-2&type=instance_sbs`
   returns the image `id` and `compatible_commercial_types`. GPU labels: `ubuntu_noble_gpu_os_12`,
   `ubuntu_jammy_gpu_os_12`, `ubuntu_noble_gpu_os_13_nvidia` (NVIDIA driver and Docker preinstalled); CPU:
   `ubuntu_noble`, `ubuntu_jammy`, ...
3. Create (operation `scaleway/instance:CreateServer`):
   `POST /instance/v1/zones/{zone}/servers` with
   `{"name": "...", "commercial_type": "L4-1-24G", "image": "<id>", "project": "<project_id>", "tags": ["frontier", "<run id>"]}`.
   The server is created **stopped** and its Block Storage root volume already bills.
4. Cloud-init (optional, before the first boot): `PATCH /instance/v1/zones/{zone}/servers/{server_id}/user_data/cloud-init`
   with the script as a `text/plain` body (`content_type="text/plain"` in the tool).
5. Start: `POST /instance/v1/zones/{zone}/servers/{server_id}/action` `{"action": "poweron"}`; poll
   `GET .../servers/{server_id}` until `state` is `running`; `public_ip.address` (or `public_ips`) gives the address.
   SSH keys of the project (IAM `ListSSHKeys`) are installed for `root`.
6. Delete: `{"action": "terminate"}` deletes the server and its `l_ssd`/`scratch` volumes, but **`sbs_volume`
   volumes are only detached and keep billing**: list them (`GET /block/v1/zones/{zone}/volumes?project_id=...`)
   and delete them (`DELETE /block/v1/zones/{zone}/volumes/{volume_id}`). A flexible IP that was created separately
   must be deleted too (`DELETE /instance/v1/zones/{zone}/ips/{ip}`).
   `poweroff` stops billing for the server but not for its volumes.

## Other services in the bundled specs

`serverless-jobs` (container batch jobs billed per second, CPU), `inference` (dedicated model deployments),
`generative-apis` (OpenAI-compatible serverless LLM endpoints), `billing` (consumption), `iam` (keys, SSH keys),
`vpc`/`ipam`, `registry`, `secret-manager`, `file-storage`, `block`, `marketplace`.
Object Storage is S3-compatible (`https://s3.<region>.scw.cloud`, guide page
`scaleway/page:object-storage__api-cli__using-api-call-list`).
