# PhageAgent MCP privacy policy

Operator: PhageAgent team, https://phageagent.deepomics.org.
Contact: xxwang5587@gmail.com.

What the server receives: your MCP token and the tool arguments you send
(search filters, accession ids, protein or family ids). It returns PhageScope
reference data only; it does not read files or data from your machine.

What is stored, per call: token id, account id, tool name, a digest and short
summary of the arguments, status, error code, duration, returned rows and
bytes, and the time. Plaintext tokens are never stored (only a SHA-256 hash)
and never logged. A per-token daily byte counter enforces the usage limit.

Analysis tasks: the request text you submit, the agent run and its output
files are stored on the server under your account, and the request is sent to
the language model provider the PhageAgent agent uses. A task stops being
reachable through MCP 30 days after submission; the stored data is not deleted
automatically yet.

Retention: audit records and usage counters are kept until the operator deletes them; there is no automatic expiry yet. Revoking a token stops new records for it.

Sharing: data is not sold or shared with third parties. Your AI client and its
model provider see the results the tools return, under their own policies.

Access: any registered user can create tokens.

Revocation: revoke a token at any time with
`DELETE /phagescope-mcp/tokens/{id}`; contact us to delete your account.
