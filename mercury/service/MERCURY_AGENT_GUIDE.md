# Mercury: agent integration guide

Mercury is an HTTP wallet and policy service: it resolves structured **intents**, runs simulations and **policy**, requests **human approval** when required, then **signs** (via custody, e.g. 1Claw) and **broadcasts** EVM transactions.

This document focuses on `**POST /v1/mercury/invoke`**, the native JSON API for coordinators and autonomous agents.

---

## Discovery


| Resource                              | URL                                          |
| ------------------------------------- | -------------------------------------------- |
| **OpenAPI (JSON)**                    | `GET /openapi.json`                          |
| **This guide (Markdown)**             | `GET /v1/mercury/invoke/guide`               |
| **Health**                            | `GET /healthz`                               |
| **Readiness + supported chains**      | `GET /readyz`                                |
| **Alchemy Notify (Address Activity)** | `POST /v1/webhooks/alchemy/address-activity` |


The OpenAPI document describes **request/response envelopes**. It does **not** enumerate every valid `intent` shape per `kind`; use the JSON patterns below together with `/openapi.json`.

---

## Alchemy integration (overview)

Mercury can call Alchemy **HTTP APIs** for read-only wallet intelligence and expose a **Notify webhook** for push events. These features use **two different secrets** in 1Claw (or test overrides):


| Secret purpose                                      | Default 1Claw path                         | Settings / env override                           |
| --------------------------------------------------- | ------------------------------------------ | ------------------------------------------------- |
| **REST API key** (Prices, Portfolio, Transfers RPC) | `mercury/apis/alchemy`                     | `MERCURY_ALCHEMY_API_SECRET_PATH`                 |
| **Webhook signing key** (HMAC on raw body)          | `mercury/apis/alchemy_webhook_signing_key` | `MERCURY_ALCHEMY_WEBHOOK_SIGNING_KEY_SECRET_PATH` |


**Registration:** `token_prices`, `portfolio_tokens`, and `transfer_history` are wired into the read-only graph only when `**MERCURY_ALCHEMY_API_SECRET_PATH`** is set to a **non-empty** path and the secret resolves. If that path is empty, those intents are unavailable (configure the key and restart).

**Invoke intents (Alchemy-backed):**


| `kind`             | Use when                                                                           | Chains (Mercury names)                     |
| ------------------ | ---------------------------------------------------------------------------------- | ------------------------------------------ |
| `token_prices`     | Spot quotes for one or many contracts                                              | `ethereum`, `base`, `arbitrum`, `optimism` |
| `portfolio_tokens` | Full fungible snapshot for one wallet (metadata, optional prices, native + ERC-20) | Same (up to **5** networks per request)    |
| `transfer_history` | Paged asset transfer history via `alchemy_getAssetTransfers`                       | One chain per request; same set            |


**Choosing Alchemy vs on-chain reads:**

- Prefer `**native_balance` / `erc20_balance`** when you need a single balance from Mercury’s RPC with no Alchemy dependency.
- Prefer `**portfolio_tokens**` when you need **many tokens at once**, **labels/metadata**, or **Alchemy-quoted USD** in one call.
- Prefer `**token_prices`** for **price-only** batches (up to 25 `(chain, token)` entries, 3 distinct chains).
- Prefer `**transfer_history`** for **indexed transfer lists**; it does not replace a full node trace—mind Alchemy pagination TTL for `page_key`.

