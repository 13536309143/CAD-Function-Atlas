# Credentials and data handling

Keep credentials in environment variables or ignored `.env` files. Adapters do not follow redirects or silently forward keys across providers/regions. Generic endpoints require HTTPS except for explicitly configured localhost servers. Audits retain response evidence and image hashes, not authorization headers or image base64.

Downloaded CAD content is processed as geometry; generated model text is parsed as JSON and never evaluated as code. Treat externally supplied models and API responses as untrusted input. Public archives contain generated text and CAD data; inspect their provenance and applicable upstream rights before reuse.

Do not publish keys in issues. If a credential was exposed, revoke it at the provider and replace the local value. The public source/data bundles are scanned before release; a scan is not a substitute for least-privilege credentials and rotation.
