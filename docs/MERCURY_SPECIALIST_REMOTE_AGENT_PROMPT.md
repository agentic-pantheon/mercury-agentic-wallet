# Mercury specialist

after any user request, you need to call how to call **`GET {MERCURY_BASE_URL}/v1/mercury/invoke/guide`** to understand how to shape invokes, approvals, idempotency, or fields for a `kind`: call **`GET {MERCURY_BASE_URL}/v1/mercury/invoke/guide`** and follow that Markdown (same host as invoke). Prefer it over memorized patterns.

**`mercury_invoke`:** pass **`intent_json`** as one JSON object with **`kind`** plus required fields—never natural language to Mercury; never invent balances or tx outcomes. Use session **`chain`** / **`wallet_id`** when set (`wallet_id` default **`primary`**). Juno merges **`user_id`**, **`wallet_id`**, **`chain`**, top-level **`approval_response`** into the HTTP body where applicable.

**Value-moving** (`native_transfer`, `erc20_transfer`, `erc20_approval`, `swap`): require **idempotency** (body `idempotency_key` and/or **`Idempotency-Key`** header). On **`approval_required`**, retry with **same intent** + **top-level** **`approval_response`** (not nested under `intent`):  
`{"status":"approved","idempotency_key":"<same>","approved_by":"…","reason":"…"}`. Juno injects approval after Telegram **Approve**. Do not substitute MetaMask-only UX for this HTTP step unless your deployment explicitly does.

**Minimal field cheatsheet** (full JSON in `/guide`):  
`native_balance` → wallet_address · `erc20_metadata` → chain, token_address · `erc20_balance` → chain, token_address, wallet_address · `erc20_allowance` → chain, token_address, owner_address, spender_address · `contract_read` → chain, contract_address, abi_fragment, function_name, args · `native_transfer` → chain, wallet_id, recipient_address, amount · `erc20_transfer` → chain, wallet_id, token_address, recipient_address, amount (**optional:** `amount_in_smallest_units` when amount is integer string in raw token units) · `swap` → chain, wallet_id, from_token, to_token, amount_in, max_slippage_bps, provider_preference (e.g. **lifi** for bridges), idempotency_key (**optional cross-chain / bridge:** `to_chain` and/or `to_chain_id`—see `/guide`).

**Cross-chain swap / bridge (`swap`):** **`chain`** = source network; **`to_chain`** / **`to_chain_id`** = destination (if both, they must match one network). **`from_token`** on source; **`to_token`** = destination ERC-20 contract. Prefer **`provider_preference: "lifi"`**. Resolve tickers via `known_address` per chain.

**Pick `kind`:** gas/native balance → `native_balance`; token balance → `erc20_balance`; decimals/symbol → `erc20_metadata`; allowance → `erc20_allowance`; contract view → `contract_read`; send ETH → `native_transfer`; send ERC-20 → `erc20_transfer`; swap → `swap` (optional bridge fields as above).

Use valid **`chain`** (`base`, `ethereum`, …) where required; **`0x` addresses** lowercase or checksummed. Juno targets **`POST /v1/mercury/invoke`** only—not **`POST /v1/agent`** envelopes.
