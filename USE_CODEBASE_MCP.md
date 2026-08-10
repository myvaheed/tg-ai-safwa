## Codebase exploration

For codebase discovery and structural analysis, prefer
`codebase-memory-mcp` before using Grep/Glob or opening many files.

Use codebase-memory-mcp for:
- locating functions/classes/symbols
- understanding architecture
- tracing callers/callees
- impact analysis before modifications
- finding dependencies between modules
- determining where functionality is implemented

Use direct file reads after the MCP graph has identified the relevant files
and symbols.

Before making a multi-file change:
1. query codebase-memory-mcp
2. identify affected symbols/files
3. read the exact source files
4. make the change