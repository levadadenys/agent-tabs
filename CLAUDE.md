Claude should follow @AGENTS.md

The `@` prefix is a Claude Code import directive, not Markdown: it inlines
AGENTS.md into the agent's context. Leave it as `@AGENTS.md` — a plain filename
or a Markdown link reads the same to a human but stops the import.

NOTE: Do not modify CLAUDE.md, edit AGENTS.md instead, which is shared by all
coding agents.
