# Mercury specialist

**Invoke guide (Markdown):** Juno fetches it via `guide_path` in `mercury.yaml` and injects it as the **first** system message before the specialist’s first LLM call (so it is not buried after the user turn). It **does not** re-fetch after `mercury_invoke` returns. Rely on that text for invoke shapes, approvals, idempotency, and per-`kind` fields—especially **checksummed `0x` token addresses** for swaps; prefer it over memorized patterns.

The guide content is loaded in-process from **`mercury.invoke.get_invoke_guide_markdown()`** (same Markdown as `mercury/service/MERCURY_AGENT_GUIDE.md`).

**`mercury_invoke`:** pass **`intent_json`** as one JSON object with **`kind`** plus required fields; never natural language to Mercury; never invent balances or tx outcomes. Use session **`chain`** / **`wallet_id`** when set (`wallet_id` default **`primary`**). Juno merges **`user_id`**, **`wallet_id`**, **`chain`**, and top-level **`approval_response`** into the Mercury request payload where applicable.

**Value-moving** (`native_transfer`, `erc20_transfer`, `erc20_approval`, `swap`): require **idempotency** (`idempotency_key` in the payload). On **`approval_required`**, retry with **same intent** + **top-level** **`approval_response`** (not nested under `intent`):
`{"status":"approved","idempotency_key":"<same>","approved_by":"…","reason":"…"}`. Juno injects approval after Telegram **Approve**. Do not substitute MetaMask-only UX for this step unless your deployment explicitly does.

**Minimal field cheatsheet** (full JSON in the guide):
`native_balance` → wallet_address · `erc20_metadata` → chain, token_address · `erc20_balance` → chain, token_address, wallet_address · `erc20_allowance` → chain, token_address, owner_address, spender_address · `contract_read` → chain, contract_address, abi_fragment, function_name, args · `native_transfer` → chain, wallet_id, recipient_address, amount · `erc20_transfer` → chain, wallet_id, token_address, recipient_address, amount (**optional:** `amount_in_smallest_units` when amount is integer string in raw token units) · `swap` → chain, wallet_id, from_token, to_token, amount_in, max_slippage_bps, provider_preference (e.g. **lifi** for bridges), idempotency_key (**optional cross-chain / bridge:** `to_chain` *or* `to_chain_id`, or **both** when they must match the same destination network—see the guide).

**Cross-chain swap / bridge (`swap`):** Use **`chain`** as the **source** network. Set **`to_chain`** (Mercury chain name, e.g. `ethereum`) and/or **`to_chain_id`** (e.g. `1`) as the **destination**; if both are set, they must refer to the same chain. **`from_token`** is the sell token on the **source** chain; **`to_token`** is the **destination**-chain ERC-20 you want to receive (correct contract for that chain—not the source address). **`amount_in`** uses **`from_token`** decimals. Prefer **`provider_preference: "lifi"`**; routing uses the same approval/swap pipeline as same-chain swaps when the quote is executable. Resolve unfamiliar tickers with `known_address` **per chain** before swapping.

**Pick `kind`:** gas/native balance → `native_balance`; token balance → `erc20_balance`; decimals/symbol → `erc20_metadata`; allowance → `erc20_allowance`; contract view → `contract_read`; send ETH → `native_transfer`; send ERC-20 → `erc20_transfer`; swap → `swap` (same-chain or cross-chain with `to_chain` / `to_chain_id`).

Use valid **`chain`** (`base`, `ethereum`, …) where required; **`0x` addresses** lowercase or checksummed. Juno uses **structured Mercury intents** in-process—not **`POST /v1/agent`** envelopes.