Detailed JSON examples appear in the sections [Example: token prices](#example-token-prices-by-contract-read-only-alchemy), [Example: portfolio tokens](#example-portfolio-tokens-by-wallet-read-only-alchemy), and [Example: transfer history](#example-transfer-history-by-wallet-read-only-alchemy). Push delivery is documented in [Alchemy Address Activity webhooks](#alchemy-address-activity-webhooks-push).

---

## Alchemy Address Activity webhooks (push)

Mercury exposes `**POST /v1/webhooks/alchemy/address-activity`** for [Alchemy Notify Address Activity](https://www.alchemy.com/docs/reference/address-activity-webhook) deliveries. This path is **separate** from `POST /v1/mercury/invoke`: it verifies `**X-Alchemy-Signature`** with **HMAC-SHA256** over the **raw** request body using the webhook signing key, then runs a **small dedicated LangGraph** (normalize → optional incoming filter → in-memory dedupe → structured log/response).

**1Claw:** store the signing key at `MERCURY_ALCHEMY_WEBHOOK_SIGNING_KEY_SECRET_PATH` (default `mercury/apis/alchemy_webhook_signing_key`). This is **not** the same secret as the REST Alchemy API key (`mercury/apis/alchemy`).

**Tests / local overrides:** you may set `app.state.alchemy_webhook_signing_key` to a raw string so the route can run without resolving the vault.

**Watched addresses (optional):**

- Query: `?watched_addresses=0xabc,0xdef` (comma-separated, case-normalized).
- Or JSON extension (for clients you control): `metadata.watched_addresses` as a comma-separated string or JSON array of strings.

If **no** watch list is provided, **every** normalized activity row is eligible (no `toAddress` filter). If a watch list **is** provided, only rows whose normalized `**toAddress`** is in that set are emitted.

Duplicates across Alchemy retries are suppressed for about an hour using an in-process key
`(webhook_id, event_id, tx_hash, log_index)` (see implementation).

---

## Supported EVM chains

`GET /readyz` returns `supported_chains` dynamically. Mercury currently recognises **ethereum** (chain id **1**), **base** (**8453**), **arbitrum** (**42161**), **optimism** (**10**), and **monad** (**143**). Each chain needs an RPC secret at its 1Claw path (defaults: `mercury/rpc/ethereum`, `mercury/rpc/base`, `mercury/rpc/arbitrum`, `mercury/rpc/optimism`, `mercury/rpc/monad`).

CoW-backed swaps cover a subset (see README); Optimism swaps are typically routed via LiFi-compatible providers unless you extend CoW slugs locally.

---

## Known token / protocol catalog

Bundled catalog: `**mercury/data/known_addresses.json`** (also loaded at runtime via `kind: known_address`). It maps **tier 1–3** tickers and **protocol** contracts per chain **when verified**. Entries are keyed by numeric `chain_id` as strings; `chain_name_to_id` repeats canonical names.

- **Tier 1:** USDC, USDT, DAI, WBTC, WETH, LINK  
- **Tier 2:** GHO, wstETH, rETH, SNX, USDe, sUSDe, crvUSD (only present on chains where deployed)  
- **Tier 3:** EURC, cbETH; **OP** on Optimism; **ARB** on Arbitrum One

**Monad** may ship sparse or empty token lists until official deployments exist—do not assume L1 parity.

**Protocol JSON keys (`category` = `protocol`, dot-separated keys):**

- `AAVE_V3.pool_addresses_provider`, `AAVE_V3.pool` — Aave v3 periphery / pool proxies.  
- `MORPHO.morpho_blue` — Morpho Blue core (currently a **checksum zero-address placeholder**; replace before relying on Morpho tooling).

Treat the JSON/`known_address` result as authoritative for whichever keys exist **on that chain**. For `erc20_*`, `swap`, and `contract_read` you still pass **checksummed** `0x` addresses unless you resolved them first with `**known_address`**.

Aliases for the same intent: `**address_lookup**`, `**lookup_known_address**`.

---

## ENS names (human-readable addresses)

Mercury resolves **dot-separated ENS names** into checksummed `0x` addresses **before** graph execution. Resolution **starts on Ethereum mainnet ENS**, while the `**chain`** on your intent selects which **multichain address record** is read (ENSIP‑11 coin type)—for example a Base operation reads the Base address attached to the name, which may differ from the Ethereum mainnet address.

**Where you can use DNS-style names** (same JSON field names as hex addresses):

- `native_balance.wallet_address`
- `erc20_balance.wallet_address`
- `portfolio_tokens.wallet_address`
- `transfer_history.wallet_address`
- `erc20_allowance.owner_address`, `erc20_allowance.spender_address`
- `contract_read.contract_address`
- `native_transfer.recipient_address`
- `erc20_transfer.recipient_address`
- `erc20_approval.spender_address`
- `swap.recipient_address` *(optional)*

**Do not pass ENS names** for token contract selection; those fields still expect checksummed `0x` contracts (or resolve symbols with `kind: known_address`):

- `token_address`, `from_token`, `to_token`

Examples:

```json
{
  "user_id": "user-1",
  "wallet_id": "primary",
  "chain": "base",
  "intent": {
    "kind": "native_balance",
    "wallet_address": "vitalik.eth"
  }
}
```

```json
{
  "request_id": "req-send-ens",
  "user_id": "user-1",
  "wallet_id": "primary",
  "idempotency_key": "send-ens-1",
  "intent": {
    "kind": "native_transfer",
    "chain": "base",
    "wallet_id": "primary",
    "recipient_address": "alice.eth",
    "amount": "0.001"
  }
}
```

If a name cannot be resolved or has no address for the requested chain, `invoke` returns `validation_failed` with details (no RPC URLs are echoed).

---

## `POST /v1/mercury/invoke`

**Content-Type:** `application/json`

### Optional headers


| Header            | Purpose                                                                  |
| ----------------- | ------------------------------------------------------------------------ |
| `X-Request-ID`    | Correlates logs; falls back to `request_id` in body or a generated UUID. |
| `Idempotency-Key` | Same as body `idempotency_key` when you prefer headers.                  |


### Request body: `MercuryInvokeRequest`

Top-level fields (unknown top-level keys are **rejected** with HTTP 422):


| Field               | Required                     | Notes                                                                                    |
| ------------------- | ---------------------------- | ---------------------------------------------------------------------------------------- |
| `user_id`           | yes                          | Stable caller identity string.                                                           |
| `wallet_id`         | yes                          | Wallet id used for 1Claw paths (e.g. `primary`).                                         |
| `intent`            | yes                          | Object **or** string. For production agents, prefer a **structured object** with `kind`. |
| `chain`             | no                           | Optional default chain when omitted inside `intent`.                                     |
| `idempotency_key`   | required for value-moving    | Same value must be used for approval retry.                                              |
| `request_id`        | no                           | Request correlation.                                                                     |
| `approval_response` | when retrying after approval | **Must be top-level** (see [Approval](#approval-for-value-moving-transactions)).         |
| `metadata`          | no                           | Extra key/value metadata.                                                                |


---

## Intent kinds (overview)

### Read-only (no signing)

Examples: `native_balance`, `erc20_balance`, `erc20_allowance`, `erc20_metadata`, `contract_read`, `known_address`, `token_prices` (aliases: `get_token_prices`), `portfolio_tokens` (aliases: `get_portfolio_tokens`), `transfer_history` (aliases: `get_transfer_history`).

These do **not** require idempotency or approval in the same way as transfers.

### Value-moving (signing + policy + often approval)

Includes: `native_transfer`, `erc20_transfer`, `erc20_approval`, `swap`.

Requirements:

1. `**idempotency_key`** — in the **body** (or `Idempotency-Key` header). Value-moving transactions are rejected without it.
2. **Policy** may require **human approval** before signing.
3. **String-only** `intent` (natural language) is **not** used for these; use a structured `intent` object with `kind`.

---

## Example: read native balance

```json
{
  "user_id": "user-1",
  "wallet_id": "primary",
  "chain": "base",
  "intent": {
    "kind": "native_balance",
    "wallet_address": "0x000000000000000000000000000000000000dEaD"
  }
}
```

---

## Example: resolve bundled USDC address (read-only)

```json
{
  "user_id": "user-1",
  "wallet_id": "primary",
  "chain": "ethereum",
  "intent": {
    "kind": "known_address",
    "category": "token",
    "key": "USDC"
  }
}
```

Use `category: protocol` plus keys such as `**AAVE_V3.pool**` for contract addresses referenced in simulations or `contract_read` calls.

---

## Example: read ERC20 token balance (read-only)

Use `**erc20_balance**` when you already have a checksummed contract address for the token.

```json
{
  "user_id": "user-1",
  "wallet_id": "primary",
  "chain": "base",
  "intent": {
    "kind": "erc20_balance",
    "token_address": "0x0555E30Da8F98308edB960aa94C0DBA30dd6B0c2",
    "wallet_address": "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
  }
}
```

Resolve symbol tickers (WBTC, USDC, …) via `**kind: known_address**` on the same chain **before** calling `erc20_balance`.

---

## Example: token prices by contract (read-only, Alchemy)

`**token_prices`** calls Alchemy’s **Prices API** using the API key from the 1Claw path `**mercury/apis/alchemy`** (override via settings / `MERCURY_ALCHEMY_API_SECRET_PATH`). Up to **25** `(chain, token_address)` pairs and **3** distinct chains per request (`ethereum`, `base`, `arbitrum`, `optimism`).

Single token:

```json
{
  "user_id": "user-1",
  "wallet_id": "primary",
  "intent": {
    "kind": "token_prices",
    "chain": "base",
    "token_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
  }
}
```

Batch:

```json
{
  "user_id": "user-1",
  "wallet_id": "primary",
  "intent": {
    "kind": "token_prices",
    "tokens": [
      { "chain": "base", "token_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913" },
      { "chain": "ethereum", "token_address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48" }
    ]
  }
}
```

---

## Example: portfolio tokens by wallet (read-only, Alchemy)

`**portfolio_tokens**` uses Alchemy’s **Portfolio API** (`tokens/by-address`) with the same 1Claw API key path as `**token_prices`** (`mercury/apis/alchemy`, overridable via `MERCURY_ALCHEMY_API_SECRET_PATH`). One wallet per request, up to **five** networks among `ethereum`, `base`, `arbitrum`, and `optimism`. Optional flags: `with_metadata`, `with_prices`, `include_native_tokens`, `include_erc20_tokens`. Use either Mercury `chains`, raw Alchemy `networks` (e.g. `eth-mainnet`), or a default `chain` when querying a single network.

**Structured `data` / tool rows:** each token entry includes `**balance`** (raw string from Alchemy, often **hex**) and `**balance_display`** (decimal **human** amount: `token_balance / 10**decimals`, using `tokenMetadata.decimals` when present, **18** for native when decimals are missing, or `"<integer> (raw base units)"` when decimals are unknown for an ERC-20). Agents building tables or summaries for users should prefer `**balance_display`** (and the invoke `**message**`, which uses the same formatting) instead of echoing hex.

```json
{
  "user_id": "user-1",
  "wallet_id": "primary",
  "intent": {
    "kind": "portfolio_tokens",
    "wallet_address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
    "chains": ["ethereum", "base"],
    "with_prices": true,
    "with_metadata": true
  }
}
```

If the tool result includes `page_key`, pass it back as `page_key` on a follow-up intent to fetch the next page.

---

## Example: transfer history by wallet (read-only, Alchemy)

`**transfer_history**` calls Alchemy’s **Transfers API** (`alchemy_getAssetTransfers` JSON-RPC) on the chain’s Alchemy node URL, using the same 1Claw API key path as `**token_prices`** (`mercury/apis/alchemy`, overridable via `MERCURY_ALCHEMY_API_SECRET_PATH`). One **Mercury `chain`** and one `**wallet_address**` per request (`ethereum`, `base`, `arbitrum`, `optimism`). Optional: `direction` (`incoming`, `outgoing`, or `both`; aliases `in` / `out`), `categories` (defaults to `external`, `internal`, `erc20`, `erc721`, `erc1155`), `from_block`, `to_block`, `max_count` (1–1000, default 100), `page_key`, `with_metadata`, `exclude_zero_value`.

When `**direction` is `both**`, Mercury issues separate incoming and outgoing queries and merges the first page (deduped by transaction hash); `**page_key` pagination is not used**—use `**incoming`** or `**outgoing**` if you need to page (echo `page_key` within Alchemy’s short TTL, often around ten minutes).

```json
{
  "user_id": "user-1",
  "wallet_id": "primary",
  "intent": {
    "kind": "transfer_history",
    "chain": "base",
    "wallet_address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
    "direction": "incoming",
    "max_count": 25
  }
}
```

---

## Example: arbitrary contract view call (`contract_read`)

Avoid Solidity signature strings like `function balanceOf(...)`. Mercury expects `**abi_fragment**` as a **JSON array** of EIP-712 ABI fragments (objects), plus `**function_name`** and `**args**`:

```json
{
  "user_id": "user-1",
  "wallet_id": "primary",
  "chain": "base",
  "intent": {
    "kind": "contract_read",
    "contract_address": "0x0555E30Da8F98308edB960aa94C0DBA30dd6B0c2",
    "abi_fragment": [
      {
        "type": "function",
        "name": "balanceOf",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"type": "uint256"}]
      }
    ],
    "function_name": "balanceOf",
    "args": ["0xc1923710468607b8b7db38a6afbb9b432744390c"]
  }
}
```

Standard ERC20 balances should still prefer `**erc20_balance**` instead of encoding `balanceOf` yourself.

---

## Example: ERC20 transfer (first call — approval often required)

Use a stable idempotency key for this logical operation. Either set `**idempotency_key**` on the body or pass `**Idempotency-Key**`.

**Body `idempotency_key`:**

```json
{
  "request_id": "req-transfer-1",
  "user_id": "user-1",
  "wallet_id": "primary",
  "idempotency_key": "erc20-transfer-1",
  "intent": {
    "kind": "erc20_transfer",
    "chain": "base",
    "wallet_id": "primary",
    "token_address": "0x000000000000000000000000000000000000cafE",
    "recipient_address": "0x000000000000000000000000000000000000bEEF",
    "amount": "1.5"
  }
}
```

**Amount:** By default `**amount`** is a **human-readable decimal** in whole tokens (e.g. `"1.5"` for one and a half USDC on a 6‑decimal token). Agents that supply a **nonnegative integer string in the token’s smallest units** (often called “wei” for 18‑decimal assets) should set `**"amount_in_smallest_units": true`**. Example: for USDC with 6 decimals, `"1000000"` with that flag is exactly 1 USDC; `"1000"` is 0.001 USDC.

**Equivalent idempotency via header** (omit `idempotency_key` from body if you only use the header):

```http
Idempotency-Key: erc20-transfer-1
```

```json
{
  "request_id": "req-transfer-1",
  "user_id": "user-1",
  "wallet_id": "primary",
  "intent": {
    "kind": "erc20_transfer",
    "chain": "base",
    "wallet_id": "primary",
    "token_address": "0x000000000000000000000000000000000000cafE",
    "recipient_address": "0x000000000000000000000000000000000000bEEF",
    "amount": "1.5"
  }
}
```

With the default **request-metadata** approver, the first response may be HTTP 200 with `status: "approval_required"` and `approval_required: true` instead of a `tx_hash`.

---

## Approval for value-moving transactions

After a human or operator confirms the **same** transfer (same amount, recipient, token, chain, idempotency), call `**invoke` again** with:

- The **same** `intent` (same parameters).
- The **same** `idempotency_key` as the prepared transfer.
- `**approval_response`** as a **top-level** field — **not** nested only under `intent`.

If you include `idempotency_key` inside `approval_response`, it **must match** the transaction’s idempotency key.

```json
{
  "user_id": "user-1",
  "wallet_id": "primary",
  "idempotency_key": "erc20-transfer-1",
  "intent": {
    "kind": "erc20_transfer",
    "chain": "base",
    "wallet_id": "primary",
    "token_address": "0x000000000000000000000000000000000000cafE",
    "recipient_address": "0x000000000000000000000000000000000000bEEF",
    "amount": "1.5"
  },
  "approval_response": {
    "status": "approved",
    "idempotency_key": "erc20-transfer-1",
    "approved_by": "operator-or-user-id",
    "reason": "User confirmed in chat"
  }
}
```

### Common mistake

Placing `**approval_response` only inside `intent**` does **not** wire into the approval step the server expects from a top-level `MercuryInvokeRequest`. Use the top-level field as shown.

---

## Example: swap intent (sketch)

```json
{
  "request_id": "req-swap-1",
  "user_id": "user-1",
  "wallet_id": "primary",
  "idempotency_key": "swap-base-1",
  "intent": {
    "kind": "swap",
    "chain": "base",
    "from_token": "0x000000000000000000000000000000000000cafE",
    "to_token": "0x000000000000000000000000000000000000dEaD",
    "amount_in": "10",
    "max_slippage_bps": 50,
    "provider_preference": "lifi"
  }
}
```

**Selling native gas token (e.g. ETH on Base):** use the canonical zero placeholder as `from_token`: `0x0000000000000000000000000000000000000000`. Some clients use `0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE`; Mercury normalizes that to the same sentinel. `**to_token`** must still be a checksummed **ERC-20** contract (not the sentinel). `**amount_in`** for native sells is interpreted with **18** decimals.

Mercury skips the ERC-20 allowance/approval preparation when `from_token` is the native sentinel; the resulting swap transaction may carry **non-zero native `value`** (wei). **LiFi** supports these routes in Mercury. **CoW** and **Uniswap** adapter paths reject selling the native asset directly unless you swap using **wrapped** native (e.g. WETH) as `from_token`.

### Example: Base ETH → USDC (native `from_token`, LiFi)

```json
{
  "request_id": "req-native-sell-1",
  "user_id": "user-1",
  "wallet_id": "primary",
  "idempotency_key": "swap-base-eth-native-1",
  "intent": {
    "kind": "swap",
    "chain": "base",
    "wallet_id": "primary",
    "from_token": "0x0000000000000000000000000000000000000000",
    "to_token": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "amount_in": "0.01",
    "max_slippage_bps": 50,
    "provider_preference": "lifi"
  }
}
```

### Cross-chain swap / bridge (`swap`)

Use the same `**kind: "swap"**` intent as a same-chain swap, but describe **two networks**:


| Field                          | Role                                                                                |
| ------------------------------ | ----------------------------------------------------------------------------------- |
| `**chain`**                    | **Source** network (where the wallet spends `from_token`).                          |
| `**to_chain`** *(optional)*    | **Destination** network name (Mercury canonical name, e.g. `ethereum`, `arbitrum`). |
| `**to_chain_id*`* *(optional)* | **Destination** EVM chain id (e.g. `1` for Ethereum mainnet).                       |


You may send `**to_chain_id` only** (destination resolved internally), `**to_chain` only**, or **both**; if both are present, they must refer to the **same** chain or validation fails.

**Tokens**

- `**from_token*`* — checksummed ERC-20 (or the canonical **native** sell sentinel on the source chain—see the Base ETH → USDC native example above) on the **source** `chain`. `**amount_in`** uses this token’s decimals.
- `**to_token**` — checksummed ERC-20 on the **destination** chain—the asset you want **on the other side**. It must **not** be the same logical position as `from_token` on the wrong chain; use the **destination** contract address (resolve via `**kind: known_address`** on `**to_chain**` when needed).

**Providers**

Cross-chain quotes are typically obtained via **LiFi** (`provider_preference: "lifi"`). Same-chain-leaning adapters may not support bridge routes; prefer LiFi when bridging.

**Pipeline**

ERC-20 allowance and approval behavior on the **source** chain is the same as for same-chain swaps. The returned transaction may be a bridge step LiFi expects the wallet to sign and broadcast.

### Example: Base → Ethereum USDC bridge sketch (LiFi)

```json
{
  "request_id": "req-bridge-1",
  "user_id": "user-1",
  "wallet_id": "primary",
  "idempotency_key": "swap-base-to-eth-usdc-1",
  "intent": {
    "kind": "swap",
    "chain": "base",
    "wallet_id": "primary",
    "from_token": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "to_token": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "to_chain": "ethereum",
    "to_chain_id": 1,
    "amount_in": "50",
    "max_slippage_bps": 50,
    "provider_preference": "lifi"
  }
}
```

---

## Responses

Success is **HTTP 200** with a `**MercuryInvokeResponse`**: `request_id`, `status`, `message`, optional `data`, `tx_hash`, `receipt`, `approval_required`, `approval_payload`, `error`.

- `**message**` — human-oriented summary. For `**portfolio_tokens**`, token amounts in the summary are **decimal** (not raw hex); structured rows still carry `**balance`** and `**balance_display**` as described [above](#example-portfolio-tokens-by-wallet-read-only-alchemy).
- `**approval_required` / `approval_denied**` — treat as “not signed yet”; retry with `[approval_response](#approval-for-value-moving-transactions)` when appropriate.
- `**rejected**` / policy — e.g. missing idempotency key, simulation failure, policy rule.
- **HTTP 422** — JSON failed Pydantic validation (wrong fields, extra top-level keys on `MercuryInvokeRequest`).

---

## Pan-agentikit envelope API (optional)

For coordinator ↔ Mercury with **envelopes**, use `**POST /v1/agent`** with a `PanAgentEnvelope` body. Inbound payloads support `user_message` and `task_request`; value-moving work should use `**task_request**` with a structured `intent` / `input`, not plain `user_message` alone for transfers.

See the repository **README** and `mercury/service/pan_agentikit_models.py` for payload shapes.

---

## cURL template (invoke)

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/mercury/invoke" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: my-req-1" \
  -H "Idempotency-Key: my-idem-1" \
  -d @body.json
```

---

*Generated for automated agents and human operators. Version follows the Mercury service deployment.*