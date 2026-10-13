# Frozen repair-3 rejection evidence

Actual run `001-repair-3`, main `e83a67b46cdc7b7a5f0e4bd0bc646dce60cc13a1`.

`repair3_economics_rejection.json` is the exact 27285-byte `0315.json` reply from
`recon-c10ecbbae233-discover/a7cefa30c3f54721b9b9dc06133c20f1`, with13 source
issues. SHA256: `ac5a3bcc878fdb3f862f14ed8ab96f5eefc05363e2f9ccadca8d6e0530a5a177`.
The SDK received only `result_too_large`, not these conditions.

`repair3_submission_errors.json` contains the last actual submit call/result
pair from each of three SDK transcripts. Fields are preserved, serialized with
LF; the original compressed transcript SHA is retained per case. Only tool
events are included, with no assistant text, reasoning or reconstructed replies.
The result text retains the SDK's `Error: ` prefix and `isError=true`.

These are saved diagnostic transport cases, not current site snapshots,
teacher records, a paid-run reproduction, or proof of complete collection.
