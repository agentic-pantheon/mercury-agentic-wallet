# Mercury: agent integration guide

Mercury is an HTTP wallet and policy service: it resolves structured **intents**, runs simulations and **policy**, requests **human approval** when required, then **signs** (via custody, e.g. 1Claw) and **broadcasts** EVM transactions.

This document focuses on **`POST /v1/mercury/invoke`**, the native JSON API for coordinators and autonomous agents.

---

## Discovery

| Resource | URL |
|----------|-----|
| **OpenAPI (JSON)** | `GET /openapi.json` |
| **This guide (Markdown)** | `GET /v1/mercury/invoke/guide` |
| **Health** | `GET /healthz` |
| **Readiness + supported chains** | `GET /readyz` |

The OpenAPI document describes **request/response envelopes**. It does **not** enumerate every valid `intent` shape per `kind`; use the JSON patterns below together with `/openapi.json`.

---

## Supported EVM chains

`GET /readyz` returns `supported_chains` dynamically. Mercury currently recognises **ethereum** (chain id **1**), **base** (**8453**), **arbitrum** (**42161**), **optimism** (**10**), and **monad** (**143**). Each chain needs an RPC secret at its 1Claw path (defaults: `mercury/rpc/ethereum`, `mercury/rpc/base`, `mercury/rpc/arbitrum`, `mercury/rpc/optimism`, `mercury/rpc/monad`).

CoW-backed swaps cover a subset (see README); Optimism swaps are typically routed via LiFi-compatible providers unless you extend CoW slugs locally.

---

## Known token / protocol catalog

Bundled catalog: **`mercury/data/known_addresses.json`** (also loaded at runtime via `kind: known_address`). It maps **tier 1–3** tickers and **protocol** contracts per chain **when verified**. Entries are keyed by numeric `chain_id` as strings; `chain_name_to_id` repeats canonical names.

- **Tier 1:** USDC, USDT, DAI, WBTC, WETH, LINK  
- **Tier 2:** GHO, wstETH, rETH, SNX, USDe, sUSDe, crvUSD (only present on chains where deployed)  
- **Tier 3:** EURC, cbETH; **OP** on Optimism; **ARB** on Arbitrum One  

**Monad** may ship sparse or empty token lists until official deployments exist—do not assume L1 parity.

**Protocol JSON keys (`category` = `protocol`, dot-separated keys):**

- `AAVE_V3.pool_addresses_provider`, `AAVE_V3.pool` — Aave v3 periphery / pool proxies.  
- `MORPHO.morpho_blue` — Morpho Blue core (currently a **checksum zero-address placeholder**; replace before relying on Morpho tooling).

Treat the JSON/`known_address` result as authoritative for whichever keys exist **on that chain**. For `erc20_*`, `swap`, and `contract_read` you still pass **checksummed** `0x` addresses unless you resolved them first with **`known_address`**.

Aliases for the same intent: **`address_lookup`**, **`lookup_known_address`**.

---

## `POST /v1/mercury/invoke`

**Content-Type:** `application/json`

### Optional headers

| Header | Purpose |
|--------|---------|
| `X-Request-ID` | Correlates logs; falls back to `request_id` in body or a generated UUID. |
| `Idempotency-Key` | Same as body `idempotency_key` when you prefer headers. |

### Request body: `MercuryInvokeRequest`

Top-level fields (unknown top-level keys are **rejected** with HTTP 422):

| Field | Required | Notes |
|-------|----------|--------|
| `user_id` | yes | Stable caller identity string. |
| `wallet_id` | yes | Wallet id used for 1Claw paths (e.g. `primary`). |
| `intent` | yes | Object **or** string. For production agents, prefer a **structured object** with `kind`. |
| `chain` | no | Optional default chain when omitted inside `intent`. |
| `idempotency_key` | required for value-moving | Same value must be used for approval retry. |
| `request_id` | no | Request correlation. |
| `approval_response` | when retrying after approval | **Must be top-level** (see [Approval](#approval-for-value-moving-transactions)). |
| `metadata` | no | Extra key/value metadata. |

---

## Intent kinds (overview)

### Read-only (no signing)

Examples: `native_balance`, `erc20_balance`, `erc20_allowance`, `erc20_metadata`, `contract_read`, `known_address`.

These do **not** require idempotency or approval in the same way as transfers.

### Value-moving (signing + policy + often approval)

Includes: `native_transfer`, `erc20_transfer`, `erc20_approval`, `swap`.

Requirements:

1. **`idempotency_key`** — in the **body** (or `Idempotency-Key` header). Value-moving transactions are rejected without it.
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

Use `category: protocol` plus keys such as **`AAVE_V3.pool`** for contract addresses referenced in simulations or `contract_read` calls.

---

## Example: read ERC20 token balance (read-only)

Use **`erc20_balance`** when you already have a checksummed contract address for the token.

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

Resolve symbol tickers (WBTC, USDC, …) via **`kind: known_address`** on the same chain **before** calling `erc20_balance`.

---

## Example: arbitrary contract view call (`contract_read`)

Avoid Solidity signature strings like ``function balanceOf(...)``. Mercury expects **`abi_fragment`** as a **JSON array** of EIP-712 ABI fragments (objects), plus **`function_name`** and **`args`**:

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

Standard ERC20 balances should still prefer **`erc20_balance`** instead of encoding `balanceOf` yourself.

---

## Example: ERC20 transfer (first call — approval often required)

Use a stable idempotency key for this logical operation. Either set **`idempotency_key`** on the body or pass **`Idempotency-Key`**.

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

**Amount:** By default **`amount`** is a **human-readable decimal** in whole tokens (e.g. `"1.5"` for one and a half USDC on a 6‑decimal token). Agents that supply a **nonnegative integer string in the token’s smallest units** (often called “wei” for 18‑decimal assets) should set **`"amount_in_smallest_units": true`**. Example: for USDC with 6 decimals, `"1000000"` with that flag is exactly 1 USDC; `"1000"` is 0.001 USDC.

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

After a human or operator confirms the **same** transfer (same amount, recipient, token, chain, idempotency), call **`invoke` again** with:

- The **same** `intent` (same parameters).
- The **same** `idempotency_key` as the prepared transfer.
- **`approval_response`** as a **top-level** field — **not** nested only under `intent`.

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

Placing **`approval_response` only inside `intent`** does **not** wire into the approval step the server expects from a top-level `MercuryInvokeRequest`. Use the top-level field as shown.

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

---

## Responses

Success is **HTTP 200** with a **`MercuryInvokeResponse`**: `request_id`, `status`, `message`, optional `data`, `tx_hash`, `receipt`, `approval_required`, `approval_payload`, `error`.

- **`approval_required` / `approval_denied`** — treat as “not signed yet”; retry with [`approval_response`](#approval-for-value-moving-transactions) when appropriate.
- **`rejected`** / policy — e.g. missing idempotency key, simulation failure, policy rule.
- **HTTP 422** — JSON failed Pydantic validation (wrong fields, extra top-level keys on `MercuryInvokeRequest`).

---

## Pan-agentikit envelope API (optional)

For coordinator ↔ Mercury with **envelopes**, use **`POST /v1/agent`** with a `PanAgentEnvelope` body. Inbound payloads support `user_message` and `task_request`; value-moving work should use **`task_request`** with a structured `intent` / `input`, not plain `user_message` alone for transfers.

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
