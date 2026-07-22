#!/bin/bash
# Single Claude Fable request to debug Shot Dash loading issue
# Usage: ANTHROPIC_KEY="sk-ant-..." bash debug_request.sh > fable_response.json

ANTHROPIC_KEY="${ANTHROPIC_KEY:-YOUR_KEY_HERE}"

curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{
  "model": "claude-sonnet-4-20250514",
  "max_tokens": 4000,
  "system": "You are debugging a JavaScript single-page dashboard. The issue: when the page loads, the shot grid shows \"0 shots\" even though the API endpoint /api/shots returns 13 shots correctly. BUT calling fetchShots() manually from the browser console works fine — it fetches 13 shots and renders them. The auto-load (called at the bottom of the script) fails silently. Also, the server process keeps getting SIGTERM'\''d by the container, but that is not the current issue. Focus ONLY on why the auto-load fetchShots returns empty when console invocation works. The script uses async/await, fire-and-forget promise chains, and setTimeout retries. DOM elements exist (table headers render). Syntax passes node --check. The API is same-origin (no CORS).\\n\\nAnswer with: (1) the most likely root cause by line number, (2) the fix in 1-3 lines of code change.",
  "messages": [{
    "role": "user",
    "content": "This is the complete Shot Dash code. The Python backend at shot_dash.py serves an HTML SPA at index.html.\\n\\n=== PYTHON BACKEND (shot_dash.py, 1032 lines) ===\\n'"$(<shot_dash.py sed 's/\\/\\\\/g; s/'\''/'\''\\'\'\''/g')"'\\n\\n=== FRONTEND (index.html, 352 lines) ===\\n'"$(<index.html sed 's/\\/\\\\/g; s/'\''/'\''\\'\'\''/g')"'\\n\\nSYMPTOM: Page shows \"0 shots\" on load. curl http://127.0.0.1:8090/api/shots returns 13 shots. Calling fetchShots() from browser console works and renders 13 rows. Auto-load fetchShots called at bottom of script returns empty. Retry via setTimeout also returns empty.\\n\\nFind the bug."
  }]
}'
