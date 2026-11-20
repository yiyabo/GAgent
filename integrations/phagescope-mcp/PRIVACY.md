# PhageScope MCP privacy policy

Operator: PhageScope / GAgent team, https://phageagent.deepomics.org.
Contact: xxwang5587@gmail.com.

What the server receives: your MCP token and the tool arguments you send
(search filters, accession ids, protein or family ids). It returns PhageScope
reference data only; it does not read files or data from your machine.

What is stored, per call: token id, account id, tool name, a digest and short
summary of the arguments, status, error code, duration, returned rows and
bytes, and the time. Plaintext tokens are never stored (only a SHA-256 hash)
and never logged. A per-token daily byte counter enforces the usage limit.

Retention: audit records and usage counters are kept until the operator deletes them; there is no automatic expiry yet. Revoking a token stops new records for it.

Sharing: data is not sold or shared with third parties. Your AI client and its
model provider see the results the tools return, under their own policies.

Access: on request; tokens are granted only after approval.

Revocation: revoke a token at any time with
`DELETE /phagescope-mcp/tokens/{id}`; contact us to delete your account.
