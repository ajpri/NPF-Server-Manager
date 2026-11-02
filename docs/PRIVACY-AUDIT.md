# Pre-publication privacy audit

Reviewed November 2, 2026, before the initial GitHub commit of version 26.11.

The review covered application and installer source, test helpers, documentation, OpenAPI examples, validation logs/results, and all four screenshots. Text checks searched for personal names and home paths, email addresses, private-network addresses, SSH material, credential/token formats, and literal credential assignments. Findings were reviewed in context. Screenshots were visually inspected; PNG chunks contained only image headers, pixels and end markers, with no embedded text or EXIF metadata.

No private personal information, real authentication credentials, SSH connection details, private keys, runtime configuration or financial databases were found in the reviewed project files. Public upstream GitHub references are intentional. Test accounts/passwords and example API identifiers/tokens are explicitly synthetic fixtures or placeholders, not production credentials. The generic guest account paths identify disposable test fixtures.

The initial publication uses a fresh Git history with a single commit. Its exact staged file list is checked before publication; Git author/committer email uses GitHub's noreply address. Local account/agent directories, environment files, runtime state/configuration, databases, keys and package archives are excluded through `.gitignore`.

This records the scope and findings of this review; future changes and generated artifacts require their own review.
